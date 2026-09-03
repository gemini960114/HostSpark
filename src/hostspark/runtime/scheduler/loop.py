from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import hostspark.state as state
from hostspark.core.executor import is_headless_permission_denied
from hostspark.core.sanitizer import redact_sensitive
from hostspark.storage.schedule_store import (
    NO_REPORT_SENTINEL,
    DueSchedule,
    render_prompt_variables,
)
from hostspark.telegram.formatters import result_message, send_formatted_to_chat

logger = logging.getLogger(__name__)


def _get_run_agy() -> Callable:
    bot_mod = sys.modules.get("bot")
    if bot_mod is not None and hasattr(bot_mod, "run_agy"):
        return bot_mod.run_agy
    from hostspark.core.executor import run_agy
    return run_agy


def cleanup_expired_workspaces_and_uploads(
    workspace_root: Path,
    state_dir: Path,
    schedule_db_path: Path | None = None,
    max_age_days: int = 30,
) -> int:
    now_ts = datetime.now(timezone.utc).timestamp()
    max_age_seconds = max_age_days * 86400

    target_dirs = [
        workspace_root / "uploads",
        workspace_root / "workspaces",
        state_dir.parent / "workspaces",
    ]
    if schedule_db_path is not None:
        target_dirs.append(schedule_db_path.parent / "workspaces")

    deleted_count = 0
    seen_dirs = set()
    for root_dir in target_dirs:
        try:
            resolved = root_dir.expanduser().resolve()
            if resolved in seen_dirs or not resolved.is_dir():
                continue
            seen_dirs.add(resolved)
            for item in resolved.rglob("*"):
                if item.is_file():
                    try:
                        if now_ts - item.stat().st_mtime > max_age_seconds:
                            item.unlink(missing_ok=True)
                            deleted_count += 1
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("清理過期檔案時略過：%s (%s)", root_dir, exc)
    return deleted_count


async def _execute_due_schedule(application, due: DueSchedule) -> None:
    config = state.get_config()
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
    run_agy_fn = _get_run_agy()
    try:
        async with state.agy_lock:
            result = await run_agy_fn(
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
        admin_ids = config.allowed_user_ids or {config.allowed_user_id}
        if success and result.stdout.strip() == NO_REPORT_SENTINEL:
            logger.info("排程 #%s 本次不需通知", schedule.id)
        else:
            for admin_id in sorted(admin_ids):
                with suppress(Exception):
                    await send_formatted_to_chat(
                        application.bot,
                        admin_id,
                        f"⏰ **排程 #{schedule.id} 執行結果**\n\n{result_message(result)}",
                    )
        if not success:
            error = result.stderr or result.stdout or "AGY 執行失敗"
    except Exception as exc:
        success = False
        error = redact_sensitive(str(exc))
        logger.exception("排程 #%s 執行或通知失敗", schedule.id)
    finally:
        auto_paused = state.get_schedule_store().record_result(
            schedule.id,
            success=success,
            error=error,
        )
    if auto_paused:
        admin_ids = config.allowed_user_ids or {config.allowed_user_id}
        for admin_id in sorted(admin_ids):
            with suppress(Exception):
                await send_formatted_to_chat(
                    application.bot,
                    admin_id,
                    f"⚠️ **排程 #{schedule.id} 已自動暫停**\n\n連續失敗 3 次，請使用 "
                    f"`/schedule_show {schedule.id}` 查看，再以 `/schedule_resume {schedule.id}` 恢復。",
                )


async def schedule_loop(application) -> None:
    logger.info("AGY 排程器已啟動")
    last_cleanup_date = None
    while True:
        try:
            # 1. Execute due schedules
            for due in state.get_schedule_store().claim_due():
                await _execute_due_schedule(application, due)

            # 2. Daily routine cleanup for expired files (> 30 days)
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today_str != last_cleanup_date:
                config = state.get_config()
                count = cleanup_expired_workspaces_and_uploads(
                    config.workspace_root,
                    config.state_db_path,
                    schedule_db_path=config.schedule_db_path,
                    max_age_days=30,
                )
                last_cleanup_date = today_str
                if count > 0:
                    logger.info("每日例行清理完成：共刪除 %s 個過期暫存檔案", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("排程器輪詢失敗")
        await asyncio.sleep(20)
