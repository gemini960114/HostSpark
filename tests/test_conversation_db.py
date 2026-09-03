import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hostspark.storage.conversation_db import list_conversations


class ConversationDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_list_from_sqlite(self) -> None:
        db_path = self.root / "conversations.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                summary TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?)",
            ("conv-1", "討論專案架構", "2026-09-01T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?)",
            ("conv-2", "修復資料庫問題", "2026-09-01T11:00:00Z"),
        )
        conn.commit()
        conn.close()

        items, total = list_conversations(db_path=db_path, limit=10, offset=0)
        self.assertEqual(total, 2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "conv-2")
        self.assertEqual(items[0]["summary"], "修復資料庫問題")

    def test_list_from_brain_dir(self) -> None:
        brain_dir = self.root / "brain"
        conv_dir = brain_dir / "c1234567-89ab-cdef-0123-456789abcdef"
        log_dir = conv_dir / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        transcript = log_dir / "transcript.jsonl"
        with open(transcript, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "SYSTEM", "content": "init"}) + "\n")
            f.write(json.dumps({"type": "USER_INPUT", "content": "你好，請幫我寫一份報告"}) + "\n")

        items, total = list_conversations(brain_dir=brain_dir, limit=10, offset=0)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "c1234567-89ab-cdef-0123-456789abcdef")
        self.assertIn("你好，請幫我寫一份報告", items[0]["summary"])

    def test_nonexistent_paths(self) -> None:
        items, total = list_conversations(
            db_path=self.root / "nonexistent.db",
            brain_dir=self.root / "nonexistent_brain",
        )
        self.assertEqual(total, 0)
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
