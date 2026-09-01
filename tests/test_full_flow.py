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

import bot
from agy_bot_core import BotConfig, ProcessResult
from chat_state import ChatStateStore
from media_resolver import fetch_ssrf_safe_media
from schedule_store import ScheduleStore


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

        self.previous_config = bot.CONFIG
        self.previous_store = bot.SCHEDULE_STORE
        self.previous_chat_store = bot.CHAT_STATE_STORE
        self.previous_env_path = bot.ENV_PATH

        bot.ENV_PATH = self.env_file
        bot.CONFIG = BotConfig(
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
        bot.SCHEDULE_STORE = ScheduleStore(bot.CONFIG.schedule_db_path)
        bot.CHAT_STATE_STORE = ChatStateStore(bot.CONFIG.state_db_path)

    async def asyncTearDown(self) -> None:
        bot.CONFIG = self.previous_config
        bot.SCHEDULE_STORE = self.previous_store
        bot.CHAT_STATE_STORE = self.previous_chat_store
        bot.ENV_PATH = self.previous_env_path
        self.tempdir.cleanup()

    def test_build_application_handlers(self) -> None:
        # Build real Application with python-telegram-bot to verify all registered CommandHandlers
        app = bot.build_application(bot.CONFIG)
        self.assertIsNotNone(app)
        self.assertGreater(len(app.handlers[0]), 20)

    async def test_auth_multi_user_and_chat(self) -> None:
        # Authorized user & chat
        u_ok = SimpleNamespace(id=1001)
        c_ok = SimpleNamespace(id=2001, type="group")
        msg = SimpleNamespace(reply_text=AsyncMock())
        up_ok = SimpleNamespace(effective_user=u_ok, effective_chat=c_ok, message=msg)
        self.assertFalse(await bot.reject_unauthorized(up_ok))

        # Unauthorized user
        u_bad = SimpleNamespace(id=9999)
        up_bad_user = SimpleNamespace(effective_user=u_bad, effective_chat=c_ok, message=msg)
        self.assertTrue(await bot.reject_unauthorized(up_bad_user))

        # Unauthorized chat
        c_bad = SimpleNamespace(id=9999, type="group")
        up_bad_chat = SimpleNamespace(effective_user=u_ok, effective_chat=c_bad, message=msg)
        self.assertTrue(await bot.reject_unauthorized(up_bad_chat))

    async def test_session_settings_flow(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)

        # 1. /model command with argument
        ctx_model = SimpleNamespace(args=["gemini-2.5-pro"])
        await bot.model_command(update, ctx_model)
        msg.reply_text.assert_awaited()
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).model, "gemini-2.5-pro")

        # 2. /effort command
        ctx_effort = SimpleNamespace(args=["medium"])
        await bot.effort_command(update, ctx_effort)
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).effort, "medium")

        # 3. /mode command
        ctx_mode = SimpleNamespace(args=["accept-edits"])
        await bot.mode_command(update, ctx_mode)
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).mode, "accept-edits")

        # 4. /sandbox command
        ctx_sandbox = SimpleNamespace(args=["off"])
        await bot.sandbox_command(update, ctx_sandbox)
        self.assertFalse(bot.CHAT_STATE_STORE.get_or_create(2001).sandbox)

        # 5. /verbose command
        ctx_verbose = SimpleNamespace(args=["detailed"])
        await bot.verbose_command(update, ctx_verbose)
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).verbose, "detailed")

        # 6. /session command
        await bot.session_command(update, SimpleNamespace())
        last_call_text = msg.reply_text.await_args.args[0]
        self.assertIn("gemini-2.5-pro", last_call_text)
        self.assertIn("medium", last_call_text)
        self.assertIn("accept-edits", last_call_text)

        # 7. /new command
        bot.CHAT_STATE_STORE.update(2001, conversation_id="some-uuid")
        self.assertIsNotNone(bot.CHAT_STATE_STORE.get_or_create(2001).conversation_id)
        await bot.new_command(update, SimpleNamespace())
        self.assertIsNone(bot.CHAT_STATE_STORE.get_or_create(2001).conversation_id)

    async def test_extended_chat_state_commands(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)

        # /output_format
        await bot.output_format_command(update, SimpleNamespace(args=["json"]))
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).output_format, "json")

        # /json_schema
        await bot.json_schema_command(update, SimpleNamespace(args=["schemas/my_schema.json"]))
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).json_schema, "schemas/my_schema.json")

        # /log_file
        await bot.log_file_command(update, SimpleNamespace(args=["/tmp/test.log"]))
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).log_file, "/tmp/test.log")

        # /print_timeout
        await bot.print_timeout_command(update, SimpleNamespace(args=["15m"]))
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).print_timeout, "15m")

        # /new_project
        await bot.new_project_command(update, SimpleNamespace(args=["on"]))
        self.assertTrue(bot.CHAT_STATE_STORE.get_or_create(2001).new_project)

        # /disable_slash_commands
        await bot.disable_slash_commands_command(update, SimpleNamespace(args=["1"]))
        self.assertTrue(bot.CHAT_STATE_STORE.get_or_create(2001).disable_slash_commands)

    async def test_model_picker_callback(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")

        # Model select callback
        query_model = SimpleNamespace(
            from_user=user,
            data="model_sel:claude-3-5-sonnet",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat=chat),
        )
        up_model = SimpleNamespace(callback_query=query_model, effective_chat=chat, effective_user=user)
        await bot.global_callback_query_handler(up_model, SimpleNamespace())
        self.assertEqual(bot.CHAT_STATE_STORE.get_or_create(2001).model, "claude-3-5-sonnet")

    async def test_setdefault_flow_and_defaults_inheritance(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        status_msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=status_msg)

        bot.CHAT_STATE_STORE.update(2001, model="gemini-2.5-pro", effort="medium", mode="accept-edits", sandbox=False, verbose="detailed")

        # Call /setdefault
        await bot.setdefault_command(update, SimpleNamespace())
        status_msg.reply_text.assert_awaited()
        keyboard = status_msg.reply_text.await_args.kwargs["reply_markup"]
        confirm_data = keyboard.inline_keyboard[0][0].callback_data

        # Confirm callback
        query = SimpleNamespace(
            from_user=user,
            data=confirm_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat=chat),
        )
        up_cb = SimpleNamespace(callback_query=query, effective_chat=chat, effective_user=user)
        await bot.global_callback_query_handler(up_cb, SimpleNamespace())
        query.edit_message_text.assert_awaited_with("✅ 已成功將設定寫回 `.env` 全域預設值！")

        # Verify in-memory defaults updated
        self.assertEqual(bot.CONFIG.default_model, "gemini-2.5-pro")
        self.assertEqual(bot.CONFIG.default_effort, "medium")
        self.assertEqual(bot.CONFIG.default_mode, "accept-edits")
        self.assertFalse(bot.CONFIG.default_sandbox)
        self.assertEqual(bot.CONFIG.default_verbose, "detailed")

        # New chat inherits these defaults
        new_chat_settings = bot.CHAT_STATE_STORE.get_or_create(
            2002,
            defaults={
                "model": bot.CONFIG.default_model,
                "effort": bot.CONFIG.default_effort,
                "mode": bot.CONFIG.default_mode,
                "sandbox": bot.CONFIG.default_sandbox,
                "verbose": bot.CONFIG.default_verbose,
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

        # 1. When allow_bot_update = False
        object.__setattr__(bot.CONFIG, "allow_bot_update", False)
        await bot.restart_command(update, SimpleNamespace())
        msg.reply_text.assert_awaited_with("❌ 未啟用遠端重啟。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")

        await bot.update_command(update, SimpleNamespace())
        msg.reply_text.assert_awaited_with("❌ 未啟用遠端更新。請在 `.env` 設定 `ALLOW_BOT_UPDATE=1`。")

        # 2. When allow_bot_update = True
        object.__setattr__(bot.CONFIG, "allow_bot_update", True)
        with patch("bot.run_process", AsyncMock(return_value=ProcessResult(0, "Already up to date.", ""))):
            await bot.update_command(update, SimpleNamespace())
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

        # 1. Reject interactive
        ctx_i = SimpleNamespace(args=["-i"])
        await bot.agy_command(update, ctx_i)
        reply_msg.reply_text.assert_awaited()
        self.assertIn("禁止使用互動模式", reply_msg.reply_text.await_args.args[0])

        ctx_i2 = SimpleNamespace(args=["--prompt-interactive=true", "-p", "hi"])
        await bot.agy_command(update, ctx_i2)
        self.assertIn("禁止使用互動模式", reply_msg.reply_text.await_args.args[0])

        # 2. Dangerous command triggers confirmation
        ctx_dang = SimpleNamespace(args=["plugin", "install", "test-plugin"])
        await bot.agy_command(update, ctx_dang)
        dang_text = reply_msg.reply_text.await_args.args[0]
        self.assertIn("潛在風險", dang_text)
        keyboard = reply_msg.reply_text.await_args.kwargs["reply_markup"]
        confirm_token = keyboard.inline_keyboard[0][0].callback_data.split(":", 1)[1]

        # Confirm via /agy_confirm
        ctx_conf = SimpleNamespace(args=[confirm_token])
        with patch("bot.run_process", AsyncMock(return_value=ProcessResult(0, "Plugin installed", ""))):
            await bot.agy_confirm_command(update, ctx_conf)

    async def test_readonly_tools(self) -> None:
        user = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=2001, type="private")
        msg = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=user, effective_chat=chat, message=msg)

        with patch("bot.run_process", AsyncMock(return_value=ProcessResult(0, "mock output", ""))):
            await bot.agents_command(update, SimpleNamespace())
            await bot.changelog_command(update, SimpleNamespace())
            await bot.plugins_command(update, SimpleNamespace())
            await bot.version_command(update, SimpleNamespace())

    async def test_fetch_ssrf_safe_media_blocks_private_ip(self) -> None:
        # Private / loopback URLs return None
        res_local = await fetch_ssrf_safe_media("http://127.0.0.1/test.png")
        self.assertIsNone(res_local)

        res_meta = await fetch_ssrf_safe_media("http://169.254.169.254/latest/meta-data")
        self.assertIsNone(res_meta)


if __name__ == "__main__":
    unittest.main()
