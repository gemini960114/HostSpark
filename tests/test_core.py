import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from agy_bot_core import (
    ConfigError,
    compose_agy_prompt,
    load_config,
    md_to_telegram_html,
    redact_sensitive,
    run_process,
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
        self.assertEqual(config.permission_mode, "safe")
        self.assertEqual(config.timeout_seconds, 30)
        self.assertEqual(config.max_output_bytes, 4096)
        self.assertEqual(config.agy_bin, Path(sys.executable).resolve())

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


if __name__ == "__main__":
    unittest.main()
