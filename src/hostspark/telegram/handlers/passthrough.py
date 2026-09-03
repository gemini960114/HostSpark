from __future__ import annotations

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import hostspark.state as state
from hostspark.core.cli_args import (
    is_dangerous_custom_command,
    prepare_custom_args,
    validate_custom_args,
)
from hostspark.core.executor import run_process
from hostspark.core.sanitizer import (
    build_safe_subprocess_env,
    redact_sensitive,
    safe_join,
)
from hostspark.telegram.auth import _get_chat_id, reject_unauthorized
from hostspark.telegram.formatters import result_message, send_formatted_response

logger = logging.getLogger(__name__)


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
    chat_state = state.get_chat_state_store().get_or_create(chat_id)
    final_args = prepare_custom_args(raw_args, enforce_sandbox=chat_state.sandbox)

    if is_dangerous_custom_command(final_args):
        token = state.get_pending_actions().put("agy_confirm", update.effective_user.id, final_args, ttl_minutes=15)
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

    config = state.get_config()
    status_msg = await update.message.reply_text("⏳ 正在執行 agy 指令...")
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    try:
        async with state.agy_lock:
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
    action = state.get_pending_actions().pop(token, user_id=update.effective_user.id)
    if not action or action.kind != "agy_confirm":
        await update.message.reply_text("❌ 確認 Token 無效或已過期。")
        return

    args = action.payload
    config = state.get_config()
    status_msg = await update.message.reply_text(f"⏳ 正在執行核准後的指令：`agy {' '.join(args)}`...")
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    try:
        async with state.agy_lock:
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
    config = state.get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "agents"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, result_message(res))


async def changelog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "changelog"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, result_message(res))


async def plugins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "plugins"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, result_message(res))


async def cli_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "--help"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, f"```\n{res.stdout}\n```")


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    res = await run_process([str(config.agy_bin), "--version"], cwd=config.agy_workdir, env=env, timeout_seconds=15, max_output_bytes=50000)
    await send_formatted_response(update.message, f"📌 **AGY CLI Version**:\n`{res.stdout or res.stderr}`")


async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = state.get_chat_state_store().get_or_create(chat_id).agent or "(預設)"
        await update.message.reply_text(f"目前 Agent: `{curr}`\n切換用法：`/agent <AGENT_NAME>`")
        return
    val = context.args[0].strip()
    state.get_chat_state_store().update(chat_id, agent=val)
    await update.message.reply_text(f"✅ 已設定 Agent 為：`{val}`")


async def project_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = state.get_chat_state_store().get_or_create(chat_id).project or "(無)"
        await update.message.reply_text(f"目前 Project: `{curr}`\n設定用法：`/project <PROJECT_ID>`")
        return
    val = context.args[0].strip()
    state.get_chat_state_store().update(chat_id, project=val)
    await update.message.reply_text(f"✅ 已設定專案 (project) 為：`{val}`")


async def add_dir_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    chat_id = _get_chat_id(update)
    if not context.args:
        curr = state.get_chat_state_store().get_or_create(chat_id).add_dirs
        await update.message.reply_text(f"目前額外目錄 (add_dirs): `{list(curr)}`\n新增用法：`/add-dir <目錄路徑>`")
        return
    raw_path = " ".join(context.args).strip()
    config = state.get_config()
    try:
        resolved = safe_join(config.workspace_root, raw_path)
        if not resolved.is_dir():
            await update.message.reply_text(f"❌ 目錄不存在：`{resolved}`")
            return
        chat_state = state.get_chat_state_store().get_or_create(chat_id)
        dirs = list(chat_state.add_dirs)
        if str(resolved) not in dirs:
            dirs.append(str(resolved))
            state.get_chat_state_store().update(chat_id, add_dirs=dirs)
        await update.message.reply_text(f"✅ 已將目錄加入工作空間：`{resolved}`")
    except Exception as exc:
        await update.message.reply_text(f"❌ 目錄路徑無效：{exc}")
