"""Offline coach regression suite. All profiles, schedules and food are synthetic."""
import concurrent.futures
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "diet-tracking-analysis/scripts"
sys.path.insert(0, str(SCRIPTS))
import coach
import coach_store


class CoachTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="coach test ")
        self.ws = Path(self.temp.name)
        self.time = datetime(2026, 10, 13, 9, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.c = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.time)
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def write(self, action, **payload):
        self.counter += 1
        return self.c.execute(action, {"request_id": f"test:{self.counter}", **payload})

    def profile(self, **fields):
        old = self.c.execute("context")["profile"]
        return self.write("profile-set", fields=fields, expected_revision=old.get("revision", 0))

    def goal(self, body="Synthetic goal", effective_date="today", calories=1800):
        return self.write("plan-save", kind="goal", body=body, effective_date=effective_date,
                          targets={"calories": calories})["plan"]

    def activate(self, p):
        return self.write("plan-activate", plan_id=p["id"], confirmed=True)

    def weight(self, value=80, day="today", **extra):
        return self.write("weight-add", value=value, unit="kg", date=day, **extra)["weight"]

    def reminder(self, **patch):
        old = self.c.execute("reminders")
        settings = {"enabled": True, "kind": "breakfast", "time": "09:00", "weekdays": list(range(7)),
                    "route": {"agent": "coach", "channel": "feishu", "to": "synthetic-user"}, **patch}
        self.write("reminder-set", key="breakfast", expected_revision=old["revision"], settings=settings, confirmed=True)
        return self.c.execute("reminder-specs")["specs"][0]

    def bind(self, spec):
        job = {"id": "synthetic-job", **spec["job"]}
        self.write("reminder-bind", key=spec["key"], spec_hash=spec["spec_hash"], observed_job=job)
        return job

    def test_context_no_profile_required(self):
        data = self.c.execute("context")
        self.assertEqual(data["profile"], {})
        self.assertIsNone(data["latest_weight"])
        self.assertIsNone(data["active_plans"]["goal"])

    def test_unique_requests_even_same_phone_session(self):
        a, b = self.c.execute("context"), self.c.execute("context")
        self.assertNotEqual(a["request_file"], b["request_file"])
        self.assertFalse(Path(a["request_file"]).exists())
        self.assertEqual(Path(a["request_file"]).parent.stat().st_mode & 0o777, 0o700)

    def test_context_refreshes_after_midnight(self):
        a = self.c.execute("context")
        self.time = self.time.replace(day=14, hour=0, minute=1)
        b = self.c.execute("context")
        self.assertEqual((a["today"], b["today"]), ("2026-10-13", "2026-10-14"))
        self.assertEqual(b["yesterday"], "2026-10-13")

    def test_timezone_and_dst_use_clock_not_fixed_offset(self):
        utc = datetime(2026, 11, 1, 6, 30, tzinfo=ZoneInfo("UTC"))
        ny = coach.Coach(self.ws, "America/New_York", clock=lambda: utc)
        self.assertTrue(ny.execute("context")["now"].endswith("-05:00"))
        tokyo = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: utc)
        self.assertTrue(tokyo.execute("context")["now"].endswith("+09:00"))

    def test_profile_preserves_user_owned_sections(self):
        (self.ws / "health-profile.md").write_text("# Existing notes\n\nPrivate text.\n")
        (self.ws / "USER.md").write_text("Never overwrite")
        self.profile(height_cm=170, age_years=30)
        self.assertTrue((self.ws / "health-profile.md").read_text().startswith("# Existing notes\n\nPrivate text."))
        self.assertEqual((self.ws / "USER.md").read_text(), "Never overwrite")

    def test_profile_persists_cross_process(self):
        self.profile(height_cm=170)
        c = coach.Coach(self.ws, "Asia/Tokyo")
        self.assertEqual(c.execute("context")["profile"]["height_cm"], 170)

    def test_profile_revision_conflict(self):
        self.profile(height_cm=170)
        with self.assertRaises(coach.CoachError):
            self.write("profile-set", expected_revision=0, fields={"height_cm": 160})

    def test_profile_retries_are_idempotent(self):
        p = {"request_id": "fixed", "fields": {"height_cm": 170}, "expected_revision": 0}
        self.c.execute("profile-set", p)
        self.assertTrue(self.c.execute("profile-set", p)["duplicate"])
        self.assertEqual(self.c.execute("context")["profile"]["revision"], 1)

    def test_profile_missing_is_not_guessed(self):
        self.profile(height_cm=170)
        data = self.c.execute("context")["profile"]
        self.assertNotIn("age_years", data)
        self.assertNotIn("biological_sex", data)

    def test_profile_field_deletion(self):
        self.profile(height_cm=170)
        self.profile(height_cm=None)
        self.assertNotIn("height_cm", self.c.execute("context")["profile"])

    def test_preferences_typed_lists_and_revision(self):
        self.write("preferences-set", fields={"allergies": ["synthetic nut"], "cooking": "microwave"}, expected_revision=0)
        self.assertEqual(self.c.execute("context")["preferences"]["allergies"], ["synthetic nut"])

    def test_unknown_profile_fields_rejected(self):
        with self.assertRaises(coach.CoachError):
            self.profile(api_key="should not be stored")

    def test_corrupt_markers_fail_without_overwrite(self):
        p = self.ws / "health-profile.md"
        p.write_text("<!-- standalone-coach:profile:begin -->\nmalformed")
        old = p.read_bytes()
        with self.assertRaises(coach.CoachError):
            self.write("profile-set", fields={"height_cm": 170}, expected_revision=0)
        self.assertEqual(old, p.read_bytes())

    def test_weight_units_kg_lb_jin(self):
        a = self.write("weight-add", value=160, unit="斤")["weight"]
        b = self.write("weight-add", value=176.3698097, unit="lb")["weight"]
        self.assertEqual(a["value"], 80)
        self.assertAlmostEqual(b["value"], 80, places=3)

    def test_unknown_weight_time_is_explicit_not_fabricated(self):
        a = self.weight()
        self.assertIsNone(a["observed_at"])
        self.assertEqual(a["time_precision"], "day")

    def test_backfill_uses_observation_date(self):
        self.weight(day="yesterday")
        data = self.c.execute("measurements", period="yesterday")
        self.assertEqual(data["weights"][0]["date"], "2026-10-12")
        self.assertFalse(self.c.execute("measurements", period="today")["weights"])

    def test_weight_future_rejected(self):
        with self.assertRaises(coach.CoachError):
            self.weight(day="2026-10-14")

    def test_weight_new_message_identical_value_is_new(self):
        self.weight()
        self.weight()
        self.assertEqual(len(self.c.execute("measurements")["weights"]), 2)

    def test_weight_correction_targets_only_one_id(self):
        a, b = self.weight(80), self.weight(82)
        self.write("weight-update", id=a["id"], value=81, unit="kg", expected_revision=1)
        values = {r["id"]: r["value"] for r in self.c.execute("measurements")["weights"]}
        self.assertEqual(values, {a["id"]: 81, b["id"]: 82})

    def test_weight_delete_retry_does_not_resurrect(self):
        a = self.weight()
        p = {"request_id": "delete", "id": a["id"], "expected_revision": 1}
        self.c.execute("weight-delete", p)
        self.assertTrue(self.c.execute("weight-delete", p)["duplicate"])
        self.assertFalse(self.c.execute("measurements")["weights"])

    def test_legacy_weight_read_and_update(self):
        self.c.execute("context")
        (self.ws / "data/weight.json").write_text(json.dumps({"2026-10-12T08:00:00+09:00": {"value": 80, "unit": "kg", "custom": "keep"}}))
        a = self.c.execute("measurements")["weights"][0]
        self.write("weight-update", id=a["id"], value=81, unit="kg", expected_revision=0)
        self.assertEqual(self.c.execute("measurements")["weights"][0]["custom"], "keep")

    def test_retry_after_midnight_keeps_first_date(self):
        p = {"request_id": "day-retry", "value": 80, "unit": "kg", "date": "today"}
        first = self.c.execute("weight-add", p)
        self.time += timedelta(days=1)
        second = self.c.execute("weight-add", p)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["weight"]["date"], first["weight"]["date"])

    def test_exercise_unknown_calories_not_zero_claim(self):
        self.write("exercise-add", items=[{"activity": "walk", "duration_min": 30}])
        data = self.c.execute("review")["summary"]
        self.assertEqual(data["exercise_minutes"], 30)
        self.assertEqual(data["exercise_calorie_unknown_sessions"], 1)

    def test_exercise_batch_and_net_gross_separation(self):
        self.write("exercise-add", items=[{"activity": "walk", "duration_min": 30, "calories_kcal": 120,
                   "net_calories_kcal": 90, "source": "device"}, {"activity": "stretch", "duration_min": 10}])
        report = self.c.execute("review")
        self.assertEqual(report["summary"]["known_exercise_net_kcal"], 90)
        self.assertEqual(report["summary"]["exercise_minutes"], 40)
        self.assertFalse(report["summary"]["automatic_target_change"])

    def test_exercise_update_delete(self):
        row = self.write("exercise-add", items=[{"activity": "walk", "duration_min": 30}])["exercises"][0]
        self.write("exercise-update", id=row["id"], expected_revision=1, item={"activity": "walk", "duration_min": 20})
        self.assertEqual(self.c.execute("review")["summary"]["exercise_minutes"], 20)
        self.write("exercise-delete", id=row["id"], expected_revision=2)
        self.assertFalse(self.c.execute("measurements")["exercises"])

    def test_plan_draft_has_no_effect(self):
        self.goal()
        self.assertIsNone(self.c.execute("context")["active_plans"]["goal"])
        self.assertFalse((self.ws / "PLAN.md").exists())

    def test_plan_confirmation_required(self):
        p = self.goal()
        with self.assertRaises(coach.CoachError):
            self.write("plan-activate", plan_id=p["id"], confirmed=False)

    def test_plan_preserves_nonmanaged_text(self):
        (self.ws / "PLAN.md").write_text("# User original plan\nKeep this.\n")
        self.activate(self.goal())
        self.assertTrue((self.ws / "PLAN.md").read_text().startswith("# User original plan\nKeep this."))

    def test_future_plan_does_not_replace_today(self):
        p = self.goal()
        self.activate(p)
        q = self.goal(effective_date="2026-10-14", calories=1900)
        self.activate(q)
        self.assertEqual(self.c.execute("context")["active_plans"]["goal"]["id"], p["id"])
        self.time += timedelta(days=1)
        self.assertEqual(self.c.execute("context")["active_plans"]["goal"]["id"], q["id"])

    def test_stale_proposal_rejected(self):
        a, b = self.goal("a"), self.goal("b")
        self.activate(a)
        with self.assertRaises(coach.CoachError):
            self.activate(b)

    def test_old_draft_does_not_rewrite_yesterday(self):
        p = self.goal()
        self.time += timedelta(days=1)
        with self.assertRaises(coach.CoachError):
            self.activate(p)

    def test_meal_and_exercise_plans_local_only(self):
        for kind, filename in (("meal", "MEAL-PLAN.md"), ("exercise", "EXERCISE-PLAN.md")):
            p = self.write("plan-save", kind=kind, body="Synthetic seven-day plan", targets={})["plan"]
            self.activate(p)
            self.assertIn("Synthetic seven-day plan", (self.ws / filename).read_text())
        self.assertFalse((self.ws / "data/coach-reminders.json").exists())

    def test_meal_plan_cannot_duplicate_goal_target(self):
        with self.assertRaises(coach.CoachError):
            self.write("plan-save", kind="meal", body="test", targets={"calories": 1800})

    def test_report_uses_historical_goals(self):
        self.time -= timedelta(days=1)
        self.activate(self.goal(calories=1800))
        self.time += timedelta(days=1)
        self.activate(self.goal(calories=1900))
        report = self.c.execute("review", start="2026-10-12", end="2026-10-13")
        self.assertEqual([d["target_calories"] for d in report["meal_days"]], [1800, 1900])

    def test_empty_review_is_unknown_not_zero_day_average(self):
        s = self.c.execute("review")["summary"]
        self.assertIsNone(s["average_per_logged_day"])
        self.assertEqual(s["review_readiness"], "insufficient_data")
        self.assertIsNone(s["weight_change_kg"])

    def test_review_uses_fasting_daily_median(self):
        self.weight(80, fasting=True)
        self.weight(90, fasting=False)
        s = self.c.execute("review")["summary"]
        self.assertEqual(s["weight_days"], 1)
        self.assertEqual(s["daily_weights"][0]["kg"], 80)

    def test_review_snapshot_conflict(self):
        r = self.c.execute("review")
        self.weight()
        with self.assertRaises(coach.CoachError):
            self.write("review-save", start=r["start"], end=r["end"], expected_snapshot=r["snapshot"], analysis="old")

    def test_review_saved_locally_without_changing_plan(self):
        p = self.goal()
        self.activate(p)
        r = self.c.execute("review")
        saved = self.write("review-save", start=r["start"], end=r["end"], expected_snapshot=r["snapshot"], analysis="Data is incomplete.")
        self.assertTrue((self.ws / saved["path"]).exists())
        self.assertEqual(self.c.execute("context")["active_plans"]["goal"]["id"], p["id"])

    def test_proposal_references_real_review(self):
        r = self.c.execute("review")
        saved = self.write("review-save", start=r["start"], end=r["end"], expected_snapshot=r["snapshot"], analysis="test")
        proposal = self.write("plan-save", kind="goal", body="Proposed habit change", targets={}, basis_review_id=saved["review_id"])
        self.assertEqual(proposal["plan"]["status"], "draft")

    def test_bad_review_reference_rejected(self):
        with self.assertRaises(coach.CoachError):
            self.write("plan-save", kind="goal", body="bad", basis_review_id="invented")

    def test_reminders_disabled_by_default(self):
        self.assertFalse(self.c.execute("reminder-gate", key="breakfast")["send"])
        self.assertEqual(self.c.execute("reminder-specs")["specs"], [])

    def test_reminder_requires_explicit_route_and_confirmation(self):
        with self.assertRaises(coach.CoachError):
            self.write("reminder-set", key="breakfast", expected_revision=0, settings={"enabled": True}, confirmed=False)

    def test_native_spec_has_no_model_or_author_endpoint(self):
        entry = self.reminder()
        self.assertNotIn("model", entry["job"]["payload"])
        self.assertEqual(entry["job"]["schedule"]["tz"], "Asia/Tokyo")
        self.assertEqual(entry["job"]["sessionTarget"], "isolated")
        self.assertEqual(entry["job"]["delivery"]["channel"], "feishu")
        self.assertNotIn("nanorhino", json.dumps(entry))

    def test_sync_unregistered_is_only_a_plan(self):
        self.reminder()
        data = self.c.execute("reminder-sync-plan", {"complete": True, "jobs": []})
        self.assertEqual(data["operations"][0]["action"], "add")
        self.assertEqual(data["native_status"], "not_applied")
        self.assertFalse(self.c.execute("reminder-gate", key="breakfast")["send"])

    def test_sync_requires_complete_snapshot(self):
        self.reminder()
        with self.assertRaises(coach.CoachError):
            self.c.execute("reminder-sync-plan", {"complete": False, "jobs": []})

    def test_matching_gateway_job_not_duplicated(self):
        e = self.reminder()
        job = self.bind(e)
        data = self.c.execute("reminder-sync-plan", {"complete": True, "jobs": [job]})
        self.assertEqual(data["operations"], [])

    def test_disable_requires_native_update_but_gate_stops_immediately(self):
        entry = self.reminder()
        job = self.bind(entry)
        self.reminder(enabled=False)
        data = self.c.execute("reminder-sync-plan", {"complete": True, "jobs": [job]})
        self.assertEqual(data["operations"][0]["action"], "update")
        self.assertFalse(data["operations"][0]["patch"]["enabled"])
        self.assertFalse(self.c.execute("reminder-gate", key="breakfast")["send"])

    def test_unrelated_jobs_never_modified(self):
        e = self.reminder()
        job = self.bind(e)
        other = {"id": "other", "name": "unrelated", "enabled": True}
        data = self.c.execute("reminder-sync-plan", {"complete": True, "jobs": [job, other]})
        self.assertFalse(data["operations"])

    def test_foreign_name_collision_rejected(self):
        e = self.reminder()
        other = {"id": "foreign", "name": e["job"]["name"], "payload": {"message": "not our job"}}
        with self.assertRaises(coach.CoachError):
            self.c.execute("reminder-sync-plan", {"complete": True, "jobs": [other]})

    def test_wrong_bind_rejected(self):
        e = self.reminder()
        job = {"id": "wrong", **e["job"], "agentId": "other"}
        with self.assertRaises(coach.CoachError):
            self.write("reminder-bind", key=e["key"], spec_hash=e["spec_hash"], observed_job=job)

    def test_pause_and_expiry(self):
        e = self.reminder(pause_until="2026-10-13T10:00:00+09:00")
        self.bind(e)
        self.assertEqual(self.c.execute("reminder-gate", key="breakfast")["reason"], "paused")
        self.time += timedelta(days=1)
        self.assertTrue(self.c.execute("reminder-gate", key="breakfast")["send"])

    def test_route_change_suppresses_stale_native_job_until_sync(self):
        self.bind(self.reminder())
        self.reminder(route={"agent": "coach", "channel": "feishu", "to": "new-user"})
        self.assertEqual(self.c.execute("reminder-gate", key="breakfast")["reason"], "gateway_sync_required")

    def test_late_retry_suppressed(self):
        self.bind(self.reminder())
        self.time = self.time.replace(hour=23)
        self.assertEqual(self.c.execute("reminder-gate", key="breakfast")["reason"], "outside_delivery_window")

    def test_gate_does_not_mark_delivery(self):
        self.bind(self.reminder())
        data = self.c.execute("reminder-gate", key="breakfast")
        self.assertTrue(data["send"])
        self.assertFalse(data["delivered"])

    def test_weight_gate_after_weight_record(self):
        self.bind(self.reminder(kind="weight"))
        self.weight()
        self.assertEqual(self.c.execute("reminder-gate", key="breakfast")["reason"], "weight_already_recorded")

    def test_journal_recovers_profile_before_receipt_commit(self):
        original = coach_store.atomic
        hit = [False]
        def fail(path, raw):
            if Path(path).name == "health-profile.md" and not hit[0]:
                hit[0] = True
                raise OSError("synthetic crash")
            return original(path, raw)
        p = {"request_id": "crash", "expected_revision": 0, "fields": {"height_cm": 170}}
        with mock.patch.object(coach_store, "atomic", side_effect=fail), self.assertRaises(OSError):
            self.c.execute("profile-set", p)
        r = self.c.execute("profile-set", p)
        self.assertTrue(r["duplicate"])
        self.assertEqual(self.c.execute("context")["profile"]["height_cm"], 170)

    def test_multi_file_plan_transaction_recovers(self):
        p = self.goal()
        original = coach_store.atomic
        def fail(path, raw):
            if Path(path).name == "PLAN.md":
                raise OSError("synthetic crash")
            return original(path, raw)
        request = {"request_id": "plan-crash", "plan_id": p["id"], "confirmed": True}
        with mock.patch.object(coach_store, "atomic", side_effect=fail), self.assertRaises(OSError):
            self.c.execute("plan-activate", request)
        self.assertTrue(self.c.execute("plan-activate", request)["duplicate"])
        self.assertIn(p["id"], (self.ws / "PLAN.md").read_text())

    def test_pending_external_edit_conflict_not_overwritten(self):
        original = coach_store.atomic
        def fail(path, raw):
            if Path(path).name == "health-profile.md":
                raise OSError("synthetic crash")
            return original(path, raw)
        with mock.patch.object(coach_store, "atomic", side_effect=fail), self.assertRaises(OSError):
            self.profile(height_cm=170)
        (self.ws / "health-profile.md").write_text("external change")
        with self.assertRaises(coach.CoachError):
            self.c.execute("context")
        self.assertEqual((self.ws / "health-profile.md").read_text(), "external change")

    def test_modified_document_backup_and_private_permissions(self):
        self.profile(height_cm=170)
        self.profile(height_cm=171)
        self.assertTrue(list((self.ws / "data/coach-backups").glob("*/health-profile.md")))
        self.assertEqual((self.ws / "health-profile.md").stat().st_mode & 0o777, 0o600)

    def test_symlinked_profile_refused(self):
        other = self.ws / "other.txt"
        other.write_text("unchanged")
        (self.ws / "health-profile.md").symlink_to(other)
        with self.assertRaises(coach.CoachError):
            self.profile(height_cm=170)
        self.assertEqual(other.read_text(), "unchanged")

    def test_invalid_numbers_never_saved(self):
        for value in (float("nan"), float("inf"), -1, True):
            with self.assertRaises(coach.CoachError):
                self.weight(value)

    def test_unknown_payload_not_silently_ignored(self):
        with self.assertRaises(coach.CoachError):
            self.write("weight-add", value=80, unit="kg", timezone="wrong")

    def test_concurrent_distinct_weight_requests_no_lost_updates(self):
        def run(i):
            c = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.time)
            return c.execute("weight-add", {"request_id": f"parallel:{i}", "value": 80 + i / 100, "unit": "kg"})
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(run, range(15)))
        self.assertTrue(all(r["saved"] for r in results))
        self.assertEqual(len(self.c.execute("measurements")["weights"]), 15)

    def test_cli_outputs_json_and_nonzero_error(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "coach.py"), "weight-add", "--workspace", str(self.ws), "--tz", "Asia/Tokyo"],
                           input='{"value": 80}', text=True, capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_cli_new_process_reads_saved_preferences(self):
        self.write("preferences-set", fields={"notes": ["synthetic preference"]}, expected_revision=0)
        r = subprocess.run([sys.executable, str(SCRIPTS / "coach.py"), "context", "--workspace", str(self.ws), "--tz", "Asia/Tokyo"],
                           text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["preferences"]["notes"], ["synthetic preference"])


if __name__ == "__main__":
    unittest.main()
