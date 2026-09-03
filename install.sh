#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$EUID" -eq 0 ]]; then
    echo "❌ Please run ./install.sh as the regular Ubuntu user that will run the bot; do not run it as root."
    exit 1
fi

INSTALL_USER="$USER"
INSTALL_GROUP="$(id -gn)"
USER_HOME="$HOME"
SERVICE_NAME="agy-telegram.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

echo "======================================================"
echo " Installing the Antigravity Telegram Ubuntu VM control service"
echo "======================================================"

if command -v agy >/dev/null 2>&1; then
    AGY_PATH="$(command -v agy)"
elif [[ -x "$USER_HOME/.local/bin/agy" ]]; then
    AGY_PATH="$USER_HOME/.local/bin/agy"
else
    echo "❌ agy not found. Install it, log in, and confirm 'agy --version' works before retrying."
    exit 1
fi
AGY_BIN_DIR="$(cd "$(dirname "$AGY_PATH")" && pwd)"

if command -v uv >/dev/null 2>&1; then
    if [[ ! -x "venv/bin/python" ]]; then
        echo "Creating uv virtual environment..."
        uv venv venv
    fi
    echo "Syncing Python dependencies..."
    uv pip install --python venv/bin/python -r requirements.lock
    uv pip install --python venv/bin/python -e .
else
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Installing Python3 and venv..."
        sudo apt-get update
        sudo apt-get install -y python3-pip python3-venv
    fi
    if [[ ! -x "venv/bin/python" ]]; then
        echo "Creating Python virtual environment..."
        if ! python3 -m venv venv; then
            sudo apt-get update
            sudo apt-get install -y python3-venv
            python3 -m venv venv
        fi
    fi
    echo "Syncing Python dependencies..."
    venv/bin/python -m pip install --upgrade pip
    venv/bin/python -m pip install -r requirements.lock
    venv/bin/python -m pip install -e .
fi

if [[ ! -f ".env" ]]; then
    cp .env.example .env
    chmod 600 .env
    echo "❌ Created $SCRIPT_DIR/.env. Fill in the Token, User ID, and AGY_PERMISSION_MODE, then re-run the installer."
    exit 2
fi
chmod 600 .env

echo "Validating bot configuration..."
"$SCRIPT_DIR/venv/bin/python" -m hostspark --check-config

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
ExecStart="$SCRIPT_DIR/venv/bin/python" -m hostspark
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

echo "Installing systemd service..."
sudo install -m 0644 "$SERVICE_TMP" "$SERVICE_PATH"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"

if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "❌ Service failed to start:"
    sudo systemctl status "$SERVICE_NAME" --no-pager
    exit 1
fi

echo "======================================================"
echo "✅ Installation complete, service is running."
echo "Check status: sudo systemctl status $SERVICE_NAME"
echo "Check logs:   sudo journalctl -u $SERVICE_NAME -f"
echo "======================================================"
