"""集中收納系統所有 AI Prompt 提示詞、多模態引導約束、快捷指令模板與排程改寫提示詞。"""

from __future__ import annotations

from pathlib import Path

from hostspark.constants import NO_REPORT_SENTINEL


# =====================================================================
# 1. 多模態指示與原生引導約束 (Multimodal System Constraints & Hints)
# =====================================================================

MULTIMODAL_AUDIO_ASR_HINT: str = (
    "\n\n【重要多模態指示】：Gemini 具備原生音訊辨識（ASR）能力！"
    "請直接呼叫 view_file 工具讀取此音訊檔案路徑，即可直接取得極速高精準度繁體中文聽寫轉錄與語意分析，"
    "嚴禁在本機撰寫 Python 腳本或執行 whisper、pytorch 等耗損主機 CPU 資源的離線模型。"
)

MULTIMODAL_VIDEO_HINT: str = (
    "\n\n【提示】：可直接呼叫 view_file 工具載入此檔案進行 Gemini 原生多模態視訊分析。"
)

MULTIMODAL_IMAGE_HINT: str = (
    "\n\n【提示】：可直接呼叫 view_file 工具載入此圖片進行 Gemini 原生多模態視覺分析。"
)

# =====================================================================
# 2. 附件上傳之預設分析需求 (Default Multimodal Analysis Prompts)
# =====================================================================

DEFAULT_AUDIO_PROMPT: str = "請聆聽/解析此音訊內容，進行語音聽寫轉錄或提供重點摘要。"
DEFAULT_VIDEO_PROMPT: str = "請分析此影片內容並提供摘要。"
DEFAULT_IMAGE_PROMPT: str = "請分析此圖片並說明其內容。"
DEFAULT_DOCUMENT_PROMPT: str = "請分析此附件並提供摘要。"


def build_attachment_prompt(
    kind: str,
    target_path: str | Path,
    caption: str | None,
    default_prompt: str,
    hint: str = "",
) -> str:
    """組裝附件上傳至 AGY 的完整 Prompt，包含檔案路徑、使用者指示與多模態約束。"""
    description = caption if caption else default_prompt
    return f"使用者上傳了{kind}：`{target_path}`\n\n說明：{description}{hint}"


# =====================================================================
# 3. 系統快捷指令 Prompt 模板 (Slash Command Prompts)
# =====================================================================

LEARN_WITH_PAYLOAD_PROMPT: str = "請將以下對話內容與技巧整理為可重複使用的規則與 Skill：\n\n{payload}"
LEARN_DEFAULT_PROMPT: str = "請將本對話的經驗與技巧整理為可重複使用的規則與 Skill。"


def build_learn_prompt(payload: str | None = None) -> str:
    """產生 /learn 指令傳給 AGY 的 Prompt。"""
    if payload:
        return LEARN_WITH_PAYLOAD_PROMPT.format(payload=payload)
    return LEARN_DEFAULT_PROMPT


COMPACT_CONTEXT_PROMPT: str = "請壓縮目前對話的上下文，保留核心決策、狀態與待辦事項。"


# =====================================================================
# 4. 多輪對話追問與佇列合併 (Job Queue Chaining & Global Rules)
# =====================================================================

FOLLOWUP_PROMPT_PREFIX: str = "\n\n[Update / Follow-up]:\n"


def compose_followup_prompt(parts: list[str], latest_prompt: str) -> str:
    """將等待佇列中的多個連續請求與最新的追問組裝為單一 Prompt。"""
    return "\n\n".join(parts) + f"{FOLLOWUP_PROMPT_PREFIX}{latest_prompt}"


def compose_agy_prompt(user_text: str, rule_prompt: str) -> str:
    """將全域行為規則 (AGY_RULE_PROMPT) 與使用者文字請求組裝。"""
    if not rule_prompt:
        return user_text
    return f"{rule_prompt}\n\n使用者請求：\n{user_text}"


# =====================================================================
# 5. 排程改寫器系統提示詞 (Schedule Prompt Expansion)
# =====================================================================

SCHEDULE_EXPANSION_SYSTEM_PROMPT: str = """你是定時任務提示詞編輯器。請把下方使用者要求整理成一段可重複、獨立執行的完整 AGY 任務提示詞。

規則：
1. 不要現在執行任務，只重寫提示詞。
2. 保留使用者意圖，不自行擴張權限、操作範圍或通知頻率。
3. 明確說明每次應查詢、判斷、輸出什麼；資訊不足時採保守做法。
4. 可使用這些執行時變數：{{{{now}}}}、{{{{date}}}}、{{{{time}}}}、{{{{timezone}}}}、{{{{scheduled_at}}}}、{{{{run_number}}}}。
5. 使用者原本寫下的其他 {{{{變數}}}} 必須原樣保留，不可猜測其值。
6. 若使用者要求「沒有異常就不通知」之類條件，請規定無須通知時只輸出 {sentinel}。
7. 不要輸出 Markdown code fence、前言、分析或說明，只輸出整理後的提示詞。

cron：{cron_expr}
時區：{timezone_name}
使用者要求：
{original_prompt}"""


def build_prompt_expansion_request(
    original_prompt: str,
    cron_expr: str,
    timezone_name: str,
    sentinel: str = NO_REPORT_SENTINEL,
) -> str:
    """組裝呼叫 AGY 整理排程 Prompt 用的系統請求。"""
    return SCHEDULE_EXPANSION_SYSTEM_PROMPT.format(
        sentinel=sentinel,
        cron_expr=cron_expr,
        timezone_name=timezone_name,
        original_prompt=original_prompt,
    )
