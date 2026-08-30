# Antigravity CLI (agy) Telegram Bot 遠端遙控系統

本專案是一個輕量級橋接系統，將 Telegram Bot 與伺服器本機的 **Google Antigravity CLI (`agy`)** 深度整合。讓管理員可以隨時透過手機 Telegram，24/7 進行自然語言主機維運、程式碼撰寫、Docker 容器監控與自動化故障排查。

---

## 1. 事前準備條件 (Prerequisites)

在全新 Linux 伺服器上部署本系統前，只需確認以下 3 樣基礎工具：

1. **安裝並登入 `agy` (Antigravity CLI)**：
   - 確保終端機可執行 `agy --version`。
2. **安裝 `uv`（推薦極速 Python 套件管理工具）或 `python3`**：
   - 一秒安裝 `uv`：`curl -LsSf https://astral.sh/uv/install.sh | sh`
   - （或使用 Ubuntu 內建：`sudo apt update && sudo apt install -y python3-pip python3-venv`）
3. **準備 Telegram 機器人 Token**：
   - 在 Telegram 搜尋 `@BotFather`，發送 `/newbot` 取得 `Bot Token`。

---

## 2. 專案目錄結構

```text
/home/ubuntu/telegram_agy_bot/
├── bot.py                  # Telegram Bot 核心橋接程式 (處理訊息、非同步排程鎖、調用 agy)
├── .env                    # 機密設定檔 (Bot Token 與 白名單 User ID，權限 600)
├── .env.example            # 設定檔範本
├── requirements.txt        # Python 依賴清單 (python-telegram-bot, python-dotenv)
├── install.sh              # 一鍵自動化安裝腳本
├── INSTALL_BY_AI.md        # 給 AI Agent (如 agy) 的一鍵全自動安裝指示手冊
├── README.md               # 本說明文件
└── venv/                   # Python 3 獨立虛擬環境
```

- **Linux 系統常駐服務檔**：`/etc/systemd/system/agy-telegram.service`

---

## 3. 兩種極速安裝部署方式

### 方式 A：交給 AI 自動全自動安裝（最推薦 ⭐⭐⭐⭐⭐）
將整包 `telegram_agy_bot` 目錄放到新主機後，啟動 `agy` 直接吩咐 AI：
> **「請閱讀當前目錄下的 `INSTALL_BY_AI.md`，使用 `uv` 幫我安裝並啟動 Telegram Bot 服務，我的 Bot Token 是 `xxxx`」**

AI 就會自動在背後建立虛擬環境、安裝依賴、配置 `.env` 安全權限並註冊開機自啟服務！

---

### 方式 B：手動一鍵腳本安裝
```bash
# 1. 進入目錄並執行安裝腳本
cd /home/ubuntu/telegram_agy_bot
chmod +x install.sh
./install.sh

# 2. 填入您的 Telegram Bot Token
nano .env

# 3. 啟動/重啟服務
sudo systemctl restart agy-telegram.service
```

---

## 4. 安全防護機制（白名單鎖定）

- **預設狀態：只有管理員本人可使用（100% 專屬鎖定）**。
- 首次啟動時，**第一個在 Telegram 傳送 `/start` 給機器的帳號會自動綁定為唯一管理員**，並記錄在 `.env` 中。
- 若非白名單內的陌生人發送訊息，系統會直接拒絕（`⛔ 您沒有權限使用此機器人`），完全不會觸發任何伺服器指令。

### 如何新增其他授權使用者？
若日後想授權同事或第二個帳號一起使用：
1. 讓對方在 Telegram 搜尋 `@userinfobot` 取得其專屬 `User ID`（純數字）。
2. 編輯 `.env`：
   ```bash
   nano /home/ubuntu/telegram_agy_bot/.env
   ```
3. 在 `ALLOWED_USER_ID` 加入對方的 ID（以逗號分隔，例如：`ALLOWED_USER_ID='8557428151,123456789'`）。
4. 重啟服務即可生效：
   ```bash
   sudo systemctl restart agy-telegram.service
   ```

---

## 5. 系統服務管理指令

本專案已註冊為 Linux `systemd` 系統服務，開機自動啟動，若異常會自動重啟：

```bash
# 查看機器人即時運行狀態
sudo systemctl status agy-telegram.service

# 查看即時對話日誌 (可看到每次執行的指令與輸出)
sudo journalctl -u agy-telegram.service -f

# 重啟機器人服務
sudo systemctl restart agy-telegram.service

# 停止機器人服務
sudo systemctl stop agy-telegram.service
```

---

## 6. 手機 Telegram 常用操作與指令

- **`/status`**：即時查看主機 Docker 容器狀態、硬碟剩餘空間與記憶體用量。
- **`/clear`**：重置對話上下文，開啟全新的工作階段。
- **自然語言對話 / 手機語音輸入**：
  - `請檢查 HMP 網站目前狀態與資料庫連線`
  - `查看目前 /var/log/hmp-monitor.log 的最新 10 行紀錄`
  - `伺服器有哪些 Docker 容器在跑？`
