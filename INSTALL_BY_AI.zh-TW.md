*[English](INSTALL_BY_AI.md) | 繁體中文*

# 給 AI Agent 的 Ubuntu VM 安裝指示

本專案只以一般 Ubuntu 使用者搭配 systemd 為正式支援目標。不要直接以 root 執行 Bot，也不要主動替使用者新增 `NOPASSWD:ALL`（若使用者因維運需求自行開啟，請提醒其於任務完成後執行 `sudo rm -f /etc/sudoers.d/$USER` 還原）。

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
   - `ALLOWED_USER_IDS`（或 `ALLOWED_USER_ID`）
   - `ALLOWED_CHAT_IDS`（選填）、`TELEGRAM_PRIVATE_ONLY`（選填，預設 1）
   - `AGY_PERMISSION_MODE=safe` 或 `full`
   - 需要時才設定 `AGY_BIN`、`AGY_WORKDIR`、`AGY_WORKSPACE_ROOT`、`AGY_RULE_PROMPT`
   - 確認 `AGY_SCHEDULE_TIMEZONE` 符合使用者所在地

4. **`AGY_ALLOWED_MODELS` 必須用真實模型清單，不要照抄範例值**：先執行 `agy models` 取得這台 VM / 這個帳號實際可用的模型代號，再把結果填入 `AGY_ALLOWED_MODELS`（逗號分隔）。若略過這步、留空或照抄文件裡的範例，`/models` 選單可能會顯示不存在的模型名稱，使用者選了會直接失敗。

5. 向使用者明確確認以下兩個決策，兩者都不可自行推定：
   - `AGY_PERMISSION_MODE`：`full` 會讓 AGY 自動核准所有工具操作；rule prompt 不能取代真正的權限隔離。
   - `ALLOW_BOT_UPDATE`（預設 `0`）：開啟後任何授權使用者可透過 Telegram 的 `/update` 觸發 `git pull` 並自動重啟服務、透過 `/restart` 觸發重啟。這是額外的攻擊面（Telegram 帳號被盜等同能觸發程式碼更新與重啟），只有使用者明確要這個功能時才開啟。

6. 執行安裝腳本；它會使用 uv（若已安裝）或 Python venv、同步依賴（含 `pexpect`、`httpx` 等）、驗證設定、依目前使用者與 repo 實際路徑產生 systemd service：

   ```bash
   chmod +x install.sh
   ./install.sh
   ```

7. 驗證服務與最近日誌：

   ```bash
   sudo systemctl status agy-telegram.service --no-pager
   sudo journalctl -u agy-telegram.service -n 50 --no-pager
   ```

8. 若 `ALLOW_BOT_UPDATE=1`，跟使用者說明：`/restart`／`/update` 內部會嘗試 `systemctl restart`，但一般服務使用者通常沒有 polkit 授權執行這個指令；失敗時會改用非零結束碼讓程序結束，交由 systemd 的 `Restart=on-failure` 自動拉起（安裝腳本產生的 unit 已內建這個設定，不需要額外授權）。這代表這兩個指令仍會正常運作，只是重啟過程會在 `journalctl` 留下看起來像「crash」的紀錄，屬正常現象。

9. 不要把 `.env`、Token、AGY 登入憑證或日誌中的秘密提交到 Git。
