import unittest
from datetime import datetime, timedelta, timezone

from hostspark.runtime.pending_actions import PendingActionStore


UTC = timezone.utc


class PendingActionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PendingActionStore()

    def test_put_and_get_pop(self) -> None:
        t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        token = self.store.put("schedule_add", 123, {"foo": "bar"}, ttl_minutes=15, now=t0)
        self.assertTrue(bool(token))

        # Check get
        action = self.store.get(token, user_id=123, now=t0)
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, "schedule_add")
        self.assertEqual(action.payload, {"foo": "bar"})

        # Wrong user
        self.assertIsNone(self.store.get(token, user_id=999, now=t0))

        # Pop
        popped = self.store.pop(token, user_id=123, now=t0)
        self.assertEqual(popped, action)

        # After pop, it's gone
        self.assertIsNone(self.store.get(token, user_id=123, now=t0))

    def test_expiration(self) -> None:
        t0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        token = self.store.put("agy_confirm", 123, "cmd", ttl_minutes=15, now=t0)

        # Before expiration
        t1 = t0 + timedelta(minutes=14, seconds=59)
        self.assertIsNotNone(self.store.get(token, user_id=123, now=t1))

        # After expiration
        t2 = t0 + timedelta(minutes=15, seconds=1)
        self.assertIsNone(self.store.get(token, user_id=123, now=t2))
        self.assertIsNone(self.store.pop(token, user_id=123, now=t2))


if __name__ == "__main__":
    unittest.main()
