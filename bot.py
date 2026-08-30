import asyncio
import logging
import os
import secrets
import shutil
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
    compose_agy_prompt,
    format_result_message,
    is_headless_permission_denied,
    load_config,
    md_to_telegram_html,
    redact_sensitive,
    run_process,
    split_markdown_into_chunks,
)
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


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = Path(os.getenv("AGY_ENV_FILE", str(BASE_DIR / ".env"))).expanduser()
load_dotenv(ENV_PATH)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CONFIG: BotConfig | None = None
SCHEDULE_STORE: ScheduleStore | None = None
agy_lock = asyncio.Lock()
UTC = timezone.utc


@dataclass(frozen=True)
class PendingSchedule:
    user_id: int
    cron_expr: str
    original_prompt: str
    prompt_template: str
    created_at: datetime


def get_config() -> BotConfig:
    if CONFIG is None:
        raise RuntimeError("Bot 尚未載入設定")
    return CONFIG


def get_schedule_store() -> ScheduleStore:
    if SCHEDULE_STORE is None:
        raise RuntimeError("排程資料庫尚未初始化")
    return SCHEDULE_STORE


def is_authorized(user_id: int) -> bool:
    return user_id == get_config().allowed_user_id


async def run_agy(
    user_text: str,
    *,
    continue_conversation: bool,
    workdir: Path | None = None,
    add_primary_workdir: bool = False,
    allow_full_permissions: bool = True,
) -> ProcessResult:
    config = get_config()
    prompt = compose_agy_prompt(user_text, config.rule_prompt)
    args = [str(config.agy_bin), "-p", prompt]
    if continue_conversation:
        args.append("--continue")
    if config.permission_mode == "full" and allow_full_permissions:
        args.append("--dangerously-skip-permissions")
    if add_primary_workdir and workdir != config.agy_workdir:
        args.extend(["--add-dir", str(config.agy_workdir)])
    args.extend(["--print-timeout", f"{config.timeout_seconds}s"])

    env = os.environ.copy()
    env["PATH"] = f"{config.agy_bin.parent}{os.pathsep}{env.get('PATH', '')}"
    return await run_process(
        args,
        cwd=workdir or config.agy_workdir,
        env=env,
        timeout_seconds=config.timeout_seconds + 10,
        max_output_bytes=config.max_output_bytes,
    )


def result_message(result: ProcessResult) -> str:
    return format_result_message(result, get_config().permission_mode)


async def send_formatted_response(message, text: str) -> None:
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


async def reject_unauthorized(update: Update) -> bool:
    user = update.effective_user
    if user and is_authorized(user.id):
        return False
    if user:
        logger.warning("未授權訪問：%s", user.id)
    if update.message:
        await update.message.reply_text("⛔ 您沒有權限使用此機器人。")
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    mode = get_config().permission_mode
    mode_text = "Full（不逐次審核）" if mode == "full" else "Safe（遵循 AGY 權限規則）"
    msg = (
        "🤖 <b>Antigravity CLI (agy) 助手在線中！</b>\n\n"
        f"• 執行模式：<b>{mode_text}</b>\n"
        "• <code>/status</code> - 查看 VM 健康狀況\n"
        "• <code>/clear</code> - 開啟全新工作階段\n\n"
        "• <code>/schedule_add</code> - 建立 AGY 定時任務\n"
        "• <code>/schedule_list</code> - 列出定時任務\n"
        "• <code>/schedule_help</code> - 查看排程說明\n\n"
        "請直接傳送您要執行的文字任務。"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def _status_section(title: str, args: list[str]) -> str:
    config = get_config()
    env = os.environ.copy()
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
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
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
    await send_formatted_response(update.message, "📊 **VM 即時健康狀態**\n\n" + "\n\n".join(sections))


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    status_message = await update.message.reply_text("🔄 正在建立新的對話工作階段...")
    try:
        async with agy_lock:
            result = await run_agy("已開啟新對話，請簡短確認。", continue_conversation=False)
        if result.returncode == 0 and not result.timed_out:
            await status_message.edit_text("✅ 已建立新的對話工作階段。")
        else:
            await status_message.edit_text(result_message(result))
    except Exception as exc:
        logger.exception("重置工作階段失敗")
        await status_message.edit_text(f"❌ 重置失敗：{redact_sensitive(str(exc))}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not update.message or not update.message.text:
        return

    config = get_config()
    try:
        status_message = await update.message.reply_text(
            config.waiting_message,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        status_message = await update.message.reply_text(
            config.waiting_message,
        )
    chat_id = update.effective_chat.id

    async def keep_typing() -> None:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        async with agy_lock:
            result = await run_agy(update.message.text, continue_conversation=True)
        with suppress(Exception):
            await status_message.delete()
        await send_formatted_response(update.message, result_message(result))
    except Exception as exc:
        logger.exception("AGY 執行異常")
        await status_message.edit_text(f"❌ 執行異常：{redact_sensitive(str(exc))}")
    finally:
        typing_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await typing_task


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


async def schedule_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = get_config()
    store = get_schedule_store()
    if store.count() >= config.schedule_max_tasks:
        await update.message.reply_text(f"❌ 排程數量已達上限（{config.schedule_max_tasks}）。")
        return
    pending_schedules = context.application.bot_data.setdefault("pending_schedules", {})
    now = datetime.now(UTC)
    expired_tokens = [
        token
        for token, pending in pending_schedules.items()
        if now - pending.created_at > timedelta(minutes=15)
    ]
    for token in expired_tokens:
        pending_schedules.pop(token, None)
    if len(pending_schedules) >= 5:
        await update.message.reply_text("❌ 尚有太多未確認的排程預覽，請先確認或取消。")
        return

    try:
        raw_cron, original_prompt = parse_schedule_add_payload(_command_payload(update))
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

    pending_token = secrets.token_urlsafe(8)
    pending_schedules[pending_token] = PendingSchedule(
        user_id=update.effective_user.id,
        cron_expr=cron_expr,
        original_prompt=original_prompt,
        prompt_template=prompt_template,
        created_at=datetime.now(UTC),
    )
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


async def schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if query.from_user.id != get_config().allowed_user_id:
        await query.answer("您沒有權限操作排程。", show_alert=True)
        return
    await query.answer()
    try:
        action, pending_token = query.data.split(":", 1)
    except (AttributeError, ValueError):
        await query.edit_message_text("❌ 無效的排程操作。")
        return
    pending_schedules = context.application.bot_data.setdefault("pending_schedules", {})
    pending = pending_schedules.pop(pending_token, None)
    if pending is None or datetime.now(UTC) - pending.created_at > timedelta(minutes=15):
        await query.edit_message_text("⌛ 此排程預覽已失效，請重新建立。")
        return
    if pending.user_id != query.from_user.id:
        await query.edit_message_text("❌ 此排程預覽不屬於目前使用者。")
        return
    if action == "schedule_cancel":
        await query.edit_message_text("已取消建立排程。")
        return
    if action != "schedule_confirm":
        await query.edit_message_text("❌ 無效的排程操作。")
        return

    config = get_config()
    store = get_schedule_store()
    if store.count() >= config.schedule_max_tasks:
        await query.edit_message_text(f"❌ 排程數量已達上限（{config.schedule_max_tasks}）。")
        return
    schedule = store.add(
        cron_expr=pending.cron_expr,
        timezone_name=config.schedule_timezone,
        original_prompt=pending.original_prompt,
        prompt_template=pending.prompt_template,
    )
    await query.edit_message_text(
        f"✅ 已建立排程 #{schedule.id}\n"
        f"cron：{schedule.cron_expr}\n"
        f"下次執行：{_local_time(schedule.next_run_at, schedule.timezone)}"
    )


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
        if success and result.stdout.strip() == NO_REPORT_SENTINEL:
            logger.info("排程 #%s 本次不需通知", schedule.id)
        else:
            await send_formatted_to_chat(
                application.bot,
                config.allowed_user_id,
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
        with suppress(Exception):
            await send_formatted_to_chat(
                application.bot,
                config.allowed_user_id,
                f"⚠️ **排程 #{schedule.id} 已自動暫停**\n\n連續失敗 3 次，請使用 "
                f"`/schedule_show {schedule.id}` 查看，再以 `/schedule_resume {schedule.id}` 恢復。",
            )


async def schedule_loop(application) -> None:
    logger.info("AGY 排程器已啟動")
    while True:
        try:
            for due in get_schedule_store().claim_due():
                await _execute_due_schedule(application, due)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("排程器輪詢失敗")
        await asyncio.sleep(20)


async def post_init(application) -> None:
    application.bot_data["schedule_task"] = asyncio.create_task(
        schedule_loop(application), name="agy-schedule-loop"
    )


async def post_shutdown(application) -> None:
    task = application.bot_data.get("schedule_task")
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def main() -> None:
    global CONFIG, SCHEDULE_STORE
    try:
        CONFIG = load_config()
    except ConfigError as exc:
        print(f"設定錯誤：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if "--check-config" in sys.argv:
        print("設定驗證成功")
        return

    SCHEDULE_STORE = ScheduleStore(CONFIG.schedule_db_path)

    if CONFIG.permission_mode == "full":
        logger.warning("AGY 目前使用 Full 模式：所有工具權限將自動核准")
    logger.info("載入設定：workdir=%s, mode=%s", CONFIG.agy_workdir, CONFIG.permission_mode)

    app = (
        ApplicationBuilder()
        .token(CONFIG.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("schedule_help", schedule_help_command))
    app.add_handler(CommandHandler("schedule_add", schedule_add_command))
    app.add_handler(CommandHandler("schedule_list", schedule_list_command))
    app.add_handler(CommandHandler("schedule_show", schedule_show_command))
    app.add_handler(CommandHandler("schedule_pause", schedule_pause_command))
    app.add_handler(CommandHandler("schedule_resume", schedule_resume_command))
    app.add_handler(CommandHandler("schedule_delete", schedule_delete_command))
    app.add_handler(
        CallbackQueryHandler(schedule_callback, pattern=r"^schedule_(?:confirm|cancel):")
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram AGY Bot 正在啟動長輪詢")
    app.run_polling()


if __name__ == "__main__":
    main()
