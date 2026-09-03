from __future__ import annotations

import html
import logging
import shutil
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

import hostspark.state as state
import hostspark.core.executor as executor
from hostspark.core.sanitizer import build_safe_subprocess_env, redact_sensitive
from hostspark.telegram.auth import _get_chat_id, reject_unauthorized
from hostspark.telegram.formatters import send_formatted_response

logger = logging.getLogger(__name__)


def get_reply_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("/status"), KeyboardButton("/model"), KeyboardButton("/effort")],
        [KeyboardButton("/schedule_list"), KeyboardButton("/new"), KeyboardButton("/cancel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    mode = config.permission_mode
    mode_text = "Full（不逐次審核）" if mode == "full" else "Safe（遵循 AGY 權限規則）"
    bot_name = html.escape(config.bot_name)
    user_count = len(config.allowed_user_ids)

    msg = (
        f"🤖 <b>{bot_name} 在線中！</b>\n\n"
        f"• 授權使用者數：<b>{user_count}</b> 位\n"
        f"• 全域執行模式：<b>{mode_text}</b>\n\n"
        "💬 <b>對話管理</b>\n"
        "• <code>/new</code> 或 <code>/clear</code> - 開啟全新對話\n"
        "• <code>/session</code> - 檢視目前對話各項設定\n\n"
        "⚙️ <b>模型與執行設定</b>\n"
        "• <code>/model [名稱]</code> - 查看或切換模型\n"
        "• <code>/effort low|medium|high</code> - 設定推理深度\n"
        "• <code>/mode plan|accept-edits</code> - 切換執行模式\n"
        "• <code>/sandbox on|off</code> - 切換沙箱限制\n"
        "• <code>/verbose detailed|compact|silent</code> - 進度詳細度\n"
        "• <code>/setdefault</code> - 將目前設定儲存為全域預設值\n\n"
        "🛠️ <b>系統與遙控工具</b>\n"
        "• <code>/status</code> - 檢視 VM 與任務佇列狀態\n"
        "• <code>/usage</code> / <code>/quota</code> - 檢視 AGY 額度與用量\n"
        "• <code>/context</code> - 檢視上下文明細\n"
        "• <code>/cancel</code> - 取消目前對話中正在執行或排隊的任務\n"
        "• <code>/agy [ARGS]</code> - 自訂 CLI passthrough 指令\n\n"
        "⏰ <b>定時排程</b>\n"
        "• <code>/schedule_help</code> - 查看排程功能說明\n"
        "• <code>/schedule_list</code> - 列出所有定時任務\n\n"
        "請直接傳送文字訊息或圖片/檔案附件開始交談。"
    )
    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        reply_markup=get_reply_keyboard(),
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    await update.message.reply_text(
        "📱 <b>快捷功能選單</b>\n請使用下方鍵盤或輸入指令操作：",
        parse_mode=ParseMode.HTML,
        reply_markup=get_reply_keyboard(),
    )


async def _status_section(title: str, args: list[str]) -> str:
    config = state.get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    result = await executor.run_process(
        args,
        cwd=config.agy_workdir,
        env=env,
        timeout_seconds=15,
        max_output_bytes=200_000,
    )
    if result.timed_out:
        body = "查詢逾時"
    elif result.returncode != 0:
        body = result.stderr or f"命令失敗（exit {result.returncode}）"
    else:
        body = result.stdout or "沒有輸出"
    return f"**{title}**\n```\n{body}\n```"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    await context.bot.send_chat_action(
        chat_id=_get_chat_id(update), action=ChatAction.TYPING
    )

    q_stat = state.JOB_QUEUE.get_status()
    queue_line = (
        f"• 佇列等待任務：**{q_stat['queue_length']}** 個\n"
        f"• 執行狀態：**{'忙碌中 ⏳' if q_stat['is_busy'] else '閒置中 ✅'}**"
    )

    checks = [
        ("系統負載與運行時間", ["uptime"]),
        ("根目錄磁碟", ["df", "-h", "/"]),
        ("記憶體", ["free", "-h"]),
    ]
    docker_bin = shutil.which("docker")
    if docker_bin:
        checks.append(("Docker 容器", [docker_bin, "ps"]))

    sections = []
    for title, args in checks:
        try:
            sections.append(await _status_section(title, args))
        except FileNotFoundError:
            sections.append(f"**{title}**\n```\n命令不存在\n```")
        except Exception as exc:
            logger.exception("狀態查詢失敗：%s", title)
            sections.append(f"**{title}**\n```\n查詢失敗：{redact_sensitive(str(exc))}\n```")

    msg = f"📊 **VM 即時健康與任務狀態**\n\n{queue_line}\n\n" + "\n\n".join(sections)
    await send_formatted_response(update.message, msg)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    cancelled = state.JOB_QUEUE.cancel_for_chat(chat_id)
    if cancelled:
        await update.message.reply_text("🛑 已取消此 Chat 進行中與佇列中的任務。")
    else:
        await update.message.reply_text("ℹ️ 目前沒有正在執行或等待中的任務。")
