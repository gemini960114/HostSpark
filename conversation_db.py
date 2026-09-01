import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def _default_brain_dir() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "brain"


def _list_from_sqlite(path: Path, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return [], 0

    uri = f"file:{resolved}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            # Check table existence
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            target_table = None
            if "conversation_summaries" in tables:
                target_table = "conversation_summaries"
            elif "conversations" in tables:
                target_table = "conversations"
            elif "sessions" in tables:
                target_table = "sessions"

            if not target_table:
                return [], 0

            # Count total
            total = conn.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]

            # Fetch rows
            cursor = conn.execute(
                f"SELECT * FROM {target_table} ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                d = dict(row)
                conv_id = d.get("conversation_id") or d.get("id") or d.get("uuid") or str(d.get("rowid", ""))
                summary = d.get("summary") or d.get("title") or d.get("name") or "(無摘要)"
                updated_at = d.get("updated_at") or d.get("created_at") or ""
                results.append({
                    "id": str(conv_id),
                    "summary": str(summary),
                    "updated_at": str(updated_at),
                })
            return results, total
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("讀取對話 SQLite 資料庫失敗：%s", exc)
        return [], 0


def _list_from_brain_dir(brain_dir: Path, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
    resolved = brain_dir.expanduser().resolve()
    if not resolved.is_dir():
        return [], 0

    entries: list[tuple[float, str, str]] = []
    try:
        for item in resolved.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue
            conv_id = item.name
            mtime = item.stat().st_mtime
            summary = ""

            # Check transcript
            transcript = item / ".system_generated" / "logs" / "transcript.jsonl"
            if not transcript.is_file():
                transcript = item / "transcript.jsonl"

            if transcript.is_file():
                try:
                    with open(transcript, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            data = json.loads(line.strip())
                            if data.get("type") == "USER_INPUT" and data.get("content"):
                                summary = str(data["content"]).strip().replace("\n", " ")[:80]
                                break
                except Exception:
                    pass

            if not summary:
                summary = f"對話 {conv_id[:8]}..."
            entries.append((mtime, conv_id, summary))
    except Exception as exc:
        logger.warning("讀取對話目錄失敗：%s", exc)
        return [], 0

    entries.sort(key=lambda x: x[0], reverse=True)
    total = len(entries)
    paginated = entries[offset : offset + limit]
    results = [
        {"id": conv_id, "summary": summary, "updated_at": ""}
        for _, conv_id, summary in paginated
    ]
    return results, total


def list_conversations(
    db_path: Path | None = None,
    brain_dir: Path | None = None,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    if db_path and db_path.is_file():
        results, total = _list_from_sqlite(db_path, limit, offset)
        if results or total > 0:
            return results, total

    dir_to_check = brain_dir or _default_brain_dir()
    return _list_from_brain_dir(dir_to_check, limit, offset)
