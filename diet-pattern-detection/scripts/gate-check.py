#!/usr/bin/env python3
"""Read-only pre-agent gate for the Diet pattern detection cron.

Prints NO_REPLY only while fewer than three complete meal days exist (or when
the one-time task is already marked complete). Once data is sufficient it
prints SEND so the agent can perform the actual pattern decision and required
cron cleanup/completion marking.
"""

import argparse
import importlib.util
import re
from pathlib import Path


def load_detector():
    path = Path(__file__).with_name("detect-pattern.py")
    spec = importlib.util.spec_from_file_location("diet_pattern_detector", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile_date(text: str, field: str):
    match = re.search(rf"{re.escape(field)}:?\*{{0,2}}\s*(\d{{4}}-\d{{2}}-\d{{2}})", text, re.I)
    return match.group(1) if match else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-dir", required=True)
    parser.add_argument("--tz-offset", required=True, type=int)
    args = parser.parse_args()

    workspace = Path(args.workspace_dir)
    profile = workspace / "health-profile.md"
    if not profile.exists():
        raise FileNotFoundError(profile)
    if profile_date(profile.read_text(encoding="utf-8"), "Pattern Detection Completed"):
        print("NO_REPLY already_completed")
        return

    detector = load_detector()
    result = detector.detect_diet_pattern(
        data_dir=str(workspace / "data/meals"),
        current_mode="balanced",
        weight=1.0,
        target_weight=1.0,
        tz_offset=args.tz_offset,
    )
    if result.get("reason") == "insufficient_data":
        print(f"NO_REPLY insufficient_data days_found={result.get('days_found', 0)}")
        return
    print("SEND data_sufficient")


if __name__ == "__main__":
    main()
