import asyncio
import html
import io
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agy_bot_core import (
    BotConfig,
    ConfigError,
    ProcessResult,
    build_safe_subprocess_env,
    compose_agy_prompt,
    format_result_message,
    is_headless_permission_denied,
    load_config,
    md_to_telegram_html,
    redact_sensitive,
    detect_schedule_intent,
    model_has_baked_in_effort,
    run_process,
    safe_join,
    split_markdown_into_chunks,
)
from agy_stream import run_agy_streaming
from chat_state import ChatSettings, ChatStateStore
from cli_passthrough import (
    is_dangerous_custom_command,
    parse_cli_args,
    prepare_custom_args,
    validate_custom_args,
)
from instance_lock import InstanceLock, InstanceLockError
from job_queue import Job, JobQueue
from media_resolver import detect_output_media, fetch_ssrf_safe_media
from pending_actions import PendingActionStore
from pty_runner import run_pty_command
from schedule_store import (
    NO_REPORT_SENTINEL,
    DueSchedule,
    Schedule,
    ScheduleError,
    ScheduleStore,
    build_prompt_expansion_request,
    normalize_cron,
    parse_schedule_add_payload,
    render_prompt_variables,
)


import hostspark.state as state
from hostspark.state import (
    BASE_DIR,
    ENV_PATH,
    JOB_QUEUE,
    PENDING_ACTIONS,
    agy_lock,
    get_agy_lock,
    get_chat_state_store,
    get_config,
    get_instance_lock,
    get_job_queue,
    get_pending_actions,
    get_schedule_store,
    pop_context_injection,
    queue_context_injection,
    set_instance_lock,
)


load_dotenv(ENV_PATH)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

UTC = timezone.utc

SAFE_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".json", ".csv", ".py", ".go", ".js", ".ts",
    ".yaml", ".yml", ".toml", ".log", ".png", ".jpg", ".jpeg", ".webp", ".gif",
}



def is_authorized(
    user_id: int,
    chat_id: int | None = None,
    chat_type: str | None = None,
) -> bool:
    config = get_config()
    if user_id not in config.allowed_user_ids:
        return False
    if config.allowed_chat_ids and chat_id is not None and chat_id not in config.allowed_chat_ids:
        return False
    if config.private_only and chat_type is not None and chat_type != "private":
        return False
    return True


async def reject_unauthorized(update: Update) -> bool:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    user_id = getattr(user, "id", None) if user else None
    chat_id = getattr(chat, "id", None) if chat else None
    chat_type = getattr(chat, "type", None) if chat else None

    if user_id is not None and is_authorized(user_id, chat_id, chat_type):
        return False
    if user_id is not None:
        logger.warning("未授權訪問：user_id=%s, chat_id=%s", user_id, chat_id)
    if getattr(update, "message", None) and hasattr(update.message, "reply_text"):
        await update.message.reply_text("⛔ 您沒有權限使用此機器人。")
    return True

def _get_chat_id(update: Update, default: int = 0) -> int:
    chat = getattr(update, "effective_chat", None)
    if chat and hasattr(chat, "id"):
        return chat.id
    user = getattr(update, "effective_user", None)
    if user and hasattr(user, "id"):
        return user.id
    query = getattr(update, "callback_query", None)
    if query and getattr(query, "message", None) and getattr(query.message, "chat", None):
        return query.message.chat.id
    return default


def get_reply_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("/status"), KeyboardButton("/model"), KeyboardButton("/mode")],
        [KeyboardButton("/schedule_list"), KeyboardButton("/new"), KeyboardButton("/cancel")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


async def run_agy(
    user_text: str,
    *,
    chat_id: int | None = None,
    continue_conversation: bool = True,
    workdir: Path | None = None,
    add_primary_workdir: bool = False,
    allow_full_permissions: bool = True,
    on_chunk: Any = None,
    on_event: Any = None,
) -> ProcessResult:
    config = get_config()
    if chat_id is not None:
        injected_report = pop_context_injection(chat_id)
        if injected_report:
            user_text = (
                "（以下是系統剛才查詢指令的結果，供你回答使用者接下來這句話時參考，"
                "使用者看得到這份報告，不用整段複述）：\n"
                f"{injected_report}\n\n"
                f"使用者的訊息：\n{user_text}"
            )
    prompt = compose_agy_prompt(user_text, config.rule_prompt)
    args = [str(config.agy_bin), "-p", prompt]

    chat_state: ChatSettings | None = None
    if chat_id is not None:
        chat_state = get_chat_state_store().get_or_create(chat_id)

    if chat_id is not None and workdir is None:
        # `--continue` resumes "the most recently active conversation in this
        # working directory" -- it is scoped by cwd, NOT by Telegram chat/user
        # (confirmed empirically: two unrelated `--continue` calls from the same
        # cwd share history; from different cwds they don't). Every normal
        # per-chat turn used to share `config.agy_workdir` as cwd, which meant a
        # second authorized chat's plain `--continue` message could silently
        # resume and see context from a DIFFERENT chat's conversation. Giving
        # each chat_id its own dedicated cwd makes `--continue` naturally scope
        # to that chat alone, without changing anything else about how
        # continuation/--conversation binding works.
        workdir = config.state_db_path.parent / "workspaces" / f"chat-{chat_id}"
        workdir.mkdir(parents=True, exist_ok=True)
        add_primary_workdir = True

    if chat_state:
        if chat_state.conversation_id:
            args.extend(["--conversation", chat_state.conversation_id])
        elif continue_conversation and chat_state.continue_enabled:
            args.append("--continue")

        if chat_state.model:
            args.extend(["--model", chat_state.model])
        # Models whose own name already bakes in an effort tier (e.g. the
        # "-high"/"-medium"/"-low" suffixed Gemini/GPT-OSS models) reject any
        # --effort flag that doesn't match that suffix. Since our default
        # per-chat effort is "high", picking e.g. gemini-3.7-flash-medium would
        # otherwise always fail with "--model X conflicts with --effort=high".
        if chat_state.effort and not model_has_baked_in_effort(chat_state.model):
            args.extend(["--effort", chat_state.effort])

        if config.permission_mode == "full" and chat_state.mode == "accept-edits":
            args.extend(["--mode", "accept-edits"])
        else:
            args.extend(["--mode", "plan"])

        if chat_state.sandbox:
            args.append("--sandbox")

        if chat_state.agent:
            args.extend(["--agent", chat_state.agent])
        if chat_state.project:
            args.extend(["--project", chat_state.project])

        for extra_dir in chat_state.add_dirs:
            args.extend(["--add-dir", extra_dir])

        if chat_state.output_format and chat_state.output_format != "text":
            args.extend(["--output-format", chat_state.output_format])
        if chat_state.json_schema:
            args.extend(["--json-schema", chat_state.json_schema])
        if chat_state.log_file:
            args.extend(["--log-file", chat_state.log_file])
        if chat_state.print_timeout:
            args.extend(["--print-timeout", chat_state.print_timeout])
        else:
            args.extend(["--print-timeout", f"{config.timeout_seconds}s"])

        if chat_state.new_project:
            args.append("--new-project")
        if chat_state.disable_slash_commands:
            args.append("--disable-slash-commands")
    else:
        if continue_conversation:
            args.append("--continue")
        args.extend(["--print-timeout", f"{config.timeout_seconds}s"])

    if config.permission_mode == "full" and allow_full_permissions:
        args.append("--dangerously-skip-permissions")
    if add_primary_workdir and workdir != config.agy_workdir:
        args.extend(["--add-dir", str(config.agy_workdir)])

    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    run_cwd = workdir or config.agy_workdir

    if on_chunk or on_event:
        return await run_agy_streaming(
            args,
            cwd=run_cwd,
            env=env,
            timeout_seconds=config.timeout_seconds + 10,
            max_output_bytes=config.max_output_bytes,
            on_chunk=on_chunk,
            on_event=on_event,
        )

    return await run_process(
        args,
        cwd=run_cwd,
        env=env,
        timeout_seconds=config.timeout_seconds + 10,
        max_output_bytes=config.max_output_bytes,
    )


def result_message(result: ProcessResult) -> str:
    return format_result_message(result, get_config().permission_mode)


async def send_formatted_response(message, text: str) -> None:
    # If response is exceptionally long (> 7000 chars), send as document file
    if len(text) > 7000:
        try:
            bio = io.BytesIO(text.encode("utf-8"))
            bio.name = f"response_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            await message.reply_document(
                document=bio,
                caption="📄 回覆內容過長，已將完整內容儲存為 Markdown 檔案傳送。",
            )
            return
        except Exception as exc:
            logger.warning("以檔案發送超長訊息失敗，退回分段文字發送：%s", exc)

    for chunk in split_markdown_into_chunks(text, max_chunk_size=3500):
        try:
            await message.reply_text(
                md_to_telegram_html(chunk),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("HTML 格式傳送失敗，改用純文字：%s", exc)
            try:
                await message.reply_text(chunk)
            except Exception:
                logger.exception("Telegram 訊息傳送失敗")


async def send_formatted_to_chat(bot, chat_id: int, text: str) -> None:
    if len(text) > 7000:
        try:
            bio = io.BytesIO(text.encode("utf-8"))
            bio.name = f"schedule_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            await bot.send_document(
                chat_id=chat_id,
                document=bio,
                caption="📄 排程執行回覆過長，已轉為 Markdown 檔案傳送。",
            )
            return
        except Exception as exc:
            logger.warning("排程超長訊息檔案傳送失敗，改用文字：%s", exc)

    for chunk in split_markdown_into_chunks(text, max_chunk_size=3500):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=md_to_telegram_html(chunk),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as html_exc:
            logger.warning("排程訊息 HTML 傳送失敗，改用純文字：%s", html_exc)
            await bot.send_message(chat_id=chat_id, text=chunk)


# -----------------------------------------------------------------------------
# Handlers: Start / Help / Menu / Status
# -----------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
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
    config = get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    result = await run_process(
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

    q_stat = JOB_QUEUE.get_status()
    # NOTE: this message is sent via send_formatted_response(), which runs the
    # Markdown→HTML converter (md_to_telegram_html); it must use Markdown `**bold**`,
    # not raw HTML tags, or html.escape() inside that converter will show the
    # tags as literal text instead of rendering them.
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


# -----------------------------------------------------------------------------
# Handlers: Conversation Management (/new, /clear, /continue)
# -----------------------------------------------------------------------------

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    get_chat_state_store().clear_conversation(chat_id)
    await update.message.reply_text("✅ 已重置對話工作階段。下一個提問將開啟全新對話。")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await new_command(update, context)


async def continue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"on", "true", "1", "enable", "off", "false", "0", "disable"}:
        await update.message.reply_text("用法：`/continue on` 或 `/continue off`")
        return

    arg = context.args[0].lower()
    chat_id = _get_chat_id(update)
    store = get_chat_state_store()
    if arg in {"on", "true", "1", "enable"}:
        store.update(chat_id, continue_enabled=True)
        await update.message.reply_text("✅ 已啟用自動延續對話（`--continue`）。")
    else:
        store.update(chat_id, continue_enabled=False)
        await update.message.reply_text("🔒 已停用自動延續對話。")


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    settings = get_chat_state_store().get_or_create(chat_id)
    config = get_config()

    add_dirs_str = ", ".join(settings.add_dirs) if settings.add_dirs else "(無)"
    msg = (
        f"⚙️ <b>目前 Chat 對話設定</b>\n\n"
        f"• 模型：<code>{settings.model or '預設'}</code>\n"
        f"• 推理深度 (effort)：<code>{settings.effort}</code>\n"
        f"• 執行模式 (mode)：<code>{settings.mode}</code> (全域: {config.permission_mode})\n"
        f"• 沙箱限制 (sandbox)：<code>{'啟用' if settings.sandbox else '停用'}</code>\n"
        f"• 進度詳細度 (verbose)：<code>{settings.verbose}</code>\n"
        f"• 自動接續對話：<code>{'是' if settings.continue_enabled else '否'}</code>\n"
        f"• 自訂 Agent：<code>{settings.agent or '(預設)'}</code>\n"
        f"• 專案 (project)：<code>{settings.project or '(無)'}</code>\n"
        f"• 額外目錄 (add_dirs)：<code>{add_dirs_str}</code>\n"
        f"• 輸出格式 (output_format)：<code>{settings.output_format}</code>\n"
        f"• 更新時間：<code>{settings.updated_at}</code>\n\n"
        "💡 可使用 <code>/setdefault</code> 將上述設定寫入 <code>.env</code> 全域預設。"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


# -----------------------------------------------------------------------------
# Handlers: Configuration Commands (/model, /effort, /mode, /sandbox, /verbose, /setdefault)
# -----------------------------------------------------------------------------

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    store = get_chat_state_store()

    if context.args:
        new_model = context.args[0].strip()
        store.update(chat_id, model=new_model)
        await update.message.reply_text(f"✅ 已切換模型為：<code>{html.escape(new_model)}</code>", parse_mode=ParseMode.HTML)
        return

    config = get_config()
    models = list(config.allowed_models)
    if not models:
        # Fallback list when AGY_ALLOWED_MODELS is unset. Keep this in sync
        # with `agy models` -- picking a name AGY doesn't actually offer will
        # fail at run_agy() time with "invalid model selection".
        models = [
            "gemini-3.7-flash-high",
            "gemini-3.6-flash-high",
            "gemini-3.1-pro-high",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
            "gpt-oss-120b-medium",
        ]

    keyboard = [
        [InlineKeyboardButton(f"🤖 {m}", callback_data=f"model_sel:{m}")]
        for m in models
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("請選擇欲使用的模型：", reply_markup=reply_markup)


async def effort_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"low", "medium", "high"}:
        await update.message.reply_text("用法：`/effort low|medium|high`")
        return
    val = context.args[0].lower()
    get_chat_state_store().update(_get_chat_id(update), effort=val)
    await update.message.reply_text(f"✅ 已設定推理深度 (effort) 為：`{val}`")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"plan", "accept-edits"}:
        await update.message.reply_text("用法：`/mode plan|accept-edits`")
        return
    val = context.args[0].lower()
    config = get_config()
    if val == "accept-edits" and config.permission_mode == "safe":
        await update.message.reply_text(
            "⚠️ 全域模式目前為 `Safe`，`accept-edits` 模式僅能在全域 `Full` 模式下生效。\n"
            "已為此 Chat 記錄設定，但 AGY 執行時仍會自動受限於 Plan 模式。"
        )
    get_chat_state_store().update(_get_chat_id(update), mode=val)
    await update.message.reply_text(f"✅ 已設定模式 (mode) 為：`{val}`")


async def sandbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"on", "off", "1", "0"}:
        await update.message.reply_text("用法：`/sandbox on|off`")
        return
    val = context.args[0].lower() in {"on", "1"}
    get_chat_state_store().update(_get_chat_id(update), sandbox=val)
    await update.message.reply_text(f"✅ 已設定沙箱模式 (sandbox) 為：`{'on' if val else 'off'}`")


async def verbose_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"detailed", "compact", "silent"}:
        await update.message.reply_text("用法：`/verbose detailed|compact|silent`")
        return
    val = context.args[0].lower()
    get_chat_state_store().update(_get_chat_id(update), verbose=val)
    await update.message.reply_text(f"✅ 已設定進度詳細度 (verbose) 為：`{val}`")


async def setdefault_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    settings = get_chat_state_store().get_or_create(chat_id)

    payload = {
        "model": settings.model,
        "effort": settings.effort,
        "mode": settings.mode,
        "sandbox": settings.sandbox,
        "verbose": settings.verbose,
    }
    token = PENDING_ACTIONS.put("setdefault", update.effective_user.id, payload, ttl_minutes=15)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 確認寫入 .env", callback_data=f"setdefault_confirm:{token}"),
        InlineKeyboardButton("❌ 取消", callback_data=f"setdefault_cancel:{token}"),
    ]])

    msg = (
        "⚠️ <b>確認寫入全域預設值？</b>\n\n"
        f"即將把此 Chat 的設定寫回 <code>.env</code>：\n"
        f"• Model: <code>{settings.model or '(不修改)'}</code>\n"
        f"• Effort: <code>{settings.effort}</code>\n"
        f"• Mode: <code>{settings.mode}</code>\n"
        f"• Sandbox: <code>{settings.sandbox}</code>\n"
        f"• Verbose: <code>{settings.verbose}</code>\n\n"
        "這將作為所有新對話與重啟後的預設值。"
    )
    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode=ParseMode.HTML)


# -----------------------------------------------------------------------------
# Handlers: PTY & Quota Commands (/usage, /quota, /credits, /context, /tokens, /learn, /compact)
# -----------------------------------------------------------------------------

async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    status_msg = await update.message.reply_text("⏳ 正在查詢 AGY 配額與使用量...")
    config = get_config()
    try:
        async with agy_lock:
            report = await run_pty_command(config.agy_bin, config.agy_workdir, "/quota", timeout_seconds=30)
        await status_msg.edit_text(report, parse_mode=ParseMode.HTML)
        queue_context_injection(_get_chat_id(update), report)
    except Exception as exc:
        logger.exception("查詢配額失敗")
        await status_msg.edit_text(f"❌ 查詢失敗：{redact_sensitive(str(exc))}")


async def context_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    status_msg = await update.message.reply_text("⏳ 正在查詢上下文資訊...")
    config = get_config()
    try:
        async with agy_lock:
            report = await run_pty_command(config.agy_bin, config.agy_workdir, "/context", timeout_seconds=30)
        await status_msg.edit_text(report)
        queue_context_injection(_get_chat_id(update), report)
    except Exception as exc:
        logger.exception("查詢上下文失敗")
        await status_msg.edit_text(f"❌ 查詢失敗：{redact_sensitive(str(exc))}")


async def tokens_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    # Read usage metrics
    await usage_command(update, context)


async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    payload = " ".join(context.args) if context.args else ""
    prompt = f"請將以下對話內容與技巧整理為可重複使用的規則與 Skill：\n\n{payload}" if payload else "請將本對話的經驗與技巧整理為可重複使用的規則與 Skill。"
    await _enqueue_and_handle_prompt(update, context, prompt)


async def compact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    prompt = "請壓縮目前對話的上下文，保留核心決策、狀態與待辦事項。"
    await _enqueue_and_handle_prompt(update, context, prompt)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    cancelled = JOB_QUEUE.cancel_for_chat(chat_id)
    if cancelled:
        await update.message.reply_text("🛑 已取消此 Chat 進行中與佇列中的任務。")
    else:
        await update.message.reply_text("ℹ️ 目前沒有正在執行或等待中的任務。")


# -----------------------------------------------------------------------------
# Handlers: CLI Passthrough & Read-only tools
# -----------------------------------------------------------------------------

async def agy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args:
        await update.message.reply_text("用法：`/agy [ARGS...]`，例如 `/agy models` 或 `/agy -p '檢查日誌'`")
        return

    raw_args = context.args
    is_valid, err = validate_custom_args(raw_args)
    if not is_valid:
        await update.message.reply_text(err)
        return

    chat_id = _get_chat_id(update)
    chat_state = get_chat_state_store().get_or_create(chat_id)
    final_args = prepare_custom_args(raw_args, enforce_sandbox=chat_state.sandbox)

    if is_dangerous_custom_command(final_args):
        token = PENDING_ACTIONS.put("agy_confirm", update.effective_user.id, final_args, ttl_minutes=15)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ 確認執行危險指令", callback_data=f"agy_confirm:{token}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"agy_cancel:{token}"),
        ]])
        cmd_str = " ".join(final_args)
        await update.message.reply_text(
            f"⚠️ **即將執行具潛在風險的 AGY 指令：**\n\n```\nagy {cmd_str}\n```\n\n"
            f"請點擊按鈕確認或輸入 `/agy-confirm {token}`：",
            reply_markup=keyboard,
        )
        return

    # Safe execution
    config = get_config()
    status_msg = await update.message.reply_text("⏳ 正在執行 agy 指令...")
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    try:
        async with agy_lock:
            res = await run_process(
                [str(config.agy_bin)] + final_args,
                cwd=config.agy_workdir,
                env=env,
                timeout_seconds=config.timeout_seconds,
                max_output_bytes=config.max_output_bytes,
            )
        await status_msg.delete()
        await send_formatted_response(update.message, result_message(res))
    except Exception as exc:
        logger.exception("執行 agy 指令異常")
        await status_msg.edit_text(f"❌ 執行異常：{redact_sensitive(str(exc))}")


async def agy_confirm_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args:
        await update.message.reply_text("用法：`/agy-confirm <TOKEN>`")
        return
    token = context.args[0].strip()
    action = PENDING_ACTIONS.pop(token, user_id=update.effective_user.id)
    if not action or action.kind != "agy_confirm":
        await update.message.reply_text("❌ 確認 Token 無效或已過期。")
        return

    args = action.payload
    config = get_config()
    status_msg = await update.message.reply_text(f"⏳ 正在執行核准後的指令：`agy {' '.join(args)}`...")
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    try:
        async with agy_lock:
            res = await run_process(
                [str(config.agy_bin)] + args,
                cwd=config.agy_workdir,
                env=env,
                timeout_seconds=config.timeout_seconds,
                max_output_bytes=config.max_output_bytes,
            )
        await status_msg.delete()
        await send_formatted_response(update.message, result_message(res))
    except Exception as exc:
        logger.exception("執行核准後的 agy 指令異常")
        await status_msg.edit_text(f"❌ 執行異常：{redact_sensitive(str(exc))}")


async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "agents"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, result_message(res))


async def changelog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "changelog"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, result_message(res))


async def plugins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "plugins"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, result_message(res))


async def cli_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "--help"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, f"```\n{res.stdout}\n```")


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "--version"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, f"📌 **AGY CLI Version**:\n`{res.stdout or res.stderr}`")


async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = get_chat_state_store().get_or_create(chat_id).agent or "(預設)"
        await update.message.reply_text(f"目前 Agent: `{curr}`\n切換用法：`/agent <AGENT_NAME>`")
        return
    val = context.args[0].strip()
    get_chat_state_store().update(chat_id, agent=val)
    await update.message.reply_text(f"✅ 已設定 Agent 為：`{val}`")


async def project_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = get_chat_state_store().get_or_create(chat_id).project or "(無)"
        await update.message.reply_text(f"目前 Project: `{curr}`\n設定用法：`/project <PROJECT_ID>`")
        return
    val = context.args[0].strip()
    get_chat_state_store().update(chat_id, project=val)
    await update.message.reply_text(f"✅ 已設定專案 (project) 為：`{val}`")


async def add_dir_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = get_chat_state_store().get_or_create(chat_id).add_dirs
        await update.message.reply_text(f"目前額外目錄 (add_dirs): `{list(curr)}`\n新增用法：`/add-dir <目錄路徑>`")
        return
    raw_path = " ".join(context.args).strip()
    config = get_config()
    try:
        resolved = safe_join(config.workspace_root, raw_path)
        if not resolved.is_dir():
            await update.message.reply_text(f"❌ 目錄不存在：`{resolved}`")
            return
        state = get_chat_state_store().get_or_create(chat_id)
        dirs = list(state.add_dirs)
        if str(resolved) not in dirs:
            dirs.append(str(resolved))
            get_chat_state_store().update(chat_id, add_dirs=dirs)
        await update.message.reply_text(f"✅ 已將目錄加入工作空間：`{resolved}`")
    except Exception as exc:
        await update.message.reply_text(f"❌ 目錄路徑無效：{exc}")


# -----------------------------------------------------------------------------
# Handlers: Attachments & Media
# -----------------------------------------------------------------------------

async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    chat_id = _get_chat_id(update)
    message = update.message
    if not message:
        return

    file_obj = None
    orig_filename = ""
    caption = message.caption or ""

    if message.document:
        doc = message.document
        file_obj = await doc.get_file()
        orig_filename = doc.file_name or f"doc_{doc.file_unique_id}"
    elif message.photo:
        photo = message.photo[-1]
        file_obj = await photo.get_file()
        orig_filename = f"photo_{photo.file_unique_id}.jpg"

    if not file_obj:
        return

    ext = Path(orig_filename).suffix.lower()
    if ext not in SAFE_EXTENSIONS:
        await message.reply_text(
            f"❌ 不支援此副檔名：`{ext}`\n支援格式：`{', '.join(sorted(SAFE_EXTENSIONS))}`"
        )
        return

    # Sanitize filename
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(orig_filename).stem) + ext
    uploads_dir = safe_join(config.workspace_root, "uploads", str(chat_id))
    uploads_dir.mkdir(parents=True, exist_ok=True)
    target_path = uploads_dir / f"{file_obj.file_unique_id[:8]}_{safe_name}"

    try:
        await file_obj.download_to_drive(custom_path=target_path)
        prompt = f"使用者上傳了附件：`{target_path}`\n\n說明：" + (caption if caption else "請分析此附件並提供摘要。")
        await message.reply_text(f"📎 已儲存附件：`{safe_name}`，正在交由 AGY 分析...")
        await _enqueue_and_handle_prompt(update, context, prompt)
    except Exception as exc:
        logger.exception("下載或處理附件失敗")
        await message.reply_text(f"❌ 處理附件失敗：{redact_sensitive(str(exc))}")


# -----------------------------------------------------------------------------
# Handlers: Message Routing & Job Queue Execution
# -----------------------------------------------------------------------------

async def _enqueue_and_handle_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str
) -> None:
    config = get_config()
    chat_id = _get_chat_id(update)
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", 0) if user else 0

    status_msg = None
    if getattr(update, "message", None) and hasattr(update.message, "reply_text"):
        try:
            status_msg = await update.message.reply_text(config.waiting_message)
        except Exception:
            pass

    try:
        job, was_merged = JOB_QUEUE.enqueue(
            chat_id=chat_id,
            user_id=user_id,
            prompt=prompt,
            auto_interrupt=config.auto_interrupt,
        )
    except RuntimeError as exc:
        if getattr(update, "message", None) and hasattr(update.message, "reply_text"):
            await update.message.reply_text(f"❌ {exc}")
        return

    # Attach this call's status message to the job itself so whichever worker
    # callback ends up processing it (see post_init's job_handler) can find and
    # reuse it, instead of sending a second, orphaned "thinking..." message.
    job.status_msg = status_msg

    if was_merged and status_msg:
        with suppress(Exception):
            await status_msg.edit_text("🔄 已合併前次任務與新追加的指示，重新執行中...")

    app = getattr(context, "application", None) or context
    # If worker is not active, start or process
    if JOB_QUEUE._worker_task is None or JOB_QUEUE._worker_task.done():
        JOB_QUEUE.start(lambda j: _execute_chat_job(app, j, status_msg=j.status_msg))

    # Wait for job completion
    await job.done_event.wait()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not getattr(update, "message", None) or not getattr(update.message, "text", None):
        return

    text = update.message.text
    detected = detect_schedule_intent(text)
    if detected:
        raw_cron, task_text = detected
        await update.message.reply_text(
            "⏰ 這看起來是「在某個時間點或週期執行」的請求，已直接為你走排程建立流程"
            "（一般對話不會處理這類任務，避免 AGY 自己等到那個時間才回覆、白白佔用任務佇列）。"
            "若你原本只是聊天、不是真的要排程，取消下方確認即可。"
        )
        await _run_schedule_add_flow(update, context, raw_cron, task_text)
        return

    await _enqueue_and_handle_prompt(update, context, text)


async def _execute_chat_job(application, job: Job, status_msg=None) -> None:
    config = get_config()
    chat_id = job.chat_id
    store = get_chat_state_store()

    # Track in-flight
    store.set_in_flight(chat_id, job.prompt)

    if status_msg is None and getattr(application, "bot", None) and hasattr(application.bot, "send_message"):
        try:
            status_msg = await application.bot.send_message(
                chat_id=chat_id,
                text=config.waiting_message,
            )
        except Exception:
            pass

    chat_state = get_chat_state_store().get_or_create(
        chat_id,
        defaults={
            "model": config.default_model,
            "effort": config.default_effort,
            "mode": config.default_mode,
            "sandbox": config.default_sandbox,
            "verbose": config.default_verbose,
        },
    )
    last_edit_time = 0.0
    accumulated_draft = ""

    async def on_chunk_cb(draft_text: str) -> None:
        nonlocal last_edit_time, accumulated_draft
        accumulated_draft = draft_text
        if chat_state.verbose == "silent" or status_msg is None:
            return
        now_ts = asyncio.get_event_loop().time()
        if now_ts - last_edit_time > 1.8:
            last_edit_time = now_ts
            if chat_state.verbose == "compact":
                lines = [l.strip() for l in draft_text.splitlines() if l.strip()]
                last_line = lines[-1] if lines else ""
                snippet = last_line[:200]
                if snippet:
                    with suppress(Exception):
                        await status_msg.edit_text(f"⏳ <b>正在執行：</b> <code>{html.escape(snippet)}</code>", parse_mode=ParseMode.HTML)
            else:
                snippet = draft_text[-800:].strip()
                if snippet:
                    with suppress(Exception):
                        await status_msg.edit_text(f"⏳ <b>正在思考與執行：</b>\n\n<code>{html.escape(snippet)}</code>", parse_mode=ParseMode.HTML)

    async def keep_typing() -> None:
        while True:
            with suppress(Exception):
                await application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    job_start_time = asyncio.get_event_loop().time()
    try:
        async with agy_lock:
            result = await run_agy(
                job.prompt,
                chat_id=chat_id,
                continue_conversation=True,
                on_chunk=on_chunk_cb,
            )

        job_duration = asyncio.get_event_loop().time() - job_start_time
        if status_msg is not None:
            if config.progress_mode == "delete":
                with suppress(Exception):
                    await status_msg.delete()
            elif config.progress_mode == "compact":
                if hasattr(status_msg, "edit_text"):
                    with suppress(Exception):
                        await status_msg.edit_text("✅ 執行完成。")
            elif config.progress_mode == "full":
                if hasattr(status_msg, "edit_text"):
                    lines_count = len(accumulated_draft.splitlines())
                    with suppress(Exception):
                        await status_msg.edit_text(
                            f"✅ <b>執行完成</b>（耗時 {job_duration:.1f}s，處理 {lines_count} 行日誌）\n\n"
                            f"<code>{html.escape(accumulated_draft[-600:].strip() or '無進度日誌')}</code>",
                            parse_mode=ParseMode.HTML,
                        )

        formatted_res = result_message(result)
        bot_inst = getattr(application, "bot", None)
        if bot_inst and hasattr(bot_inst, "send_message"):
            await send_formatted_to_chat(bot_inst, chat_id, formatted_res)
        elif getattr(application, "message", None) and hasattr(application.message, "reply_text"):
            await send_formatted_response(application.message, formatted_res)

        # Detect output media / files to send
        if result.returncode == 0 and result.stdout:
            allowed_dirs = [config.workspace_root, Path("/tmp"), Path("/var/tmp")]
            media_files, media_urls = detect_output_media(result.stdout, allowed_dirs)
            for mpath in media_files:
                try:
                    ext = mpath.suffix.lower()
                    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                        with open(mpath, "rb") as f:
                            await application.bot.send_photo(chat_id=chat_id, photo=f, caption=f"📸 產生檔案：`{mpath.name}`")
                    else:
                        with open(mpath, "rb") as f:
                            await application.bot.send_document(chat_id=chat_id, document=f, caption=f"📄 產生檔案：`{mpath.name}`")
                except Exception as m_exc:
                    logger.warning("傳送輸出媒體失敗：%s (%s)", mpath, m_exc)

            for murl in media_urls:
                try:
                    img_bytes = await fetch_ssrf_safe_media(murl)
                    if img_bytes:
                        await application.bot.send_photo(chat_id=chat_id, photo=io.BytesIO(img_bytes), caption=f"🌐 網路圖片：`{murl}`")
                except Exception as u_exc:
                    logger.warning("傳送輸出 URL 圖片失敗：%s (%s)", murl, u_exc)

    except Exception as exc:
        logger.exception("處理任務異常 (chat_id=%s)", chat_id)
        if status_msg:
            with suppress(Exception):
                await status_msg.edit_text(f"❌ 執行異常：{redact_sensitive(str(exc))}")
    finally:
        store.set_in_flight(chat_id, None)
        typing_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await typing_task


# -----------------------------------------------------------------------------
# Callback Query Router (Schedules, Models, Resume, SetDefault, AGY)
# -----------------------------------------------------------------------------

async def global_callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user is None:
        return

    user_id = query.from_user.id
    if not is_authorized(user_id):
        await query.answer("⛔ 您沒有權限操作此項目。", show_alert=True)
        return

    data = query.data or ""
    await query.answer()

    # Model selector: model_sel:<name>
    if data.startswith("model_sel:"):
        model_name = data.split(":", 1)[1]
        get_chat_state_store().update(_get_chat_id(update), model=model_name)
        await query.edit_message_text(f"✅ 已切換模型為：`{model_name}`")
        return

    # SetDefault confirm / cancel
    if data.startswith("setdefault_confirm:"):
        token = data.split(":", 1)[1]
        action = PENDING_ACTIONS.pop(token, user_id=user_id)
        if not action or action.kind != "setdefault":
            await query.edit_message_text("❌ 確認 Token 無效或已過期。")
            return
        payload = action.payload
        _write_defaults_to_env(payload)
        await query.edit_message_text("✅ 已成功將設定寫回 `.env` 全域預設值！")
        return

    if data.startswith("setdefault_cancel:"):
        token = data.split(":", 1)[1]
        PENDING_ACTIONS.pop(token, user_id=user_id)
        await query.edit_message_text("已取消設定寫入。")
        return

    # AGY Passthrough confirm / cancel
    if data.startswith("agy_confirm:"):
        token = data.split(":", 1)[1]
        action = PENDING_ACTIONS.pop(token, user_id=user_id)
        if not action or action.kind != "agy_confirm":
            await query.edit_message_text("❌ 確認 Token 無效或已過期。")
            return
        args = action.payload
        config = get_config()
        await query.edit_message_text(f"⏳ 正在執行核准後的指令：`agy {' '.join(args)}`...")
        env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
        try:
            async with agy_lock:
                res = await run_process(
                    [str(config.agy_bin)] + args,
                    cwd=config.agy_workdir,
                    env=env,
                    timeout_seconds=config.timeout_seconds,
                    max_output_bytes=config.max_output_bytes,
                )
            await send_formatted_response(query.message, result_message(res))
        except Exception as exc:
            logger.exception("執行核准後的 agy 指令異常")
            await query.edit_message_text(f"❌ 執行異常：{redact_sensitive(str(exc))}")
        return

    if data.startswith("agy_cancel:"):
        token = data.split(":", 1)[1]
        PENDING_ACTIONS.pop(token, user_id=user_id)
        await query.edit_message_text("已取消指令執行。")
        return

    # Schedule callbacks
    if data.startswith("schedule_confirm:") or data.startswith("schedule_cancel:"):
        action_name, token = data.split(":", 1)
        action = PENDING_ACTIONS.pop(token, user_id=user_id)
        if not action or action.kind != "schedule_add":
            await query.edit_message_text("⌛ 此排程預覽已失效，請重新建立。")
            return
        if action_name == "schedule_cancel":
            await query.edit_message_text("已取消建立排程。")
            return

        config = get_config()
        store = get_schedule_store()
        if store.count() >= config.schedule_max_tasks:
            await query.edit_message_text(f"❌ 排程數量已達上限（{config.schedule_max_tasks}）。")
            return

        pending = action.payload
        schedule = store.add(
            cron_expr=pending["cron_expr"],
            timezone_name=config.schedule_timezone,
            original_prompt=pending["original_prompt"],
            prompt_template=pending["prompt_template"],
        )
        await query.edit_message_text(
            f"✅ 已建立排程 #{schedule.id}\n"
            f"cron：{schedule.cron_expr}\n"
            f"下次執行：{_local_time(schedule.next_run_at, schedule.timezone)}"
        )
        return


def _write_defaults_to_env(payload: dict[str, Any]) -> None:
    env_file = ENV_PATH
    content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    lines = content.splitlines()

    mapping = {
        "model": "AGY_MODEL",
        "effort": "AGY_EFFORT",
        "mode": "AGY_MODE",
        "sandbox": "AGY_SANDBOX",
        "verbose": "AGY_VERBOSE",
    }

    updates = {}
    for k, v in payload.items():
        if k in mapping and v is not None:
            updates[mapping[k]] = "1" if isinstance(v, bool) and v else ("0" if isinstance(v, bool) else str(v))

    new_lines = []
    found_keys = set()
    for line in lines:
        stripped = line.strip()
        matched_key = None
        for env_key in updates:
            if stripped.startswith(f"{env_key}=") or stripped.startswith(f"export {env_key}="):
                matched_key = env_key
                break
        if matched_key:
            new_lines.append(f"{matched_key}={updates[matched_key]}")
            found_keys.add(matched_key)
        else:
            new_lines.append(line)

    for env_key, val in updates.items():
        if env_key not in found_keys:
            new_lines.append(f"{env_key}={val}")

    new_content = "\n".join(new_lines) + "\n"

    # Atomic write via temp file
    temp_file = env_file.parent / f".env.tmp_{secrets.token_hex(4)}"
    temp_file.write_text(new_content, encoding="utf-8")
    os.replace(temp_file, env_file)
    with suppress(Exception):
        env_file.chmod(0o600)

    # Dynamically update in-memory CONFIG
    if state.CONFIG:
        if "model" in payload and payload["model"] is not None:
            object.__setattr__(state.CONFIG, "default_model", payload["model"])
        if "effort" in payload and payload["effort"] is not None:
            object.__setattr__(state.CONFIG, "default_effort", payload["effort"])
        if "mode" in payload and payload["mode"] is not None:
            object.__setattr__(state.CONFIG, "default_mode", payload["mode"])
        if "sandbox" in payload and payload["sandbox"] is not None:
            object.__setattr__(state.CONFIG, "default_sandbox", bool(payload["sandbox"]))
        if "verbose" in payload and payload["verbose"] is not None:
            object.__setattr__(state.CONFIG, "default_verbose", payload["verbose"])



# -----------------------------------------------------------------------------
# Schedules (Preserved)
# -----------------------------------------------------------------------------

def _local_time(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "—"
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S %Z")


def _schedule_status(schedule: Schedule) -> str:
    if schedule.enabled:
        return "啟用"
    if schedule.consecutive_failures >= 3:
        return "已自動暫停"
    return "暫停"


def _command_payload(update: Update) -> str:
    if not update.message or not update.message.text:
        return ""
    parts = update.message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else ""


def _schedule_id(context: ContextTypes.DEFAULT_TYPE) -> int:
    if len(context.args) != 1:
        raise ScheduleError("請提供一個排程 ID")
    try:
        schedule_id = int(context.args[0])
    except ValueError as exc:
        raise ScheduleError("排程 ID 必須是正整數") from exc
    if schedule_id < 1:
        raise ScheduleError("排程 ID 必須是正整數")
    return schedule_id


async def schedule_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    message = (
        "⏰ **AGY 定時任務**\n\n"
        "新增格式：\n"
        "```\n/schedule_add 分 時 日 月 週 任務內容\n```\n"
        "例如每小時：\n"
        "```\n/schedule_add 0 * * * * 查詢台北天氣並簡短回報\n```\n"
        "例如每天 09:00：\n"
        "```\n/schedule_add 0 9 * * * 檢查 VM 狀態並摘要異常\n```\n\n"
        f"時區：`{config.schedule_timezone}`\n"
        f"最短間隔：{config.schedule_min_interval_minutes} 分鐘\n"
        f"最多排程：{config.schedule_max_tasks}\n\n"
        "管理指令：\n"
        "`/schedule_list`\n"
        "`/schedule_show ID`\n"
        "`/schedule_pause ID`\n"
        "`/schedule_resume ID`\n"
        "`/schedule_delete ID`\n\n"
        "可用變數：`{{now}}`、`{{date}}`、`{{time}}`、`{{timezone}}`、"
        "`{{scheduled_at}}`、`{{run_number}}`。\n\n"
        "💡 **重要觀念**：\n"
        "• 管理排程（查/停/啟/刪）請務必使用 `/schedule_*` 指令。\n"
        "• 排程執行的「任務內容」則完全支援自然語言描述。"
    )
    await send_formatted_response(update.message, message)


async def _run_schedule_add_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE, raw_cron: str, original_prompt: str
) -> None:
    """建立排程的共用核心流程：驗證 cron、請 AGY 整理成可重複執行的 prompt、彈出二次確認按鈕。

    供 `/schedule_add`（從指令參數解析 cron/task）與 `handle_message` 的自然語言排程
    偵測（`detect_schedule_intent`，已解析好 cron/task）共用，兩者都會走到同一套
    人工確認機制，不會略過確認直接建立排程。
    """
    if await reject_unauthorized(update):
        return
    config = get_config()
    store = get_schedule_store()
    if store.count() >= config.schedule_max_tasks:
        await update.message.reply_text(f"❌ 排程數量已達上限（{config.schedule_max_tasks}）。")
        return

    if PENDING_ACTIONS.count(kind="schedule_add", user_id=update.effective_user.id) >= 5:
        await update.message.reply_text("❌ 尚有太多未確認的排程預覽，請先確認或取消。")
        return

    try:
        cron_expr = normalize_cron(
            raw_cron,
            config.schedule_timezone,
            config.schedule_min_interval_minutes,
        )
    except ScheduleError as exc:
        await update.message.reply_text(f"❌ {exc}\n\n使用 /schedule_help 查看範例。")
        return

    status_message = await update.message.reply_text("🧭 AGY 正在整理可重複執行的任務 prompt...")
    expansion_prompt = build_prompt_expansion_request(
        original_prompt, cron_expr, config.schedule_timezone
    )
    builder_workdir = config.schedule_db_path.parent / "workspaces" / "prompt-builder"
    builder_workdir.mkdir(parents=True, exist_ok=True)
    try:
        async with agy_lock:
            result = await run_agy(
                expansion_prompt,
                continue_conversation=False,
                workdir=builder_workdir,
                allow_full_permissions=False,
            )
    except Exception as exc:
        logger.exception("建立排程 prompt 時 AGY 執行異常")
        await status_message.edit_text(f"❌ AGY 執行異常：{redact_sensitive(str(exc))}")
        return

    if result.returncode != 0 or result.timed_out or not result.stdout.strip():
        await status_message.edit_text(result_message(result))
        return
    prompt_template = result.stdout.strip()
    if len(prompt_template) > 8_000:
        await status_message.edit_text("❌ AGY 整理後的 prompt 超過 8000 個字元，請縮短原始要求。")
        return

    payload = {
        "cron_expr": cron_expr,
        "original_prompt": original_prompt,
        "prompt_template": prompt_template,
    }
    pending_token = PENDING_ACTIONS.put("schedule_add", update.effective_user.id, payload, ttl_minutes=15)

    preview = prompt_template
    if len(preview) > 2_500:
        preview = preview[:2_500] + "\n…（完整內容會保存於排程）"
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ 確認建立", callback_data=f"schedule_confirm:{pending_token}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"schedule_cancel:{pending_token}"),
        ]]
    )
    await status_message.edit_text(
        "請確認 AGY 整理後的定時任務：\n\n"
        f"cron：{cron_expr}\n"
        f"時區：{config.schedule_timezone}\n\n"
        f"{preview}",
        reply_markup=keyboard,
    )


async def schedule_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    try:
        raw_cron, original_prompt = parse_schedule_add_payload(_command_payload(update))
    except ScheduleError as exc:
        await update.message.reply_text(f"❌ {exc}\n\n使用 /schedule_help 查看範例。")
        return
    await _run_schedule_add_flow(update, context, raw_cron, original_prompt)


async def schedule_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    schedules = get_schedule_store().list_all()
    if not schedules:
        await update.message.reply_text("目前沒有定時任務。使用 /schedule_help 查看新增方式。")
        return
    lines = ["⏰ **目前的 AGY 定時任務**"]
    for schedule in schedules:
        summary = " ".join(schedule.original_prompt.split())
        if len(summary) > 80:
            summary = summary[:80] + "…"
        lines.append(
            f"**#{schedule.id}**｜{_schedule_status(schedule)}｜`{schedule.cron_expr}`\n"
            f"下次：{_local_time(schedule.next_run_at, schedule.timezone)}\n{summary}"
        )
    await send_formatted_response(update.message, "\n\n".join(lines))


async def schedule_show_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    try:
        schedule_id = _schedule_id(context)
    except ScheduleError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    schedule = get_schedule_store().get(schedule_id)
    if schedule is None:
        await update.message.reply_text("❌ 找不到此排程。")
        return
    message = (
        f"⏰ **排程 #{schedule.id}**\n\n"
        f"狀態：{_schedule_status(schedule)}\n"
        f"cron：`{schedule.cron_expr}`\n"
        f"時區：`{schedule.timezone}`\n"
        f"下次：{_local_time(schedule.next_run_at, schedule.timezone)}\n"
        f"上次：{_local_time(schedule.last_run_at, schedule.timezone)}\n"
        f"執行次數：{schedule.run_count}\n"
        f"連續失敗：{schedule.consecutive_failures}\n"
        f"上次狀態：{schedule.last_status or '—'}\n"
    )
    if schedule.last_error:
        message += f"上次錯誤：{schedule.last_error}\n"
    message += (
        f"\n**原始要求**\n{schedule.original_prompt}\n\n"
        f"**AGY 任務 prompt**\n{schedule.prompt_template}"
    )
    await send_formatted_response(update.message, message)


async def _change_schedule_state(
    update: Update, context: ContextTypes.DEFAULT_TYPE, action: str
) -> None:
    if await reject_unauthorized(update):
        return
    try:
        schedule_id = _schedule_id(context)
    except ScheduleError as exc:
        await update.message.reply_text(f"❌ {exc}")
        return
    store = get_schedule_store()
    if action == "pause":
        changed = store.pause(schedule_id)
        text = "已暫停"
    elif action == "resume":
        changed = store.resume(schedule_id)
        text = "已恢復"
    else:
        changed = store.delete(schedule_id)
        text = "已刪除"
    if changed:
        await update.message.reply_text(f"✅ {text}排程 #{schedule_id}。")
    else:
        await update.message.reply_text("❌ 找不到此排程。")


async def schedule_pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_schedule_state(update, context, "pause")


async def schedule_resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_schedule_state(update, context, "resume")


async def schedule_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _change_schedule_state(update, context, "delete")


async def _execute_due_schedule(application, due: DueSchedule) -> None:
    config = get_config()
    schedule = due.schedule
    workspace = config.schedule_db_path.parent / "workspaces" / f"schedule-{schedule.id}"
    workspace.mkdir(parents=True, exist_ok=True)
    prompt = render_prompt_variables(
        schedule.prompt_template,
        timezone_name=schedule.timezone,
        scheduled_at=due.scheduled_at,
        run_number=schedule.run_count + 1,
    )
    success = False
    error: str | None = None
    auto_paused = False
    try:
        async with agy_lock:
            result = await run_agy(
                prompt,
                continue_conversation=False,
                workdir=workspace,
                add_primary_workdir=True,
            )
        permission_denied = (
            config.permission_mode == "safe"
            and is_headless_permission_denied(result.stderr)
        )
        success = result.returncode == 0 and not result.timed_out and not permission_denied
        admin_ids = config.allowed_user_ids or {config.allowed_user_id}
        if success and result.stdout.strip() == NO_REPORT_SENTINEL:
            logger.info("排程 #%s 本次不需通知", schedule.id)
        else:
            for admin_id in sorted(admin_ids):
                with suppress(Exception):
                    await send_formatted_to_chat(
                        application.bot,
                        admin_id,
                        f"⏰ **排程 #{schedule.id} 執行結果**\n\n{result_message(result)}",
                    )
        if not success:
            error = result.stderr or result.stdout or "AGY 執行失敗"
    except Exception as exc:
        success = False
        error = redact_sensitive(str(exc))
        logger.exception("排程 #%s 執行或通知失敗", schedule.id)
    finally:
        auto_paused = get_schedule_store().record_result(
            schedule.id,
            success=success,
            error=error,
        )
    if auto_paused:
        admin_ids = config.allowed_user_ids or {config.allowed_user_id}
        for admin_id in sorted(admin_ids):
            with suppress(Exception):
                await send_formatted_to_chat(
                    application.bot,
                    admin_id,
                    f"⚠️ **排程 #{schedule.id} 已自動暫停**\n\n連續失敗 3 次，請使用 "
                    f"`/schedule_show {schedule.id}` 查看，再以 `/schedule_resume {schedule.id}` 恢復。",
                )


async def schedule_loop(application) -> None:
    logger.info("AGY 排程器已啟動")
    last_cleanup_date = None
    while True:
        try:
            # 1. Execute due schedules
            for due in get_schedule_store().claim_due():
                await _execute_due_schedule(application, due)

            # 2. Daily routine cleanup for expired files (> 30 days)
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today_str != last_cleanup_date:
                config = get_config()
                count = cleanup_expired_workspaces_and_uploads(
                    config.workspace_root,
                    config.state_db_path,
                    schedule_db_path=config.schedule_db_path,
                    max_age_days=30,
                )
                last_cleanup_date = today_str
                if count > 0:
                    logger.info("每日例行清理完成：共刪除 %s 個過期暫存檔案", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("排程器輪詢失敗")
        await asyncio.sleep(20)


# -----------------------------------------------------------------------------
# Bot Lifecycle: Post Init / Shutdown & In-flight Recovery
# -----------------------------------------------------------------------------

def cleanup_expired_workspaces_and_uploads(
    workspace_root: Path,
    state_dir: Path,
    schedule_db_path: Path | None = None,
    max_age_days: int = 30,
) -> int:
    now_ts = datetime.now(timezone.utc).timestamp()
    max_age_seconds = max_age_days * 86400

    target_dirs = [
        workspace_root / "uploads",
        workspace_root / "workspaces",
        state_dir.parent / "workspaces",
    ]
    if schedule_db_path is not None:
        target_dirs.append(schedule_db_path.parent / "workspaces")

    deleted_count = 0
    seen_dirs = set()
    for root_dir in target_dirs:
        try:
            resolved = root_dir.expanduser().resolve()
            if resolved in seen_dirs or not resolved.is_dir():
                continue
            seen_dirs.add(resolved)
            for item in resolved.rglob("*"):
                if item.is_file():
                    try:
                        if now_ts - item.stat().st_mtime > max_age_seconds:
                            item.unlink(missing_ok=True)
                            deleted_count += 1
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("清理過期檔案時略過：%s (%s)", root_dir, exc)
    return deleted_count


async def post_init(application) -> None:
    # 1. Start schedule task
    application.bot_data["schedule_task"] = asyncio.create_task(
        schedule_loop(application), name="agy-schedule-loop"
    )

    # 2. Start Job Queue worker
    async def job_handler(job: Job) -> None:
        await _execute_chat_job(application, job, status_msg=job.status_msg)

    JOB_QUEUE.start(job_handler)

    # 3. Crash / Restart in-flight jobs recovery
    store = get_chat_state_store()
    in_flight = store.get_all_in_flight()
    if in_flight:
        logger.warning("偵測到 %s 個未完成的任務，正在自動恢復執行...", len(in_flight))
        for chat_id, prompt in in_flight:
            with suppress(Exception):
                await application.bot.send_message(
                    chat_id=chat_id,
                    text="🔄 <b>任務自動恢復通知</b>\n先前未完成的請求已自動重新排入佇列執行中...",
                    parse_mode=ParseMode.HTML,
                )
            JOB_QUEUE.enqueue(chat_id=chat_id, user_id=0, prompt=prompt, auto_interrupt=False)
        store.clear_all_in_flight()

    # 4. Clean up expired files (> 30 days) across uploads and workspaces
    config = get_config()
    cleanup_expired_workspaces_and_uploads(config.workspace_root, config.state_db_path, schedule_db_path=config.schedule_db_path, max_age_days=30)


async def post_shutdown(application) -> None:
    task = application.bot_data.get("schedule_task")
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await JOB_QUEUE.stop()
    if INSTANCE_LOCK:
        INSTANCE_LOCK.release()



async def output_format_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args or context.args[0].lower() not in {"text", "json", "stream-json"}:
        curr = get_chat_state_store().get_or_create(chat_id).output_format
        await update.message.reply_text(f"目前輸出格式 (output_format): `{curr}`\n設定用法：`/output-format text|json|stream-json`")
        return
    fmt = context.args[0].lower()
    get_chat_state_store().update(chat_id, output_format=fmt)
    await update.message.reply_text(f"✅ 已設定輸出格式 (output_format) 為：`{fmt}`")


async def json_schema_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = get_chat_state_store().get_or_create(chat_id).json_schema or "(無)"
        await update.message.reply_text(f"目前 JSON Schema: `{curr}`\n設定用法：`/json-schema <SCHEMA>` (傳入 `clear` 清除)")
        return
    schema_val = " ".join(context.args).strip()
    if schema_val.lower() == "clear":
        get_chat_state_store().update(chat_id, json_schema=None)
        await update.message.reply_text("✅ 已清除 JSON Schema 設定。")
    else:
        get_chat_state_store().update(chat_id, json_schema=schema_val)
        await update.message.reply_text(f"✅ 已設定 JSON Schema 為：`{schema_val}`")


async def log_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = get_chat_state_store().get_or_create(chat_id).log_file or "(無)"
        await update.message.reply_text(f"目前日誌檔案 (log_file): `{curr}`\n設定用法：`/log-file <PATH>` (傳入 `clear` 清除)")
        return
    log_val = context.args[0].strip()
    if log_val.lower() == "clear":
        get_chat_state_store().update(chat_id, log_file=None)
        await update.message.reply_text("✅ 已清除日誌檔案設定。")
    else:
        get_chat_state_store().update(chat_id, log_file=log_val)
        await update.message.reply_text(f"✅ 已設定日誌檔案 (log_file) 為：`{log_val}`")


async def print_timeout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = get_chat_state_store().get_or_create(chat_id).print_timeout or "(使用全域預設)"
        await update.message.reply_text(f"目前執行超時 (print_timeout): `{curr}`\n設定用法：`/print-timeout <DURATION>` (例如 `5m`、`600s`，傳入 `clear` 清除)")
        return
    to_val = context.args[0].strip()
    if to_val.lower() == "clear":
        get_chat_state_store().update(chat_id, print_timeout=None)
        await update.message.reply_text("✅ 已清除自訂超時，回復全域預設。")
    else:
        get_chat_state_store().update(chat_id, print_timeout=to_val)
        await update.message.reply_text(f"✅ 已設定超時 (print_timeout) 為：`{to_val}`")


async def new_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args or context.args[0].lower() not in {"on", "off", "1", "0"}:
        curr = "on" if get_chat_state_store().get_or_create(chat_id).new_project else "off"
        await update.message.reply_text(f"目前新專案旗標 (new_project): `{curr}`\n設定用法：`/new-project on|off`")
        return
    val = context.args[0].lower() in {"on", "1"}
    get_chat_state_store().update(chat_id, new_project=val)
    await update.message.reply_text(f"✅ 已設定新專案旗標 (new_project) 為：`{'on' if val else 'off'}`")


async def disable_slash_commands_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args or context.args[0].lower() not in {"on", "off", "1", "0"}:
        curr = "on" if get_chat_state_store().get_or_create(chat_id).disable_slash_commands else "off"
        await update.message.reply_text(f"目前停用斜線指令旗標 (disable_slash_commands): `{curr}`\n設定用法：`/disable-slash-commands on|off`")
        return
    val = context.args[0].lower() in {"on", "1"}
    get_chat_state_store().update(chat_id, disable_slash_commands=val)
    await update.message.reply_text(f"✅ 已設定停用斜線指令旗標 (disable_slash_commands) 為：`{'on' if val else 'off'}`")


async def _perform_bot_restart(delay_seconds: float) -> None:
    """觸發服務重啟。

    優先嘗試 `systemctl restart`；但該指令通常需要 polkit 授權，一般（非
    root）服務使用者常會被拒絕（`Interactive authentication required`）。
    因此一律準備非零結束碼作為保底手段——systemd unit 設有
    `Restart=on-failure`，只有非零 exit code 才會被視為失敗並觸發自動重啟；
    `sys.exit(0)` 屬於「正常結束」，systemd 不會重啟，會導致服務整個停擺。
    """
    await asyncio.sleep(delay_seconds)
    if shutil.which("systemctl"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "restart", "agy-telegram.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                return
            logger.warning(
                "systemctl restart 未成功（exit %s，可能缺少 polkit 授權）：%s；改用結束碼觸發 Restart=on-failure",
                proc.returncode,
                stderr.decode("utf-8", errors="replace").strip(),
            )
        except Exception as exc:
            logger.warning("呼叫 systemctl restart 失敗：%s；改用結束碼觸發 Restart=on-failure", exc)
    sys.exit(1)


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    if not config.allow_bot_update:
        await update.message.reply_text("❌ 未啟用遠端重啟。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")
        return

    await update.message.reply_text("🔄 正在重新啟動 HostSpark 服務...")
    asyncio.create_task(_perform_bot_restart(1.0))


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    if not config.allow_bot_update:
        await update.message.reply_text("❌ 未啟用遠端更新。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")
        return

    status_msg = await update.message.reply_text("⬇️ 正在自 GitHub 拉取最新更新...")
    env = build_safe_subprocess_env()
    git_bin = shutil.which("git") or "git"
    result = await run_process(
        [git_bin, "pull", "origin", "main"],
        cwd=BASE_DIR,
        env=env,
        timeout_seconds=30,
        max_output_bytes=100_000,
    )

    if result.returncode != 0:
        await status_msg.edit_text(f"❌ 更新失敗（exit {result.returncode}）：\n\n{result.stderr or result.stdout}")
        return

    await status_msg.edit_text(f"✅ 更新成功：\n\n<pre>{html.escape(result.stdout)}</pre>\n\n🔄 正在自動重啟服務...", parse_mode=ParseMode.HTML)
    asyncio.create_task(_perform_bot_restart(1.5))


def build_application(config: BotConfig | None = None) -> Any:
    cfg = config or get_config()
    app = (
        ApplicationBuilder()
        .token(cfg.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Conversation management
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("continue", continue_command))
    app.add_handler(CommandHandler("session", session_command))

    # Configuration commands
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("models", model_command))
    app.add_handler(CommandHandler("effort", effort_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("sandbox", sandbox_command))
    app.add_handler(CommandHandler("verbose", verbose_command))
    app.add_handler(CommandHandler("setdefault", setdefault_command))

    # Context, quota, learn, compact
    app.add_handler(CommandHandler("usage", usage_command))
    app.add_handler(CommandHandler("quota", usage_command))
    app.add_handler(CommandHandler("credits", usage_command))
    app.add_handler(CommandHandler("context", context_command))
    app.add_handler(CommandHandler("tokens", tokens_command))
    app.add_handler(CommandHandler("learn", learn_command))
    app.add_handler(CommandHandler("compact", compact_command))

    # Passthrough & Read-only tools
    app.add_handler(CommandHandler("agy", agy_command))
    app.add_handler(CommandHandler("agy_confirm", agy_confirm_command))
    app.add_handler(CommandHandler("agents", agents_command))
    app.add_handler(CommandHandler("changelog", changelog_command))
    app.add_handler(CommandHandler("plugins", plugins_command))
    app.add_handler(CommandHandler("cli_help", cli_help_command))
    app.add_handler(CommandHandler("version", version_command))
    app.add_handler(CommandHandler("agent", agent_command))
    app.add_handler(CommandHandler("project", project_command))
    app.add_handler(CommandHandler("add_dir", add_dir_command))

    # Extended chat settings handlers (only valid underscore names accepted by Telegram Bot API)
    app.add_handler(CommandHandler("output_format", output_format_command))
    app.add_handler(CommandHandler("json_schema", json_schema_command))
    app.add_handler(CommandHandler("log_file", log_file_command))
    app.add_handler(CommandHandler("print_timeout", print_timeout_command))
    app.add_handler(CommandHandler("new_project", new_project_command))
    app.add_handler(CommandHandler("disable_slash_commands", disable_slash_commands_command))

    # System update and restart
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("update", update_command))

    # Schedules
    app.add_handler(CommandHandler("schedule_help", schedule_help_command))
    app.add_handler(CommandHandler("schedule_add", schedule_add_command))
    app.add_handler(CommandHandler("schedule_list", schedule_list_command))
    app.add_handler(CommandHandler("schedule_show", schedule_show_command))
    app.add_handler(CommandHandler("schedule_pause", schedule_pause_command))
    app.add_handler(CommandHandler("schedule_resume", schedule_resume_command))
    app.add_handler(CommandHandler("schedule_delete", schedule_delete_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(global_callback_query_handler))

    # Attachments & Messages
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_attachment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


def main() -> None:
    try:
        state.CONFIG = load_config()
    except ConfigError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if "--check-config" in sys.argv:
        print("設定驗證成功")
        return

    # Instance lock
    lock_path = state.CONFIG.state_db_path.parent / "bot.pid"
    state.INSTANCE_LOCK = InstanceLock(lock_path)
    try:
        state.INSTANCE_LOCK.acquire()
    except InstanceLockError as exc:
        print(f"啟動失敗：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    state.SCHEDULE_STORE = ScheduleStore(state.CONFIG.schedule_db_path)
    state.CHAT_STATE_STORE = ChatStateStore(state.CONFIG.state_db_path)

    if state.CONFIG.permission_mode == "full":
        logger.warning("AGY 目前使用 Full 模式：所有工具權限將自動核准")
    logger.info("載入設定：workdir=%s, mode=%s", state.CONFIG.agy_workdir, state.CONFIG.permission_mode)

    app = build_application(state.CONFIG)

    logger.info("Telegram AGY Bot 正在啟動長輪詢")
    app.run_polling()


if __name__ == "__main__":
    main()


schedule_callback = global_callback_query_handler


def __getattr__(name: str) -> Any:
    if hasattr(state, name):
        return getattr(state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

