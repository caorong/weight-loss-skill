# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Nutrition calculator for diet-tracking-analysis skill.

Commands:
  detect-meal  — Detect meal type from timestamp, timezone, and meal schedule.
  target       — Compute daily macro targets from weight & calorie goal.
  analyze      — Compute cumulative intake, compare with targets, output status.
  save         — Persist a meal record to today's log file.
  load         — Load today's (or a given date's) meal records.
  evaluate     — Evaluate cumulative intake at a meal checkpoint (range-based).
  check-missing — Check which main meals are missing before the current meal.
  save-recommendation — Save meal recommendations for today.
  produce-check — Evaluate cumulative vegetable and fruit intake (China region).
  calibration-lookup — Look up user's portion calibrations for food items.
  oil-calibration-lookup — Look up user's oil calibrations for food items.

Usage:
  python3 nutrition-calc.py detect-meal --tz-offset 28800 --meals 3 \
      [--schedule '{"breakfast":"09:00","lunch":"12:00","dinner":"18:00"}'] \
      [--log '[...]'] [--timestamp 2026-03-17T11:14:13Z]
  python3 nutrition-calc.py target  --weight 65 --cal 1500 [--meals 3]
  python3 nutrition-calc.py analyze --weight 65 --cal 1500 --meals 3 \
      --log '[{"name":"breakfast","calories":379,"protein":24,"carbs":45,"fat":12}]'
  python3 nutrition-calc.py save --data-dir /path/to/data \
      --meal '{"name":"breakfast","meal_type":"breakfast","calories":379,"protein":24,"carbs":45,"fat":12,"foods":[{"name":"boiled eggs x2","calories":144}]}'
  python3 nutrition-calc.py load --data-dir /path/to/data [--date 2026-02-27]
  python3 nutrition-calc.py evaluate --weight 65 --cal 1500 --meals 3 \
      --current-meal lunch --log '[...]'
  python3 nutrition-calc.py check-missing --meals 3 --current-meal lunch --log '[...]'
  python3 nutrition-calc.py save-recommendation --data-dir /path/to/data \
      --meal-type lunch --items '["鸡胸肉+糙米+西兰花", "牛肉面+茶叶蛋", "沙拉+全麦面包+酸奶"]'
  python3 nutrition-calc.py produce-check --meals 3 --current-meal lunch --log '[...]'
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

def _normalize_path(p):
    """Lowercase wechat-dm/wecom-dm segment to avoid case-mismatch directories."""
    import re as _re
    return _re.sub(r'(workspace-(?:wechat|wecom)-dm-)([^/]+)', lambda m: m.group(1) + m.group(2).lower(), p)



def _local_date(tz_offset: int = None) -> str:
    """Return local date as YYYY-MM-DD string.
    If tz_offset (seconds from UTC) is given, compute local date from UTC now.
    Otherwise fall back to server's date.today().
    """
    if tz_offset is not None:
        utc_now = datetime.now(timezone.utc)
        local_dt = utc_now + timedelta(seconds=tz_offset)
        return local_dt.date().isoformat()
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Backward compatibility: migrate old short field names to full names
# ---------------------------------------------------------------------------

# Mapping from old short keys to new full keys
_SHORT_TO_LONG = {
    "cal": "calories", "p": "protein", "c": "carbs", "f": "fat",
    "protein_g": "protein", "carbs_g": "carbs", "fat_g": "fat",
}


def _migrate_meal(meal: dict) -> dict:
    """Normalize meal dicts across old and new formats.

    Handles: short keys (cal→calories), _g suffix keys (protein_g→protein),
    items→foods rename, and meal-level macro summation from food items.
    """
    out = {}
    for k, v in meal.items():
        new_key = _SHORT_TO_LONG.get(k, k)
        if new_key in out:
            continue
        if k == "foods" and isinstance(v, list):
            out[k] = [_migrate_meal(f) for f in v]
        else:
            out[new_key] = v

    # New format: items → foods
    if "items" in out and "foods" not in out:
        out["foods"] = [_migrate_meal(f) for f in out.pop("items")]

    # New format: no meal-level macros → sum from foods
    if "foods" in out and "calories" not in out:
        for key in ("calories", "protein", "carbs", "fat"):
            out[key] = round(sum(f.get(key, 0) for f in out["foods"]), 1)

    return out


def _migrate_meals(meals: list) -> list:
    """Migrate a list of meal dicts."""
    return [_migrate_meal(m) for m in meals]


# ---------------------------------------------------------------------------
# Meal blocks & aliases
# ---------------------------------------------------------------------------

MEAL_BLOCKS_3 = [
    {"label": "breakfast", "pct": 30, "meals": ["breakfast", "snack_am"]},
    {"label": "lunch",     "pct": 40, "meals": ["lunch", "snack_pm"]},
    {"label": "dinner",    "pct": 30, "meals": ["dinner"]},
]

MEAL_BLOCKS_2_DEFAULT = [
    {"label": "lunch",  "pct": 50, "meals": ["lunch", "snack_am"]},
    {"label": "dinner", "pct": 50, "meals": ["dinner", "snack_pm"]},
]

# Backward-compat alias: old meal_1/meal_2 names → standard names.
# Used when reading existing data. The actual mapping depends on the user's
# schedule; this is the default (most common: skip breakfast → lunch + dinner).
LEGACY_MEAL_ALIAS = {
    "meal_1":   "lunch",
    "meal_2":   "dinner",
    "snack_1":  "snack_am",
    "snack_2":  "snack_pm",
    "meal 1":   "lunch",
    "meal 2":   "dinner",
}


def _get_2meal_names(schedule: dict = None) -> tuple:
    """Determine which two standard meal names a 2-meal user has.

    Examines the schedule dict keys. If keys are standard names (breakfast,
    lunch, dinner), returns them sorted by time. If keys are meal_1/meal_2
    (legacy), infers from time windows. Falls back to ("lunch", "dinner").
    """
    if not schedule:
        return ("lunch", "dinner")

    standard = {"breakfast", "lunch", "dinner"}
    sched_standard = [k for k in schedule if k in standard]

    if len(sched_standard) == 2:
        sorted_meals = sorted(sched_standard,
                              key=lambda k: _parse_hhmm(schedule[k]))
        return tuple(sorted_meals)

    # Legacy keys: meal_1/meal_2 — infer from time windows
    if "meal_1" in schedule and "meal_2" in schedule:
        t1 = _parse_hhmm(schedule["meal_1"])
        t2 = _parse_hhmm(schedule["meal_2"])
        names = []
        for t in (t1, t2):
            if t < 10.5:
                names.append("breakfast")
            elif t < 15:
                names.append("lunch")
            else:
                names.append("dinner")
        # Deduplicate: if both map to the same (unlikely), use defaults
        if len(set(names)) < 2:
            return ("lunch", "dinner")
        return tuple(names)

    return ("lunch", "dinner")

# ---------------------------------------------------------------------------
# Diet mode configurations
# ---------------------------------------------------------------------------

DIET_MODE_FAT = {
    "usda":          (20, 35),
    "balanced":      (20, 35),
    "high_protein":  (20, 35),
    "low_carb":      (40, 50),
    "keto":          (65, 75),
    "mediterranean": (20, 35),
    "plant_based":   (20, 30),
    "if_16_8":       (20, 35),
    "if_5_2":        (20, 35),
}


# ---------------------------------------------------------------------------
# Produce tracking constants (China region)
# ---------------------------------------------------------------------------

# Cumulative vegetable target (g) by checkpoint. None = no target at this checkpoint.
# Key: (meals_per_day, block_index)
PRODUCE_VEG_TARGETS: dict = {
    (3, 0): None,   # breakfast block — no vegetable requirement
    (3, 1): 150,    # by lunch — cumulative ≥150g
    (3, 2): 300,    # by dinner — cumulative ≥300g
    (2, 0): 150,    # by meal_1 — cumulative ≥150g
    (2, 1): 300,    # by meal_2 — cumulative ≥300g
}

PRODUCE_FRUIT_DAILY_MIN = 200  # g — minimum daily fruit intake
PRODUCE_FRUIT_DAILY_MAX = 350  # g — maximum daily fruit intake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_2meal_blocks(first: str, second: str) -> list:
    """Build 2-meal blocks from standard meal names."""
    return [
        {"label": first,  "pct": 50, "meals": [first, "snack_am"]},
        {"label": second, "pct": 50, "meals": [second, "snack_pm"]},
    ]


def get_meal_blocks(meals: int, schedule: dict = None) -> list:
    if meals == 3:
        return MEAL_BLOCKS_3
    first, second = _get_2meal_names(schedule)
    return _make_2meal_blocks(first, second)


def resolve_meal_name(meal_name: str, meals: int, schedule: dict = None) -> str:
    """Resolve a meal name to standard form.

    For 3-meal mode: identity (breakfast/lunch/dinner pass through).
    For 2-meal mode: legacy names (meal_1/meal_2) → standard names based on
    schedule. Standard names pass through unchanged.
    """
    if meals == 2:
        # Legacy backward compat: meal_1/meal_2 → standard name
        if meal_name in LEGACY_MEAL_ALIAS:
            first, second = _get_2meal_names(schedule)
            legacy_to_standard = {
                "meal_1": first, "meal 1": first,
                "meal_2": second, "meal 2": second,
                "snack_1": "snack_am", "snack_2": "snack_pm",
            }
            return legacy_to_standard.get(meal_name, meal_name)
    return meal_name


def find_block_index(meal_name: str, meals: int, schedule: dict = None) -> int:
    """Find which block a meal type belongs to."""
    resolved = resolve_meal_name(meal_name, meals, schedule)
    for i, block in enumerate(get_meal_blocks(meals, schedule)):
        if resolved in block["meals"]:
            return i
    return None


def _in_range(value: float, lo: float, hi: float) -> bool:
    return lo <= value <= hi


def _range_status(value: float, lo: float, hi: float) -> str:
    if value < lo:
        return "low"
    elif value > hi:
        return "high"
    return "on_track"


def _sum_macros(meal_list: list) -> dict:
    s = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    for m in meal_list:
        s["calories"] += m.get("calories", 0)
        s["protein"] += m.get("protein", 0)
        s["carbs"] += m.get("carbs", 0)
        s["fat"] += m.get("fat", 0)
    return {k: round(v, 1) for k, v in s.items()}


def get_log_path(data_dir: str, day: str = None, tz_offset: int = None) -> str:
    day = day or _local_date(tz_offset)
    return os.path.join(data_dir, f"{day}.json")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def calc_targets(weight: float, daily_cal: int, meals: int = 3,
                 mode: str = "balanced", schedule: dict = None,
                 target_weight: float = None) -> dict:
    # Protein: use target weight if provided, otherwise current weight
    protein_weight = target_weight if target_weight is not None else weight

    # Mode-specific protein multipliers
    if mode == "high_protein":
        p_lo, p_mid, p_hi = 1.4, 1.6, 1.8
    else:
        p_lo, p_mid, p_hi = 1.2, 1.4, 1.6

    protein = round(protein_weight * p_mid, 1)
    protein_lo = round(protein_weight * p_lo, 1)
    protein_hi = round(protein_weight * p_hi, 1)

    fat_lo_pct, fat_hi_pct = DIET_MODE_FAT.get(mode, (25, 35))
    fat_mid_pct = (fat_lo_pct + fat_hi_pct) / 2

    fat = round(daily_cal * fat_mid_pct / 100 / 9, 1)
    fat_lo = round(daily_cal * fat_lo_pct / 100 / 9, 1)
    fat_hi = round(daily_cal * fat_hi_pct / 100 / 9, 1)

    carb = round((daily_cal - protein * 4 - fat * 9) / 4, 1)
    carb_lo = round((daily_cal - protein_hi * 4 - fat_hi * 9) / 4, 1)
    carb_hi = round((daily_cal - protein_lo * 4 - fat_lo * 9) / 4, 1)

    cal_lo = daily_cal - 100
    cal_hi = daily_cal + 100

    blocks = get_meal_blocks(meals, schedule)
    alloc = []
    for b in blocks:
        alloc.append({"meal": b["label"], "pct": b["pct"],
                       "calories": round(daily_cal * b["pct"] / 100)})

    return {
        "daily_calories": daily_cal,
        "calories_range": {"min": cal_lo, "max": cal_hi},
        "weight": weight,
        "meals": meals,
        "protein": {"target": protein, "min": protein_lo, "max": protein_hi},
        "fat": {"target": fat, "min": fat_lo, "max": fat_hi},
        "carb": {"target": carb, "min": carb_lo, "max": carb_hi},
        "allocation": alloc,
    }








# ---------------------------------------------------------------------------
# Portion calibration memory (stored in health-preferences.md)
# ---------------------------------------------------------------------------

_MAX_CALIBRATIONS = 200
_CAL_SECTION_HEADER = "## Portion Calibrations\n"
_CAL_LINE_RE = re.compile(
    r'^- \[(\d{4}-\d{2}-\d{2})\] (.+?) → (\d+)g \(×(\d+)\)$')
# Confusion pairs: AI guessed X but user corrected to Y
_CONFUSION_SECTION_HEADER = "## Correction Aliases\n"
_CONFUSION_LINE_RE = re.compile(
    r'^- (.+?) → (.+?)$')


















# ---------------------------------------------------------------------------
# Oil calibration memory (stored in health-preferences.md)
# ---------------------------------------------------------------------------

_MAX_OIL_CALIBRATIONS = 200
_OIL_CAL_SECTION_HEADER = "## Oil Calibrations\n"
_OIL_CAL_LINE_RE = re.compile(
    r'^- \[(\d{4}-\d{2}-\d{2})\] (.+?) → (\d+)g/100g \(×(\d+)\)$')














def _save_evaluation_to_meal(data_dir: str, day: str, meal_name: str, eval_result: dict):
    """Persist suggestion_type into the saved meal record.

    Only stores suggestion_type from the script layer. The human-readable
    suggestion_text is written later by diet-tracking-analysis via
    save-evaluation command after the LLM composes the response.
    """
    path = get_log_path(data_dir, day)
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        meals = json.load(f)
    for m in meals:
        if m.get("name") == meal_name:
            m["evaluation"] = {
                "suggestion_type": eval_result.get("suggestion_type"),
            }
            break
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meals, f, ensure_ascii=False, indent=2)


def save_evaluation_text(data_dir: str, meal_name: str, suggestion_text: str,
                         day: str = None, tz_offset: int = None) -> dict:
    """Save the LLM-generated suggestion text into a meal's evaluation record.

    Called by diet-tracking-analysis after composing the user-facing response.
    Merges suggestion_text into the existing evaluation dict (which already
    has suggestion_type from _save_evaluation_to_meal).
    """
    day = day or _local_date(tz_offset)
    path = get_log_path(data_dir, day)
    if not os.path.exists(path):
        return {"saved": False, "error": "No meal file for this date"}
    with open(path, "r", encoding="utf-8") as f:
        meals = json.load(f)
    found = False
    for m in meals:
        if m.get("name") == meal_name:
            if "evaluation" not in m:
                m["evaluation"] = {}
            m["evaluation"]["suggestion_text"] = suggestion_text
            found = True
            break
    if not found:
        return {"saved": False, "error": f"Meal '{meal_name}' not found"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meals, f, ensure_ascii=False, indent=2)
    return {"saved": True, "meal": meal_name, "date": day}


def load_meals(data_dir: str, day: str = None, tz_offset: int = None) -> dict:
    """Load all meals for a given day, migrating old format if needed."""
    path = get_log_path(data_dir, day, tz_offset)
    resolved_day = day or _local_date(tz_offset)
    if not os.path.exists(path):
        return {"date": resolved_day, "meals": [], "meals_count": 0}
    with open(path, "r", encoding="utf-8") as f:
        meals = _migrate_meals(json.load(f))
    return {"date": resolved_day, "meals": meals, "meals_count": len(meals)}


def evaluate(weight: float, daily_cal: int, meals: int,
             current_meal: str, log: list,
             assumed_meals: list = None,
             mode: str = "balanced",
             schedule: dict = None,
             target_weight: float = None) -> dict:
    """Evaluate cumulative intake at the checkpoint for *current_meal*.

    Uses range-based evaluation:
    - Each checkpoint scales daily min/max ranges by the checkpoint percentage.
    - Adjustment is needed when: calories outside checkpoint kcal range
      OR 2+ macros outside their checkpoint ranges.
    """
    log = _migrate_meals(log)
    if assumed_meals:
        assumed_meals = _migrate_meals(assumed_meals)

    targets = calc_targets(weight, daily_cal, meals, mode, schedule, target_weight)
    blocks = get_meal_blocks(meals, schedule)

    block_idx = find_block_index(current_meal, meals, schedule)
    if block_idx is None:
        return {"error": f"Unknown meal name: {current_meal}"}

    checkpoint_pct = sum(blocks[i]["pct"] for i in range(block_idx + 1))

    checkpoint_meal_names: set[str] = set()
    for i in range(block_idx + 1):
        checkpoint_meal_names.update(blocks[i]["meals"])

    logged_names = {resolve_meal_name(m.get("name", ""), meals, schedule) for m in log}

    checkpoint_log = [m for m in log
                      if resolve_meal_name(m.get("name", ""), meals, schedule) in checkpoint_meal_names]

    missing_meals: list = []
    for i in range(block_idx + 1):
        main_meal = blocks[i]["meals"][0]
        if main_meal not in logged_names:
            missing_meals.append(main_meal)

    actual = _sum_macros(checkpoint_log)

    cp_target = {
        "calories": round(daily_cal * checkpoint_pct / 100),
        "protein": round(targets["protein"]["target"] * checkpoint_pct / 100, 1),
        "carbs": round(targets["carb"]["target"] * checkpoint_pct / 100, 1),
        "fat": round(targets["fat"]["target"] * checkpoint_pct / 100, 1),
    }

    cp_range = {
        "calories_min": round(targets["calories_range"]["min"] * checkpoint_pct / 100),
        "calories_max": round(targets["calories_range"]["max"] * checkpoint_pct / 100),
        "protein_min": round(targets["protein"]["min"] * checkpoint_pct / 100, 1),
        "protein_max": round(targets["protein"]["max"] * checkpoint_pct / 100, 1),
        "carbs_min": round(targets["carb"]["min"] * checkpoint_pct / 100, 1),
        "carbs_max": round(targets["carb"]["max"] * checkpoint_pct / 100, 1),
        "fat_min": round(targets["fat"]["min"] * checkpoint_pct / 100, 1),
        "fat_max": round(targets["fat"]["max"] * checkpoint_pct / 100, 1),
    }

    adjusted = dict(actual)
    if assumed_meals:
        for m in assumed_meals:
            if resolve_meal_name(m.get("name", ""), meals, schedule) in checkpoint_meal_names:
                adjusted["calories"] = round(adjusted["calories"] + m.get("calories", 0), 1)
                adjusted["protein"] = round(adjusted["protein"] + m.get("protein", 0), 1)
                adjusted["carbs"] = round(adjusted["carbs"] + m.get("carbs", 0), 1)
                adjusted["fat"] = round(adjusted["fat"] + m.get("fat", 0), 1)

    status = {
        "calories": _range_status(actual["calories"], cp_range["calories_min"], cp_range["calories_max"]),
        "protein": _range_status(actual["protein"], cp_range["protein_min"], cp_range["protein_max"]),
        "carbs": _range_status(actual["carbs"], cp_range["carbs_min"], cp_range["carbs_max"]),
        "fat": _range_status(actual["fat"], cp_range["fat_min"], cp_range["fat_max"]),
    }

    cal_outside = not _in_range(actual["calories"], cp_range["calories_min"], cp_range["calories_max"])
    macros_outside = sum(1 for k in ["protein", "carbs", "fat"] if status[k] != "on_track")
    needs_adjustment = cal_outside or macros_outside >= 2

    # Calories within range but macros imbalanced → defer to next-day optimization
    cal_in_range_macro_off = (not cal_outside) and macros_outside >= 1

    suggestion_base = adjusted if assumed_meals else actual
    diff = {
        "calories": round(cp_target["calories"] - suggestion_base["calories"], 1),
        "protein": round(cp_target["protein"] - suggestion_base["protein"], 1),
        "carbs": round(cp_target["carbs"] - suggestion_base["carbs"], 1),
        "fat": round(cp_target["fat"] - suggestion_base["fat"], 1),
    }

    return {
        "current_meal": current_meal,
        "checkpoint_pct": checkpoint_pct,
        "checkpoint_target": cp_target,
        "checkpoint_range": cp_range,
        "actual": actual,
        "adjusted": adjusted if assumed_meals else None,
        "status": status,
        "needs_adjustment": needs_adjustment,
        "cal_in_range_macro_off": cal_in_range_macro_off,
        "diff_for_suggestions": diff,
        "missing_meals": missing_meals,
        "meals_included": [m.get("name") for m in checkpoint_log],
        "resolved_meal": resolve_meal_name(current_meal, meals, schedule),
    }




def produce_check(meals: int, current_meal: str, log: list,
                  schedule: dict = None) -> dict:
    """Evaluate cumulative vegetable and fruit intake at the current checkpoint.

    Vegetables: cumulative target based on checkpoint (None = no target at that point).
    Fruits: checked only at the final meal of the day (200–350 g daily total).

    Meal JSON records may include optional fields:
      - vegetables_g: grams of vegetables in this meal (cooked weight)
      - fruits_g: grams of fruit in this meal
    Missing fields default to 0.
    """
    log = _migrate_meals(log)
    blocks = get_meal_blocks(meals, schedule)
    block_idx = find_block_index(current_meal, meals, schedule)
    if block_idx is None:
        return {"error": f"Unknown meal name: {current_meal}"}

    checkpoint_meal_names: set[str] = set()
    for i in range(block_idx + 1):
        checkpoint_meal_names.update(blocks[i]["meals"])

    checkpoint_log = [
        m for m in log
        if resolve_meal_name(m.get("name", ""), meals, schedule) in checkpoint_meal_names
    ]

    veg_total = round(sum((m.get("vegetables_g") or 0) for m in checkpoint_log), 1)
    fruit_total = round(sum((m.get("fruits_g") or 0) for m in checkpoint_log), 1)

    is_final = block_idx == len(blocks) - 1

    veg_target = PRODUCE_VEG_TARGETS.get((meals, block_idx))
    has_veg_target = veg_target is not None

    veg_status: str | None = None
    if has_veg_target:
        veg_status = "on_track" if veg_total >= veg_target else "low"

    fruit_status: str | None = None
    if is_final:
        if fruit_total < PRODUCE_FRUIT_DAILY_MIN:
            fruit_status = "low"
        elif fruit_total > PRODUCE_FRUIT_DAILY_MAX:
            fruit_status = "high"
        else:
            fruit_status = "on_track"

    return {
        "current_meal": current_meal,
        "is_final_meal": is_final,
        "vegetables_actual_g": veg_total,
        "vegetables_target_g": veg_target,
        "has_vegetable_target": has_veg_target,
        "vegetable_status": veg_status,
        "fruits_actual_g": fruit_total,
        "fruits_daily_min_g": PRODUCE_FRUIT_DAILY_MIN if is_final else None,
        "fruits_daily_max_g": PRODUCE_FRUIT_DAILY_MAX if is_final else None,
        "fruit_status": fruit_status,
    }




def recent_overshoot_check(data_dir: str, daily_cal: int,
                           lookback_days: int = 7,
                           ref_date: str = None,
                           tz_offset: int = None) -> dict:
    """Check how many of the recent days had calorie overshoot.

    Counts ALL overshoot days in the lookback window (excluding today),
    regardless of whether they are consecutive.
    """
    end = date.fromisoformat(ref_date) if ref_date else date.fromisoformat(_local_date(tz_offset))
    cal_hi = daily_cal + 100  # same range as calc_targets

    overshoot_days: list[str] = []
    for offset in range(1, lookback_days + 1):  # start at 1 to exclude today
        day = (end - timedelta(days=offset)).isoformat()
        path = get_log_path(data_dir, day)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            meals = _migrate_meals(json.load(f))
        day_cal = round(sum(m.get("calories", 0) for m in meals), 1)
        if day_cal > cal_hi:
            overshoot_days.append(day)

    return {
        "lookback_days": lookback_days,
        "overshoot_days": overshoot_days,
        "overshoot_count": len(overshoot_days),
    }


# ---------------------------------------------------------------------------
# Diet pattern detection
# ---------------------------------------------------------------------------











# ---------------------------------------------------------------------------
# Meal history & recommendations
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# Meal type detection from timestamp + schedule
# ---------------------------------------------------------------------------

# Default time windows (3-meal mode) — used when no custom schedule provided.
# Format: (start_hour, end_hour, meal_name)
# Hours are in local time. Windows that cross midnight use end > 24.
DEFAULT_WINDOWS_3 = [
    (5,  10, "breakfast"),
    (10, 11, "snack_am"),
    (11, 14, "lunch"),
    (14, 17, "snack_pm"),
    (17, 21, "dinner"),
    (21, 29, "snack_pm"),   # 21:00 – 05:00 next day (29 = 24+5)
]

DEFAULT_WINDOWS_2 = [
    (5,  10, "breakfast"),
    (10, 11, "snack_am"),
    (11, 14, "lunch"),
    (14, 17, "snack_pm"),
    (17, 21, "dinner"),
    (21, 29, "snack_pm"),
]


def _parse_hhmm(s: str) -> float:
    """Parse 'HH:MM' to fractional hours (e.g. '09:30' → 9.5)."""
    parts = s.strip().split(":")
    return int(parts[0]) + int(parts[1]) / 60.0




# Snack offset: if current time is more than this many hours AFTER the main
# meal time AND that meal is already logged, classify as snack instead.
_SNACK_OFFSET_HOURS = 1.5

# Map main meal → snack name (3-meal mode)
_SNACK_MAP_3 = {
    "breakfast": "snack_am",
    "lunch": "snack_pm",
    # dinner has no snack after it in standard mode
}

_SNACK_MAP_2 = {
    # For 2-meal, first meal → snack_am; second meal has no snack after it.
    # Dynamic: detect_meal builds this from schedule at runtime.
    "breakfast": "snack_am",
    "lunch": "snack_am",
    # dinner has no snack after it (last meal)
}


# ---------------------------------------------------------------------------
# Local date utility
# ---------------------------------------------------------------------------

def local_date_info(tz_offset: int) -> dict:
    """Return local date info: today, weekday, and current week's Mon-Sun range.

    Useful for any skill that needs the user's local date without relying
    on the LLM to compute it.
    """
    utc_now = datetime.now(timezone.utc)
    local_dt = utc_now + timedelta(seconds=tz_offset)
    today = local_dt.date()
    weekday = today.isoweekday()  # Mon=1, Sun=7
    monday = today - timedelta(days=weekday - 1)
    sunday = monday + timedelta(days=6)
    prev_monday = monday - timedelta(days=7)
    prev_sunday = monday - timedelta(days=1)

    return {
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "weekday_num": weekday,
        "local_time": local_dt.strftime("%H:%M:%S"),
        "current_week": {
            "monday": monday.isoformat(),
            "sunday": sunday.isoformat(),
        },
        "previous_week": {
            "monday": prev_monday.isoformat(),
            "sunday": prev_sunday.isoformat(),
        },
    }




# ---------------------------------------------------------------------------
# Composite commands (CRUD)
# ---------------------------------------------------------------------------








def query_day(data_dir: str, tz_offset: int, weight: float,
              daily_cal: int, meals: int, day: str = None,
              mode: str = "balanced", region: str = None,
              schedule: dict = None, target_weight: float = None) -> dict:
    """Load a day's records and evaluate current status.

    Args:
        data_dir: Directory with daily JSON logs.
        tz_offset: Timezone offset from UTC in seconds.
        weight: Body weight in kg.
        daily_cal: Daily calorie target (kcal).
        meals: Meals per day (2 or 3).
        day: Date override (YYYY-MM-DD). Defaults to today (local).
        mode: Diet mode for evaluate.
        region: Region code for produce-check.

    Returns: dict with date, meals, evaluation, and produce.
    """
    loaded = load_meals(data_dir, day, tz_offset)
    all_meals = loaded.get("meals", [])
    resolved_day = loaded.get("date", day or _local_date(tz_offset))

    result = {
        "date": resolved_day,
        "meals": all_meals,
        "meals_count": len(all_meals),
    }

    if not all_meals:
        result["evaluation"] = None
        result["produce"] = None
        return result

    # Determine the latest logged meal as checkpoint
    blocks = get_meal_blocks(meals, schedule)
    latest_block_idx = -1
    for m in all_meals:
        idx = find_block_index(m.get("name", ""), meals, schedule)
        if idx is not None and idx > latest_block_idx:
            latest_block_idx = idx
            latest_meal = m.get("name", "")

    if latest_block_idx < 0:
        latest_meal = all_meals[-1].get("name", "breakfast")

    result["evaluation"] = evaluate(weight, daily_cal, meals,
                                    latest_meal, all_meals, None, mode, schedule, target_weight)

    # Add overshoot history (same as log-meal does)
    if result["evaluation"]:
        overshoot_history = recent_overshoot_check(
            data_dir, daily_cal, lookback_days=7,
            ref_date=resolved_day, tz_offset=tz_offset)
        result["evaluation"]["recent_overshoot_count"] = overshoot_history["overshoot_count"]
        result["evaluation"]["recent_overshoot_days"] = overshoot_history["overshoot_days"]

    if region and region.upper() == "CN":
        result["produce"] = produce_check(meals, latest_meal, all_meals, schedule)
    else:
        result["produce"] = None

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------



def main():
    parser = argparse.ArgumentParser(description="Nutrition calculator (slim)")
    sub = parser.add_subparsers(dest="cmd")

    # load
    l = sub.add_parser("load", help="Load meal records for a date")
    l.add_argument("--data-dir", required=True)
    l.add_argument("--date", default=None)
    l.add_argument("--tz-offset", type=int, default=None)

    # local-date
    ld = sub.add_parser("local-date", help="Get local date info")
    ld.add_argument("--tz-offset", type=int, required=True)

    # query-day
    qd = sub.add_parser("query-day", help="Query daily progress summary")
    qd.add_argument("--data-dir", required=True)
    qd.add_argument("--tz-offset", type=int, required=True)
    qd.add_argument("--weight", type=float, required=True)
    qd.add_argument("--cal", type=int, required=True)
    qd.add_argument("--meals", type=int, required=True)
    qd.add_argument("--date", default=None)
    qd.add_argument("--region", default=None)
    qd.add_argument("--mode", default="balanced")
    qd.add_argument("--bmr", type=float, default=None)
    qd.add_argument("--schedule", default=None)

    # save-evaluation
    se = sub.add_parser("save-evaluation", help="Save suggestion text to meal record")
    se.add_argument("--data-dir", required=True)
    se.add_argument("--meal-name", required=True)
    se.add_argument("--suggestion-text", required=True)
    se.add_argument("--tz-offset", type=int, required=True)
    se.add_argument("--date", default=None)



    args = parser.parse_args()
    if hasattr(args, 'data_dir') and args.data_dir:
        args.data_dir = _normalize_path(args.data_dir)

    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    if args.cmd == "load":
        data_dir = _normalize_path(args.data_dir)
        day = args.date or _local_date(args.tz_offset)
        meals = load_meals(data_dir, day)
        print(json.dumps(meals, ensure_ascii=False, indent=2))

    elif args.cmd == "local-date":
        result = local_date_info(args.tz_offset)
        print(json.dumps(result, ensure_ascii=False))

    elif args.cmd == "query-day":
        data_dir = _normalize_path(args.data_dir)
        schedule = json.loads(args.schedule) if args.schedule else None
        result = query_day(
            data_dir=data_dir,
            tz_offset=args.tz_offset,
            weight=args.weight,
            daily_cal=args.cal,
            meals=args.meals,
            day=args.date,
            region=args.region,
            mode=args.mode,
            schedule=schedule,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.cmd == "save-evaluation":
        data_dir = _normalize_path(args.data_dir)
        day = args.date or _local_date(args.tz_offset)
        save_evaluation_text(data_dir, day, args.meal_name, args.suggestion_text)
        print(json.dumps({"status": "ok", "meal_name": args.meal_name, "date": day}))


        data_dir = _normalize_path(args.data_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
