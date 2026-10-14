"""Stable exercise identity regressions; synthetic temporary workspaces only."""
import concurrent.futures
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "diet-tracking-analysis/scripts"))
import coach
import coach_store


class ExerciseIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="exercise identity synthetic ")
        self.ws = Path(self.tmp.name)
        self.now = datetime(2026, 10, 13, 9, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.c = self.client()
        self.path = self.ws / "data/exercise.json"
        self.path.parent.mkdir()
        self.seq = 0

    def tearDown(self):
        self.tmp.cleanup()

    def client(self):
        return coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.now)

    def seed(self, rows=None, **blocks):
        rows = rows if rows is not None else [
            {"activity": f"SYNTHETIC {name}", "duration_min": minutes}
            for name, minutes in (("A", 10), ("B", 20), ("C", 30))]
        data = {"2026-10-13": {"exercises": rows, "custom": "keep", "total_calories": 123}, **blocks}
        self.path.write_text(json.dumps(data))
        return copy.deepcopy(data)

    def saved(self):
        return json.loads(self.path.read_text())

    def rows(self, client=None):
        return (client or self.c).execute("measurements")["exercises"]

    def write(self, action, **payload):
        self.seq += 1
        return self.c.execute(action, {"request_id": f"exercise-id:{self.seq}", **payload})

    def update(self, row):
        return self.write("exercise-update", id=row["id"], expected_revision=row["revision"],
                          item={"activity": "SYNTHETIC B corrected", "duration_min": 25})["exercise"]

    def delete(self, row):
        return self.write("exercise-delete", id=row["id"], expected_revision=row["revision"])

    def normalize(self):
        # A first write persists identities, then rejects the temporary position ID.
        with self.assertRaisesRegex(coach.CoachError, "position-based"):
            self.write("exercise-delete", id="legacy_exercise_2026-10-13_0", expected_revision=0)
        return self.rows()

    def test_first_write_persists_uuids_without_changing_legacy_fields(self):
        original = self.seed()
        rows = self.normalize()
        for row in rows:
            self.assertRegex(row["id"], r"^exercise_[0-9a-f]{32}$")
            self.assertEqual(row["revision"], 0)
        saved = self.saved()
        self.assertEqual([r["id"] for r in saved["2026-10-13"]["exercises"]], [r["id"] for r in rows])
        for row in saved["2026-10-13"]["exercises"]:
            del row["id"]
        self.assertEqual(saved, original)
        backups = list((self.ws / "data/coach-backups").glob("*/data/exercise.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_repeated_reads_do_not_rewrite_ids_or_create_more_transactions(self):
        self.seed()
        first = self.normalize()
        before = self.path.read_bytes()
        state = (self.ws / "data/coach-state.json").read_bytes()
        self.assertEqual(self.rows(self.client()), first)
        self.assertEqual(self.c.execute("review")["exercises"], first)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual((self.ws / "data/coach-state.json").read_bytes(), state)

    def test_read_only_review_snapshot_changes_only_after_first_exercise_write(self):
        # Legacy JSON need not use the canonical sorted date order written by packed().
        self.seed(**{"2026-10-12": {"exercises": [{"activity": "SYNTHETIC yesterday", "duration_min": 15}]}})
        before = self.path.read_bytes()
        first = self.c.execute("review")
        second = self.client().execute("review")
        self.assertEqual(first["exercises"], second["exercises"])
        self.assertEqual(first["snapshot"], second["snapshot"])
        saved = self.write("review-save", start=first["start"], end=first["end"],
                           expected_snapshot=first["snapshot"], analysis="SYNTHETIC first review")
        self.assertTrue(saved["saved"])
        self.assertEqual(self.path.read_bytes(), before)
        self.normalize()
        fresh = self.c.execute("review")
        self.assertNotEqual(first["snapshot"], fresh["snapshot"])
        self.assertEqual(fresh["snapshot"], self.client().execute("review")["snapshot"])
        with self.assertRaisesRegex(coach.CoachError, "Review data changed"):
            self.write("review-save", start=first["start"], end=first["end"],
                       expected_snapshot=first["snapshot"], analysis="SYNTHETIC stale review")
        self.assertTrue(self.write("review-save", start=fresh["start"], end=fresh["end"],
                                  expected_snapshot=fresh["snapshot"], analysis="SYNTHETIC fresh review")["saved"])

    def test_legacy_measurement_reads_do_not_assign_or_persist_ids(self):
        self.seed()
        before = self.path.read_bytes()
        first = self.rows()
        self.assertEqual([r["id"] for r in first], [f"legacy_exercise_2026-10-13_{i}" for i in range(3)])
        self.assertEqual(self.rows(self.client()), first)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.ws / "data/coach-state.json").exists())
        self.assertFalse((self.ws / "data/coach-backups").exists())

    def test_empty_query_does_not_create_exercise_file(self):
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.path.exists())
        self.assertFalse((self.ws / "data/coach-state.json").exists())

    def test_preserves_existing_ids_and_new_record_contract(self):
        new = self.write("exercise-add", items=[{"activity": "SYNTHETIC new", "duration_min": 5}])["exercises"][0]
        legacy_id = "legacy_exercise_2026-10-13_4"
        original = self.seed([new, {"id": "custom-stable-id", "revision": 7, "activity": "SYNTHETIC custom", "duration_min": 15},
                              {"id": legacy_id, "revision": 1, "activity": "SYNTHETIC persisted", "duration_min": 20},
                              {"activity": "SYNTHETIC missing", "duration_min": 30}])
        self.normalize()
        self.assertEqual(self.saved()["2026-10-13"]["exercises"][:3], original["2026-10-13"]["exercises"][:3])
        self.assertEqual(new["revision"], 1)
        self.assertRegex(new["id"], r"^exercise_[0-9a-f]{32}$")

    def test_delete_first_then_update_second_never_targets_third(self):
        self.seed()
        a, b, c = self.normalize()
        self.delete(a)
        updated = self.update(b)
        after = self.rows()
        self.assertEqual([(r["id"], r["activity"]) for r in after],
                         [(b["id"], "SYNTHETIC B corrected"), (c["id"], c["activity"])])
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(after[1]["duration_min"], 30)

    def test_update_second_then_delete_first_keeps_unique_deletable_ids(self):
        self.seed()
        a, b, c = self.normalize()
        self.update(b)
        self.delete(a)
        fresh_b, fresh_c = self.rows()
        self.assertEqual((fresh_b["id"], fresh_c["id"]), (b["id"], c["id"]))
        self.delete(fresh_b)
        self.assertEqual([r["id"] for r in self.rows()], [c["id"]])

    def test_add_normalizes_all_dates_and_preserves_ids_across_later_merges(self):
        self.seed(**{"2026-10-12": {"exercises": [{"activity": "SYNTHETIC yesterday", "duration_min": 15}]}})
        new = self.write("exercise-add", items=[{"activity": "SYNTHETIC new", "duration_min": 5}])["exercises"][0]
        initial = {r["activity"]: r["id"] for r in self.rows()}
        self.assertEqual(len(set(initial.values())), 5)
        for _ in range(3):
            self.write("exercise-add", items=[{"activity": "SYNTHETIC extra", "duration_min": 3}])
            for row in self.rows():
                if row["activity"] in initial:
                    self.assertEqual(row["id"], initial[row["activity"]])
        self.assertEqual(self.saved()["2026-10-13"]["exercises"][3], new)

    def test_first_update_or_delete_preserves_normalized_sibling_ids(self):
        for action in ("exercise-update", "exercise-delete"):
            with self.subTest(action=action):
                self.seed([{"id": "existing-stable-id", "revision": 7, "activity": "SYNTHETIC existing", "duration_min": 10},
                           {"activity": "SYNTHETIC missing B", "duration_min": 20},
                           {"activity": "SYNTHETIC missing C", "duration_min": 30}])
                fields = {"item": {"activity": "SYNTHETIC corrected", "duration_min": 5}} if action == "exercise-update" else {}
                self.write(action, id="existing-stable-id", expected_revision=7, **fields)
                siblings = self.saved()["2026-10-13"]["exercises"][-2:]
                self.assertEqual(len({r["id"] for r in siblings}), 2)
                self.assertTrue(all(r["id"].startswith("exercise_") for r in siblings))
                self.assertEqual([r["duration_min"] for r in siblings], [20, 30])
                if action == "exercise-update":
                    updated = self.saved()["2026-10-13"]["exercises"][0]
                    self.assertEqual(updated["id"], "existing-stable-id")
                    self.assertEqual(updated["revision"], 8)

    def test_old_positional_requests_reject_but_keep_migration_after_error(self):
        for action in ("exercise-update", "exercise-delete"):
            with self.subTest(action=action):
                self.seed()
                payload = {"request_id": "stale:" + action, "id": "legacy_exercise_2026-10-13_1", "expected_revision": 0}
                if action == "exercise-update":
                    payload["item"] = {"activity": "WRONG", "duration_min": 99}
                with self.assertRaisesRegex(coach.CoachError, "[Pp]osition|[Qq]uery"):
                    self.c.execute(action, payload)
                saved = self.path.read_bytes()
                rows = self.rows()
                self.assertTrue(all(r["id"].startswith("exercise_") for r in rows))
                self.assertEqual([r["activity"] for r in rows], ["SYNTHETIC A", "SYNTHETIC B", "SYNTHETIC C"])
                with self.assertRaises(coach.CoachError):
                    self.c.execute(action, copy.deepcopy(payload))
                self.assertEqual(self.path.read_bytes(), saved)
                self.assertNotIn(coach_store.sha(payload["request_id"].encode()), self.c.store.state["requests"])
                self.delete(rows[0])
                with self.assertRaises(coach.CoachError):
                    self.c.execute(action, {**payload, "request_id": payload["request_id"] + ":after-delete"})
                self.assertEqual([r["id"] for r in self.rows()], [r["id"] for r in rows[1:]])

    def test_stored_position_shaped_id_is_preserved_but_not_ambiguously_mutated(self):
        row = {"id": "legacy_exercise_2026-10-13_1", "revision": 1, "activity": "SYNTHETIC persisted", "duration_min": 10}
        self.seed([row])
        fresh = self.rows()[0]
        before = self.path.read_bytes()
        for action in ("exercise-update", "exercise-delete"):
            with self.subTest(action=action), self.assertRaisesRegex(coach.CoachError, "[Pp]osition"):
                self.write(action, id=fresh["id"], expected_revision=1, **({"item": {"activity": "WRONG", "duration_min": 1}} if action == "exercise-update" else {}))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.saved()["2026-10-13"]["exercises"], [row])

    def test_failed_add_still_persists_normalization_without_adding_records(self):
        self.seed()
        with self.assertRaises(coach.CoachError):
            self.write("exercise-add", items=[])
        saved = self.saved()["2026-10-13"]["exercises"]
        self.assertEqual(len(saved), 3)
        self.assertTrue(all("id" in r for r in saved))

    def test_duplicate_ids_fail_before_any_write_including_other_dates(self):
        for action in ("measurements", "exercise-add", "exercise-update", "exercise-delete"):
            with self.subTest(action=action):
                duplicate = {"id": "exercise_duplicate", "activity": "SYNTHETIC duplicate", "duration_min": 5}
                self.seed([{"activity": "SYNTHETIC missing", "duration_min": 10}, duplicate],
                          **{"2026-10-12": {"exercises": [duplicate]}})
                before = self.path.read_bytes()
                payload = {"request_id": "duplicate:" + action}
                if action == "exercise-add":
                    payload["items"] = [{"activity": "SYNTHETIC new", "duration_min": 2}]
                elif action != "measurements":
                    payload.update(id=duplicate["id"], expected_revision=0)
                    if action == "exercise-update":
                        payload["item"] = {"activity": "WRONG", "duration_min": 1}
                with self.assertRaisesRegex(coach.CoachError, "Duplicate exercise ID"):
                    self.c.execute(action, payload if action != "measurements" else None)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse((self.ws / "data/coach-state.json").exists())
                self.assertFalse((self.ws / "data/coach-backups").exists())

    def test_invalid_existing_ids_fail_without_replacing_them(self):
        for value in (None, "", "  ", 12, [], {}):
            with self.subTest(value=value):
                self.seed([{"activity": "SYNTHETIC missing", "duration_min": 5},
                           {"id": value, "activity": "SYNTHETIC invalid", "duration_min": 10}])
                before = self.path.read_bytes()
                with self.assertRaises(coach.CoachError):
                    self.rows()
                with self.assertRaises(coach.CoachError):
                    self.write("exercise-add", items=[{"activity": "SYNTHETIC new", "duration_min": 2}])
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse((self.ws / "data/coach-state.json").exists())

    def test_malformed_later_row_prevents_partial_migration(self):
        self.seed([{"activity": "SYNTHETIC valid", "duration_min": 5}, {"activity": "SYNTHETIC invalid"}])
        before = self.path.read_bytes()
        with self.assertRaises(coach.CoachError):
            self.rows()
        with self.assertRaises(coach.CoachError):
            self.write("exercise-add", items=[{"activity": "SYNTHETIC new", "duration_min": 2}])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.ws / "data/coach-state.json").exists())

    def test_interrupted_migration_recovers_same_ids_before_or_after_data_write(self):
        for stage in ("before-data", "after-data", "before-receipt"):
            with self.subTest(stage=stage):
                original = self.seed()
                atomic = coach_store.atomic

                def fail(path, raw):
                    if path == self.path and stage in {"before-data", "after-data"}:
                        if stage == "after-data":
                            atomic(path, raw)
                        raise OSError("synthetic interrupted migration")
                    if path == self.c.store.state_path and stage == "before-receipt" and json.loads(raw)["pending"] is None:
                        raise OSError("synthetic interrupted receipt")
                    return atomic(path, raw)

                with mock.patch.object(coach_store, "atomic", side_effect=fail), self.assertRaises(OSError):
                    self.write("exercise-add", items=[])
                state = json.loads(self.c.store.state_path.read_text())
                pending = state["pending"]
                expected = json.loads(pending["changes"][0]["after"])
                expected_ids = [r["id"] for r in expected["2026-10-13"]["exercises"]]
                with mock.patch.object(coach.uuid, "uuid4", side_effect=AssertionError("must reuse journal IDs")):
                    recovered = self.rows(self.client())
                self.assertEqual([r["id"] for r in recovered], expected_ids)
                self.assertEqual(self.saved(), expected)
                self.assertEqual(json.loads(self.c.store.state_path.read_text())["pending"], None)
                backup = self.ws / f"data/coach-backups/{pending['id']}/data/exercise.json"
                self.assertEqual(json.loads(backup.read_text()), original)

    def test_concurrent_readers_and_adders_share_ids_and_lose_no_records(self):
        self.seed()

        def run(i):
            client = self.client()
            if i % 2:
                client.execute("exercise-add", {"request_id": f"parallel:{i}",
                               "items": [{"activity": f"SYNTHETIC extra {i}", "duration_min": 2}]})
            return {r["activity"]: r["id"] for r in self.rows(client)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(run, range(16)))
        for activity in ("SYNTHETIC A", "SYNTHETIC B", "SYNTHETIC C"):
            # Readers that acquired the lock before the first write see temporary
            # IDs; all readers after that write must agree on the persisted UUID.
            self.assertEqual(len({r[activity] for r in results if r[activity].startswith("exercise_")}), 1)
        final = self.rows()
        self.assertEqual(len(final), 11)
        self.assertEqual(len({r["id"] for r in final}), 11)

    def test_interrupted_normalization_retry_still_performs_requested_mutation(self):
        self.seed()
        payload = {"request_id": "migration-then-add", "items": [{"activity": "SYNTHETIC new", "duration_min": 2}]}
        atomic = coach_store.atomic

        def fail(path, raw):
            if path == self.path:
                raise OSError("synthetic interrupted migration")
            return atomic(path, raw)

        with mock.patch.object(coach_store, "atomic", side_effect=fail), self.assertRaises(OSError):
            self.c.execute("exercise-add", payload)
        pending = json.loads(self.c.store.state_path.read_text())["pending"]
        self.assertNotEqual(pending["key"], coach_store.sha(payload["request_id"].encode()))
        normalized = json.loads(pending["changes"][0]["after"])["2026-10-13"]["exercises"]
        result = self.client().execute("exercise-add", copy.deepcopy(payload))
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["action"], "exercise-add")
        self.assertEqual(self.saved()["2026-10-13"]["exercises"][:3], normalized)
        self.assertEqual(self.saved()["2026-10-13"]["exercises"][3:], result["exercises"])
        self.assertTrue(self.client().execute("exercise-add", copy.deepcopy(payload))["duplicate"])
        self.assertEqual(len(self.rows()), 4)

    def test_concurrent_identical_adds_keep_one_receipt_and_one_new_record(self):
        self.seed()
        payload = {"request_id": "parallel-same-request", "items": [{"activity": "SYNTHETIC new", "duration_min": 2}]}

        def run(_):
            return self.client().execute("exercise-add", copy.deepcopy(payload))

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(run, range(10)))
        self.assertEqual(sum(not r["duplicate"] for r in results), 1)
        self.assertEqual(len({r["exercises"][0]["id"] for r in results}), 1)
        self.assertEqual(len(self.rows()), 4)

    def test_custom_id_with_legacy_prefix_is_not_a_position_reference(self):
        self.seed([{"id": "legacy_exercise_custom", "activity": "SYNTHETIC custom", "duration_min": 10}])
        self.delete(self.rows()[0])
        self.assertEqual(self.rows(), [])

    def test_review_save_error_does_not_normalize_exercises(self):
        self.seed()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(coach.CoachError, "Review data changed"):
            self.write("review-save", start="2026-10-13", end="2026-10-13",
                       expected_snapshot="old-position-based-snapshot", analysis="SYNTHETIC stale review")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.ws / "data/coach-state.json").exists())
        self.assertFalse((self.ws / "data/coach-reviews.json").exists())

    def test_exercise_reminder_gate_does_not_normalize_exercises(self):
        self.seed()
        before = self.path.read_bytes()
        self.write("reminder-set", key="exercise", expected_revision=0, confirmed=True, settings={
            "enabled": True, "kind": "exercise", "time": "09:00", "weekdays": list(range(7)),
            "route": {"agent": "synthetic-coach", "channel": "synthetic", "to": "synthetic-user"}})
        spec = self.c.execute("reminder-specs")["specs"][0]
        self.write("reminder-bind", key="exercise", spec_hash=spec["spec_hash"],
                   observed_job={"id": "synthetic-job", **spec["job"]})
        result = self.c.execute("reminder-gate", key="exercise")
        self.assertFalse(result["send"])
        self.assertEqual(result["reason"], "exercise_already_recorded")
        self.assertEqual(self.path.read_bytes(), before)

    def test_retries_and_revision_guards_keep_migrated_ids(self):
        self.seed()
        a, b, c = self.normalize()
        payload = {"request_id": "retry-update", "id": b["id"], "expected_revision": 0,
                   "item": {"activity": "SYNTHETIC B corrected", "duration_min": 25}}
        first = self.c.execute("exercise-update", payload)
        self.delete(a)
        retry = self.client().execute("exercise-update", copy.deepcopy(payload))
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["exercise"], first["exercise"])
        before = self.path.read_bytes()
        with self.assertRaisesRegex(coach.CoachError, "Revision conflict"):
            self.c.execute("exercise-update", {**payload, "request_id": "new-stale-request"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual([r["id"] for r in self.rows()], [b["id"], c["id"]])


if __name__ == "__main__":
    unittest.main()
