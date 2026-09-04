"""全域系統常數與預設配置（包含副檔名白名單、時間閥值、限額與標記等）。"""

from __future__ import annotations

# =====================================================================
# 1. 檔案與多媒體副檔名白名單 (File & Multimedia Extension Whitelists)
# =====================================================================
PHOTO_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
})
IMAGE_EXTENSIONS: frozenset[str] = PHOTO_EXTENSIONS | frozenset({".svg", ".bmp"})
AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac", ".opus", ".wma",
})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
})
DOC_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".txt", ".md", ".json", ".csv", ".py", ".go", ".js", ".ts",
    ".yaml", ".yml", ".toml", ".log",
})
SAFE_EXTENSIONS: frozenset[str] = (
    DOC_EXTENSIONS | IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
)

# =====================================================================
# 2. Telegram 訊息格式與傳輸硬限制 (Telegram Protocol & Message Limits)
# =====================================================================
# Telegram 官方單則訊息上限為 4096 字元；分塊設定 3500 預留 HTML 標籤閉合空間
TELEGRAM_MESSAGE_MAX_CHUNK_SIZE: int = 3500
# 訊息長度超過此字元數時轉為 Markdown 檔案傳送，以維護閱讀體驗
LONG_MESSAGE_FILE_THRESHOLD_CHARS: int = 7000
# Telegram Bot API 機器人檔案傳輸上限（50 MB）
TELEGRAM_BOT_FILE_MAX_BYTES: int = 50 * 1024 * 1024

# =====================================================================
# 3. 即時串流、心跳與狀態卡片時間閥值 (Streaming & Heartbeat Thresholds)
# =====================================================================
# 串流輸出編輯 Telegram 訊息的最低防抖間隔（秒），避免觸發 Telegram 每分鐘 30 次編輯限制
STATUS_EDIT_DEBOUNCE_SECONDS: float = 1.8
# 發送「輸入中...」(ChatAction.TYPING) 的週期（秒）
TYPING_HEARTBEAT_INTERVAL_SECONDS: float = 4.0
# 當底層無任何新輸出時，卡片跳秒心跳的最低刷新間隔（秒）
TYPING_HEARTBEAT_EDIT_INTERVAL_SECONDS: float = 3.5
# 底層無回應達到此秒數時，提示「指令執行時間較長，可點擊 /cancel 中斷」
STALL_WARNING_THRESHOLD_SECONDS: int = 30
# 狀態卡片在 compact 模式下擷取的指令字串最大長度
COMPACT_STATUS_SNIPPET_MAX_CHARS: int = 200
# 狀態卡片在 detailed 模式下擷取的日誌字串最大長度
DETAILED_STATUS_SNIPPET_MAX_CHARS: int = 800

# =====================================================================
# 4. 網路防禦與外部媒體下載限制 (SSRF & Safe Media Fetching)
# =====================================================================
# 抓取外部媒體之最大位元組限制（10 MB）
SSRF_MEDIA_DOWNLOAD_MAX_BYTES: int = 10_000_000
# 抓取外部媒體的 HTTP 超時時間（秒）
SSRF_MEDIA_DOWNLOAD_TIMEOUT_SECONDS: float = 10.0

# =====================================================================
# 5. 子進程與對話快取超時閥值 (Subprocess & Context Cache Thresholds)
# =====================================================================
# PTY 快捷子進程（如 /quota, /context, git pull）的預設超時秒數
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS: float = 30.0
# /context 查詢快取的有效存活時間（秒）
PENDING_CONTEXT_TTL_SECONDS: float = 600.0

# =====================================================================
# 6. 任務佇列與排程器閥值 (Job Queue & Scheduler Thresholds)
# =====================================================================
# 預設非同步任務排隊佇列上限
DEFAULT_JOB_QUEUE_MAXSIZE: int = 50
# 排程任務連續失敗自動暫停（熔斷保護）門檻次數
DEFAULT_CIRCUIT_BREAKER_MAX_FAILURES: int = 3
# 排程器每日定時清理暫存（uploads / workspaces）的保留天數
DEFAULT_SCHEDULE_CLEANUP_MAX_AGE_DAYS: int = 30
# 排程器單次批次取得待執行排程之上限數量
SCHEDULE_CLAIM_BATCH_LIMIT: int = 10
# 排程原始使用者輸入的最大字元限制
SCHEDULE_MAX_ORIGINAL_PROMPT_CHARS: int = 4_000
# 排程擴充改寫後 prompt 的最大字元限制
SCHEDULE_MAX_EXPANSION_PROMPT_CHARS: int = 8_000

# =====================================================================
# 6. 系統特殊標記 (Sentinels)
# =====================================================================
# 定時任務若判斷「無異常無須通知」時由 LLM 輸出的靜默標記
NO_REPORT_SENTINEL: str = "[NO_REPORT]"
