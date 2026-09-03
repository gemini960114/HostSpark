import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hostspark.config import ConfigError, load_config
from hostspark.core.executor import ProcessResult, run_process
from hostspark.core.prompt import (
    compose_agy_prompt,
    detect_schedule_intent,
    model_has_baked_in_effort,
)
from hostspark.core.sanitizer import (
    build_safe_subprocess_env,
    redact_sensitive,
    safe_join,
    validate_project_dir_name,
)
from hostspark.core.workspace import list_project_dirs
from hostspark.telegram.formatters import (
    format_result_message,
    md_to_telegram_html,
    split_markdown_into_chunks,
)


VALID_TOKEN = f"{987654321}:{'A' * 25}"


class ConfigTests(unittest.TestCase):
    def valid_env(self, workdir: str) -> dict[str, str]:
        return {
            "TELEGRAM_BOT_TOKEN": VALID_TOKEN,
            "ALLOWED_USER_ID": "123456789",
            "AGY_PERMISSION_MODE": "safe",
            "AGY_BIN": sys.executable,
            "AGY_WORKDIR": workdir,
            "AGY_TIMEOUT_SECONDS": "30",
            "AGY_MAX_OUTPUT_BYTES": "4096",
        }

    def test_load_config(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            config = load_config(self.valid_env(workdir))
        self.assertEqual(config.allowed_user_id, 123456789)
        self.assertEqual(config.allowed_user_ids, frozenset({123456789}))
        self.assertEqual(config.permission_mode, "safe")
        self.assertEqual(config.timeout_seconds, 30)
        self.assertEqual(config.max_output_bytes, 4096)
        self.assertEqual(config.agy_bin, Path(sys.executable).resolve())
        self.assertEqual(config.bot_name, "HostSpark")
        self.assertEqual(config.waiting_message, "⏳ HostSpark 正在思考與執行中，請稍候...")

    def test_custom_bot_name_and_waiting_message(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            env = self.valid_env(workdir)
            env["AGY_BOT_NAME"] = "智慧助理"
            config = load_config(env)
        self.assertEqual(config.bot_name, "智慧助理")
        self.assertEqual(config.waiting_message, "⏳ 智慧助理 正在思考與執行中，請稍候...")

    def test_custom_waiting_message(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            env = self.valid_env(workdir)
            env["AGY_WAITING_MESSAGE"] = "⏳ 處理中..."
            config = load_config(env)
        self.assertEqual(config.waiting_message, "⏳ 處理中...")

    def test_permission_mode_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            env = self.valid_env(workdir)
            env["AGY_PERMISSION_MODE"] = ""
            with self.assertRaises(ConfigError):
                load_config(env)

    def test_placeholder_token_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            env = self.valid_env(workdir)
            env["TELEGRAM_BOT_TOKEN"] = f"{123456789}:{'A' * 25}"
            with self.assertRaises(ConfigError):
                load_config(env)

    def test_invalid_schedule_timezone_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            env = self.valid_env(workdir)
            env["AGY_SCHEDULE_TIMEZONE"] = "not/a-real-timezone"
            with self.assertRaisesRegex(ConfigError, "IANA 時區"):
                load_config(env)


class ExtendedConfigAndSecurityTests(unittest.TestCase):
    def valid_env(self, workdir: str) -> dict[str, str]:
        return {
            "TELEGRAM_BOT_TOKEN": VALID_TOKEN,
            "ALLOWED_USER_IDS": "1001, 1002, 1003",
            "ALLOWED_CHAT_IDS": "-100123456, -100789",
            "TELEGRAM_PRIVATE_ONLY": "0",
            "AGY_PERMISSION_MODE": "safe",
            "AGY_BIN": sys.executable,
            "AGY_WORKDIR": workdir,
            "AGY_ALLOWED_MODELS": "gemini-2.5-pro, claude-3-5-sonnet",
            "AGY_PROGRESS_MODE": "compact",
            "AGY_AUTO_INTERRUPT": "1",
            "ALLOW_BOT_UPDATE": "1",
        }

    def test_multi_user_and_chat_ids(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            config = load_config(self.valid_env(workdir))
        self.assertEqual(config.allowed_user_ids, frozenset({1001, 1002, 1003}))
        self.assertIn(config.allowed_user_id, {1001, 1002, 1003})
        self.assertEqual(config.allowed_chat_ids, frozenset({-100123456, -100789}))
        self.assertFalse(config.private_only)
        self.assertEqual(config.allowed_models, ("gemini-2.5-pro", "claude-3-5-sonnet"))
        self.assertEqual(config.progress_mode, "compact")
        self.assertTrue(config.auto_interrupt)
        self.assertTrue(config.allow_bot_update)

    def test_safe_join(self) -> None:
        with tempfile.TemporaryDirectory() as base_dir:
            base = Path(base_dir)
            sub = safe_join(base, "uploads", "chat_1", "file.txt")
            self.assertEqual(sub, (base / "uploads" / "chat_1" / "file.txt").resolve())

            # Traversal attempts must raise ValueError
            with self.assertRaises(ValueError):
                safe_join(base, "../../etc/passwd")

            with self.assertRaises(ValueError):
                safe_join(base, "/etc/shadow")

    def test_validate_project_dir_name(self) -> None:
        self.assertIsNone(validate_project_dir_name("my-project_1.2"))
        self.assertIsNotNone(validate_project_dir_name(""))
        self.assertIsNotNone(validate_project_dir_name("   "))
        self.assertIsNotNone(validate_project_dir_name(".."))
        self.assertIsNotNone(validate_project_dir_name("."))
        self.assertIsNotNone(validate_project_dir_name("../../etc"))
        self.assertIsNotNone(validate_project_dir_name("a/b"))
        self.assertIsNotNone(validate_project_dir_name("uploads"))
        self.assertIsNotNone(validate_project_dir_name("Uploads"))
        self.assertIsNotNone(validate_project_dir_name("workspaces"))
        self.assertIsNotNone(validate_project_dir_name("-leading-dash"))
        self.assertIsNotNone(validate_project_dir_name("has space"))
        # A name that passes validation must never be rejected by safe_join.
        with tempfile.TemporaryDirectory() as base_dir:
            base = Path(base_dir)
            self.assertIsNone(validate_project_dir_name("ok_name-1.2"))
            safe_join(base, "ok_name-1.2")  # must not raise

    def test_list_project_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as base_dir:
            base = Path(base_dir)
            (base / "project-a").mkdir()
            (base / "project-b").mkdir()
            (base / "uploads").mkdir()  # reserved, must be excluded
            (base / "workspaces").mkdir()  # reserved, must be excluded
            (base / "not-a-dir.txt").write_text("x")  # files must be excluded
            self.assertEqual(list_project_dirs(base), ["project-a", "project-b"])

        # A root that doesn't exist yet must return an empty list, not raise.
        self.assertEqual(list_project_dirs(Path("/nonexistent/path/xyz")), [])

    def test_build_safe_subprocess_env(self) -> None:
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "secret_token_123",
            "ALLOWED_USER_IDS": "1001,1002",
            "ALLOWED_CHAT_IDS": "999",
            "OTHER_VAR": "safe_value",
        }):
            env = build_safe_subprocess_env(extra_path=Path("/custom/bin"))
            self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
            self.assertNotIn("ALLOWED_USER_IDS", env)
            self.assertNotIn("ALLOWED_CHAT_IDS", env)
            self.assertEqual(env.get("OTHER_VAR"), "safe_value")
            self.assertEqual(env.get("NO_COLOR"), "1")
            self.assertEqual(env.get("TERM"), "dumb")
            self.assertTrue(env["PATH"].startswith("/custom/bin:"))

    def test_extended_redact_sensitive(self) -> None:
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgN_p"
        ssh_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nbG9jYWwta2V5\n-----END OPENSSH PRIVATE KEY-----"
        text = f"AWS: {aws_key} JWT: {jwt} SSH:\n{ssh_key}\nTelegram: https://api.telegram.org/bot987654321:abcdefghijklmnopqrstuvw"
        redacted = redact_sensitive(text)
        self.assertNotIn(aws_key, redacted)
        self.assertNotIn(jwt, redacted)
        self.assertNotIn("bG9jYWwta2V5", redacted)
        self.assertNotIn("abcdefghijklmnopqrstuvw", redacted)
        self.assertIn("[REDACTED_AWS_KEY]", redacted)
        self.assertIn("[REDACTED_JWT]", redacted)
        self.assertIn("[REDACTED_SSH_PRIVATE_KEY]", redacted)


class TextTests(unittest.TestCase):
    def test_rule_prompt_is_prepended(self) -> None:
        self.assertEqual(
            compose_agy_prompt("檢查狀態", "使用繁體中文"),
            "使用繁體中文\n\n使用者請求：\n檢查狀態",
        )

    def test_rule_prompt_can_be_empty(self) -> None:
        self.assertEqual(compose_agy_prompt("hello", ""), "hello")

    def test_long_single_line_is_always_split(self) -> None:
        text = "x" * 10_000
        chunks = split_markdown_into_chunks(text, 3500)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 3500 for chunk in chunks))

    def test_split_prefers_newlines_without_losing_text(self) -> None:
        text = ("a" * 30) + "\n" + ("b" * 30)
        chunks = split_markdown_into_chunks(text, 40)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 40 for chunk in chunks))

    def test_sensitive_values_are_redacted(self) -> None:
        text = (
            f"TELEGRAM_BOT_TOKEN={VALID_TOKEN} "
            f"PASSWORD={'hunter' + '2'} Authorization: Bearer {'abc' + '.def.ghi'}"
        )
        redacted = redact_sensitive(text)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("abc.def.ghi", redacted)
        self.assertNotIn(VALID_TOKEN, redacted)

    def test_html_is_escaped(self) -> None:
        self.assertEqual(md_to_telegram_html("<script>"), "&lt;script&gt;")
        self.assertEqual(md_to_telegram_html("**ok**"), "<b>ok</b>")

    def test_permission_denied_friendly_message(self) -> None:
        result = ProcessResult(
            returncode=0,
            stdout="",
            stderr='jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied. Alternatively, re-run with --dangerously-skip-permissions to auto-approve all tools.',
        )
        msg = format_result_message(result, "safe")
        self.assertIn("很抱歉", msg)
        self.assertIn("Safe 權限模式", msg)
        self.assertIn("AGY_PERMISSION_MODE", msg)

    def test_normal_stdout_message(self) -> None:
        result = ProcessResult(returncode=0, stdout="Hello world", stderr="")
        self.assertEqual(format_result_message(result, "safe"), "Hello world")

    def test_flag_mentioned_in_normal_stdout_is_not_misclassified(self) -> None:
        stdout = "安全說明：不要使用 --dangerously-skip-permissions。"
        result = ProcessResult(returncode=0, stdout=stdout, stderr="")
        self.assertEqual(format_result_message(result, "safe"), stdout)

    def test_unrelated_error_mentioning_flag_is_not_misclassified(self) -> None:
        stderr = "unrelated error; try --dangerously-skip-permissions"
        result = ProcessResult(returncode=1, stdout="", stderr=stderr)
        msg = format_result_message(result, "safe")
        self.assertIn("AGY 執行失敗", msg)
        self.assertNotIn("Safe 權限模式限制", msg)

    def test_full_mode_is_not_reported_as_safe(self) -> None:
        stderr = (
            'a tool required the "command" permission that headless mode cannot prompt for, '
            "so it was auto-denied"
        )
        result = ProcessResult(returncode=0, stdout="", stderr=stderr)
        msg = format_result_message(result, "full")
        self.assertNotIn("Safe 權限模式限制", msg)
        self.assertIn(stderr, msg)


class ProcessTests(unittest.TestCase):
    def test_output_is_truncated_and_process_is_drained(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            result = asyncio.run(
                run_process(
                    [sys.executable, "-c", "print('x' * 5000)"],
                    cwd=Path(workdir),
                    env=os.environ.copy(),
                    timeout_seconds=5,
                    max_output_bytes=100,
                )
            )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(result.stdout.encode()), 100)

    def test_timeout_stops_process(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            result = asyncio.run(
                run_process(
                    [sys.executable, "-c", "import time; time.sleep(5)"],
                    cwd=Path(workdir),
                    env=os.environ.copy(),
                    timeout_seconds=0.1,
                    max_output_bytes=100,
                )
            )
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)


class ScheduleIntentTests(unittest.TestCase):
    def test_recurring_minutes_with_arabic_digit(self) -> None:
        result = detect_schedule_intent("每5分鐘檢查一次磁碟空間")
        self.assertIsNotNone(result)
        cron_expr, task = result
        self.assertEqual(cron_expr, "*/5 * * * *")
        self.assertEqual(task, "每5分鐘檢查一次磁碟空間")

    def test_recurring_minutes_with_chinese_numeral(self) -> None:
        result = detect_schedule_intent("每五分鐘跟我說一次天氣")
        self.assertIsNotNone(result)
        cron_expr, _task = result
        self.assertEqual(cron_expr, "*/5 * * * *")

    def test_recurring_minutes_with_chinese_teens(self) -> None:
        result = detect_schedule_intent("每十五分鐘提醒我喝水")
        self.assertIsNotNone(result)
        cron_expr, _task = result
        self.assertEqual(cron_expr, "*/15 * * * *")

    def test_recurring_hours(self) -> None:
        result = detect_schedule_intent("每2小時幫我巡檢一次服務")
        self.assertIsNotNone(result)
        cron_expr, _task = result
        self.assertEqual(cron_expr, "0 */2 * * *")

    def test_full_date_time_with_intent(self) -> None:
        text = "幫我排程 2026 年 9 月 2 日（星期三） 00:18:00 跟我說晚安"
        result = detect_schedule_intent(text)
        self.assertIsNotNone(result)
        cron_expr, task = result
        self.assertEqual(cron_expr, "18 0 2 9 *")
        self.assertEqual(task, text)

    def test_bare_time_requires_intent_keyword(self) -> None:
        # A bare HH:MM with no scheduling-intent keyword should NOT trigger --
        # e.g. asking about a past log entry at a specific time.
        self.assertIsNone(detect_schedule_intent("幫我查昨天15:00的日誌"))

    def test_bare_time_with_intent_keyword_triggers(self) -> None:
        result = detect_schedule_intent("提醒我 21:30 開會")
        self.assertIsNotNone(result)
        cron_expr, _task = result
        self.assertEqual(cron_expr, "30 21 * * *")

    def test_plain_text_without_time_returns_none(self) -> None:
        self.assertIsNone(detect_schedule_intent("請幫我檢查 Nginx 設定檔語法"))

    def test_empty_text_returns_none(self) -> None:
        self.assertIsNone(detect_schedule_intent(""))

    def test_already_valid_schedule_add_command_is_not_double_wrapped(self) -> None:
        # Guard against re-detecting intent inside a message that already IS a
        # /schedule_add command (defence in depth alongside the COMMAND filter
        # in bot.py that should route real commands away from handle_message).
        text = "/schedule_add 32 0 2 9 * 幫我排程 2026 年 9 月 2 日（星期三） 00:32:00 跟我說晚安"
        result = detect_schedule_intent(text)
        self.assertIsNotNone(result)
        cron_expr, task = result
        # It still detects a (valid) cron from the embedded date/time -- the
        # real safeguard against double-wrapping is that bot.py's handle_message
        # is never reached for a genuine /schedule_add command in the first
        # place (filters.TEXT & ~filters.COMMAND excludes it upstream).
        self.assertEqual(cron_expr, "32 0 2 9 *")
        self.assertEqual(task, text)


class ModelEffortTests(unittest.TestCase):
    def test_high_medium_low_suffixes_are_baked_in(self) -> None:
        self.assertTrue(model_has_baked_in_effort("gemini-3.7-flash-high"))
        self.assertTrue(model_has_baked_in_effort("gemini-3.7-flash-medium"))
        self.assertTrue(model_has_baked_in_effort("gemini-3.1-pro-low"))
        self.assertTrue(model_has_baked_in_effort("gpt-oss-120b-medium"))

    def test_models_without_effort_suffix(self) -> None:
        self.assertFalse(model_has_baked_in_effort("claude-sonnet-4-6"))
        self.assertFalse(model_has_baked_in_effort("claude-opus-4-6-thinking"))

    def test_none_or_empty_model(self) -> None:
        self.assertFalse(model_has_baked_in_effort(None))
        self.assertFalse(model_has_baked_in_effort(""))


if __name__ == "__main__":
    unittest.main()
