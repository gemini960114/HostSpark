import sqlite3
import tempfile
import unittest
from pathlib import Path

from hostspark.storage.chat_state import ChatSettings, ChatStateStore


class ChatStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "chat_state.db"
        self.store = ChatStateStore(self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_get_or_create_default(self) -> None:
        settings = self.store.get_or_create(12345)
        self.assertEqual(settings.chat_id, 12345)
        self.assertIsNone(settings.conversation_id)
        self.assertEqual(settings.effort, "high")
        self.assertEqual(settings.mode, "plan")
        self.assertTrue(settings.sandbox)
        self.assertEqual(settings.add_dirs, ())
        self.assertEqual(settings.output_format, "text")
        self.assertTrue(settings.continue_enabled)
        self.assertFalse(settings.new_project)
        self.assertFalse(settings.disable_slash_commands)
        self.assertEqual(settings.verbose, "compact")
        self.assertIsNone(settings.workspace_dir)

    def test_workspace_dir_round_trips(self) -> None:
        updated = self.store.update(12345, workspace_dir="my-project")
        self.assertEqual(updated.workspace_dir, "my-project")
        self.assertEqual(self.store.get_or_create(12345).workspace_dir, "my-project")

    def test_migration_adds_workspace_dir_to_pre_existing_database(self) -> None:
        # Simulate a database created before workspace_dir existed: build the
        # table by hand with the old column list, insert a row, then confirm
        # opening it through ChatStateStore (which runs the migration in
        # _initialize()) adds the column without disturbing existing data.
        old_db_path = Path(self.tempdir.name) / "legacy_chat_state.db"
        conn = sqlite3.connect(old_db_path)
        conn.execute(
            """
            CREATE TABLE chat_settings (
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
        conn.execute(
            "INSERT INTO chat_settings (chat_id, model, updated_at) VALUES (?, ?, ?)",
            (999, "old-model", "2020-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        migrated_store = ChatStateStore(old_db_path)
        settings = migrated_store.get_or_create(999)
        self.assertEqual(settings.model, "old-model")
        self.assertIsNone(settings.workspace_dir)

        # Opening it a second time (e.g. a service restart) must be a no-op,
        # not an "duplicate column name" error.
        ChatStateStore(old_db_path)

    def test_update_fields(self) -> None:
        updated = self.store.update(
            12345,
            model="gemini-2.5-pro",
            effort="medium",
            mode="accept-edits",
            sandbox=False,
            agent="coder",
            project="proj-123",
            add_dirs=["/dir1", "/dir2"],
            output_format="json",
            json_schema='{"type": "object"}',
            log_file="/tmp/log.txt",
            print_timeout="10m",
            continue_enabled=False,
            new_project=True,
            disable_slash_commands=True,
            verbose="detailed",
        )
        self.assertEqual(updated.model, "gemini-2.5-pro")
        self.assertEqual(updated.effort, "medium")
        self.assertEqual(updated.mode, "accept-edits")
        self.assertFalse(updated.sandbox)
        self.assertEqual(updated.agent, "coder")
        self.assertEqual(updated.project, "proj-123")
        self.assertEqual(updated.add_dirs, ("/dir1", "/dir2"))
        self.assertEqual(updated.output_format, "json")
        self.assertEqual(updated.json_schema, '{"type": "object"}')
        self.assertEqual(updated.log_file, "/tmp/log.txt")
        self.assertEqual(updated.print_timeout, "10m")
        self.assertFalse(updated.continue_enabled)
        self.assertTrue(updated.new_project)
        self.assertTrue(updated.disable_slash_commands)
        self.assertEqual(updated.verbose, "detailed")

    def test_clear_conversation(self) -> None:
        self.store.update(12345, conversation_id="conv-uuid-123")
        s1 = self.store.get_or_create(12345)
        self.assertEqual(s1.conversation_id, "conv-uuid-123")
        s2 = self.store.clear_conversation(12345)
        self.assertIsNone(s2.conversation_id)

    def test_in_flight_prompt(self) -> None:
        self.store.set_in_flight(100, "prompt 1")
        self.store.set_in_flight(200, "prompt 2")
        in_flight = self.store.get_all_in_flight()
        self.assertEqual(len(in_flight), 2)
        self.assertIn((100, "prompt 1"), in_flight)
        self.assertIn((200, "prompt 2"), in_flight)

        self.store.set_in_flight(100, None)
        in_flight = self.store.get_all_in_flight()
        self.assertEqual(in_flight, [(200, "prompt 2")])

        self.store.clear_all_in_flight()
        self.assertEqual(self.store.get_all_in_flight(), [])


if __name__ == "__main__":
    unittest.main()
