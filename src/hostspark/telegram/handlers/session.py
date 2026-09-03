from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import hostspark.state as state
from hostspark.core.sanitizer import safe_join, validate_project_dir_name
from hostspark.core.workspace import list_project_dirs, switch_project_dir
from hostspark.telegram.auth import _get_chat_id, reject_unauthorized

logger = logging.getLogger(__name__)


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    config = state.get_config()

    if context.args:
        name = context.args[0].strip()
        error = validate_project_dir_name(name)
        if error:
            await update.message.reply_text(f"❌ {error}")
            return
        safe_join(config.workspace_root, name).mkdir(parents=True, exist_ok=True)
        switch_project_dir(chat_id, name)
        await update.message.reply_text(
            f"✅ 已切換至專案目錄 `{name}`，並開啟全新對話。"
        )
        return

    existing = list_project_dirs(config.workspace_root)
    current = state.get_chat_state_store().get_or_create(chat_id).workspace_dir
    if not existing:
        await update.message.reply_text(
            "目前還沒有任何專案目錄。請輸入 `/new <新目錄名稱>` 建立第一個。"
        )
        return

    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if d == current else '📁 '}{d}", callback_data=f"workdir_sel:{d}")]
        for d in existing
    ]
    await update.message.reply_text(
        "請選擇要使用的專案目錄，或輸入 `/new <新目錄名稱>` 建立新的：",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    state.get_chat_state_store().clear_conversation(chat_id)
    await update.message.reply_text("✅ 已重置對話工作階段。下一個提問將開啟全新對話。")


async def continue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"on", "true", "1", "enable", "off", "false", "0", "disable"}:
        await update.message.reply_text("用法：`/continue on` 或 `/continue off`")
        return

    arg = context.args[0].lower()
    chat_id = _get_chat_id(update)
    store = state.get_chat_state_store()
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
    settings = state.get_chat_state_store().get_or_create(chat_id)
    config = state.get_config()

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
        f"• 專案目錄 (workspace_dir)：<code>{settings.workspace_dir or '(未選擇，使用預設隔離目錄)'}</code>\n"
        f"• 專案 (project)：<code>{settings.project or '(無)'}</code>\n"
        f"• 額外目錄 (add_dirs)：<code>{add_dirs_str}</code>\n"
        f"• 輸出格式 (output_format)：<code>{settings.output_format}</code>\n"
        f"• 更新時間：<code>{settings.updated_at}</code>\n\n"
        "💡 可使用 <code>/setdefault</code> 將上述設定寫入 <code>.env</code> 全域預設。"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
