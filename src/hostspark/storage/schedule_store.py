import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterError, croniter


UTC = timezone.utc
from hostspark.constants import (
    DEFAULT_CIRCUIT_BREAKER_MAX_FAILURES,
    NO_REPORT_SENTINEL,
    SCHEDULE_CLAIM_BATCH_LIMIT,
    SCHEDULE_MAX_ORIGINAL_PROMPT_CHARS,
)
from hostspark.prompts import build_prompt_expansion_request

__all__ = [
    "ScheduleError",
    "Schedule",
    "DueSchedule",
    "ScheduleStore",
    "get_timezone",
    "normalize_cron",
    "next_run_time",
    "parse_schedule_add_payload",
    "render_prompt_variables",
    # Re-exported for backward compatibility (other modules and tests import
    # these from here rather than from their new homes in hostspark.constants
    # / hostspark.prompts).
    "NO_REPORT_SENTINEL",
    "build_prompt_expansion_request",
]


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Schedule:
    id: int
    cron_expr: str
    timezone: str
    original_prompt: str
    prompt_template: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    run_count: int
    consecutive_failures: int
    created_at: datetime


@dataclass(frozen=True)
class DueSchedule:
    schedule: Schedule
    scheduled_at: datetime


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"無效的 IANA 時區：{name}") from exc


def normalize_cron(expression: str, timezone_name: str, minimum_minutes: int) -> str:
    expression = " ".join(expression.split())
    if len(expression.split()) != 5:
        raise ScheduleError("排程必須是標準五欄 cron：分 時 日 月 週")
    try:
        if not croniter.is_valid(expression, strict=True):
            raise ScheduleError("cron 表達式無效")
    except (CroniterError, ValueError) as exc:
        raise ScheduleError("cron 表達式無效") from exc

    zone = get_timezone(timezone_name)
    cursor = datetime(2026, 1, 1, tzinfo=zone)
    try:
        iterator = croniter(expression, cursor)
        occurrences = [iterator.get_next(datetime) for _ in range(32)]
    except (CroniterError, ValueError) as exc:
        raise ScheduleError("cron 表達式無效") from exc

    minimum = timedelta(minutes=minimum_minutes)
    for previous, current in zip(occurrences, occurrences[1:]):
        if current.astimezone(UTC) - previous.astimezone(UTC) < minimum:
            raise ScheduleError(f"排程間隔不得少於 {minimum_minutes} 分鐘")
    return expression


def next_run_time(expression: str, timezone_name: str, after: datetime) -> datetime:
    if after.tzinfo is None:
        raise ValueError("after 必須包含時區")
    zone = get_timezone(timezone_name)
    local_after = after.astimezone(zone)
    try:
        result = croniter(expression, local_after).get_next(datetime)
    except (CroniterError, ValueError) as exc:
        raise ScheduleError("無法計算下一次執行時間") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=zone)
    return result.astimezone(UTC)


def parse_schedule_add_payload(text: str) -> tuple[str, str]:
    parts = text.strip().split(maxsplit=5)
    if len(parts) != 6:
        raise ScheduleError("格式：/schedule_add <分 時 日 月 週> <任務內容>")
    cron_expr = " ".join(parts[:5])
    request = parts[5].strip()
    if not request:
        raise ScheduleError("任務內容不可留空")
    if len(request) > SCHEDULE_MAX_ORIGINAL_PROMPT_CHARS:
        raise ScheduleError(f"原始任務不可超過 {SCHEDULE_MAX_ORIGINAL_PROMPT_CHARS} 個字元")
    return cron_expr, request


def render_prompt_variables(
    template: str,
    *,
    timezone_name: str,
    scheduled_at: datetime,
    run_number: int,
    now: datetime | None = None,
) -> str:
    zone = get_timezone(timezone_name)
    current = (now or datetime.now(UTC)).astimezone(zone)
    scheduled = scheduled_at.astimezone(zone)
    values = {
        "{{now}}": current.isoformat(timespec="seconds"),
        "{{date}}": current.date().isoformat(),
        "{{time}}": current.strftime("%H:%M:%S"),
        "{{timezone}}": timezone_name,
        "{{scheduled_at}}": scheduled.isoformat(timespec="seconds"),
        "{{run_number}}": str(run_number),
    }
    rendered = template
    for variable, value in values.items():
        rendered = rendered.replace(variable, value)
    return rendered


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime 必須包含時區")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


class ScheduleStore:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cron_expr TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    original_prompt TEXT NOT NULL,
                    prompt_template TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    last_status TEXT,
                    last_error TEXT,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_schedules_due "
                "ON schedules(enabled, next_run_at)"
            )
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @staticmethod
    def _row_to_schedule(row: sqlite3.Row) -> Schedule:
        return Schedule(
            id=row["id"],
            cron_expr=row["cron_expr"],
            timezone=row["timezone"],
            original_prompt=row["original_prompt"],
            prompt_template=row["prompt_template"],
            enabled=bool(row["enabled"]),
            next_run_at=_from_iso(row["next_run_at"]),
            last_run_at=_from_iso(row["last_run_at"]),
            last_status=row["last_status"],
            last_error=row["last_error"],
            run_count=row["run_count"],
            consecutive_failures=row["consecutive_failures"],
            created_at=_from_iso(row["created_at"]),
        )

    def count(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM schedules").fetchone()
        return int(row["count"])

    def add(
        self,
        *,
        cron_expr: str,
        timezone_name: str,
        original_prompt: str,
        prompt_template: str,
        now: datetime | None = None,
    ) -> Schedule:
        current = now or datetime.now(UTC)
        next_run = next_run_time(cron_expr, timezone_name, current)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO schedules (
                    cron_expr, timezone, original_prompt, prompt_template,
                    enabled, next_run_at, created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    cron_expr,
                    timezone_name,
                    original_prompt,
                    prompt_template,
                    _to_iso(next_run),
                    _to_iso(current),
                ),
            )
            schedule_id = cursor.lastrowid
        schedule = self.get(schedule_id)
        if schedule is None:
            raise RuntimeError("建立排程後無法讀回資料")
        return schedule

    def get(self, schedule_id: int) -> Schedule | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
        return self._row_to_schedule(row) if row else None

    def list_all(self) -> list[Schedule]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM schedules ORDER BY id").fetchall()
        return [self._row_to_schedule(row) for row in rows]

    def delete(self, schedule_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        return cursor.rowcount == 1

    def pause(self, schedule_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE schedules SET enabled = 0, next_run_at = NULL WHERE id = ?",
                (schedule_id,),
            )
        return cursor.rowcount == 1

    def resume(self, schedule_id: int, now: datetime | None = None) -> bool:
        schedule = self.get(schedule_id)
        if schedule is None:
            return False
        current = now or datetime.now(UTC)
        next_run = next_run_time(schedule.cron_expr, schedule.timezone, current)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE schedules
                SET enabled = 1, next_run_at = ?, consecutive_failures = 0,
                    last_status = 'resumed', last_error = NULL
                WHERE id = ?
                """,
                (_to_iso(next_run), schedule_id),
            )
        return cursor.rowcount == 1

    def claim_due(
        self, now: datetime | None = None, limit: int = SCHEDULE_CLAIM_BATCH_LIMIT
    ) -> list[DueSchedule]:
        current = now or datetime.now(UTC)
        claimed: list[DueSchedule] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM schedules
                WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
                ORDER BY next_run_at, id
                LIMIT ?
                """,
                (_to_iso(current), limit),
            ).fetchall()
            for row in rows:
                schedule = self._row_to_schedule(row)
                scheduled_at = schedule.next_run_at
                if scheduled_at is None:
                    continue
                try:
                    next_run = next_run_time(schedule.cron_expr, schedule.timezone, current)
                except ScheduleError as exc:
                    connection.execute(
                        """
                        UPDATE schedules
                        SET enabled = 0, next_run_at = NULL,
                            last_status = 'invalid', last_error = ?
                        WHERE id = ?
                        """,
                        (str(exc), schedule.id),
                    )
                    continue
                connection.execute(
                    "UPDATE schedules SET next_run_at = ?, last_status = 'running' WHERE id = ?",
                    (_to_iso(next_run), schedule.id),
                )
                claimed.append(DueSchedule(schedule=schedule, scheduled_at=scheduled_at))
        return claimed

    def record_result(
        self,
        schedule_id: int,
        *,
        success: bool,
        error: str | None = None,
        finished_at: datetime | None = None,
        max_failures: int = DEFAULT_CIRCUIT_BREAKER_MAX_FAILURES,
    ) -> bool:
        current = finished_at or datetime.now(UTC)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT consecutive_failures FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
            if row is None:
                return False
            failures = 0 if success else int(row["consecutive_failures"]) + 1
            auto_paused = not success and failures >= max_failures
            connection.execute(
                """
                UPDATE schedules
                SET last_run_at = ?, last_status = ?, last_error = ?,
                    run_count = run_count + 1, consecutive_failures = ?,
                    enabled = CASE WHEN ? THEN 0 ELSE enabled END,
                    next_run_at = CASE WHEN ? THEN NULL ELSE next_run_at END
                WHERE id = ?
                """,
                (
                    _to_iso(current),
                    "success" if success else "failed",
                    None if success else (error or "未知錯誤")[:2_000],
                    failures,
                    auto_paused,
                    auto_paused,
                    schedule_id,
                ),
            )
        return auto_paused
