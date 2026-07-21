#!/usr/bin/env python3
"""Weekly low-calorie safety check for weekly-report skill.

Checks if weekly average calorie intake falls below BMR floor.

Usage:
  python3 weekly-low-cal-check.py --data-dir <meals_dir> --bmr <kcal> \
    [--date YYYY-MM-DD] [--tz-offset <seconds>]

Output: JSON to stdout.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_SHORT_TO_LONG = {
    "cal": "calories", "p": "protein", "c": "carbs", "f": "fat",
    "protein_g": "protein", "carbs_g": "carbs", "fat_g": "fat",
}


def _normalize_path(p):
    return re.sub(r'(workspace-(?:wechat|wecom)-dm-)([^/]+)',
                  lambda m: m.group(1) + m.group(2).lower(), p)


def _local_date(tz_offset=None):
    if tz_offset is not None:
        utc_now = datetime.now(timezone.utc)
        local_dt = utc_now + timedelta(seconds=tz_offset)
        return local_dt.date().isoformat()
    return date.today().isoformat()


def _migrate_meal(meal):
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


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def weekly_low_cal_check(data_dir, bmr, ref_date=None, tz_offset=None):
    end = date.fromisoformat(ref_date) if ref_date else date.fromisoformat(_local_date(tz_offset))

    daily_totals = []
    days_below = []

    for offset in range(7):
        day = (end - timedelta(days=offset)).isoformat()
        path = os.path.join(data_dir, f"{day}.json")
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            meals = json.load(f)
        if not meals:
            continue
        meals = [_migrate_meal(m) for m in meals]
        day_cal = sum(m.get("calories", 0) for m in meals)
        if day_cal > 0:
            daily_totals.append({"date": day, "calories": round(day_cal, 1)})
            if day_cal < bmr:
                days_below.append(day)

    avg_cal = round(sum(d["calories"] for d in daily_totals) / len(daily_totals), 1) if daily_totals else 0

    return {
        "period_end": end.isoformat(),
        "logged_days": len(daily_totals),
        "daily_totals": sorted(daily_totals, key=lambda d: d["date"]),
        "weekly_avg_calories": avg_cal,
        "bmr": bmr,
        "calorie_floor": bmr,
        "days_below_floor": sorted(days_below),
        "days_below_count": len(days_below),
        "below_floor": avg_cal < bmr if daily_totals else False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Weekly low-calorie safety check")
    parser.add_argument("--data-dir", required=True, help="Path to meals data directory")
    parser.add_argument("--bmr", type=float, required=True, help="BMR in kcal")
    parser.add_argument("--date", default=None, help="End date (YYYY-MM-DD), default today")
    parser.add_argument("--tz-offset", type=int, default=None, help="Timezone offset in seconds")
    args = parser.parse_args()

    result = weekly_low_cal_check(
        data_dir=_normalize_path(args.data_dir),
        bmr=args.bmr,
        ref_date=args.date,
        tz_offset=args.tz_offset,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
