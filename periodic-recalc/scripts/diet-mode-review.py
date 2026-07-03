#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Diet mode review script.

Analyzes the user's actual eating patterns over the last N days and compares
with their current diet_mode's expected macro ranges. Recommends a mode change
if their actual pattern consistently falls outside the current mode's ranges.

Usage:
  python3 diet-mode-review.py --workspace /path/to/workspace --target-weight 55 --days 28
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Import locale helpers from shared (two levels up from scripts/)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'shared'))
from locale_helpers import is_china_user


# Diet mode macro ranges (carbs% / fat% only — protein uses g/kg)
DIET_MODE_RANGES = {
    'usda': {
        'carbs': (45, 65),
        'fat': (20, 35),
    },
    'balanced': {
        'carbs': (35, 45),
        'fat': (20, 35),
    },
    'high_protein': {
        'carbs': (25, 35),
        'fat': (20, 35),
    },
    'low_carb': {
        'carbs': (15, 25),
        'fat': (40, 50),
    },
    'keto': {
        'carbs': (5, 10),
        'fat': (65, 75),
    },
    'mediterranean': {
        'carbs': (40, 50),
        'fat': (20, 35),
    },
    'plant_based': {
        'carbs': (45, 55),
        'fat': (20, 30),
    },
    'if_16_8': {  # defaults to balanced
        'carbs': (35, 45),
        'fat': (20, 35),
    },
    'if_5_2': {  # defaults to balanced
        'carbs': (35, 45),
        'fat': (20, 35),
    },
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


def read_json(path: Path) -> dict | list:
    """Read and parse JSON file."""
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_current_diet_mode(workspace: Path) -> str:
    """Extract current diet_mode from PLAN.md or health-profile.md."""
    plan_md = workspace / 'PLAN.md'
    health_profile = workspace / 'health-profile.md'

    for path in [plan_md, health_profile]:
        if path.exists():
            content = path.read_text(encoding='utf-8')
            match = re.search(r'\*\*Diet Mode:\*\*\s*(\w+)', content, re.IGNORECASE)
            if match:
                return match.group(1)

    return 'balanced'  # default


def collect_meal_data(workspace: Path, days: int) -> list[dict]:
    """
    Collect meal data for the last N days.
    Returns list of dicts with {date, calories, protein_g, carbs_g, fat_g}.
    """
    meals_dir = workspace / 'data' / 'meals'
    if not meals_dir.exists():
        return []

    today = date.today()
    meal_data = []

    for day_offset in range(days):
        day = today - timedelta(days=day_offset)
        meal_file = meals_dir / f'{day.isoformat()}.json'

        if not meal_file.exists():
            continue

        meals = read_json(meal_file)
        if not meals:
            continue

        # Sum up all meals for the day
        daily_total = {
            'date': day.isoformat(),
            'calories': 0,
            'protein_g': 0,
            'carbs_g': 0,
            'fat_g': 0,
        }

        for meal in meals:
            # Handle both 'protein' and 'protein_g' field names
            daily_total['calories'] += meal.get('calories', 0)
            daily_total['protein_g'] += meal.get('protein', meal.get('protein_g', 0))
            daily_total['carbs_g'] += meal.get('carbs', meal.get('carbs_g', 0))
            daily_total['fat_g'] += meal.get('fat', meal.get('fat_g', 0))

        # Only include days with meaningful data (>200 kcal)
        if daily_total['calories'] >= 200:
            meal_data.append(daily_total)

    return meal_data


def calculate_macro_percentages(meal_data: list[dict]) -> dict | None:
    """
    Calculate average macro percentages and absolute protein grams across all days.
    Returns {protein_pct, carbs_pct, fat_pct, protein_g} or None if insufficient data.
    """
    if len(meal_data) < 7:
        return None

    total_protein_cal = 0
    total_carbs_cal = 0
    total_fat_cal = 0
    total_calories = 0
    total_protein_g = 0

    for day in meal_data:
        protein_cal = day['protein_g'] * 4
        carbs_cal = day['carbs_g'] * 4
        fat_cal = day['fat_g'] * 9

        total_protein_cal += protein_cal
        total_carbs_cal += carbs_cal
        total_fat_cal += fat_cal
        total_calories += day['calories']
        total_protein_g += day['protein_g']

    if total_calories == 0:
        return None

    return {
        'protein_pct': round((total_protein_cal / total_calories) * 100, 1),
        'carbs_pct': round((total_carbs_cal / total_calories) * 100, 1),
        'fat_pct': round((total_fat_cal / total_calories) * 100, 1),
        'protein_g': round(total_protein_g / len(meal_data), 1),
    }


def is_within_range(value: float, range_tuple: tuple[float, float]) -> bool:
    """Check if value is within range (inclusive)."""
    return range_tuple[0] <= value <= range_tuple[1]


def find_best_matching_mode(actual_macros: dict, current_mode: str, workspace: Path, target_weight: float) -> tuple[str, str] | None:
    """
    Find the diet mode that best matches the actual macro percentages.
    Returns (mode_name, reason) or None if current mode is still the best fit.
    """
    current_ranges = DIET_MODE_RANGES.get(current_mode)
    if not current_ranges:
        return None

    # Check if actual macros fit current mode (carbs + fat by %, protein by g/kg)
    fits_carbs_fat = (
        is_within_range(actual_macros['carbs_pct'], current_ranges['carbs']) and
        is_within_range(actual_macros['fat_pct'], current_ranges['fat'])
    )

    # Protein check via absolute gram range
    p_lo, p_hi = PROTEIN_RANGE.get(current_mode, (1.2, 1.6))
    target_lo = target_weight * p_lo
    target_hi = target_weight * p_hi
    actual_protein_g = actual_macros.get('protein_g', 0)
    protein_fits = target_lo <= actual_protein_g <= target_hi

    fits_current = fits_carbs_fat and protein_fits

    if fits_current:
        return None  # Current mode is fine

    # China region gate: exclude Mediterranean and USDA for China users
    is_china = is_china_user(workspace)

    # Find best alternative mode (closest match)
    best_match = None
    best_score = float('inf')

    for mode_name, ranges in DIET_MODE_RANGES.items():
        if mode_name == current_mode:
            continue
        # Skip Mediterranean and USDA for China users
        if is_china and mode_name in ('mediterranean', 'usda'):
            continue

        c_mid = (ranges['carbs'][0] + ranges['carbs'][1]) / 2
        f_mid = (ranges['fat'][0] + ranges['fat'][1]) / 2

        # Protein distance via gram range
        p_lo, p_hi = PROTEIN_RANGE.get(mode_name, (1.2, 1.6))
        mode_target_lo = target_weight * p_lo
        mode_target_hi = target_weight * p_hi
        if actual_protein_g < mode_target_lo:
            protein_distance = (mode_target_lo - actual_protein_g) / mode_target_lo
        elif actual_protein_g > mode_target_hi:
            protein_distance = (actual_protein_g - mode_target_hi) / mode_target_hi
        else:
            protein_distance = 0

        distance = (
            protein_distance * 100 +
            abs(actual_macros['carbs_pct'] - c_mid) +
            abs(actual_macros['fat_pct'] - f_mid)
        )

        if distance < best_score:
            best_score = distance
            best_match = mode_name

    if not best_match:
        return None

    # Generate reason
    reasons = []

    if not protein_fits:
        if actual_protein_g > target_hi:
            reasons.append(f"protein ({actual_protein_g:.0f}g/day) exceeds {current_mode} range ({target_lo:.0f}-{target_hi:.0f}g/day)")
        else:
            reasons.append(f"protein ({actual_protein_g:.0f}g/day) below {current_mode} range ({target_lo:.0f}-{target_hi:.0f}g/day)")

    if not is_within_range(actual_macros['carbs_pct'], current_ranges['carbs']):
        if actual_macros['carbs_pct'] > current_ranges['carbs'][1]:
            reasons.append(f"carbs ({actual_macros['carbs_pct']:.0f}%) exceed {current_mode} range ({current_ranges['carbs'][0]}-{current_ranges['carbs'][1]}%)")
        else:
            reasons.append(f"carbs ({actual_macros['carbs_pct']:.0f}%) below {current_mode} range ({current_ranges['carbs'][0]}-{current_ranges['carbs'][1]}%)")

    if not is_within_range(actual_macros['fat_pct'], current_ranges['fat']):
        if actual_macros['fat_pct'] > current_ranges['fat'][1]:
            reasons.append(f"fat ({actual_macros['fat_pct']:.0f}%) exceeds {current_mode} range ({current_ranges['fat'][0]}-{current_ranges['fat'][1]}%)")
        else:
            reasons.append(f"fat ({actual_macros['fat_pct']:.0f}%) below {current_mode} range ({current_ranges['fat'][0]}-{current_ranges['fat'][1]}%)")

    reason = "Your actual " + ", ".join(reasons) + f". This matches {best_match} mode better."

    return best_match, reason


def main():
    parser = argparse.ArgumentParser(description='Review diet mode against actual eating patterns')
    parser.add_argument('--workspace', type=Path, required=True,
                        help='Path to user workspace directory')
    parser.add_argument('--target-weight', type=float, required=True,
                        help='Target weight in kg for protein calculation')
    parser.add_argument('--days', type=int, default=28,
                        help='Number of days to analyze (default: 28)')
    args = parser.parse_args()

    workspace = args.workspace.resolve()

    # Get current diet mode
    current_mode = get_current_diet_mode(workspace)

    # Collect meal data
    meal_data = collect_meal_data(workspace, args.days)

    if len(meal_data) < 7:
        print(json.dumps({
            "action": "insufficient_data",
            "days_analyzed": len(meal_data),
            "message": f"Not enough meal data (only {len(meal_data)} days available, need at least 7)."
        }))
        return

    # Calculate actual macro percentages
    actual_macros = calculate_macro_percentages(meal_data)

    if not actual_macros:
        print(json.dumps({
            "action": "insufficient_data",
            "days_analyzed": len(meal_data),
            "message": "Could not calculate macro percentages from available data."
        }))
        return

    # Find best matching mode
    match_result = find_best_matching_mode(actual_macros, current_mode, workspace, args.target_weight)

    if match_result is None:
        # Current mode is fine
        print(json.dumps({
            "action": "no_change",
            "current_mode": current_mode,
            "actual_macros": actual_macros,
            "days_analyzed": len(meal_data),
            "message": "Actual eating pattern matches current diet mode."
        }))
    else:
        # Recommend change
        recommended_mode, reason = match_result
        current_range = DIET_MODE_RANGES[current_mode]
        recommended_range = DIET_MODE_RANGES[recommended_mode]
        print(json.dumps({
            "action": "recommend_change",
            "current_mode": current_mode,
            "actual_macros": actual_macros,
            "recommended_mode": recommended_mode,
            "reason": reason,
            "days_analyzed": len(meal_data),
            "current_mode_range": {k: list(v) for k, v in current_range.items()},
            "recommended_mode_range": {k: list(v) for k, v in recommended_range.items()},
        }))


if __name__ == '__main__':
    main()
