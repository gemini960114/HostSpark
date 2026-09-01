import os
import tempfile
import unittest
from pathlib import Path

from instance_lock import InstanceLock, InstanceLockError


class InstanceLockTests(unittest.TestCase):
    def test_acquire_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lock_path = Path(d) / "bot.pid"
            lock = InstanceLock(lock_path)
            lock.acquire()
            self.assertTrue(lock_path.exists())
            self.assertEqual(lock_path.read_text().strip(), str(os.getpid()))
            lock.release()
            self.assertFalse(lock_path.exists())

    def test_lock_file_permissions_are_0600(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lock_path = Path(d) / "bot.pid"
            lock = InstanceLock(lock_path)
            lock.acquire()
            mode = lock_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)
            lock.release()

    def test_stale_lock_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lock_path = Path(d) / "bot.pid"
            # Write a non-existent PID (e.g. 999999)
            lock_path.write_text("999999")
            lock = InstanceLock(lock_path)
            lock.acquire()
            self.assertEqual(lock_path.read_text().strip(), str(os.getpid()))
            lock.release()

    def test_conflict_lock_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lock_path = Path(d) / "bot.pid"
            # Current process PID as conflict
            # If we create lock1 and lock2
            lock1 = InstanceLock(lock_path)
            lock1.acquire()

            # Second lock from "another" check
            # Since lock1 has current pid, another process check:
            # Let's write pid 1 (init / systemd, always alive)
            lock_path.write_text("1")
            lock2 = InstanceLock(lock_path)
            with self.assertRaises(InstanceLockError):
                lock2.acquire()
            lock1.release()


if __name__ == "__main__":
    unittest.main()
