"""Synthetic, offline cadence/notification regressions; no native jobs created."""
import concurrent.futures
import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests import test_coach

coach = test_coach.coach


class ReviewCadenceTests(unittest.TestCase):
    setUp = test_coach.CoachTests.setUp
    tearDown = test_coach.CoachTests.tearDown
    write = test_coach.CoachTests.write
    bind = test_coach.CoachTests.bind
    weight = test_coach.CoachTests.weight
    # Reuse fixture helpers but do not inherit the entire parent test suite.
    def configure(self, key, review_type, time="09:00", weekdays=None, **patch):
        settings = {"kind": "review", "review_type": review_type, "enabled": True,
                    "time": time, "weekdays": weekdays or (list(range(7)) if review_type == "daily" else [2]),
                    "route": {"agent": "coach", "channel": "test", "to": "synthetic"}, **patch}
        self.write("reminder-set", key=key, settings=settings, confirmed=True,
                   expected_revision=self.c.execute("reminders")["revision"])
        spec = next(s for s in self.c.execute("reminder-specs")["specs"] if s["key"] == key)
        self.bind(spec)
        return spec

    def test_typed_default_windows_and_legacy_manual(self):
        daily = self.c.execute("review", review_type="daily")
        self.assertEqual((daily["start"], daily["end"]), ("2026-10-13", "2026-10-13"))
        weekly = self.c.execute("review", review_type="weekly")
        self.assertEqual((weekly["start"], weekly["end"]), ("2026-10-05", "2026-10-11"))
        legacy = self.c.execute("review")
        self.assertEqual(legacy["review_type"], "custom")
        self.assertEqual(legacy["start"], "2026-10-07")
        for kind, period in [("daily", "last7"), ("weekly", "today"), ("weekly", "this-week")]:
            with self.assertRaises(ValueError):
                self.c.execute("review", review_type=kind, period=period)

    def test_weekly_sunday_and_monday_use_closed_previous_week(self):
        self.time = self.time.replace(day=18)
        self.assertEqual(self.c.execute("review", review_type="weekly")["end"], "2026-10-11")
        with self.assertRaises(ValueError):
            self.c.execute("review", review_type="weekly", period="this-week")
        self.time = self.time.replace(day=19)
        self.assertEqual(self.c.execute("review", review_type="weekly")["end"], "2026-10-18")

    def test_typed_save_survives_elapsed_clock_but_not_data_changes(self):
        report = self.c.execute("review", review_type="daily")
        self.time += timedelta(minutes=1)
        p = dict(request_id="daily-save", review_type="daily", start=report["start"], end=report["end"],
                 expected_snapshot=report["snapshot"], analysis="Recorded data only; completeness unverified.")
        out = self.c.execute("review-save", p)
        self.assertIn("coach-review-daily-", out["path"])
        self.assertTrue(self.c.execute("review-save", p)["duplicate"])
        self.weight()
        with self.assertRaises(ValueError):
            self.c.execute("review-save", dict(p, request_id="stale-save"))

    def test_daily_gate_uses_today_and_deduplicates(self):
        self.configure("daily", "daily")
        first = self.c.execute("reminder-gate", key="daily")
        self.assertEqual(first["review_period"], "today")
        self.assertEqual(first["reviews"][0]["start"], "2026-10-13")
        self.assertFalse(first["delivered"])
        self.assertFalse(self.c.execute("reminder-gate", key="daily")["send"])
        self.weight()  # Late/corrected data cannot cause a repeat scheduled notification.
        self.assertFalse(self.c.execute("reminder-gate", key="daily")["send"])
        self.time += timedelta(days=1)
        self.assertTrue(self.c.execute("reminder-gate", key="daily")["send"])

    def test_same_time_daily_weekly_combine_in_either_order(self):
        self.configure("daily", "daily")
        self.configure("weekly", "weekly")
        loser = self.c.execute("reminder-gate", key="weekly")
        self.assertEqual(loser["reason"], "combined_with_other_review")
        winner = self.c.execute("reminder-gate", key="daily")
        self.assertEqual({r["review_type"] for r in winner["reviews"]}, {"daily", "weekly"})
        self.assertEqual(winner["reviews"][1]["end"], "2026-10-11")
        self.assertFalse(self.c.execute("reminder-gate", key="daily")["send"])
        self.assertFalse(self.c.execute("reminder-gate", key="weekly")["send"])

    def test_distinct_routes_are_not_suppressed_by_each_other(self):
        self.configure("daily", "daily")
        self.configure("other", "daily", route={"agent": "coach", "channel": "other", "to": "synthetic"})
        first = self.c.execute("reminder-gate", key="daily")
        second = self.c.execute("reminder-gate", key="other")
        self.assertTrue(first["send"] and second["send"])
        self.assertNotEqual(first["reviews"][0]["notification_key"], second["reviews"][0]["notification_key"])

    def test_concurrent_gate_reserves_only_once(self):
        self.configure("daily", "daily")
        def run(_):
            return coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.time).execute("reminder-gate", key="daily")["send"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(run, range(4))), 1)

    def test_disabled_weekly_does_not_block_daily(self):
        self.configure("aaa_weekly", "weekly", enabled=False)
        self.configure("daily", "daily")
        self.assertEqual(len(self.c.execute("reminder-gate", key="daily")["reviews"]), 1)

    def test_kind_migration_requires_new_binding_and_preserves_other(self):
        self.configure("daily", "daily")
        self.configure("weekly", "weekly")
        old = self.c.execute("reminders")
        weekly = old["items"]["weekly"]
        self.write("reminder-set", key="daily", settings={"review_type": "weekly", "weekdays": [2]},
                   confirmed=True, expected_revision=old["revision"])
        self.assertEqual(self.c.execute("reminder-gate", key="daily")["reason"], "gateway_sync_required")
        self.assertEqual(self.c.execute("reminders")["items"]["weekly"], weekly)

    def test_existing_legacy_review_retains_rolling_semantics(self):
        self.configure("legacy", "daily")
        path = self.ws / "data/coach-reminders.json"
        data = json.loads(path.read_text())
        data["items"]["legacy"].pop("review_type")
        path.write_text(json.dumps(data))
        self.bind(self.c.execute("reminder-specs")["specs"][0])
        result = self.c.execute("reminder-gate", key="legacy")
        self.assertEqual(result["review_period"], "last7")
        self.assertEqual(result["review_type"], "custom")
        old = self.c.execute("reminders")
        self.write("reminder-set", key="legacy", settings={"enabled": False}, expected_revision=old["revision"])
        self.assertNotIn("review_type", self.c.execute("reminders")["items"]["legacy"])

    def test_new_review_requires_type_and_cadence(self):
        for typ, days in [("daily", [2]), ("weekly", list(range(7))), ("unknown", [2])]:
            with self.assertRaises(ValueError):
                self.configure("new", typ, weekdays=days)
        settings = {"kind": "review", "enabled": False, "time": "09:00", "weekdays": [2],
                    "route": {"agent": "coach", "channel": "test", "to": "synthetic"}}
        with self.assertRaises(ValueError):
            self.write("reminder-set", key="new", settings=settings, expected_revision=0)

    def test_timezone_boundary_and_stale_delivery(self):
        self.time = datetime(2026, 10, 13, 15, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(self.c.execute("review", review_type="daily")["start"], "2026-10-14")
        self.configure("daily", "daily", time="23:59")
        self.assertFalse(self.c.execute("reminder-gate", key="daily")["send"])

    def test_dst_fold_sends_at_most_once(self):
        self.time = datetime(2026, 11, 1, 5, 30, tzinfo=ZoneInfo("UTC"))
        self.c = coach.Coach(self.ws, "America/New_York", clock=lambda: self.time)
        self.configure("daily", "daily", time="01:30")
        self.assertTrue(self.c.execute("reminder-gate", key="daily")["send"])
        self.time += timedelta(hours=1)
        self.assertFalse(self.c.execute("reminder-gate", key="daily")["send"])

