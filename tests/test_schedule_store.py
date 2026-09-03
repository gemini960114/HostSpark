import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hostspark.storage.schedule_store import (
    NO_REPORT_SENTINEL,
    ScheduleError,
    ScheduleStore,
    build_prompt_expansion_request,
    next_run_time,
    normalize_cron,
    parse_schedule_add_payload,
    render_prompt_variables,
)


UTC = timezone.utc


class ScheduleExpressionTests(unittest.TestCase):
    def test_valid_hourly_cron(self) -> None:
        self.assertEqual(normalize_cron("0  * * * *", "Asia/Taipei", 15), "0 * * * *")

    def test_invalid_cron_is_rejected(self) -> None:
        with self.assertRaises(ScheduleError):
            normalize_cron("not a cron", "UTC", 15)

    def test_cron_without_a_possible_date_is_rejected(self) -> None:
        with self.assertRaises(ScheduleError):
            normalize_cron("0 0 31 2 *", "UTC", 15)

    def test_too_frequent_cron_is_rejected(self) -> None:
        with self.assertRaisesRegex(ScheduleError, "不得少於 15 分鐘"):
            normalize_cron("*/5 * * * *", "UTC", 15)

    def test_next_run_uses_configured_timezone(self) -> None:
        after = datetime(2026, 8, 30, 0, 30, tzinfo=UTC)
        result = next_run_time("0 9 * * *", "Asia/Taipei", after)
        self.assertEqual(result, datetime(2026, 8, 30, 1, 0, tzinfo=UTC))

    def test_add_payload_preserves_multiline_prompt(self) -> None:
        cron_expr, prompt = parse_schedule_add_payload(
            "0 * * * * 查詢天氣\n若下雨才通知"
        )
        self.assertEqual(cron_expr, "0 * * * *")
        self.assertEqual(prompt, "查詢天氣\n若下雨才通知")


class PromptTests(unittest.TestCase):
    def test_expansion_prompt_describes_variables_and_no_report(self) -> None:
        result = build_prompt_expansion_request("沒有異常不通知", "0 * * * *", "Asia/Taipei")
        self.assertIn("{{now}}", result)
        self.assertIn(NO_REPORT_SENTINEL, result)
        self.assertIn("沒有異常不通知", result)

    def test_known_variables_are_rendered_and_unknown_are_preserved(self) -> None:
        scheduled = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
        rendered = render_prompt_variables(
            "日期={{date}} 時間={{time}} 次數={{run_number}} 自訂={{city}}",
            timezone_name="Asia/Taipei",
            scheduled_at=scheduled,
            run_number=3,
            now=scheduled,
        )
        self.assertIn("日期=2026-08-30", rendered)
        self.assertIn("時間=09:00:00", rendered)
        self.assertIn("次數=3", rendered)
        self.assertIn("自訂={{city}}", rendered)


class ScheduleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "schedules.db"
        self.store = ScheduleStore(self.db_path)
        self.base = datetime(2026, 8, 30, 0, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _add(self):
        return self.store.add(
            cron_expr="0 * * * *",
            timezone_name="UTC",
            original_prompt="檢查狀態",
            prompt_template="在 {{now}} 檢查狀態",
            now=self.base,
        )

    def test_crud_and_persistence(self) -> None:
        schedule = self._add()
        reopened = ScheduleStore(self.db_path)
        self.assertEqual(reopened.get(schedule.id).original_prompt, "檢查狀態")
        self.assertTrue(reopened.pause(schedule.id))
        self.assertFalse(reopened.get(schedule.id).enabled)
        self.assertTrue(reopened.resume(schedule.id, self.base))
        self.assertTrue(reopened.get(schedule.id).enabled)
        self.assertTrue(reopened.delete(schedule.id))
        self.assertIsNone(reopened.get(schedule.id))

    def test_claim_due_runs_once_and_skips_missed_occurrences(self) -> None:
        schedule = self._add()
        now = datetime(2026, 8, 30, 3, 30, tzinfo=UTC)
        due = self.store.claim_due(now)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].schedule.id, schedule.id)
        self.assertEqual(due[0].scheduled_at, datetime(2026, 8, 30, 1, 0, tzinfo=UTC))
        self.assertEqual(
            self.store.get(schedule.id).next_run_at,
            datetime(2026, 8, 30, 4, 0, tzinfo=UTC),
        )
        self.assertEqual(self.store.claim_due(now), [])

    def test_three_failures_auto_pause(self) -> None:
        schedule = self._add()
        self.assertFalse(self.store.record_result(schedule.id, success=False, error="one"))
        self.assertFalse(self.store.record_result(schedule.id, success=False, error="two"))
        self.assertTrue(self.store.record_result(schedule.id, success=False, error="three"))
        stored = self.store.get(schedule.id)
        self.assertFalse(stored.enabled)
        self.assertEqual(stored.consecutive_failures, 3)
        self.assertIsNone(stored.next_run_at)

    def test_success_resets_failure_count(self) -> None:
        schedule = self._add()
        self.store.record_result(schedule.id, success=False, error="one")
        self.store.record_result(schedule.id, success=True)
        stored = self.store.get(schedule.id)
        self.assertEqual(stored.consecutive_failures, 0)
        self.assertEqual(stored.run_count, 2)
        self.assertEqual(stored.last_status, "success")


if __name__ == "__main__":
    unittest.main()
