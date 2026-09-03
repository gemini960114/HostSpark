"""Backward compatibility adapter for cli_passthrough."""
from hostspark.core.cli_args import (
    DANGEROUS_PLUGIN_ACTIONS,
    SAFE_FLAGS,
    SAFE_SUBCOMMANDS,
    is_dangerous_custom_command,
    parse_cli_args,
    prepare_custom_args,
    validate_custom_args,
)

__all__ = [
    "DANGEROUS_PLUGIN_ACTIONS",
    "SAFE_FLAGS",
    "SAFE_SUBCOMMANDS",
    "is_dangerous_custom_command",
    "parse_cli_args",
    "prepare_custom_args",
    "validate_custom_args",
]
