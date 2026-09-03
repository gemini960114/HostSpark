from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

from hostspark.core.executor import ProcessResult, is_headless_permission_denied


def split_markdown_into_chunks(text: str, max_chunk_size: int = 3500) -> list[str]:
    if max_chunk_size < 1:
        raise ValueError("max_chunk_size 必須大於 0")
    if not text:
        return [""]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chunk_size, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end + 1)
            if newline >= start + max_chunk_size // 2:
                end = newline + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def md_to_telegram_html(text: str) -> str:
    def replace_callout(match: re.Match[str]) -> str:
        kind = match.group(1).upper()
        emoji = "💡"
        if kind in {"WARNING", "CAUTION"}:
            emoji = "⚠️"
        elif kind in {"IMPORTANT", "CRITICAL"}:
            emoji = "❗"
        elif kind == "TIP":
            emoji = "✨"
        return f"{emoji} <b>{kind}</b>:\n"

    text = re.sub(r"^>\s*\[!([A-Z]+)\]\s*", replace_callout, text, flags=re.MULTILINE)

    code_blocks: list[tuple[str, str]] = []

    def save_code_block(match: re.Match[str]) -> str:
        code_blocks.append((match.group(1) or "", match.group(2)))
        return f"\x00CB_{len(code_blocks) - 1}\x00"

    text = re.sub(r"```(\w*)\n([\s\S]*?)```", save_code_block, text)

    inline_codes: list[str] = []

    def save_inline_code(match: re.Match[str]) -> str:
        inline_codes.append(match.group(1))
        return f"\x00IC_{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", save_inline_code, text)
    text = html.escape(text)

    def replace_link(match: re.Match[str]) -> str:
        return f'<a href="{match.group(2)}">{match.group(1)}</a>'

    text = re.sub(r"\[(.*?)\]\((https?://[^\s\)]+)\)", replace_link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    for idx, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC_{idx}\x00", f"<code>{html.escape(code)}</code>")

    for idx, (lang, code) in enumerate(code_blocks):
        escaped_code = html.escape(code.rstrip())
        if lang:
            tag = f'<pre><code class="language-{html.escape(lang)}">{escaped_code}</code></pre>'
        else:
            tag = f"<pre>{escaped_code}</pre>"
        text = text.replace(f"\x00CB_{idx}\x00", tag)

    return text


def format_result_message(result: ProcessResult, permission_mode: str) -> str:
    truncation_note = "\n\n⚠️ 輸出過長，僅顯示前段內容。" if (
        result.stdout_truncated or result.stderr_truncated
    ) else ""
    if result.timed_out:
        return "⚠️ **AGY 執行逾時，程序已停止。**" + truncation_note

    if permission_mode == "safe" and is_headless_permission_denied(result.stderr):
        return (
            "⚠️ **很抱歉，此操作因 Safe 權限模式限制無法完成。**\n\n"
            "目前機器人運行於 `Safe` 權限模式，執行系統工具或指令時需要終端機互動確認授權；"
            "但因 Telegram 於背景非互動環境運作，無法向您彈出授權視窗，因此該工具權限已被自動拒絕。\n\n"
            "💡 **如何解除限制？**\n"
            "若要讓機器人能夠直接執行指令與排查任務，請在 `.env` 中將 `AGY_PERMISSION_MODE` 改為 `full` 並重啟服務："
            "\n```bash\nsudo systemctl restart agy-telegram.service\n```\n"
            "Full 模式會自動核准所有 AGY 工具操作，只應用於私人、可快照且可重建的專用 VM。"
        )

    if result.returncode != 0:
        details = result.stderr or result.stdout or "沒有錯誤詳情"
        return f"❌ **AGY 執行失敗（exit {result.returncode}）**\n\n{details}{truncation_note}"
    if result.stdout:
        return result.stdout + truncation_note
    if result.stderr:
        return f"⚠️ AGY 沒有標準輸出：\n\n{result.stderr}{truncation_note}"
    return "✅ 執行完成。"
