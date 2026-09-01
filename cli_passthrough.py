import shlex
from typing import Sequence


SAFE_SUBCOMMANDS = {
    "agent",
    "agents",
    "changelog",
    "help",
    "install",
    "models",
    "plugin",
    "plugins",
    "update",
}

SAFE_FLAGS = {"--help", "-h", "--version", "-v"}

DANGEROUS_PLUGIN_ACTIONS = {
    "install",
    "uninstall",
    "enable",
    "disable",
    "import",
    "link",
}


def parse_cli_args(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"指令解析失敗（引號未成對）：{exc}") from exc


def validate_custom_args(args: Sequence[str]) -> tuple[bool, str]:
    if not args:
        return False, "請提供 agy 參數。"

    # Rule 1: Always reject interactive flags (including --prompt-interactive=value or -i=value)
    for arg in args:
        clean_arg = arg.strip().lower()
        if (
            clean_arg in {"-i", "--prompt-interactive"}
            or clean_arg.startswith("-i=")
            or clean_arg.startswith("--prompt-interactive=")
        ):
            return False, "❌ 禁止使用互動模式旗標 `-i` 或 `--prompt-interactive`。"

    first = args[0]
    # Check if first is a safe subcommand or help/version
    if first in SAFE_SUBCOMMANDS or first in SAFE_FLAGS:
        return True, ""

    # Rule 2: Must contain --print / -p / --prompt
    has_print_flag = any(
        arg in {"-p", "--print", "--prompt"} or arg.startswith("-p=") or arg.startswith("--print=") or arg.startswith("--prompt=")
        for arg in args
    )
    if not has_print_flag:
        return False, "❌ 自訂指令必須包含 `--print` / `-p` / `--prompt` 或使用唯讀子命令（如 `models`、`agents`、`changelog`）。"

    return True, ""


def is_dangerous_custom_command(args: Sequence[str]) -> bool:
    if not args:
        return False

    # Trigger 1: --dangerously-skip-permissions anywhere
    if any(
        arg == "--dangerously-skip-permissions" or arg.startswith("--dangerously-skip-permissions=")
        for arg in args
    ):
        return True

    first = args[0].lower()

    # Trigger 2: update or install subcommands
    if first in {"update", "install"}:
        return True

    # Trigger 3: plugin/plugins with dangerous actions
    if first in {"plugin", "plugins"}:
        if len(args) >= 2:
            action = args[1].lower()
            if action in DANGEROUS_PLUGIN_ACTIONS:
                return True

    return False


def prepare_custom_args(args: Sequence[str], enforce_sandbox: bool = False) -> list[str]:
    result = list(args)
    if enforce_sandbox and "--sandbox" not in result:
        result.append("--sandbox")
    return result
