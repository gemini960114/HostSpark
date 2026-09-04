from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import hostspark.state as state
from hostspark.constants import (
    DEFAULT_CIRCUIT_BREAKER_MAX_FAILURES,
    SCHEDULE_MAX_EXPANSION_PROMPT_CHARS,
)
from hostspark.core.sanitizer import redact_sensitive
from hostspark.storage.schedule_store import (
    Schedule,
    ScheduleError,
    build_prompt_expansion_request,
    normalize_cron,
    parse_schedule_add_payload,
)
from hostspark.telegram.auth import reject_unauthorized
from hostspark.telegram.formatters import result_message, send_formatted_response

logger = logging.getLogger(__name__)


import hostspark.core.executor as executor


def _local_time(value: datetime | None, timezone_name: str) -> str:
    if value is None:
        return "—"
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S %Z")


def _schedule_status(schedule: Schedule) -> str:
    if schedule.enabled:
        return "啟用"
    if schedule.consecutive_failures >= DEFAULT_CIRCUIT_BREAKER_MAX_FAILURES:
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
    config = state.get_config()
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
    if await reject_unauthorized(update):
        return
    config = state.get_config()
    store = state.get_schedule_store()
    if store.count() >= config.schedule_max_tasks:
        await update.message.reply_text(f"❌ 排程數量已達上限（{config.schedule_max_tasks}）。")
        return

    pending_actions = state.get_pending_actions()
    if pending_actions.count(kind="schedule_add", user_id=update.effective_user.id) >= 5:
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
        async with state.agy_lock:
            result = await executor.run_agy(
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
    if len(prompt_template) > SCHEDULE_MAX_EXPANSION_PROMPT_CHARS:
        await status_message.edit_text(
            f"❌ AGY 整理後的 prompt 超過 {SCHEDULE_MAX_EXPANSION_PROMPT_CHARS} 個字元，請縮短原始要求。"
        )
        return

    payload = {
        "cron_expr": cron_expr,
        "original_prompt": original_prompt,
        "prompt_template": prompt_template,
    }
    pending_token = pending_actions.put("schedule_add", update.effective_user.id, payload, ttl_minutes=15)

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
    schedules = state.get_schedule_store().list_all()
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
    schedule = state.get_schedule_store().get(schedule_id)
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
    store = state.get_schedule_store()
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
