import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


from hostspark.constants import DEFAULT_JOB_QUEUE_MAXSIZE
from hostspark.prompts import compose_followup_prompt

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    chat_id: int
    user_id: int
    prompt: str
    created_at: float = field(default_factory=time.time)
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    result: Any = None
    error: Exception | None = None
    status_msg: Any = None


class JobQueue:
    def __init__(self, maxsize: int = DEFAULT_JOB_QUEUE_MAXSIZE):
        self._maxsize = maxsize
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=maxsize)
        self._active_job: Job | None = None
        self._active_task: asyncio.Task | None = None
        self._chat_pending: dict[int, list[Job]] = {}
        self._worker_task: asyncio.Task | None = None
        self._closed = False

    @property
    def active_job(self) -> Job | None:
        return self._active_job

    def get_status(self) -> dict[str, Any]:
        pending_count = sum(
            len([j for j in jobs if not j.cancelled])
            for jobs in self._chat_pending.values()
        )
        return {
            "queue_length": pending_count,
            "active_chat_id": self._active_job.chat_id if (self._active_job and not self._active_job.cancelled) else None,
            "is_busy": self._active_job is not None and not self._active_job.cancelled,
        }

    def enqueue(
        self,
        chat_id: int,
        user_id: int,
        prompt: str,
        auto_interrupt: bool = True,
    ) -> tuple[Job, bool]:
        if self._closed:
            raise RuntimeError("JobQueue 已關閉")

        was_merged = False
        final_prompt = prompt

        if auto_interrupt:
            parts: list[str] = []
            # Check active job
            if self._active_job and self._active_job.chat_id == chat_id and not self._active_job.cancelled:
                parts.append(self._active_job.prompt)

            # Check pending jobs
            pending_list = self._chat_pending.get(chat_id, [])
            for pj in pending_list:
                if not pj.cancelled:
                    parts.append(pj.prompt)

            if parts:
                was_merged = True
                final_prompt = compose_followup_prompt(parts, prompt)
                self.cancel_for_chat(chat_id)

        job = Job(
            id=secrets.token_hex(4),
            chat_id=chat_id,
            user_id=user_id,
            prompt=final_prompt,
        )
        self._chat_pending.setdefault(chat_id, []).append(job)
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull as exc:
            self._chat_pending[chat_id].remove(job)
            raise RuntimeError("任務佇列已滿，請稍後再試。") from exc

        return job, was_merged

    def cancel_for_chat(self, chat_id: int) -> bool:
        cancelled_any = False
        pending_list = self._chat_pending.pop(chat_id, [])
        for j in pending_list:
            if not j.cancelled:
                j.cancelled = True
                j.done_event.set()
                cancelled_any = True

        if self._active_job and self._active_job.chat_id == chat_id and not self._active_job.cancelled:
            self._active_job.cancelled = True
            self._active_job.done_event.set()
            if self._active_task and not self._active_task.done():
                self._active_task.cancel()
            cancelled_any = True

        return cancelled_any

    async def _worker_loop(self, handler: Callable[[Job], Awaitable[Any]]) -> None:
        while not self._closed:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                break

            # Remove from chat pending
            pending_list = self._chat_pending.get(job.chat_id, [])
            if job in pending_list:
                pending_list.remove(job)
                if not pending_list:
                    self._chat_pending.pop(job.chat_id, None)

            if job.cancelled:
                self._queue.task_done()
                continue

            self._active_job = job
            try:
                self._active_task = asyncio.create_task(handler(job))
                job.result = await self._active_task
            except asyncio.CancelledError:
                job.cancelled = True
            except Exception as exc:
                job.error = exc
            finally:
                self._active_job = None
                self._active_task = None
                job.done_event.set()
                self._queue.task_done()

    def start(self, handler: Callable[[Job], Awaitable[Any]]) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop(handler))

    async def stop(self) -> None:
        self._closed = True
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
