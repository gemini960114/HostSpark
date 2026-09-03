from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import re
import secrets
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

import hostspark.state as state
from hostspark.core.prompt import detect_schedule_intent
from hostspark.core.sanitizer import (
    build_safe_subprocess_env,
    redact_sensitive,
    safe_join,
    validate_project_dir_name,
)
from hostspark.core.workspace import switch_project_dir
from hostspark.runtime.job_queue import Job
from hostspark.runtime.scheduler import _local_time, _run_schedule_add_flow
from hostspark.telegram.auth import _get_chat_id, is_authorized, reject_unauthorized
from hostspark.telegram.formatters import (
    result_message,
    send_formatted_response,
    send_formatted_to_chat,
)
from hostspark.telegram.media import detect_output_media, fetch_ssrf_safe_media

logger = logging.getLogger(__name__)

SAFE_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".json", ".csv", ".py", ".go", ".js", ".ts",
    ".yaml", ".yml", ".toml", ".log", ".png", ".jpg", ".jpeg", ".webp", ".gif",
}


import hostspark.core.executor as executor


async def handle_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_unauthorized(update):
        return
    config = state.get_config()
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


async def _enqueue_and_handle_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str
) -> None:
    config = state.get_config()
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
        job, was_merged = state.JOB_QUEUE.enqueue(
            chat_id=chat_id,
            user_id=user_id,
            prompt=prompt,
            auto_interrupt=config.auto_interrupt,
        )
    except RuntimeError as exc:
        if getattr(update, "message", None) and hasattr(update.message, "reply_text"):
            await update.message.reply_text(f"❌ {exc}")
        return

    job.status_msg = status_msg

    if was_merged and status_msg:
        with suppress(Exception):
            await status_msg.edit_text("🔄 已合併前次任務與新追加的指示，重新執行中...")

    app = getattr(context, "application", None) or context
    if state.JOB_QUEUE._worker_task is None or state.JOB_QUEUE._worker_task.done():
        state.JOB_QUEUE.start(lambda j: _execute_chat_job(app, j, status_msg=j.status_msg))

    # Intentionally not awaiting job.done_event here: _execute_chat_job (run by the
    # queue's own worker task) sends the final reply itself via status_msg, and this
    # handler returning promptly is what lets python-telegram-bot dispatch the next
    # update (e.g. /cancel) without waiting for this job to finish.


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
    config = state.get_config()
    chat_id = job.chat_id
    store = state.get_chat_state_store()

    store.set_in_flight(chat_id, job.prompt)

    if status_msg is None and getattr(application, "bot", None) and hasattr(application.bot, "send_message"):
        try:
            status_msg = await application.bot.send_message(
                chat_id=chat_id,
                text=config.waiting_message,
            )
        except Exception:
            pass

    chat_state = state.get_chat_state_store().get_or_create(
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
        async with state.agy_lock:
            result = await executor.run_agy(
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


def _write_defaults_to_env(payload: dict[str, Any]) -> None:
    env_file = state.ENV_PATH
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

    temp_file = env_file.parent / f".env.tmp_{secrets.token_hex(4)}"
    temp_file.write_text(new_content, encoding="utf-8")
    os.replace(temp_file, env_file)
    with suppress(Exception):
        env_file.chmod(0o600)

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

    if data.startswith("model_sel:"):
        model_name = data.split(":", 1)[1]
        state.get_chat_state_store().update(_get_chat_id(update), model=model_name)
        await query.edit_message_text(f"✅ 已切換模型為：`{model_name}`")
        return

    if data.startswith("effort_sel:"):
        level = data.split(":", 1)[1]
        state.get_chat_state_store().update(_get_chat_id(update), effort=level)
        await query.edit_message_text(f"✅ 已設定推理深度 (effort) 為：`{level}`")
        return

    if data.startswith("workdir_sel:"):
        name = data.split(":", 1)[1]
        error = validate_project_dir_name(name)
        if error:
            await query.edit_message_text(f"❌ {error}")
            return
        switch_project_dir(_get_chat_id(update), name)
        await query.edit_message_text(f"✅ 已切換至專案目錄 `{name}`，並開啟全新對話。")
        return

    if data.startswith("setdefault_confirm:"):
        token = data.split(":", 1)[1]
        action = state.get_pending_actions().pop(token, user_id=user_id)
        if not action or action.kind != "setdefault":
            await query.edit_message_text("❌ 確認 Token 無效或已過期。")
            return
        payload = action.payload
        _write_defaults_to_env(payload)
        await query.edit_message_text("✅ 已成功將設定寫回 `.env` 全域預設值！")
        return

    if data.startswith("setdefault_cancel:"):
        token = data.split(":", 1)[1]
        state.get_pending_actions().pop(token, user_id=user_id)
        await query.edit_message_text("已取消設定寫入。")
        return

    if data.startswith("agy_confirm:"):
        token = data.split(":", 1)[1]
        action = state.get_pending_actions().pop(token, user_id=user_id)
        if not action or action.kind != "agy_confirm":
            await query.edit_message_text("❌ 確認 Token 無效或已過期。")
            return
        args = action.payload
        config = state.get_config()
        await query.edit_message_text(f"⏳ 正在執行核准後的指令：`agy {' '.join(args)}`...")
        env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
        try:
            async with state.agy_lock:
                res = await executor.run_process(
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
        state.get_pending_actions().pop(token, user_id=user_id)
        await query.edit_message_text("已取消指令執行。")
        return

    if data.startswith("schedule_confirm:") or data.startswith("schedule_cancel:"):
        action_name, token = data.split(":", 1)
        action = state.get_pending_actions().pop(token, user_id=user_id)
        if not action or action.kind != "schedule_add":
            await query.edit_message_text("⌛ 此排程預覽已失效，請重新建立。")
            return
        if action_name == "schedule_cancel":
            await query.edit_message_text("已取消建立排程。")
            return

        config = state.get_config()
        store = state.get_schedule_store()
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
