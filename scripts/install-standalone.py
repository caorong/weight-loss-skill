#!/usr/bin/env python3
"""Install only the audited standalone entry point; never activate legacy jobs."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FILES = ("SKILL.md", "scripts/meal-log.py", "references/standalone-flow.md")


def reject_symlink(path, boundary):
    current = path
    while current != boundary:
        if current.is_symlink():
            raise ValueError(f"Refusing symlink: {current}")
        current = current.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Existing, actual OpenClaw agent workspace")
    parser.add_argument("--timezone", required=True, help="Explicit fallback IANA timezone")
    parser.add_argument("--replace", action="store_true", help="Back up then replace this skill only")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing anything")
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 10):
            raise ValueError("Python 3.10+ is required")
        import fcntl  # POSIX file locking is required in the execution environment.
        ZoneInfo(args.timezone)
        workspace = Path(args.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError("Workspace must already exist and be the agent's configured workspace")
        source = ROOT / "diet-tracking-analysis"
        target = workspace / "skills" / "diet-tracking-analysis"
        backups = workspace / "data" / "skill-backups"
        for path in (target, backups):
            reject_symlink(path, workspace)
        for relative in FILES:
            if not (source / relative).is_file():
                raise ValueError(f"Incomplete checkout: missing {relative}")
        if target.exists() and not args.replace:
            raise ValueError(f"Already installed: {target}. Use --replace to back up and replace only this skill")
        if args.dry_run:
            print(json.dumps({"dry_run": True, "target": str(target), "files": list(FILES),
                              "timezone": args.timezone}, ensure_ascii=False))
            return 0
        # Same interpreter, no pip, no cloud calls, and no edits to USER/PLAN/AGENTS.
        preflight = subprocess.run(
            [sys.executable, str(source / "scripts/meal-log.py"), "init",
             "--workspace", str(workspace), "--tz", args.timezone],
            capture_output=True, text=True, timeout=30)
        if preflight.returncode:
            raise ValueError(f"Ledger preflight failed: {preflight.stdout or preflight.stderr}")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".diet-stage-", dir=target.parent))
        previous = None
        try:
            for relative in FILES:
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, destination)
                destination.chmod(0o600)
            (stage / "runtime.json").write_text(json.dumps({
                "workspace": str(workspace), "timezone_fallback": args.timezone,
                "python": sys.executable,
                "note": "Use USER.md Timezone when present. Paths must be visible inside the agent execution environment."
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (stage / "runtime.json").chmod(0o600)
            if target.exists():
                backups.mkdir(parents=True, exist_ok=True, mode=0o700)
                previous = backups / ("diet-tracking-analysis-" + uuid.uuid4().hex)
                os.replace(target, previous)
            os.replace(stage, target)
        except Exception:
            if previous is not None and previous.exists() and not target.exists():
                os.replace(previous, target)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        print(json.dumps({"ok": True, "installed": str(target),
                          "backup": str(previous) if previous else None,
                          "preflight": json.loads(preflight.stdout)}, ensure_ascii=False, indent=2))
        print("Start a new OpenClaw session. Confirm the model can see a real photo and run Python.")
        print("No model keys, gateway config, profiles, reminders or other skills were changed.")
        print("Existing global legacy skills/jobs are NOT disabled. Use a dedicated agent with only diet-tracking-analysis enabled.")
        return 0
    except (ValueError, OSError, ImportError, subprocess.SubprocessError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
