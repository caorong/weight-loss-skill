"""Deterministic, read-only evidence for daily and weekly coach reviews.

The caller owns the coach lock and the meal snapshot. This module does not
write data, infer unrecorded consumption, prescribe a deficit, or parse free
text exercise plans into invented schedules. Narrative belongs to the skill.
"""
from __future__ import annotations

import copy
import math
import re
import statistics
from datetime import date, datetime, time, timedelta

from coach_store import need

NUTRIENTS = ("calories", "protein", "carbs", "fat")
MAIN_MEALS = ("breakfast", "lunch", "dinner")
# Conservative name evidence, not nutritional inference from a photo or meal
# type. A generic snack, milk tea, juice, or staple is not enough evidence.
SWEET_DRINK = re.compile(
    r"\b(?:regular soda|sugar[- ]sweetened (?:drink|beverage|tea)|sweetened soda)\b|"
    r"(?:含糖饮料|加糖奶茶|全糖奶茶|加糖汽水)", re.I)
DESSERT = re.compile(r"\b(?:cake|cookies?|ice cream|doughnuts?|donuts?)\b|(?:蛋糕|曲奇|冰淇淋|甜甜圈)", re.I)
NON_DESSERT_CAKE = re.compile(r"\b(?:rice|fish|potato|corn|tofu) cakes?\b|(?:米糕|鱼糕|雞蛋糕|鸡蛋糕)", re.I)
FOOD_SOURCES = {"model_estimate", "package_label", "user_provided"}
PORTION_SOURCES = {"photo_estimate", "user_description", "user_measured", "package_serving"}
NEGATED_SWEET = re.compile(r"\b(?:unsweetened|sugar[- ]free|zero[- ]sugar|diet soda)\b|(?:无糖|零糖|不加糖)", re.I)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _daily_weights(rows):
    """Match Coach.report's one-value-per-day fasting/otherwise median rule."""
    result = []
    for day in sorted({r["date"] for r in rows}):
        readings = [r for r in rows if r["date"] == day]
        fasting = [r for r in readings if r.get("fasting") is True]
        selected = fasting or readings
        result.append({"date": day, "kg": round(statistics.median(r["value"] for r in selected), 3),
                       "method": "fasting_median" if fasting else "all_readings_median",
                       "readings": len(selected)})
    return result


def _exercise_summary(rows):
    net = [r["net_calories_kcal"] for r in rows if _number(r.get("net_calories_kcal"))]
    gross = [r["calories_kcal"] for r in rows if _number(r.get("calories_kcal"))]
    return {"recorded_sessions": len(rows),
            "recorded_minutes": round(sum(r["duration_min"] for r in rows), 2),
            "known_net_kcal": round(sum(net), 2) if net else None,
            "known_gross_kcal": round(sum(gross), 2) if gross else None,
            "net_unknown_sessions": len(rows) - len(net),
            "gross_unknown_sessions": len(rows) - len(gross),
            "sources": sorted({r.get("source") if isinstance(r.get("source"), str) else "unknown" for r in rows}),
            "coverage": "recorded_sessions_only", "calories_are_estimates": True,
            "added_to_intake_target": False, "subtracted_from_intake": False}


def _food_evidence(day):
    result = []
    for meal in day["records"]:
        for food in meal.get("foods", []):
            item = {"date": day["date"], "meal_id": meal["id"], "food_id": food["id"],
                    "meal_type": meal.get("meal_type", "unknown"),
                    "name": food.get("name") if isinstance(food.get("name"), str) else None,
                    "quantity": food.get("quantity"), "unit": food.get("unit"),
                    "source": food.get("source") if isinstance(food.get("source"), str) and food["source"] in FOOD_SOURCES else "unknown",
                    "portion_source": food.get("portion_source") if isinstance(food.get("portion_source"), str) and food["portion_source"] in PORTION_SOURCES else "unknown",
                    "nutrients": {k: food.get(k) if _number(food.get(k)) else None for k in NUTRIENTS}}
            item["item_nutrients_complete"] = all(v is not None for v in item["nutrients"].values())
            result.append(item)
    return result


def _tradeoffs(day, profile, preferences):
    """Offer bounded retrospective options only for explicitly evidenced foods.

    No calorie-savings guesses, substitute-food safety guesses, meal skipping,
    or compensation. Known clinical/restriction context needs individualized
    interpretation, so deterministic portion options are withheld in that case.
    """
    delta = day["intake_minus_target"]["calories"]
    if delta is None or delta <= 0:
        return []
    if (profile.get("health_flags") or profile.get("medications") or
            (_number(profile.get("age_years")) and profile["age_years"] < 18) or
            preferences.get("allergies") or preferences.get("dietary_restrictions")):
        return []
    candidates = []
    for food in day["food_evidence"]:
        name = food["name"] or ""
        if (not food["item_nutrients_complete"] or food["nutrients"]["calories"] <= 0 or
                not _number(food["quantity"]) or food["quantity"] <= 0 or
                not isinstance(food["unit"], str) or food["unit"] not in {"g", "ml", "serving"} or
                food["source"] == "unknown" or food["portion_source"] == "unknown"):
            continue
        if SWEET_DRINK.search(name) and not NEGATED_SWEET.search(name):
            category = "explicitly_sweetened_drink"
            option = "Consider whether a less-sweet version of this recorded drink would be equally satisfying."
        elif DESSERT.search(name) and not NON_DESSERT_CAKE.search(name):
            category = "dessert"
            option = "Consider whether a smaller portion of this recorded dessert would be equally satisfying."
        else:
            continue
        candidates.append({"date": day["date"], "meal_id": food["meal_id"], "food_id": food["food_id"],
                           "food_name": name, "evidence_category": category,
                           "recorded_quantity": food["quantity"], "unit": food["unit"],
                           "recorded_calories": food["nutrients"]["calories"],
                           "option": option, "estimated_calorie_saving": None,
                           "scope": "optional_retrospective_tradeoff",
                           "condition": "Only if suitable for health, dietary needs, hunger and preference; no need to compensate."})
    return sorted(candidates, key=lambda c: (-c["recorded_calories"], str(c["food_id"])))[:3]


def _weekly(coach, report):
    a, b = date.fromisoformat(report["start"]), date.fromisoformat(report["end"])
    previous_a, previous_b = a - timedelta(days=7), a - timedelta(days=1)
    prior = _daily_weights([r for r in coach.weight_rows()
                           if previous_a.isoformat() <= r["date"] <= previous_b.isoformat()])
    recent = report["summary"]["daily_weights"]
    full_week = (b - a).days == 6
    closed = b < coach.now.date()
    sufficient = full_week and closed and len(prior) >= 3 and len(recent) >= 3
    reasons = []
    if not full_week:
        reasons.append("reviewed_window_is_not_seven_days")
    if not closed:
        reasons.append("reviewed_window_still_open")
    if len(prior) < 3:
        reasons.append("fewer_than_three_previous_weight_dates")
    if len(recent) < 3:
        reasons.append("fewer_than_three_reviewed_weight_dates")
    trend = {"method": "difference_of_weekly_means_of_daily_medians",
             "status": "descriptive_only" if sufficient else "insufficient_data",
             "minimum_weight_dates_per_week": 3, "previous_start": previous_a.isoformat(),
             "previous_end": previous_b.isoformat(), "reviewed_start": report["start"],
             "reviewed_end": report["end"], "previous_daily_weights": prior,
             "reviewed_daily_weights": copy.deepcopy(recent),
             "previous_weight_days": len(prior), "reviewed_weight_days": len(recent),
             "previous_mean_kg": round(statistics.mean(w["kg"] for w in prior), 3) if len(prior) >= 3 else None,
             "reviewed_mean_kg": round(statistics.mean(w["kg"] for w in recent), 3) if len(recent) >= 3 else None,
             "mean_change_kg": round(statistics.mean(w["kg"] for w in recent) -
                                     statistics.mean(w["kg"] for w in prior), 3) if sufficient else None,
             "limitations": reasons + ["Weight change does not establish fat change or its cause."]}
    days = report["meal_days"]
    observations = []
    over = [d["date"] for d in days if d["intake_minus_target"]["calories"] is not None
            and d["intake_minus_target"]["calories"] > 0]
    if len(over) >= 2:
        observations.append({"kind": "recorded_intake_above_effective_target", "dates": over,
                             "count": len(over), "meaning": "Recorded intake-target differences, not energy surplus."})
    no_records = [d["date"] for d in days if not d["has_records"]]
    if len(no_records) >= 2:
        observations.append({"kind": "no_meal_records", "dates": no_records, "count": len(no_records),
                             "meaning": "Recording gaps do not establish that meals were skipped."})
    for meal_type in MAIN_MEALS:
        dates = [d["date"] for d in days if d["has_records"] and meal_type not in d["recorded_meal_types"]]
        if len(dates) >= 2:
            observations.append({"kind": "meal_type_not_recorded", "meal_type": meal_type,
                                 "dates": dates, "count": len(dates),
                                 "meaning": "This meal type was not logged; actual consumption is unknown."})
    for category in ("explicitly_sweetened_drink", "dessert"):
        evidence = [c for d in days for c in d["food_tradeoff_candidates"] if c["evidence_category"] == category]
        dates = sorted({c["date"] for c in evidence})
        if len(dates) >= 2:
            observations.append({"kind": "repeated_food_tradeoff_evidence", "category": category,
                                 "dates": dates, "count": len(dates),
                                 "food_ids": [c["food_id"] for c in evidence],
                                 "meaning": "A repeated recorded choice for discussion, not a proven cause of weight change."})
    plan_ids = sorted({d["exercise_plan_id"] for d in days if d["exercise_plan_id"]})
    adherence = {"status": "not_assessable", "reason": "plan_schedule_is_unstructured" if plan_ids else "no_confirmed_exercise_plan",
                 "effective_plan_ids": plan_ids,
                 "plan_by_date": [{"date": d["date"], "plan_id": d["exercise_plan_id"]} for d in days],
                 "recorded_sessions": len(report["exercises"]),
                 "recorded_active_days": len({r["date"] for r in report["exercises"]}),
                 "recorded_minutes": report["summary"]["exercise_minutes"],
                 "planned_sessions": None, "completion_rate": None,
                 "limitations": ["Free-text plans are not a machine-verified schedule.",
                                 "No exercise record does not establish that exercise was missed."]}
    return {"calendar_week": full_week and a.weekday() == 0 and b.weekday() == 6,
            "calendar_window_closed": closed, "weight_trend": trend,
            "recurring_observations": observations, "exercise_adherence": adherence,
            "sufficient_data": {"recorded_intake_discussion": sum(d["has_records"] for d in days) >= 5,
                                "complete_intake_comparison": False, "weight_trend": sufficient,
                                "exercise_adherence": False},
            "automatic_plan_change": False}


def enrich_report(coach, report, review_type="custom"):
    """Enrich and return a base Coach.report, without hashing or writing it.

    Call before computing snapshot. Exclude only ``cutoff_at`` from that hash:
    the query clock changes without source data changing. All substantive
    fields (including review type, coverage, historical targets and prior-week
    weight observations) must stay in the snapshot for review-save freshness.
    """
    need(review_type in {"daily", "weekly", "custom"}, "review_type: daily, weekly or custom")
    need(review_type != "daily" or report["start"] == report["end"], "Daily review requires exactly one day")
    report["review_type"] = review_type
    report["cutoff_at"] = coach.now.isoformat()
    registry = coach.plans()
    profile = report["profiles"]["profile"]
    preferences = report["profiles"]["preferences"]
    all_candidates = []
    for day in report["meal_days"]:
        goal = coach.active(registry, "goal", day["date"])
        exercise_plan = coach.active(registry, "exercise", day["date"])
        day["goal_plan_id"] = goal["id"] if goal else None
        day["exercise_plan_id"] = exercise_plan["id"] if exercise_plan else None
        day["effective_targets"] = {key: (goal or {}).get("targets", {}).get(key) for key in NUTRIENTS}
        day["target_calories"] = day["effective_targets"]["calories"]
        day["intake_minus_target"] = {
            key: round(day["totals"][key] - value, 2) if day["has_records"] and _number(value) else None
            for key, value in day["effective_targets"].items()}
        day["target_comparison"] = {"basis": "recorded_intake_minus_effective_target",
                                    "coverage": "recorded_only", "full_day_intake_known": False,
                                    "energy_balance_known": False, "exercise_adjustment_applied": False,
                                    "interpretation": "A negative difference does not establish under-eating; unrecorded intake is unknown."}
        closed = date.fromisoformat(day["date"]) < coach.now.date()
        next_day = date.fromisoformat(day["date"]) + timedelta(days=1)
        day["completeness"] = {
            "status": "no_records" if not day["has_records"] else "unverified" if closed else "partial_day",
            "calendar_day_closed": closed, "full_intake_confirmed": False,
            "missing_main_meal_types": [m for m in MAIN_MEALS if m not in day["recorded_meal_types"]],
            "missing_meal_interpretation": "Not recorded does not mean not eaten.",
            "intake_window_end_exclusive": datetime.combine(next_day, time.min, tzinfo=coach.tz).isoformat(),
            "record_cutoff_field": "cutoff_at"}
        day["exercise_estimate"] = _exercise_summary([r for r in report["exercises"] if r["date"] == day["date"]])
        day["food_evidence"] = _food_evidence(day)
        day["largest_recorded_food_contributors"] = sorted(
            [copy.deepcopy(f) for f in day["food_evidence"] if f["nutrients"]["calories"] is not None],
            key=lambda f: (-f["nutrients"]["calories"], str(f["food_id"])))[:3]
        day["food_tradeoff_candidates"] = _tradeoffs(day, profile, preferences)
        all_candidates.extend(day["food_tradeoff_candidates"])
        unknowns = ["Full-day intake completeness is not confirmed.",
                    "Total energy expenditure and energy balance are unknown."]
        if not closed:
            unknowns.append("The local calendar day is still in progress.")
        if not day["has_records"]:
            unknowns.append("There are no meal records; intake is unknown, not zero.")
        if any(v is None for v in day["effective_targets"].values()):
            unknowns.append("One or more nutrient targets have not been confirmed for this date.")
        if day["exercise_estimate"]["net_unknown_sessions"]:
            unknowns.append("Some recorded exercise sessions have no net calorie estimate.")
        if not day["exercise_estimate"]["recorded_sessions"]:
            unknowns.append("No exercise sessions were recorded; actual activity is unknown.")
        if any(not f["item_nutrients_complete"] or f["source"] == "unknown" or f["portion_source"] == "unknown"
               for f in day["food_evidence"]) or any(not m.get("foods") for m in day["records"]):
            unknowns.append("Some food-level nutrient, source or portion details are missing.")
        day["unknowns"] = unknowns
    report["food_tradeoff_candidates"] = sorted(
        all_candidates, key=lambda c: (-c["recorded_calories"], c["date"], str(c["food_id"])))[:3]
    report["exercise_estimate"] = _exercise_summary(report["exercises"])
    report["coverage"] = {"basis": "recorded_only", "full_intake_confirmed": False,
                          "calendar_window_closed": date.fromisoformat(report["end"]) < coach.now.date(),
                          "complete_intake_days": None,
                          "unrecorded_dates": [d["date"] for d in report["meal_days"] if not d["has_records"]]}
    report["review_boundaries"] = {"automatic_target_change": False, "automatic_plan_change": False,
                                   "compensation_recommended": False, "tomorrow_tasks": []}
    if review_type == "weekly":
        report["weekly_review"] = _weekly(coach, report)
    return report
