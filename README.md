# Antigravity CLI Telegram Ubuntu VM Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://www.python.org/)

透過 Telegram 長輪詢，把單一管理員的文字請求交給 Ubuntu VM 上的 Antigravity CLI (`agy`) 執行。正式支援目標是國網、晶創雲等採用一般 Ubuntu 使用者與 systemd 的 VM。

> [!WARNING]
> 這是遠端 VM 管理工具，不是一般聊天機器人。Full 模式會自動核准 AGY 的所有工具操作；Telegram 帳號或 Bot Token 失守時，VM 也可能失守。請先閱讀 [SECURITY.md](SECURITY.md)。

## 功能

- 僅接受一個預先設定的 Telegram 數字 User ID。
- 不需要開放額外 inbound port。
- 支援 AGY Safe／Full 兩種執行模式。
- 支援部署者自訂 `AGY_RULE_PROMPT`。
- `/status` 顯示 uptime、磁碟與記憶體；偵測到 Docker 時才附加容器狀態。
- `/clear` 建立新的 AGY 對話工作階段。
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

## 使用

- `/start` 或 `/help`：顯示目前執行模式與指令。
- `/status`：顯示 VM 健康狀態；Docker 為選用項目。
- `/clear`：建立新的 AGY 工作階段。
- 一般文字：交給 AGY 執行。

```bash
sudo systemctl status agy-telegram.service
sudo journalctl -u agy-telegram.service -f
sudo systemctl restart agy-telegram.service
sudo systemctl stop agy-telegram.service
```

## 設定驗證與測試

```bash
venv/bin/python bot.py --check-config
python -m unittest discover -s tests -v
python -m py_compile bot.py agy_bot_core.py
```

## 隱私提醒

Telegram Bot 訊息屬於雲端 Bot API 資料流，不應視為端對端加密的系統管理通道。不要要求 Bot 回傳 Token、私鑰、完整 `.env`、資料庫備份或其他秘密。程式提供的遮罩只能降低意外洩漏，不能保證辨識所有秘密格式。

## 授權

本專案採用 [MIT License](LICENSE)。
