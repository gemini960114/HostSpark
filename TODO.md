# AGY Telegram Bot 排程功能：完成項目與 Linux 驗收清單

本文件記錄本次排程功能已完成的內容，以及切換到 Ubuntu VM 後仍需執行的真實環境驗收。完成 Linux 驗收前，不應宣稱排程功能已在正式環境完整驗證。

## 目前狀態

- Windows 開發與模擬測試：已完成。
- Ubuntu 依賴安裝、測試與設定驗證：已完成（32 項單元與整合測試全數通過，`pip check` 無異常，`bot.py --check-config` 成功）。
- AGY CLI 整合驗證：已完成（`agy` 1.1.22 headless 測試通過，支援 `--add-dir` 與 `--continue`）。
- systemd 服務部署與排程器啟動：已完成（`agy-telegram.service` 正常運行，SQLite 資料庫已建立）。
- Git commit／push：排程核心程式碼已 commit 並 push 至 main 分支。
- Telegram 實際操作端到端驗收：待管理員透過 Telegram App 依步驟驗收。

## 已完成的程式功能

- 使用 SQLite 持久保存排程，Bot 重啟後資料仍存在。
- 支援標準五欄 cron：`分 時 日 月 週`。
- 新增排程時，先由 AGY 將原始要求整理成可重複、獨立執行的完整 prompt。
- AGY 整理後必須由管理員在 Telegram 按下「確認建立」，才會正式啟用。
- Prompt 整理階段不會加入 `--dangerously-skip-permissions`，即使正式執行模式是 Full。
- 每個排程使用獨立 AGY workspace，並以 `--add-dir` 開放主要 `AGY_WORKDIR`，避免改變一般問答使用 `--continue` 的最近對話。
- 排程與人工問答共用一把 AGY 執行鎖，同一時間只執行一項 AGY 任務。
- 停機期間錯過多次執行時，恢復後最多執行一次，不集中補跑所有舊任務。
- 連續失敗三次後自動暫停排程並通知管理員。
- 支援 `[NO_REPORT]`：AGY 精確輸出該值時，本次不傳 Telegram 訊息。
- 支援以下執行時變數：

  - `{{now}}`
  - `{{date}}`
  - `{{time}}`
  - `{{timezone}}`
  - `{{scheduled_at}}`
  - `{{run_number}}`

- 未知的 `{{自訂變數}}` 保持原樣，不自行猜測或取代。
- 可設定最短排程間隔與最大排程數量。
- SQLite connection 在每次交易完成後確實關閉，已修正 Windows 檔案鎖定問題。
- `.gitignore` 已排除常見的 `schedules.db`、WAL 與 SHM 檔案。
- README、`.env.example`、安裝說明及安全政策已更新。

## 已完成的 Windows 驗證

- 32 項單元與整合模擬測試全部通過。
- Python 語法編譯檢查通過。
- `bot`、`schedule_store`、Telegram、dotenv、croniter 與 tzdata 模組載入通過。
- 使用假 Bot Token 與 Python 執行檔模擬 `bot.py --check-config` 通過。
- SQLite 新增、查詢、刪除、暫停、恢復與重新開啟資料庫測試通過。
- cron 格式、無效日期、最短執行間隔與 Asia/Taipei 時區測試通過。
- 到期任務只領取一次、停機後不大量補跑的模擬通過。
- 主動傳送 Telegram 訊息的 mock 測試通過。
- `[NO_REPORT]` 不傳訊息的模擬通過。
- Prompt 整理階段不使用 Full 權限的參數測試通過。
- 連續三次失敗後自動暫停的測試通過。
- Git whitespace 檢查與 tracked files Token 格式掃描通過。

## Telegram 排程指令

```text
/schedule_help
/schedule_add 分 時 日 月 週 任務內容
/schedule_list
/schedule_show ID
/schedule_pause ID
/schedule_resume ID
/schedule_delete ID
```

範例：

```text
/schedule_add 0 * * * * 查詢台北目前天氣與未來三小時降雨機率，簡短回報
```

```text
/schedule_add 0 9 * * * 檢查 VM、服務與 Docker 狀態，摘要需要注意的異常
```

## Linux 驗收需要的材料與帳號

開始前準備：

- 一台 Ubuntu VM，建議是可建立快照、可重建的私人測試 VM。
- 一個一般 Ubuntu 使用者，不要直接使用 root 執行 Bot。
- 可使用 `sudo` 安裝套件及管理 systemd service。
- 可正常連線至 Telegram Bot API 與 AGY 所需服務的網路。
- 已安裝並登入的 AGY CLI。
- 有效且未洩漏的 Telegram Bot Token。
- 唯一管理員的 Telegram 數字 User ID。
- 正確的 IANA 時區，例如 `Asia/Taipei`。
- 足夠的 AGY／模型額度，用於建立 prompt 與執行排程。
- 建議先建立 VM 快照。

安全要求：

- 不要把真實 Token 貼到聊天、issue、終端指令或 Git commit。
- 如果 Token 曾貼到不受信任的位置，先透過 `@BotFather` 重新產生 Token。
- `.env` 權限必須是 `600`。
- 第一輪驗收使用 `AGY_PERMISSION_MODE=safe`。
- Full 模式只在專用、可快照、可重建且沒有不必要正式憑證的 VM 測試。
- 不要將 Token、密碼、私鑰或 API Key 寫入排程 prompt；原始要求與完整 prompt 會存入 SQLite。

## Linux 需要的軟體

必要軟體：

- Git
- Python 3.10 或更新版本
- Python venv
- AGY CLI
- systemd
- ca-certificates

選用軟體：

- uv，用來加速建立虛擬環境與安裝依賴。
- Docker，只有要測試 `/status` 的 Docker 區段或 Docker 維運排程時才需要。
- SQLite CLI，只有人工檢查資料庫時才需要；程式本身使用 Python 內建 SQLite，不需要額外安裝。

Ubuntu 安裝基本工具：

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip ca-certificates
```

選用：

```bash
sudo apt-get install -y sqlite3
```

## 切換到 Linux 後的執行順序

### 1. 確認目前 Git 狀態

如果 Windows 修改尚未 commit／push，先在 Windows 完成 commit／push，再到 VM 拉取。不要在 VM 上直接覆蓋有本機修改的 tracked files。

在 VM 執行：

```bash
cd /path/to/agy-telegram-bot
git status --short
git pull --ff-only origin main
git log -1 --oneline
```

成功標準：

- `git pull` 沒有 merge conflict。
- 最新 commit 是排程功能版本。
- `git status --short` 沒有非預期修改。

### 2. 確認 AGY CLI

```bash
command -v agy
agy --version
agy -p "只回覆 ok" --print-timeout 60s
```

成功標準：

- 找得到 `agy` 執行檔。
- 已完成登入。
- headless prompt 可以正常取得回覆。

另外確認目前 AGY 版本支援排程隔離使用的參數：

```bash
agy --help | grep -E -- '--add-dir|--continue|--print-timeout'
```

成功標準：三個參數都存在。若 `--add-dir` 不存在，先停止驗收並升級 AGY，不要直接改成共用工作目錄。

### 3. 備份並更新 `.env`

先將備份放在 repo 外：

```bash
cp -p .env "$HOME/agy-telegram.env.pre-scheduler"
chmod 600 "$HOME/agy-telegram.env.pre-scheduler"
nano .env
```

至少確認以下設定：

```dotenv
TELEGRAM_BOT_TOKEN=請填入有效且未洩漏的Token
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

```bash
chmod 600 .env
```

`AGY_SCHEDULE_DB_PATH` 留空時，預設使用：

```text
~/.local/state/agy-telegram-bot/schedules.db
```

### 4. 安裝依賴與更新 systemd

建議直接使用專案腳本：

```bash
chmod +x install.sh
./install.sh
```

腳本會建立或更新 `venv`、依 `requirements.lock` 安裝套件、驗證設定、更新 systemd service 並啟動 Bot。

若需要手動安裝：

```bash
python3 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.lock
```

確認依賴：

```bash
venv/bin/python -c "import telegram, dotenv, croniter; from zoneinfo import ZoneInfo; print(ZoneInfo('Asia/Taipei')); print('runtime imports ok')"
venv/bin/python -m pip check
```

### 5. 執行 Linux 測試

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m py_compile bot.py agy_bot_core.py schedule_store.py
venv/bin/python bot.py --check-config
```

成功標準：

- 所有測試通過。
- 沒有 syntax error。
- 顯示「設定驗證成功」。

### 6. 檢查 systemd

```bash
sudo systemctl status agy-telegram.service --no-pager
sudo journalctl -u agy-telegram.service -n 100 --no-pager
```

成功標準：

- Service 為 `active (running)`。
- 日誌出現 Bot 與排程器啟動訊息。
- 沒有設定、SQLite、AGY 登入或 Telegram 網路錯誤。

### 7. 驗證既有功能沒有回歸

從唯一允許的 Telegram 帳號依序執行：

```text
/start
/status
請只回覆目前日期，不要修改任何檔案
/clear
```

成功標準：

- `/start` 顯示排程相關指令。
- `/status` 可回報 VM 狀態。
- 一般 AGY 問答正常。
- `/clear` 可建立新的人工對話。

另外使用未授權 Telegram 帳號測試，必須收到拒絕訊息，且不得執行 AGY。

### 8. 驗證新增、確認、列出與查看

先取得目前 VM 分鐘，例如目前為 14:22，可建立下一個容易觀察的整點或指定分鐘排程。cron 的最短「重複間隔」是 15 分鐘，但可以使用每小時固定分鐘：

```text
/schedule_add 25 * * * * 只回覆「排程測試成功」，並附上 {{now}}、{{scheduled_at}} 與 {{run_number}}
```

確認事項：

- Bot 顯示 AGY 整理後的 prompt 預覽。
- 預覽中沒有執行原始任務，只是在整理 prompt。
- 按「取消」時不建立排程。
- 再建立一次並按「確認建立」。
- 顯示排程 ID 與正確的下一次執行時間。

接著執行：

```text
/schedule_list
/schedule_show ID
```

成功標準：

- 能看到 cron、時區、原始要求、完整 prompt 與下次時間。
- `{{now}}` 等變數仍保存在模板中，尚未提前替換。

### 9. 驗證主動 Telegram 回報

保持 Bot 與 VM 運行，等待排程時間到達，不要先傳訊息給 Bot。

成功標準：

- Bot 主動傳送「排程 ID 執行結果」。
- `{{now}}`、`{{scheduled_at}}`、`{{run_number}}` 已替換成實際值。
- `/schedule_show ID` 的執行次數、上次時間與狀態已更新。
- systemd 日誌沒有例外。

### 10. 驗證人工對話沒有被排程污染

在排程執行前，先傳送：

```text
請記住測試代碼 ALPHA，只回覆收到
```

等待排程執行後，再傳送：

```text
我剛才請你記住的測試代碼是什麼？
```

成功標準：

- 一般對話仍回答 `ALPHA`。
- 不會把排程 prompt 當成最近一次人工對話。

這項測試用來驗證 AGY workspace 隔離與 `--continue` 行為。

### 11. 驗證暫停、恢復與刪除

```text
/schedule_pause ID
/schedule_list
/schedule_resume ID
/schedule_list
/schedule_delete ID
/schedule_list
```

成功標準：

- 暫停後沒有下次執行時間，也不會執行。
- 恢復後重新計算下一次執行時間。
- 刪除後不再出現在清單中。

### 12. 驗證 SQLite 持久化與重啟

先建立一個尚未到期的測試排程，記住其 ID，然後執行：

```bash
sudo systemctl restart agy-telegram.service
sudo systemctl status agy-telegram.service --no-pager
```

回到 Telegram：

```text
/schedule_list
/schedule_show ID
```

成功標準：排程仍存在，而且下一次時間正確。

可選擇檢查檔案權限：

```bash
ls -l "$HOME/.local/state/agy-telegram-bot/schedules.db"
```

預期資料庫檔案只有 Bot 服務使用者可以讀寫。

### 13. 驗證 `[NO_REPORT]`

建立測試排程：

```text
/schedule_add <測試cron> 無論任何情況都只輸出 [NO_REPORT]
```

成功標準：

- 到期時 Telegram 不會收到執行結果。
- `/schedule_show ID` 顯示本次狀態為成功，執行次數增加。

測試完成後刪除該排程。

### 14. 驗證 Safe 模式失敗處理

維持：

```dotenv
AGY_PERMISSION_MODE=safe
```

建立一個確實需要 AGY 工具授權、但不會造成破壞的測試排程。不要使用刪除、重啟或修改正式資料的任務。

成功標準：

- 權限被拒絕時收到友善的 Safe 模式訊息。
- 該次記為失敗，不會誤記為成功。
- 不要為了通過測試直接切換 Full；先確認錯誤符合預期。

### 15. 驗證連續失敗自動暫停

這項測試會消耗至少三次排程執行，建議最後執行。建立一個會穩定失敗但不造成副作用的任務。

成功標準：

- 第三次連續失敗後排程自動暫停。
- Telegram 收到自動暫停通知。
- `/schedule_show ID` 顯示連續失敗三次且沒有下次執行時間。
- 修正原因後可使用 `/schedule_resume ID` 恢復。

### 16. 選用：Full 模式驗證

只有在測試 VM 已建立快照並接受風險時才執行：

```dotenv
AGY_PERMISSION_MODE=full
```

```bash
sudo systemctl restart agy-telegram.service
```

只建立可回復、低風險的測試任務，例如在專用暫存目錄建立一個文字檔，再確認內容。

成功標準：

- 排程實際執行時可使用 Full 權限。
- 新增排程的「prompt 整理階段」仍不會執行工具操作。
- 測試後依需求改回 `safe` 並重新啟動。

## 日誌與故障排查

持續查看日誌：

```bash
sudo journalctl -u agy-telegram.service -f
```

常見問題：

- `ModuleNotFoundError`：重新執行 `./install.sh` 或以 `venv/bin/python -m pip install -r requirements.lock` 安裝依賴。
- 找不到 `agy`：確認 `command -v agy`，必要時設定 `AGY_BIN`。
- AGY 未登入：以相同 Ubuntu 使用者執行互動式 `agy` 完成登入。
- 時區錯誤：使用 IANA 名稱，例如 `Asia/Taipei`，不要只填 `UTC+8`。
- Telegram 沒有主動訊息：確認 `ALLOWED_USER_ID`、Bot Token、網路、service 日誌及排程是否仍啟用。
- Safe 權限拒絕：屬於預期安全行為；先評估任務是否真的需要 Full。
- 排程沒有執行：檢查 cron、時區、下次執行時間、service uptime 與 `/schedule_show ID`。
- 排程自動暫停：查看上次錯誤，修正後使用 `/schedule_resume ID`。

## 回復方式

若新版無法啟動：

1. 保留以下診斷資訊，但先遮罩所有 Token 與秘密：

   ```bash
   git log -1 --oneline
   sudo systemctl status agy-telegram.service --no-pager
   sudo journalctl -u agy-telegram.service -n 100 --no-pager
   ```

2. 將 `.env` 還原為升級前備份：

   ```bash
   cp -p "$HOME/agy-telegram.env.pre-scheduler" .env
   chmod 600 .env
   ```

3. 不要直接刪除排程資料庫。先停止服務並備份：

   ```bash
   sudo systemctl stop agy-telegram.service
   cp -p "$HOME/.local/state/agy-telegram-bot/schedules.db" \
     "$HOME/schedules.db.backup"
   chmod 600 "$HOME/schedules.db.backup"
   ```

4. 若需要回退程式版本，先記錄目前 commit，再切換到明確已知可用的 commit；不要使用 `git reset --hard` 清除未確認的本機修改。

## 完成定義

只有下列項目全部符合，才能將 Linux 驗收標記完成：

- [x] Ubuntu 依賴安裝成功，`pip check` 通過。
- [x] AGY headless 執行成功且支援 `--add-dir`。
- [x] 完整測試與 `--check-config` 通過。
- [x] systemd service 穩定運行（排程器已啟動）。
- [ ] 舊有 `/status`、一般問答、`/clear` 沒有回歸。（待 Telegram 操作）
- [ ] 未授權 Telegram 帳號無法操作。（待 Telegram 操作）
- [ ] 排程可新增、預覽、取消與確認。（待 Telegram 操作）
- [ ] 排程可列出、查看、暫停、恢復與刪除。（待 Telegram 操作）
- [ ] Bot 可在沒有人工提問時主動回報。（待 Telegram 操作）
- [ ] 時間變數替換正確。（待 Telegram 操作）
- [ ] `[NO_REPORT]` 可抑制訊息。（待 Telegram 操作）
- [ ] Bot 重啟後排程仍存在。（已完成單元測試，待 Telegram 操作）
- [ ] 排程不污染一般 `--continue` 對話。（待 Telegram 操作）
- [ ] Safe 權限拒絕能正確記錄。（待 Telegram 操作）
- [ ] 連續三次失敗會自動暫停。（已完成單元測試，待 Telegram 操作）
- [ ] 日誌未出現未處理例外或秘密內容。（已在運行日誌中確認）
- [ ] 測試排程與測試資料已清理。
- [ ] 最終變更已 commit 並 push。
