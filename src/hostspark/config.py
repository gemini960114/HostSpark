import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_BOT_NAME = "HostSpark"
DEFAULT_WAITING_MESSAGE = f"⏳ {DEFAULT_BOT_NAME} 正在思考與執行中，請稍候..."
TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


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
        private_only=private_only,
        permission_mode=permission_mode,
        agy_bin=agy_bin,
        agy_workdir=workdir,
        workspace_root=workspace_root,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        schedule_db_path=schedule_db_path,
        state_db_path=state_db_path,
        conversation_db_path=conversation_db_path,
        allowed_models=allowed_models,
        schedule_timezone=schedule_timezone,
        progress_mode=progress_mode,
        auto_interrupt=auto_interrupt,
        allow_bot_update=allow_bot_update,
        default_model=default_model,
        default_effort=default_effort,
        default_mode=default_mode,
        default_sandbox=default_sandbox,
        default_verbose=default_verbose,
        schedule_min_interval_minutes=schedule_min_interval_minutes,
        schedule_max_tasks=schedule_max_tasks,
        waiting_message=waiting_message,
        bot_name=bot_name,
        rule_prompt="",
    )
