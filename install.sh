#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$EUID" -eq 0 ]]; then
    echo "❌ 請以要執行 Bot 的一般 Ubuntu 使用者執行 ./install.sh，不要直接使用 root。"
    exit 1
fi

INSTALL_USER="$USER"
INSTALL_GROUP="$(id -gn)"
USER_HOME="$HOME"
SERVICE_NAME="agy-telegram.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

echo "======================================================"
echo " 安裝 Antigravity Telegram Ubuntu VM 控制服務"
echo "======================================================"

if command -v agy >/dev/null 2>&1; then
    AGY_PATH="$(command -v agy)"
elif [[ -x "$USER_HOME/.local/bin/agy" ]]; then
    AGY_PATH="$USER_HOME/.local/bin/agy"
else
    echo "❌ 找不到 agy。請先安裝、登入並確認 agy --version 可執行。"
    exit 1
fi
AGY_BIN_DIR="$(cd "$(dirname "$AGY_PATH")" && pwd)"

if command -v uv >/dev/null 2>&1; then
    if [[ ! -x "venv/bin/python" ]]; then
        echo "建立 uv 虛擬環境..."
        uv venv venv
    fi
    echo "同步 Python 依賴..."
    uv pip install --python venv/bin/python -r requirements.lock
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo "安裝 Python3 與 venv..."
        sudo apt-get update
        sudo apt-get install -y python3-pip python3-venv
    fi
    if [[ ! -x "venv/bin/python" ]]; then
        echo "建立 Python 虛擬環境..."
        if ! python3 -m venv venv; then
            sudo apt-get update
            sudo apt-get install -y python3-venv
            python3 -m venv venv
        fi
    fi
    echo "同步 Python 依賴..."
    venv/bin/python -m pip install --upgrade pip
    venv/bin/python -m pip install -r requirements.lock
fi

if [[ ! -f ".env" ]]; then
    cp .env.example .env
    chmod 600 .env
    echo "❌ 已建立 $SCRIPT_DIR/.env。請填入 Token、User ID 與 AGY_PERMISSION_MODE 後重新執行安裝。"
    exit 2
fi
chmod 600 .env

echo "驗證 Bot 設定..."
venv/bin/python bot.py --check-config

SERVICE_TMP="$(mktemp)"
trap 'rm -f "$SERVICE_TMP"' EXIT
cat >"$SERVICE_TMP" <<SYSTEMD_EOF
[Unit]
Description=Antigravity CLI Telegram Bot Bridge
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$INSTALL_USER
Group=$INSTALL_GROUP
WorkingDirectory=$SCRIPT_DIR
Environment="PATH=$AGY_BIN_DIR:$USER_HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart="$SCRIPT_DIR/venv/bin/python" "$SCRIPT_DIR/bot.py"
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF

echo "安裝 systemd 服務..."
sudo install -m 0644 "$SERVICE_TMP" "$SERVICE_PATH"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "❌ 服務啟動失敗："
    sudo systemctl status "$SERVICE_NAME" --no-pager
    exit 1
fi

echo "======================================================"
echo "✅ 安裝完成，服務已啟動。"
echo "查看狀態：sudo systemctl status $SERVICE_NAME"
echo "查看日誌：sudo journalctl -u $SERVICE_NAME -f"
echo "======================================================"
