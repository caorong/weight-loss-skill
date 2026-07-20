#!/usr/bin/env python3
"""
activation-mark-sent.py — Deterministically increment an activation nudge
counter inside data/engagement.json (called AFTER a successful send).

Owned by notification-manager (which owns engagement.json). This replaces
LLM-driven read-modify-write of the counter: the "max 2 nudges then permanent
Silent" anti-nag guarantee depends on the count being exact, so the increment
must be deterministic, not left to the model.

Generic over nudge type via --counter:
  - first_meal_nudges_sent   (Part-2 first-meal nudge: onboarded, never logged)
  - nudges_sent              (Part-1 activation nudge: greeted, never replied)

Behavior:
  - Read-modify-write the `activation` block inside engagement.json.
  - Create the `activation` block if absent; never clobber sibling fields
    (notification_stage, stage_changed_at, etc.) or sibling counters.
  - Atomic write (tmp file + os.replace) under an exclusive flock, so concurrent
    crons can't lose an increment.

Usage:
  python3 activation-mark-sent.py --workspace-dir <WS> \
      --counter first_meal_nudges_sent|nudges_sent

Output (stdout): JSON {"status":"ok","counter":<name>,"value":<new_value>}
Exit code 0 on success, 1 on hard failure (e.g. cannot write).
"""

import argparse
import fcntl
import json
import os
import sys
import tempfile

VALID_COUNTERS = ("first_meal_nudges_sent", "nudges_sent")


def _normalize_path(p):
    """Lowercase wechat-dm/wecom-dm segment to avoid case-mismatch directories."""
    import re as _re
    return _re.sub(
        r'(workspace-(?:wechat|wecom)-dm-)([^/]+)',
        lambda m: m.group(1) + m.group(2).lower(), p,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Increment an activation nudge counter in engagement.json"
    )
    parser.add_argument("--workspace-dir", required=True, help="Agent workspace root")
    parser.add_argument("--counter", required=True, choices=VALID_COUNTERS,
                        help="Which activation counter to increment")
    args = parser.parse_args()
    workspace_dir = _normalize_path(args.workspace_dir)

    path = os.path.join(workspace_dir, "data", "engagement.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    lockfile = path + ".lock"
    lock_fd = None
    try:
        lock_fd = open(lockfile, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        data = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, IOError):
                data = {}

        activation = data.get("activation")
        if not isinstance(activation, dict):
            activation = {}
        current = activation.get(args.counter, 0)
        if not isinstance(current, int):
            current = 0
        new_value = current + 1
        activation[args.counter] = new_value
        data["activation"] = activation

        # Atomic write to avoid races with the gateway / concurrent crons.
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        print(json.dumps(
            {"status": "ok", "counter": args.counter, "value": new_value},
            ensure_ascii=False,
        ))
        return 0
    except Exception as e:
        print(f"[activation-mark-sent] error: {e}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
