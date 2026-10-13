#!/usr/bin/env python3
"""Install and verify only the standalone skill. Default runner: uv."""
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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
FILES = ("SKILL.md", "scripts/meal-log.py", "scripts/self-test.py",
         "references/standalone-flow.md", "pyproject.toml", "uv.lock", ".python-version")


def reject_symlink(path, boundary):
    current = path
    while current != boundary:
        if current.is_symlink():
            raise ValueError(f"Refusing symlink: {current}")
        if current == current.parent:
            raise ValueError(f"Path is outside workspace: {path}")
        current = current.parent


def run_checked(command, *, timeout=120, env=None):
    result = subprocess.run(command, capture_output=True, text=True,
                            timeout=timeout, env=env)
    if result.returncode:
        raise ValueError(f"Command failed ({result.returncode}): "
                         f"{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Existing, actual OpenClaw agent workspace")
    parser.add_argument("--timezone", required=True, help="Explicit fallback IANA timezone")
    parser.add_argument("--runner", choices=("uv", "python"), default="uv",
                        help="uv (default); python is an explicit stdlib-only fallback")
    parser.add_argument("--offline", action="store_true", help="No network while preparing the installed uv environment")
    parser.add_argument("--replace", action="store_true", help="Back up then replace this skill only")
    parser.add_argument("--dry-run", action="store_true", help="Preview; installer does not create files or environments")
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 10):
            raise ValueError("Python 3.10+ is required")
        import fcntl  # POSIX locking is required in the execution environment.
        ZoneInfo(args.timezone)
        workspace = Path(args.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError("Workspace must already exist and be the agent's configured workspace")
        source = ROOT / "diet-tracking-analysis"
        target = workspace / "skills" / "diet-tracking-analysis"
        backups = workspace / "data" / "skill-backups"
        for path in (target, backups):
            reject_symlink(path, workspace)
        if target.resolve() == source.resolve():
            raise ValueError("Run installer from a separate checkout, not the installed skill directory")
        for relative in FILES:
            if not (source / relative).is_file():
                raise ValueError(f"Incomplete checkout: missing {relative}")
        if target.exists() and not args.replace:
            raise ValueError(f"Already installed: {target}. Use --replace to back up and replace only this skill")
        uv = shutil.which("uv") if args.runner == "uv" else None
        if args.runner == "uv" and not uv:
            raise ValueError("uv is missing in this execution environment. Install uv with permission; "
                             "or explicitly choose --runner python. No silent fallback.")
        # An unrelated uv project must not redirect this skill's environment.
        if args.runner == "uv" and any(os.environ.get(k) for k in ("UV_PROJECT_ENVIRONMENT", "UV_WORKING_DIR")):
            raise ValueError("Unset UV_PROJECT_ENVIRONMENT and UV_WORKING_DIR for this installer and agent")
        if args.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "target": str(target),
                              "files": list(FILES), "runner": args.runner,
                              "timezone": args.timezone}, ensure_ascii=False))
            return 0
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".diet-stage-", dir=target.parent))
        previous = None
        placed = False
        try:
            for relative in FILES:
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, destination)
                destination.chmod(0o600)
            if target.exists():
                backups.mkdir(parents=True, exist_ok=True, mode=0o700)
                previous = backups / ("diet-tracking-analysis-" + uuid.uuid4().hex)
                os.replace(target, previous)
            os.replace(stage, target)
            placed = True
            # Create .venv at its FINAL location, never copy/move a checkout venv.
            if args.runner == "uv":
                sync = [uv, "sync", "--locked", "--project", str(target), "--no-python-downloads"]
                if args.offline:
                    sync.append("--offline")
                run_checked(sync)
                prefix = [uv, "run", "--locked", "--offline", "--no-python-downloads",
                          "--project", str(target), "python"]
            else:
                # stdlib-only fallback must not point to a disposable source .venv.
                python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
                prefix = [python]
            cli = prefix + [str(target / "scripts/meal-log.py")]
            smoke = json.loads(run_checked(prefix + [str(target / "scripts/self-test.py")]))
            if not smoke.get("ok"):
                raise ValueError("Installed smoke test did not pass")
            preflight = json.loads(run_checked(cli + ["init", "--workspace", str(workspace), "--tz", args.timezone]))
            if not preflight.get("ok") or not preflight.get("writable"):
                raise ValueError("Installed ledger preflight did not pass")
            python = run_checked(prefix + ["-c", "import sys; print(sys.executable)"]).strip()
            runtime = {
                "schema_version": 2, "runner": args.runner, "command": cli,
                "workspace": str(workspace), "timezone_fallback": args.timezone, "python": python,
                "note": "Use command as an argv prefix. USER.md Timezone takes precedence. "
                        "Run in this SAME execution environment. No automatic runner fallback. "
                        "Keep UV_PROJECT_ENVIRONMENT/UV_WORKING_DIR unset."
            }
            (target / "runtime.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (target / "runtime.json").chmod(0o600)
        except Exception:
            if placed and target.exists():
                shutil.rmtree(target)
            if previous is not None and previous.exists() and not target.exists():
                os.replace(previous, target)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        print(json.dumps({"ok": True, "installed": str(target), "runner": args.runner,
                          "command": cli, "backup": str(previous) if previous else None,
                          "preflight": preflight, "self_test": smoke,
                          "openclaw_discovery": "not_tested", "model_vision": "not_tested",
                          "notes": ["Start a new OpenClaw session and verify skill/tool access and a real photo.",
                                    "No model keys, gateway config, profiles, reminders or other skills changed.",
                                    "Existing global legacy skills/jobs are NOT disabled."]},
                         ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError, ZoneInfoNotFoundError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        print(f"Install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
