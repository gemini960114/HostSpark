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


    async def test_reject_unauthorized_user(self) -> None:
        user = SimpleNamespace(id=999999999)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, message=message)
        rejected = await bot.reject_unauthorized(update)
        self.assertTrue(rejected)
        message.reply_text.assert_awaited_once_with("⛔ 您沒有權限使用此機器人。")

    async def test_start_command_authorized(self) -> None:
        user = SimpleNamespace(id=123456789)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, message=message)
        context = SimpleNamespace()
        await bot.start_command(update, context)
        message.reply_text.assert_awaited_once()
        sent_text = message.reply_text.await_args.args[0]
        self.assertIn("Antigravity CLI (agy) 助手在線中", sent_text)
        self.assertIn("Full（不逐次審核）", sent_text)

    async def test_schedule_help_command(self) -> None:
        user = SimpleNamespace(id=123456789)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, message=message)
        context = SimpleNamespace()
        await bot.schedule_help_command(update, context)
        message.reply_text.assert_awaited_once()
        sent_text = message.reply_text.await_args.args[0]
        self.assertIn("/schedule_add", sent_text)
        self.assertIn("Asia/Taipei", sent_text)

    async def test_schedule_add_cancel_flow(self) -> None:
        user = SimpleNamespace(id=123456789)
        status_msg = SimpleNamespace(edit_text=AsyncMock())
        msg = SimpleNamespace(
            text="/schedule_add 0 * * * * 檢查伺服器狀態",
            reply_text=AsyncMock(return_value=status_msg),
        )
        update = SimpleNamespace(effective_user=user, message=msg)
        app = SimpleNamespace(bot_data={})
        context = SimpleNamespace(application=app)

        with patch("bot.run_agy", AsyncMock(return_value=ProcessResult(0, "整理後的 prompt 模板", ""))):
            await bot.schedule_add_command(update, context)

        status_msg.edit_text.assert_awaited_once()
        preview_text = status_msg.edit_text.await_args.args[0]
        self.assertIn("整理後的 prompt 模板", preview_text)
        keyboard = status_msg.edit_text.await_args.kwargs["reply_markup"]
        cancel_btn = keyboard.inline_keyboard[0][1]
        cancel_token = cancel_btn.callback_data

        # Simulate clicking Cancel
        query = SimpleNamespace(
            from_user=user,
            data=cancel_token,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        cb_update = SimpleNamespace(callback_query=query)
        await bot.schedule_callback(cb_update, context)

        query.edit_message_text.assert_awaited_once_with("已取消建立排程。")
        self.assertEqual(bot.SCHEDULE_STORE.count(), 0)

    async def test_schedule_add_confirm_flow(self) -> None:
        user = SimpleNamespace(id=123456789)
        status_msg = SimpleNamespace(edit_text=AsyncMock())
        msg = SimpleNamespace(
            text="/schedule_add 0 9 * * * 每日系統健康檢查",
            reply_text=AsyncMock(return_value=status_msg),
        )
        update = SimpleNamespace(effective_user=user, message=msg)
        app = SimpleNamespace(bot_data={})
        context = SimpleNamespace(application=app)

        with patch("bot.run_agy", AsyncMock(return_value=ProcessResult(0, "整理後的每日檢查 prompt", ""))):
            await bot.schedule_add_command(update, context)

        status_msg.edit_text.assert_awaited_once()
        keyboard = status_msg.edit_text.await_args.kwargs["reply_markup"]
        confirm_btn = keyboard.inline_keyboard[0][0]
        confirm_token = confirm_btn.callback_data

        # Simulate clicking Confirm
        query = SimpleNamespace(
            from_user=user,
            data=confirm_token,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        cb_update = SimpleNamespace(callback_query=query)
        await bot.schedule_callback(cb_update, context)

        query.edit_message_text.assert_awaited_once()
        res_text = query.edit_message_text.await_args.args[0]
        self.assertIn("已建立排程 #1", res_text)
        self.assertEqual(bot.SCHEDULE_STORE.count(), 1)
        stored = bot.SCHEDULE_STORE.get(1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.cron_expr, "0 9 * * *")
        self.assertEqual(stored.prompt_template, "整理後的每日檢查 prompt")

    async def test_schedule_pause_resume_delete_commands(self) -> None:
        schedule = bot.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="Asia/Taipei",
            original_prompt="測試任務",
            prompt_template="測試模板",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        user = SimpleNamespace(id=123456789)
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, message=msg)
        context = SimpleNamespace(args=[str(schedule.id)])

        # Pause
        await bot.schedule_pause_command(update, context)
        msg.reply_text.assert_awaited_with(f"✅ 已暫停排程 #{schedule.id}。")
        self.assertFalse(bot.SCHEDULE_STORE.get(schedule.id).enabled)

        # Resume
        await bot.schedule_resume_command(update, context)
        msg.reply_text.assert_awaited_with(f"✅ 已恢復排程 #{schedule.id}。")
        self.assertTrue(bot.SCHEDULE_STORE.get(schedule.id).enabled)

        # Delete
        await bot.schedule_delete_command(update, context)
        msg.reply_text.assert_awaited_with(f"✅ 已刪除排程 #{schedule.id}。")
        self.assertIsNone(bot.SCHEDULE_STORE.get(schedule.id))

    async def test_three_consecutive_failures_triggers_auto_pause_notification(self) -> None:
        schedule = bot.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="會失敗的任務",
            prompt_template="執行會失敗的任務",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)

        # 1st failure
        due = bot.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 1, 0, tzinfo=UTC))[0]
        with patch("bot.run_agy", AsyncMock(return_value=ProcessResult(1, "", "錯誤1"))):
            await bot._execute_due_schedule(application, due)
        self.assertEqual(bot.SCHEDULE_STORE.get(schedule.id).consecutive_failures, 1)
        self.assertTrue(bot.SCHEDULE_STORE.get(schedule.id).enabled)

        # 2nd failure
        due = bot.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 2, 0, tzinfo=UTC))[0]
        with patch("bot.run_agy", AsyncMock(return_value=ProcessResult(1, "", "錯誤2"))):
            await bot._execute_due_schedule(application, due)
        self.assertEqual(bot.SCHEDULE_STORE.get(schedule.id).consecutive_failures, 2)
        self.assertTrue(bot.SCHEDULE_STORE.get(schedule.id).enabled)

        # 3rd failure
        due = bot.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 3, 0, tzinfo=UTC))[0]
        with patch("bot.run_agy", AsyncMock(return_value=ProcessResult(1, "", "錯誤3"))):
            await bot._execute_due_schedule(application, due)
        stored = bot.SCHEDULE_STORE.get(schedule.id)
        self.assertEqual(stored.consecutive_failures, 3)
        self.assertFalse(stored.enabled)
        # Should have sent failure report AND auto-pause notification
        self.assertGreaterEqual(len(telegram_bot.messages), 2)
        last_msg = telegram_bot.messages[-1]["text"]
        self.assertIn("已自動暫停", last_msg)


    async def test_safe_mode_permission_denied_in_schedule(self) -> None:
        bot.CONFIG = BotConfig(**{**bot.CONFIG.__dict__, "permission_mode": "safe"})
        schedule = bot.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="需要工具授權的任務",
            prompt_template="執行受限命令",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        due = bot.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 1, 0, tzinfo=UTC))[0]
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)
        denied_stderr = "Permission denied: --dangerously-skip-permissions is required to run in headless mode"
        with patch("bot.run_agy", AsyncMock(return_value=ProcessResult(1, "", denied_stderr))):
            await bot._execute_due_schedule(application, due)
        stored = bot.SCHEDULE_STORE.get(schedule.id)
        self.assertEqual(stored.consecutive_failures, 1)
        self.assertEqual(stored.last_status, "failed")
        self.assertIn("Permission denied", stored.last_error)

    async def test_handle_message_uses_configured_waiting_message(self) -> None:
        user = SimpleNamespace(id=123456789)
        status_msg = SimpleNamespace(
            delete=AsyncMock(),
            edit_text=AsyncMock(),
        )
        msg = SimpleNamespace(
            text="你好",
            reply_text=AsyncMock(return_value=status_msg),
        )
        update = SimpleNamespace(
            effective_user=user,
            effective_chat=SimpleNamespace(id=123456789),
            message=msg,
        )
        bot_mock = SimpleNamespace(send_chat_action=AsyncMock())
        context = SimpleNamespace(bot=bot_mock)

        result = ProcessResult(0, "你好！我是國網AI助理", "")
        with patch("bot.run_agy", AsyncMock(return_value=result)):
            await bot.handle_message(update, context)

        msg.reply_text.assert_awaited()
        first_call = msg.reply_text.await_args_list[0]
        self.assertEqual(first_call.args[0], bot.CONFIG.waiting_message)
        status_msg.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()


