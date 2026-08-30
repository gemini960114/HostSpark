# 給 AI Agent 的 Ubuntu VM 安裝指示

本專案只以一般 Ubuntu 使用者搭配 systemd 為正式支援目標。不要直接以 root 執行 Bot，也不要替使用者新增 `NOPASSWD:ALL`。

## 安裝 SOP

1. 確認目前是預計執行服務的一般使用者，且下列命令成功：

   ```bash
   agy --version
   agy -p "reply ok"
   ```

2. 在 repo 根目錄建立設定：

   ```bash
   cp -n .env.example .env
   chmod 600 .env
   ```

3. 將使用者提供的值寫入 `.env`：
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_USER_ID`
   - `AGY_PERMISSION_MODE=safe` 或 `full`
   - 需要時才設定 `AGY_BIN`、`AGY_WORKDIR`、`AGY_RULE_PROMPT`
   - 確認 `AGY_SCHEDULE_TIMEZONE` 符合使用者所在地

4. 向使用者說明：`full` 會讓 AGY 自動核准所有工具操作；rule prompt 不能取代真正的權限隔離。必須由使用者明確選擇，不可自行推定為 `full`。

5. 執行安裝腳本；它會使用 uv（若已安裝）或 Python venv、同步依賴、驗證設定、依目前使用者與 repo 實際路徑產生 systemd service：

   ```bash
   chmod +x install.sh
   ./install.sh
   ```

6. 驗證服務與最近日誌：

   ```bash
   sudo systemctl status agy-telegram.service --no-pager
   sudo journalctl -u agy-telegram.service -n 50 --no-pager
   ```

7. 不要把 `.env`、Token、AGY 登入憑證或日誌中的秘密提交到 Git。
