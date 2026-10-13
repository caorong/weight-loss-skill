"""Offline regression tests; fixtures are synthetic, never personal health data."""
import concurrent.futures
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "diet-tracking-analysis/scripts/meal-log.py"
spec = importlib.util.spec_from_file_location("meal_log", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def food(name="test rice", calories=200):
    return {"name": name, "quantity": 150, "unit": "g", "calories": calories,
            "protein": 4, "carbs": 40, "fat": 2, "source": "model_estimate",
            "portion_source": "photo_estimate", "note": "Synthetic test values, not nutrition advice"}


def request(key="message-1", **extra):
    return {"request_id": key, "date": "2020-01-02", "meal_type": "lunch", "foods": [food()], **extra}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.ledger = mod.Ledger(self.workspace, "Asia/Tokyo")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, key="message-1", **kwargs):
        return self.ledger.execute("add", request(key, **kwargs))

    def mutation(self, result, key="message-2", **extra):
        return {"request_id": key, "date": result["date"], "meal_id": result["meal_id"], **extra}

    def cli(self, action, data=None, *flags):
        command = [sys.executable, str(SCRIPT), action, "--workspace", str(self.workspace),
                   "--tz", "Asia/Tokyo", *flags]
        return subprocess.run(command, input=json.dumps(data) if data is not None else "",
                              capture_output=True, text=True, timeout=15)

    def test_init_does_not_seed_food_or_profile(self):
        out = self.ledger.execute("init")
        self.assertTrue(out["writable"])
        self.assertEqual(out["timezone"], "Asia/Tokyo")
        self.assertFalse((self.workspace / "USER.md").exists())
        self.assertEqual(list((self.workspace / "data/meals").glob("*.json")), [])

    def test_add_sum_foods_and_preserve_sources(self):
        result = self.add(foods=[food(), food("test egg", 80)])
        self.assertEqual(result["logged_totals"]["calories"], 280)
        self.assertEqual(result["meal"]["protein"], 8)
        self.assertEqual(result["meal"]["foods"][0]["source"], "model_estimate")

    def test_duplicate_add_and_payload_conflict(self):
        a, b = self.add(), self.add()
        self.assertEqual(a["meal_id"], b["meal_id"])
        self.assertTrue(b["duplicate"])
        with self.assertRaises(mod.LedgerError):
            self.add(foods=[food("different", 400)])
        self.assertEqual(len(self.ledger.load("2020-01-02")), 1)

    def test_same_food_new_message_is_a_new_record(self):
        self.add()
        self.add("message-2")
        self.assertEqual(self.ledger.execute("list", day="2020-01-02")["logged_totals"]["calories"], 400)

    def test_append_and_idempotent_retry(self):
        a = self.add()
        req = self.mutation(a, foods=[food("test banana", 50)])
        out = self.ledger.execute("append", req)
        self.assertEqual(out["meal"]["calories"], 250)
        self.assertTrue(self.ledger.execute("append", req)["duplicate"])
        self.assertEqual(len(self.ledger.load(a["date"])[0]["foods"]), 2)

    def test_scale_only_selected_food(self):
        a = self.add(foods=[food(), food("unchanged", 80)])
        req = self.mutation(a, food_id=a["meal"]["foods"][0]["id"], scale=0.5, expected_revision=1)
        out = self.ledger.execute("update", req)
        self.assertEqual(out["meal"]["foods"][0]["quantity"], 75)
        self.assertEqual(out["meal"]["foods"][1], a["meal"]["foods"][1])
        self.assertEqual(out["logged_totals"]["calories"], 180)
        self.assertTrue(self.ledger.execute("update", req)["duplicate"])
        self.assertEqual(self.ledger.load(a["date"])[0]["calories"], 180)

    def test_replace_food_retains_id(self):
        a = self.add()
        fid = a["meal"]["foods"][0]["id"]
        out = self.ledger.execute("update", self.mutation(a, food_id=fid, food=food("replacement", 150)))
        self.assertEqual(out["meal"]["foods"][0]["id"], fid)
        self.assertEqual(out["meal"]["calories"], 150)

    def test_revision_conflict_is_not_a_write(self):
        a = self.add()
        with self.assertRaises(mod.LedgerError):
            self.ledger.execute("update", self.mutation(a, food_id=a["meal"]["foods"][0]["id"],
                                                       scale=0.5, expected_revision=0))
        self.assertEqual(self.ledger.load(a["date"])[0]["calories"], 200)

    def test_delete_item_and_delete_last_item_removes_meal(self):
        a = self.add(foods=[food(), food("drink", 100)])
        req = self.mutation(a, food_id=a["meal"]["foods"][1]["id"])
        self.assertEqual(self.ledger.execute("delete", req)["logged_totals"]["calories"], 200)
        req = self.mutation(a, "message-3", food_id=a["meal"]["foods"][0]["id"])
        self.assertIsNone(self.ledger.execute("delete", req)["meal"])
        self.assertEqual(self.ledger.load(a["date"]), [])
        self.assertTrue(self.ledger.execute("delete", req)["duplicate"])

    def test_delete_whole_meal_and_retry_after_deletion(self):
        a = self.add()
        req = self.mutation(a)
        self.ledger.execute("delete", req)
        self.assertTrue(self.ledger.execute("delete", req)["duplicate"])
        # Replaying the original add must not resurrect the deleted meal.
        self.assertTrue(self.add()["duplicate"])
        self.assertEqual(self.ledger.load(a["date"]), [])

    def test_backdating_and_missing_days_not_zero_intake(self):
        self.add()
        self.assertEqual(self.ledger.execute("list")["meals"], [])
        out = self.ledger.execute("summary", start="2020-01-01", end="2020-01-07")
        self.assertEqual(out["logged_days"], 1)
        self.assertEqual(out["average_per_logged_day"]["calories"], 200)
        self.assertEqual(out["coverage"], "recorded_only")
        self.assertFalse(out["days"][0]["has_records"])

    def test_no_target_required_and_explicit_target(self):
        self.add()
        self.assertIsNone(self.ledger.execute("summary", day="2020-01-02")["target_calories"])
        out = self.ledger.execute("summary", day="2020-01-02", target=1800)
        self.assertEqual(out["remaining_vs_logged"], 1600)

    def test_summary_range_checks(self):
        for start, end in [("2020-01-02", "2020-01-01"), ("2020-01-01", "2022-01-01")]:
            with self.subTest(start=start), self.assertRaises(mod.LedgerError):
                self.ledger.execute("summary", start=start, end=end)

    def test_reject_bad_numbers_and_missing_nutrients(self):
        for value in [-1, float("nan"), float("inf"), True, "200", None, 100001]:
            with self.subTest(value=value), self.assertRaises(mod.LedgerError):
                self.add(foods=[food(calories=value)])
        bad = food()
        del bad["protein"]
        with self.assertRaises(mod.LedgerError):
            self.add(foods=[bad])

    def test_reject_planned_future_and_bad_date(self):
        tomorrow = (datetime.now(ZoneInfo("Asia/Tokyo")) + timedelta(days=1)).date().isoformat()
        for extra in [{"status": "planned"}, {"date": tomorrow}, {"date": "2020-02-31"},
                      {"date": "../../USER"}, {"foods": []}, {"meal_type": "unknown"}]:
            with self.subTest(extra=extra), self.assertRaises(mod.LedgerError):
                self.add(**extra)

    def test_timestamp_offset_consistency(self):
        out = self.add(consumed_at="2020-01-01T16:00:00Z")  # Jan 2 in Tokyo
        self.assertEqual(out["date"], "2020-01-02")
        for timestamp in ["2020-01-01T12:00:00Z", "2020-01-02T12:00:00", "bad"]:
            with self.subTest(timestamp=timestamp), self.assertRaises(mod.LedgerError):
                self.add("message-2", consumed_at=timestamp)

    def test_timezone_invalid_does_not_default_to_server(self):
        with self.assertRaises(mod.LedgerError):
            mod.Ledger(self.workspace, "not/a/timezone")

    def test_timezone_not_system_local_date(self):
        east = mod.Ledger(self.workspace, "Pacific/Kiritimati").execute("init")
        west = mod.Ledger(self.workspace, "Pacific/Honolulu").execute("init")
        self.assertEqual((mod.day_value(east["today"]) - mod.day_value(west["today"])).days, 1)

    def test_restart_reads_same_data(self):
        self.add()
        fresh = mod.Ledger(self.workspace, "Asia/Tokyo")
        self.assertEqual(fresh.execute("list", day="2020-01-02")["logged_totals"]["calories"], 200)

    def test_backup_before_mutation(self):
        a = self.add()
        before = self.ledger.day_path(a["date"]).read_bytes()
        self.ledger.execute("delete", self.mutation(a))
        backups = list((self.workspace / "data/meals/backups/2020-01-02").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)

    def test_malformed_existing_file_is_not_overwritten(self):
        self.ledger.execute("init")
        path = self.ledger.day_path("2020-01-02")
        path.write_text("broken json")
        with self.assertRaises(mod.LedgerError):
            self.add()
        self.assertEqual(path.read_text(), "broken json")

    def test_legacy_format_retained_and_ids_are_stable(self):
        self.ledger.execute("init")
        path = self.ledger.day_path("2020-01-02")
        path.write_text(json.dumps([{"name": "lunch", "custom": "keep",
                                    "items": [{"name": "old rice", "cal": 200, "p": 4, "c": 40, "f": 2}]}]))
        a = self.ledger.execute("list", day="2020-01-02")["meals"][0]
        b = self.ledger.execute("list", day="2020-01-02")["meals"][0]
        self.assertEqual(a["id"], b["id"])
        result = self.ledger.execute("append", {"request_id": "append-legacy", "date": "2020-01-02",
                                               "meal_id": a["id"], "foods": [food("new", 50)]})
        self.assertEqual(result["meal"]["calories"], 250)
        self.assertEqual(result["meal"]["custom"], "keep")
        self.assertIsInstance(json.loads(path.read_text()), list)

    def test_reject_legacy_planned_entry_and_nonlist(self):
        self.ledger.execute("init")
        path = self.ledger.day_path("2020-01-02")
        for raw in [{"lunch": {}}, [{"eaten": False}]]:
            path.write_text(json.dumps(raw))
            with self.assertRaises(mod.LedgerError):
                self.ledger.execute("list", day="2020-01-02")

    def test_recover_crash_before_day_write(self):
        real = mod.atomic_write
        def fail(path, raw):
            if path.name == "2020-01-02.json":
                raise OSError("simulated power loss")
            real(path, raw)
        with mock.patch.object(mod, "atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                self.add()
        result = self.add()
        self.assertTrue(result["duplicate"])
        self.assertEqual(len(self.ledger.load("2020-01-02")), 1)

    def test_recover_crash_after_day_write(self):
        real = mod.atomic_write
        counter = 0
        def fail(path, raw):
            nonlocal counter
            if path == self.ledger.state_path:
                counter += 1
                if counter == 2:
                    raise OSError("simulated crash before receipt")
            real(path, raw)
        with mock.patch.object(mod, "atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                self.add()
        self.assertTrue(self.add()["duplicate"])
        self.assertEqual(len(self.ledger.load("2020-01-02")), 1)

    def test_external_edit_during_pending_transaction_is_not_overwritten(self):
        real = mod.atomic_write
        def fail(path, raw):
            if path.name == "2020-01-02.json":
                raise OSError("power loss")
            real(path, raw)
        with mock.patch.object(mod, "atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                self.add()
        path = self.ledger.day_path("2020-01-02")
        path.write_text('[{"external": true}]')
        with self.assertRaises(mod.LedgerError):
            self.add()
        self.assertEqual(path.read_text(), '[{"external": true}]')

    def test_symlinks_blocked(self):
        outside = self.workspace / "outside"
        outside.mkdir()
        (self.workspace / "data").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(mod.LedgerError):
            self.add()
        self.assertEqual(list(outside.iterdir()), [])

    def test_daily_symlink_blocked(self):
        self.ledger.execute("init")
        victim = self.workspace / "USER.md"
        victim.write_text("private profile")
        (self.workspace / "data/meals/2020-01-02.json").symlink_to(victim)
        with self.assertRaises(mod.LedgerError):
            self.add()
        self.assertEqual(victim.read_text(), "private profile")

    def test_private_file_permissions(self):
        self.add()
        for path in [self.ledger.day_path("2020-01-02"), self.ledger.state_path]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_unique_writes(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = [pool.submit(self.cli, "add", request(f"parallel-{i}")) for i in range(12)]
            for job in jobs:
                result = job.result()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = self.ledger.execute("list", day="2020-01-02")
        self.assertEqual(len(records["meals"]), 12)
        self.assertEqual(records["logged_totals"]["calories"], 2400)

    def test_concurrent_duplicate_writes(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = [pool.submit(self.cli, "add", request()) for _ in range(8)]
            for job in jobs:
                result = job.result()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.ledger.load("2020-01-02")), 1)

    def test_cli_success_json_only(self):
        process = self.cli("add", request())
        self.assertEqual(process.returncode, 0)
        self.assertTrue(json.loads(process.stdout)["saved"])
        self.assertEqual(process.stderr, "")

    def test_cli_failure_is_nonzero_not_saved(self):
        process = self.cli("add", {"foods": []})
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(json.loads(process.stdout)["ok"])
        self.assertNotIn("saved", json.loads(process.stdout))

    def test_cli_rejects_misplaced_mutation_date(self):
        process = self.cli("add", request(), "--date", "2020-01-03")
        self.assertNotEqual(process.returncode, 0)

    def test_read_only_io_error_does_not_claim_success(self):
        with mock.patch.object(mod, "atomic_write", side_effect=PermissionError("readonly")):
            with self.assertRaises(PermissionError):
                self.add()
        self.assertEqual(self.ledger.execute("list", day="2020-01-02")["meals"], [])

    def test_unknown_fields_not_silently_accepted(self):
        with self.assertRaises(mod.LedgerError):
            self.add(unrecognized=True)
        broken = food()
        broken["carb"] = broken.pop("carbs")
        with self.assertRaises(mod.LedgerError):
            self.add(foods=[broken])


if __name__ == "__main__":
    unittest.main()
