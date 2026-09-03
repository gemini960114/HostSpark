"""Backward compatibility adapter for pty_runner."""
from hostspark.core.pty import (
    ANSI_ESCAPE_RE,
    format_context_report,
    format_quota_limit_line,
    format_structured_quota,
    run_pty_command,
    strip_ansi,
)

__all__ = [
    "ANSI_ESCAPE_RE",
    "format_context_report",
    "format_quota_limit_line",
    "format_structured_quota",
    "run_pty_command",
    "strip_ansi",
]
