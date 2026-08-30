#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================"
echo " 🚀 開始自動安裝 Antigravity Telegram 遙控橋接服務"
echo "======================================================"

# 1. 檢查 Python3
if ! command -v python3 &>/dev/null; then
    echo "正在安裝 Python3 與 venv..."
    sudo apt update && sudo apt install -y python3-pip python3-venv
fi

# 2. 建立虛擬環境
if [ ! -d "venv" ]; then
    echo "建立 Python 虛擬環境..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

# 3. 檢查 .env
if [ ! -f ".env" ]; then
    echo "建立 .env 設定檔..."
    cp .env.example .env
    chmod 600 .env
    echo "⚠️ 請記得在 .env 中填入你的 TELEGRAM_BOT_TOKEN"
fi

# 4. 註冊 systemd 服務
echo "設定 systemd 系統常駐服務..."
sudo bash -c "cat << 'SYSTEMD_EOF' > /etc/systemd/system/agy-telegram.service
[Unit]
Description=Antigravity CLI (agy) Telegram Bot Bridge Service
After=network.target docker.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME
Environment=\"PATH=$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"
ExecStart=$SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF
systemctl daemon-reload
systemctl enable --now agy-telegram.service
"

echo "======================================================"
echo "🎉 安裝完成！服務已啟動。"
echo "查看狀態指令: sudo systemctl status agy-telegram.service"
echo "查看即時日誌: sudo journalctl -u agy-telegram.service -f"
echo "======================================================"
