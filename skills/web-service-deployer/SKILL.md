---
name: web-service-deployer
description: >-
  當使用者需要啟動、運行、部署或預覽任何網頁應用（React, Vue, Vite, Next.js, Flask, FastAPI, Express, HTML 靜態網站等），
  或提出「對外開放連線」、「手機預覽測試」、「分享給別人看」、「遠端存取」等需求時啟用此技能。
  優先引導使用 Docker 容器化（含 Cloudflare Tunnel 容器化）以防對話輪次結束或重啟時行程被殺死導致 Error 1033。
---

# Web 服務啟動與對外連線部署技能手冊

本技能規範了在主機上安全啟動網頁服務、預防終端機阻塞死鎖（Terminal Hang），以及使用 Cloudflare Tunnel 提供持久穩定對外連線的標準工作流程。

---

## 一、 核心原則：嚴禁終端阻塞 & 確保 Tunnel 持久化

### 1. 嚴禁前景阻塞（Zero Blocking Rule）
在 headless 模式（如 Telegram Bot 或非互動 CLI）下，任何常駐服務（如 `npm run dev`、`vite`、`python app.py`、`cloudflared` 等）**若在前景直接執行，將導致終端機被永久霸佔、對話行程卡死**。

### 2. ⚠️ 關鍵痛點：為什麼 Cloudflare Tunnel 不應直接在主機以 `nohup` 執行？
- **Agent 行程清理機制**：在 AI Agent / CLI 環境中，每當一輪對話結束等待使用者輸入、Session 重置或服務重啟時，系統往往會清理當前任務的背景子行程（例如：`Notice: All background tasks stopped...` 或 SIGHUP 訊號）。
- **致命後果（Error 1033）**：免帳號的 Cloudflare Quick Tunnel（`*.trycloudflare.com`）是**連線即網址**的暫存通道。一旦主機的 `cloudflared` 行程被終止，該網址就**永久失效**，訪客訪問立即回傳 **Error 1033**！
- **✅ 徹底解決方案：Docker 容器化守護（黃金標準）**：
  - Docker 容器由系統級 `dockerd` 守護，其生命週期**完全獨立於 Agent 對話行程、子 Shell 或 systemd 服務**。
  - 搭配 `--restart unless-stopped`，無論對話如何輪替、等待或重啟，Tunnel 皆能持續運作，徹底杜絕 Error 1033！

---

## 二、 標準作業流程（SOP）

### 步驟 1：專案偵測與方案推薦
當使用者提出啟動、看網頁或部署需求時：
1. 檢視工作目錄的專案類型（檢查 `package.json`、`requirements.txt`、`Dockerfile`、`index.html` 等）。
2. 確定專案所屬 Port（常見預設：Vite 5173、React 3000、Flask 5000、FastAPI 8000、Vue 8080）。
3. **主動向使用者推薦部署模式**：
   - **方案 A（黃金推薦）：Docker 雙容器互聯模式**
     - 原理：Web 服務與 `cloudflared` 皆封裝為獨立容器，加入同一 Docker 內部網路互聯。
     - 優點：**絕對持久穩定**、環境隔離、自動重啟、永不因對話結束或服務重啟而出現 Error 1033。
   - **方案 B（開發熱重載推薦）：主機 Nohup + Docker Tunnel 守護**
     - 原理：Web 服務在主機以 `nohup` 執行（支援代碼即時熱重載），而 `cloudflared` 依然以 Docker 容器（`--net=host`）守護對外穿透。
     - 優點：邊聊邊改代碼立即生效，且對外網址依然持久不死。
   - **方案 C（無 Docker 時的備案）：純主機 Nohup 模式**
     - 僅在主機未安裝 Docker 時作為最後降級備案，並需提醒使用者網址可能在等待輸入時離線。

---

### 步驟 2：Port 佔用檢查與清理
啟動前確認目標 Port 未被佔用：
```bash
ss -tulpn | grep :<PORT> || lsof -i :<PORT>
# 若有殘留舊行程需清理：
fuser -k <PORT>/tcp 2>/dev/null || true
```

---

### 步驟 3：服務啟動執行規範

#### 🌟 方案 A（黃金推薦）：Docker 雙容器內部互聯
```bash
# 1. 建立獨立網路
docker network create <app>-net 2>/dev/null || true

# 2. 啟動 Web 服務容器
docker run -d --name <app> --network <app>-net --restart unless-stopped <image-name>

# 3. 啟動 Cloudflare Tunnel 容器互聯
docker run -d --name <app>-cf-tunnel --network <app>-net --restart unless-stopped \
  cloudflare/cloudflared:latest tunnel --url http://<app>:<internal-port>

# 4. 擷取對外網址
sleep 3
CF_URL=$(docker logs <app>-cf-tunnel 2>&1 | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' | head -n 1)
echo "Tunnel URL: $CF_URL"
```

#### ⚡ 方案 B（混合模式）：主機 Nohup App + Docker Tunnel 守護
```bash
# 1. 主機端背景啟動 Web 服務（以 Vite 5173 為例）
nohup npx vite --host 0.0.0.0 --port 5173 > /tmp/vite_5173.log 2>&1 &
sleep 2

# 2. 以 Docker --net=host 守護 Cloudflare Tunnel
docker run -d --name cf-5173-tunnel --net=host --restart unless-stopped \
  cloudflare/cloudflared:latest tunnel --url http://127.0.0.1:5173

# 3. 擷取對外網址
sleep 3
CF_URL=$(docker logs cf-5173-tunnel 2>&1 | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' | head -n 1)
echo "Tunnel URL: $CF_URL"
```

#### ⚠️ 方案 C（無 Docker 備案）：純主機 Nohup
```bash
nohup <啟動指令> > /tmp/<服務名>.log 2>&1 &
nohup cloudflared tunnel --url http://localhost:<PORT> > /tmp/cf_<PORT>.log 2>&1 &
sleep 3
CF_URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' /tmp/cf_<PORT>.log | head -n 1)
```

---

### 步驟 4：成果回報格式

```markdown
🚀 **服務啟動完成！**

- **運行模式**：方案 A（Docker 雙容器持久守護模式）
- **本地存取**：`http://localhost:<PORT>`
- **對外安全存取（手機/外部訪問）**：
  👉 [https://xxx.trycloudflare.com](https://xxx.trycloudflare.com)

---

📋 **維護與管理指引**：
- **查看 Web 日誌**：`docker logs -f <app>`
- **查看 Tunnel 日誌**：`docker logs -f <app>-cf-tunnel`
- **停止服務與連線**：`docker rm -f <app> <app>-cf-tunnel`
```

---

## 三、 常見故障排除

1. **網頁出現 Cloudflare Error 1033**：
   - **根本原因**：主機端 `cloudflared` 行程離線。若之前使用主機 `nohup` 執行，每當對話輪次結束、等待使用者輸入或服務重啟時，系統會自動終止背景任務（Notice: `All background tasks stopped`），導致 Quick Tunnel 網址失效。
   - **根治方法**：全面改用 Docker 容器運行 `cloudflared`（方案 A 或方案 B），由 Docker Daemon 保持背景不死運作。
   - **即時排查指令**：
     ```bash
     # 檢查 Tunnel 容器狀態
     docker ps | grep cloudflared
     # 查看 Tunnel 錯誤日誌
     docker logs --tail 50 <app>-cf-tunnel
     ```

2. **日誌中看到 `location=hkg09` 或 `location=tpe01`**：
   - 這是 Cloudflare 全球 Anycast 網路自動分配的邊緣機房節點（HKG=香港、TPE=台北），為標準網路分流，完全正常且未被阻擋。

3. **舊 Tunnel 容器或 Port 殘留**：
   - 若要重啟或清理舊的穿透容器：
     ```bash
     docker rm -f $(docker ps -aq --filter name=tunnel) 2>/dev/null || true
     ```
