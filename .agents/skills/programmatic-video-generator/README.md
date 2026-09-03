# 🎬 Programmatic Video Generator - 提示詞範例與使用指南

`programmatic-video-generator` 是一套全自動、純程式碼驅動的 1080p 高畫質簡報與展示影片生成技能。只要在對話中輸入自然語言需求，AI 即可自動呼叫本 Skill 完成**資料企劃 ➔ 12 大版型排版 ➔ Ken Burns 動態運鏡 ➔ 11 種電影級轉場 ➔ 錄音室 MP3 混音 ➔ 1080p MP4 影片輸出**。

---

## 💡 一、 快速啟用提示詞範例 (Prompt Examples by Topic)

您可以直接複製以下提示詞範例（或依需求修改主題與秒數），在與 AI 對話時直接使用：

### 1. 🏢 科技產品發表與 AI 算力架構 (Tech & Product Launch)
> **提示詞**：
> 「請使用 `programmatic-video-generator` 為我們即將發表的『NextGen Cloud AI 平台』製作一段 1 分鐘（6 頁）的 1080p 發表會動態宣傳影片。內容需包含：分散式叢集架構、99.99% SLA 保證、雙重液冷節能以及全球開發者生態系，搭配科技感藍紫配色與 320kbps 鋼琴輕音樂。」

### 2. 🌍 城市觀光與國家地理旅遊 (City & Tourism Showcase)
> **提示詞**：
> 「我想製作一部 2 分鐘（12 頁）介紹『日本京都四季之美』的旅遊導覽影片。請用 `programmatic-video-generator`，每頁包含不同的視覺排版（如櫻花春季、清水寺木造建築、祇園祭文化、宇治抹茶美食與嵐山竹林），並加入電影級 Ken Burns 鏡頭推移與 1.2 秒自然轉場。」

### 3. 📈 企業年度財報與營運成果 (Business & Financial Review)
> **提示詞**：
> 「請用 `programmatic-video-generator` 製作一份『2025 年度 Q4 營運成果報告』影片，共 8 頁（約 80 秒）。需涵蓋：年度營收突破 50 億（使用 2 欄大數據版型）、海外市場成長 180%、三大核心產品線營收佔比、客戶滿意度達 98%，並於結尾給出 2026 展望。」

### 4. 🍃 ESG 永續發展與綠能轉型 (ESG & Sustainability)
> **提示詞**：
> 「請以『2050 淨零排放與綠色供應鏈』為主題，使用 `programmatic-video-generator` 製作一段 90 秒的展示影片。視覺採用森林綠與天藍配色，內容包含：離岸風電容量、RE100 綠電承諾、循環包裝減塑 40% 與社會公益成果。」

### 5. 🏥 智慧醫療與生物科技 (Smart Healthcare & Biotech)
> **提示詞**：
> 「請幫我製作一部 1 分鐘的『AI 輔助精準醫療系統』成果展示影片。使用 `programmatic-video-generator`，重點突顯：醫療影像百萬筆辨識模型、輔助診斷準確率達 96.5%、多中心臨床驗證，以及符合 HIPAA 隱私安全標準。」

### 6. 🎓 知識科普與學術研究 (Academic & Educational Explainer)
> **提示詞**：
> 「請製作一段 6 頁的科普教學影片，主題為『量子運算基本原理』。請利用這個影片生成 Skill，以圖文卡片形式講解量子疊加態、量子糾纏、量子位元 (Qubit) 以及未來在密碼學與藥物開發的應用。」

### 7. 👨‍💻 個人作品集與開源專案展示 (Developer & Portfolio Showcase)
> **提示詞**：
> 「請幫我的開源專案製作一段 4 頁（40 秒）的 GitHub Repo 亮點展示影片。內容包含：專案特色、架構流程圖卡、效能 Benchmark 對比以及 Quick Start 安裝指令，並在最後一頁放上 GitHub 網址與 Star 邀請！」

---

## 🔑 二、 提示詞撰寫心法與關鍵參數 (Prompting Best Practices)

若想獲得最佳效果，建議在提示詞中指定以下要素：

| 參數維度 | 建議指定方式 | 範例說明 |
| :--- | :--- | :--- |
| **主題與大綱** | 清楚說明主題名稱與 3~5 個核心重點 | 「介紹台灣高山與科技產業」 |
| **總長度 / 頁數** | 指定秒數或每頁秒數（預設每頁約 10 秒） | 「總長度 2 分鐘（共 12 頁）」 |
| **視覺版型需求** | 要求版型多樣化或指定特定版型 | 「每一頁排版要有變化」、「使用儀表板版型」 |
| **運鏡與轉場** | 指定動態鏡頭與轉場風格 | 「加入 Ken Burns 鏡頭縮放、1.2秒慢速轉場」 |
| **音樂類型** | 指定 MP3 風格（鋼琴、吉他、自然輕音樂） | 「搭配 320kbps 錄音室真實鋼琴輕音樂」 |

---

## 🛠️ 三、 12 大排版版型代碼速查 (Layout Archetypes Reference)

- `hero_poster`：巨幅海報置中（開場、主標題、3 大指標徽章）
- `split_2col`：左右二分雙欄（左側 90%+ 巨幅指標 + 右側水平條列卡）
- `dashboard_racks`：機房與科技儀表板（頂部 KPI 雙方塊 + 底部 3 欄深色機櫃卡）
- `pyramid_peak`：金字塔頂峰排版（中央高聳峰頂卡 + 兩側對稱基礎卡）
- `vertical_columns`：長條立柱多欄版（3~4 條垂直高聳長條卡片）
- `timeline_track`：橫向時間軸與軌道（長軸節點串聯里程碑）
- `asymmetric_showcase`：主次不對稱展示（左側 55% 寬屏主卡 + 右側雙層疊加小卡）
- `quadrant_grid`：四象限與九宮格方塊（2×2 網格卡片）
- `radial_ring`：核心環繞交融版（中央圓形印鑑 + 4 角特色卡片）
- `circle_stats`：圓形指標與寬卡（左側大圓形數字進度 + 右側雙寬屏基礎卡）
- `quote_testimonial`：溫暖名言故事版（巨幅毛玻璃名言卡片 + 3 顆評比徽章）
- `portal_cta`：宏觀世界之門結尾（金色放射光芒 + 3 根未來支柱 + 醒目 CTA 按鈕）

---

## 💻 四、 命令列直接調用方式 (CLI Usage)

```bash
# 1. 執行內建通用展示 Demo
python3 .agents/skills/programmatic-video-generator/scripts/generate_video.py --output demo.mp4

# 2. 傳入自訂 slides.json 與 MP3 音樂
python3 .agents/skills/programmatic-video-generator/scripts/generate_video.py \
  --config my_slides.json \
  --audio /path/to/music.mp3 \
  --output my_presentation.mp4 \
  --slide-duration 10.0 \
  --transition-duration 1.2
```
