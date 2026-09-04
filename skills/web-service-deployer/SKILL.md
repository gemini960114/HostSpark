---
name: web-service-deployer
description: >-
  當使用者需要啟動、運行、部署或預覽任何網頁應用（React, Vue, Vite, Next.js, Flask, FastAPI, Express, HTML 靜態網站等），
  或提出「對外開放連線」、「手機預覽測試」、「分享給別人看」、「遠端存取」等需求時啟用此技能。
  即使使用者未主動提及 nohup、Docker 或 Cloudflare，此技能亦會自動主動提供方案建議並引導外部穿透連線。
---

# Web 服務啟動與對外連線部署技能手冊

本技能規範了在主機上安全啟動網頁服務、預防終端機阻塞死鎖（Terminal Hang），以及主動引導 Cloudflare 臨時穿透對外連線的標準工作流程。

---

## 一、 核心原則：嚴禁終端阻塞（Zero Blocking Rule）

在 headless 模式（如 Telegram Bot 或非互動 CLI）下，任何常駐服務（如 `npm run dev`、`vite`、`python app.py`、`cloudflared` 等）**若在前景直接執行，將導致終端機被永久霸佔、對話行程卡死**。

- ❌ **嚴禁直接執行**：`npm run dev`、`npx vite`、`cloudflared tunnel ...`
- ✅ **一律脫鉤執行**：
  - Nohup 模式：`nohup <指令> > /tmp/<服務名>.log 2>&1 &`
  - Docker 模式：`docker run -d ...`

---

## 二、 標準作業流程（SOP）

### 步驟 1：專案偵測與方案諮詢
當使用者提出啟動、看網頁或部署需求時：
1. 檢視當前工作目錄的專案類型（檢查 `package.json`、`requirements.txt`、`Dockerfile`、`index.html` 等）。
2. 確定專案所屬 Port（常見預設：Vite 5173、React 3000、Flask 5000、FastAPI 8000、Vue 8080）。
3. **主動向使用者說明並詢問偏好的啟動模式**：
   - **方案 A：Nohup 輕量背景模式（推薦用於即時開發與快速修改）**
     - 原理：直接在主機背景執行原始碼。
     - 優點：免建映像檔，程式碼修改後立即熱重載（Hot Reload），適合邊聊邊改的開發階段。
   - **方案 B：Docker 容器化模式（推薦用於長期穩定與環境隔離）**
     - 原理：封裝至容器背景獨立運作。
     - 優點：乾淨隔離、重啟主機自動重啟（`--restart unless-stopped`），適合測試完成後的正式運行。
   - **詢問對外連線需求**：
     - 主動詢問：「是否需要同時建立 **Cloudflare 免費臨時對外安全連線**，方便您使用手機瀏覽或分享給他人測試？」

> [!TIP]
> 若使用者已在指令中明確表示「直接幫我用 nohup 跑」或「用 docker 跑」並要求提供連結，則直接執行對應流程，無需重複反問。

---

### 步驟 2：Port 佔用檢查與清理
啟動前，必須先確認目標 Port 未被舊的殘留行程佔用：
```bash
# 檢查 Port（以 5173 為例）
ss -tulpn | grep :5173 || lsof -i :5173
```
若有舊的殘留行程且使用者要求重新啟動，可清理舊行程：
```bash
# 清理指定 Port 上的舊行程
fuser -k 5173/tcp 2>/dev/null || true
```

---

### 步驟 3：服務啟動執行規範

#### 選擇方案 A：Nohup 背景脫鉤啟動
必須遵循標準的輸出重定向與背景分叉語法：

```bash
# 範例：Vite / React 專案
nohup npx vite --host 0.0.0.0 --port 5173 > /tmp/vite_5173.log 2>&1 &
sleep 2

# 驗證是否成功監聽
ss -tulpn | grep :5173
```

- **其他常見啟動範例**：
  - Python HTTP 伺服器：`nohup python3 -m http.server 8000 > /tmp/http_8000.log 2>&1 &`
  - FastAPI / Uvicorn：`nohup uvicorn main:app --host 0.0.0.0 --port 8000 > /tmp/app_8000.log 2>&1 &`
  - Node.js Express：`nohup node server.js > /tmp/node_3000.log 2>&1 &`

#### 選擇方案 B：Docker 容器啟動
```bash
# 範例：如果已存在 Dockerfile
docker build -t my-app .
docker run -d --name my-app -p 5173:5173 --restart unless-stopped my-app
```

---

### 步驟 4：Cloudflare Quick Tunnel 對外穿透

若使用者需要對外連線（或詢問後同意開啟）：
使用免帳號、即開即用的 Cloudflare Quick Tunnel，**同樣必須以 nohup 背景脫鉤啟動**：

```bash
# 啟動 Tunnel 並將日誌寫入 /tmp
nohup cloudflared tunnel --url http://localhost:5173 > /tmp/cf_5173.log 2>&1 &
sleep 3

# 從日誌中擷取 trycloudflare.com 網址
CF_URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' /tmp/cf_5173.log | head -n 1)
echo "Tunnel URL: $CF_URL"
```

---

### 步驟 5：成果回報格式

成功啟動後，依以下標準模板回覆使用者：

```markdown
🚀 **服務啟動完成！**

- **運行模式**：方案 A（Nohup 輕量背景模式）
- **本地存取**：`http://localhost:5173`
- **對外安全存取（手機/外部訪問）**：
  👉 [https://xxx.trycloudflare.com](https://xxx.trycloudflare.com)

---

📋 **維護與管理指引**：
- **查看即時日誌**：`tail -f /tmp/vite_5173.log`
- **停止服務**：`fuser -k 5173/tcp`
- **停止對外連線**：`pkill -f "cloudflared tunnel --url http://localhost:5173"`
```

---

## 三、 常見故障排除
1. **Cloudflare Tunnel 未產生網址**：
   檢查 `/tmp/cf_<PORT>.log`，若連線過慢可稍微等待 2 秒再讀取一次。
2. **本機無法連線**：
   檢查服務是否綁定在 `0.0.0.0` 而非 `127.0.0.1`（Vite 需加上 `--host 0.0.0.0`）。
3. **舊 Tunnel 殘留**：
   若要開啟新穿透，先 `pkill -f "cloudflared tunnel"` 確保乾淨。
4. **網頁出現 Cloudflare Error 1033**：
   - **原因**：主機端的 `cloudflared` 行程已中斷或被終止（例如系統服務重啟、行程被 Kill），導致 Cloudflare 邊緣 CDN 無法連回主機。
   - **重要觀念**：免帳號 Quick Tunnel（`*.trycloudflare.com`）為**臨時隨機網址**，生命週期與 `cloudflared` 行程綁定。一旦行程結束，該舊網址即永久失效，再次啟動時會產生一組全新隨機網址。
   - **排查與修復**：
     ```bash
     # 檢查行程是否存活
     ps aux | grep cloudflared
     # 重新啟動 Tunnel 並取得新網址
     nohup cloudflared tunnel --url http://localhost:<PORT> > /tmp/cf_<PORT>.log 2>&1 &
     ```
5. **日誌中看到 `location=hkg09` 或 `location=tpe01`**：
   - 這是 Cloudflare 全球 Anycast 網路自動分配的邊緣機房節點（HKG=香港、TPE=台北），為標準網路分流，完全正常且未被阻擋。
