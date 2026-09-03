import asyncio
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import hostspark.state as state
from hostspark.config import BotConfig
from hostspark.core.executor import ProcessResult, run_agy, run_process
from hostspark.runtime.scheduler import (
    _execute_due_schedule,
    cleanup_expired_workspaces_and_uploads,
    schedule_add_command,
    schedule_delete_command,
    schedule_help_command,
    schedule_pause_command,
    schedule_resume_command,
)
from hostspark.storage.chat_state import ChatStateStore
from hostspark.storage.schedule_store import NO_REPORT_SENTINEL, ScheduleStore
from hostspark.telegram.auth import reject_unauthorized
from hostspark.telegram.dispatcher import (
    global_callback_query_handler,
    handle_message,
)
from hostspark.telegram.handlers import clear_command, new_command, start_command

UTC = timezone.utc
VALID_TOKEN = f"{987654321}:{'A' * 25}"


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    """Poll `predicate()` until it's truthy or `timeout` seconds pass.

    Needed because handle_message() now enqueues and returns immediately
    (see hostspark.telegram.dispatcher._enqueue_and_handle_prompt) instead of
    blocking until the background job finishes, so tests that assert on the
    job's eventual side effects have to wait for it explicitly.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for condition")
        await asyncio.sleep(0.02)


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
        self.previous_config = state.CONFIG
        self.previous_store = state.SCHEDULE_STORE
        self.previous_chat_store = state.CHAT_STATE_STORE
        cfg = BotConfig(
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
        state.CONFIG = cfg
        sched_store = ScheduleStore(cfg.schedule_db_path)
        chat_store = ChatStateStore(cfg.state_db_path)
        state.SCHEDULE_STORE = sched_store
        state.CHAT_STATE_STORE = chat_store

    async def asyncTearDown(self) -> None:
        state.CONFIG = self.previous_config
        state.SCHEDULE_STORE = self.previous_store
        state.CHAT_STATE_STORE = self.previous_chat_store
        state.PENDING_PROJECT_INIT.clear()
        self.tempdir.cleanup()

    async def test_prompt_builder_never_receives_full_permission_flag(self) -> None:
        isolated = state.CONFIG.schedule_db_path.parent / "workspaces" / "builder"
        isolated.mkdir(parents=True)
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy(
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

    async def test_switching_project_dir_makes_next_run_pass_new_project_once(self) -> None:
        # agy doesn't treat a directory as its active project just because
        # cwd points at it -- switch_project_dir() must mark the chat so the
        # very next run_agy() call adds --new-project, and only that one.
        chat_id = 559
        state.CHAT_STATE_STORE.update(chat_id, workspace_dir="fresh-project")
        state.PENDING_PROJECT_INIT.add(chat_id)
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=chat_id, continue_conversation=False)
        self.assertIn("--new-project", mocked.await_args.args[0])
        self.assertNotIn(chat_id, state.PENDING_PROJECT_INIT)

        mocked.reset_mock()
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi again", chat_id=chat_id, continue_conversation=False)
        self.assertNotIn("--new-project", mocked.await_args.args[0])

    async def test_switch_project_dir_marks_chat_pending(self) -> None:
        from hostspark.core.workspace import switch_project_dir

        chat_id = 560
        (state.CONFIG.workspace_root / "another-project").mkdir(parents=True, exist_ok=True)
        state.PENDING_PROJECT_INIT.discard(chat_id)
        switch_project_dir(chat_id, "another-project")
        self.assertIn(chat_id, state.PENDING_PROJECT_INIT)

    async def test_executor_uses_selected_workspace_dir_without_extra_add_dir(self) -> None:
        chat_id = 557
        state.CHAT_STATE_STORE.update(chat_id, workspace_dir="my-project")
        expected_cwd = state.CONFIG.workspace_root / "my-project"
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=chat_id, continue_conversation=False)
        args = mocked.await_args.args[0]
        self.assertEqual(mocked.await_args.kwargs["cwd"], expected_cwd)
        self.assertTrue(expected_cwd.is_dir())
        # A deliberately-chosen project dir should NOT also expose
        # config.agy_workdir via an extra --add-dir.
        self.assertNotIn(str(state.CONFIG.agy_workdir), args)

    async def test_executor_falls_back_to_anonymous_workdir_without_workspace_dir(self) -> None:
        # A chat that has never used /new must be completely unaffected.
        chat_id = 558
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=chat_id, continue_conversation=False)
        args = mocked.await_args.args[0]
        cwd = mocked.await_args.kwargs["cwd"]
        self.assertEqual(cwd, state.CONFIG.state_db_path.parent / "workspaces" / f"chat-{chat_id}")
        self.assertIn("--add-dir", args)

    async def test_new_command_with_name_creates_and_switches_project_dir(self) -> None:
        chat_id = 601
        state.CHAT_STATE_STORE.update(chat_id, conversation_id="old-conv", add_dirs=["/some/other/dir"])
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456789),
            effective_chat=SimpleNamespace(id=chat_id),
            message=msg,
        )
        context = SimpleNamespace(args=["hmp-web"])
        await new_command(update, context)

        target = state.CONFIG.workspace_root / "hmp-web"
        self.assertTrue(target.is_dir())
        settings = state.CHAT_STATE_STORE.get_or_create(chat_id)
        self.assertEqual(settings.workspace_dir, "hmp-web")
        self.assertIsNone(settings.conversation_id)
        self.assertEqual(settings.add_dirs, ())  # cleared on real switch
        msg.reply_text.assert_awaited_once()
        self.assertIn("hmp-web", msg.reply_text.await_args.args[0])

    async def test_new_command_rejects_invalid_name(self) -> None:
        chat_id = 602
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456789),
            effective_chat=SimpleNamespace(id=chat_id),
            message=msg,
        )
        for bad_name in ["../../etc", "uploads", "has space"]:
            msg.reply_text.reset_mock()
            await new_command(update, SimpleNamespace(args=[bad_name]))
            msg.reply_text.assert_awaited_once()
            self.assertTrue(msg.reply_text.await_args.args[0].startswith("❌"))
        self.assertIsNone(state.CHAT_STATE_STORE.get_or_create(chat_id).workspace_dir)

    async def test_new_command_without_args_lists_existing_dirs_and_marks_current(self) -> None:
        chat_id = 603
        (state.CONFIG.workspace_root / "already-there").mkdir(parents=True, exist_ok=True)
        (state.CONFIG.workspace_root / "uploads").mkdir(parents=True, exist_ok=True)
        state.CHAT_STATE_STORE.update(chat_id, workspace_dir="already-there")
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456789),
            effective_chat=SimpleNamespace(id=chat_id),
            message=msg,
        )
        await new_command(update, SimpleNamespace(args=[]))
        msg.reply_text.assert_awaited_once()
        keyboard = msg.reply_text.await_args.kwargs["reply_markup"]
        labels = [row[0].text for row in keyboard.inline_keyboard]
        self.assertEqual(len(labels), 1)  # "uploads" excluded
        self.assertIn("already-there", labels[0])
        self.assertTrue(labels[0].startswith("✅"))

    async def test_workdir_sel_callback_switches_project_dir(self) -> None:
        chat_id = 604
        (state.CONFIG.workspace_root / "picked-project").mkdir(parents=True, exist_ok=True)
        state.CHAT_STATE_STORE.update(chat_id, conversation_id="old-conv")
        user = SimpleNamespace(id=123456789)
        chat = SimpleNamespace(id=chat_id, type="private")
        query = SimpleNamespace(
            from_user=user,
            data="workdir_sel:picked-project",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat=chat),
        )
        update = SimpleNamespace(callback_query=query, effective_chat=chat, effective_user=user)
        await global_callback_query_handler(update, SimpleNamespace())
        settings = state.CHAT_STATE_STORE.get_or_create(chat_id)
        self.assertEqual(settings.workspace_dir, "picked-project")
        self.assertIsNone(settings.conversation_id)
        query.edit_message_text.assert_awaited_once()

    async def test_clear_command_only_resets_conversation_id(self) -> None:
        chat_id = 605
        state.CHAT_STATE_STORE.update(
            chat_id, conversation_id="old-conv", workspace_dir="my-project", add_dirs=["/keep/me"]
        )
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456789),
            effective_chat=SimpleNamespace(id=chat_id),
            message=msg,
        )
        await clear_command(update, SimpleNamespace())
        settings = state.CHAT_STATE_STORE.get_or_create(chat_id)
        self.assertIsNone(settings.conversation_id)
        # /clear must NOT touch workspace_dir or add_dirs — only /new does.
        self.assertEqual(settings.workspace_dir, "my-project")
        self.assertEqual(settings.add_dirs, ("/keep/me",))

    async def test_reselecting_same_workspace_dir_keeps_add_dirs(self) -> None:
        chat_id = 606
        (state.CONFIG.workspace_root / "same-project").mkdir(parents=True, exist_ok=True)
        state.CHAT_STATE_STORE.update(chat_id, workspace_dir="same-project", add_dirs=["/keep/me"])
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123456789),
            effective_chat=SimpleNamespace(id=chat_id),
            message=msg,
        )
        await new_command(update, SimpleNamespace(args=["same-project"]))
        settings = state.CHAT_STATE_STORE.get_or_create(chat_id)
        self.assertEqual(settings.add_dirs, ("/keep/me",))

    async def test_effort_flag_omitted_for_models_with_baked_in_effort(self) -> None:
        chat_id = 555
        state.CHAT_STATE_STORE.update(chat_id, model="gemini-3.7-flash-medium", effort="high")
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=chat_id, continue_conversation=False)
        args = mocked.await_args.args[0]
        self.assertIn("gemini-3.7-flash-medium", args)
        self.assertNotIn("--effort", args)

    async def test_effort_flag_kept_for_models_without_baked_in_effort(self) -> None:
        chat_id = 556
        state.CHAT_STATE_STORE.update(chat_id, model="claude-sonnet-4-6", effort="high")
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=chat_id, continue_conversation=False)
        args = mocked.await_args.args[0]
        self.assertIn("--effort", args)
        self.assertIn("high", args)

    async def test_each_chat_gets_its_own_dedicated_workdir(self) -> None:
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=901, continue_conversation=True)
        cwd_a = mocked.await_args.kwargs["cwd"]

        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=902, continue_conversation=True)
        cwd_b = mocked.await_args.kwargs["cwd"]

        self.assertNotEqual(cwd_a, cwd_b)
        self.assertNotEqual(cwd_a, state.CONFIG.agy_workdir)
        self.assertNotEqual(cwd_b, state.CONFIG.agy_workdir)
        args_a = mocked.await_args.args[0]
        self.assertIn(str(state.CONFIG.agy_workdir), args_a)

    async def test_explicit_workdir_override_is_respected(self) -> None:
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        custom = state.CONFIG.schedule_db_path.parent / "workspaces" / "schedule-1"
        custom.mkdir(parents=True, exist_ok=True)
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy(
                "hi", chat_id=903, continue_conversation=False, workdir=custom
            )
        self.assertEqual(mocked.await_args.kwargs["cwd"], custom)

    async def test_pending_context_is_injected_once_then_consumed(self) -> None:
        chat_id = 904
        state.queue_context_injection(chat_id, "剩餘配額：42%")
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))

        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("解說使用量", chat_id=chat_id, continue_conversation=True)
        first_prompt = mocked.await_args.args[0][2]
        self.assertIn("剩餘配額：42%", first_prompt)
        self.assertIn("解說使用量", first_prompt)

        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("再問一次", chat_id=chat_id, continue_conversation=True)
        second_prompt = mocked.await_args.args[0][2]
        self.assertNotIn("剩餘配額：42%", second_prompt)

    async def test_pending_context_expires_after_ttl(self) -> None:
        chat_id = 905
        state.queue_context_injection(chat_id, "過期的報告內容")
        _queued_at, report_text = state._PENDING_CONTEXT[chat_id]
        state._PENDING_CONTEXT[chat_id] = (
            asyncio.get_event_loop().time() - state._PENDING_CONTEXT_TTL_SECONDS - 1,
            report_text,
        )
        mocked = AsyncMock(return_value=ProcessResult(0, "ok", ""))
        with patch("hostspark.core.executor.run_process", mocked):
            await run_agy("hi", chat_id=chat_id, continue_conversation=True)
        prompt = mocked.await_args.args[0][2]
        self.assertNotIn("過期的報告內容", prompt)

    async def test_cleanup_expired_workspaces_and_uploads(self) -> None:
        ws_root = state.CONFIG.workspace_root
        uploads_dir = ws_root / "uploads" / "chat_123"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        expired_file = uploads_dir / "old_image.png"
        expired_file.write_text("old image bytes")

        fresh_file = uploads_dir / "new_image.png"
        fresh_file.write_text("new image bytes")

        sched_ws = state.CONFIG.schedule_db_path.parent / "workspaces" / "schedule-99"
        sched_ws.mkdir(parents=True, exist_ok=True)
        expired_sched_file = sched_ws / "old_report.txt"
        expired_sched_file.write_text("old report")

        now = time.time()
        forty_days_ago = now - (40 * 86400)
        os.utime(expired_file, (forty_days_ago, forty_days_ago))
        os.utime(expired_sched_file, (forty_days_ago, forty_days_ago))

        deleted = cleanup_expired_workspaces_and_uploads(
            workspace_root=ws_root,
            state_dir=state.CONFIG.state_db_path,
            schedule_db_path=state.CONFIG.schedule_db_path,
            max_age_days=30,
        )
        self.assertEqual(deleted, 2)
        self.assertFalse(expired_file.exists())
        self.assertFalse(expired_sched_file.exists())
        self.assertTrue(fresh_file.exists())

    async def test_multi_admin_schedule_broadcast(self) -> None:
        object.__setattr__(state.CONFIG, "allowed_user_ids", frozenset({1001, 1002}))
        schedule = state.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="多管理員檢查",
            prompt_template="檢查狀態",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        due = state.SCHEDULE_STORE.claim_due(
            datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
        )[0]
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)
        result = ProcessResult(0, "巡檢正常", "")
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=result)):
            await _execute_due_schedule(application, due)

        recipient_ids = {m["chat_id"] for m in telegram_bot.messages}
        self.assertEqual(recipient_ids, {1001, 1002})

    async def test_scheduled_run_reports_and_records_success(self) -> None:
        schedule = state.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="檢查狀態",
            prompt_template="第 {{run_number}} 次檢查",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        due = state.SCHEDULE_STORE.claim_due(
            datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
        )[0]
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)
        result = ProcessResult(0, "狀態正常", "")
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=result)) as mocked:
            await _execute_due_schedule(application, due)

        prompt = mocked.await_args.args[0]
        self.assertIn("第 1 次檢查", prompt)
        self.assertEqual(len(telegram_bot.messages), 1)
        self.assertIn("狀態正常", telegram_bot.messages[0]["text"])
        stored = state.SCHEDULE_STORE.get(schedule.id)
        self.assertEqual(stored.run_count, 1)
        self.assertEqual(stored.last_status, "success")

    async def test_no_report_sentinel_suppresses_telegram_message(self) -> None:
        state.CONFIG = BotConfig(**{**state.CONFIG.__dict__, "permission_mode": "safe"})
        schedule = state.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="沒有異常不要通知",
            prompt_template="檢查後按規則輸出",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        due = state.SCHEDULE_STORE.claim_due(
            datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
        )[0]
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)
        with patch(
            "hostspark.core.executor.run_agy",
            AsyncMock(return_value=ProcessResult(0, NO_REPORT_SENTINEL, "")),
        ):
            await _execute_due_schedule(application, due)
        self.assertEqual(telegram_bot.messages, [])
        self.assertEqual(state.SCHEDULE_STORE.get(schedule.id).last_status, "success")

    async def test_reject_unauthorized_user(self) -> None:
        user = SimpleNamespace(id=999999999)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, message=message)
        rejected = await reject_unauthorized(update)
        self.assertTrue(rejected)
        message.reply_text.assert_awaited_once_with("⛔ 您沒有權限使用此機器人。")

    async def test_start_command_authorized(self) -> None:
        user = SimpleNamespace(id=123456789)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, message=message)
        context = SimpleNamespace()
        await start_command(update, context)
        message.reply_text.assert_awaited_once()
        sent_text = message.reply_text.await_args.args[0]
        self.assertIn(f"{state.CONFIG.bot_name} 在線中", sent_text)
        self.assertIn("Full（不逐次審核）", sent_text)

    async def test_schedule_help_command(self) -> None:
        user = SimpleNamespace(id=123456789)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, message=message)
        context = SimpleNamespace()
        await schedule_help_command(update, context)
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

        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=ProcessResult(0, "整理後的 prompt 模板", ""))):
            await schedule_add_command(update, context)

        status_msg.edit_text.assert_awaited_once()
        preview_text = status_msg.edit_text.await_args.args[0]
        self.assertIn("整理後的 prompt 模板", preview_text)
        keyboard = status_msg.edit_text.await_args.kwargs["reply_markup"]
        cancel_btn = keyboard.inline_keyboard[0][1]
        cancel_token = cancel_btn.callback_data

        query = SimpleNamespace(
            from_user=user,
            data=cancel_token,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        cb_update = SimpleNamespace(callback_query=query)
        await global_callback_query_handler(cb_update, context)

        query.edit_message_text.assert_awaited_once_with("已取消建立排程。")
        self.assertEqual(state.SCHEDULE_STORE.count(), 0)

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

        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=ProcessResult(0, "整理後的每日檢查 prompt", ""))):
            await schedule_add_command(update, context)

        status_msg.edit_text.assert_awaited_once()
        keyboard = status_msg.edit_text.await_args.kwargs["reply_markup"]
        confirm_btn = keyboard.inline_keyboard[0][0]
        confirm_token = confirm_btn.callback_data

        query = SimpleNamespace(
            from_user=user,
            data=confirm_token,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        cb_update = SimpleNamespace(callback_query=query)
        await global_callback_query_handler(cb_update, context)

        query.edit_message_text.assert_awaited_once()
        res_text = query.edit_message_text.await_args.args[0]
        self.assertIn("已建立排程 #1", res_text)
        self.assertEqual(state.SCHEDULE_STORE.count(), 1)
        stored = state.SCHEDULE_STORE.get(1)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.cron_expr, "0 9 * * *")
        self.assertEqual(stored.prompt_template, "整理後的每日檢查 prompt")

    async def test_schedule_pause_resume_delete_commands(self) -> None:
        schedule = state.SCHEDULE_STORE.add(
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

        await schedule_pause_command(update, context)
        msg.reply_text.assert_awaited_with(f"✅ 已暫停排程 #{schedule.id}。")
        self.assertFalse(state.SCHEDULE_STORE.get(schedule.id).enabled)

        await schedule_resume_command(update, context)
        msg.reply_text.assert_awaited_with(f"✅ 已恢復排程 #{schedule.id}。")
        self.assertTrue(state.SCHEDULE_STORE.get(schedule.id).enabled)

        await schedule_delete_command(update, context)
        msg.reply_text.assert_awaited_with(f"✅ 已刪除排程 #{schedule.id}。")
        self.assertIsNone(state.SCHEDULE_STORE.get(schedule.id))

    async def test_three_consecutive_failures_triggers_auto_pause_notification(self) -> None:
        schedule = state.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="會失敗的任務",
            prompt_template="執行會失敗的任務",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)

        due = state.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 1, 0, tzinfo=UTC))[0]
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=ProcessResult(1, "", "錯誤1"))):
            await _execute_due_schedule(application, due)
        self.assertEqual(state.SCHEDULE_STORE.get(schedule.id).consecutive_failures, 1)
        self.assertTrue(state.SCHEDULE_STORE.get(schedule.id).enabled)

        due = state.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 2, 0, tzinfo=UTC))[0]
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=ProcessResult(1, "", "錯誤2"))):
            await _execute_due_schedule(application, due)
        self.assertEqual(state.SCHEDULE_STORE.get(schedule.id).consecutive_failures, 2)
        self.assertTrue(state.SCHEDULE_STORE.get(schedule.id).enabled)

        due = state.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 3, 0, tzinfo=UTC))[0]
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=ProcessResult(1, "", "錯誤3"))):
            await _execute_due_schedule(application, due)
        stored = state.SCHEDULE_STORE.get(schedule.id)
        self.assertEqual(stored.consecutive_failures, 3)
        self.assertFalse(stored.enabled)
        self.assertGreaterEqual(len(telegram_bot.messages), 2)
        last_msg = telegram_bot.messages[-1]["text"]
        self.assertIn("已自動暫停", last_msg)

    async def test_safe_mode_permission_denied_in_schedule(self) -> None:
        state.CONFIG = BotConfig(**{**state.CONFIG.__dict__, "permission_mode": "safe"})
        schedule = state.SCHEDULE_STORE.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="需要工具授權的任務",
            prompt_template="執行受限命令",
            now=datetime(2026, 8, 30, 0, 0, tzinfo=UTC),
        )
        due = state.SCHEDULE_STORE.claim_due(datetime(2026, 8, 30, 1, 0, tzinfo=UTC))[0]
        telegram_bot = FakeTelegramBot()
        application = SimpleNamespace(bot=telegram_bot)
        denied_stderr = "Permission denied: --dangerously-skip-permissions is required to run in headless mode"
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=ProcessResult(1, "", denied_stderr))):
            await _execute_due_schedule(application, due)
        stored = state.SCHEDULE_STORE.get(schedule.id)
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

        object.__setattr__(state.CONFIG, "progress_mode", "compact")
        result = ProcessResult(0, "你好！我是國網AI助理", "")
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=result)):
            await handle_message(update, context)
            # handle_message no longer blocks until the job finishes (it just
            # enqueues and returns so python-telegram-bot can dispatch other
            # updates, e.g. /cancel, concurrently); wait for the background
            # worker to actually finish this job before asserting on it.
            await _wait_until(lambda: status_msg.edit_text.await_count > 0)

        msg.reply_text.assert_awaited()
        first_call = msg.reply_text.await_args_list[0]
        self.assertEqual(first_call.args[0], state.CONFIG.waiting_message)
        status_msg.edit_text.assert_awaited_with("✅ 執行完成。")

        object.__setattr__(state.CONFIG, "progress_mode", "delete")
        status_msg.delete.reset_mock()
        with patch("hostspark.core.executor.run_agy", AsyncMock(return_value=result)):
            await handle_message(update, context)
            await _wait_until(lambda: status_msg.delete.await_count > 0)
        status_msg.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
