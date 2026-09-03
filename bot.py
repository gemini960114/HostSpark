"""HostSpark Telegram Bot entrypoint and backward-compatibility facade."""
from __future__ import annotations

import logging
from typing import Any

from hostspark.config import BotConfig, ConfigError, load_config
from hostspark.core.cli_args import (
    is_dangerous_custom_command,
    parse_cli_args,
    prepare_custom_args,
    validate_custom_args,
)
from hostspark.core.executor import (
    ProcessResult,
    is_headless_permission_denied,
    run_process,
)
from hostspark.core.prompt import (
    compose_agy_prompt,
    detect_schedule_intent,
    model_has_baked_in_effort,
)
from hostspark.core.pty import run_pty_command
from hostspark.core.sanitizer import (
    build_safe_subprocess_env,
    redact_sensitive,
    safe_join,
)
from hostspark.core.streaming import run_agy_streaming
from hostspark.runtime.instance_lock import InstanceLock, InstanceLockError
from hostspark.runtime.job_queue import Job, JobQueue
from hostspark.runtime.pending_actions import PendingActionStore
from hostspark.storage.chat_state import ChatSettings, ChatStateStore
from hostspark.storage.schedule_store import (
    NO_REPORT_SENTINEL,
    DueSchedule,
    Schedule,
    ScheduleError,
    ScheduleStore,
    build_prompt_expansion_request,
    normalize_cron,
    parse_schedule_add_payload,
    render_prompt_variables,
)
from hostspark.telegram.formatters import (
    format_result_message,
    md_to_telegram_html,
    split_markdown_into_chunks,
)
from hostspark.telegram.media import detect_output_media, fetch_ssrf_safe_media

import hostspark.core.executor as _executor_mod
import hostspark.state as state
from hostspark.cli import main
from hostspark.core.executor import run_agy as _core_run_agy
from hostspark.runtime.scheduler import (
    _change_schedule_state,
    _command_payload,
    _execute_due_schedule,
    _local_time,
    _run_schedule_add_flow,
    _schedule_id,
    _schedule_status,
    cleanup_expired_workspaces_and_uploads,
    schedule_add_command,
    schedule_delete_command,
    schedule_help_command,
    schedule_list_command,
    schedule_loop,
    schedule_pause_command,
    schedule_resume_command,
    schedule_show_command,
)
from hostspark.state import (
    BASE_DIR,
    ENV_PATH,
    get_chat_state_store,
    get_config,
    get_pending_actions,
    get_schedule_store,
    pop_context_injection,
    queue_context_injection,
)
from hostspark.telegram.app import (
    build_application,
    post_init,
    post_shutdown,
)
from hostspark.telegram.auth import (
    _get_chat_id,
    is_authorized,
    reject_unauthorized,
)
from hostspark.telegram.dispatcher import (
    SAFE_EXTENSIONS,
    _enqueue_and_handle_prompt,
    _execute_chat_job,
    _write_defaults_to_env,
    global_callback_query_handler,
    handle_attachment,
    handle_message,
)
from hostspark.telegram.formatters import (
    result_message,
    send_formatted_response,
    send_formatted_to_chat,
)
from hostspark.telegram.handlers import (
    _perform_bot_restart,
    _status_section,
    add_dir_command,
    agent_command,
    agents_command,
    agy_command,
    agy_confirm_command,
    cancel_command,
    changelog_command,
    clear_command,
    cli_help_command,
    compact_command,
    context_command,
    continue_command,
    disable_slash_commands_command,
    effort_command,
    get_reply_keyboard,
    json_schema_command,
    learn_command,
    log_file_command,
    menu_command,
    mode_command,
    model_command,
    new_command,
    new_project_command,
    output_format_command,
    plugins_command,
    print_timeout_command,
    project_command,
    restart_command,
    sandbox_command,
    session_command,
    setdefault_command,
    start_command,
    status_command,
    tokens_command,
    update_command,
    usage_command,
    verbose_command,
    version_command,
)

logger = logging.getLogger(__name__)

schedule_callback = global_callback_query_handler


async def run_agy(*args, **kwargs) -> ProcessResult:
    current_run_proc = globals().get("run_process")
    if current_run_proc is not None and current_run_proc is not _executor_mod.run_process:
        orig = _executor_mod.run_process
        try:
            _executor_mod.run_process = current_run_proc
            return await _core_run_agy(*args, **kwargs)
        finally:
            _executor_mod.run_process = orig
    return await _core_run_agy(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if hasattr(state, name):
        return getattr(state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    main()
