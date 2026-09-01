import asyncio
import html
import os
import re
import signal
import shutil
from dataclasses import dataclass, field
from contextlib import suppress
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
SECRET_VALUE_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)[A-Z0-9_]*)\s*=\s*([^\s]+)"
)
BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
TELEGRAM_API_URL_RE = re.compile(r"(https?://api\.telegram\.org/bot)\d+:[A-Za-z0-9_-]{20,}")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
AWS_KEY_RE = re.compile(r"\b(AKIA[0-9A-Z]{16})\b")
SSH_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9_\s-]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9_\s-]*PRIVATE KEY-----"
)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{4,}\b")

DEFAULT_BOT_NAME = "HostSpark"
DEFAULT_WAITING_MESSAGE = f"⏳ {DEFAULT_BOT_NAME} 正在思考與執行中，請稍候..."


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    agy_bin: Path
    agy_workdir: Path
    permission_mode: str
    rule_prompt: str
    timeout_seconds: int
    max_output_bytes: int
    schedule_db_path: Path
    schedule_timezone: str
    schedule_min_interval_minutes: int
    schedule_max_tasks: int
    allowed_user_id: int = 0
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    allowed_chat_ids: frozenset[int] = field(default_factory=frozenset)
    state_db_path: Path = Path()
    workspace_root: Path = Path()
    allowed_models: tuple[str, ...] = ()
    conversation_db_path: Path | None = None
    private_only: bool = True
    progress_mode: str = "compact"  # full | compact | delete
    auto_interrupt: bool = True
    allow_bot_update: bool = False
    default_model: str | None = None
    default_effort: str = "high"
    default_mode: str = "plan"
    default_sandbox: bool = True
    default_verbose: str = "compact"
    waiting_message: str = DEFAULT_WAITING_MESSAGE
    bot_name: str = DEFAULT_BOT_NAME

    def __post_init__(self):
        if not self.allowed_user_ids and self.allowed_user_id:
            object.__setattr__(self, "allowed_user_ids", frozenset({self.allowed_user_id}))
        elif self.allowed_user_ids and not self.allowed_user_id:
            object.__setattr__(self, "allowed_user_id", next(iter(sorted(self.allowed_user_ids))))

        if not self.state_db_path or str(self.state_db_path) == ".":
            default_state = self.schedule_db_path.parent / "chat_state.db"
            object.__setattr__(self, "state_db_path", default_state)

        if not self.workspace_root or str(self.workspace_root) == ".":
            object.__setattr__(self, "workspace_root", self.agy_workdir)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


def safe_join(base: Path, *parts: str | Path) -> Path:
    resolved_base = base.expanduser().resolve()
    current = resolved_base
    for part in parts:
        part_path = Path(part)
        if part_path.is_absolute():
            resolved_abs = part_path.resolve()
            if not (resolved_abs == resolved_base or resolved_base in resolved_abs.parents):
                raise ValueError(f"絕對路徑不在基礎目錄內：{part_path}")
            current = resolved_abs
        else:
            current = (current / part_path).resolve()
            if not (current == resolved_base or resolved_base in current.parents):
                raise ValueError(f"路徑穿越已被防護阻止：{part}")
    return current


def build_safe_subprocess_env(extra_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "BOT_TOKEN",
        "ALLOWED_USER_ID",
        "ALLOWED_USER_IDS",
        "ALLOWED_CHAT_IDS",
        "TELEGRAM_ALLOWED_USER_IDS",
        "TELEGRAM_ALLOWED_CHAT_IDS",
    ):
        env.pop(var, None)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    if extra_path:
        env["PATH"] = f"{extra_path}{os.pathsep}{env.get('PATH', '')}"
    return env


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


def _bool_val(value: str, default: bool = False) -> bool:
    if not value:
        return default
    v = value.strip().lower()
    return v in {"1", "true", "yes", "on"}


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

    # Allowed user IDs (comma-separated or single)
    raw_user_ids = env.get("ALLOWED_USER_IDS", "").strip() or env.get("ALLOWED_USER_ID", "").strip()
    if not raw_user_ids:
        raise ConfigError("ALLOWED_USER_IDS 或 ALLOWED_USER_ID 必須設定")

    allowed_user_ids_set: set[int] = set()
    for item in raw_user_ids.split(","):
        cleaned = item.strip()
        if cleaned:
            uid = _positive_int(cleaned, "ALLOWED_USER_IDS", 1, 9_223_372_036_854_775_807)
            allowed_user_ids_set.add(uid)
    if not allowed_user_ids_set:
        raise ConfigError("ALLOWED_USER_IDS 必須包含至少一個有效的使用者 ID")

    # Allowed chat IDs (optional comma-separated)
    raw_chat_ids = env.get("ALLOWED_CHAT_IDS", "").strip()
    allowed_chat_ids_set: set[int] = set()
    if raw_chat_ids:
        for item in raw_chat_ids.split(","):
            cleaned = item.strip()
            if cleaned:
                try:
                    allowed_chat_ids_set.add(int(cleaned))
                except ValueError as exc:
                    raise ConfigError(f"ALLOWED_CHAT_IDS 包含無效的 chat ID：{cleaned}") from exc

    private_only = _bool_val(env.get("TELEGRAM_PRIVATE_ONLY", "1"), default=True)

    permission_mode = env.get("AGY_PERMISSION_MODE", "").strip().lower()
    if permission_mode not in {"safe", "full"}:
        raise ConfigError("AGY_PERMISSION_MODE 必須明確設定為 safe 或 full")

    agy_bin = _resolve_executable(env.get("AGY_BIN", "").strip() or None)
    if not agy_bin or not agy_bin.is_file() or not os.access(agy_bin, os.X_OK):
        raise ConfigError("找不到可執行的 agy；請設定 AGY_BIN 或確認 agy 位於 PATH")

    workdir = Path(env.get("AGY_WORKDIR", "").strip() or Path.home()).expanduser().resolve()
    if not workdir.is_dir():
        raise ConfigError(f"AGY_WORKDIR 不存在或不是目錄：{workdir}")

    workspace_root_env = env.get("AGY_WORKSPACE_ROOT", "").strip()
    workspace_root = Path(workspace_root_env).expanduser().resolve() if workspace_root_env else workdir
    if not workspace_root.is_dir():
        raise ConfigError(f"AGY_WORKSPACE_ROOT 不存在或不是目錄：{workspace_root}")

    timeout_seconds = _positive_int(
        env.get("AGY_TIMEOUT_SECONDS", "600"), "AGY_TIMEOUT_SECONDS", 10, 3600
    )
    max_output_bytes = _positive_int(
        env.get("AGY_MAX_OUTPUT_BYTES", "1000000"),
        "AGY_MAX_OUTPUT_BYTES",
        4096,
        10_000_000,
    )

    schedule_timezone = env.get("AGY_SCHEDULE_TIMEZONE", "Asia/Taipei").strip()
    try:
        ZoneInfo(schedule_timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"AGY_SCHEDULE_TIMEZONE 不是有效的 IANA 時區：{schedule_timezone}") from exc

    schedule_db_path = Path(
        env.get("AGY_SCHEDULE_DB_PATH", "").strip()
        or Path.home() / ".local" / "state" / "agy-telegram-bot" / "schedules.db"
    ).expanduser().resolve()
    if schedule_db_path.exists() and not schedule_db_path.is_file():
        raise ConfigError(f"AGY_SCHEDULE_DB_PATH 不是檔案：{schedule_db_path}")

    state_db_path = Path(
        env.get("AGY_STATE_DB_PATH", "").strip()
        or schedule_db_path.parent / "chat_state.db"
    ).expanduser().resolve()
    if state_db_path.exists() and not state_db_path.is_file():
        raise ConfigError(f"AGY_STATE_DB_PATH 不是檔案：{state_db_path}")

    conv_db_env = env.get("AGY_CONVERSATION_DB_PATH", "").strip()
    conversation_db_path = Path(conv_db_env).expanduser().resolve() if conv_db_env else None

    allowed_models_raw = env.get("AGY_ALLOWED_MODELS", "").strip()
    allowed_models = tuple(
        m.strip() for m in allowed_models_raw.split(",") if m.strip()
    ) if allowed_models_raw else ()

    progress_mode = env.get("AGY_PROGRESS_MODE", "").strip() or env.get("TELEGRAM_PROGRESS_MODE", "").strip() or "compact"
    progress_mode = progress_mode.lower()
    if progress_mode not in {"full", "compact", "delete"}:
        progress_mode = "compact"

    auto_interrupt = _bool_val(env.get("AGY_AUTO_INTERRUPT", "1"), default=True)
    allow_bot_update = _bool_val(env.get("ALLOW_BOT_UPDATE", "0"), default=False)

    default_model = env.get("AGY_MODEL", "").strip() or None
    default_effort = env.get("AGY_EFFORT", "").strip().lower() or "high"
    if default_effort not in {"low", "medium", "high"}:
        default_effort = "high"

    default_mode = env.get("AGY_MODE", "").strip().lower() or "plan"
    if default_mode not in {"plan", "accept-edits"}:
        default_mode = "plan"

    default_sandbox = _bool_val(env.get("AGY_SANDBOX", "1"), default=True)

    default_verbose = env.get("AGY_VERBOSE", "").strip().lower() or "compact"
    if default_verbose not in {"detailed", "compact", "silent"}:
        default_verbose = "compact" 

    schedule_min_interval_minutes = _positive_int(
        env.get("AGY_SCHEDULE_MIN_INTERVAL_MINUTES", "15"),
        "AGY_SCHEDULE_MIN_INTERVAL_MINUTES",
        1,
        1440,
    )
    schedule_max_tasks = _positive_int(
        env.get("AGY_SCHEDULE_MAX_TASKS", "20"),
        "AGY_SCHEDULE_MAX_TASKS",
        1,
        100,
    )

    bot_name = env.get("AGY_BOT_NAME", "").strip() or DEFAULT_BOT_NAME
    default_waiting = f"⏳ {bot_name} 正在思考與執行中，請稍候..."
    waiting_message = env.get("AGY_WAITING_MESSAGE", "").strip() or default_waiting

    return BotConfig(
        bot_token=bot_token,
        allowed_user_ids=frozenset(allowed_user_ids_set),
        allowed_chat_ids=frozenset(allowed_chat_ids_set),
        agy_bin=agy_bin,
        agy_workdir=workdir,
        workspace_root=workspace_root,
        permission_mode=permission_mode,
        rule_prompt=env.get("AGY_RULE_PROMPT", "").strip(),
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        schedule_db_path=schedule_db_path,
        state_db_path=state_db_path,
        conversation_db_path=conversation_db_path,
        allowed_models=allowed_models,
        private_only=private_only,
        progress_mode=progress_mode,
        auto_interrupt=auto_interrupt,
        allow_bot_update=allow_bot_update,
        default_model=default_model,
        default_effort=default_effort,
        default_mode=default_mode,
        default_sandbox=default_sandbox,
        default_verbose=default_verbose,
        schedule_timezone=schedule_timezone,
        schedule_min_interval_minutes=schedule_min_interval_minutes,
        schedule_max_tasks=schedule_max_tasks,
        waiting_message=waiting_message,
        bot_name=bot_name,
    )


def compose_agy_prompt(user_text: str, rule_prompt: str) -> str:
    if not rule_prompt:
        return user_text
    return f"{rule_prompt}\n\n使用者請求：\n{user_text}"


_MODEL_EFFORT_SUFFIX_RE = re.compile(r"-(high|medium|low)$", re.IGNORECASE)


def model_has_baked_in_effort(model: str | None) -> bool:
    """判斷模型名稱是否已內建推理深度（例如 `gemini-3.7-flash-high`、`gpt-oss-120b-medium`）。

    這類模型的名稱後綴本身就是 effort 等級，若同時再帶 `--effort` 給 agy，
    只要跟後綴不一致就會被 agy 拒絕（`--model X-medium conflicts with --effort=high`）。
    因此組裝 argv 時，這類模型一律不應該額外附加 `--effort` 旗標。
    """
    if not model:
        return False
    return bool(_MODEL_EFFORT_SUFFIX_RE.search(model.strip()))


_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_num_to_int(token: str) -> int | None:
    """把「五」、「十五」、「二十」這類中文數字（或阿拉伯數字字串）轉成 int，失敗回傳 None。"""
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if "十" in token:
        tens_part, _, ones_part = token.partition("十")
        tens = _CN_DIGITS.get(tens_part, 1) if tens_part else 1
        ones = _CN_DIGITS.get(ones_part, 0) if ones_part else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(token)


_RECUR_MINUTE_RE = re.compile(r"每\s*([0-9一二三四五六七八九十兩]{1,4})\s*分鐘")
_RECUR_HOUR_RE = re.compile(r"每\s*([0-9一二三四五六七八九十兩]{1,4})\s*(?:個)?小時")
_DAILY_TIME_RE = re.compile(r"每天.{0,6}?(\d{1,2})[:：](\d{2})")
_FULL_DATE_TIME_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日.{0,10}?(\d{1,2})[:：](\d{2})")
_BARE_TIME_RE = re.compile(r"(\d{1,2})[:：](\d{2})(?:[:：]\d{2})?")
_SCHEDULE_INTENT_RE = re.compile(
    r"排程|提醒我|叫我|通知我|跟我說|喊我|叫醒我|到時候提醒"
)


def detect_schedule_intent(text: str) -> tuple[str, str] | None:
    """偵測純文字中的排程／未來時間意圖，回傳 `(cron_expr, task_text)`；偵測不到則回傳 None。

    這是路由層的**決定性**攔截，不依賴 AGY／LLM 自行判斷——命中時訊息不會被送去給
    AGY 執行單次對話，而是直接餵給既有的 `/schedule_add` 建立流程（保留 AGY 整理 prompt
    ＋ Telegram 按鈕二次確認），避免 AGY 為了「等到那個時間」而在單次非互動呼叫中卡住，
    佔用全域任務佇列，也不需要使用者自己複製貼上指令再送一次。
    「每 N 分鐘／小時」這類重複性描述本身已是清楚訊號，不需要額外的意圖關鍵字；
    單一日期／時間點的描述則需搭配「排程」「提醒我」等意圖關鍵字才觸發，降低誤判。
    """
    if not text:
        return None
    stripped = text.strip()

    m = _RECUR_MINUTE_RE.search(text)
    if m:
        n = _cn_num_to_int(m.group(1))
        if n and 1 <= n <= 59:
            return f"*/{n} * * * *", stripped

    m = _RECUR_HOUR_RE.search(text)
    if m:
        n = _cn_num_to_int(m.group(1))
        if n and 1 <= n <= 23:
            return f"0 */{n} * * *", stripped

    has_intent = bool(_SCHEDULE_INTENT_RE.search(text))

    if has_intent:
        m = _DAILY_TIME_RE.search(text)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{mm} {hh} * * *", stripped

        m = _FULL_DATE_TIME_RE.search(text)
        if m:
            month, day, hh, mm = (int(g) for g in m.groups())
            if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{mm} {hh} {day} {month} *", stripped

        m = _BARE_TIME_RE.search(text)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{mm} {hh} * * *", stripped

    return None


def redact_sensitive(text: str) -> str:
    text = BOT_TOKEN_RE.sub("[REDACTED_TELEGRAM_TOKEN]", text)
    text = TELEGRAM_API_URL_RE.sub(r"\1[REDACTED_TELEGRAM_TOKEN]", text)
    text = BEARER_RE.sub("Bearer [REDACTED]", text)
    text = AWS_KEY_RE.sub("[REDACTED_AWS_KEY]", text)
    text = SSH_PRIVATE_KEY_RE.sub("[REDACTED_SSH_PRIVATE_KEY]", text)
    text = JWT_RE.sub("[REDACTED_JWT]", text)
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
    def replace_callout(match: re.Match[str]) -> str:
        kind = match.group(1).upper()
        emoji = "💡"
        if kind in {"WARNING", "CAUTION"}:
            emoji = "⚠️"
        elif kind in {"IMPORTANT", "CRITICAL"}:
            emoji = "❗"
        elif kind == "TIP":
            emoji = "✨"
        return f"{emoji} <b>{kind}</b>:\n"

    text = re.sub(r"^>\s*\[!([A-Z]+)\]\s*", replace_callout, text, flags=re.MULTILINE)

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


def is_headless_permission_denied(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return (
        "permission" in lowered
        and "headless mode cannot prompt" in lowered
        and ("auto-denied" in lowered or "soft-denied" in lowered)
    )


def format_result_message(result: ProcessResult, permission_mode: str) -> str:
    truncation_note = "\n\n⚠️ 輸出過長，僅顯示前段內容。" if (
        result.stdout_truncated or result.stderr_truncated
    ) else ""
    if result.timed_out:
        return "⚠️ **AGY 執行逾時，程序已停止。**" + truncation_note

    if permission_mode == "safe" and is_headless_permission_denied(result.stderr):
        return (
            "⚠️ **很抱歉，此操作因 Safe 權限模式限制無法完成。**\n\n"
            "目前機器人運行於 `Safe` 權限模式，執行系統工具或指令時需要終端機互動確認授權；"
            "但因 Telegram 於背景非互動環境運作，無法向您彈出授權視窗，因此該工具權限已被自動拒絕。\n\n"
            "💡 **如何解除限制？**\n"
            "若要讓機器人能夠直接執行指令與排查任務，請在 `.env` 中將 `AGY_PERMISSION_MODE` 改為 `full` 並重啟服務："
            "\n```bash\nsudo systemctl restart agy-telegram.service\n```\n"
            "Full 模式會自動核准所有 AGY 工具操作，只應用於私人、可快照且可重建的專用 VM。"
        )

    if result.returncode != 0:
        details = result.stderr or result.stdout or "沒有錯誤詳情"
        return f"❌ **AGY 執行失敗（exit {result.returncode}）**\n\n{details}{truncation_note}"
    if result.stdout:
        return result.stdout + truncation_note
    if result.stderr:
        return f"⚠️ AGY 沒有標準輸出：\n\n{result.stderr}{truncation_note}"
    return "✅ 執行完成。"
