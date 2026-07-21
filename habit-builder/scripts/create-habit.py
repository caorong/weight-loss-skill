#!/usr/bin/env python3
"""
Create a single habit with its cron reminder.
All parameters come from the agent — no predefined habits.

Usage:
  python3 create-habit.py \
    --workspace-dir /path/to/workspace \
    --agent-id wechat-dm-xxx \
    --channel wechat \
    --habit-id early-sleep \
    --description "每天12点前放下手机睡觉" \
    --checkin-msg "手机放下了吗？该睡觉啦🌙" \
    --reminder-time "00:00" \
    --notification-manager-dir /path/to/notification-manager
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def log(msg):
    print(f"[create-habit] {msg}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-dir", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--channel", default="wechat")
    parser.add_argument("--notification-manager-dir", required=True)
    parser.add_argument("--habit-id", required=True, help="Unique ID, e.g. early-sleep")
    parser.add_argument("--description", required=True, help="Human-readable description")
    parser.add_argument("--checkin-msg", required=True, help="Exact message sent to user at reminder time")
    parser.add_argument("--reminder-time", required=True, help="HH:MM in user's timezone")
    args = parser.parse_args()

    habits_path = os.path.join(args.workspace_dir, "habits.active")

    # Load existing habits
    existing = []
    if os.path.exists(habits_path):
        try:
            existing = json.loads(Path(habits_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError):
            existing = []

    # Check for duplicate
    for h in existing:
        if h.get("habit_id") == args.habit_id:
            print(json.dumps({
                "action": "skip",
                "reason": f"habit '{args.habit_id}' already exists"
            }, ensure_ascii=False))
            return

    # Validate time format
    try:
        parts = args.reminder_time.split(":")
        h, m = int(parts[0]), int(parts[1])
        assert 0 <= h <= 23 and 0 <= m <= 59
    except (ValueError, IndexError, AssertionError):
        print(json.dumps({
            "action": "error",
            "error": f"invalid time format: {args.reminder_time}, expected HH:MM"
        }, ensure_ascii=False))
        sys.exit(1)

    # Create habit entry
    from datetime import date
    habit_entry = {
        "habit_id": args.habit_id,
        "description": args.description,
        "reminder_time": args.reminder_time,
        "checkin_msg": args.checkin_msg,
        "created_at": date.today().isoformat(),
        "phase": "anchor",
        "source": "conversation",
        "completion_log": [],
    }

    # Create cron
    cron_expr = f"{m} {h} * * *"
    message = (
        f"Habit check-in for {args.habit_id}. "
        f"Send this message to the user exactly as written (do not add anything): "
        f"{args.checkin_msg}"
    )

    nm_dir = args.notification_manager_dir
    create_script = os.path.join(nm_dir, "scripts", "create-reminder.sh")

    cmd = [
        "bash", create_script,
        "--agent", args.agent_id,
        "--channel", args.channel,
        "--name", f"Habit: {args.description[:20]}",
        "--message", message,
        "--cron", cron_expr,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"cron creation failed: {result.stderr}")
        print(json.dumps({
            "action": "error",
            "error": f"cron creation failed: {result.stderr[:200]}"
        }, ensure_ascii=False))
        sys.exit(1)

    # Extract cron ID from output
    cron_id = None
    try:
        for line in result.stdout.split("\n"):
            if '"id"' in line:
                cron_id = line.split('"id"')[1].strip().strip(':').strip().strip('"').strip(',')
                break
    except Exception:
        pass

    habit_entry["cron_id"] = cron_id

    # Save to habits.active
    existing.append(habit_entry)
    Path(habits_path).write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(json.dumps({
        "action": "created",
        "habit": habit_entry,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
