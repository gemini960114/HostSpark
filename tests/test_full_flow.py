import asyncio
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import hostspark.state as state
from hostspark.config import BotConfig
from hostspark.core.executor import ProcessResult
from hostspark.storage.chat_state import ChatStateStore
from hostspark.storage.schedule_store import ScheduleStore
from hostspark.telegram.app import build_application
from hostspark.telegram.auth import reject_unauthorized
from hostspark.telegram.dispatcher import global_callback_query_handler
from hostspark.telegram.handlers import (
    agents_command,
    agy_command,
    agy_confirm_command,
    changelog_command,
    clear_command,
    disable_slash_commands_command,
    effort_command,
    json_schema_command,
    log_file_command,
    mode_command,
    model_command,
    new_project_command,
    output_format_command,
    plugins_command,
    print_timeout_command,
    restart_command,
    sandbox_command,
    session_command,
    setdefault_command,
    update_command,
    verbose_command,
    version_command,
)
from hostspark.telegram.media import fetch_ssrf_safe_media

UTC = timezone.utc
VALID_TOKEN = f"{987654321}:{'A' * 25}"


class FullFlowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.state_db = self.root / "state" / "chat_state.db"
        self.sched_db = self.root / "state" / "schedules.db"
        self.conv_db = self.root / "state" / "conversations.db"
        self.env_file = self.root / ".env"

        self.previous_config = state.CONFIG
        self.previous_store = state.SCHEDULE_STORE
        self.previous_chat_store = state.CHAT_STATE_STORE
        self.previous_env_path = state.ENV_PATH

        state.ENV_PATH = self.env_file
        cfg = BotConfig(
            bot_token=VALID_TOKEN,
            allowed_user_ids=frozenset({1001, 1002}),
            allowed_chat_ids=frozenset({2001, 2002}),
            agy_bin=Path(sys.executable),
            agy_workdir=self.workdir,
            workspace_root=self.workdir,
            permission_mode="full",
            rule_prompt="",
            timeout_seconds=30,
            max_output_bytes=4096,
            schedule_db_path=self.sched_db,
            state_db_path=self.state_db,
            conversation_db_path=self.conv_db,
            schedule_timezone="Asia/Taipei",
            schedule_min_interval_minutes=15,
            schedule_max_tasks=20,
            allowed_models=("gemini-2.5-pro", "claude-3-5-sonnet"),
            private_only=False,
            allow_bot_update=True,
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
        state.ENV_PATH = self.previous_env_path
        self.tempdir.cleanup()

    def test_build_application_handlers(self) -> None:
        app = build_application(state.CONFIG)
        self.assertIsNotNone(app)
        self.assertGreater(len(app.handlers[0]), 20)

    async def test_auth_multi_user_and_chat(self) -> None:
        u_ok = SimpleNamespace(id=1001)
        c_ok = SimpleNamespace(id=2001, type="group")
        msg = SimpleNamespace(reply_text=AsyncMock())
        up_ok = SimpleNamespace(effective_user=u_ok, effective_chat=c_ok, message=msg)
        self.assertFalse(await reject_unauthorized(up_ok))

        u_bad = SimpleNamespace(id=9999)
        up_bad_user = SimpleNamespace(effective_user=u_bad, effective_chat=c_ok, message=msg)
        self.assertTrue(await reject_unauthorized(up_bad_user))

        c_bad = SimpleNamespace(id=9999, type="group")
        up_bad_chat = SimpleNamespace(effective_user=u_ok, effective_chat=c_bad, message=msg)
        self.assertTrue(await reject_unauthorized(up_bad_chat))

    async def test_session_settings_flow(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)

        # 1. /model command with argument
        ctx_model = SimpleNamespace(args=["gemini-2.5-pro"])
        await model_command(update, ctx_model)
        msg.reply_text.assert_awaited()
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).model, "gemini-2.5-pro")

        # 2. /effort command
        ctx_effort = SimpleNamespace(args=["medium"])
        await effort_command(update, ctx_effort)
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).effort, "medium")

        # 3. /mode command
        ctx_mode = SimpleNamespace(args=["accept-edits"])
        await mode_command(update, ctx_mode)
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).mode, "accept-edits")

        # 4. /sandbox command
        ctx_sandbox = SimpleNamespace(args=["off"])
        await sandbox_command(update, ctx_sandbox)
        self.assertFalse(state.CHAT_STATE_STORE.get_or_create(2001).sandbox)

        # 5. /verbose command
        ctx_verbose = SimpleNamespace(args=["detailed"])
        await verbose_command(update, ctx_verbose)
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).verbose, "detailed")

        # 6. /session command
        await session_command(update, SimpleNamespace())
        last_call_text = msg.reply_text.await_args.args[0]
        self.assertIn("gemini-2.5-pro", last_call_text)
        self.assertIn("medium", last_call_text)
        self.assertIn("accept-edits", last_call_text)

        # 7. /clear command — pure "reset conversation, keep everything else"
        # (this is what plain /new used to do before it grew a project-dir
        # picker; /new's own behavior is covered in tests/test_bot.py)
        state.CHAT_STATE_STORE.update(2001, conversation_id="some-uuid")
        self.assertIsNotNone(state.CHAT_STATE_STORE.get_or_create(2001).conversation_id)
        await clear_command(update, SimpleNamespace())
        self.assertIsNone(state.CHAT_STATE_STORE.get_or_create(2001).conversation_id)

    async def test_extended_chat_state_commands(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)

        # /output_format
        await output_format_command(update, SimpleNamespace(args=["json"]))
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).output_format, "json")

        # /json_schema
        await json_schema_command(update, SimpleNamespace(args=["schemas/my_schema.json"]))
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).json_schema, "schemas/my_schema.json")

        # /log_file
        await log_file_command(update, SimpleNamespace(args=["/tmp/test.log"]))
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).log_file, "/tmp/test.log")

        # /print_timeout
        await print_timeout_command(update, SimpleNamespace(args=["15m"]))
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).print_timeout, "15m")

        # /new_project
        await new_project_command(update, SimpleNamespace(args=["on"]))
        self.assertTrue(state.CHAT_STATE_STORE.get_or_create(2001).new_project)

        # /disable_slash_commands
        await disable_slash_commands_command(update, SimpleNamespace(args=["1"]))
        self.assertTrue(state.CHAT_STATE_STORE.get_or_create(2001).disable_slash_commands)

    async def test_model_picker_callback(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")

        query_model = SimpleNamespace(
            from_user=user,
            data="model_sel:claude-3-5-sonnet",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat=chat),
        )
        up_model = SimpleNamespace(callback_query=query_model, effective_chat=chat, effective_user=user)
        await global_callback_query_handler(up_model, SimpleNamespace())
        self.assertEqual(state.CHAT_STATE_STORE.get_or_create(2001).model, "claude-3-5-sonnet")

    async def test_setdefault_flow_and_defaults_inheritance(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        status_msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=status_msg)

        state.CHAT_STATE_STORE.update(2001, model="gemini-2.5-pro", effort="medium", mode="accept-edits", sandbox=False, verbose="detailed")

        await setdefault_command(update, SimpleNamespace())
        status_msg.reply_text.assert_awaited()
        keyboard = status_msg.reply_text.await_args.kwargs["reply_markup"]
        confirm_data = keyboard.inline_keyboard[0][0].callback_data

        query = SimpleNamespace(
            from_user=user,
            data=confirm_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat=chat),
        )
        up_cb = SimpleNamespace(callback_query=query, effective_chat=chat, effective_user=user)
        await global_callback_query_handler(up_cb, SimpleNamespace())
        query.edit_message_text.assert_awaited_with("✅ 已成功將設定寫回 `.env` 全域預設值！")

        self.assertEqual(state.CONFIG.default_model, "gemini-2.5-pro")
        self.assertEqual(state.CONFIG.default_effort, "medium")
        self.assertEqual(state.CONFIG.default_mode, "accept-edits")
        self.assertFalse(state.CONFIG.default_sandbox)
        self.assertEqual(state.CONFIG.default_verbose, "detailed")

        new_chat_settings = state.CHAT_STATE_STORE.get_or_create(
            2002,
            defaults={
                "model": state.CONFIG.default_model,
                "effort": state.CONFIG.default_effort,
                "mode": state.CONFIG.default_mode,
                "sandbox": state.CONFIG.default_sandbox,
                "verbose": state.CONFIG.default_verbose,
            }
        )
        self.assertEqual(new_chat_settings.model, "gemini-2.5-pro")
        self.assertEqual(new_chat_settings.effort, "medium")
        self.assertEqual(new_chat_settings.mode, "accept-edits")
        self.assertFalse(new_chat_settings.sandbox)
        self.assertEqual(new_chat_settings.verbose, "detailed")

    async def test_restart_and_update_commands(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        status_msg = SimpleNamespace(edit_text=AsyncMock())
        msg = SimpleNamespace(reply_text=AsyncMock(return_value=status_msg))
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)

        object.__setattr__(state.CONFIG, "allow_bot_update", False)
        await restart_command(update, SimpleNamespace())
        msg.reply_text.assert_awaited_with("❌ 未啟用遠端重啟。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")

        await update_command(update, SimpleNamespace())
        msg.reply_text.assert_awaited_with("❌ 未啟用遠端更新。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")

        object.__setattr__(state.CONFIG, "allow_bot_update", True)
        with patch("hostspark.core.executor.run_process", AsyncMock(return_value=ProcessResult(0, "Already up to date.", ""))):
            await update_command(update, SimpleNamespace())
            status_msg.edit_text.assert_awaited()
            self.assertIn("更新成功", status_msg.edit_text.await_args.args[0])

    async def test_agy_passthrough_and_confirm(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        reply_msg = SimpleNamespace(
            reply_text=AsyncMock(),
            delete=AsyncMock(),
        )
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=reply_msg)

        ctx_i = SimpleNamespace(args=["-i"])
        await agy_command(update, ctx_i)
        reply_msg.reply_text.assert_awaited()
        self.assertIn("禁止使用互動模式", reply_msg.reply_text.await_args.args[0])

        ctx_i2 = SimpleNamespace(args=["--prompt-interactive=true", "-p", "hi"])
        await agy_command(update, ctx_i2)
        self.assertIn("禁止使用互動模式", reply_msg.reply_text.await_args.args[0])

        ctx_dang = SimpleNamespace(args=["plugin", "install", "test-plugin"])
        await agy_command(update, ctx_dang)
        dang_text = reply_msg.reply_text.await_args.args[0]
        self.assertIn("潛在風險", dang_text)
        keyboard = reply_msg.reply_text.await_args.kwargs["reply_markup"]
        confirm_token = keyboard.inline_keyboard[0][0].callback_data.split(":", 1)[1]

        ctx_conf = SimpleNamespace(args=[confirm_token])
        with patch("hostspark.core.executor.run_process", AsyncMock(return_value=ProcessResult(0, "Plugin installed", ""))):
            await agy_confirm_command(update, ctx_conf)

    async def test_readonly_tools(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)

        with patch("hostspark.core.executor.run_process", AsyncMock(return_value=ProcessResult(0, "mock output", ""))):
            await agents_command(update, SimpleNamespace())
            await changelog_command(update, SimpleNamespace())
            await plugins_command(update, SimpleNamespace())
            await version_command(update, SimpleNamespace())

    async def test_fetch_ssrf_safe_media_blocks_private_ip(self) -> None:
        res_local = await fetch_ssrf_safe_media("http://127.0.0.1/test.png")
        self.assertIsNone(res_local)

        res_meta = await fetch_ssrf_safe_media("http://169.254.169.254/latest/meta-data")
        self.assertIsNone(res_meta)


if __name__ == "__main__":
    unittest.main()
