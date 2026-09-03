from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hostspark.config import BotConfig
from hostspark.runtime.instance_lock import InstanceLock
from hostspark.runtime.job_queue import JobQueue
from hostspark.runtime.pending_actions import PendingActionStore
from hostspark.storage.chat_state import ChatStateStore
from hostspark.storage.schedule_store import ScheduleStore


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = Path(os.getenv("AGY_ENV_FILE", str(BASE_DIR / ".env"))).expanduser()

CONFIG: BotConfig | None = None
SCHEDULE_STORE: ScheduleStore | None = None
CHAT_STATE_STORE: ChatStateStore | None = None
PENDING_ACTIONS = PendingActionStore()
JOB_QUEUE = JobQueue(maxsize=50)
INSTANCE_LOCK: InstanceLock | None = None
agy_lock = asyncio.Lock()

# chat_ids that just switched (via /new) to a project directory agy may not
# have registered as a project yet. Consumed (removed) by the very next
# run_agy() call for that chat, which adds --new-project while it's present.
# Deliberately separate from chat_state.new_project (the sticky, user-facing
# /new_project on|off toggle) so switching directories never overrides that.
PENDING_PROJECT_INIT: set[int] = set()
# chat_ids that requested a fresh conversation (via /clear or /new).
# Suppresses --continue on the very next run_agy() call.
PENDING_CLEAR: set[int] = set()

_PENDING_CONTEXT_TTL_SECONDS = 600
_PENDING_CONTEXT: dict[int, tuple[float, str]] = {}


def queue_context_injection(chat_id: int, report_text: str) -> None:
    _PENDING_CONTEXT[chat_id] = (asyncio.get_event_loop().time(), report_text)


def pop_context_injection(chat_id: int) -> str | None:
    entry = _PENDING_CONTEXT.pop(chat_id, None)
    if entry is None:
        return None
    queued_at, report_text = entry
    if asyncio.get_event_loop().time() - queued_at > _PENDING_CONTEXT_TTL_SECONDS:
        return None
    return report_text


def get_config() -> BotConfig:
    if CONFIG is None:
        raise RuntimeError("Bot 尚未載入設定")
    return CONFIG


def get_schedule_store() -> ScheduleStore:
    if SCHEDULE_STORE is None:
        raise RuntimeError("排程資料庫尚未初始化")
    return SCHEDULE_STORE


def get_chat_state_store() -> ChatStateStore:
    if CHAT_STATE_STORE is None:
        raise RuntimeError("對話狀態資料庫尚未初始化")
    return CHAT_STATE_STORE


def get_pending_actions() -> PendingActionStore:
    return PENDING_ACTIONS


def get_job_queue() -> JobQueue:
    return JOB_QUEUE


def get_instance_lock() -> InstanceLock | None:
    return INSTANCE_LOCK


def set_instance_lock(lock: InstanceLock | None) -> None:
    global INSTANCE_LOCK
    INSTANCE_LOCK = lock


def get_agy_lock() -> asyncio.Lock:
    return agy_lock
