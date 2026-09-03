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


from hostspark.telegram.handlers import get_reply_keyboard



from hostspark.core.executor import run_agy as _core_run_agy
import hostspark.core.executor as _executor_mod


async def run_agy(*args, **kwargs) -> ProcessResult:
    current_run_proc = globals().get("run_process")
    if current_run_proc is not None and current_run_proc is not _executor_mod.run_process:
        orig = _executor_mod.run_process
        try:
            _executor_mod.run_process = current_run_proc
            return await _core_run_agy(*args, **kwargs)
        finally:
            _executor_mod.run_process = orig
    return await _core_run_agy(*args, **kwargs)




from hostspark.telegram.formatters import (
    result_message,
    send_formatted_response,
    send_formatted_to_chat,
)



# -----------------------------------------------------------------------------
# Handlers: Lifecycle, Session, Params, Queries, Passthrough (Moved to hostspark.telegram.handlers)
# -----------------------------------------------------------------------------
from hostspark.telegram.handlers import (
    _status_section,
    add_dir_command,
    agent_command,
    agents_command,
    agy_command,
    agy_confirm_command,
    cancel_command,
    changelog_command,
    clear_command,
    cli_help_command,
    compact_command,
    context_command,
    continue_command,
    effort_command,
    learn_command,
    menu_command,
    mode_command,
    model_command,
    new_command,
    plugins_command,
    project_command,
    sandbox_command,
    session_command,
    setdefault_command,
    start_command,
    status_command,
    tokens_command,
    usage_command,
    verbose_command,
    version_command,
)



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
# Schedules (Moved to hostspark.runtime.scheduler)
# -----------------------------------------------------------------------------
from hostspark.runtime.scheduler import (
    _change_schedule_state,
    _command_payload,
    _execute_due_schedule,
    _local_time,
    _run_schedule_add_flow,
    _schedule_id,
    _schedule_status,
    cleanup_expired_workspaces_and_uploads,
    schedule_add_command,
    schedule_delete_command,
    schedule_help_command,
    schedule_list_command,
    schedule_loop,
    schedule_pause_command,
    schedule_resume_command,
    schedule_show_command,
)



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



# -----------------------------------------------------------------------------
# Handlers: Settings & Admin (Moved to hostspark.telegram.handlers)
# -----------------------------------------------------------------------------
from hostspark.telegram.handlers import (
    _perform_bot_restart,
    disable_slash_commands_command,
    json_schema_command,
    log_file_command,
    new_project_command,
    output_format_command,
    print_timeout_command,
    restart_command,
    update_command,
)



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

