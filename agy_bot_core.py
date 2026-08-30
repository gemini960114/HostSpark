import asyncio
import html
import os
import re
import signal
import shutil
from dataclasses import dataclass
from contextlib import suppress
from pathlib import Path
from typing import Mapping


TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
SECRET_VALUE_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)[A-Z0-9_]*)\s*=\s*([^\s]+)"
)
BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    allowed_user_id: int
    agy_bin: Path
    agy_workdir: Path
    permission_mode: str
    rule_prompt: str
    timeout_seconds: int
    max_output_bytes: int


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


async def _stop_process(process: asyncio.subprocess.Process) -> None:
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


def _positive_int(value: str, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必須是整數") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{name} 必須介於 {minimum} 到 {maximum} 之間")
    return parsed


def _resolve_executable(value: str | None) -> Path | None:
    if value:
        expanded = Path(value).expanduser()
        if expanded.parent != Path(".") or expanded.is_absolute():
            return expanded.resolve()
        located = shutil.which(value)
        return Path(located).resolve() if located else expanded.resolve()

    located = shutil.which("agy")
    if located:
        return Path(located).resolve()

    fallback = Path.home() / ".local" / "bin" / "agy"
    return fallback.resolve() if fallback.exists() else None


def load_config(environ: Mapping[str, str] | None = None) -> BotConfig:
    env = os.environ if environ is None else environ

    bot_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not TELEGRAM_TOKEN_RE.fullmatch(bot_token) or bot_token.startswith("123456789:"):
        raise ConfigError("TELEGRAM_BOT_TOKEN 未設定或格式無效")

    allowed_user_id = _positive_int(
        env.get("ALLOWED_USER_ID", ""), "ALLOWED_USER_ID", 1, 9_223_372_036_854_775_807
    )

    permission_mode = env.get("AGY_PERMISSION_MODE", "").strip().lower()
    if permission_mode not in {"safe", "full"}:
        raise ConfigError("AGY_PERMISSION_MODE 必須明確設定為 safe 或 full")

    agy_bin = _resolve_executable(env.get("AGY_BIN", "").strip() or None)
    if not agy_bin or not agy_bin.is_file() or not os.access(agy_bin, os.X_OK):
        raise ConfigError("找不到可執行的 agy；請設定 AGY_BIN 或確認 agy 位於 PATH")

    workdir = Path(env.get("AGY_WORKDIR", "").strip() or Path.home()).expanduser().resolve()
    if not workdir.is_dir():
        raise ConfigError(f"AGY_WORKDIR 不存在或不是目錄：{workdir}")

    timeout_seconds = _positive_int(
        env.get("AGY_TIMEOUT_SECONDS", "600"), "AGY_TIMEOUT_SECONDS", 10, 3600
    )
    max_output_bytes = _positive_int(
        env.get("AGY_MAX_OUTPUT_BYTES", "1000000"),
        "AGY_MAX_OUTPUT_BYTES",
        4096,
        10_000_000,
    )

    return BotConfig(
        bot_token=bot_token,
        allowed_user_id=allowed_user_id,
        agy_bin=agy_bin,
        agy_workdir=workdir,
        permission_mode=permission_mode,
        rule_prompt=env.get("AGY_RULE_PROMPT", "").strip(),
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def compose_agy_prompt(user_text: str, rule_prompt: str) -> str:
    if not rule_prompt:
        return user_text
    return f"{rule_prompt}\n\n使用者請求：\n{user_text}"


def redact_sensitive(text: str) -> str:
    text = BOT_TOKEN_RE.sub("[REDACTED_TELEGRAM_TOKEN]", text)
    text = BEARER_RE.sub("Bearer [REDACTED]", text)
    return SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def split_markdown_into_chunks(text: str, max_chunk_size: int = 3500) -> list[str]:
    if max_chunk_size < 1:
        raise ValueError("max_chunk_size 必須大於 0")
    if not text:
        return [""]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chunk_size, len(text))
        if end < len(text):
            newline = text.rfind("\n", start, end + 1)
            if newline >= start + max_chunk_size // 2:
                end = newline + 1
        chunks.append(text[start:end])
        start = end
    return chunks


def md_to_telegram_html(text: str) -> str:
    code_blocks: list[tuple[str, str]] = []

    def save_code_block(match: re.Match[str]) -> str:
        code_blocks.append((match.group(1) or "", match.group(2)))
        return f"\x00CB_{len(code_blocks) - 1}\x00"

    text = re.sub(r"```(\w*)\n([\s\S]*?)```", save_code_block, text)

    inline_codes: list[str] = []

    def save_inline_code(match: re.Match[str]) -> str:
        inline_codes.append(match.group(1))
        return f"\x00IC_{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", save_inline_code, text)
    text = html.escape(text)

    def replace_link(match: re.Match[str]) -> str:
        return f'<a href="{match.group(2)}">{match.group(1)}</a>'

    text = re.sub(r"\[(.*?)\]\((https?://[^\s\)]+)\)", replace_link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*([^\*\n]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    for idx, code in enumerate(inline_codes):
        text = text.replace(f"\x00IC_{idx}\x00", f"<code>{html.escape(code)}</code>")

    for idx, (lang, code) in enumerate(code_blocks):
        escaped_code = html.escape(code.rstrip())
        if lang:
            tag = f'<pre><code class="language-{html.escape(lang)}">{escaped_code}</code></pre>'
        else:
            tag = f"<pre>{escaped_code}</pre>"
        text = text.replace(f"\x00CB_{idx}\x00", tag)

    return text
