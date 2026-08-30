# Antigravity CLI (AGY) Telegram VM Bridge 深度技術教學課程

> **課程目標**：本手冊為大型語言模型（LLM）與進階軟體工程師設計，旨在全面解析「Telegram → Antigravity CLI (`agy`) 輕量安全橋接器」的架構原理、安全模型、代碼實作、定時任務排程機制、系統維運與擴充實務。

---

## 目錄

- [第一章：專案定位與核心架構哲學](#第一章專案定位與核心架構哲學)
- [第二章：指令手冊與人機互動設計](#第二章指令手冊與人機互動設計)
- [第三章：主機級定時排程架構深度解析](#第三章主機級定時排程架構深度解析)
- [第四章：安全模型與權限控制體系](#第四章安全模型與權限控制體系)
- [第五章：代碼架構與模組職責剖析](#第五章代碼架構與模組職責剖析)
- [第六章：部署安裝與 systemd 運維實務](#第六章部署安裝與-systemd-運維實務)
- [第七章：測試驗證、故障排除與實戰演練](#第七章測試驗證故障排除與實戰演練)

---

## 第一章：專案定位與核心架構哲學

### 1.1 專案目標
本專案是一個專為私人 Ubuntu VM（如國網、晶創雲、AWS、GCP 等環境）設計的輕量 **Telegram → Antigravity CLI (`agy`) 橋接器**。它讓管理員能夠在手機端透過 Telegram 隨時操控伺服器上的 AGY AI Agent，進行即時指令執行、系統巡檢與自動化定時排程。

### 1.2 設計邊界：非重複發明輪子
本專案**不是**另一個龐大的 AI Agent 框架（如 OpenClaw、Hermes、LangChain），它遵循 Unix 哲學：
* **Telegram**：提供手機端隨身互動介面與安全通訊管道。
* **本 Bridge**：負責身分驗證（單一數字 User ID）、請求轉發、超時控制、秘密過濾、結果排版，以及**主機層級的定時排程觸發**。
* **AGY CLI**：負責大語言模型推理、多輪對話上下文、工具呼叫（Tool Use）、檔案操作與終端指令執行。
* **Ubuntu VM**：提供計算、檔案系統、Docker 容器與 systemd 系統資源。

```text
┌─────────────────────────────────────────────────────────┐
│                     Telegram Client                     │
│                (手機 / 電腦端管理員介面)                   │
└────────────────────────────┬────────────────────────────┘
                             │ HTTPS (長輪詢 Long Polling)
                             ▼
┌─────────────────────────────────────────────────────────┐
│               AGY Telegram Bridge (Python)              │
│  ├─ 驗證層 (ALLOWED_USER_ID 白名單過濾)                  │
│  ├─ 控制層 (斜線指令 / SQLite 排程器 / 執行鎖)            │
│  └─ 安全層 (Timeout / Output Cap / 敏感字串遮罩)         │
└────────────────────────────┬────────────────────────────┘
                             │ Local Subprocess (agy -p)
                             ▼
┌─────────────────────────────────────────────────────────┐
│                   Antigravity CLI (agy)                 │
│         (模型推理 / 工具呼叫 / 權限審核 / Workspace)      │
└────────────────────────────┬────────────────────────────┘
                             │ Local Execution
                             ▼
┌─────────────────────────────────────────────────────────┐
│                     Ubuntu Linux VM                     │
│               (檔案 / Docker / 服務 / 系統資源)          │
└─────────────────────────────────────────────────────────┘
```

### 1.3 核心架構哲學：確定性控制 vs 概率性推理
在 AI 系統工程中，混淆「控制指令」與「AI 對話」是導致系統不穩定與幻覺（Hallucination）的主因：
* **確定性控制面（Deterministic Control Plane）**：排程的建立、列表、暫停、恢復、刪除，以及工作階段重置，**必須由確定性的斜線指令（Slash Commands）與 SQLite 資料庫控制**。
* **概率性推理面（Probabilistic Reasoning Plane）**：任務的具體目標描述、程式碼撰寫、故障排查邏輯，交由大語言模型（AGY）以**自然語言**進行彈性推理。

---

## 第二章：指令手冊與人機互動設計

### 2.1 完整指令清單

| 指令 | 類型 | 說明 | 範例 |
|---|---|---|---|
| `/start` 或 `/help` | 資訊 | 顯示機器人狀態、目前權限模式（Safe/Full）及完整指令指南 | `/help` |
| `/status` | 維運 | 即時檢查 VM 運行狀態（Uptime、負載、磁碟剩餘、記憶體與 Docker） | `/status` |
| `/clear` | 工作階段 | 清除當前 AGY 對話上下文，開啟全新獨立的問答階段 | `/clear` |
| `/schedule_help` | 排程 | 查看定時排程的 cron 語法、時區、可用變數與安全限制 | `/schedule_help` |
| `/schedule_add` | 排程 | 建立定時任務（先經 AGY 整理提示詞並在 Telegram 預覽確認） | `/schedule_add */3 * * * * 查詢台灣天氣` |
| `/schedule_list` | 排程 | 列出目前所有已註冊、已啟用或已暫停的定時任務 | `/schedule_list` |
| `/schedule_show <ID>`| 排程 | 查看特定排程的完整細節、執行次數統計與 AGY Prompt 模板 | `/schedule_show 1` |
| `/schedule_pause <ID>`| 排程 | 暫停指定排程（保留設定但暫停定時觸發） | `/schedule_pause 1` |
| `/schedule_resume <ID>`| 排程 | 恢復暫停的排程，並自動重新計算下一次執行時間 | `/schedule_resume 1` |
| `/schedule_delete <ID>`| 排程 | 永久刪除指定的定時任務與相關資料 | `/schedule_delete 1` |
| `一般純文字` | 即時對話 | 直接交給 AGY 進行一般對話問答或單次指令執行 | `幫我檢查 Nginx 設定檔語法` |

### 2.2 核心互動口訣
> 📌 **「斜線指令管排程系統，自然語言寫任務內容」**
> - **管理排程（查、刪、停、啟）** ➜ 敲 `/schedule_*` 指令。
> - **排程要執行的工作內容** ➜ 寫自然語言即可。

---

## 第三章：主機級定時排程架構深度解析

### 3.1 為什麼需要主機層級排程？
若讓 LLM 在交談時直接接收「請每 1 分鐘幫我報天氣」，LLM 可能會在單次 headless CLI 執行中開啟內部無限迴圈，導致 Telegram Bot 的單次對話進程卡死（Hang）。
v0.2 架構引入 **Host-level Scheduler**，實現完全解耦：

```text
[Telegram /schedule_add] 
       ↓ (1. 呼叫 AGY 整理為獨立任務模板)
[AGY Prompt Refinement] 
       ↓ (2. Telegram 彈出 Inline Keyboard 按鈕)
[管理員點擊確認建立]
       ↓ (3. 寫入 SQLite schedules.db)
[背景輪詢 Schedule Loop (每 20 秒)]
       ↓ (4. 到期時喚醒獨立子程序執行 agy -p)
[推播結果至 Telegram]
```

### 3.2 關鍵排程機制設計

#### A. 兩階段確認機制（Two-Phase Confirmation）
1. 使用者輸入：`/schedule_add */15 * * * * 檢查磁碟容量若超過80%通知我`
2. Bot 啟動獨立進程要求 AGY 重寫為可重複獨立執行的 Prompt 模板（此階段強制以 Safe 權限執行）。
3. Bot 產生隨機 Token 並在 Telegram 呈現 Inline Keyboard：
   `[ ✅ 確認建立 (schedule_confirm:token) ]` `[ ❌ 取消 (schedule_cancel:token) ]`
4. 唯有經過管理員人工審核點擊確認，排程才會正式寫入 SQLite。

#### B. 工作目錄隔離（Workspace Isolation）
* 每個排程分配獨立工作目錄：`~/.local/state/agy-telegram-bot/workspaces/schedule-<ID>`。
* 執行時透過 `--add-dir` 開放主要專案目錄（`AGY_WORKDIR`）。
* **優點**：排程執行不會使用一般對話的 `--continue` 階段，**絕不污染**使用者的日常對話歷史。

#### C. 全域並行鎖（Asyncio Concurrency Lock）
* 排程執行與人工即時對話共用單一 `agy_lock = asyncio.Lock()`。
* 保證同一時間伺服器上只有一個 AGY 進程在運行，杜絕並行磁碟衝突與資源競爭。

#### D. 執行時變數替換（Runtime Variables）
在 Prompt 模板中可包含動態時間標籤，觸發時由 Python 自動代入：
* `{{now}}`：實際執行時間（ISO 格式）。
* `{{date}}`：實際執行日期（YYYY-MM-DD）。
* `{{time}}`：實際執行時間（HH:MM:SS）。
* `{{timezone}}`：排程時區（例如 `Asia/Taipei`）。
* `{{scheduled_at}}`：原訂執行時間。
* `{{run_number}}`：累計執行序號。
* *其餘未知 `{{變數}}` 保持原樣不替換。*

#### E. 靜默回報機制（`[NO_REPORT]`）
對於例行性健康巡檢（例如「正常時不要發訊息」），AGY 整理後的 Prompt 會約定：若一切正常無須通知，只輸出精確字串 `[NO_REPORT]`。
Bot 偵測到該值後，會將狀態標記為成功，但**主動抑制 Telegram 訊息傳送**，防止通知洗版。

#### F. 熔斷保護機制（Circuit Breaker）
* 當某個排程因外部 API 異常或命令錯誤**連續失敗 3 次**：
  1. 系統自動將該排程標記為暫停（`enabled=0`）。
  2. 清除下次執行時間。
  3. 即時主動推播告警訊息至 Telegram，通知管理員排障並以 `/schedule_resume <ID>` 恢復。

#### G. 重啟持久化與防集中補跑
* 排程資料保存於 SQLite（`schedules.db`）。
* 若主機停機 1 小時（錯過 4 次執行），重啟後 `claim_due()` 算法會自動跳過歷史過期時間，**最多只補跑一次**，並將下次時間對齊未來。

---

## 第四章：安全模型與權限控制體系

### 4.1 身分驗證機制
* 程式啟動時強制檢查 `ALLOWED_USER_ID`。
* 不支援「首次發送 `/start` 自動綁定」，杜絕未授權存取時間差。
* 任何非授權的 Telegram ID 訊息一律拒絕並記錄日誌。

### 4.2 Safe 模式 vs Full 模式

```text
┌──────────────────────────────────────────────────────────────┐
│                    AGY_PERMISSION_MODE                       │
├──────────────────────────────┬───────────────────────────────┤
│             safe             │              full             │
│   (開源專案 / 預設安全模式)   │   (私人專用 VM / 全自動維運)   │
├──────────────────────────────┼───────────────────────────────┤
│ • 不帶 --dangerously-... 參數│ • 自動加上 --dangerously-...  │
│ • 工具呼叫需確認時會被拒絕   │ • 工具操作自動核准無須手動確認 │
│ • 權限不足時回傳友善提示     │ • 適合無人值守但需承擔風險     │
└──────────────────────────────┴───────────────────────────────┘
```

### 4.3 敏感資訊防護與過濾
* **正則脫敏（Redaction）**：內建過濾機制，自動將輸出中的 Telegram Bot Token、Bearer Token、API Key、密碼遮罩為 `[REDACTED]`。
* **檔案權限**：`.env` 與 `schedules.db` 強制限制為 `600`（僅服務使用者可讀寫）。

### 4.4 Sudo 權限與 NOPASSWD 指南
* **原則**：Bot 正常運作（含排程、Docker ps、磁碟查詢）**不需要** root 或 sudo 權限。
* **進階自動維運需求**：若要讓 AGY 能自動執行 `sudo apt update` 或服務重啟：
  ```bash
  # 啟用免密碼 sudo（僅限專用測試/維運 VM）
  echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/$USER" && sudo chmod 0440 "/etc/sudoers.d/$USER"
  
  # 維運完成後還原安全狀態
  sudo rm -f "/etc/sudoers.d/$USER"
  ```

---

## 第五章：代碼架構與模組職責剖析

專案由三個核心 Python 模組構成：

```text
telegram_agy_bot/
├── agy_bot_core.py      # 底層子程序控制、設定驗證、字串過濾與 Markdown 處理
├── schedule_store.py    # SQLite 排程資料庫操作、Croniter 運算、變數渲染
├── bot.py               # Telegram Application 邏輯、斜線指令路由、長輪詢與背景 Loop
└── tests/               # 完整單元與整合測試套件 (40 個測試案例)
```

### 5.1 `agy_bot_core.py` 核心功能
* `load_config(environ)`：從環境變數載入並嚴格驗證所有組態（Token 格式、User ID 正整數、時區合法性、區間下限）。
* `run_process(args, cwd, env, timeout_seconds, max_output_bytes)`：
  - 非同步執行 CLI。
  - 支援 Linux Process Group 終止 (`os.killpg(process.pid, signal.SIGTERM/SIGKILL)`)。
  - 限制最大捕捉位元組數（防止記憶體耗盡）。
* `split_markdown_into_chunks(text, max_chunk_size)`：
  - 智慧切割長文本，優先在換行處截斷，確保 Telegram 4096 字元限制不爆版。
* `md_to_telegram_html(text)`：
  - 將 Markdown 的 Code block、Bold、Italic 轉為 Telegram 支援的 HTML 標籤。

### 5.2 `schedule_store.py` 核心功能
* `normalize_cron(expression, timezone_name, minimum_minutes)`：
  - 驗證五欄 cron 格式。
  - 遍歷未來 32 次觸發點，確保任意兩次間隔不小於 `minimum_minutes`。
* `render_prompt_variables(template, ...)`：
  - 執行模板字串替換。
* `ScheduleStore` 類別：
  - `add()`, `get()`, `list_all()`, `pause()`, `resume()`, `delete()`：完整 CRUD。
  - `claim_due(now)`：領取到期任務並原子化更新下次執行時間。
  - `record_result(schedule_id, success, error)`：記錄成功/失敗，並於連續 3 敗時觸發熔斷回傳 `auto_paused=True`。

### 5.3 `bot.py` 核心功能
* Telegram `CommandHandler` 與 `CallbackQueryHandler` 註冊。
* `handle_message()`：處理一般即時對話，並啟動 `keep_typing()` 定時發送輸入中動畫。
* `schedule_loop()`：背景非同步工作（每 20 秒檢查一次 SQLite 到期任務並執行）。
* `--check-config`：提供 CLI 設定語法驗證進入點。

---

## 第六章：部署安裝與 systemd 運維實務

### 6.1 環境需求
* Ubuntu 20.04 / 22.04 / 24.04 LTS。
* Python 3.10+。
* 已安裝並登入的 `agy` CLI（確認 `agy -p "reply ok"` 正常）。
* 已從 `@BotFather` 取得的 Telegram Bot Token。

### 6.2 一鍵安裝與自動化服務配置（`install.sh`）
`install.sh` 會自動執行以下標準化作業：
1. 檢查目前是否為非 root 的一般使用者。
2. 偵測並使用 `uv` 或標準 `python3 -m venv` 建立虛擬環境。
3. 依據 `requirements.lock` 安裝鎖定依賴套件。
4. 執行 `python bot.py --check-config` 進行組態防呆驗證。
5. 動態產生安全加固的 systemd unit 檔案（啟用 `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`）。
6. 自動重新加載 daemon 並啟用服務開機自啟。

```bash
# 標準安裝指令
git clone https://github.com/gemini960114/agy-telegram-bot.git
cd agy-telegram-bot
cp .env.example .env
chmod 600 .env
nano .env   # 填入 Token, ALLOWED_USER_ID, AGY_PERMISSION_MODE
chmod +x install.sh
./install.sh
```

### 6.3 系統服務維運指令表

```bash
# 查看服務運行狀態
sudo systemctl status agy-telegram.service

# 查看即時動態日誌 (追蹤 Telegram 請求與排程觸發)
sudo journalctl -u agy-telegram.service -f

# 重新啟動服務 (修改 .env 後需重啟)
sudo systemctl restart agy-telegram.service

# 停止服務
sudo systemctl stop agy-telegram.service
```

---

## 第七章：測試驗證、故障排除與實戰演練

### 7.1 本地自動化測試套件
專案具備完整的單元測試與整合模擬測試：
```bash
# 執行全部 40 項測試案例
venv/bin/python -m unittest discover -s tests -v

# 檢查 Python 語法
venv/bin/python -m py_compile bot.py agy_bot_core.py schedule_store.py

# 驗證設定檔
venv/bin/python bot.py --check-config
```

### 7.2 實戰演練（Labs）

#### 🧪 Lab 1：建立基礎狀態與問答測試
1. 在 Telegram 傳送 `/start` ➜ 確認顯示歡迎訊息與模式。
2. 傳送 `/status` ➜ 確認回報 Uptime、磁碟與記憶體。
3. 傳送 `請記住暗號 ALPHA` ➜ 確認 AGY 回應。
4. 傳送 `剛才暗號是什麼？` ➜ 確認延續對話回覆 ALPHA。
5. 傳送 `/clear` ➜ 重置對話階段。

#### 🧪 Lab 2：建立高頻天氣輪播定時排程
1. 傳送：`/schedule_add */3 * * * * 查詢台灣各縣市天氣狀況，由北至南輪流播報一個縣市，簡短回報`
2. 檢視 Bot 回傳的 Prompt 預覽，點擊 **[✅ 確認建立]**。
3. 傳送 `/schedule_list` ➜ 確認 ID #1 狀態為「啟用」，下次執行時間正確。
4. 等候 3 分鐘 ➜ Bot 自動主動推播天氣資訊。
5. 傳送 `/schedule_delete 1` ➜ 刪除排程，再次 `/schedule_list` 確認已清空。

#### 🧪 Lab 3：建立靜默異常巡檢排程（`[NO_REPORT]` 測試）
1. 傳送：`/schedule_add 0 * * * * 檢查磁碟剩餘空間，若使用率未達 90% 則只輸出 [NO_REPORT]`
2. 點擊確認建立。
3. 整點到達時，若磁碟正常，Telegram 不會收到干擾通知；於 `/schedule_show <ID>` 可見執行次數增加且狀態為 `success`。

---

### 7.3 常見故障與排除 SOP

| 現象 / 錯誤 | 原因分析 | 處置方式 |
|---|---|---|
| 輸入「停止排程」後 Bot 回覆已停止但排程仍在跑 | 純文字輸入會被當作一般 AI 對話處理，AI 產生幻覺確認但無法修改 DB | 必須使用斜線指令 `/schedule_delete <ID>` 或 `/schedule_pause <ID>` |
| Bot 顯示「思考與執行中」長達數分鐘不回應 | 對話中要求 AI 自行輪詢或耗時命令卡住 | 終端機執行 `ps aux \| grep agy` 找出該子進程並 `kill <PID>` 中止 |
| `ModuleNotFoundError: No module named 'croniter'` | 虛擬環境未同步鎖定套件 | 執行 `venv/bin/python -m pip install -r requirements.lock` |
| 排程收到「排程已自動暫停」通知 | 任務連續失敗 3 次觸發熔斷保護 | 使用 `/schedule_show <ID>` 查看上次錯誤原因，修復後以 `/schedule_resume <ID>` 恢復 |
| 收到 `Safe 模式權限拒絕` | 任務需要執行受限制的系統工具 | 評估是否真需 root/修改權限；若確認安全可在 `.env` 切換為 `AGY_PERMISSION_MODE=full` |

---

## 總結
本教學文件完整涵蓋了 Antigravity Telegram VM Bridge 的架構精髓。透過嚴謹的「指令控制 vs 自然語言推理」分層、獨立 Workspace 隔離機制、SQLite 持久化與熔斷保護，建構出一個兼具智慧性、穩定度與高度安全性的生產級 VM 遙控助理。
