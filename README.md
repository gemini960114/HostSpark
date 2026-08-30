# Antigravity CLI Telegram Ubuntu VM Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://www.python.org/)

一個專為私人 Ubuntu VM 設計的輕量 **Telegram → Antigravity CLI (`agy`) 橋接器**。正式支援目標是國網、晶創雲等採用一般 Ubuntu 使用者與 systemd 的 VM。

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

不要替 Bot 使用者設定 `NOPASSWD:ALL`。若使用者位於 Docker 群組，也應理解 Docker 控制權通常足以取得接近 root 的主機控制能力。

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

## 使用

- `/start` 或 `/help`：顯示目前執行模式與指令。
- `/status`：顯示 VM 健康狀態；Docker 為選用項目。
- `/clear`：建立新的 AGY 工作階段。
- `/schedule_help`：顯示 cron 格式、範例與可用變數。
- `/schedule_add`：讓 AGY 整理任務 prompt，預覽確認後建立排程。
- `/schedule_list`：列出所有排程與下次執行時間。
- `/schedule_show ID`：查看原始要求、完整 prompt 與執行狀態。
- `/schedule_pause ID`、`/schedule_resume ID`、`/schedule_delete ID`：管理排程。
- 一般文字：交給 AGY 執行。

```bash
sudo systemctl status agy-telegram.service
sudo journalctl -u agy-telegram.service -f
sudo systemctl restart agy-telegram.service
sudo systemctl stop agy-telegram.service
```

## AGY 定時任務

定時任務使用標準五欄 cron 表達式，時間依 `AGY_SCHEDULE_TIMEZONE` 解讀。新增時，Bot 會先要求 AGY 將原始需求整理成每次皆可獨立執行的完整 prompt；只有管理員在 Telegram 預覽並按下「確認建立」後才會啟用。

```text
/schedule_add 分 時 日 月 週 任務內容
```

例如每小時整點查詢一次天氣：

```text
/schedule_add 0 * * * * 查詢台北目前天氣與未來三小時降雨機率，簡短回報
```

例如每天 09:00 檢查 VM：

```text
/schedule_add 0 9 * * * 檢查 VM、服務與 Docker 狀態，摘要需要注意的異常
```

常用 cron：

| cron | 執行時間 |
|---|---|
| `0 * * * *` | 每小時整點 |
| `*/30 * * * *` | 每 30 分鐘 |
| `0 9 * * *` | 每天 09:00 |
| `0 9 * * 1-5` | 週一至週五 09:00 |

可在 prompt 使用以下執行時變數：

- `{{now}}`：實際執行時間。
- `{{date}}`、`{{time}}`：執行日期與時間。
- `{{timezone}}`：排程時區。
- `{{scheduled_at}}`：原訂執行時間。
- `{{run_number}}`：包含本次的執行序號。

其他未知的 `{{變數}}` 會原樣保留，交由 AGY 或使用者的工具處理。若任務要求「沒有異常就不要通知」，AGY 整理後的 prompt 會要求無須通知時只輸出 `[NO_REPORT]`，Bot 收到該精確值便不傳送訊息。

排程資料預設保存在 `~/.local/state/agy-telegram-bot/schedules.db`。Bot 重啟後仍會保留，但停機期間錯過的多次執行只會在恢復後執行一次，避免集中補跑。每個排程使用獨立 AGY workspace，並透過 `--add-dir` 存取主要 `AGY_WORKDIR`，不會改變一般問答使用 `--continue` 的最近對話。

所有排程與人工問答共用單一 AGY 執行鎖；同一時間只執行一項任務。連續失敗三次的排程會自動暫停並通知管理員。最短間隔與數量上限可由 `.env` 設定。

> [!WARNING]
> 定時任務會在無人監看時執行。實際執行沿用 `AGY_PERMISSION_MODE`；Full 模式也會自動核准排程發起的工具操作。不要把 Token、密碼、私鑰或其他憑證寫入排程，因為原始要求與完整 prompt 會保存在 SQLite。

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
