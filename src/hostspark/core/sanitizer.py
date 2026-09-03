import os
import re
from pathlib import Path

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


_PROJECT_DIR_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
RESERVED_PROJECT_DIR_NAMES = {"uploads", "workspaces"}


def validate_project_dir_name(name: str) -> str | None:
    """Validate a single-segment project directory name (for `/new <name>`).

    Returns None when `name` is safe to use as-is with safe_join(); otherwise
    a human-readable (Traditional Chinese) reason it was rejected. Deliberately
    an allowlist (ASCII letters/digits/_/./-, must start with an alnum or
    underscore) rather than a denylist, so it's safe by construction against
    path traversal and against colliding with reserved subfolder names other
    parts of the bot already use (uploads/, workspaces/).
    """
    name = name.strip()
    if not name:
        return "目錄名稱不可為空。"
    if name in {".", ".."}:
        return "不合法的目錄名稱。"
    if name.lower() in RESERVED_PROJECT_DIR_NAMES:
        return f"`{name}` 是保留名稱，請換一個。"
    if not _PROJECT_DIR_NAME_RE.match(name):
        return "目錄名稱只能包含英數字、底線、句點、連字號，且開頭不可是符號。"
    return None


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


def redact_sensitive(text: str) -> str:
    text = BOT_TOKEN_RE.sub("[REDACTED_TELEGRAM_TOKEN]", text)
    text = TELEGRAM_API_URL_RE.sub(r"\1[REDACTED_TELEGRAM_TOKEN]", text)
    text = BEARER_RE.sub("Bearer [REDACTED]", text)
    text = AWS_KEY_RE.sub("[REDACTED_AWS_KEY]", text)
    text = SSH_PRIVATE_KEY_RE.sub("[REDACTED_SSH_PRIVATE_KEY]", text)
    text = JWT_RE.sub("[REDACTED_JWT]", text)
    return SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
