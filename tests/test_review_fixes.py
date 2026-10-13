"""PR #2 regressions. Synthetic data only; no model, gateway or network calls."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "diet-tracking-analysis/scripts"))
import coach


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="coach review regression ")
        self.ws = Path(self.tmp.name)
        self.now = datetime(2026, 10, 13, 9, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.c = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.now)
        self.seq = 0

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, action, **data):
        self.seq += 1
        return self.c.execute(action, {"request_id": f"regression:{self.seq}", **data})

    def exercise(self):
        return self.write("exercise-add", date="yesterday", items=[{
            "activity": "SYNTHETIC run", "duration_min": 30, "category": "cardio",
            "intensity": "high", "note": "SYNTHETIC running note", "source": "device",
            "calories_kcal": 300, "net_calories_kcal": 260
        }])["exercises"][0]

    def replace(self, row, **fields):
        return self.write("exercise-update", id=row["id"], expected_revision=row["revision"],
                          item={"activity": "SYNTHETIC walk", "duration_min": 20, **fields})["exercise"]

    def test_replacement_drops_omitted_fields_and_keeps_metadata(self):
        row = self.exercise()
        self.now += timedelta(hours=2)
        updated = self.replace(row)
        for key in ("category", "intensity", "note", "index"):
            self.assertNotIn(key, updated)
        self.assertEqual(updated["id"], row["id"])
        self.assertEqual(updated["recorded_at"], row["recorded_at"])
        self.assertEqual(updated["date"], "2026-10-12")
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["updated_at"], self.now.isoformat())
        self.assertEqual(updated["source"], "user_reported")
        self.assertIsNone(updated["calories_kcal"])
        self.assertIsNone(updated["net_calories_kcal"])
        persisted = json.loads((self.ws / "data/exercise.json").read_text())["2026-10-12"]
        self.assertEqual(persisted["exercises"], [updated])
        self.assertFalse(persisted["calories_complete"])

    def test_replacement_uses_new_optional_values(self):
        updated = self.replace(self.exercise(), category="daily_activity", intensity="low",
                               note="SYNTHETIC walking note", calories_kcal=80, net_calories_kcal=60,
                               source="met_estimate")
        self.assertEqual(updated["intensity"], "low")
        self.assertEqual(updated["category"], "daily_activity")
        self.assertEqual(updated["note"], "SYNTHETIC walking note")
        self.assertEqual(updated["calories_kcal"], 80)
        self.assertEqual(updated["source"], "met_estimate")

    def test_replacement_does_not_modify_another_exercise(self):
        row = self.exercise()
        sibling = self.write("exercise-add", date="yesterday",
                             items=[{"activity": "SYNTHETIC stretch", "duration_min": 10, "note": "keep"}])["exercises"][0]
        self.replace(row)
        saved = json.loads((self.ws / "data/exercise.json").read_text())["2026-10-12"]["exercises"]
        self.assertEqual(saved[1], sibling)

    def test_legacy_replacement_drops_stale_fields_without_inventing_timestamp(self):
        self.c.execute("context")
        (self.ws / "data/exercise.json").write_text(json.dumps({"2026-10-12": {"exercises": [{
            "activity": "SYNTHETIC run", "duration_min": 30, "category": "cardio", "note": "old",
            "met": 8.0, "distance": 5.0
        }]}}))
        row = self.c.execute("measurements", period="yesterday")["exercises"][0]
        updated = self.replace(row)
        for key in ("category", "note", "met", "distance", "recorded_at", "index"):
            self.assertNotIn(key, updated)
        self.assertEqual(updated["id"], row["id"])
        self.assertEqual(updated["revision"], 1)

    def test_replacement_retry_and_revision_guards_unchanged(self):
        row = self.exercise()
        payload = {"request_id": "replacement-retry", "id": row["id"], "expected_revision": 1,
                   "item": {"activity": "SYNTHETIC walk", "duration_min": 20}}
        first = self.c.execute("exercise-update", payload)
        second = self.c.execute("exercise-update", copy.deepcopy(payload))
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["exercise"], second["exercise"])
        before = (self.ws / "data/exercise.json").read_bytes()
        with self.assertRaises(coach.CoachError):
            self.c.execute("exercise-update", {**payload, "request_id": "stale-new-request"})
        self.assertEqual((self.ws / "data/exercise.json").read_bytes(), before)

    def seed_weights(self, offsets):
        for days_ago, value in offsets:
            day = (self.now.date() - timedelta(days=days_ago)).isoformat()
            self.write("weight-add", date=day, value=value, unit="kg")

    def test_long_review_uses_only_two_adjacent_seven_day_buckets(self):
        self.seed_weights([(i, 90 if i >= 14 else 80 if i >= 7 else 79) for i in range(30)])
        report = self.c.execute("review", start="2026-09-14", end="2026-10-13")
        self.assertEqual(report["summary"]["weight_days"], 30)
        self.assertEqual(report["summary"]["two_week_mean_change_kg"], -1.0)
        # The review still keeps all requested measurements; only trend bucketing changes.
        self.assertEqual(report["summary"]["weight_change_kg"], -11.0)
        fortnight = self.c.execute("review", period="last14")
        self.assertEqual(report["summary"]["two_week_mean_change_kg"],
                         fortnight["summary"]["two_week_mean_change_kg"])

    def test_previous_window_includes_day_minus_13_and_minus_7_not_minus_14(self):
        self.seed_weights([(14, 200), (13, 80), (10, 80), (7, 80), (6, 79), (3, 79), (0, 79)])
        report = self.c.execute("review", start="2026-09-29", end="2026-10-13")
        self.assertEqual(report["summary"]["two_week_mean_change_kg"], -1.0)

    def test_older_weights_cannot_fill_missing_previous_week(self):
        self.seed_weights([(20, 90), (19, 90), (18, 90), (12, 80), (10, 80), (6, 79), (3, 79), (0, 79)])
        report = self.c.execute("review", start="2026-09-14", end="2026-10-13")
        self.assertIsNone(report["summary"]["two_week_mean_change_kg"])

    def test_sparse_recent_week_still_returns_unknown(self):
        self.seed_weights([(13, 80), (10, 80), (7, 80), (6, 79), (0, 79)])
        self.assertIsNone(self.c.execute("review", period="last14")["summary"]["two_week_mean_change_kg"])

    def test_historical_range_anchors_trend_to_range_end_not_today(self):
        self.seed_weights([(i, 90 if i >= 18 else 80 if i >= 11 else 79 if i >= 4 else 200) for i in range(34)])
        report = self.c.execute("review", start="2026-09-10", end="2026-10-09")
        self.assertEqual(report["summary"]["two_week_mean_change_kg"], -1.0)

    def test_seven_day_review_has_no_previous_week(self):
        self.seed_weights([(13, 80), (10, 80), (7, 80), (6, 79), (3, 79), (0, 79)])
        self.assertIsNone(self.c.execute("review", period="last7")["summary"]["two_week_mean_change_kg"])


if __name__ == "__main__":
    unittest.main()
