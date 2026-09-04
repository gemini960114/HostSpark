from __future__ import annotations

import logging
import sys
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import hostspark.state as state
from hostspark.core.pty import run_pty_command
from hostspark.core.sanitizer import redact_sensitive
from hostspark.telegram.auth import _get_chat_id, reject_unauthorized

logger = logging.getLogger(__name__)


from hostspark.prompts import COMPACT_CONTEXT_PROMPT, build_learn_prompt
from hostspark.telegram.dispatcher import _enqueue_and_handle_prompt


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    status_msg = await update.message.reply_text("⏳ 正在查詢 AGY 配額與使用量...")
    config = state.get_config()
    try:
        async with state.agy_lock:
            report = await run_pty_command(config.agy_bin, config.agy_workdir, "/quota")
        await status_msg.edit_text(report, parse_mode=ParseMode.HTML)
        state.queue_context_injection(_get_chat_id(update), report)
    except Exception as exc:
        logger.exception("查詢配額失敗")
        await status_msg.edit_text(f"❌ 查詢失敗：{redact_sensitive(str(exc))}")


async def context_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    status_msg = await update.message.reply_text("⏳ 正在查詢上下文資訊...")
    config = state.get_config()
    try:
        async with state.agy_lock:
            report = await run_pty_command(config.agy_bin, config.agy_workdir, "/context")
        await status_msg.edit_text(report)
        state.queue_context_injection(_get_chat_id(update), report)
    except Exception as exc:
        logger.exception("查詢上下文失敗")
        await status_msg.edit_text(f"❌ 查詢失敗：{redact_sensitive(str(exc))}")


async def tokens_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    await usage_command(update, context)


async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    payload = " ".join(context.args) if context.args else None
    prompt = build_learn_prompt(payload)
    await _enqueue_and_handle_prompt(update, context, prompt)


async def compact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    prompt = COMPACT_CONTEXT_PROMPT
    await _enqueue_and_handle_prompt(update, context, prompt)
