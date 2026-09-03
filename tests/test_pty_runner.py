import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from hostspark.core.pty import (
    format_context_report,
    format_quota_limit_line,
    format_structured_quota,
    run_pty_command,
    strip_ansi,
)


class PtyRunnerTests(unittest.IsolatedAsyncioTestCase):
    def test_strip_ansi(self) -> None:
        ansi_text = "\x1b[31mError:\x1b[0m \x1b[1mFile not found\x1b[0m"
        self.assertEqual(strip_ansi(ansi_text), "Error: File not found")

    def test_format_quota_limit_line(self) -> None:
        # 20 used out of 100 with 50% cycle elapsed (time elapsed = 50%, usage = 20% -> delta = +30% -> ⭐)
        line = format_quota_limit_line("Requests", 20.0, 100.0, "reqs", cycle_elapsed_ratio=0.5)
        self.assertIn("Requests", line)
        self.assertIn("20.0/100.0 reqs", line)
        self.assertIn("⭐", line)
        self.assertIn("[+30%]", line)

        # 60 used out of 100 with 50% cycle elapsed (delta = 50 - 60 = -10% -> 🟡)
        line_yellow = format_quota_limit_line("Tokens", 60.0, 100.0, "k", cycle_elapsed_ratio=0.5)
        self.assertIn("🟡", line_yellow)

        # Zero or unknown total -> ⚪
        line_white = format_quota_limit_line("Unlimited", 50.0, 0, "calls")
        self.assertIn("⚪", line_white)
        self.assertIn("[無上限/未知]", line_white)

    def test_format_structured_quota(self) -> None:
        data = {
            "account": "user@example.com",
            "quotas": [
                {
                    "name": "Gemini 2.5 Pro",
                    "used": 150,
                    "total": 1000,
                    "unit": "RPM",
                }
            ],
            "reset_time": "2026-09-02 00:00 UTC",
        }
        res = format_structured_quota(data)
        self.assertIn("user@example.com", res)
        self.assertIn("Gemini 2.5 Pro", res)
        self.assertIn("150.0/1000.0 RPM", res)
        self.assertIn("2026-09-02 00:00 UTC", res)

    def test_format_context_report(self) -> None:
        raw_json = '{"model": "gemini-2.5-pro", "tokens": {"user": 100, "system": 500, "agent": 300}, "checkpoint": "cp-123"}'
        res = format_context_report(raw_json)
        self.assertIn("gemini-2.5-pro", res)
        self.assertIn("Token 使用分類", res)
        self.assertIn("cp-123", res)

        raw_text = "Model: claude-3-5-sonnet\nTokens used: 1200\nCheckpoint: saved"
        res_text = format_context_report(raw_text)
        self.assertIn("🤖", res_text)
        self.assertIn("📊", res_text)

    async def test_run_pty_fallback(self) -> None:
        # Mock interactive script that prints something
        with tempfile.TemporaryDirectory() as workdir:
            fake_agy = Path(workdir) / "fake_agy"
            with open(fake_agy, "w") as f:
                f.write(f"#!{sys.executable}\n")
                f.write("import sys\n")
                f.write("print('Welcome to AGY CLI')\n")
                f.write("cmd = input()\n")
                f.write("print('Executed: ' + cmd)\n")
            fake_agy.chmod(0o755)

            out = await run_pty_command(fake_agy, Path(workdir), "/context", timeout_seconds=5)
            self.assertTrue(len(out) > 0)


if __name__ == "__main__":
    unittest.main()
