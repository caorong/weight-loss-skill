"""Synthetic offline seven-choice onboarding; no gateway or real user jobs."""
import concurrent.futures
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests import test_coach
from coach_reminder_onboarding import OPTIONS

coach = test_coach.coach
ROOT = Path(__file__).resolve().parents[1]


class ReminderOnboardingTests(unittest.TestCase):
    setUp = test_coach.CoachTests.setUp
    tearDown = test_coach.CoachTests.tearDown
    write = test_coach.CoachTests.write
    bind = test_coach.CoachTests.bind

    def state(self):
        return self.c.execute("reminder-onboarding")

    def choose(self, decisions=None, offered=None, **extra):
        p = {"expected_revision": self.state()["revision"], **extra}
        if decisions is not None:
            p.update(decisions=decisions, confirmed=True)
        if offered is not None:
            p["offered"] = offered
        return self.write("reminder-onboarding-set", **p)

    def configure(self, option, key=None, **extra):
        s = {**OPTIONS[option], "enabled": True, "time": "09:00",
             "weekdays": [2] if option == "weekly-review" else list(range(7)),
             "route": {"agent": "coach", "channel": "test", "to": "synthetic"}, **extra}
        return self.write("reminder-set", key=key or option, settings=s, confirmed=True,
                          expected_revision=self.state()["revision"])

    def test_fresh_context_offers_all_seven_without_seeding_registry(self):
        view = self.c.execute("context")["reminder_onboarding"]
        self.assertEqual(view["unasked"], list(OPTIONS))
        self.assertTrue(all(s["state"] == "unasked" and not s["enabled"] for s in view["options"].values()))
        self.assertFalse((self.ws / "data/coach-reminders.json").exists())
        self.assertEqual(self.c.execute("reminder-specs")["specs"], [])

    def test_offered_and_silence_survive_restart_without_reoffering(self):
        self.choose(offered=list(OPTIONS))
        self.c = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.time)
        view = self.c.execute("context")["reminder_onboarding"]
        self.assertEqual(view["awaiting_answer"], list(OPTIONS))
        self.assertEqual(view["unasked"], [])
        self.assertTrue(all(not s["enabled"] for s in view["options"].values()))
        self.assertEqual(self.c.execute("reminder-specs")["specs"], [])

    def test_subset_answers_preserve_unanswered_and_unasked(self):
        self.choose(offered=["daily-review", "weekly-review", "breakfast"])
        self.choose({"daily-review": "accepted", "weekly-review": "declined"})
        v = self.state()
        self.assertEqual(v["needs_configuration"], ["daily-review"])
        self.assertEqual(v["awaiting_answer"], ["breakfast"])
        self.assertEqual(v["unasked"], ["lunch", "dinner", "weight", "exercise"])
        self.assertEqual(v["options"]["weekly-review"]["state"], "declined")

    def test_accept_all_records_one_revision_and_no_schedule(self):
        result = self.choose({k: "accepted" for k in OPTIONS})
        self.assertEqual(result["revision"], 1)
        self.assertFalse(result["schedules_changed"])
        self.assertEqual(result["native_status"], "not_applied")
        self.assertEqual(self.state()["needs_configuration"], list(OPTIONS))
        self.assertEqual(self.c.execute("reminder-specs")["specs"], [])
        self.assertEqual(self.c.execute("reminder-sync-plan", {"complete": True, "jobs": []})["operations"], [])
        for key in OPTIONS:
            self.assertFalse(self.c.execute("reminder-gate", key=key)["send"])

    def test_decline_and_skip_all_stay_off_without_repeated_offers(self):
        for decision in ("declined", "skipped"):
            self.choose({k: decision for k in OPTIONS})
            for _ in range(2):
                v = self.c.execute("context")["reminder_onboarding"]
                self.assertEqual(v["unasked"] + v["awaiting_answer"] + v["needs_configuration"], [])
                self.assertTrue(all(s["state"] == decision and not s["enabled"] for s in v["options"].values()))
            with self.assertRaisesRegex(ValueError, "re-offer"):
                self.choose(offered=["breakfast"])
            self.assertEqual(self.c.execute("reminder-specs")["specs"], [])

    def test_explicit_revisit_changes_only_requested_choice(self):
        self.choose({k: "declined" for k in OPTIONS})
        self.choose({"exercise": "accepted"})
        v = self.state()
        self.assertEqual(v["needs_configuration"], ["exercise"])
        self.assertTrue(all(v["options"][k]["state"] == "declined" for k in OPTIONS if k != "exercise"))

    def test_batch_validation_fails_atomically(self):
        cases = [
            {"decisions": {"breakfast": "accepted", "lunch": "yes"}, "confirmed": True},
            {"decisions": {"breakfast": "accepted", "all": "accepted"}, "confirmed": True},
            {"decisions": {"breakfast": "accepted"}},
            {"decisions": {"breakfast": []}, "confirmed": True},
            {"offered": ["breakfast", "breakfast"]},
            {"offered": ["breakfast"], "decisions": {"breakfast": "accepted"}, "confirmed": True},
            {"offered": ["lunch", None]}, {},
        ]
        for i, p in enumerate(cases):
            with self.subTest(p=p), self.assertRaises(ValueError):
                self.c.execute("reminder-onboarding-set", dict(p, request_id=f"bad:{i}", expected_revision=0))
            self.assertEqual(self.state()["revision"], 0)
            self.assertFalse((self.ws / "data/coach-reminders.json").exists())

    def test_stale_revision_is_rejected_and_retry_is_idempotent_after_restart(self):
        p = {"request_id": "answer:fixed", "expected_revision": 0, "confirmed": True,
             "decisions": {"daily-review": "accepted"}}
        self.c.execute("reminder-onboarding-set", p)
        self.c = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.time)
        self.assertTrue(self.c.execute("reminder-onboarding-set", p)["duplicate"])
        with self.assertRaises(ValueError):
            self.c.execute("reminder-onboarding-set", dict(p, request_id="answer:stale"))
        with self.assertRaises(ValueError):
            self.c.execute("reminder-onboarding-set", dict(p, decisions={"daily-review": "declined"}))
        self.assertEqual(self.state()["revision"], 1)

    def test_concurrent_choice_writes_do_not_lose_answers(self):
        def answer(option):
            c = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.time)
            try:
                c.execute("reminder-onboarding-set", {"request_id": option, "expected_revision": 0,
                          "confirmed": True, "decisions": {option: "accepted"}})
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(answer, ["breakfast", "lunch"])), 1)
        self.assertEqual(len(self.state()["needs_configuration"]), 1)

    def test_existing_arbitrary_keys_including_disabled_are_preserved(self):
        self.configure("breakfast", key="early-meal", enabled=False)
        self.configure("weekly-review", key="monday-check")
        path = self.ws / "data/coach-reminders.json"
        before = path.read_bytes()
        v = self.c.execute("context")["reminder_onboarding"]
        self.assertEqual(v["options"]["breakfast"]["configured_keys"], ["early-meal"])
        self.assertEqual(v["options"]["breakfast"]["state"], "configured")
        self.assertFalse(v["options"]["breakfast"]["enabled"])
        self.assertNotIn("weekly-review", v["unasked"])
        self.assertEqual(path.read_bytes(), before)
        old_items = self.c.execute("reminders")["items"]
        self.choose({"exercise": "skipped"})
        self.assertEqual(self.c.execute("reminders")["items"], old_items)
        with self.assertRaisesRegex(ValueError, "Existing reminders"):
            self.choose({"breakfast": "accepted"})

    def test_legacy_reviews_need_explicit_migration_without_auto_offer(self):
        self.configure("daily-review", key="old-review")
        path = self.ws / "data/coach-reminders.json"
        data = json.loads(path.read_text())
        data["items"]["old-review"].pop("review_type")
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        v = self.state()
        self.assertEqual(v["legacy_review_keys"], ["old-review"])
        for key in ("daily-review", "weekly-review"):
            self.assertEqual(v["options"][key]["state"], "migration_required")
            self.assertNotIn(key, v["unasked"])
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.choose({"daily-review": "accepted"})

    def test_occupied_canonical_key_is_not_suggested_for_another_kind(self):
        self.configure("exercise", key="breakfast")
        self.assertIsNone(self.state()["options"]["breakfast"]["suggested_key"])
        self.assertEqual(self.state()["options"]["breakfast"]["state"], "unasked")

    def test_accepted_choice_requires_complete_confirmed_settings(self):
        self.choose({"exercise": "accepted", "weekly-review": "accepted"})
        for settings in [
            {"enabled": True, "kind": "exercise"},
            {"enabled": True, "kind": "exercise", "time": "09:00", "weekdays": []},
            {"enabled": True, "kind": "review", "review_type": "weekly", "time": "09:00", "weekdays": [1, 2]},
        ]:
            with self.assertRaises(ValueError):
                self.write("reminder-set", key="exercise", settings=settings, confirmed=True,
                           expected_revision=self.state()["revision"])
        self.assertEqual(self.state()["needs_configuration"], ["weekly-review", "exercise"])
        self.assertEqual(self.c.execute("reminder-specs")["specs"], [])

    def test_partial_native_sync_resumes_without_recreating_matching_jobs(self):
        self.choose({"breakfast": "accepted", "exercise": "accepted"})
        self.assertEqual(self.configure("breakfast")["native_status"], "sync_required")
        self.configure("exercise", weekdays=[2, 4])
        specs = {s["key"]: s for s in self.c.execute("reminder-specs")["specs"]}
        first = self.bind(specs["breakfast"])
        self.assertEqual(self.state()["options"]["exercise"]["state"], "configured")
        self.assertEqual(self.state()["native_status"], "verify_with_gateway")
        self.assertEqual(self.c.execute("reminder-gate", key="exercise")["reason"], "gateway_sync_required")
        ops = self.c.execute("reminder-sync-plan", {"complete": True, "jobs": [first]})["operations"]
        self.assertEqual([(op["action"], op["key"]) for op in ops], [("add", "exercise")])
        second = {"id": "synthetic-exercise-job", **specs["exercise"]["job"]}
        self.assertEqual(self.c.execute("reminder-sync-plan", {"complete": True, "jobs": [first, second]})["operations"], [])
        self.write("reminder-bind", key="exercise", spec_hash=specs["exercise"]["spec_hash"], observed_job=second)
        self.assertTrue(self.c.execute("reminder-gate", key="exercise")["send"])

    def test_corrupt_onboarding_fails_closed_without_overwrite(self):
        self.choose({"breakfast": "declined"})
        path = self.ws / "data/coach-reminders.json"
        data = json.loads(path.read_text())
        data["onboarding"]["choices"]["breakfast"]["state"] = "default-on"
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.c.execute("context")
        with self.assertRaises(ValueError):
            self.choose({"exercise": "accepted"})
        self.assertEqual(path.read_bytes(), before)

    def test_reinstall_preserves_decisions_settings_and_bindings(self):
        self.choose({"dinner": "declined", "exercise": "skipped"})
        self.configure("weight", key="morning-scale", enabled=False)
        self.bind(self.c.execute("reminder-specs")["specs"][0])
        path = self.ws / "data/coach-reminders.json"
        before = path.read_bytes()
        cmd = [sys.executable, str(ROOT / "scripts/install-standalone.py"), "--runner", "python",
               "--workspace", str(self.ws), "--timezone", "Asia/Tokyo"]
        for flags in ([], ["--replace"]):
            result = subprocess.run(cmd + flags, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.state()["options"]["dinner"]["state"], "declined")
        self.assertEqual(self.state()["options"]["exercise"]["state"], "skipped")
        self.assertFalse(self.state()["options"]["weight"]["enabled"])


class ReminderOnboardingContractTests(unittest.TestCase):
    def test_model_eval_fixtures_are_unique_and_explicitly_not_run(self):
        data = json.loads((ROOT / "diet-tracking-analysis/evals/reminder-onboarding.json").read_text())
        self.assertEqual(data["evaluation"]["result_status"], "not_run")
        self.assertIn("not automated LLM results", data["scope"])
        cases = data["cases"]
        self.assertGreaterEqual(len(cases), 12)
        self.assertEqual(len(cases), len({c["id"] for c in cases}))
        for case in cases:
            self.assertTrue(case["context"] and case["user"] and case["expected"])

    def test_install_docs_use_current_main_or_explicit_pr_head(self):
        for filename in ("README.md", "AI-INSTALL.md", "AGENTS.md"):
            text = (ROOT / filename).read_text()
            self.assertNotIn("feat/local-coach-v5", text)
            self.assertIn("main", text)
            self.assertIn("head", text)

    def test_runtime_and_install_docs_cover_all_seven_consent_boundaries(self):
        base = ROOT / "diet-tracking-analysis"
        entry = " ".join((base / "SKILL.md").read_text().split())
        native = " ".join((base / "references/native-reminders.md").read_text().split())
        for word in ("daily", "weekly", "breakfast", "lunch", "dinner", "weight", "exercise"):
            self.assertIn(word, entry)
        for phrase in ("only unasked", "Silence is not consent", "not another opt-in question",
                       "IANA timezone", "chosen exercise days", "does not prove registration"):
            self.assertIn(phrase, entry)
        for phrase in ("declined or skipped", "no enabled setting or job", "configured_keys",
                       "one weekday", "same request_id and payload", "read-back"):
            self.assertIn(phrase, native)


if __name__ == "__main__":
    unittest.main()
