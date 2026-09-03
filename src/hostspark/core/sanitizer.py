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
