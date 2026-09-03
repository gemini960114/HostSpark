from __future__ import annotations

from pathlib import Path

import hostspark.state as state
from hostspark.core.sanitizer import RESERVED_PROJECT_DIR_NAMES


def list_project_dirs(root: Path) -> list[str]:
    """Subdirectories of `root` selectable as a project dir, alphabetical."""
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name.lower() not in RESERVED_PROJECT_DIR_NAMES
    )


def switch_project_dir(chat_id: int, name: str) -> None:
    """Point `chat_id` at project directory `name` and start a fresh session.

    Also drops any /add_dir entries from the previous project — those are
    absolute paths with no per-project scoping, so left alone they'd keep
    granting agy access to a now-unrelated project's extra directories.

    Setting cwd alone isn't enough for agy to treat the directory as its
    active project — without --new-project (or a previously-registered
    --project) it falls back to its own internal scratch location
    (~/.gemini/antigravity-cli/scratch) regardless of cwd. Mark the chat
    pending so the very next run_agy() call adds --new-project once.
    """
    store = state.get_chat_state_store()
    current = store.get_or_create(chat_id).workspace_dir
    fields = {"workspace_dir": name, "conversation_id": None}
    if current != name:
        fields["add_dirs"] = []
    store.update(chat_id, **fields)
    state.PENDING_PROJECT_INIT.add(chat_id)
    state.PENDING_CLEAR.add(chat_id)
