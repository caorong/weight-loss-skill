#!/usr/bin/env python3
"""Detect if user's actual eating pattern matches a different diet mode.

Standalone script extracted from diet-tracking-analysis/nutrition-calc.py.
Used by diet-pattern-detection skill only.

Usage:
  python3 detect-pattern.py --data-dir <meals_dir> --current-mode <mode> [--date YYYY-MM-DD] [--tz-offset <seconds>]

Output: JSON to stdout.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Import locale helpers from shared (parent directory)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'shared'))
from locale_helpers import is_china_user


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIET_MODE_MACROS = {
    "usda":          {"carbs": (45, 65), "fat": (20, 35)},
    "balanced":      {"carbs": (35, 45), "fat": (20, 35)},
    "high_protein":  {"carbs": (25, 35), "fat": (20, 35)},
    "low_carb":      {"carbs": (15, 25), "fat": (40, 50)},
    "keto":          {"carbs": (5, 10),  "fat": (65, 75)},
    "mediterranean": {"carbs": (40, 50), "fat": (20, 35)},
    "plant_based":   {"carbs": (45, 55), "fat": (20, 30)},
    "if_16_8":       {"carbs": (35, 45), "fat": (20, 35)},
    "if_5_2":        {"carbs": (35, 45), "fat": (20, 35)},
}

# Protein range per mode (g per kg target body weight) — matches nutrition-calc.py
PROTEIN_RANGE = {
    "high_protein": (1.4, 1.8),
    "balanced": (1.2, 1.6),
    "plant_based": (1.2, 1.6),
    "usda": (1.2, 1.6),
    "mediterranean": (1.2, 1.6),
    "low_carb": (1.2, 1.6),
    "keto": (1.2, 1.6),
    "if_16_8": (1.2, 1.6),
    "if_5_2": (1.2, 1.6),
}

_SHORT_TO_LONG = {
    "cal": "calories", "p": "protein", "c": "carbs", "f": "fat",
    "protein_g": "protein", "carbs_g": "carbs", "fat_g": "fat",
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _normalize_path(p):
    """Lowercase wechat-dm/wecom-dm segment to avoid case-mismatch directories."""
    return re.sub(r'(workspace-(?:wechat|wecom)-dm-)([^/]+)',
                  lambda m: m.group(1) + m.group(2).lower(), p)


def _local_date(tz_offset: int = None) -> str:
    if tz_offset is not None:
        utc_now = datetime.now(timezone.utc)
        local_dt = utc_now + timedelta(seconds=tz_offset)
        return local_dt.date().isoformat()
    return date.today().isoformat()


def _get_log_path(data_dir: str, day: str) -> str:
    return os.path.join(data_dir, f"{day}.json")


def _migrate_meal(meal: dict) -> dict:
    out = {}
    for k, v in meal.items():
        new_key = _SHORT_TO_LONG.get(k, k)
        if new_key in out:
            continue
        if k == "foods" and isinstance(v, list):
            out[k] = [_migrate_meal(f) for f in v]
        else:
            out[new_key] = v
    if "items" in out and "foods" not in out:
        out["foods"] = [_migrate_meal(f) for f in out.pop("items")]
    if "foods" in out and "calories" not in out:
        for key in ("calories", "protein", "carbs", "fat"):
            out[key] = round(sum(f.get(key, 0) for f in out["foods"]), 1)
    return out


def _migrate_meals(meals: list) -> list:
    return [_migrate_meal(m) for m in meals]


def _get_expected_meals(workspace: Path) -> int:
    """Read expected meals per day from health-profile.md. Default 3."""
    hp = workspace / "health-profile.md"
    if not hp.exists():
        return 3
    try:
        text = hp.read_text(encoding="utf-8")
        m = re.search(r"Meals per Day:\*?\*?\s*(\d+)", text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 3


def _is_complete_day(meals: list, expected_meals: int) -> bool:
    """A day is complete if distinct non-snack eaten meals >= expected_meals."""
    meals = _migrate_meals(meals)
    non_snack = set()
    for meal in meals:
        name = meal.get("meal_name", "").lower()
        if name == "snack":
            continue
        if meal.get("eaten", True):
            non_snack.add(name)
    return len(non_snack) >= expected_meals


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _calc_macro_pcts(meals: list):
    meals = _migrate_meals(meals)
    total_cal = sum(m.get("calories", 0) for m in meals)
    total_p = sum(m.get("protein", 0) for m in meals)
    total_c = sum(m.get("carbs", 0) for m in meals)
    total_f = sum(m.get("fat", 0) for m in meals)
    if total_cal < 500:
        return None
    return {
        "calories": round(total_cal, 1),
        "protein_g": round(total_p, 1),
        "protein_pct": round(total_p * 4 / total_cal * 100, 1),
        "carbs_pct": round(total_c * 4 / total_cal * 100, 1),
        "fat_pct": round(total_f * 9 / total_cal * 100, 1),
    }


def _mode_distance(p_pct: float, c_pct: float, f_pct: float, mode: str,
                    protein_g: float = None, target_weight: float = None) -> float:
    ranges = DIET_MODE_MACROS.get(mode)
    if not ranges:
        return float("inf")
    dist = 0.0
    # Protein: absolute gram range matching via target_weight × (lo, hi)
    if protein_g is not None and target_weight is not None:
        p_lo, p_hi = PROTEIN_RANGE.get(mode, (1.2, 1.6))
        target_lo = target_weight * p_lo
        target_hi = target_weight * p_hi
        if protein_g < target_lo:
            dist += (target_lo - protein_g) / target_lo * 100
        elif protein_g > target_hi:
            dist += (protein_g - target_hi) / target_hi * 100
        # else: within range, distance contribution = 0
    # Carbs & fat: percentage range matching
    for actual, key in [(c_pct, "carbs"), (f_pct, "fat")]:
        lo, hi = ranges[key]
        if actual < lo:
            dist += lo - actual
        elif actual > hi:
            dist += actual - hi
    return dist


def _get_pros_cons(current_mode: str, detected_mode: str) -> dict:
    mode_info = {
        "balanced": {
            "name": "Balanced / Flexible",
            "pros": [
                "No food restrictions — highest flexibility and adherence",
                "Easy to maintain long-term",
                "Well-suited for beginners",
            ],
            "cons": [
                "Less targeted than specialized modes",
                "Requires tracking to stay on course",
            ],
        },
        "high_protein": {
            "name": "High-Protein",
            "pros": [
                "Better muscle preservation during calorie deficit",
                "Higher satiety — feel fuller longer",
                "Increased thermic effect of food",
            ],
            "cons": [
                "Can feel monotonous — requires rotating protein sources",
                "May be harder to hit protein targets consistently",
                "Higher food cost (protein sources tend to be pricier)",
            ],
        },
        "low_carb": {
            "name": "Low-Carb",
            "pros": [
                "Reduced hunger and more stable energy for many people",
                "Lower insulin response",
                "Can reduce bloating",
            ],
            "cons": [
                "Fiber intake may drop — need to eat plenty of vegetables",
                "Can feel restrictive for carb lovers",
                "May reduce exercise performance initially",
            ],
        },
        "keto": {
            "name": "Keto",
            "pros": [
                "Strong appetite suppression after adaptation",
                "High fat intake increases meal satisfaction",
            ],
            "cons": [
                "Extremely restrictive — hard to sustain socially",
                "Keto flu during adaptation (1-2 weeks)",
                "Risk of nutrient deficiencies without careful planning",
                "Not recommended below 1,800 kcal/day",
            ],
        },
        "mediterranean": {
            "name": "Mediterranean",
            "pros": [
                "Strong evidence for cardiovascular health",
                "Feels like eating well rather than dieting",
                "Rich in healthy fats and whole foods",
            ],
            "cons": [
                "Olive oil and nuts are calorie-dense — portions need care",
                "May require more cooking and meal prep",
            ],
        },
        "plant_based": {
            "name": "Plant-Based",
            "pros": [
                "High fiber naturally increases satiety",
                "Associated with lower heart disease risk",
                "Often lower calorie density",
            ],
            "cons": [
                "Hitting protein targets is harder without animal products",
                "Requires more intentional meal planning",
                "May need B12 and other supplements",
            ],
        },
        "usda": {
            "name": "Healthy U.S.-Style (USDA)",
            "pros": [
                "Government-backed, evidence-based guidelines",
                "No food groups excluded — very flexible",
                "Good baseline for general health",
            ],
            "cons": [
                "Broad ranges may feel too vague for specific goals",
                "Less targeted for weight loss than specialized modes",
            ],
        },
    }
    detected_info = mode_info.get(detected_mode, {})
    current_info = mode_info.get(current_mode, {})
    return {
        "switch_to": detected_mode,
        "switch_to_name": detected_info.get("name", detected_mode),
        "switch_from": current_mode,
        "switch_from_name": current_info.get("name", current_mode),
        "pros": detected_info.get("pros", []),
        "cons": detected_info.get("cons", []),
    }


def detect_diet_pattern(data_dir: str, current_mode: str,
                        weight: float = None, target_weight: float = None,
                        ref_date: str = None, tz_offset: int = None) -> dict:
    end = date.fromisoformat(ref_date) if ref_date else date.fromisoformat(_local_date(tz_offset))

    # Derive workspace path from data_dir (workspace/data/meals -> workspace)
    workspace = Path(data_dir).parent.parent
    is_china = is_china_user(workspace)
    expected_meals = _get_expected_meals(workspace)

    daily_splits: list[dict] = []
    for offset in range(7):
        day = (end - timedelta(days=offset)).isoformat()
        path = _get_log_path(data_dir, day)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            meals = json.load(f)
        if not _is_complete_day(meals, expected_meals):
            continue
        pcts = _calc_macro_pcts(meals)
        if pcts is not None:
            daily_splits.append({"date": day, **pcts})
        if len(daily_splits) >= 3:
            break

    if len(daily_splits) < 3:
        return {
            "has_pattern": False,
            "reason": "insufficient_data",
            "days_found": len(daily_splits),
            "daily_splits": sorted(daily_splits, key=lambda d: d["date"]),
        }

    avg_p = round(sum(d["protein_pct"] for d in daily_splits) / 3, 1)
    avg_c = round(sum(d["carbs_pct"] for d in daily_splits) / 3, 1)
    avg_f = round(sum(d["fat_pct"] for d in daily_splits) / 3, 1)
    avg_protein_g = round(sum(d["protein_g"] for d in daily_splits) / 3, 1)
    avg_split = {"protein_pct": avg_p, "carbs_pct": avg_c, "fat_pct": avg_f, "protein_g": avg_protein_g}

    effective_current = current_mode
    if current_mode in ("if_16_8", "if_5_2"):
        effective_current = "balanced"

    current_dist = _mode_distance(avg_p, avg_c, avg_f, effective_current,
                                  protein_g=avg_protein_g, target_weight=target_weight)

    # Only consider balanced and high_protein as switch targets
    candidate_modes = ["balanced", "high_protein"]

    best_mode = None
    best_dist = float("inf")
    for mode in candidate_modes:
        dist = _mode_distance(avg_p, avg_c, avg_f, mode,
                              protein_g=avg_protein_g, target_weight=target_weight)
        if dist < best_dist:
            best_dist = dist
            best_mode = mode

    all_days_match = all(
        _mode_distance(d["protein_pct"], d["carbs_pct"], d["fat_pct"], best_mode,
                       protein_g=d["protein_g"], target_weight=target_weight) <=
        _mode_distance(d["protein_pct"], d["carbs_pct"], d["fat_pct"], effective_current,
                       protein_g=d["protein_g"], target_weight=target_weight)
        for d in daily_splits
    )

    mismatch = (best_mode != effective_current
                and best_dist < current_dist
                and all_days_match)

    pros_cons = _get_pros_cons(effective_current, best_mode) if mismatch else None

    result = {
        "has_pattern": mismatch,
        "current_mode": current_mode,
        "effective_current_mode": effective_current,
        "detected_mode": best_mode if mismatch else None,
        "current_mode_distance": round(current_dist, 1),
        "detected_mode_distance": round(best_dist, 1),
        "avg_split": avg_split,
        "daily_splits": sorted(daily_splits, key=lambda d: d["date"]),
        "days_found": len(daily_splits),
        "all_days_consistent": all_days_match,
        "pros_cons": pros_cons,
    }
    if mismatch:
        result["effective_current_mode_range"] = {k: list(v) for k, v in DIET_MODE_MACROS[effective_current].items()}
        result["detected_mode_range"] = {k: list(v) for k, v in DIET_MODE_MACROS[best_mode].items()}
        # Add protein gram ranges (based on target_weight × multiplier range)
        if target_weight:
            cur_p = PROTEIN_RANGE.get(effective_current, (1.2, 1.6))
            det_p = PROTEIN_RANGE.get(best_mode, (1.2, 1.6))
            result["effective_current_mode_range"]["protein_g"] = [round(target_weight * cur_p[0]), round(target_weight * cur_p[1])]
            result["detected_mode_range"]["protein_g"] = [round(target_weight * det_p[0]), round(target_weight * det_p[1])]
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Detect diet pattern from meal logs")
    parser.add_argument("--data-dir", required=True, help="Path to meals data directory")
    parser.add_argument("--current-mode", required=True, help="Current diet mode from health-profile.md")
    parser.add_argument("--weight", type=float, required=True, help="Current weight in kg")
    parser.add_argument("--target-weight", type=float, required=True, help="Target weight in kg")
    parser.add_argument("--date", default=None, help="Reference date (YYYY-MM-DD), default today")
    parser.add_argument("--tz-offset", type=int, default=None, help="Timezone offset in seconds from UTC")
    args = parser.parse_args()

    result = detect_diet_pattern(
        data_dir=_normalize_path(args.data_dir),
        current_mode=args.current_mode,
        weight=args.weight,
        target_weight=args.target_weight,
        ref_date=args.date,
        tz_offset=args.tz_offset,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
