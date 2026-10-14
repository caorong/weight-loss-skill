"""Daily/weekly review evidence regression tests; synthetic local data only."""
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
from coach_review import enrich_report
from coach_store import packed, sha


class ReviewReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="review reports ")
        self.ws = Path(self.tmp.name)
        self.now = datetime(2026, 10, 14, 20, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.c = coach.Coach(self.ws, "Asia/Tokyo", clock=lambda: self.now)
        self.sequence = 0

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, action, **payload):
        self.sequence += 1
        return self.c.execute(action, {"request_id": f"report-test:{self.sequence}", **payload})

    def plan(self, day, kind="goal", **targets):
        old = self.now
        self.now = datetime.fromisoformat(day + "T08:00:00+09:00")
        plan = self.write("plan-save", kind=kind, body="SYNTHETIC plan: walk three times per week",
                          targets=targets)["plan"]
        self.write("plan-activate", plan_id=plan["id"], confirmed=True)
        self.now = old
        return plan

    def meal(self, day, foods=None, meal_type="lunch", legacy=False):
        self.sequence += 1
        foods = foods if foods is not None else [self.food()]
        ledger = coach.meals_module.Ledger(self.ws, "Asia/Tokyo")
        path = ledger.day_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = json.loads(path.read_text()) if path.exists() else []
        row = {"id": f"meal-{self.sequence}", "meal_type": meal_type, "date": day,
               "foods": foods, "status": "consumed", **coach.meals_module.totals(foods)}
        if legacy:
            row.update({"calories": 500, "protein": 20, "carbs": 70, "fat": 15})
        rows.append(row)
        path.write_text(json.dumps(rows))
        return row

    def food(self, name="SYNTHETIC rice", calories=400, quantity=100, **changes):
        self.sequence += 1
        return {"id": f"food-{self.sequence}", "name": name, "calories": calories,
                "quantity": quantity, "unit": "g", "protein": 10, "carbs": 50, "fat": 10,
                "source": "user_provided", "portion_source": "user_measured", **changes}

    def report(self, start="2026-10-14", end=None, kind="daily"):
        # Use the public base report, then the helper, so tests work both before
        # and after the orchestrating Coach integration is applied.
        result = self.c.execute("review", start=start, end=end or start)
        result.pop("snapshot", None)
        return enrich_report(self.c, result, kind)

    def weight(self, day, value, **extra):
        return self.write("weight-add", date=day, value=value, unit="kg", **extra)

    def seed_two_weeks(self):
        for day in ("2026-09-29", "2026-10-01", "2026-10-04"):
            self.weight(day, 80, fasting=True)
        for day in ("2026-10-05", "2026-10-08", "2026-10-11"):
            self.weight(day, 79, fasting=True)

    def test_daily_rejects_multi_day_range(self):
        with self.assertRaisesRegex(coach.CoachError, "exactly one day"):
            self.report("2026-10-13", "2026-10-14")

    def test_unknown_review_type_rejected(self):
        with self.assertRaisesRegex(coach.CoachError, "review_type"):
            self.report(kind="monthly")

    def test_empty_day_is_unknown_not_minus_target(self):
        self.plan("2026-10-14", calories=1800)
        result = self.report()
        day = result["meal_days"][0]
        self.assertIsNone(day["intake_minus_target"]["calories"])
        self.assertEqual(day["completeness"]["status"], "no_records")
        self.assertIsNone(result["exercise_estimate"]["known_net_kcal"])
        self.assertEqual(result["food_tradeoff_candidates"], [])
        self.assertFalse(result["coverage"]["full_intake_confirmed"])
        self.assertIn("intake is unknown, not zero", " ".join(day["unknowns"]))

    def test_effective_targets_and_differences_apply_per_date(self):
        first = self.plan("2026-10-12", calories=1800, protein=90, carbs=210, fat=60)
        second = self.plan("2026-10-13", calories=1900, protein=95)
        for day in ("2026-10-12", "2026-10-13"):
            self.meal(day, [self.food(calories=2000, protein=100, carbs=220, fat=70)])
        result = self.report("2026-10-12", "2026-10-13", "custom")
        a, b = result["meal_days"]
        self.assertEqual([a["goal_plan_id"], b["goal_plan_id"]], [first["id"], second["id"]])
        self.assertEqual(a["intake_minus_target"], {"calories": 200, "protein": 10, "carbs": 10, "fat": 10})
        self.assertEqual(b["intake_minus_target"], {"calories": 100, "protein": 5, "carbs": None, "fat": None})
        self.assertFalse(a["target_comparison"]["energy_balance_known"])
        self.assertFalse(a["target_comparison"]["exercise_adjustment_applied"])

    def test_draft_target_does_not_leak_into_comparison(self):
        self.write("plan-save", kind="goal", body="SYNTHETIC draft", targets={"calories": 1800})
        self.meal("2026-10-14")
        day = self.report()["meal_days"][0]
        self.assertIsNone(day["effective_targets"]["calories"])
        self.assertIsNone(day["intake_minus_target"]["calories"])

    def test_current_cutoff_and_three_meals_do_not_claim_complete_intake(self):
        for kind in ("breakfast", "lunch", "dinner"):
            self.meal("2026-10-14", meal_type=kind)
        result = self.report()
        day = result["meal_days"][0]
        self.assertEqual(result["cutoff_at"], self.now.isoformat())
        self.assertEqual(day["completeness"]["status"], "partial_day")
        self.assertEqual(day["completeness"]["missing_main_meal_types"], [])
        self.assertFalse(day["completeness"]["full_intake_confirmed"])
        self.assertEqual(day["completeness"]["intake_window_end_exclusive"], "2026-10-15T00:00:00+09:00")

    def test_closed_day_still_has_unverified_intake(self):
        self.meal("2026-10-13")
        day = self.report("2026-10-13")["meal_days"][0]
        self.assertTrue(day["completeness"]["calendar_day_closed"])
        self.assertEqual(day["completeness"]["status"], "unverified")
        self.assertEqual(day["completeness"]["missing_main_meal_types"], ["breakfast", "dinner"])
        self.assertIn("does not mean not eaten", day["completeness"]["missing_meal_interpretation"])

    def test_cutoff_uses_local_midnight_across_dst(self):
        self.now = datetime(2026, 11, 1, 6, 30, tzinfo=ZoneInfo("UTC"))
        self.c = coach.Coach(self.ws, "America/New_York", clock=lambda: self.now)
        result = self.report("2026-11-01")
        self.assertEqual(result["cutoff_at"], "2026-11-01T01:30:00-05:00")
        self.assertEqual(result["meal_days"][0]["completeness"]["intake_window_end_exclusive"], "2026-11-02T00:00:00-05:00")

    def test_exercise_is_separate_and_missing_is_not_zero_estimate(self):
        self.plan("2026-10-14", calories=1800)
        self.meal("2026-10-14", [self.food(calories=2000)])
        self.write("exercise-add", items=[{"activity": "SYNTHETIC walk", "duration_min": 30,
                    "calories_kcal": 250, "net_calories_kcal": 200, "source": "device"},
                    {"activity": "SYNTHETIC stretch", "duration_min": 10}])
        result = self.report()
        day = result["meal_days"][0]
        self.assertEqual(day["intake_minus_target"]["calories"], 200)
        self.assertEqual(result["exercise_estimate"]["known_net_kcal"], 200)
        self.assertEqual(result["exercise_estimate"]["known_gross_kcal"], 250)
        self.assertEqual(result["exercise_estimate"]["net_unknown_sessions"], 1)
        self.assertFalse(result["exercise_estimate"]["subtracted_from_intake"])
        self.assertFalse(result["exercise_estimate"]["added_to_intake_target"])

    def test_gross_exercise_calories_not_treated_as_net(self):
        self.write("exercise-add", items=[{"activity": "SYNTHETIC walk", "duration_min": 30,
                                          "calories_kcal": 250}])
        estimate = self.report()["exercise_estimate"]
        self.assertIsNone(estimate["known_net_kcal"])
        self.assertEqual(estimate["known_gross_kcal"], 250)

    def test_explicit_zero_exercise_estimate_remains_distinguishable(self):
        self.write("exercise-add", items=[{"activity": "SYNTHETIC", "duration_min": 1, "net_calories_kcal": 0}])
        self.assertEqual(self.report()["exercise_estimate"]["known_net_kcal"], 0)

    def test_candidate_references_actual_food_and_is_optional(self):
        self.plan("2026-10-14", calories=1800)
        food = self.food("SYNTHETIC cake", calories=2000)
        meal = self.meal("2026-10-14", [food])
        result = self.report()
        candidate, = result["food_tradeoff_candidates"]
        self.assertEqual(candidate["food_id"], food["id"])
        self.assertEqual(candidate["meal_id"], meal["id"])
        self.assertEqual(candidate["scope"], "optional_retrospective_tradeoff")
        self.assertIsNone(candidate["estimated_calorie_saving"])
        self.assertIn("no need to compensate", candidate["condition"])
        self.assertEqual(result["review_boundaries"]["tomorrow_tasks"], [])

    def test_candidates_are_bounded_and_staples_are_not_portion_cut_candidates(self):
        self.plan("2026-10-14", calories=1800)
        self.meal("2026-10-14", [self.food(name, calories=1000) for name in (
            "SYNTHETIC rice", "SYNTHETIC chicken", "SYNTHETIC cake", "SYNTHETIC cookie",
            "SYNTHETIC ice cream", "SYNTHETIC regular soda")])
        result = self.report()
        self.assertEqual(len(result["food_tradeoff_candidates"]), 3)
        self.assertTrue(all(c["evidence_category"] in {"dessert", "explicitly_sweetened_drink"}
                            for c in result["food_tradeoff_candidates"]))

    def test_under_target_no_target_and_ambiguous_foods_do_not_create_restriction_options(self):
        self.meal("2026-10-14", [self.food("SYNTHETIC cake", calories=500)])
        self.assertEqual(self.report()["food_tradeoff_candidates"], [])
        self.plan("2026-10-14", calories=1800)
        self.assertEqual(self.report()["food_tradeoff_candidates"], [])
        self.meal("2026-10-14", [self.food("SYNTHETIC milk tea", calories=1500)])
        choices = self.report()["food_tradeoff_candidates"]
        self.assertEqual([c["food_name"] for c in choices], ["SYNTHETIC cake"])

    def test_sugar_free_drink_is_not_classified_as_sweetened(self):
        self.plan("2026-10-14", calories=1800)
        self.meal("2026-10-14", [self.food("SYNTHETIC sugar-free regular soda", calories=2000)])
        self.assertEqual(self.report()["food_tradeoff_candidates"], [])

    def test_rice_and_fish_cakes_are_not_assumed_to_be_desserts(self):
        self.plan("2026-10-14", calories=1800)
        self.meal("2026-10-14", [self.food("SYNTHETIC rice cakes", calories=1000),
                                 self.food("SYNTHETIC fish cake", calories=1000)])
        self.assertEqual(self.report()["food_tradeoff_candidates"], [])

    def test_legacy_invalid_provenance_is_unknown_and_not_candidate_evidence(self):
        self.plan("2026-10-14", calories=1800)
        self.meal("2026-10-14", [self.food("SYNTHETIC cake", calories=2000,
                                         source=None, portion_source={"old": "format"})])
        day = self.report()["meal_days"][0]
        self.assertEqual(day["food_evidence"][0]["source"], "unknown")
        self.assertEqual(day["food_evidence"][0]["portion_source"], "unknown")
        self.assertEqual(day["food_tradeoff_candidates"], [])

    def test_known_health_or_restriction_context_withholds_portion_options(self):
        self.plan("2026-10-14", calories=1800)
        self.meal("2026-10-14", [self.food("SYNTHETIC cake", calories=2000)])
        self.write("profile-set", expected_revision=0, fields={"health_flags": ["SYNTHETIC clinical context"]})
        self.assertEqual(self.report()["food_tradeoff_candidates"], [])

    def test_legacy_food_detail_never_fabricated(self):
        self.plan("2026-10-14", calories=100)
        self.meal("2026-10-14", [{"id": "old-food", "name": "SYNTHETIC cake"}], legacy=True)
        day = self.report()["meal_days"][0]
        self.assertEqual(day["totals"]["calories"], 500)
        self.assertIsNone(day["food_evidence"][0]["nutrients"]["calories"])
        self.assertEqual(day["food_tradeoff_candidates"], [])
        self.assertEqual(day["largest_recorded_food_contributors"], [])
        self.assertIn("details are missing", " ".join(day["unknowns"]))

    def test_daily_foods_do_not_include_yesterday(self):
        self.meal("2026-10-13", [self.food("YESTERDAY cake")])
        self.meal("2026-10-14", [self.food("TODAY rice")])
        result = self.report()
        self.assertEqual([f["name"] for f in result["meal_days"][0]["food_evidence"]], ["TODAY rice"])
        self.assertNotIn("weekly_review", result)

    def test_weekly_trend_reads_previous_week_without_expanding_meal_range(self):
        self.seed_two_weeks()
        self.meal("2026-10-01", [self.food("PRIOR WEEK cake")])
        self.meal("2026-10-08", [self.food("REVIEWED rice")])
        result = self.report("2026-10-05", "2026-10-11", "weekly")
        trend = result["weekly_review"]["weight_trend"]
        self.assertEqual(trend["mean_change_kg"], -1)
        self.assertEqual((trend["previous_start"], trend["previous_end"]), ("2026-09-28", "2026-10-04"))
        self.assertEqual(len(result["weights"]), 3)
        self.assertEqual(len(result["meal_days"]), 7)
        self.assertEqual(result["summary"]["logged_days"], 1)
        self.assertNotIn("PRIOR WEEK cake", json.dumps(result))
        self.assertTrue(result["weekly_review"]["calendar_week"])
        self.assertFalse(result["weekly_review"]["sufficient_data"]["recorded_intake_discussion"])

    def test_weekly_multiple_same_day_readings_do_not_fill_missing_dates(self):
        for value in (80, 81, 82):
            self.weight("2026-10-01", value)
        for day in ("2026-10-05", "2026-10-08", "2026-10-11"):
            self.weight(day, 79)
        trend = self.report("2026-10-05", "2026-10-11", "weekly")["weekly_review"]["weight_trend"]
        self.assertEqual(trend["previous_weight_days"], 1)
        self.assertIsNone(trend["mean_change_kg"])
        self.assertIn("fewer_than_three_previous_weight_dates", trend["limitations"])

    def test_weekly_fasting_median_and_historical_anchor(self):
        self.seed_two_weeks()
        self.weight("2026-10-01", 100, fasting=False)
        self.weight("2026-10-13", 200)
        trend = self.report("2026-10-05", "2026-10-11", "weekly")["weekly_review"]["weight_trend"]
        self.assertEqual(trend["mean_change_kg"], -1)
        self.assertEqual(trend["previous_mean_kg"], 80)
        self.assertEqual(trend["reviewed_mean_kg"], 79)

    def test_prior_week_bounds_exclude_older_weights(self):
        for day in ("2026-09-25", "2026-09-26", "2026-09-27", "2026-10-01"):
            self.weight(day, 80)
        for day in ("2026-10-05", "2026-10-08", "2026-10-11"):
            self.weight(day, 79)
        self.assertIsNone(self.report("2026-10-05", "2026-10-11", "weekly")["weekly_review"]["weight_trend"]["mean_change_kg"])

    def test_short_or_open_week_is_not_ready_for_weekly_trend(self):
        self.seed_two_weeks()
        for day in ("2026-10-12", "2026-10-13", "2026-10-14"):
            self.weight(day, 78)
        result = self.report("2026-10-08", "2026-10-14", "weekly")
        self.assertFalse(result["weekly_review"]["sufficient_data"]["weight_trend"])
        self.assertIsNone(result["weekly_review"]["weight_trend"]["mean_change_kg"])
        short = self.report("2026-10-05", "2026-10-08", "weekly")
        self.assertIn("reviewed_window_is_not_seven_days", short["weekly_review"]["weight_trend"]["limitations"])

    def test_weekly_recurring_observations_are_records_not_causal_claims(self):
        self.plan("2026-10-05", calories=1800)
        for day in ("2026-10-05", "2026-10-08"):
            self.meal(day, [self.food("SYNTHETIC cake", calories=2000)])
        week = self.report("2026-10-05", "2026-10-11", "weekly")["weekly_review"]
        over = next(r for r in week["recurring_observations"] if r["kind"] == "recorded_intake_above_effective_target")
        self.assertEqual(over["count"], 2)
        self.assertIn("not energy surplus", over["meaning"])
        foods = next(r for r in week["recurring_observations"] if r["kind"] == "repeated_food_tradeoff_evidence")
        self.assertIn("not a proven cause", foods["meaning"])
        missing = next(r for r in week["recurring_observations"] if r["kind"] == "no_meal_records")
        self.assertIn("do not establish", missing["meaning"])

    def test_exercise_plan_adherence_does_not_invent_numeric_schedule(self):
        plan = self.plan("2026-10-05", kind="exercise")
        self.write("exercise-add", date="2026-10-08", items=[{"activity": "SYNTHETIC walk", "duration_min": 20}])
        adherence = self.report("2026-10-05", "2026-10-11", "weekly")["weekly_review"]["exercise_adherence"]
        self.assertEqual(adherence["effective_plan_ids"], [plan["id"]])
        self.assertEqual(adherence["recorded_sessions"], 1)
        self.assertEqual(adherence["reason"], "plan_schedule_is_unstructured")
        self.assertIsNone(adherence["planned_sessions"])
        self.assertIsNone(adherence["completion_rate"])

    def test_no_plan_or_exercise_logs_is_not_failed_adherence(self):
        adherence = self.report("2026-10-05", "2026-10-11", "weekly")["weekly_review"]["exercise_adherence"]
        self.assertEqual(adherence["reason"], "no_confirmed_exercise_plan")
        self.assertEqual(adherence["status"], "not_assessable")
        self.assertIsNone(adherence["completion_rate"])

    def test_output_is_stable_except_cutoff_when_clock_advances(self):
        self.meal("2026-10-14")
        a = self.report()
        self.now += timedelta(minutes=2)
        b = self.report()
        self.assertNotEqual(a.pop("cutoff_at"), b.pop("cutoff_at"))
        self.assertEqual(a, b)

    def test_prior_week_weight_change_affects_weekly_snapshot_material(self):
        self.seed_two_weeks()
        a = self.report("2026-10-05", "2026-10-11", "weekly")
        self.weight("2026-09-30", 70)
        b = self.report("2026-10-05", "2026-10-11", "weekly")
        a.pop("cutoff_at")
        b.pop("cutoff_at")
        self.assertNotEqual(sha(packed(a).encode()), sha(packed(b).encode()))

    def test_enrichment_does_not_change_existing_totals_or_write_files(self):
        self.plan("2026-10-14", calories=1800)
        self.meal("2026-10-14")
        base = self.report()  # Establish the existing coach/meal lock files first.
        before = {str(p.relative_to(self.ws)): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}
        result = enrich_report(self.c, copy.deepcopy(base), "daily")
        after = {str(p.relative_to(self.ws)): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["summary"]["recorded_totals"]["calories"], 400)
        self.assertEqual(result["meal_days"][0]["totals"]["calories"], 400)
        self.assertFalse(result["review_boundaries"]["automatic_plan_change"])


if __name__ == "__main__":
    unittest.main()
