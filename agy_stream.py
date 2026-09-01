import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from agy_bot_core import ProcessResult, _stop_process, redact_sensitive


logger = logging.getLogger(__name__)


async def run_agy_streaming(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    max_output_bytes: int,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )

    stdout_bytes = bytearray()
    stderr_bytes = bytearray()
    stdout_truncated = False
    stderr_truncated = False
    timed_out = False

    async def read_stdout():
        nonlocal stdout_truncated
        if process.stdout is None:
            return

        accumulated_text = ""
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            remaining = max_output_bytes - len(stdout_bytes)
            if remaining > 0:
                stdout_bytes.extend(line[:remaining])
            if len(line) > max(remaining, 0):
                stdout_truncated = True

            decoded_line = line.decode("utf-8", errors="replace")
            clean_line = decoded_line.strip()
            if not clean_line:
                continue

            # Try parsing as JSON event
            if clean_line.startswith("{") and clean_line.endswith("}"):
                try:
                    event = json.loads(clean_line)
                    if isinstance(event, dict):
                        if on_event:
                            try:
                                await on_event(event)
                            except Exception as e:
                                logger.debug("on_event callback error: %s", e)

                        # Extract text if available
                        text_snippet = None
                        if "content" in event and isinstance(event["content"], str):
                            text_snippet = event["content"]
                        elif "text" in event and isinstance(event["text"], str):
                            text_snippet = event["text"]
                        elif "delta" in event and isinstance(event["delta"], str):
                            text_snippet = event["delta"]

                        if text_snippet and on_chunk:
                            accumulated_text += text_snippet
                            try:
                                await on_chunk(redact_sensitive(accumulated_text))
                            except Exception as e:
                                logger.debug("on_chunk callback error: %s", e)
                        continue
                except Exception:
                    pass

            # Regular text line
            accumulated_text += decoded_line
            if on_chunk:
                try:
                    await on_chunk(redact_sensitive(accumulated_text))
                except Exception as e:
                    logger.debug("on_chunk callback error: %s", e)

    async def read_stderr():
        nonlocal stderr_truncated
        if process.stderr is None:
            return
        while True:
            block = await process.stderr.read(65_536)
            if not block:
                break
            remaining = max_output_bytes - len(stderr_bytes)
            if remaining > 0:
                stderr_bytes.extend(block[:remaining])
            if len(block) > max(remaining, 0):
                stderr_truncated = True

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())

    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        await _stop_process(process)
    except BaseException:
        await _stop_process(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise

    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    return ProcessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=redact_sensitive(stdout_bytes.decode("utf-8", errors="replace").strip()),
        stderr=redact_sensitive(stderr_bytes.decode("utf-8", errors="replace").strip()),
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )
