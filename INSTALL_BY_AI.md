# 給 AI Agent (如 agy) 的一鍵全自動安裝指示手冊

> **當使用者把這個 repo 給你，並請你安裝時，請按照以下標準 SOP 執行：**

## 1. 環境檢查與依賴安裝
1. 檢查主機是否具備 Python 環境：
   - 若主機有 `uv`（執行 `which uv`），優先使用 `uv` 建立虛擬環境：
     ```bash
     uv venv venv
     ./venv/bin/pip install -r requirements.txt
     ```
   - 若無 `uv`，檢查 `python3-venv`，若未安裝則執行 `sudo apt update && sudo apt install -y python3-pip python3-venv`，並執行：
     ```bash
     python3 -m venv venv
     ./venv/bin/pip install -r requirements.txt
     ```

## 2. 設定檔配置
1. 檢查當前目錄是否有 `.env`，若無則自 `.env.example` 複製。
2. 將使用者提供的 `TELEGRAM_BOT_TOKEN` 與 `ALLOWED_USER_ID`（若有）寫入 `.env`。
3. 執行 `chmod 600 .env` 保護機密權限。

## 3. 註冊並啟動 systemd 服務
1. 建立 `/etc/systemd/system/agy-telegram.service`：
   ```ini
   [Unit]
   Description=Antigravity CLI (agy) Telegram Bot Bridge Service
   After=network.target docker.service

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/home/ubuntu
   Environment="PATH=/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
   ExecStart=/home/ubuntu/telegram_agy_bot/venv/bin/python3 /home/ubuntu/telegram_agy_bot/bot.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
2. 執行 `sudo systemctl daemon-reload && sudo systemctl enable --now agy-telegram.service`。
3. 執行 `sudo systemctl status agy-telegram.service` 驗證狀態為 active (running) 並回報使用者！
