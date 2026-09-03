from __future__ import annotations

import logging
from telegram import Update
from telegram.ext import ContextTypes

import hostspark.state as state
from hostspark.telegram.auth import _get_chat_id, reject_unauthorized

logger = logging.getLogger(__name__)


async def output_format_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args or context.args[0].lower() not in {"text", "json", "stream-json"}:
        curr = state.get_chat_state_store().get_or_create(chat_id).output_format
        await update.message.reply_text(f"目前輸出格式 (output_format): `{curr}`\n設定用法：`/output-format text|json|stream-json`")
        return
    fmt = context.args[0].lower()
    state.get_chat_state_store().update(chat_id, output_format=fmt)
    await update.message.reply_text(f"✅ 已設定輸出格式 (output_format) 為：`{fmt}`")


async def json_schema_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = state.get_chat_state_store().get_or_create(chat_id).json_schema or "(無)"
        await update.message.reply_text(f"目前 JSON Schema: `{curr}`\n設定用法：`/json-schema <SCHEMA>` (傳入 `clear` 清除)")
        return
    schema_val = " ".join(context.args).strip()
    if schema_val.lower() == "clear":
        state.get_chat_state_store().update(chat_id, json_schema=None)
        await update.message.reply_text("✅ 已清除 JSON Schema 設定。")
    else:
        state.get_chat_state_store().update(chat_id, json_schema=schema_val)
        await update.message.reply_text(f"✅ 已設定 JSON Schema 為：`{schema_val}`")


async def log_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = state.get_chat_state_store().get_or_create(chat_id).log_file or "(無)"
        await update.message.reply_text(f"目前日誌檔案 (log_file): `{curr}`\n設定用法：`/log-file <PATH>` (傳入 `clear` 清除)")
        return
    log_val = context.args[0].strip()
    if log_val.lower() == "clear":
        state.get_chat_state_store().update(chat_id, log_file=None)
        await update.message.reply_text("✅ 已清除日誌檔案設定。")
    else:
        state.get_chat_state_store().update(chat_id, log_file=log_val)
        await update.message.reply_text(f"✅ 已設定日誌檔案 (log_file) 為：`{log_val}`")


async def print_timeout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = state.get_chat_state_store().get_or_create(chat_id).print_timeout or "(使用全域預設)"
        await update.message.reply_text(f"目前執行超時 (print_timeout): `{curr}`\n設定用法：`/print-timeout <DURATION>` (例如 `5m`、`600s`，傳入 `clear` 清除)")
        return
    to_val = context.args[0].strip()
    if to_val.lower() == "clear":
        state.get_chat_state_store().update(chat_id, print_timeout=None)
        await update.message.reply_text("✅ 已清除自訂超時，回復全域預設。")
    else:
        state.get_chat_state_store().update(chat_id, print_timeout=to_val)
        await update.message.reply_text(f"✅ 已設定超時 (print_timeout) 為：`{to_val}`")


async def new_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args or context.args[0].lower() not in {"on", "off", "1", "0"}:
        curr = "on" if state.get_chat_state_store().get_or_create(chat_id).new_project else "off"
        await update.message.reply_text(f"目前新專案旗標 (new_project): `{curr}`\n設定用法：`/new-project on|off`")
        return
    val = context.args[0].lower() in {"on", "1"}
    state.get_chat_state_store().update(chat_id, new_project=val)
    await update.message.reply_text(f"✅ 已設定新專案旗標 (new_project) 為：`{'on' if val else 'off'}`")


async def disable_slash_commands_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args or context.args[0].lower() not in {"on", "off", "1", "0"}:
        curr = "on" if state.get_chat_state_store().get_or_create(chat_id).disable_slash_commands else "off"
        await update.message.reply_text(f"目前停用斜線指令旗標 (disable_slash_commands): `{curr}`\n設定用法：`/disable-slash-commands on|off`")
        return
    val = context.args[0].lower() in {"on", "1"}
    state.get_chat_state_store().update(chat_id, disable_slash_commands=val)
    await update.message.reply_text(f"✅ 已設定停用斜線指令旗標 (disable_slash_commands) 為：`{'on' if val else 'off'}`")
