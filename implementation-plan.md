# HostSpark Implementation Plan — 移植 antigravity-cli-telegram-bot 功能

> 目標：在不破壞 HostSpark 既有排程系統與安全模型的前提下，將 antigravity-cli-telegram-bot 的高價值功能移植進來。本文件是實作藍圖，供後續逐 Phase 開發與 code review 對照。

---

## 0. 現況基準（以現有原始碼為準）

| 模組 | 職責 | 關鍵事實 |
|---|---|---|
| `bot.py` (703 行) | Telegram handler、指令路由、排程 loop | 目前只註冊 11 個 handler：`/start` `/help` `/status` `/clear` `/schedule_*` ×6 + 1 個 callback + 1 個文字 MessageHandler |
| `agy_bot_core.py` (353 行) | 子程序執行、設定載入、Markdown/HTML 轉換、秘密遮罩 | `BotConfig` 是 frozen dataclass，`load_config()` 一次性從環境變數建置；`run_process()` 用 `asyncio.create_subprocess_exec`（無 shell，安全），一次性讀完 stdout/stderr 到記憶體（無串流回呼） |
| `schedule_store.py` (385 行) | 排程 SQLite CRUD、cron 運算、模板變數渲染 | 已有成熟的 SQLite 存取模式（`_connection()` context manager、WAL、`chmod 600`），**這是唯一可以直接複用的架構範本** |
| 授權模型 | `ALLOWED_USER_ID`：**單一**數字 ID，寫死在 `BotConfig`，`is_authorized()` 用 `==` 比對 | 目前**不支援多使用者、不支援 chat 白名單** |
| 對話狀態 | 無 HostSpark 端狀態；靠 AGY CLI 自己在 `AGY_WORKDIR` 下用 `--continue` 維護「最後一次對話」 | 沒有 per-chat、per-user 的對話隔離；`/clear` 只是送一句話讓 AGY 開新 session，不是刪除任何 HostSpark 狀態 |
| 併發控制 | 全域 `asyncio.Lock()`（`agy_lock`），排程與即時對話共用 | 沒有 queue，沒有 `/cancel`，沒有排隊機制——第二個請求會卡住等鎖 |
| 執行模式 | 全域 `AGY_PERMISSION_MODE=safe|full`，並非 per-chat | 對應 antigravity 的 `/mode`（per-chat plan/accept-edits）與 `/sandbox`（per-chat），語意不同，需要重新設計對應關係 |

**結論**：目前 HostSpark 是「單一管理員、單一對話串、單一全域模式」的簡化模型。要做到比較表列出的功能，**第一件事是把這個模型升級成「多使用者、per-chat 狀態」**，否則後面每個功能都會卡在「沒有地方存設定」。

> **2026-09-01 更新**：已完整讀過 antigravity-cli-telegram-bot 的 TypeScript 原始碼（不只 README），核對出 21 項本文件原本沒寫到或寫錯的地方，詳見**第 9 節**。以下各節已就地補上關鍵修正並標注「（見 9.x）」，第 9 節本身是完整清單，不重複貼原始碼引用。

---

## 1. 設計原則（不可違反的邊界）

1. **排程系統（`schedule_store.py` 全部邏輯）維持不動**——這是 HostSpark 唯一領先的資產，只允許新增欄位（如有需要），不重構既有 CRUD 邏輯。
2. **子程序呼叫一律不經過 shell**（延續 `create_subprocess_exec` 模式），新增的 `/agy` passthrough 與 PTY runner 也必須遵守。
3. **秘密遮罩（`redact_sensitive`）覆蓋範圍要跟著新功能擴大**，不能因為新增 PTY/串流輸出而繞過遮罩。
4. **危險操作需要二次確認**（比照現有 `/schedule_add` 兩階段確認 UI 模式），`/agy-confirm`、plugin/update/install 類都要套用同一套 pending-token 機制（`bot_data.setdefault("pending_xxx", {})` 已有前例）。
5. **Safe/Full 全域模式繼續作為最外層安全閘門**：per-chat 的 `/mode` `/sandbox` 只能在 Full 模式下才允許放寬，Safe 模式下一律忽略或拒絕使用者調高風險的請求。
6. **每個 Phase 結束都要跑 `python -m unittest discover -s tests -v` 全綠，並手動跑一次 Lab 1（既有 README 的驗收流程）確認沒有回歸。**

---

## 2. 架構決策

### 2.1 多使用者授權升級
- `ALLOWED_USER_ID`（單數）→ 新增 `ALLOWED_USER_IDS`（逗號分隔），**保留舊變數名稱做向後相容**：若 `ALLOWED_USER_IDS` 未設定，退回讀取 `ALLOWED_USER_ID`。
- 新增可選 `ALLOWED_CHAT_IDS`（逗號分隔），預設空＝不限制 chat（沿用現有「僅限私訊」的隱含行為，新增 `TELEGRAM_PRIVATE_ONLY=1` 開關，比照對方設計）。
- `is_authorized(user_id, chat_id)` 改為檢查兩個集合。

### 2.2 Per-chat 狀態存放（新模組 `chat_state.py`）
比照 `schedule_store.py` 的 SQLite 模式，新增一張表：

```sql
CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    conversation_id TEXT,          -- AGY --conversation 用的 UUID，NULL 表示尚未建立
    model TEXT,                    -- NULL 使用 AGY 預設
    effort TEXT DEFAULT 'high',    -- low|medium|high
    mode TEXT DEFAULT 'plan',      -- plan|accept-edits（僅 Full 全域模式下 accept-edits 才生效）
    sandbox INTEGER DEFAULT 1,     -- 0|1
    agent TEXT,                    -- 自訂 agent 名稱
    project TEXT,                  -- --project 值
    add_dirs TEXT,                 -- JSON array，--add-dir 清單
    output_format TEXT DEFAULT 'text',
    json_schema TEXT,
    log_file TEXT,
    print_timeout TEXT,
    continue_enabled INTEGER DEFAULT 1,
    new_project INTEGER DEFAULT 0,
    disable_slash_commands INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);
```

- 提供 `ChatStateStore`：`get_or_create(chat_id)`、`update(chat_id, **fields)`、`clear_conversation(chat_id)`。
- 檔案權限 `chmod 600`，路徑用新的 `AGY_STATE_DB_PATH`（預設 `~/.local/state/agy-telegram-bot/chat_state.db`，跟排程 DB 同目錄不同檔）。
- **這個 store 是 Phase 1+ 所有 per-chat 指令的唯一資料來源**，`run_agy()` 組 CLI 參數時要從這裡讀，不能散落在各 handler。

### 2.3 `run_agy()` 參數組裝重構
現有 `run_agy()` 只知道 `continue_conversation`、`workdir`、`allow_full_permissions`。需要擴充成讀取 `ChatState` 組出完整 argv：

```
agy -p "<prompt>"
    [--conversation <uuid> | --continue]
    [--model <id>] [--effort low|medium|high]
    [--mode plan|accept-edits] [--sandbox=on|off 對應旗標]
    [--agent <name>] [--project <id>]
    [--add-dir <path>]...
    [--output-format text|json|stream-json]
    [--json-schema <value>] [--log-file <path>] [--print-timeout <value>]
    [--new-project] [--disable-slash-commands]
    [--dangerously-skip-permissions]（只在全域 Full 模式）
```

- 全部旗標值都要驗證合法性（比照 `agy_bot_core.py` 現有 `_positive_int` 風格寫驗證函式），避免使用者透過 `/model`、`/project` 等指令注入奇怪字串當作 argv（因為不經過 shell，注入風險低，但仍要做白名單/格式驗證，避免把錯誤參數傳給 `agy` 造成非預期行為）。

### 2.4 串流輸出（stream-json）
- 新增 `agy_stream.py`：`async def run_agy_streaming(args, cwd, env, on_event: Callable[[dict], Awaitable[None]], timeout_seconds, max_output_bytes) -> ProcessResult`。
- 逐行讀 stdout（NDJSON），每行 `json.loads`，呼叫 `on_event(event)`；`on_event` 由 `bot.py` 提供，做「每 N 秒或每 M 個事件」節流後 `edit_message_text`（Telegram 有 rate limit，需節流，比照對方 README 提到的「live response drafts」）。
- 保留現有 `run_process()`（非串流）給 `/status` 等內部指令繼續用；`run_agy()` 一般對話路徑改用 `run_agy_streaming`，其餘（`_status_section`、排程執行）維持原本 `run_process`。
- 最終回覆仍要走 `redact_sensitive` + `format_result_message`，串流過程中的中繼訊息也要過一次遮罩（防止工具呼叫過程中洩漏秘密到「思考中」訊息）。

### 2.5 PTY 互動指令（`/usage` `/credits` `/context`）
> **修正（見 9.6）**：`/usage` `/credits` **不是純 PTY**。對方實作優先嘗試 `agy --print /quota --output-format json --dangerously-skip-permissions`（結構化 JSON，不需要 TUI 擷取），失敗時才退回 PTY 擷取畫面。HostSpark 應該照抄這個「先試結構化輸出，失敗才用 PTY」的順序，大幅降低複雜度與脆弱性。但注意對方的直接路徑「不論全域 sandbox/permission 政策，一律強制帶 `--dangerously-skip-permissions`」——這點在 HostSpark 要不要照搬需要明確決定（Safe 模式下是否該讓唯讀的 quota 查詢也自動核准權限，屬於安全政策選擇，不是純技術問題，建議 Phase 2 開工前先跟使用者確認）。

- 新增依賴 `pexpect`（更新 `requirements.txt` / `requirements.lock`），**僅作為結構化路徑失敗時的 fallback**。
- 新增 `pty_runner.py`：`async def run_pty_command(agy_bin, workdir, env, slash_command, timeout_seconds) -> str`
  - 用 `pexpect.spawn` 啟動 `agy`（互動模式），等待畫面穩定的 prompt，送出 `/usage`（或 `/credits`、`/context`）字串，等待輸出穩定後擷取。
  - 移除 ANSI escape（`re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw)`），套用 `redact_sensitive`。
  - 強制 timeout（比照現有 `_stop_process` 的 SIGTERM→SIGKILL 兩段式終止, 但用 `pexpect` 的 `close(force=True)`）。
  - 執行時**從子程序環境變數移除 `TELEGRAM_BOT_TOKEN`**（比照對方 `safe-env.ts` 的 secret scrubbing 做法）。
- 這條路徑與 `agy_lock` 共用鎖，避免跟一般對話/排程同時搶 AGY 進程。

### 2.6 對話恢復（`/resume` `/sessions` `/new`）
- 新增唯讀模組 `conversation_db.py`：連線到 AGY 自己維護的 SQLite（路徑由新環境變數 `AGY_CONVERSATION_DB_PATH` 指定，需使用者依實際 AGY 安裝路徑設定，**唯讀開啟** `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`）。
- 提供 `list_conversations(limit, offset)` 讀 `conversation_summaries` 表，過濾空/已刪除對話。
- `/resume`：inline keyboard 分頁（每頁 10 筆，比照對方設計），選定後把 `conversation_id` 寫進 `ChatStateStore`，之後 `run_agy()` 改用 `--conversation <id>` 而非 `--continue`。
- `/new`：清除該 chat 的 `conversation_id`（設 NULL），下一句話會讓 AGY 開新對話（不帶 `--continue`/`--conversation`）。
- `/sessions` 為 `/resume` 的 alias。
- `/clear` 保留現有行為（相容舊使用者），但底層改成呼叫 `/new` 的邏輯（清空 state），確保語意一致。

### 2.7 CLI Passthrough 與危險操作確認
- 新增 `cli_passthrough.py`：白名單解析 `agy --help` 已知旗標（比照對方「repeatable `--add-dir`」等），`/agy ARGS...` 直接把 `context.args` 原樣接到 argv（**不做字串拼接、不過 shell**）。
- **參數合法性檢查（修正，見 9.11）**：對方 `validateCustomArgs` 有兩條規則本文件原本沒寫，兩條都必須照搬：
  1. **一律拒絕 `--prompt-interactive` / `-i`**——這是防止 Telegram 觸發一個沒有 TTY 可互動、會直接卡死的 session 的**最關鍵防線**，比危險子命令判斷更基本，優先度最高。
  2. 除非第一個參數是已知唯讀子命令（`agent agents changelog help install models plugin plugins update`）或 `--help/-h/--version/-v`，否則**必須**帶 `--print`/`-p`/`--prompt`，禁止使用者用 `/agy` 繞過去啟動任何形式的互動模式。
- **危險子命令判斷（修正，見 9.12）**：原文件寫的「plugin install/remove、update、install」不準確，正確條件是「三選一」：
  1. 參數中**任何位置**出現 `--dangerously-skip-permissions`（獨立觸發，不管子命令是什麼）；
  2. 子命令是 `update` 或 `install`；
  3. 子命令是 `plugin`/`plugins`，且動作是 `install|uninstall|enable|disable|import|link`（**不是 `remove`，是 `uninstall`**；且要涵蓋 `enable/disable/import/link`，原文件全部漏列）。
- **`--sandbox` 強制附加（新增，見 9.10）**：全域設定為「sandbox 開啟且不允許停用」時，`/agy` passthrough 若使用者沒帶 `--sandbox`，要強制附加，不能讓 passthrough 繞過全域 sandbox 政策（原文件的 `/sandbox` 說明只涵蓋一般對話路徑，沒提到 passthrough 也要套用）。
- 危險命令：先回覆「請輸入 `/agy-confirm` 確認」+ 存 pending token（沿用 `PendingSchedule` 的模式，新增 `PendingAgyCommand`），15 分鐘過期，跟現有排程確認機制共用同一套 TTL 邏輯，可考慮抽成共用的 `PendingActionStore`（見 2.9 重構建議）。

### 2.8 多模態附件
- 新增 handler：`MessageHandler(filters.PHOTO | filters.Document.ALL, handle_attachment)`。
- 下載到 `AGY_WORKSPACE_ROOT/uploads/<chat_id>/<telegram_file_unique_id>_<原始檔名>`；**檔名先過濾**（只允許 `[A-Za-z0-9._-]`，其餘字元替換為 `_`，避免路徑穿越/特殊檔名）。
- 下載後用 `Path(...).resolve()` 確認結果路徑仍在 `AGY_WORKSPACE_ROOT` 之下，否則拒絕（比照對方「path containment」）。
- 副檔名白名單：`.pdf .txt .md .json .csv .py .go .js .ts .yaml .yml .toml .log` 等，其餘副檔名拒絕並提示。
- 存完後可選擇性把檔案路徑透過 `--add-dir <uploads目錄>` 加進下一次 `run_agy()` 呼叫。

> **新增遺漏功能（見 9.17）**：以上都只是「Telegram → HostSpark」方向的附件上傳。對方另有一個**完全獨立、方向相反**的功能：掃描 **AGY 輸出文字**中出現的本地檔案路徑與公開圖片 URL，驗證後把檔案/圖片**回傳**給 Telegram 使用者（例如 AGY 幫使用者產生一張圖或寫出一份報表檔案時，直接把結果傳回聊天室，而不是只回一段文字說「已產生在 /path/to/file」）。這需要兩層防護：
> 1. **本地路徑**：只允許白名單目錄（`/tmp`、`/var/tmp`、AGY 的 artifact 輸出目錄、`AGY_WORKSPACE_ROOT`），其餘一律拒絕。
> 2. **遠端 URL**：完整的私有/保留 IP 檢查（含 DNS 反解後二次檢查，防止 DNS rebinding 繞過），不能只檢查字面 IP。
>
> 這是原計畫完全沒設計到的一塊，複雜度不低，且明顯超出「Phase 2 附件上傳」的範疇，已在第 9 節列為建議的獨立 **Phase 5** 項目。

### 2.9 重構建議：抽出共用的「Pending Action」機制
現有 `schedule_add_command` 已經手刻了一套「token → pending 資料，15 分鐘 TTL，`bot_data` 存放」的邏輯。`/agy-confirm` 也需要一模一樣的模式。建議在 Phase 3 抽成：

```python
# pending_actions.py
class PendingActionStore:
    def put(self, kind: str, user_id: int, payload: Any, ttl_minutes: int = 15) -> str: ...
    def pop(self, token: str, user_id: int) -> Any | None: ...
    def purge_expired(self) -> None: ...
```

`schedule_add_command` 與 `/agy` 危險指令共用這個 store，減少重複程式碼（這是唯一建議的重構，其餘一律新增而非修改既有邏輯）。

---

## 3. Phase 拆解與交付項目

### Phase 0 — 地基（預估 1 個工作單位）
**目標**：讓後面每個功能都有地方存資料。

- [ ] `agy_bot_core.py`：`BotConfig` 新增欄位（多使用者、chat 白名單、`AGY_STATE_DB_PATH`、`AGY_CONVERSATION_DB_PATH`、`AGY_WORKSPACE_ROOT`、允許模型清單 `AGY_ALLOWED_MODELS`）；`load_config()` 對應解析與驗證。
- [ ] 新增 `chat_state.py`（`ChatStateStore`，見 2.2）+ 對應單元測試 `tests/test_chat_state.py`。
- [ ] `bot.py`：`is_authorized()` 改為多使用者/多 chat 版本；`main()` 初始化 `CHAT_STATE_STORE`（比照 `SCHEDULE_STORE` 的 global 模式）。
- [ ] `.env.example` 新增對應變數說明（沿用現有中文註解風格）。
- [ ] 更新 `README.md`「必要條件」與「設定」章節。
- **驗收**：`/start` 顯示目前允許的使用者數量；多組 `ALLOWED_USER_IDS` 都能通過授權；舊版只設 `ALLOWED_USER_ID` 的部署仍正常運作（回歸測試）。

### Phase 1 — 核心可用性
**目標**：對話管理 + per-chat 模型/模式控制，這是使用者體感差異最大的部分。

- [ ] `conversation_db.py`（唯讀 AGY SQLite 讀取）+ `tests/test_conversation_db.py`（用臨時 SQLite fixture 模擬 `conversation_summaries` 表）。
- [ ] `/new` `/resume` `/sessions` 指令 + inline keyboard 分頁 callback（`resume_page:<offset>`、`resume_select:<uuid>`）。
- [ ] `run_agy()` 重構：讀 `ChatStateStore`，依 2.3 組完整 argv；`--continue` 邏輯改為「有 `conversation_id` 用 `--conversation`，否則用 `--continue`（若非 `/new` 之後的第一句）」。
- [ ] `/model` `/models`（inline picker，清單來自 `AGY_ALLOWED_MODELS`）。
- [ ] `/effort <low|medium|high>`。
- [ ] `/mode <plan|accept-edits>`（Safe 全域模式下鎖死只能 `plan`，回覆提示需切到 Full）。
- [ ] `/sandbox <on|off>`。
- [ ] `/session`（彙整顯示目前 chat 的所有設定，從 `ChatStateStore` 讀出格式化輸出）。
- [ ] **`/verbose <detailed|compact|silent>`（新增，見 9.2）**：per-chat 設定，寫入 `ChatStateStore`，控制串流進度訊息的詳細程度，跟 `/effort`/`/mode` 同一批做。
- [ ] **`/setdefault`（新增，見 9.1）**：把目前 chat 的 model/effort/mode/sandbox 寫回 `.env`，變成**全域**預設值（跨 chat、跨重啟生效）。這是唯一一個「per-chat 設定會反向影響全域設定」的指令，實作時要特別小心寫檔的原子性（先寫暫存檔再 rename，避免同時重啟時 `.env` 半寫入），且只有已授權使用者能觸發，建議額外要求一次確認（比照 pending action 機制）避免誤觸。
- [ ] 串流輸出（`agy_stream.py`，見 2.4），套用到一般文字對話路徑，取代目前「等待訊息→整段回覆」。
- [ ] **進度訊息結束狀態可設定（新增，見 9.21）**：新增全域 `AGY_PROGRESS_MODE=full|compact|delete`，決定串流結束後「思考中」訊息是刪除、收合成一行摘要、還是展開完整統計。
- [ ] 更新 `class.md` 補上新架構章節（比照現有教學文件風格，之後每個 Phase 都補）。
- **驗收**：兩個不同 Telegram 使用者各自的對話、模型設定互不影響；`/resume` 能翻頁並成功接續一段舊對話；一般對話能看到中繼進度訊息而非死等。

### Phase 2 — 高價值獨立功能
- [ ] `/tokens`：從 stream-json 事件中累積 token 使用量（per-turn + 累計，存在 `ChatStateStore` 或記憶體 `bot_data`，依是否需要跨重啟持久化決定）。
- [ ] `pty_runner.py` + `/usage` `/quota` `/credits` `/context`（見 2.5，**先試 `agy --print /quota --output-format json` 結構化路徑，失敗才 fallback 到 PTY**）+ `tests/test_pty_runner.py`（用假的互動腳本模擬 AGY TUI 輸出，驗證 ANSI 清理與逾時終止）。
- [ ] **`/usage` 配額「進度指標」（新增，見 9.8）**：不是單純顯示剩餘百分比，要算「配額週期已過時間 vs 實際剩餘量」的落差，用 🟢/🟡/🔴/⭐/⚪ + `[+12%]`/`[On track]` 這類標籤呈現使用速度是否正常。
- [ ] **`/context` 完整結構（修正，見 9.9）**：不是一行數字，要包含模型名稱、估計用量、分類 token 明細（使用者訊息／代理回覆／工具呼叫／系統提示／系統工具／Skills／子代理）、剩餘可用空間、checkpoint 資訊、artifact 數量。
- [ ] **`/learn` `/compact`（新增，見 9.3，工作量低）**：不需要新的 CLI 旗標或子系統，只是把使用者輸入包裝成特定用途的 prompt（例如「將這段對話整理成可重複使用的規則/skill」「壓縮目前對話上下文」）送給 AGY，跟一般文字對話走同一條路徑，適合順手做掉。
- [ ] 多模態附件（2.8）+ `/add-dir` `/project` 指令（寫入 `ChatStateStore`，供 `run_agy()` 組 `--add-dir`/`--project`）。
- [ ] `/status` 擴充：加入「目前 queue 長度、是否有進行中任務」（需要 Phase 2 的簡易 queue 資訊，不必等到 Phase 4 完整 queue 才顯示）。
- [ ] **子程序環境隔離擴大（修正，見 9.15）**：不是只從子程序環境移除 `TELEGRAM_BOT_TOKEN`，要連 `ALLOWED_USER_IDS`／`ALLOWED_CHAT_IDS`（白名單本身）都一起移除，並固定設定 `NO_COLOR=1`、`TERM=dumb`；而且這個處理要套用在**所有** AGY 子程序呼叫（一般對話、排程、PTY 全部都要），不是只有 PTY 路徑才做。
- [ ] **超長回覆改傳檔案（新增，見 9.20）**：AGY 回覆長度超過 `2 × max_chunk_size` 時，改成上傳一份 `.md` 文件附件，而不是切成一堆訊息轟炸聊天室。
- **驗收**：上傳一張圖片/一份 `.py` 附件後，AGY 能在後續對話中引用該檔案；`/usage` `/credits` 能在 60 秒內回報或明確逾時訊息，且輸出不含任何 Token 字串。

### Phase 3 — 安全強化 + CLI Passthrough
- [ ] 路徑封裝共用函式 `safe_join(base: Path, *parts) -> Path`（附件、`/add-dir`、workspace 隔離都改用這個函式），寫進 `agy_bot_core.py`。
- [ ] 擴充 `redact_sensitive`：涵蓋更多常見金鑰格式（AWS `AKIA...`、SSH 私鑰 header、JWT 三段式），對照對方 `safe-env.ts` 的規則清單逐條補齊。
- [ ] `pending_actions.py`（2.9 重構）並讓 `/schedule_add` 改用它（順便減少重複程式碼）。
- [ ] `/agy ARGS...` + `/agy-confirm`（見 2.7 完整修正版：**先擋 `--prompt-interactive`/`-i`**，再判斷「三選一」危險條件，`--sandbox` 強制附加）。
- [ ] `/agents`（唯讀，呼叫 `agy agents`）、`/changelog`、`/plugins`、`/cli-help`、`/version`（`agy --version`，見 9.7 附錄：無獨立 version 子命令）：都是唯讀，跟 `/agy` passthrough 一起做。
- [ ] **`/agent NAME`（修正，見 9.13）：這個不是唯讀查詢，是寫入 `ChatStateStore` 的 per-chat 設定**（跟 `/project` 同性質），不要跟唯讀的 `/agents` 混在一起實作。
- [ ] `/output-format` `/json-schema` `/log-file` `/print-timeout` `/new-project` `/disable-slash-commands`（全部只是寫入 `ChatStateStore` 對應欄位，交給 `run_agy()` 組 argv 時套用，工作量小，跟 `/agy` passthrough 同批次做）。
- [ ] **`/continue`（修正，見 9.14）**：不是單純布林開關。無參數或 `list` 時要**等同 `/resume`**（瀏覽/選擇歷史對話）；只有帶 `on`/`off` 時才是切換 `ChatStateStore.continue_enabled`。實作時直接讓 `/continue`（無參數）內部呼叫 `/resume` 的邏輯，避免重複一套分頁 UI。
- [ ] `SECURITY.md` 更新：新增「CLI passthrough 風險」與「附件路徑隔離」章節。
- **驗收**：`/agy plugin install xxx`、`/agy plugin enable xxx`、`/agy --dangerously-skip-permissions ...`（不管子命令為何）都必須先跳出確認、`/agy-confirm` 後才真正執行；`/agy -i` 與 `/agy --prompt-interactive` 必須直接被拒絕，不能進入確認流程；任何一次執行的完整 stdout/stderr 過 `redact_sensitive` 後手動檢查不含任何已知格式的憑證。

### Phase 4 — 體驗打磨與韌性
- [ ] 常駐 reply keyboard + `/menu`（比照對方「Model / Mode」精簡按鈕設計）。
- [ ] **Instance lock（修正，見 9.16）**：不是單純「PID 檔案存在就拒絕啟動」。要檢查 lock 檔裡的 PID 是否還存活（`os.kill(pid, 0)` 判斷），**若程序已死則刪除舊 lock 並重新取得**——否則 Bot 一旦異常崩潰，systemd 的 `Restart=on-failure` 會被舊 lock 卡死，永遠起不來，這是比原文件單純提到的「防止重複啟動」更重要的一半。
- [ ] **Job queue（修正，見 9.18）**：對方是**單一全域 queue**（不是 per-chat 平行），同一時間全站只執行一個任務，`/status`/`/cancel` 只是「過濾顯示屬於這個 chat 的項目」。這跟 HostSpark 現有的全域 `agy_lock` 精神一致，**維持全域序列化，不要改成 per-chat 平行**，只需要把現有的單一 `Lock` 升級成有界佇列（`asyncio.Queue(maxsize=MAX_QUEUE_SIZE)`）+ 加上下面兩項對方有、原文件沒提到的機制：
  - **`/cancel`**：取消該 chat 佇列中或執行中的任務，需要能終止子程序（複用 `_stop_process`）。
  - **Auto-interrupt 合併（見 9.19）**：`AGY_AUTO_INTERRUPT=true` 時，使用者在任務執行中又傳新訊息，不是排隊等待，而是：先收集「目前執行中任務的 prompt + 該 chat 所有還在佇列中的 prompt」，把新訊息以 `[Update / Follow-up]: <新訊息>` 的格式接在後面合併成一個 prompt，**取消**原本執行中/排隊中的任務，改成執行這個合併後的單一任務——確保使用者連續追加的指示不會被遺失或互相打斷。
  - **Crash 後 in-flight job 自動恢復（新增，見 9.5）**：每次要執行任務前，先把「這個 chat 目前要跑的任務」寫進持久化狀態（`ChatStateStore` 或獨立表）；Bot 重啟時讀回這個狀態，若有未完成的任務，主動回覆使用者「您的請求先前被中斷，正在重新執行」並自動重新排入佇列。這是讓「Bot 自我更新後自動重啟」安全的前提，見下一項。
- [ ] **自我更新流程（重新評估，見 9.4）**：原計畫決定「不透過 Telegram 觸發，只寫腳本+文件」，是因為擔心 Bot 重啟自己造成競態風險。但對方證實這個風險是可解的——只要有上面的「crash 後 in-flight job 自動恢復」機制，`/restart` `/update` 由授權使用者透過 Telegram 觸發（`git pull` + 重新建置 + 觸發 systemd 重啟）就是安全的。**建議修正決策**：改為實作 `/restart` `/update`（比照 `ALLOW_BOT_UPDATE` 開關，預設關閉，需明確在 `.env` 開啟才能用），但**必須先完成 in-flight job 恢復機制**才能做這一項，順序上排在 Job queue 之後。
- [ ] CI：新增 `.github/workflows/ci.yml`（跑 `py_compile` + `unittest discover`），比照對方的測試矩陣精神。
- **驗收**：連續快速傳送 5 則訊息，確認依序處理而非互相打斷；`/cancel` 能在 3 秒內讓卡住的任務停止並回覆確認；手動 `kill -9` Bot 進程模擬崩潰後用 systemd 拉起，確認 lock 不會卡死、且未完成任務有自動恢復通知；追加訊息時確認舊任務被合併而非遺失。

### Phase 5 — 進階/低優先（原計畫沒有，原始碼比對後新增）
> 這些是原始碼裡確實存在、但複雜度較高或跟核心「Telegram 遙控 VM」需求關聯較弱的功能，建議放在 Phase 0-4 穩定上線之後再評估是否要做。

- [ ] **輸出媒體回傳（SSRF 防護）**（見 2.8 補充、9.17）：掃描 AGY 輸出文字中的本地檔案路徑/公開圖片 URL，驗證後回傳給 Telegram。
- [ ] **Markdown 渲染保真度**（見 9.22）：LaTeX 轉 Unicode（`\frac` `\sqrt` 希臘字母、上下標）、GitHub 風格 `> [!NOTE]` 提示區塊、Markdown 表格轉卡片列表（Telegram HTML 沒有 `<table>`）。若 HostSpark 現有 `md_to_telegram_html` 已經「夠用」，這項可以無限期延後，純屬呈現美觀，不影響功能。

---

## 4. 完整指令 → Phase 對照表

| 指令 | Phase | 備註 |
|---|---|---|
| `/start` `/help` `/status` `/clear` | 既有 | Phase 0-1 會擴充內容，不新增指令 |
| `/schedule_*`（6 個） | 既有 | 不動邏輯，Phase 3 抽 `PendingActionStore` 時共用 |
| `/menu` | 4 | |
| `/new` `/resume` `/sessions` `/continue`（無參數/`list`） | 1 | `/continue` 無參數時等同 `/resume`（見 9.14） |
| `/models` `/model` `/effort` `/mode` `/sandbox` `/session` `/verbose` | 1 | `/verbose` 為新增指令（見 9.2） |
| `/setdefault` | 1 | **新增指令**，會寫回全域 `.env`（見 9.1），需額外確認流程 |
| `/tokens` | 2 | 依賴 Phase 1 的串流事件 |
| `/usage` `/quota` `/credits` | 2 | 先試結構化 JSON 路徑，失敗才 fallback PTY（見 9.6） |
| `/context` | 2 | 完整結構見 9.9 |
| `/learn` `/compact` | 2 | **新增指令**，只是包裝 prompt，工作量低（見 9.3） |
| `/cancel` | 4 | 依賴 Phase 4 的 queue |
| `/restart` `/update` | 4 | **新增指令**，需先有 Phase 4 的 in-flight 恢復機制才能安全做（見 9.4） |
| `/agents` `/changelog` `/plugins` `/cli-help` `/version` | 3 | 唯讀，跟 `/agy` passthrough 同批 |
| `/agent NAME` | 3 | **非唯讀**，寫入 `ChatStateStore`（見 9.13），跟 `/project` 同性質 |
| `/agy` `/agy-confirm` | 3 | 危險判斷條件見 2.7 修正版；`--prompt-interactive`/`-i` 一律拒絕 |
| `/project` `/add-dir` `/output-format` `/json-schema` `/log-file` `/print-timeout` `/continue`（`on`/`off`）`/new-project` `/disable-slash-commands` | 3 | 純 `ChatStateStore` 欄位寫入 |

---

## 5. 測試計畫

- 每個新模組都要有獨立 `tests/test_<module>.py`，比照現有 `test_core.py` / `test_schedule_store.py` 的風格（unittest，不用額外測試框架）。
- `bot.py` 的 handler 測試比照現有 `test_bot.py` 的模式（假的 `Update`/`Context` 物件 + monkeypatch `run_process`/`run_agy`）。
- 新增整合測試：`tests/test_full_flow.py`，模擬「`/new` → 傳文字 → `/resume` 恢復同一對話 → `/model` 切換 → 再傳文字」的完整鏈路，全程 mock 掉真正的 `agy` 子程序呼叫。
- PTY 相關測試無法在 CI 容器內 100% 模擬真實 AGY TUI，至少要做到：假的 pexpect 腳本（一個會印出固定格式報表的 shell script）驗證 ANSI 清理、逾時、秘密遮罩三件事。
- 每個 Phase 結束前執行：
  ```bash
  python -m py_compile bot.py agy_bot_core.py schedule_store.py chat_state.py conversation_db.py agy_stream.py pty_runner.py cli_passthrough.py pending_actions.py
  python -m unittest discover -s tests -v
  venv/bin/python bot.py --check-config
  ```

---

## 6. 風險登記表

| 風險 | 影響 | 緩解措施 |
|---|---|---|
| ~~AGY CLI 實際旗標與本文件假設不一致~~ | ~~Phase 1 全部卡住~~ | **已於 2026-09-01 在實際 VM 上核對，見下方「附錄：已驗證的 AGY CLI 參考」，結論：假設成立，僅 `--sandbox` 的語意需修正為布林旗標**。 |
| PTY 互動指令高度依賴 AGY TUI 版面 | `/usage` `/credits` 可能因 AGY 版本更新而失效（對方 README 也承認這個限制） | 抓不到報表時要明確回覆「解析失敗，可能是 AGY 版本更新」而非靜默逾時 |
| 多使用者/per-chat 狀態上線後，舊有單一使用者部署的行為必須不變 | 造成既有使用者升級後對話「消失」或模式跑掉 | Phase 0 必須做「舊環境變數自動遷移」路徑，並在升級文件（README「從舊版升級」章節）新增對應步驟 |
| `/agy` passthrough 是全案風險最高的功能 | 一旦危險子命令清單漏列，等於繞過所有其他安全機制 | Phase 3 用「白名單允許的安全子命令」而非「黑名單擋危險子命令」，未知子命令一律要求確認，寧可誤擋不要漏放 |
| 串流解析（stream-json）與現有 `redact_sensitive` 只在最終訊息套用 | 中繼進度訊息可能提前洩漏秘密 | 2.4 已註明：串流的每一段中繼文字都要過 `redact_sensitive` 再送出 |

---

## 7. 附錄：已驗證的 AGY CLI 參考（2026-09-01，`agy --version` = 1.1.23）

在實際安裝的 VM 上執行 `agy --help` 取得的完整輸出（用於核對 2.3 節的 argv 組裝表）：

```text
Usage of agy:
  --add-dir                       Add a directory to the workspace (repeatable) (default [])
  --agent                         Agent for the current CLI session
  -c                              Short alias for --continue
  --continue                      Continue the most recent conversation
  --conversation                  Resume a previous conversation by ID
  --dangerously-skip-permissions  Auto-approve all tool permission requests without prompting
  --disable-slash-commands        Disable slash command and skill expansion in print mode
  --effort                        Reasoning effort for the current CLI session (low|medium|high)
  -i                              Short alias for --prompt-interactive
  --input-format                  Input format for print mode (text, stream-json)...
  --json-schema                   Optional JSON schema string or path to a schema file...
  --log-file                      Override CLI log file path
  --mode                          Set the agent execution mode for this session (accept-edits, plan)
  --model                         Model for the current CLI session
  --new-project                   Create a new project for this session
  --output-format                 Output format for print mode (text, json, stream-json) (default text)
  -p                              Short alias for --print
  --print                         Run a single prompt non-interactively and print the response
  --print-timeout                 Timeout for print mode wait (default 5m0s)
  --project                       Project ID or project name for the current CLI session
  --prompt                        Alias for --print
  --prompt-interactive            Run an initial prompt interactively and continue the session
  --sandbox                       Run in a sandbox with terminal restrictions enabled

Available subcommands:
  agent           List available agents
  agents          List available agents
  changelog       Show changelog and release notes
  help            Show help for subcommands
  install         Configure environment paths and shell settings
  mcp             Manage MCP servers (add, remove, list, enable, disable)
  mic-serve       Serve this machine's microphone to a CLI on another host
  models          List available models
  plugin          Manage plugins (install, uninstall, list, enable, disable)
  plugins         Alias for plugin
  update          Update CLI
```

### 對 2.3 節 argv 組裝表的修正

- **`--sandbox` 是無參數的布林旗標**（說明文字沒有像 `--add-dir` 那樣標示 `(default [])`），初步判斷是「出現即啟用沙箱」，而不是 `--sandbox=on|off`。Phase 1 實作 `/sandbox on|off` 時：
  - `on` → 附加 `--sandbox`
  - `off` → 不附加該旗標
  - 實作前應先用一個不涉及真實 prompt 的呼叫（例如 `agy --sandbox --help`）確認旗標語法不會噴錯，避免直接對正式 workflow 送出未驗證的旗標組合。
- **`agy --version`** 未列在 `--help` 的旗標清單中，屬於隱藏旗標，但實測可正常回傳版本號，`/version` 指令可直接呼叫它。
- **`agy models`**、**`agy agent` / `agy agents`**、**`agy changelog`**、**`agy plugin` / `agy plugins`**、**`agy update`** 皆為獨立子命令（非旗標），`/models` `/agents` `/changelog` `/plugins` 對應的實作應該呼叫 `agy <subcommand>` 而非加旗標到 `-p` 呼叫上。
- **沒有獨立的 `version` 子命令**（`agent/agents/changelog/help/install/mcp/mic-serve/models/plugin/plugins/update` 之外沒有 `version`），`/version` 一律用 `agy --version`。
- **`/usage` `/credits` `/context` `/tokens` 在 `--help` 中完全沒有對應的頂層旗標或子命令**——這些是 AGY 互動模式下的 **slash command**（`/quota`、`/context` 等，跟 Telegram 指令同名但完全是兩回事）。**更新（見第 9 節 #6）**：後續讀原始碼發現 `/usage` `/credits` 其實可以用 `agy --print /quota --output-format json` 走非互動結構化路徑取得，不需要每次都進 PTY；只有沒有結構化輸出的情境（或該路徑失敗時）才需要 PTY fallback。`/context` `/tokens` 目前沒有找到對應的結構化 CLI 路徑，維持 PTY／串流事件解析。
- 新發現 **`--input-format`**（含 `stream-json` 選項，可讀 stdin 逐行 NDJSON 驅動多輪對話）：本計畫範圍內不使用，但若未來要做「排程一次呼叫、多輪 turn」可以參考，先記錄不展開。
- **`mcp`、`mic-serve`、`install` 子命令**：本計畫範圍未涵蓋（MCP 管理、麥克風轉發、環境安裝屬於超出「Telegram 遙控日常任務」範疇的功能），列為明確排除項目（見第 8 節）。

---

## 8. 明確排除範圍（本輪不做）

- npm/GitHub Packages 對應的 Python 套件發布（PyPI）——先以 git clone + `install.sh` 部署為主，之後有需要再開新計畫。
- 對方的 TypeScript 模組化目錄結構（`domain/infra/router/...`）不強制照搬；HostSpark 維持「少量檔案、扁平結構」的既有風格，只在檔案數明顯超出可讀範圍時才拆子目錄。
- Webhook 模式（兩邊都用 long polling，維持現狀）。
- `mcp`（MCP 伺服器管理）、`mic-serve`（麥克風轉發）、`install`（環境安裝）三個 `agy` 子命令對應的 Telegram 指令——超出「Telegram 遙控 VM 日常任務」的範疇（見第 7 節附錄）。

---

## 9. 原始碼比對落差清單（2026-09-01，逐檔讀完 antigravity-cli-telegram-bot/src 全部 ~5,300 行後核對）

> 第 1-8 節原本是根據對方 **README** 寫的架構級計畫，方向正確但不少細節是猜的。這裡逐一列出讀完實際 TypeScript 原始碼後找到的落差，每項都標了應該歸到哪個 Phase（已同步反映在第 3、4 節），供之後開發時逐條核對，不需要再重讀一次對方原始碼。

### 9.1～9.4：遺漏的整個指令
1. **`/setdefault`（`router/commands.ts`、`usecases/default-settings.ts` 全檔）**——把「目前這個 chat」的 model/effort/mode/sandbox 設定寫回磁碟上的 `.env`，變成**全域、跨重啟**的預設值，不只是這個 chat 自己的設定。這是唯一一個「per-chat 操作會反向影響全域狀態」的指令，語意特殊，寫入時要注意原子性（先寫暫存檔再 rename）。→ Phase 1。
2. **`/verbose <detailed|compact|silent>`（`router/commands.ts`、`usecases/prompt-job.ts`）**——per-chat 設定，控制串流進度訊息的詳細程度，跟 `/effort`/`/mode` 同類但原文件完全沒提到。→ Phase 1。
3. **`/learn` `/compact`（`router/commands.ts`）**——不是新 CLI 能力，只是把使用者輸入包裝成特定 prompt（分別是「把這段對話整理成可重複使用的規則」「壓縮目前對話上下文」）送給 AGY，走一般對話路徑即可，工作量很低。→ Phase 2。
4. **`/restart` `/update`（`router/commands.ts`、`usecases/self-update.ts` 全檔）**——原計畫 Phase 4 明確決定「不透過 Telegram 觸發，只寫腳本＋文件」，理由是怕 Bot 自己重啟自己造成競態。但對方證實這風險可解：靠「in-flight job 持久化＋重啟後自動恢復」（見 9.5）保底，`/restart`/`/update` 由授權使用者透過 `ALLOW_BOT_UPDATE` 開關明確啟用後即可安全使用。→ 建議修正決策，改列入 Phase 4，但要排在 9.5 的機制完成之後。

### 9.5～9.14：行為理解有誤或過度簡化
5. **Crash/restart 韌性：in-flight job 持久化（`state.ts` 的 `setInFlight`/`clearInFlight`/`inFlight`，`bot.ts` 的 `resumeInterruptedJobs`、`announcePendingRestartNotice`）**——執行任務前先把「這個 chat 目前在跑什麼」寫進持久化狀態；Bot 重啟後讀回，主動告知使用者「請求先前被中斷，正在恢復」並自動重新排入佇列。原文件完全沒提到，卻是 9.4 (`/restart`) 能安全落地的前提。→ Phase 4（併入 Job queue 項目）。
6. **`/usage` `/credits` 不是純 PTY（`pty-runner.ts:614-763`）**——真正做法是先呼叫 `agy --print /quota --output-format json --dangerously-skip-permissions`（結構化 JSON，不需要 TUI 擷取），失敗才 fallback 到 PTY 畫面擷取。這比原文件「純 PTY」簡單可靠很多，應該改成 HostSpark 的主要路徑。**但**注意對方的直接路徑無論全域權限政策為何，一律強制帶 `--dangerously-skip-permissions`——HostSpark 是否要在 Safe 模式下也對這種唯讀查詢自動放行，是需要明確決定的安全政策，不是純技術細節。→ Phase 2。
7. **PTY 互動細節（`pty-runner.ts:309-498`，內嵌一段 Python pty 腳本）**——原文件「等待畫面穩定→送出字串→等待輸出穩定」寫得太抽象，實際上要處理：自動偵測並回答「trust this folder」對話框（方向鍵選單 + `\r`，或舊版 `[y/n]` + `y\r`）、偵測「尚未登入」畫面並快速失敗、逐字元分段輸入指令（不是一次寫入整行）、1.8 秒沒回應時重送一次 Enter、用「偵測到 quota 關鍵字後等 0.8 秒／否則等 3.0 秒」的閒置時間判斷畫面已穩定。→ Phase 2，實作 `pty_runner.py` 時要照這個流程做，不能只寫抽象邏輯。
8. **`/usage` 配額「進度指標」（`pty-runner.ts:51-111` `formatQuotaLimitLine`）**——不是單純顯示剩餘百分比，而是算「配額週期已過時間 vs 實際剩餘量」的落差，用 🟢/🟡/🔴/⭐/⚪ + `[+12%]`/`[On track]` 這類標籤呈現使用速度是否正常（例如：時間過了 50% 但配額只用了 20%，代表「超前」）。原文件完全沒提到這個計算邏輯。→ Phase 2。
9. **`/context` 完整結構（`pty-runner.ts:277-307`）**——包含模型名稱、估計用量、分類 token 明細（使用者訊息／代理回覆／工具呼叫／系統提示／系統工具／Skills／子代理）、剩餘可用空間、checkpoint 資訊、artifact 數量。原文件只有一行帶過。→ Phase 2。
10. **`--sandbox` 強制附加也適用於 `/agy` passthrough（`usecases/custom-agy.ts:16-21`）**——全域「sandbox 開啟且不允許停用」時，`/agy` 呼叫若沒帶 `--sandbox`，要強制附加，不能讓 passthrough 繞過全域政策。原文件的 `/sandbox` 說明只涵蓋一般對話路徑。→ Phase 3。
11. **`/agy` 參數驗證比原文件的危險清單更嚴格（`agy-runner.ts:93-103` `validateCustomArgs`）**——兩條規則：(a) **一律拒絕 `--prompt-interactive`/`-i`**（防止觸發無 TTY 可互動、會卡死的 session，這是比危險子命令判斷更基本的防線，優先度最高）；(b) 除非第一個參數是已知唯讀子命令（`agent agents changelog help install models plugin plugins update`）或 `--help/-h/--version/-v`，否則必須帶 `--print`/`-p`/`--prompt`。原文件完全沒提到規則 (a)。→ Phase 3。
12. **危險子命令判斷條件錯誤（`usecases/custom-agy.ts:9-14` `isDangerousCustomCommand`）**——正確條件是「三選一」：① 參數中任何位置出現 `--dangerously-skip-permissions`（獨立觸發）；② 子命令是 `update` 或 `install`；③ 子命令是 `plugin`/`plugins` 且動作是 `install|uninstall|enable|disable|import|link`。原文件寫的「plugin install/remove」用詞錯誤（是 `uninstall` 不是 `remove`）且漏列 `enable/disable/import/link` 與獨立的 `--dangerously-skip-permissions` 觸發條件。→ Phase 3。
13. **`/agent NAME` 是寫入操作，不是唯讀（`router/commands.ts:167-177` 對比 `commands.ts:297-301` 的 `/agents`）**——`/agent NAME` 寫入 `ChatStateStore` 的 per-chat 設定（跟 `/project` 同性質），`/agents`（唯讀，列出可用 agent）才是單純呼叫 `agy agents`。原文件把兩者混在同一批「都是唯讀」的描述裡。→ Phase 3。
14. **`/continue` 是雙重語意，不是單純布林開關（`router/commands.ts:266-279`）**——無參數或帶 `list` 時，行為等同 `/resume`（瀏覽並選擇歷史對話）；只有帶 `on`/`off` 才是切換 `ChatStateStore.continue_enabled`。原文件只把它當成單純的布林寫入指令。→ Phase 1（`/continue` 無參數）+ Phase 3（`/continue on|off`）。

### 9.15～9.17：Phase 3 沒完全涵蓋的安全機制
15. **子程序環境隔離範圍更大（`infra/safe-env.ts` 全檔）**——不是只從子程序環境移除 `TELEGRAM_BOT_TOKEN`，要連 `TELEGRAM_ALLOWED_USER_IDS`／`TELEGRAM_ALLOWED_CHAT_IDS`（白名單本身）都移除，並固定設定 `NO_COLOR=1`、`TERM=dumb`；而且套用範圍是**所有** AGY 子程序呼叫（`agy-runner.ts:111,299`），不是只有 PTY 路徑。→ Phase 2（隨 `run_agy()` 重構一起做，越早做越好，不用等到 Phase 3）。
16. **Instance lock 要處理殘留鎖接管（`infra/instance-lock.ts:9-54`）**——檢查 lock 檔裡的 PID 是否還存活（`kill(pid, 0)`），若程序已死，刪除舊 lock 並重新取得。原文件只說「PID 檔案防止重複啟動」，沒提到這個殘留鎖處理——沒有這段，Bot 崩潰後 systemd 的 `Restart=on-failure` 會被舊 lock 卡死，永遠起不來。→ Phase 4。
17. **輸出媒體回傳＋SSRF 防護是整個獨立方向的功能（`telegram/media-resolver.ts` 全檔 + `usecases/image-detection.ts` 全檔）**——原文件 Phase 2 的附件功能只涵蓋「Telegram → HostSpark」方向。對方另外掃描 **AGY 輸出文字**裡的本地檔案路徑／公開圖片 URL，驗證後把檔案／圖片**回傳**給 Telegram（例如 AGY 產生一張圖或一份報表後直接傳回聊天室）。驗證分兩層：本地路徑只允許白名單目錄（`/tmp`、`/var/tmp`、AGY artifact 目錄、workspace root）；遠端 URL 要做完整私有/保留 IP 檢查，且要防 DNS rebinding（不能只查字面 IP，要在 DNS 反解後對真正解析出來的 IP 再查一次）。→ 獨立列為 Phase 5，複雜度高於一般附件上傳。

### 9.18～9.21：Queue／串流行為的落差
18. **Queue 是全域單一佇列，不是 per-chat（`queue.ts` 全檔、`bot.ts:20-30`）**——全站同時只執行一個任務，`/status`／`/cancel` 只是「過濾顯示屬於這個 chat 的項目」。這跟 HostSpark 現有的全域 `agy_lock` 精神一致，**維持全域序列化的決定不用改**，原文件 Phase 0 提到的「多使用者」只影響「設定互相獨立」，不代表要做成多工並行執行——這點原文件沒有明確講清楚，容易被誤讀成要做 per-chat 平行佇列。→ Phase 4（維持設計，不新增工作量）。
19. **Auto-interrupt 合併機制的具體做法（`usecases/enqueue.ts` 全檔）**——`AGY_AUTO_INTERRUPT=true` 時：新訊息進來，先收集「執行中任務的 prompt + 該 chat 所有排隊中的 prompt」，把新訊息用 `[Update / Follow-up]: <文字>` 格式接在後面合併成一個 prompt，**取消**原本執行中/排隊中的任務，改執行合併後的單一任務。原文件只有 README 等級的一句話帶過（「zero-loss 合併」），沒有具體合併順序與格式。→ Phase 4。
20. **超長回覆改傳檔案（`usecases/prompt-job.ts:295`）**——AGY 回覆超過 `2 × max_chunk_size` 時，改成上傳 `.md` 文件附件，而非切成多則文字訊息。原文件的串流/切割設計沒提到這個 fallback。→ Phase 2。
21. **進度訊息結束狀態可設定（`config.ts:90-97`、`usecases/prompt-job.ts:253-274`）**——`TELEGRAM_PROGRESS_MODE=full|compact|delete`：串流結束後「思考中」訊息分別是「展開完整統計」「收合成一行摘要」「直接刪除」。原文件的 Phase 1 串流設計沒提到這是可設定的行為。→ Phase 1。

### 9.22：低優先／裝飾性
22. **`telegram/markdown-renderer.ts`（550 行）比原文件假設的「沿用現有 Markdown/HTML 轉換」複雜很多**：LaTeX 轉 Unicode（`\frac` `\sqrt`、希臘字母、上下標、`\text{}`）、GitHub 風格 `> [!NOTE]` 等提示區塊轉成 emoji 前綴段落、Markdown 表格轉卡片列表（因為 Telegram HTML 沒有 `<table>`）。如果 HostSpark 現有 `agy_bot_core.md_to_telegram_html` 已經夠用，這項可以無限期延後，純屬呈現美觀，不影響任何功能正確性。→ Phase 5，最低優先。

### 總結
第 1-8 節的核心架構決策（Phase 0 的狀態存放設計、Phase 1 的 argv/session 模型、Phase 3 的 pending-action/passthrough 模式）跟原始碼的實際設計意圖是吻合的，**不需要推翻重寫**。以上 22 項落差已經就地補進第 2、3、4 節對應位置，這節純粹是「核對紀錄」，之後不需要再重讀一次對方原始碼確認同樣的事。
