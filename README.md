# 🚀 Antigravity CLI (agy) Telegram VM Remote Control Agent
### 透過手機 Telegram，24/7 隨時隨地用語音與自然語言掌控整台 Linux 雲端虛擬機 (Cloud VM / VPS)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://www.python.org/)
[![Powered by: Google Antigravity](https://img.shields.io/badge/Powered%20by-Google%20Antigravity%20(agy)-orange.svg)](https://antigravity.google)

---

## ⚡ 極速 3 步驟安裝（交給 AI 全自動完成）

日後您若開啟了任何一台全新 Linux 伺服器，只需執行：

```bash
git clone https://github.com/gemini960114/agy-telegram-bot.git
cd agy-telegram-bot
agy
```

然後在 `agy` 對話視窗對 AI 說一句話：
> **「請閱讀 `INSTALL_BY_AI.md`，使用 `uv` 幫我安裝並啟動 Telegram 機器人服務，我的 Token 是 `xxxx`」**

AI 就會在 30 秒內全自動為您建立隔離環境、安裝依賴、配置權限並啟動開機自啟系統服務！

---

## 📖 專案簡介 (Overview)

**`agy-telegram-bot`** 是一個專為 **雲端虛擬機（Linux VM / VPS / 實體伺服器）** 量身打造的 AI 遙控運維橋接系統。

傳統上，維護雲端 VM 必須打開筆記型電腦、連線 VPN、啟動 SSH 終端機並手動敲打複雜的 Linux 指令。  
透過本專案，您可以**直接在手機 Telegram 聊天室中，使用文字或語音向主機發布任務**。伺服器本機的 **Google Antigravity AI Agent (`agy`)** 會直接在 VM 內部執行多步驟自主推理、調用系統工具、讀寫檔案、管理 Docker 並即時回傳結構化報表！

---

## 🎯 核心 VM 掌控場景 (VM Control Scenarios)

您可以在手機 Telegram 上直接對 VM 下達以下自然語言任務：

### 1. 🖥️ 虛擬機系統健康與資源監控
- 「*幫我查看這台 VM 的 CPU、記憶體使用量與硬碟剩餘空間*」
- 「*目前 VM 的系統負載（Load Average）與開機時間是多少？*」
- 「*檢查伺服器近期是否有 OOM (Out of Memory) 或異常重啟紀錄？*」

### 2. 🐳 Docker 容器與網站服務維運
- 「*檢查目前 VM 上所有 Docker 容器的運作與健康狀態*」
- 「*網站出現 500 錯誤，幫我查看 Apache 和 MySQL 容器的最新錯誤日誌並修復*」
- 「*幫我重啟 Web 容器並驗證首頁 HTTP 回應碼*」

### 3. 🔒 網路、SSL 憑證與資安檢查
- 「*檢查目前 VM 上的 Let's Encrypt SSL 憑證到期日*」
- 「*這台 VM 目前開放並監聽了哪些網路 Port（netstat/ss）？*」
- 「*幫我執行 Certbot 續期並熱重載 Web 伺服器*」

### 4. 🛠️ 程式碼檢視、設定檔修改與排程維護
- 「*幫我把 `statistics.php` 裡面的資料庫連線主機改為 `db`*」
- 「*查看 root 的 crontab 排程任務清單*」
- 「*備份資料庫並壓縮存放到指定目錄*」

---

## 🛡️ 針對 VM 控制的安全架構 (Security Architecture)

本系統預設遵循 **「最小權限原則」**，兼顧極致安全與日常便利：

1. **🚫 零外部監聽埠 (Zero Inbound Ports)**：
   - 採用 Telegram 長輪詢（Long Polling 出站加密連線），**VM 不需要對外開放任何額外 Port 或 Webhook**，駭客無法透過網路掃描攻擊機器人。
2. **🔐 專屬 User ID 白名單鎖定 (Strict Whitelist)**：
   - 每一則傳入訊息皆嚴格比對使用者的 Telegram 數字 ID。**非白名單內的陌生人一律直接拒絕且不觸發任何指令**。
3. **👤 預設非 Root 權限隔離**：
   - 機器人預設以一般使用者（`ubuntu`）身分運行。
   - 因為使用者已具備 `docker` 群組權限，日常 90% 的維護（Docker 重啟、日誌分析、Web 程式修改）皆**原生免 Sudo 密碼即可順暢操作**。

---

## ⚠️ 進階特殊用法：開啟 AI 最大 Root 全權模式（不建議）

> [!WARNING]
> **資安警告 (Security Notice)**：  
> 預設情況下，AI 無法透過 Telegram 執行需要 `sudo` 密碼的高危指令（如 `apt install`、修改 `/etc/` 底層設定、新增刪除系統帳號）。  
> 若您希望賦予 Telegram AI **完全不受限的最高 Root 超級管理員權限**（讓 AI 在手機端可全自動執行 `sudo apt install` 或修改系統核心配置），可手動開啟 Sudo 免密碼。**生產環境請審慎評估資安風險！**

### 1. 開啟最大 Root 全權模式：
```bash
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USER && sudo chmod 0440 /etc/sudoers.d/$USER
```

### 2. 恢復標準安全模式（取消免密碼，推薦）：
```bash
sudo rm -f /etc/sudoers.d/$USER
```

---

## 📋 事前準備 (Prerequisites)

在一台全新的 Linux VM 上，只需準備好以下 3 樣基礎工具：

1. **`agy` (Antigravity CLI)**：安裝於 VM 並完成登入（可在終端機執行 `agy --version`）。
2. **`uv`（極速 Python 套件管理器，推薦）或 `python3`**：
   - 一秒安裝 `uv`：`curl -LsSf https://astral.sh/uv/install.sh | sh`
   - （或 Ubuntu 內建：`sudo apt update && sudo apt install -y python3-pip python3-venv`）
3. **Telegram Bot Token**：
   - 在 Telegram 搜尋官方機器人 `@BotFather`，發送 `/newbot` 取得 `Bot Token`。

---

## 🚀 手動部署方式 (Manual Installation)

如果您不透過 AI，亦可手動執行腳本安裝：

```bash
# 1. 進入專案目錄並執行安裝腳本
cd agy-telegram-bot
chmod +x install.sh
./install.sh

# 2. 填入您的 Telegram Bot Token
nano .env

# 3. 啟動/重啟 systemd 服務
sudo systemctl restart agy-telegram.service
```

---

## ⚙️ VM 系統服務管理指令

本專案已註冊為 Linux `systemd` 守護服務（開機自啟、崩潰自動重連）：

```bash
# 查看機器人運行狀態
sudo systemctl status agy-telegram.service

# 查看即時對話與 VM 執行日誌
sudo journalctl -u agy-telegram.service -f

# 重啟服務
sudo systemctl restart agy-telegram.service

# 停止服務
sudo systemctl stop agy-telegram.service
```

---

## 📱 手機常用快捷指令

- **`/status`**：一鍵產生 VM 即時健康報表（Docker 狀態、磁碟容量、記憶體使用）。
- **`/clear`**：重置對話工作階段，開啟全新維運任務。
- **直接輸入自然語言 / 語音**：直接指派任何 VM 維運或程式任務。

---

## 📄 開源授權 (License)

本專案採用 [MIT License](LICENSE) 開源授權，歡迎自由修改與推廣使用。
