#!/usr/bin/env python3
"""Verify the installed coach in a temporary workspace; no gateway/API calls."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    script = Path(__file__).with_name("coach.py")
    checks = []
    counter = 0
    with tempfile.TemporaryDirectory(prefix="coach smoke ") as ws:
        def call(action, payload=None, flags=()):
            nonlocal counter
            counter += 1
            if payload is not None:
                payload = {"request_id": f"smoke-coach:{counter}", **payload}
            proc = subprocess.run([sys.executable, str(script), action, "--workspace", ws, "--tz", "Asia/Tokyo", *flags],
                                  input=json.dumps(payload) if payload is not None else None,
                                  text=True, capture_output=True, timeout=30)
            if proc.returncode:
                raise ValueError(proc.stdout or proc.stderr)
            out = json.loads(proc.stdout)
            if not out.get("ok"):
                raise ValueError(str(out))
            return out

        def check(value, name):
            if not value:
                raise ValueError("Coach smoke check failed: " + name)
            checks.append(name)

        ctx = call("context")
        check(ctx["today"] and ctx["request_file"], "fresh_clock_and_unique_request_path")
        call("profile-set", {"expected_revision": 0, "fields": {"height_cm": 170, "age_years": 30}})
        check(call("context")["profile"]["height_cm"] == 170, "profile_persistence")
        call("preferences-set", {"expected_revision": 0, "fields": {"notes": ["SYNTHETIC FIXTURE"]}})
        check(call("context")["preferences"]["notes"] == ["SYNTHETIC FIXTURE"], "preference_persistence")
        weight = call("weight-add", {"date": ctx["today"], "value": 160, "unit": "斤"})["weight"]
        check(weight["value"] == 80, "weight_units")
        call("weight-update", {"id": weight["id"], "expected_revision": 1, "value": 79, "unit": "kg"})
        check(call("context")["latest_weight"]["value"] == 79, "weight_correction")
        call("exercise-add", {"date": ctx["today"], "items": [{"activity": "SYNTHETIC WALK", "duration_min": 20}]})
        check(call("review")["summary"]["exercise_calorie_unknown_sessions"] == 1, "exercise_unknown_calories")
        for kind in ("goal", "meal", "exercise"):
            p = call("plan-save", {"kind": kind, "body": "SYNTHETIC PLAN", "effective_date": ctx["today"]})["plan"]
            check(call("context")["active_plans"][kind] is None, kind + "_draft_no_effect")
            call("plan-activate", {"plan_id": p["id"], "confirmed": True})
            check(call("context")["active_plans"][kind]["id"] == p["id"], kind + "_confirmed_local")
        report = call("review")
        saved = call("review-save", {"start": report["start"], "end": report["end"],
                     "expected_snapshot": report["snapshot"], "analysis": "SYNTHETIC incomplete records."})
        check((Path(ws) / saved["path"]).is_file(), "review_local_file")
        daily = call("review", flags=("--review-type", "daily"))
        check(daily["start"] == daily["end"] == ctx["today"], "daily_today_only")
        check(daily["review_boundaries"]["tomorrow_tasks"] == [], "daily_no_tomorrow_tasks")
        weekly = call("review", flags=("--review-type", "weekly"))
        check(weekly["weekly_review"]["calendar_week"] and weekly["weekly_review"]["calendar_window_closed"], "weekly_previous_complete_calendar_week")
        check(call("reminder-specs")["specs"] == [], "reminders_opt_in_only")
    print(json.dumps({"ok": True, "checks": checks, "real_workspace_modified": False,
                      "native_scheduler": "not_tested", "model_planning_and_vision": "not_tested"}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
