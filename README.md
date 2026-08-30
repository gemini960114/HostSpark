# HostSpark (Antigravity CLI Telegram Linux VM Agent)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://www.python.org/)

**HostSpark** 是一個專為 Linux / Ubuntu 主機設計的 24/7 自主 AI 代理系統，透過 Telegram 與 Antigravity CLI (`agy`) 將主機轉化為隨身可控的專屬工程師與維運助手。正式支援目標是國網、晶創雲、各大雲端 VPS 與採用一般 Ubuntu 使用者與 systemd 的伺服器。

本專案不是另一套 AI Agent 框架，也不自行實作模型、推理引擎或電腦控制工具。Telegram 負責提供手機通訊介面，本 Bot 只負責單一管理員驗證、請求轉送、定時觸發、timeout、錯誤處理及訊息格式；真正的 AI 推理、工具調用、檔案操作與系統控制能力均由 AGY 提供。

```text
Telegram
   ↓
本 Bot（身分驗證／轉送／timeout／回覆格式）
   ↓
AGY CLI（模型／推理／工具／權限／工作階段）
   ↓
Ubuntu VM（檔案／Docker／服務／系統資源）
```

當 AGY 更新模型、工具或電腦操作能力時，本 Bot 通常不需要重新實作這些能力；只有 AGY 的命令列參數、輸出格式或工作階段協定發生破壞性變更時，橋接程式才需要配合更新。

> [!WARNING]
> 這是遠端 VM 管理工具，不是一般聊天機器人。Full 模式會自動核准 AGY 的所有工具操作；Telegram 帳號或 Bot Token 失守時，VM 也可能失守。請先閱讀 [SECURITY.md](SECURITY.md)。

## 專案定位

這個專案適合：

- 已經在 Ubuntu VM 安裝並登入 AGY。
- 只需要透過 Telegram 從手機直接使用 AGY。
- 希望部署簡單、依賴少，而且容易閱讀與稽核橋接程式。
- 不需要額外的多模型 Gateway、長期記憶、多 Agent 或多通訊平台。

這個專案不打算取代 OpenClaw、Hermes 等完整 Agent 平台，也不會重複開發它們提供的多模型、多平台、長期記憶、複雜工作流或 Skills 生態。內建排程只負責依 cron 定時觸發一段獨立 AGY prompt 並回報 Telegram，不是通用工作流引擎。若需求是完整個人 AI 助理平台，應直接選擇成熟框架；若需求是單純、直接地從 Telegram 即時或定時驅動 AGY，則這個 Bridge 提供較小且清楚的解決方案。

## 功能

- 僅接受一個預先設定的 Telegram 數字 User ID。
- 不需要開放額外 inbound port。
- 支援 AGY Safe／Full 兩種執行模式。
- 支援部署者自訂 `AGY_RULE_PROMPT`。
- `/status` 顯示 uptime、磁碟與記憶體；偵測到 Docker 時才附加容器狀態。
- `/clear` 建立新的 AGY 對話工作階段。
- 支援持久化 AGY 定時任務，可新增、預覽、確認、列出、查看、暫停、恢復與刪除。
- 定時任務到期後主動執行 AGY，並將結果傳送給管理員。
- 子程序具備硬 timeout、輸出容量限制、錯誤碼判斷與常見秘密遮罩。

目前只處理 Telegram 文字訊息，不支援語音。

## 必要條件

1. Ubuntu VM 與一般使用者帳號（預設情境為 `ubuntu`，但程式不寫死帳號名稱）。
2. 已安裝並登入 AGY，且 `agy -p "reply ok"` 可正常執行。
3. Python 3.10+；可選擇安裝 `uv` 加速依賴同步。
4. 從 Telegram `@BotFather` 取得 Bot Token。
5. 管理員的 Telegram 數字 User ID。

## 設定

先建立只允許目前使用者讀取的設定檔：

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

必要設定：

```dotenv
TELEGRAM_BOT_TOKEN=你的_Bot_Token
ALLOWED_USER_ID=你的數字_User_ID
AGY_PERMISSION_MODE=safe
```

可選設定：

```dotenv
# 留空時自動從 PATH 或 ~/.local/bin/agy 尋找
AGY_BIN=

# 留空時使用 systemd 服務使用者的 home
AGY_WORKDIR=

AGY_RULE_PROMPT="只操作指定的專案目錄；修改前先說明；使用繁體中文回覆。"
# 留空時預設為 "HostSpark"
AGY_BOT_NAME="HostSpark"
# 留空時預設為 "⏳ HostSpark 正在思考與執行中，請稍候..."
AGY_WAITING_MESSAGE="⏳ HostSpark 正在思考與執行中，請稍候..."
AGY_TIMEOUT_SECONDS=600
AGY_MAX_OUTPUT_BYTES=1000000

# 定時任務設定
AGY_SCHEDULE_TIMEZONE=Asia/Taipei
AGY_SCHEDULE_DB_PATH=
AGY_SCHEDULE_MIN_INTERVAL_MINUTES=15
AGY_SCHEDULE_MAX_TASKS=20
```

`AGY_RULE_PROMPT` 是行為提示，不能取代 AGY permissions、sandbox 或 Ubuntu 權限隔離。

## Safe 與 Full 模式

### Safe（public repo 建議預設）

```dotenv
AGY_PERMISSION_MODE=safe
```

不加入 `--dangerously-skip-permissions`。AGY headless 執行中需要互動核准的操作會依 AGY policy 拒絕，因此部分命令型任務可能無法完成。

### Full（私人專用 VM 才考慮）

```dotenv
AGY_PERMISSION_MODE=full
```

加入 `--dangerously-skip-permissions`，所有 AGY 工具操作不再逐次審核。只應用於可快照、可重建且沒有不必要正式環境密鑰的專用 VM。

### 關於 sudo 權限與 NOPASSWD 設定（選用進階功能）

**預設情況下，Bot 正常運作完全不需要 `sudo` 權限**（一般問答、定時排程、SQLite 資料庫、磁碟與 Docker 查詢皆可在一般使用者權限下執行）。

若您在**私人專用、已建立快照且可隨時重建**的測試 VM 上，希望讓 AGY 具備自動執行系統管理命令（例如 `sudo apt update`、安裝套件或重啟 systemd 服務）的能力，可手動設定免密碼 sudo：

```bash
# 啟用免密碼 sudo（僅限專用測試/維運 VM）
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/$USER" && sudo chmod 0440 "/etc/sudoers.d/$USER"
```

> [!CAUTION]
> 啟用 `NOPASSWD:ALL` 搭配 Full 模式代表 AGY 具備完整的 root 控制能力。**強烈建議在完成特定維運或測試任務後立即執行還原**，避免主機長期暴露在最高權限風險中。

```bash
# 還原免密碼 sudo 設定（回復為需輸入密碼的預設安全狀態）
sudo rm -f "/etc/sudoers.d/$USER"
```

## 安裝

```bash
git clone https://github.com/gemini960114/agy-telegram-bot.git
cd agy-telegram-bot
cp .env.example .env
chmod 600 .env
nano .env
chmod +x install.sh
./install.sh
```

安裝腳本會：

- 使用 uv 或 Python venv 建立隔離環境。
- 每次執行都同步依賴。
- 使用 `requirements.lock` 鎖定直接與間接 runtime 依賴。
- 啟動前執行完整設定驗證。
- 依目前使用者與 repo 實際路徑建立 systemd service。
- 以 `Restart=on-failure` 啟動並驗證服務狀態。

也可讓 AGY 按照 [INSTALL_BY_AI.md](INSTALL_BY_AI.md) 安裝，但 Token、User ID 與 Safe／Full 模式必須由使用者明確提供。

## 從舊版升級

升級不需要刪除原本的 repo 或 `.env`。請先確認目前程式沒有尚未保存的本機修改：

```bash
sudo systemctl stop agy-telegram.service
sudo systemctl cat agy-telegram.service
cd /path/to/your/agy-telegram-bot
git status --short
```

從 `ExecStart` 可以確認舊版實際安裝目錄。如果 `git status --short` 顯示 `bot.py`、`install.sh` 等 tracked files 被修改，先備份或整理修改，不要直接 pull。

將 `.env` 備份到 repo 外，避免被誤加入 Git：

```bash
cp -p .env "$HOME/agy-telegram.env.pre-upgrade"
chmod 600 "$HOME/agy-telegram.env.pre-upgrade"
```

拉取新版：

```bash
git pull --ff-only origin main
git log -1 --oneline
```

新版不再允許第一個 `/start` 使用者自動成為管理員，而且必須明確設定 Safe／Full 模式。編輯原本的 `.env`：

```bash
nano .env
```

至少確認以下內容存在；不要把 Token 直接寫在 shell command 中，以免留在 shell history：

```dotenv
TELEGRAM_BOT_TOKEN=請填入有效Token
ALLOWED_USER_ID=請填入數字UserID
AGY_PERMISSION_MODE=safe

AGY_BIN=
AGY_WORKDIR=
AGY_RULE_PROMPT=
AGY_BOT_NAME=
AGY_WAITING_MESSAGE=
AGY_TIMEOUT_SECONDS=600
AGY_MAX_OUTPUT_BYTES=1000000
AGY_SCHEDULE_TIMEZONE=Asia/Taipei
AGY_SCHEDULE_DB_PATH=
AGY_SCHEDULE_MIN_INTERVAL_MINUTES=15
AGY_SCHEDULE_MAX_TASKS=20
```

舊版固定使用 `--dangerously-skip-permissions`。若要維持相同行為，可明確設定 `AGY_PERMISSION_MODE=full`；若希望使用較保守的新預設，使用 `safe`。

完成設定後重新執行安裝腳本。它會同步鎖定依賴、驗證設定，並依目前使用者、AGY 位置和 repo 實際路徑更新 systemd service：

```bash
chmod 600 .env
chmod +x install.sh
./install.sh
```

最後檢查服務與日誌：

```bash
sudo systemctl status agy-telegram.service --no-pager
sudo journalctl -u agy-telegram.service -n 50 --no-pager
```

再從 Telegram 執行 `/start`、`/status`，以及一個不修改系統的唯讀任務。

## Bot Token 洩漏或遺失

Bot Token 等同 Bot 的控制憑證。任何取得 Token 的人都可能控制 Bot；不要把 Token 貼到 issue、聊天記錄、終端輸出、截圖或 Git commit。Telegram 的官方 BotFather 文件也要求安全保存 Token，並提供 `/token` 重新產生 Token：[Telegram BotFather 文件](https://core.telegram.org/bots/features#botfather)。

如果 Token 曾經出現在不受信任的位置，即使之後刪除訊息，也應視為已洩漏並立即輪替：

1. 在 Telegram 開啟官方 `@BotFather`。
2. 傳送 `/token`，選擇受影響的 Bot，產生新 Token；舊 Token 將失效。
3. 停止服務：

   ```bash
   sudo systemctl stop agy-telegram.service
   ```

4. 使用 `nano .env` 將 `TELEGRAM_BOT_TOKEN` 換成新 Token。不要把新 Token 放在 shell command、issue 或聊天訊息中。
5. 確認權限並重新啟動：

   ```bash
   chmod 600 .env
   venv/bin/python bot.py --check-config
   sudo systemctl restart agy-telegram.service
   sudo systemctl status agy-telegram.service --no-pager
   ```

6. 檢查 repo 與 Git 歷史是否曾提交 Token。如果曾經提交，單純刪除目前檔案不夠；先輪替 Token，再清理 Git 歷史與遠端副本。

## 指令手冊與使用說明

### 完整指令清單

| 指令 | 說明 | 範例 |
|---|---|---|
| `/start` 或 `/help` | 顯示歡迎訊息、目前權限模式（Safe / Full）及指令快速導覽 | `/help` |
| `/status` | 檢查 VM 即時健康狀態（負載、磁碟容量、記憶體與 Docker） | `/status` |
| `/clear` | 重置對話上下文，開啟全新的 AGY 工作階段 | `/clear` |
| `/schedule_help` | 查看定時排程的 cron 語法、可用變數與設定限制 | `/schedule_help` |
| `/schedule_add` | 建立定時任務（先經 AGY 整理提示詞並在 Telegram 預覽確認） | `/schedule_add 0 * * * * 查詢台北天氣並簡報` |
| `/schedule_list` | 列出目前所有已建立、已啟用或已暫停的定時任務 | `/schedule_list` |
| `/schedule_show ID` | 查看特定排程的完整細節、執行統計與 AGY Prompt 模板 | `/schedule_show 1` |
| `/schedule_pause ID` | 暫停指定的定時任務（暫停期間不觸發） | `/schedule_pause 1` |
| `/schedule_resume ID` | 恢復暫停的定時任務，並自動重新計算下一次執行時間 | `/schedule_resume 1` |
| `/schedule_delete ID` | 永久刪除指定的定時任務 | `/schedule_delete 1` |
| 一般文字 | 交給 AGY 進行一般對話問答與單次任務執行 | `請幫我檢查伺服器連線狀態` |

### 服務管理命令（Linux 終端機）

```bash
# 查看服務狀態
sudo systemctl status agy-telegram.service

# 查看即時日誌
sudo journalctl -u agy-telegram.service -f

# 重新啟動服務
sudo systemctl restart agy-telegram.service

# 停止服務
sudo systemctl stop agy-telegram.service
```

## AGY 定時任務（v0.2 新增）

> [!IMPORTANT]
> **排程核心觀念：指令管排程、自然語言寫內容**
> - **排程管理動作（查、停、啟、刪）**：**必須使用 Telegram 專屬斜線指令**（例如 `/schedule_list`、`/schedule_delete 1`、`/schedule_pause 1`），**切勿使用純文字對話**（如直接打「停止排程」）。純文字會被當成一般聊天送給 AI，AI 無法直接修改 Bot 底層資料庫，容易產生虛假確認（幻覺）。
> - **排程要執行的任務內容**：在 `/schedule_add` 後方**完全支援自然語言**（例如 `/schedule_add */3 * * * * 查詢台灣天氣並輪播各縣市`），AGY 會自動理解並整理為標準 Prompt 模板。

### 為什麼採用專屬固定指令（`/schedule_add` 等）？

在 Telegram 中，一般純文字對話屬於「即時單次對話」（使用 `--continue` 延續上下文）。若直接在純文字輸入「幫我每 3 分鐘排程...」或「幫我停止排程...」，會讓 AGY CLI 誤在背景開啟遞迴輪詢或產生無效的純文字回覆。

因此 v0.2 將排程功能提升為**主機系統層級（Host-level Scheduler）**：
1. **穩定與可控**：由系統 SQLite 與主機定時器管理觸發時機，時間到達時才喚醒獨立的 AGY CLI 進程，執行完畢即釋放資源。
2. **兩階段確認機制**：輸入 `/schedule_add` 後，AGY 會先將需求整理成獨立、可重複執行的完整 Prompt，並在 Telegram 彈出 **「✅ 確認建立 / ❌ 取消」** 按鈕，由管理員人工審核後才正式啟用。
3. **工作階段隔離（Workspace Isolation）**：每個排程使用專屬獨立工作目錄，搭配 `--add-dir` 開放專案目錄，**絕不污染**一般日常對話的 `--continue` 歷史記錄。
4. **全域並行鎖（Concurrency Lock）**：排程與人工問答共用 AGY 執行鎖，同一時間只執行一項 AGY 任務，避免資源衝突與競爭。
5. **熔斷保護（Circuit Breaker）**：連續失敗 3 次的排程會自動暫停，並即時推播告警訊息至 Telegram，防止錯誤排程無限空轉消耗 API 額度。
6. **重啟持久化**：重啟 Bot 或 VM 後排程自動保留；停機期間錯過的多次執行在恢復後最多只補跑一次，避免大量集中發送。

### 排程語法與範例

定時任務使用標準五欄 cron 表達式，時間依 `AGY_SCHEDULE_TIMEZONE`（預設 `Asia/Taipei`）解讀：

```text
/schedule_add 分 時 日 月 週 任務內容
```

#### 常用排程範例：

```text
# 每 3 分鐘輪播一次台灣縣市天氣（間隔下限可於 .env 設定）
/schedule_add */3 * * * * 查詢台灣各縣市天氣狀況，由北至南輪流播報一個縣市，簡短回報

# 每 15 分鐘執行一次伺服器資源巡檢
/schedule_add */15 * * * * 檢查系統負載與記憶體，若有異常則簡要回報

# 每天早上 09:00 產出維運摘要
/schedule_add 0 9 * * * 檢查 VM、服務與 Docker 狀態，摘要需要注意的異常

# 週一至週五 18:00 執行下班備份確認
/schedule_add 0 18 * * 1-5 檢查今日備份檔是否正常產生並回報大小
```

#### 常用 cron 參考：

| cron | 執行頻率 |
|---|---|
| `*/3 * * * *` | 每 3 分鐘 |
| `*/15 * * * *` | 每 15 分鐘 |
| `0 * * * *` | 每小時整點 |
| `0 9 * * *` | 每天 09:00 |
| `0 9 * * 1-5` | 週一至週五 09:00 |

### 執行時變數（Runtime Variables）

可在 Prompt 模板中使用以下變數，排程執行時會自動代入實際數值：

- `{{now}}`：實際執行時間（ISO 格式）。
- `{{date}}`：實際執行日期（YYYY-MM-DD）。
- `{{time}}`：實際執行時間（HH:MM:SS）。
- `{{timezone}}`：排程設定時區（例如 `Asia/Taipei`）。
- `{{scheduled_at}}`：原訂排程觸發時間。
- `{{run_number}}`：包含本次在內的累計執行序號。

未知的 `{{自訂變數}}` 會原樣保留，交由 AGY 或外部工具自行處理。

### 靜默回報機制（`[NO_REPORT]`）

若任務屬於「沒有異常就不要通知」（例如例行檢查），AGY 整理後的 Prompt 會要求在無須通知時只輸出精確值 `[NO_REPORT]`。Bot 收到該值後會判定執行成功但**不發送 Telegram 訊息**，避免訊息洗版。

> [!WARNING]
> 定時任務會在無人值守時自動執行。實際執行會沿用 `AGY_PERMISSION_MODE`（Full 模式會自動核准工具操作）。**請勿將 Token、密碼、私鑰等敏感憑證寫入排程內容**，因為原始要求與 Prompt 模板會保存在 SQLite 資料庫中。

## 設定驗證與測試

```bash
venv/bin/python bot.py --check-config
python -m unittest discover -s tests -v
python -m py_compile bot.py agy_bot_core.py schedule_store.py
```

## 隱私提醒

Telegram Bot 訊息屬於雲端 Bot API 資料流，不應視為端對端加密的系統管理通道。不要要求 Bot 回傳 Token、私鑰、完整 `.env`、資料庫備份或其他秘密。程式提供的遮罩只能降低意外洩漏，不能保證辨識所有秘密格式。

## 授權

本專案採用 [MIT License](LICENSE)。
