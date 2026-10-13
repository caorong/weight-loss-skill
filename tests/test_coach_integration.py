"""Installed full-loop and date-boundary tests; no real channels or health data."""
import ast
import concurrent.futures
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "diet-tracking-analysis/scripts/meal-log.py"
spec = importlib.util.spec_from_file_location("date_meals", SCRIPT)
meals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(meals)


def food():
    return {"name": "SYNTHETIC", "quantity": 100, "unit": "g", "calories": 200, "protein": 5,
            "carbs": 40, "fat": 2, "source": "user_provided", "portion_source": "user_measured"}


class DateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = Path(self.temp.name)
        self.ledger = meals.Ledger(self.ws, "Asia/Tokyo")

    def tearDown(self):
        self.temp.cleanup()

    def at(self, day, hour):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, day, hour, tzinfo=ZoneInfo("Asia/Tokyo")).astimezone(tz)
        return mock.patch.object(meals, "datetime", Clock)

    def request(self, token, date="today"):
        return {"request_id": token, "date": date, "meal_type": "lunch", "foods": [food()]}

    def test_new_operation_changes_day_without_reading_more_history(self):
        with self.at(13, 23):
            a = self.ledger.execute("add", self.request("first"))
        with self.at(14, 1):
            b = self.ledger.execute("add", self.request("second"))
        self.assertEqual((a["date"], b["date"]), ("2026-10-13", "2026-10-14"))

    def test_same_retry_after_midnight_keeps_previous_day(self):
        req = self.request("same")
        with self.at(13, 23):
            self.ledger.execute("add", req)
        with self.at(14, 1):
            r = self.ledger.execute("add", req)
        self.assertTrue(r["duplicate"])
        self.assertEqual(r["date"], "2026-10-13")
        self.assertFalse(self.ledger.day_path("2026-10-14").exists())

    def test_yesterday_is_resolved_at_first_execution(self):
        with self.at(14, 1):
            r = self.ledger.execute("add", self.request("backfill", "yesterday"))
        self.assertEqual(r["date"], "2026-10-13")

    def test_explicit_iso_backfill_preserved(self):
        with self.at(14, 1):
            r = self.ledger.execute("add", self.request("backfill", "2026-10-01"))
        self.assertEqual(r["date"], "2026-10-01")

    def test_two_private_request_files_never_replace_each_other(self):
        a, b = self.ledger.execute("init"), self.ledger.execute("init")
        self.assertNotEqual(a["request_file"], b["request_file"])
        paths = [Path(a["request_file"]), Path(b["request_file"])]
        for i, p in enumerate(paths):
            p.write_text(json.dumps(self.request(f"private:{i}", "2020-01-01")))
        def call(p):
            return subprocess.run([sys.executable, str(SCRIPT), "add", "--workspace", str(self.ws),
                "--tz", "Asia/Tokyo", "--input", str(p)], capture_output=True, text=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call, paths))
        self.assertTrue(all(r.returncode == 0 for r in results))
        self.assertEqual(len(self.ledger.execute("list", day="2020-01-01")["meals"]), 2)


class FullInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="full coach install ")
        self.ws = Path(self.temp.name)
        r = subprocess.run([sys.executable, str(ROOT / "scripts/install-standalone.py"), "--workspace", str(self.ws),
                             "--timezone", "Asia/Tokyo", "--runner", "python"], text=True, capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.receipt = json.loads(r.stdout)
        self.runtime = json.loads((self.ws / "skills/diet-tracking-analysis/runtime.json").read_text())
        self.n = 0

    def tearDown(self):
        self.temp.cleanup()

    def call(self, command, action, data=None):
        self.n += 1
        if data is not None:
            data = {"request_id": f"full:{self.n}", **data}
        r = subprocess.run(self.runtime["commands"][command] + [action, "--workspace", str(self.ws), "--tz", "Asia/Tokyo"],
                           cwd="/tmp", input=json.dumps(data) if data is not None else None,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_installer_reports_coach_smoke_separately(self):
        self.assertTrue(self.receipt["coach_self_test"]["ok"])
        self.assertGreaterEqual(len(self.receipt["coach_self_test"]["checks"]), 10)
        self.assertEqual(self.receipt["native_scheduler"], "not_tested")
        self.assertEqual(self.receipt["model_vision"], "not_tested")

    def test_installer_does_not_seed_actual_profiles_measurements_plans(self):
        for name in ["health-profile.md", "health-preferences.md", "PLAN.md", "data/weight.json",
                     "data/exercise.json", "data/plans.json", "data/coach-reminders.json"]:
            self.assertFalse((self.ws / name).exists(), name)

    def test_complete_local_loop_from_installed_paths(self):
        info = self.call("coach", "context")
        self.call("coach", "profile-set", {"expected_revision": 0, "fields": {"height_cm": 170}})
        self.call("meals", "add", {"meal_type": "lunch", "date": info["today"], "foods": [food()]})
        self.call("coach", "weight-add", {"value": 80, "unit": "kg"})
        self.call("coach", "exercise-add", {"items": [{"activity": "SYNTHETIC", "duration_min": 30}]})
        p = self.call("coach", "plan-save", {"kind": "goal", "body": "SYNTHETIC GOAL"})["plan"]
        self.call("coach", "plan-activate", {"plan_id": p["id"], "confirmed": True})
        report = self.call("coach", "review")
        self.assertEqual(report["summary"]["recorded_totals"]["calories"], 200)
        self.assertEqual(report["summary"]["weight_days"], 1)
        self.assertEqual(report["summary"]["exercise_minutes"], 30)
        out = self.call("coach", "review-save", {"start": report["start"], "end": report["end"],
                    "expected_snapshot": report["snapshot"], "analysis": "SYNTHETIC LIMITED DATA"})
        self.assertTrue((self.ws / out["path"]).exists())

    def test_installed_coach_runs_without_source_cwd(self):
        self.assertTrue(self.call("coach", "context")["ok"])
        self.assertTrue(self.call("coach", "review")["ok"])

    def test_installed_docs_do_not_require_author_services(self):
        base = self.ws / "skills/diet-tracking-analysis"
        skill = (base / "SKILL.md").read_text()
        self.assertIn("Every", skill.replace("EVERY", "Every"))
        self.assertIn("commands.coach", skill)
        self.assertNotIn("/home/nanorhino", skill)
        self.assertTrue((base / "references/native-reminders.md").exists())

    def test_plan_and_profile_updates_do_not_create_reminders(self):
        self.call("coach", "profile-set", {"expected_revision": 0, "fields": {"height_cm": 170}})
        p = self.call("coach", "plan-save", {"kind": "meal", "body": "SYNTHETIC MEAL PLAN"})["plan"]
        self.call("coach", "plan-activate", {"plan_id": p["id"], "confirmed": True})
        self.assertEqual(self.call("coach", "reminder-specs")["specs"], [])

    def test_installed_scripts_parse_as_python310(self):
        for p in (self.ws / "skills/diet-tracking-analysis/scripts").glob("*.py"):
            ast.parse(p.read_text(), filename=str(p), feature_version=(3, 10))


if __name__ == "__main__":
    unittest.main()
