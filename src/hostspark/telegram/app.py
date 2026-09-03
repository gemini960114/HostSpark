from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import hostspark.state as state
from hostspark.config import BotConfig
from hostspark.runtime.job_queue import Job
from hostspark.runtime.scheduler import (
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
from hostspark.telegram.dispatcher import (
    _execute_chat_job,
    global_callback_query_handler,
    handle_attachment,
    handle_message,
)
from hostspark.telegram.handlers import (
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
    disable_slash_commands_command,
    effort_command,
    json_schema_command,
    learn_command,
    log_file_command,
    menu_command,
    mode_command,
    model_command,
    new_command,
    new_project_command,
    output_format_command,
    plugins_command,
    print_timeout_command,
    project_command,
    restart_command,
    sandbox_command,
    session_command,
    setdefault_command,
    start_command,
    status_command,
    tokens_command,
    update_command,
    usage_command,
    verbose_command,
    version_command,
)

logger = logging.getLogger(__name__)


async def post_init(application) -> None:
    # 1. Start schedule task
    application.bot_data["schedule_task"] = asyncio.create_task(
        schedule_loop(application), name="agy-schedule-loop"
    )

    # 2. Start Job Queue worker
    async def job_handler(job: Job) -> None:
        await _execute_chat_job(application, job, status_msg=job.status_msg)

    state.JOB_QUEUE.start(job_handler)

    # 3. Crash / Restart in-flight jobs recovery
    store = state.get_chat_state_store()
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
            state.JOB_QUEUE.enqueue(chat_id=chat_id, user_id=0, prompt=prompt, auto_interrupt=False)
        store.clear_all_in_flight()

    # 4. Clean up expired files (> 30 days) across uploads and workspaces
    config = state.get_config()
    cleanup_expired_workspaces_and_uploads(
        config.workspace_root,
        config.state_db_path,
        schedule_db_path=config.schedule_db_path,
        max_age_days=30,
    )


async def post_shutdown(application) -> None:
    task = application.bot_data.get("schedule_task")
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await state.JOB_QUEUE.stop()
    if state.INSTANCE_LOCK:
        state.INSTANCE_LOCK.release()


def build_application(config: BotConfig | None = None) -> Any:
    cfg = config or state.get_config()
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
