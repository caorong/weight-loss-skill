#!/usr/bin/env python3
"""Exercise the installed CLI in a temporary workspace; never seed real food logs."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    cli = Path(__file__).resolve().with_name("meal-log.py")
    checks = []
    with tempfile.TemporaryDirectory(prefix="meal smoke ") as temp:
        def call(action, payload=None):
            result = subprocess.run(
                [sys.executable, str(cli), action, "--workspace", temp, "--tz", "Asia/Tokyo"],
                input=json.dumps(payload) if payload is not None else None,
                capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError(result.stderr or result.stdout)
            data = json.loads(result.stdout)
            if not data.get("ok"):
                raise ValueError(f"{action}: {data}")
            return data

        def check(condition, name):
            if not condition:
                raise ValueError(f"Smoke check failed: {name}")
            checks.append(name)

        info = call("init")
        check(info["writable"], "temporary_workspace_writable")
        request = {"request_id": "smoke:add:1", "date": info["today"],
                   "meal_type": "lunch", "foods": [{"name": "SYNTHETIC TEST ONLY",
                   "quantity": 100, "unit": "g", "calories": 200, "protein": 5,
                   "carbs": 40, "fat": 2, "source": "user_provided",
                   "portion_source": "user_measured"}]}
        added = call("add", request)
        check(added["saved"] and added["logged_totals"]["calories"] == 200, "add_and_sum")
        check(call("add", request)["duplicate"], "retry_deduplication")
        meal = added["meal"]
        corrected = call("update", {"request_id": "smoke:update:1", "date": info["today"],
                          "meal_id": meal["id"], "food_id": meal["foods"][0]["id"],
                          "expected_revision": meal["revision"], "scale": 0.5})
        check(corrected["logged_totals"]["calories"] == 100, "portion_correction")
        # Each call is a new subprocess, checking persistence across processes.
        listed = call("list")
        check(len(listed["meals"]) == 1, "persistent_read")
        removed = call("delete", {"request_id": "smoke:delete:1", "date": info["today"],
                        "meal_id": meal["id"], "expected_revision": corrected["meal"]["revision"]})
        check(removed["saved"] and not call("list")["meals"], "delete")
        check(call("summary")["ok"], "summary")
    print(json.dumps({"ok": True, "checks": checks, "fixture": "temporary_synthetic",
                      "real_workspace_modified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
