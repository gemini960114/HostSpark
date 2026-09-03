from __future__ import annotations

import html
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import hostspark.state as state
from hostspark.core.prompt import strip_effort_suffix
from hostspark.telegram.auth import _get_chat_id, reject_unauthorized

logger = logging.getLogger(__name__)


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    store = state.get_chat_state_store()

    if context.args:
        new_model = context.args[0].strip()
        store.update(chat_id, model=new_model)
        await update.message.reply_text(f"✅ 已切換模型為：<code>{html.escape(new_model)}</code>", parse_mode=ParseMode.HTML)
        return

    config = state.get_config()
    models = list(config.allowed_models)
    if not models:
        models = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]

    current = store.get_or_create(
        chat_id,
        defaults={"model": config.default_model, "effort": config.default_effort},
    ).model or config.default_model
    # 舊資料可能還存著帶 effort 後綴的完整模型名（例如 gemini-3.8-flash-medium），
    # 先去掉後綴再比對，才能正確標出目前對應的基底模型。
    current_family = strip_effort_suffix(current)

    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if m == current_family else '🤖 '}{m}", callback_data=f"model_sel:{m}")]
        for m in models
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"目前模型：`{current_family}`\n請選擇欲使用的模型：", reply_markup=reply_markup
    )


_EFFORT_LABELS = {"low": "🔵 Low", "medium": "🟡 Medium", "high": "🔴 High"}


async def effort_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)

    if context.args:
        if context.args[0].lower() not in {"low", "medium", "high"}:
            await update.message.reply_text("用法：`/effort low|medium|high`")
            return
        val = context.args[0].lower()
        state.get_chat_state_store().update(chat_id, effort=val)
        await update.message.reply_text(f"✅ 已設定推理深度 (effort) 為：`{val}`")
        return

    config = state.get_config()
    current = state.get_chat_state_store().get_or_create(
        chat_id,
        defaults={"model": config.default_model, "effort": config.default_effort},
    ).effort

    keyboard = [[
        InlineKeyboardButton(
            f"{'✅ ' if lvl == current else ''}{_EFFORT_LABELS[lvl]}",
            callback_data=f"effort_sel:{lvl}",
        )
        for lvl in ("low", "medium", "high")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"目前推理深度 (effort)：`{current}`\n請選擇：", reply_markup=reply_markup)


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"plan", "accept-edits"}:
        await update.message.reply_text("用法：`/mode plan|accept-edits`")
        return
    val = context.args[0].lower()
    config = state.get_config()
    if val == "accept-edits" and config.permission_mode == "safe":
        await update.message.reply_text(
            "⚠️ 全域模式目前為 `Safe`，`accept-edits` 模式僅能在全域 `Full` 模式下生效。\n"
            "已為此 Chat 記錄設定，但 AGY 執行時仍會自動受限於 Plan 模式。"
        )
    state.get_chat_state_store().update(_get_chat_id(update), mode=val)
    await update.message.reply_text(f"✅ 已設定模式 (mode) 為：`{val}`")


async def sandbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"on", "off", "1", "0"}:
        await update.message.reply_text("用法：`/sandbox on|off`")
        return
    val = context.args[0].lower() in {"on", "1"}
    state.get_chat_state_store().update(_get_chat_id(update), sandbox=val)
    await update.message.reply_text(f"✅ 已設定沙箱模式 (sandbox) 為：`{'on' if val else 'off'}`")


async def verbose_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    if not context.args or context.args[0].lower() not in {"detailed", "compact", "silent"}:
        await update.message.reply_text("用法：`/verbose detailed|compact|silent`")
        return
    val = context.args[0].lower()
    state.get_chat_state_store().update(_get_chat_id(update), verbose=val)
    await update.message.reply_text(f"✅ 已設定進度詳細度 (verbose) 為：`{val}`")


async def setdefault_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    settings = state.get_chat_state_store().get_or_create(chat_id)

    payload = {
        "model": settings.model,
        "effort": settings.effort,
        "mode": settings.mode,
        "sandbox": settings.sandbox,
        "verbose": settings.verbose,
    }
    token = state.get_pending_actions().put("setdefault", update.effective_user.id, payload, ttl_minutes=15)

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
