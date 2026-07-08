#!/usr/bin/env python3
"""
find-slot.py — Find an available cron minute slot to avoid simultaneous sends.

Given a target cron expression and timezone, examines all existing recurring cron
jobs and finds a nearby minute where fewer than MAX_PER_MINUTE jobs are scheduled.

Usage:
  python3 find-slot.py --cron "45 11 * * *" --tz "Asia/Shanghai" --type meal
  python3 find-slot.py --cron "30 7 * * 3,6" --tz "Asia/Shanghai" --type weight

Output (stdout): adjusted cron expression (e.g. "47 11 * * *")
If no adjustment needed, outputs the original expression unchanged.

Exit codes:
  0 = success (adjusted or unchanged)
  1 = error
  2 = window full, using original (warning printed to stderr)
"""

import argparse
import json
import os
import sys
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "..", ".."))
_STATE_DIR = os.environ.get("OPENCLAW_STATE_DIR", os.path.join(_PROJECT_ROOT, ".openclaw-gateway"))

# Bedrock TPM 换 anthropic 直连之后 3-4 个 job/min 的爆点也远低于 TPM 上限,
# 提高上限让 find-slot 能把 job 压平到实际有位置的分钟,减少 fallback 到 base
# cron 造成 21:00/11:45/8:30 累积热点(2026-07-06 事故 → 2026-07-08 rebalance
# → 新用户 batch-create-reminders 撞满原窗口再 fallback 回原点)。
MAX_PER_MINUTE = 4


def cron_to_utc_minutes(expr: str, tz_name: str) -> list[int]:
    """
    Convert a cron expression's hour:minute + timezone to a list of UTC
    minute-of-day values (0-1439). Returns a list because some cron expressions
    may have multiple hours/minutes (e.g. "0,30 8 * * *").

    Only extracts minute and hour fields; ignores day/month/dow for simplicity
    (per design: we count all recurring jobs regardless of day overlap).
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Expected 5-field cron, got: {expr}")

    minutes = _expand_field(fields[0], 0, 59)
    hours = _expand_field(fields[1], 0, 23)

    tz = ZoneInfo(tz_name)
    utc_minutes = set()

    for h in hours:
        for m in minutes:
            # Use a reference date to convert local -> UTC
            # We pick a non-DST-ambiguous date; slight inaccuracy near DST
            # transitions is acceptable for slot distribution purposes
            from datetime import datetime

            local_dt = datetime(2026, 6, 15, h, m, tzinfo=tz)
            utc_dt = local_dt.astimezone(timezone.utc)
            utc_min = utc_dt.hour * 60 + utc_dt.minute
            utc_minutes.add(utc_min)

    return list(utc_minutes)


def _expand_field(field: str, lo: int, hi: int) -> list[int]:
    """Expand a single cron field into a list of integer values."""
    results = set()
    for part in field.split(","):
        if "/" in part:
            range_part, step = part.split("/", 1)
            step = int(step)
            if range_part == "*":
                start, end = lo, hi
            elif "-" in range_part:
                start, end = map(int, range_part.split("-", 1))
            else:
                start, end = int(range_part), hi
            results.update(range(start, end + 1, step))
        elif "-" in part:
            a, b = map(int, part.split("-", 1))
            results.update(range(a, b + 1))
        elif part == "*":
            results.update(range(lo, hi + 1))
        else:
            results.add(int(part))
    return sorted(results)


def get_existing_jobs() -> list[dict]:
    """Read all cron jobs from gateway's persisted state file.

    Historically shelled out to `openclaw cron list --json`. That CLI path
    hard-caps the response at 200 jobs (openclaw src/cron/service/ops.ts
    Math.min(200, ...)), and does not expose --offset/--limit for pagination.
    In production with 900+ jobs, the CLI silently dropped 700+ from the
    count map, so the allocator thought most minutes were empty ⇒ never
    adjusted anything ⇒ 21:00 accumulated 60+ Product tips and 44+ Weekly
    reports, all firing at the same instant and swamping Bedrock TPM
    (2026-07-06 21:00 incident).

    Reading jobs.json directly avoids the cap: no CLI, no 200-limit, and
    also faster (no node fork + WebSocket handshake). Slight staleness
    (jobs.json is atomically rewritten by the gateway on every cron
    mutation) is acceptable for slot allocation.
    """
    jobs_path = os.path.join(_STATE_DIR, "cron", "jobs.json")
    try:
        with open(jobs_path) as f:
            data = json.load(f)
        return data.get("jobs", [])
    except FileNotFoundError:
        print(f"WARNING: jobs.json not found at {jobs_path}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"WARNING: read jobs.json failed: {e}", file=sys.stderr)
        return []


def build_utc_minute_counts(jobs: list[dict]) -> dict[int, int]:
    """
    Build a map of UTC minute-of-day -> number of recurring jobs at that minute.
    Only counts enabled, recurring (cron kind) jobs.
    """
    counts: dict[int, int] = {}
    for job in jobs:
        if not job.get("enabled", True):
            continue
        sched = job.get("schedule", {})
        if sched.get("kind") != "cron":
            continue
        expr = sched.get("expr", "")
        tz_name = sched.get("tz", "UTC")
        try:
            for utc_min in cron_to_utc_minutes(expr, tz_name):
                counts[utc_min] = counts.get(utc_min, 0) + 1
        except Exception as e:
            print(f"WARNING: skipping job {job.get('id','?')}: {e}", file=sys.stderr)
    return counts


def find_available_slot(
    target_utc_min: int, window_before: int, window_after: int, counts: dict[int, int]
) -> tuple[int, bool]:
    """
    Find an available minute slot near the target.

    Scans outward from target: target, target-1, target+1, target-2, target+2, ...
    but only within [target - window_before, target + window_after].

    Returns (chosen_utc_minute, is_fallback).
    is_fallback=True means window was full, returning original target.
    """
    lo = (target_utc_min - window_before) % 1440
    hi = (target_utc_min + window_after) % 1440

    def in_window(m: int) -> bool:
        m = m % 1440
        if lo <= hi:
            return lo <= m <= hi
        else:  # wraps around midnight
            return m >= lo or m <= hi

    # Scan outward from target
    for offset in range(0, window_before + window_after + 1):
        candidates = []
        if offset == 0:
            candidates = [target_utc_min % 1440]
        else:
            candidates = [
                (target_utc_min - offset) % 1440,
                (target_utc_min + offset) % 1440,
            ]
        for candidate in candidates:
            if in_window(candidate) and counts.get(candidate, 0) < MAX_PER_MINUTE:
                return candidate, False

    return target_utc_min % 1440, True  # fallback


def utc_minute_to_local(utc_min: int, tz_name: str) -> tuple[int, int]:
    """Convert a UTC minute-of-day back to local hour, minute."""
    from datetime import datetime

    utc_dt = datetime(2026, 6, 15, utc_min // 60, utc_min % 60, tzinfo=timezone.utc)
    local_dt = utc_dt.astimezone(ZoneInfo(tz_name))
    return local_dt.hour, local_dt.minute


def adjust_cron_expr(original_expr: str, new_hour: int, new_minute: int) -> str:
    """Replace the minute and hour fields of a cron expression."""
    fields = original_expr.strip().split()
    fields[0] = str(new_minute)
    fields[1] = str(new_hour)
    return " ".join(fields)


def _window_for_type(job_type: str) -> tuple[int, int]:
    if job_type in ("meal", "weight"):
        return 10, 5
    return 10, 0


def _allocate_one(cron: str, tz: str, job_type: str, counts: dict[int, int]) -> tuple[str, int]:
    """
    Find an available slot for one job, update counts in-place, and return
    (adjusted_cron, chosen_utc_min).  Mutates counts so callers in a batch
    see slots already reserved by earlier jobs.
    """
    target_utc_mins = cron_to_utc_minutes(cron, tz)
    target_utc_min = target_utc_mins[0]
    window_before, window_after = _window_for_type(job_type)

    if counts.get(target_utc_min, 0) < MAX_PER_MINUTE:
        counts[target_utc_min] = counts.get(target_utc_min, 0) + 1
        return cron, target_utc_min

    chosen_utc_min, is_fallback = find_available_slot(
        target_utc_min, window_before, window_after, counts
    )

    if is_fallback:
        print(
            f"WARNING: All slots in window are full "
            f"(target UTC minute {target_utc_min}, "
            f"window [-{window_before}, +{window_after}]). "
            f"Using original time.",
            file=sys.stderr,
        )
        counts[target_utc_min] = counts.get(target_utc_min, 0) + 1
        return cron, target_utc_min

    new_hour, new_minute = utc_minute_to_local(chosen_utc_min, tz)
    adjusted = adjust_cron_expr(cron, new_hour, new_minute)
    if adjusted != cron:
        print(
            f"Adjusted cron: {cron} -> {adjusted} "
            f"(slot {counts.get(target_utc_min, 0)} jobs at target, "
            f"moved to UTC :{chosen_utc_min % 60:02d})",
            file=sys.stderr,
        )
    counts[chosen_utc_min] = counts.get(chosen_utc_min, 0) + 1
    return adjusted, chosen_utc_min


def main_batch(batch_file: str) -> None:
    """
    Batch mode: read a JSON array of job definitions, fetch cron list once,
    allocate slots for all jobs sequentially (updating in-memory counts after
    each allocation so subsequent jobs see already-reserved slots), then write
    a JSON array of {name, adjusted_cron} to stdout.

    Input JSON format:
        [{"name": "...", "cron": "...", "type": "meal|weight|other", "tz": "..."}, ...]

    Output JSON format:
        [{"name": "...", "adjusted_cron": "..."}, ...]
    """
    with open(batch_file) as f:
        jobs = json.load(f)

    existing = get_existing_jobs()
    counts = build_utc_minute_counts(existing)

    results = []
    for job in jobs:
        name = job["name"]
        cron = job["cron"]
        tz = job["tz"]
        job_type = job.get("type", "other")

        adjusted, _ = _allocate_one(cron, tz, job_type, counts)
        results.append({"name": name, "adjusted_cron": adjusted})

    print(json.dumps(results, indent=2))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--cron", help="5-field cron expression (single-job mode)")
    p.add_argument("--tz", help="Timezone for this cron (single-job mode)")
    p.add_argument(
        "--type",
        choices=["meal", "weight", "other"],
        default="other",
        help="Job type determines search window",
    )
    p.add_argument(
        "--batch-file",
        help="JSON file with array of job definitions (batch mode). "
             "When set, --cron/--tz/--type are ignored.",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.batch_file:
        main_batch(args.batch_file)
        sys.exit(0)

    if not args.cron or not args.tz:
        print("ERROR: --cron and --tz are required in single-job mode", file=sys.stderr)
        sys.exit(1)

    # 1. Parse target cron -> UTC minute(s)
    target_utc_mins = cron_to_utc_minutes(args.cron, args.tz)
    if not target_utc_mins:
        print(f"ERROR: could not parse cron expression: {args.cron}", file=sys.stderr)
        sys.exit(1)

    # 2. Fetch existing jobs and count per-minute
    existing = get_existing_jobs()
    counts = build_utc_minute_counts(existing)

    # 3. Allocate slot
    adjusted, _ = _allocate_one(args.cron, args.tz, args.type, counts)

    print(adjusted)
    sys.exit(0)


if __name__ == "__main__":
    main()
