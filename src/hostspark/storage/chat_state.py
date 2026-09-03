import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc


@dataclass(frozen=True)
class ChatSettings:
    chat_id: int
    conversation_id: str | None = None
    model: str | None = None
    effort: str = "high"
    mode: str = "plan"
    sandbox: bool = True
    agent: str | None = None
    project: str | None = None
    add_dirs: tuple[str, ...] = ()
    workspace_dir: str | None = None
    output_format: str = "text"
    json_schema: str | None = None
    log_file: str | None = None
    print_timeout: str | None = None
    continue_enabled: bool = True
    new_project: bool = False
    disable_slash_commands: bool = False
    verbose: str = "compact"
    in_flight_prompt: str | None = None
    updated_at: str = ""


class ChatStateStore:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    conversation_id TEXT,
                    model TEXT,
                    effort TEXT DEFAULT 'high',
                    mode TEXT DEFAULT 'plan',
                    sandbox INTEGER DEFAULT 1,
                    agent TEXT,
                    project TEXT,
                    add_dirs TEXT DEFAULT '[]',
                    output_format TEXT DEFAULT 'text',
                    json_schema TEXT,
                    log_file TEXT,
                    print_timeout TEXT,
                    continue_enabled INTEGER DEFAULT 1,
                    new_project INTEGER DEFAULT 0,
                    disable_slash_commands INTEGER DEFAULT 0,
                    verbose TEXT DEFAULT 'compact',
                    in_flight_prompt TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Migration: chat_settings already existed in production before
            # workspace_dir was added, and CREATE TABLE IF NOT EXISTS above
            # only affects brand-new databases, so add the column by hand
            # when missing. Idempotent — safe to run on every startup.
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_settings)")}
            if "workspace_dir" not in existing_cols:
                conn.execute("ALTER TABLE chat_settings ADD COLUMN workspace_dir TEXT")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _row_to_settings(self, row: sqlite3.Row) -> ChatSettings:
        add_dirs_raw = row["add_dirs"] or "[]"
        try:
            add_dirs_list = json.loads(add_dirs_raw)
            if not isinstance(add_dirs_list, list):
                add_dirs_list = []
        except Exception:
            add_dirs_list = []

        return ChatSettings(
            chat_id=row["chat_id"],
            conversation_id=row["conversation_id"],
            model=row["model"],
            effort=row["effort"] or "high",
            mode=row["mode"] or "plan",
            sandbox=bool(row["sandbox"]),
            agent=row["agent"],
            project=row["project"],
            add_dirs=tuple(str(d) for d in add_dirs_list),
            workspace_dir=row["workspace_dir"],
            output_format=row["output_format"] or "text",
            json_schema=row["json_schema"],
            log_file=row["log_file"],
            print_timeout=row["print_timeout"],
            continue_enabled=bool(row["continue_enabled"]),
            new_project=bool(row["new_project"]),
            disable_slash_commands=bool(row["disable_slash_commands"]),
            verbose=row["verbose"] or "compact",
            in_flight_prompt=row["in_flight_prompt"],
            updated_at=row["updated_at"] or "",
        )

    def get_or_create(self, chat_id: int, defaults: Any = None) -> ChatSettings:
        now_iso = datetime.now(UTC).isoformat(timespec="seconds")
        d = defaults or {}
        init_model = d.get("model")
        init_effort = d.get("effort") or "high"
        init_mode = d.get("mode") or "plan"
        init_sandbox = 1 if d.get("sandbox", True) else 0
        init_verbose = d.get("verbose") or "compact"

        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)
            )
            row = cursor.fetchone()
            if row is not None:
                return self._row_to_settings(row)

            conn.execute(
                """
                INSERT INTO chat_settings (
                    chat_id, model, effort, mode, sandbox, add_dirs, output_format,
                    continue_enabled, new_project, disable_slash_commands, verbose, updated_at
                ) VALUES (?, ?, ?, ?, ?, '[]', 'text', 1, 0, 0, ?, ?)
                """,
                (chat_id, init_model, init_effort, init_mode, init_sandbox, init_verbose, now_iso),
            )
            cursor = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)
            )
            return self._row_to_settings(cursor.fetchone())

    def update(self, chat_id: int, **fields: Any) -> ChatSettings:
        self.get_or_create(chat_id)
        now_iso = datetime.now(UTC).isoformat(timespec="seconds")

        allowed_columns = {
            "conversation_id",
            "model",
            "effort",
            "mode",
            "sandbox",
            "agent",
            "project",
            "add_dirs",
            "workspace_dir",
            "output_format",
            "json_schema",
            "log_file",
            "print_timeout",
            "continue_enabled",
            "new_project",
            "disable_slash_commands",
            "verbose",
            "in_flight_prompt",
        }

        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed_columns:
                continue
            if key in {"sandbox", "continue_enabled", "new_project", "disable_slash_commands"}:
                updates[key] = 1 if value else 0
            elif key == "add_dirs":
                if isinstance(value, (list, tuple)):
                    updates[key] = json.dumps(list(value))
                elif isinstance(value, str):
                    updates[key] = value
                else:
                    updates[key] = "[]"
            else:
                updates[key] = value

        updates["updated_at"] = now_iso
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        params = list(updates.values()) + [chat_id]

        with self._connection() as conn:
            conn.execute(
                f"UPDATE chat_settings SET {set_clause} WHERE chat_id = ?",
                params,
            )
            cursor = conn.execute(
                "SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)
            )
            return self._row_to_settings(cursor.fetchone())

    def clear_conversation(self, chat_id: int) -> ChatSettings:
        return self.update(chat_id, conversation_id=None)

    def set_in_flight(self, chat_id: int, prompt: str | None) -> None:
        self.update(chat_id, in_flight_prompt=prompt)

    def get_all_in_flight(self) -> list[tuple[int, str]]:
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT chat_id, in_flight_prompt FROM chat_settings WHERE in_flight_prompt IS NOT NULL AND in_flight_prompt != ''"
            )
            return [(row["chat_id"], row["in_flight_prompt"]) for row in cursor.fetchall()]

    def clear_all_in_flight(self) -> None:
        now_iso = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connection() as conn:
            conn.execute(
                "UPDATE chat_settings SET in_flight_prompt = NULL, updated_at = ? WHERE in_flight_prompt IS NOT NULL",
                (now_iso,),
            )
