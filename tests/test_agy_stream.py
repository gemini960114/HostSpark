import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from hostspark.core.streaming import run_agy_streaming


class AgyStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_plain_text(self) -> None:
        chunks = []

        async def on_chunk(text: str) -> None:
            chunks.append(text)

        script = "import sys, time; print('line 1'); sys.stdout.flush(); time.sleep(0.05); print('line 2'); sys.stdout.flush()"
        with tempfile.TemporaryDirectory() as workdir:
            result = await run_agy_streaming(
                [sys.executable, "-c", script],
                cwd=Path(workdir),
                env=os.environ.copy(),
                timeout_seconds=5,
                max_output_bytes=10000,
                on_chunk=on_chunk,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("line 1", result.stdout)
        self.assertIn("line 2", result.stdout)
        self.assertTrue(len(chunks) >= 2)

    async def test_streaming_ndjson_events(self) -> None:
        events = []
        chunks = []

        async def on_event(ev: dict) -> None:
            events.append(ev)

        async def on_chunk(text: str) -> None:
            chunks.append(text)

        script = """import json, sys, time
print(json.dumps({"type": "delta", "delta": "Hello "}))
sys.stdout.flush()
time.sleep(0.05)
print(json.dumps({"type": "delta", "delta": "World!"}))
sys.stdout.flush()
"""
        with tempfile.TemporaryDirectory() as workdir:
            result = await run_agy_streaming(
                [sys.executable, "-c", script],
                cwd=Path(workdir),
                env=os.environ.copy(),
                timeout_seconds=5,
                max_output_bytes=10000,
                on_chunk=on_chunk,
                on_event=on_event,
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["delta"], "Hello ")
        self.assertEqual(events[1]["delta"], "World!")
        self.assertTrue(any("Hello" in c for c in chunks))


if __name__ == "__main__":
    unittest.main()
