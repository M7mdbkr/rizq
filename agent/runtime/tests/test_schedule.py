"""Things Rizq does on a date, including the Saudi National Day discount."""
from __future__ import annotations

import datetime
import importlib
import os
import pathlib
import tempfile
import unittest

from rizq_agent import rizq, schedule

DEV = "fixtures/rizq_dev.sqlite"


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mkstemp(suffix=".sqlite")[1]
        self._old = schedule.DB
        schedule.DB = pathlib.Path(self.path)

    def tearDown(self):
        schedule.DB = self._old
        pathlib.Path(self.path).unlink(missing_ok=True)

    # ------------------------------------------------------------- dates
    def test_saudi_occasions_are_the_right_days(self):
        self.assertEqual(schedule.occasion_date("national_day", 2026),
                         datetime.date(2026, 9, 23))
        self.assertEqual(schedule.occasion_date("founding_day", 2026),
                         datetime.date(2026, 2, 22))

    def test_it_can_prepare_the_day_before(self):
        self.assertEqual(schedule.occasion_date("national_day", 2026, days_before=1),
                         datetime.date(2026, 9, 22))

    def test_an_unknown_occasion_is_refused(self):
        with self.assertRaises(ValueError):
            schedule.occasion_date("eid_al_fitr")        # Hijri: deliberately not guessed

    def test_yearly_repeat_rolls_to_next_year(self):
        start = datetime.date(2026, 9, 22)
        after = datetime.date(2026, 9, 23)
        self.assertEqual(schedule.next_occurrence("yearly", start, after),
                         datetime.date(2027, 9, 22))

    def test_monthly_repeat_survives_a_short_month(self):
        start = datetime.date(2026, 1, 31)
        got = schedule.next_occurrence("monthly", start, datetime.date(2026, 2, 1))
        self.assertEqual(got, datetime.date(2026, 2, 28))

    def test_daily_and_weekly_roll_forward(self):
        start = datetime.date(2026, 1, 1)
        self.assertEqual(schedule.next_occurrence("daily", start, datetime.date(2026, 3, 5)),
                         datetime.date(2026, 3, 5))
        weekly = schedule.next_occurrence("weekly", start, datetime.date(2026, 1, 9))
        self.assertEqual((weekly - start).days % 7, 0)
        self.assertGreaterEqual(weekly, datetime.date(2026, 1, 9))

    # ------------------------------------------------------------- the headline case
    def test_the_national_day_discount_end_to_end(self):
        job = schedule.schedule_occasion_discount("national_day", 20, "WATANI", "user_owner")
        self.assertEqual(job["repeat"], "yearly")
        fire = datetime.date.fromisoformat(job["next_run"])
        self.assertEqual(fire.month, 9)
        self.assertEqual(fire.day, 22, "it must prepare the day before")

        results = schedule.run_due(None, now=fire)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["result"]["ok"])

        queued = schedule.pending()
        self.assertEqual(len(queued), 1)
        self.assertIn("WATANI", queued[0]["summary"])
        self.assertEqual(queued[0]["payload"]["percent"], 20)
        self.assertTrue(queued[0]["payload"]["active_on"].endswith("-09-23"),
                        "the discount must be live ON National Day")

    def test_it_repeats_every_year_without_being_asked_again(self):
        job = schedule.schedule_occasion_discount("national_day", 15, "KSA", "user_owner")
        first = datetime.date.fromisoformat(job["next_run"])
        schedule.run_due(None, now=first)
        again = datetime.date.fromisoformat(schedule.jobs()[0]["next_run"])
        self.assertEqual(again.year, first.year + 1)
        self.assertEqual((again.month, again.day), (first.month, first.day))

    def test_a_queued_discount_is_not_applied_until_a_human_applies_it(self):
        job = schedule.schedule_occasion_discount("national_day", 20, "WATANI", "user_owner")
        schedule.run_due(None, now=datetime.date.fromisoformat(job["next_run"]))
        action = schedule.pending()[0]
        self.assertIsNone(action["applied_at"], "money must not move on a timer alone")
        self.assertTrue(schedule.mark_applied(action["id"], "user_owner"))
        self.assertEqual(schedule.pending(), [])
        self.assertFalse(schedule.mark_applied(action["id"], "user_owner"), "single use")

    def test_a_silly_discount_is_refused(self):
        for percent in (0, -5, 95, 1000):
            with self.assertRaises(ValueError, msg=percent):
                schedule.schedule_occasion_discount("national_day", percent, "X", "u")

    def test_a_bad_code_is_refused(self):
        for code in ("", "   ", "DROP TABLE", "a b"):
            with self.assertRaises(ValueError, msg=code):
                schedule.schedule_occasion_discount("national_day", 10, code, "u")

    # ------------------------------------------------------------- reports
    def test_a_daily_report_produces_a_readable_sentence(self):
        schedule.add("تقرير يومي", "report", "daily", datetime.date.today(),
                     {"skill": "sales_period", "params": {"days": 1}}, "user_owner")
        db = rizq.open_copy(DEV)
        try:
            results = schedule.run_due(db)
        finally:
            db.close()
        text = results[0]["result"]["text"]
        self.assertIn("مبيعات", text)
        self.assertRegex(text, r"\d")
        self.assertNotIn("{", text, "a report must not be JSON")

    def test_a_report_without_a_database_fails_softly(self):
        schedule.add("تقرير", "report", "once", datetime.date.today(),
                     {"skill": "sales_period"}, "u")
        self.assertFalse(schedule.run_due(None)[0]["result"]["ok"])

    def test_a_once_job_disables_itself_after_running(self):
        schedule.add("مرة واحدة", "reminder", "once", datetime.date.today(), {"text": "x"}, "u")
        schedule.run_due(None)
        self.assertEqual(schedule.jobs(), [])
        self.assertEqual(len(schedule.jobs(include_disabled=True)), 1)

    def test_one_broken_job_does_not_stop_the_others(self):
        schedule.add("سيئة", "report", "once", datetime.date.today(),
                     {"skill": "no_such_skill"}, "u")
        schedule.add("سليمة", "reminder", "once", datetime.date.today(), {"text": "ok"}, "u")
        results = schedule.run_due(None)
        self.assertEqual(len(results), 2)
        self.assertTrue(any(r["result"].get("ok") for r in results))

    # ------------------------------------------------------------- management
    def test_jobs_can_be_listed_disabled_and_removed(self):
        job = schedule.add("مهمة", "reminder", "daily", datetime.date.today(), {"text": "t"}, "u")
        self.assertEqual(len(schedule.jobs()), 1)
        schedule.set_enabled(job["id"], False)
        self.assertEqual(schedule.jobs(), [])
        schedule.set_enabled(job["id"], True)
        self.assertTrue(schedule.remove(job["id"]))
        self.assertEqual(schedule.jobs(), [])

    def test_bad_definitions_are_refused(self):
        today = datetime.date.today()
        with self.assertRaises(ValueError):
            schedule.add("x", "nonsense", "daily", today, {}, "u")
        with self.assertRaises(ValueError):
            schedule.add("x", "reminder", "fortnightly", today, {}, "u")
        with self.assertRaises(ValueError):
            schedule.add("   ", "reminder", "daily", today, {}, "u")

    def test_nothing_runs_before_it_is_due(self):
        schedule.add("بكرة", "reminder", "once",
                     datetime.date.today() + datetime.timedelta(days=5), {"text": "x"}, "u")
        self.assertEqual(schedule.run_due(None), [])


if __name__ == "__main__":
    unittest.main()
