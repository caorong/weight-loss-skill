#!/usr/bin/env python3
"""Combined save + weight context in a single call.

Runs weight-tracker.py save, then returns recent weight history and plan
context so the model can judge whether to intervene.

Usage:
  python3 save-and-check.py \
    --data-dir {workspaceDir}/data \
    --value 75.2 --unit kg \
    --tz-offset 28800 \
    --plan-file {workspaceDir}/PLAN.md \
    --health-profile {workspaceDir}/health-profile.md \
    --user-file {workspaceDir}/USER.md \
    [--correct]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone


def _normalize_path(p):
    return re.sub(r'(workspace-(?:wechat|wecom)-dm-)([^/]+)', lambda m: m.group(1) + m.group(2).lower(), p)


def _validate_data_dir(p):
    """Reject --data-dir pointing to workspace root (missing /data suffix)."""
    if re.search(r'workspace-(?:wechat|wecom)-dm-[^/]+/?$', p.rstrip('/')):
        sys.stderr.write(
            "ERROR: --data-dir points to the workspace root; expected '<workspace>/data'. "
            f"Got: {p}\n"
        )
        sys.exit(2)


def run_script(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, result.stderr
        return json.loads(result.stdout), None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return None, str(e)


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_value_unit(entry):
    """Read (value, unit) tolerating off-schema shapes ({morning:X}/{evening:X}/bare
    number). Mirrors weight-tracker.extract_value_unit. Returns (None, None) if no
    numeric value found."""
    if isinstance(entry, (int, float)):
        return float(entry), "kg"
    if isinstance(entry, dict):
        val = entry.get("value")
        if val is None:
            for alt in ("morning", "evening", "weight", "kg", "am", "pm"):
                if isinstance(entry.get(alt), (int, float)):
                    val = entry[alt]
                    break
        if val is None:
            return None, None
        try:
            return float(val), entry.get("unit", "kg")
        except (TypeError, ValueError):
            return None, None
    return None, None


def get_recent_weights(data_dir, tz_offset, limit=14):
    """Return last N weight readings sorted by date."""
    raw = load_json(os.path.join(data_dir, "weight.json"))
    readings = []
    for k, v in sorted(raw.items()):
        val, unit = _extract_value_unit(v)
        if val is None:
            continue  # skip unreadable/off-schema entries rather than crash
        readings.append({"date": k[:10], "value": val, "unit": unit})
    return readings[-limit:]


def get_strategy_status(data_dir, tz_offset):
    """Check if there's an active weight-gain strategy."""
    tz = timezone(timedelta(seconds=tz_offset))
    today = datetime.now(tz).strftime("%Y-%m-%d")
    strat = load_json(os.path.join(data_dir, "weight-gain-strategy.json"))
    active = strat.get("active_strategy", {})
    if active and active.get("status") == "active":
        end = active.get("end_date", "")
        if end >= today:
            result = {"active": True, "type": active.get("type"), "end_date": end}
            if active.get("consensus"):
                result["consensus"] = active["consensus"]
            return result
    return {"active": False}


def get_last_intervention(data_dir):
    """Return the last intervention date from weight-gain-state.json."""
    state = load_json(os.path.join(data_dir, "weight-gain-state.json"))
    return state.get("last_intervention_date")


def save_intervention_date(data_dir, tz_offset):
    """Record that an intervention happened today (called by weight-gain-strategy)."""
    tz = timezone(timedelta(seconds=tz_offset))
    today = datetime.now(tz).strftime("%Y-%m-%d")
    path = os.path.join(data_dir, "weight-gain-state.json")
    state = load_json(path)
    state["last_intervention_date"] = today
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Save weight + context / mark intervention")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--value", type=float, default=None)
    parser.add_argument("--unit", default=None)
    parser.add_argument("--tz-offset", type=int, default=0)
    parser.add_argument("--correct", action="store_true")
    parser.add_argument("--fasting", action="store_true", help="Mark this reading as a fasting/morning weigh-in")
    parser.add_argument("--note", default=None, help="Optional free-text note for this entry")
    parser.add_argument("--plan-file", default=None)
    parser.add_argument("--health-profile", default=None)
    parser.add_argument("--user-file", default=None)
    parser.add_argument("--mark-intervention", action="store_true",
                        help="Record today as last intervention date and exit")
    args = parser.parse_args()
    args.data_dir = _normalize_path(args.data_dir)
    _validate_data_dir(args.data_dir)

    # --- Mark intervention mode ---
    if args.mark_intervention:
        save_intervention_date(args.data_dir, args.tz_offset)
        print(json.dumps({"marked": True, "date": datetime.now(
            timezone(timedelta(seconds=args.tz_offset))).strftime("%Y-%m-%d")}))
        return

    if args.value is None or args.unit is None:
        parser.error("--value and --unit are required (unless --mark-intervention)")


    script_dir = os.path.dirname(os.path.abspath(__file__))
    tracker = os.path.join(script_dir, "weight-tracker.py")

    # --- Step 1: Save ---
    save_cmd = [
        "python3", tracker, "save",
        "--data-dir", args.data_dir,
        "--value", str(args.value),
        "--unit", args.unit,
        "--tz-offset", str(args.tz_offset),
    ]
    if args.correct:
        save_cmd.append("--correct")
    if args.fasting:
        save_cmd.append("--fasting")
    if args.note:
        save_cmd.extend(["--note", args.note])

    save_result, save_err = run_script(save_cmd)
    if not save_result:
        print(json.dumps({
            "save": None,
            "error": f"save failed: {save_err}",
        }, indent=2, ensure_ascii=False))
        sys.exit(1)

    # --- Step 2: Context for model judgment ---
    recent = get_recent_weights(args.data_dir, args.tz_offset)
    strategy = get_strategy_status(args.data_dir, args.tz_offset)
    last_intervention = get_last_intervention(args.data_dir)

    print(json.dumps({
        "save": save_result,
        "context": {
            "recent_weights": recent,
            "active_strategy": strategy,
            "last_intervention_date": last_intervention,
        },
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
