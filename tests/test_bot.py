import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from agy_bot_core import BotConfig, ProcessResult
from schedule_store import NO_REPORT_SENTINEL, ScheduleStore


UTC = timezone.utc
VALID_TOKEN = f"{987654321}:{'A' * 25}"


class FakeTelegramBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class BotScheduleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        workdir = root / "primary"
        workdir.mkdir()
        self.previous_config = bot.CONFIG
        self.previous_store = bot.SCHEDULE_STORE
        bot.CONFIG = BotConfig(
            bot_token=VALID_TOKEN,
            allowed_user_id=123456789,
            agy_bin=Path(sys.executable),
            agy_workdir=workdir,
            permission_mode="full",
            rule_prompt="",
            timeout_seconds=30,
            max_output_bytes=4096,
            schedule_db_path=root / "state" / "schedules.db",
            schedule_timezone="Asia/Taipei",
            schedule_min_interval_minutes=15,
            schedule_max_tasks=20,
        )
        bot.SCHEDULE_STORE = ScheduleStore(bot.CONFIG.schedule_db_path)

    async def asyncTearDown(self) -> None:
        bot.CONFIG = self.previous_config
        bot.SCHEDULE_STORE = self.previous_store
        self.tempdir.cleanup()

    async def test_prompt_builder_never_receives_full_permission_flag(self) -> None:
        isolated = bot.CONFIG.schedule_db_path.parent / "workspaces" / "builder"
        isolated.mkdir(parents=True)
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("bot.run_process", mocked):
            await bot.run_agy(
                "rewrite this",
                continue_conversation=False,
                workdir=isolated,
                add_primary_workdir=True,
                allow_full_permissions=False,
            )
        args = mocked.await_args.args[0]
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertIn("--add-dir", args)
        self.assertEqual(mocked.await_args.kwargs["cwd"], isolated)

    async def test_scheduled_run_reports_and_records_success(self) -> None:
        schedule = bot.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="檢查狀態",
            prompt_template="第 {{run_number}} 次檢查",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        due = bot.SCHEDULE_STORE.claim_due(
            datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
        )[0]
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)
        result = ProcessResult(0, "狀態正常", "")
        with patch("bot.run_agy", AsyncMock(return_value=result)) as mocked:
            await bot._execute_due_schedule(application, due)

        prompt = mocked.await_args.args[0]
        self.assertIn("第 1 次檢查", prompt)
        self.assertEqual(len(telegram_bot.messages), 1)
        self.assertIn("狀態正常", telegram_bot.messages[0]["text"])
        stored = bot.SCHEDULE_STORE.get(schedule.id)
        self.assertEqual(stored.run_count, 1)
        self.assertEqual(stored.last_status, "success")

    async def test_no_report_sentinel_suppresses_telegram_message(self) -> None:
        bot.CONFIG = BotConfig(**{**bot.CONFIG.__dict__, "permission_mode": "safe"})
        schedule = bot.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="沒有異常不要通知",
            prompt_template="檢查後按規則輸出",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        due = bot.SCHEDULE_STORE.claim_due(
            datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
        )[0]
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)
        with patch(
            "bot.run_agy",
            AsyncMock(return_value=ProcessResult(0, NO_REPORT_SENTINEL, "")),
        ):
            await bot._execute_due_schedule(application, due)
        self.assertEqual(telegram_bot.messages, [])
        self.assertEqual(bot.SCHEDULE_STORE.get(schedule.id).last_status, "success")


if __name__ == "__main__":
    unittest.main()
