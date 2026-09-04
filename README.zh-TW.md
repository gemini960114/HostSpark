*[English](README.md) | 繁體中文*

# HostSpark

### 一觸即燃

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://www.python.org/)

![HostSpark 在 Telegram 回應 /status 的畫面](assets/telegram-status.png)

**HostSpark** 是一個專為 Linux / Ubuntu 主機設計的 24/7 自主 AI 代理系統，透過 Telegram 與 Antigravity CLI (`agy`) 將主機轉化為隨身可控的專屬工程師與維運助手。正式支援目標是國網、晶創雲、各大雲端 VPS 與採用一般 Ubuntu 使用者與 systemd 的伺服器。

本專案不是另一套 AI Agent 框架，也不自行實作模型、推理引擎或電腦控制工具。Telegram 負責提供手機通訊介面，本 Bot 只負責授權驗證、請求轉送、串流即時輸出、工作階段隔離、定時觸發、timeout、錯誤處理及訊息格式；真正的 AI 推理、工具調用、檔案操作與系統控制能力均由 AGY 提供。

```text
Telegram (手機 / 桌面)
   ↓
HostSpark Bot（身分授權／Per-chat 狀態／佇列控制／串流即時更新／排程器）
   ↓ (非 shell 安全調用，環境隔離，機密過濾)
AGY CLI（模型推理／工具操作／上下文管理／工作階段）
   ↓
Ubuntu VM（檔案系統／Docker 容器／系統服務／硬體資源）
```

> [!WARNING]
> 這是遠端 VM 管理工具，不是一般聊天機器人。Full 模式會自動核准 AGY 的所有工具操作；Telegram 帳號或 Bot Token 失守時，VM 也可能失守。請先閱讀 [SECURITY.md](SECURITY.md)。

## ✨ 核心特色與功能

- **多使用者與 Chat 白名單授權**：支援 `ALLOWED_USER_IDS` 與 `ALLOWED_CHAT_IDS`，支援私訊限制開關 `TELEGRAM_PRIVATE_ONLY`。
- **Per-Chat 獨立狀態管理**：每個 Chat / 使用者各自擁有獨立的 Model、Effort、Mode、Sandbox、Verbose 與 Workspace 設定，互不干擾。
- **即時串流輸出（Live Stream Feedback）**：即時回報思考與工具執行中進度，並支援可設定的進度結束模式（`full` / `compact` / `delete`）。
- **對話工作階段管理**：`/new [名稱]` 選擇或建立一個位於 `AGY_WORKSPACE_ROOT` 底下、有名字的專案目錄當作這個 Chat 的工作目錄，並開啟全新 Session；`/clear` 只重置對話。沒用過 `/new` 的 Chat 仍維持各自專屬、匿名的 AGY 工作目錄，彼此對話內容互不干擾。
- **多模態附件與檔案互動**：支援上傳圖片、文件（`.py`, `.log`, `.pdf`, `.json` 等）直接交由 AGY 分析；AGY 產生的圖片與報表自動透過 Telegram 傳回。
- **配額與使用量即時查詢**：`/usage` / `/quota` / `/credits` 提供結構化進度指標（🟢/🟡/🔴/⭐/⚪ 視覺化標籤與進度落差分析）；`/context` 檢視上下文明細。
- **安全 CLI Passthrough 與兩階段確認**：`/agy [ARGS]` 支援原生 CLI 旗標（強制阻擋 `-i` 互動死鎖，危險指令自動觸發確認）。
- **主機層級定時任務（Scheduler）**：SQLite 持久化、五欄 cron、執行時變數模板、3次失敗自動熔斷保護；執行結果與熔斷警告會廣播給**全部**已授權管理員（多組 `ALLOWED_USER_IDS` 時每位都會收到）。
- **任務佇列與 Auto-Interrupt 合併**：全域單一序列化佇列，連續傳送訊息時自動以 `[Update / Follow-up]` 智慧合併前次指示。
- **進程鎖與崩潰自動恢復**：單一實例鎖（PID 檢查與殘留鎖自動接管）；Bot 重啟時自動恢復未完成任務。
- **每日自動清理**：排程迴圈每天例行清除 `uploads/`、per-chat 與排程專屬工作目錄裡超過 30 天的暫存檔案，避免長期 24/7 運行塞爆磁碟。
- **機密遮罩與安全隔離**：子程序自動過濾 Telegram Token、User ID 白名單、AWS Key、SSH 私鑰與 JWT。

---

## ⚙️ 環境設定（.env）

建立設定檔：

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

### 核心設定項目：

```dotenv
# Telegram Bot API Token（必填，從 @BotFather 取得）
TELEGRAM_BOT_TOKEN=你的_Bot_Token

# 授權操作的 Telegram 數字 User ID（必填，多個以逗號分隔）
ALLOWED_USER_IDS=123456789,987654321

# 必填：safe 遵循 AGY 權限規則；full 自動核准所有 AGY 工具操作
AGY_PERMISSION_MODE=safe
```

### 可選進階設定項目：

```dotenv
# 允許的 Chat/群組 ID（留空代表不限制）
ALLOWED_CHAT_IDS=

# 僅允許私訊操作（1=是, 0=否，預設 1）
TELEGRAM_PRIVATE_ONLY=1

# agy 執行檔路徑（留空時自動搜尋 PATH 與 ~/.local/bin/agy）
AGY_BIN=

# AGY 工作目錄（留空時使用使用者 home）
AGY_WORKDIR=

# 允許切換的模型清單（逗號分隔）。留空時退回程式內建的預設清單，
# 但那份清單不保證跟你的 agy 帳號實際可用的模型一致——
# 建議先在 VM 上執行 `agy models` 取得真實清單再填入這裡。不帶 -high/-medium/-low
# 後綴的基底名稱（例如 gemini-3.8-flash）會跟 AGY_EFFORT／/effort 選單組合成
# agy 認得的完整模型名稱；已帶後綴的名稱則直接照樣使用。
AGY_ALLOWED_MODELS="gemini-3.8-flash,gemini-3.7-flash,claude-sonnet-4-6"

# 新對話預設的推理深度（low|medium|high，預設 high）
AGY_EFFORT=medium

# 執行時思考與指令進度詳細度（detailed=完整展開多行, compact=單行摘要, silent=靜默不刷新，預設 detailed）
AGY_VERBOSE=detailed

# 任務完成後狀態卡片的收尾模式（compact=顯示打勾完成, delete=執行完刪除思考卡片, full=保留耗時與日誌統計，預設 compact）
AGY_PROGRESS_MODE=compact

# 新訊息進入時自動中斷前次任務並合併 Prompt（預設 1）
AGY_AUTO_INTERRUPT=1

# 允許透過 Telegram /restart 與 /update 自我更新（預設 0）
# 開啟前請先讀本節下方「關於 /restart 與 /update」。
ALLOW_BOT_UPDATE=0

# 定時任務時區
AGY_SCHEDULE_TIMEZONE=Asia/Taipei

# 以下皆為可選，留空使用預設值：
# AGY_RULE_PROMPT=          # 每次執行前附加給 AGY 的自訂行為規則（支援約束常駐服務 nohup 背景化等）
# AGY_BOT_NAME="HostSpark"  # Bot 自稱名稱
# AGY_WAITING_MESSAGE=      # 執行中顯示的等待訊息
# AGY_WORKSPACE_ROOT=       # 附件儲存與路徑隔離的根目錄，預設同 AGY_WORKDIR；也是 /new 專案目錄選擇/建立功能的母目錄
# AGY_DEFAULT_PROJECT_DIR=initial  # 在 AGY_WORKSPACE_ROOT 底下預先建立的預設專案子目錄名稱，避免全新安裝時 /new 選單是空的
# AGY_CONVERSATION_DB_PATH= # 保留給未來功能使用，目前無指令會讀取
# AGY_TIMEOUT_SECONDS=600         # 單次 AGY 執行逾時秒數（10~3600）
# AGY_MAX_OUTPUT_BYTES=1000000    # stdout/stderr 各自保留上限
# AGY_SCHEDULE_DB_PATH=           # 排程 SQLite 路徑
# AGY_STATE_DB_PATH=              # Per-chat 狀態 SQLite 路徑
# AGY_SCHEDULE_MIN_INTERVAL_MINUTES=15  # 排程最短間隔（分鐘）
# AGY_SCHEDULE_MAX_TASKS=20             # 排程數量上限
```

---

## 📖 Telegram 指令手冊

### 1. 基礎與狀態
| 指令 | 說明 |
|---|---|
| `/start` 或 `/help` | 顯示歡迎訊息、當前權限狀態與功能完整導覽 |
| `/menu` | 開啟 3-3-2 佈局的常駐快捷功能選單鍵盤（涵蓋系統監控、模型調校、專案工作區與任務控制） |
| `/status` | 檢視 VM 即時負載、磁碟、記憶體、Docker 與任務佇列狀態 |
| `/cancel` | 取消目前 Chat 中正在執行或佇列等待的任務 |

> [!TIP]
> **常駐快捷鍵盤（3-3-2 佈局）**
> 輸入 `/menu` 即可隨時喚出下方快捷鍵盤，依功能與對稱性精心編排：
> ```text
> [ /status ]        [ /model ]   [ /effort ]   ← 系統監控與模型調校
> [ /session ]       [ /new ]     [ /clear ]    ← 專案工作區與對話重置
> [  /schedule_list  ]    [   /cancel   ]       ← 定時排程與緊急取消
> ```

> [!TIP]
> **常駐服務與 Web 應用部署（防終端死鎖規範）**
> 專案內建隨附推薦技能 `web-service-deployer` 位於 [`skills/web-service-deployer/`](skills/web-service-deployer/SKILL.md)（執行 `./install.sh` 時會自動同步安裝至 `~/.gemini/config/skills/`）。當請 AI 啟動 Web 服務（如 Vite、React、Flask、FastAPI）或需要手機遠端預覽時，AI 會主動提供 **Nohup 輕量開發** 與 **Docker 容器化** 部署選項，並自動串接 **Cloudflare Quick Tunnel 臨時安全對外連線**，絕不阻塞終端行程。

### 2. 對話與工作階段
| 指令 | 說明 |
|---|---|
| `/new [名稱]` | 選擇或建立 `AGY_WORKSPACE_ROOT` 底下的專案目錄當作工作區（不帶名稱：彈出按鈕清單供選擇），自動以 `--add-dir` 掛載為 Active Workspace 並開啟全新對話 |
| `/clear` | 只重置對話工作階段，保留當前專案目錄；下一則訊息將開啟全新 Session（不延續舊對話） |
| `/continue on\|off` | 切換是否自動延續對話（`--continue`） |
| `/session` | 檢視目前 Chat 的所有設定（所在專案目錄、Model、Effort、Mode、Sandbox 等） |
| `/learn [內容]` | 整理對話經驗與技能為可重複使用的 Skill |
| `/compact` | 壓縮目前對話上下文，保留核心決策與狀態 |

### 3. 模型與執行偏好
| 指令 | 說明 |
|---|---|
| `/model [名稱]` 或 `/models` | 查看可用模型清單或切換當前模型 |
| `/effort low\|medium\|high` | 設定推理深度（Reasoning effort） |
| `/mode plan\|accept-edits` | 切換執行模式（accept-edits 需全域 Full 模式） |
| `/sandbox on\|off` | 開啟或關閉終端機沙箱限制 |
| `/verbose detailed\|compact\|silent` | 設定執行時串流進度詳細度（detailed: 完整展開多行思考日誌, compact: 單行摘要, silent: 靜默不刷新） |
| `/setdefault` | 經確認後將目前 Chat 設定寫回 `.env` 作為全域預設值 |

### 4. 額度、上下文與 CLI Passthrough
| 指令 | 說明 |
|---|---|
| `/usage` / `/quota` / `/credits` | 查詢 AGY 剩餘配額、使用進度與重置時間 |
| `/context` | 檢視上下文用量、分類 Token 明細與 Checkpoint |
| `/agy [ARGS...]` | 直接執行原生 `agy` CLI 指令（危險指令自動觸發確認） |
| `/agy_confirm [TOKEN]` | 二次確認並執行具潛在風險的 agy 指令 |
| `/agents`, `/changelog`, `/plugins`, `/version`, `/cli_help` | 唯讀查詢 AGY 內建資訊 |
| `/agent [名稱]`, `/project [ID]`, `/add_dir [路徑]` | 設定當前 Chat 的專屬 Agent、專案或目錄 |
| `/output_format text\|json\|stream-json` | 設定本次 Chat 呼叫 AGY 時使用的輸出格式 |
| `/json_schema <SCHEMA>\|clear` | 設定或清除 `--json-schema` |
| `/log_file <PATH>\|clear` | 設定或清除 `--log-file` |
| `/print_timeout <DURATION>\|clear` | 設定或清除 `--print-timeout`（例如 `5m`、`600s`） |
| `/new_project on\|off` | 切換 `--new-project` |
| `/disable_slash_commands on\|off` | 切換 `--disable-slash-commands` |

### 5. 定時排程管理
| 指令 | 說明 |
|---|---|
| `/schedule_help` | 查看定時任務 cron 語法、變數模板與規則說明 |
| `/schedule_add <cron> <任務>` | 建立定時任務（經 AGY 整理提示詞並彈出預覽確認） |
| `/schedule_list` | 列出所有定時任務清單與下次執行時間 |
| `/schedule_show <ID>` | 查看特定排程之詳細 Prompt 與執行統計 |
| `/schedule_pause <ID>` | 暫停指定排程 |
| `/schedule_resume <ID>` | 恢復指定排程 |
| `/schedule_delete <ID>` | 刪除指定排程 |

> [!TIP]
> 如果你用一般文字提到「排程」「提醒我」或「每 N 分鐘/小時」之類的未來時間需求，Bot 會直接攔截並帶你走 `/schedule_add` 的建立流程（AGY 整理 prompt ＋ Telegram 按鈕二次確認），不會把訊息當一般對話送給 AGY——因為 AGY 若把這類請求當一般對話處理，可能會試圖在單次呼叫中真的「等到那個時間」才回覆，白白佔用全域任務佇列。若你只是剛好聊天內容提到時間、不是真的要排程，在彈出的確認按鈕點「❌ 取消」即可。

### 6. 運維：遠端重啟與更新
| 指令 | 說明 |
|---|---|
| `/restart` | 重新啟動 Bot 服務 |
| `/update` | 於 repo 目錄執行 `git pull origin main`，成功後自動重啟服務 |

兩者預設**停用**，需在 `.env` 設定 `ALLOW_BOT_UPDATE=1` 才能使用。

> [!NOTE]
> `install.sh` 把服務裝成一般使用者身分執行的系統層級 systemd unit，該使用者通常**沒有權限**直接呼叫 `systemctl restart` 觸發自己的服務（會被 polkit 擋下並回報 `Interactive authentication required`）。因此 `/restart`／`/update` 內部會先嘗試 `systemctl restart`，若失敗則改用**非零結束碼**讓程序結束——systemd unit 設有 `Restart=on-failure`，會在數秒內自動重新拉起服務。這代表你不需要額外授予 sudo 或設定 polkit 規則，`/restart`／`/update` 就能正常運作；重啟期間服務會短暫離線約 5 秒。

### 手機端指令自動補齊（選用）

向 `@BotFather` 傳送 `/setcommands`，選擇你的 Bot，貼上以下清單，之後在 Telegram 輸入 `/` 就會跳出中文說明的自動補齊選單（這是精選常用子集，不含全部指令）：

```text
menu - 開啟快捷功能操作鍵盤 (3-3-2 佈局)
status - 檢視主機資源狀態與任務佇列
session - 檢視目前對話設定與專案目錄
new - 切換或建立專案目錄並開啟全新對話
clear - 重置對話工作階段（保留當前專案目錄）
model - 查看或切換當前 AI 模型
effort - 設定推理深度 (low/medium/high)
mode - 設定執行模式 (plan/accept-edits)
sandbox - 開啟或關閉沙箱隔離 (on/off)
verbose - 設定串流進度詳細度 (detailed/compact/silent)
setdefault - 將目前設定寫回全域預設值
usage - 查詢 AGY 額度與配額指標
quota - 查詢剩餘配額與重置時間
context - 檢視上下文與 Token 用量明細
cancel - 取消當前執行或佇列中的任務
agy - 執行原生 AGY CLI 參數
schedule_list - 列出所有定時排程任務
schedule_add - 新增定時排程任務
schedule_help - 查看定時排程 cron 語法與說明
help - 顯示完整功能操作說明
```

---

## 🚀 快速安裝與升級

> 想請 AI agent（Claude 等）代為安裝？請改給它讀 [INSTALL_BY_AI.zh-TW.md](INSTALL_BY_AI.zh-TW.md)，不要用這一節。

```bash
git clone https://github.com/gemini960114/HostSpark.git
cd HostSpark
cp .env.example .env
chmod 600 .env
nano .env
chmod +x install.sh
./install.sh
```

### 驗證設定與測試：

```bash
venv/bin/python bot.py --check-config
venv/bin/python -m unittest discover -s tests -v
```

### 從舊版升級

舊版只支援單一 `ALLOWED_USER_ID`、沒有 per-chat 設定、沒有本文件描述的多數指令。升級前：

1. `sudo systemctl stop agy-telegram.service`，備份現有 `.env`（`cp -p .env .env.bak`）。
2. `git pull` 後對照 `.env.example` 補齊新變數；舊的 `ALLOWED_USER_ID` 會自動相容，不需要立刻改成 `ALLOWED_USER_IDS`，但建議改過去以便之後新增多使用者。
3. `./install.sh` 重新同步依賴與 systemd unit（會用新的 `requirements.lock`，含新增的 `pexpect`、`httpx` 等套件）。
4. 啟動後跑一次 `/start` `/status` 確認基本功能正常，再測試你會用到的新指令。

### Bot Token 若疑似外洩

立即在 `@BotFather` 用 `/token` 重新產生 Token（舊 Token 即刻失效），更新 `.env` 後 `sudo systemctl restart agy-telegram.service`。不要把 Token 貼到 issue、聊天記錄或終端機截圖中；一旦外洩即視為已外洩，即使事後刪除訊息也應輪替。

---

## 🔒 安全性與隱私說明

- **無 Shell 調用**：所有子程序一律透過 `create_subprocess_exec` 呼叫，絕不經過 shell 拼接。
- **路徑防穿透（Path Traversal Defense）**：附件上傳與 `/add_dir` 一律經過 `safe_join` 驗證，限制於工作空間內。
- **SSRF 防護**：AGY 輸出媒體解析具備 DNS 反解與私有/保留 IP 驗證，防止內網探測與 DNS Rebinding。
- **憑證過濾**：日誌與 Telegram 輸出自動過濾 Bot Token、AWS Key、SSH 私鑰與 JWT。

更多細節請參閱 [SECURITY.md](SECURITY.md)。

## 📄 授權

本專案採用 [MIT License](LICENSE)。
