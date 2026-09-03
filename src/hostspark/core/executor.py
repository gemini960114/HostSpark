from __future__ import annotations

import asyncio
import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hostspark.state as state
from hostspark.core.prompt import compose_agy_prompt, resolve_model_and_effort_args
from hostspark.core.sanitizer import build_safe_subprocess_env, redact_sensitive, safe_join


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


async def _read_stream_limited(
    stream: asyncio.StreamReader | None, max_bytes: int
) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False

    captured = bytearray()
    truncated = False
    while True:
        block = await stream.read(65_536)
        if not block:
            break
        remaining = max_bytes - len(captured)
        if remaining > 0:
            captured.extend(block[:remaining])
        if len(block) > max(remaining, 0):
            truncated = True
    return bytes(captured), truncated


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        await process.wait()


_stop_process = stop_process



async def run_process(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
    max_output_bytes: int,
) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    stdout_task = asyncio.create_task(_read_stream_limited(process.stdout, max_output_bytes))
    stderr_task = asyncio.create_task(_read_stream_limited(process.stderr, max_output_bytes))
    timed_out = False

    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        timed_out = True
        await _stop_process(process)
    except BaseException:
        await _stop_process(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise

    (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
        stdout_task, stderr_task
    )
    return ProcessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=redact_sensitive(stdout.decode("utf-8", errors="replace").strip()),
        stderr=redact_sensitive(stderr.decode("utf-8", errors="replace").strip()),
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def is_headless_permission_denied(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return (
        "permission" in lowered
        and "headless mode cannot prompt" in lowered
        and ("auto-denied" in lowered or "soft-denied" in lowered)
    )


async def run_agy(
    user_text: str,
    *,
    chat_id: int | None = None,
    continue_conversation: bool = True,
    workdir: Path | None = None,
    add_primary_workdir: bool = False,
    allow_full_permissions: bool = True,
    on_chunk: Any = None,
    on_event: Any = None,
) -> ProcessResult:
    config = state.get_config()
    if chat_id is not None:
        injected_report = state.pop_context_injection(chat_id)
        if injected_report:
            user_text = (
                "（以下是系統剛才查詢指令的結果，供你回答使用者接下來這句話時參考，"
                "使用者看得到這份報告，不用整段複述）：\n"
                f"{injected_report}\n\n"
                f"使用者的訊息：\n{user_text}"
            )
    prompt = compose_agy_prompt(user_text, config.rule_prompt)
    args = [str(config.agy_bin), "-p", prompt]

    chat_state = None
    if chat_id is not None:
        chat_state = state.get_chat_state_store().get_or_create(chat_id)

    if chat_id is not None and workdir is None:
        if chat_state and chat_state.workspace_dir:
            # Chat has explicitly picked a project directory via /new — use it
            # as-is and don't also expose config.agy_workdir via --add-dir
            # (add_primary_workdir stays False): the whole point of picking a
            # project dir is to confine agy to it.
            workdir = safe_join(config.workspace_root, chat_state.workspace_dir)
            workdir.mkdir(parents=True, exist_ok=True)
        else:
            workdir = config.state_db_path.parent / "workspaces" / f"chat-{chat_id}"
            workdir.mkdir(parents=True, exist_ok=True)
            add_primary_workdir = True

    if chat_state:
        if chat_state.conversation_id:
            args.extend(["--conversation", chat_state.conversation_id])
        elif continue_conversation and chat_state.continue_enabled:
            args.append("--continue")

        args.extend(resolve_model_and_effort_args(chat_state.model, chat_state.effort))

        if config.permission_mode == "full" and chat_state.mode == "accept-edits":
            args.extend(["--mode", "accept-edits"])
        else:
            args.extend(["--mode", "plan"])

        if chat_state.sandbox:
            args.append("--sandbox")

        if chat_state.agent:
            args.extend(["--agent", chat_state.agent])
        if chat_state.project:
            args.extend(["--project", chat_state.project])

        for extra_dir in chat_state.add_dirs:
            args.extend(["--add-dir", extra_dir])

        if chat_state.output_format and chat_state.output_format != "text":
            args.extend(["--output-format", chat_state.output_format])
        if chat_state.json_schema:
            args.extend(["--json-schema", chat_state.json_schema])
        if chat_state.log_file:
            args.extend(["--log-file", chat_state.log_file])
        if chat_state.print_timeout:
            args.extend(["--print-timeout", chat_state.print_timeout])
        else:
            args.extend(["--print-timeout", f"{config.timeout_seconds}s"])

        if chat_state.new_project:
            args.append("--new-project")
        if chat_state.disable_slash_commands:
            args.append("--disable-slash-commands")
    else:
        if continue_conversation:
            args.append("--continue")
        args.extend(["--print-timeout", f"{config.timeout_seconds}s"])

    if config.permission_mode == "full" and allow_full_permissions:
        args.append("--dangerously-skip-permissions")
    if add_primary_workdir and workdir != config.agy_workdir:
        args.extend(["--add-dir", str(config.agy_workdir)])

    env = build_safe_subprocess_env(extra_path=config.agy_bin.parent)
    run_cwd = workdir or config.agy_workdir

    if on_chunk or on_event:
        from hostspark.core.streaming import run_agy_streaming

        return await run_agy_streaming(
            args,
            cwd=run_cwd,
            env=env,
            timeout_seconds=config.timeout_seconds + 10,
            max_output_bytes=config.max_output_bytes,
            on_chunk=on_chunk,
            on_event=on_event,
        )

    return await run_process(
        args,
        cwd=run_cwd,
        env=env,
        timeout_seconds=config.timeout_seconds + 10,
        max_output_bytes=config.max_output_bytes,
    )
