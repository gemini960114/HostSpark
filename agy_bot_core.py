"""Backward compatibility adapter for agy_bot_core.

Re-exports symbols from the modularized hostspark package.
"""

from hostspark.config import (
    DEFAULT_BOT_NAME,
    DEFAULT_WAITING_MESSAGE,
    TELEGRAM_TOKEN_RE,
    BotConfig,
    ConfigError,
    load_config,
)
from hostspark.core.executor import (
    ProcessResult,
    _stop_process,
    is_headless_permission_denied,
    run_process,
)
from hostspark.core.prompt import (
    compose_agy_prompt,
    detect_schedule_intent,
    model_has_baked_in_effort,
)
from hostspark.core.sanitizer import (
    AWS_KEY_RE,
    BEARER_RE,
    BOT_TOKEN_RE,
    JWT_RE,
    SECRET_VALUE_RE,
    SSH_PRIVATE_KEY_RE,
    TELEGRAM_API_URL_RE,
    build_safe_subprocess_env,
    redact_sensitive,
    safe_join,
)
from hostspark.telegram.formatters import (
    format_result_message,
    md_to_telegram_html,
    split_markdown_into_chunks,
)

__all__ = [
    "DEFAULT_BOT_NAME",
    "DEFAULT_WAITING_MESSAGE",
    "TELEGRAM_TOKEN_RE",
    "BotConfig",
    "ConfigError",
    "load_config",
    "ProcessResult",
    "_stop_process",
    "is_headless_permission_denied",
    "run_process",
    "compose_agy_prompt",
    "detect_schedule_intent",
    "model_has_baked_in_effort",
    "AWS_KEY_RE",
    "BEARER_RE",
    "BOT_TOKEN_RE",
    "JWT_RE",
    "SECRET_VALUE_RE",
    "SSH_PRIVATE_KEY_RE",
    "TELEGRAM_API_URL_RE",
    "build_safe_subprocess_env",
    "redact_sensitive",
    "safe_join",
    "format_result_message",
    "md_to_telegram_html",
    "split_markdown_into_chunks",
]
