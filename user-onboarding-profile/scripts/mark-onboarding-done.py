#!/usr/bin/env python3
"""
mark-onboarding-done.py — Write/update the Onboarding Completed marker in
health-profile.md with a guaranteed, machine-readable format.

Why this script exists:
  Agents have been observed writing `## Onboarding Completed\\n\\n<date>`
  (a standalone section) instead of the spec-compliant
  `## Automation\\n- **Onboarding Completed:** <date>` (a field inside the
  Automation section). Downstream code (onboarding_completed() detector in
  the miniprogram backend, and conflict-check in migration.py) parses the
  field form with a regex that does not accept the section form, so users
  who got the wrong format are treated as "not onboarded" forever.

  This script writes the field form deterministically so agents don't have
  to get it right.

What it does:
  1. Read health-profile.md in the given workspace.
  2. If a standalone `## Onboarding Completed` / `## Pattern Detection Completed`
     section exists (common agent mistake), remove it.
  3. Ensure `## Automation` section exists (add it if missing, placed right
     before `## Health Flags` if present, otherwise at end of file).
  4. Inside ## Automation, ensure two fields:
       - **Onboarding Completed:** <date>
       - **Pattern Detection Completed:** <existing or —>
     If field already present, update `Onboarding Completed` to the new date
     and leave Pattern Detection Completed alone.
  5. Update the top-level `**Updated:**` header to the current timestamp.

Usage:
  python3 mark-onboarding-done.py --workspace /path/to/workspace \\
    [--tz-name Asia/Shanghai]

  If --tz-name not provided, reads USER.md > Timezone (defaults Asia/Shanghai).

Output (JSON):
  {"ok": true, "date": "2026-05-12", "mode": "updated" | "inserted"}

  On failure:
  {"ok": false, "error": "..."}

Idempotent: re-running on already-marked workspace just updates the date
(or is a no-op if today's date is already there).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _read_timezone_from_user_md(workspace: str) -> str | None:
    user_md = os.path.join(workspace, "USER.md")
    if not os.path.exists(user_md):
        return None
    try:
        with open(user_md, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"\*\*Timezone:\*\*\s*(\S+)", content)
        if m:
            val = m.group(1).strip()
            if val and val != "—":
                return val
    except Exception:
        pass
    return None


def _now(tz_name: str | None) -> datetime:
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    # Fallback: UTC+8
    return datetime.now(timezone(timedelta(hours=8)))


# Detect an erroneous standalone section like:
#   ## Onboarding Completed
#   (blank line)
#   2026-05-12
# Also matches "## Pattern Detection Completed" if the agent made the same
# mistake for that field.
_BAD_STANDALONE_RE = re.compile(
    r"^##\s+(Onboarding\s+Completed|Pattern\s+Detection\s+Completed)\s*\n+"
    r"(?:(?!^##\s)[^\n]*\n)*",  # consume until next ## section or EOF
    re.MULTILINE,
)

# Automation section — already existing
_AUTOMATION_SECTION_RE = re.compile(
    r"^##\s+Automation\s*\n((?:(?!^##\s)[^\n]*\n)*)",
    re.MULTILINE,
)

# Within Automation, the Onboarding Completed field
_ONBOARDING_FIELD_RE = re.compile(
    r"^(\s*-\s*\*\*Onboarding\s+Completed:\*\*\s*).*?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_PATTERN_FIELD_RE = re.compile(
    r"^(\s*-\s*\*\*Pattern\s+Detection\s+Completed:\*\*\s*).*?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_UPDATED_HEADER_RE = re.compile(
    r"^(\*\*Updated:\*\*)\s*.*$",
    re.MULTILINE,
)


def _extract_standalone_value(content: str) -> str | None:
    """If a '## Onboarding Completed' standalone section exists, extract its
    value (the first non-empty line after the heading). Returns None if not
    found or value unusable.
    """
    m = re.search(
        r"^##\s+Onboarding\s+Completed\s*\n+([^\n]+)",
        content, re.MULTILINE,
    )
    if not m:
        return None
    val = m.group(1).strip()
    if not val or val in ("—", "-", "none", "None"):
        return None
    return val


def _remove_bad_standalone_sections(content: str) -> str:
    """Remove any standalone '## Onboarding Completed' / '## Pattern Detection
    Completed' sections."""
    return _BAD_STANDALONE_RE.sub("", content)


def _upsert_automation_field(content: str, date_str: str) -> tuple[str, str]:
    """Ensure ## Automation section + Onboarding Completed field exist.
    Returns (new_content, mode) where mode ∈ {"updated", "inserted"}.
    """
    m_auto = _AUTOMATION_SECTION_RE.search(content)
    if m_auto:
        # Automation section exists — upsert the field
        section_body = m_auto.group(1)
        if _ONBOARDING_FIELD_RE.search(section_body):
            # Field exists — update its value
            new_section_body = _ONBOARDING_FIELD_RE.sub(
                rf"\g<1>{date_str}", section_body, count=1,
            )
            mode = "updated"
        else:
            # Field missing — append it. Keep pattern detection field if
            # already there (just add our field in front).
            pattern_match = _PATTERN_FIELD_RE.search(section_body)
            if pattern_match:
                new_section_body = section_body.replace(
                    pattern_match.group(0),
                    f"- **Onboarding Completed:** {date_str}\n"
                    + pattern_match.group(0),
                    1,
                )
            else:
                trimmed = section_body.rstrip("\n")
                new_section_body = (
                    (trimmed + "\n" if trimmed else "")
                    + f"- **Onboarding Completed:** {date_str}\n"
                    + "- **Pattern Detection Completed:** —\n"
                )
            mode = "inserted"
        new_content = content[:m_auto.start(1)] + new_section_body + content[m_auto.end(1):]
        return new_content, mode

    # Automation section missing entirely — insert it before ## Health Flags
    # if present, otherwise at end of file.
    automation_block = (
        "## Automation\n"
        f"- **Onboarding Completed:** {date_str}\n"
        "- **Pattern Detection Completed:** —\n\n"
    )
    m_flags = re.search(r"^##\s+Health\s+Flags\s*$", content, re.MULTILINE)
    if m_flags:
        insert_pos = m_flags.start()
        new_content = content[:insert_pos] + automation_block + content[insert_pos:]
    else:
        # Append at end
        if not content.endswith("\n"):
            content += "\n"
        if not content.endswith("\n\n"):
            content += "\n"
        new_content = content + automation_block
    return new_content, "inserted"


def _update_updated_header(content: str, iso_ts: str) -> str:
    if _UPDATED_HEADER_RE.search(content):
        return _UPDATED_HEADER_RE.sub(rf"\g<1> {iso_ts}", content, count=1)
    return content


# 业务侧统一发卡入口(见 card-send-refactor:系统/业务拆分)。取代旧的直接
# node send-fixed-card.mjs —— 现在业务层负责读 USER.md/填标题/选模板,系统层
# (backend send-miniprogram-card.mjs)只做渲染发送。
_BUILD_SEND_CARD = (
    "/home/nanorhino/backend-service/.openclaw-user-service/"
    "skills/shared/card/build-and-send-card.sh"
)

_CREATE_REMINDER = (
    "/home/nanorhino/backend-service/.openclaw-user-service/"
    "skills/notification-manager/scripts/create-reminder.sh"
)


def _schedule_d7_mpcard_cron(workspace: str, now: datetime) -> str:
    """Best-effort: create a one-shot cron that fires on completion day + 6
    days at 10:00 Beijing time, telling the agent to send a miniprogram card
    (weight curve cover → dashboard) as a recall nudge.

    Never raises: any failure returns a status string for the output JSON and
    must NOT affect the onboarding-completed marker (already written).
    """
    try:
        account_id = os.path.basename(os.path.normpath(workspace))
        prefix = "workspace-wechat-dm-"
        if not account_id.startswith(prefix):
            return "skipped_bad_workspace"
        account_id = account_id[len(prefix):]
        agent_id = f"wechat-dm-{account_id}"

        identity_path = os.path.join(workspace, "wechat-identity.json")
        if not os.path.exists(identity_path):
            return "skipped_no_robotid"
        with open(identity_path, "r", encoding="utf-8") as f:
            robot_id = (json.load(f) or {}).get("robotId")
        if not robot_id:
            return "skipped_no_robotid"

        # Target: (today + 6 days) 10:00:00 +08:00.
        target = (now + timedelta(days=6)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        at_iso = target.strftime("%Y-%m-%dT%H:%M:%S%z")
        at_iso = at_iso[:-2] + ":" + at_iso[-2:]

        name = f"d7-mpcard-{account_id[-6:]}"

        # Idempotency: skip if a same-named job already exists.
        try:
            listed = subprocess.run(
                ["openclaw", "cron", "list", "--agent", agent_id, "--all", "--json"],
                capture_output=True, text=True, timeout=30,
            )
            if listed.returncode == 0 and listed.stdout.strip():
                data = json.loads(listed.stdout)
                jobs = data.get("jobs", data) if isinstance(data, dict) else data
                if any(j.get("name") == name for j in (jobs or [])):
                    return "exists"
        except Exception:
            # List failure shouldn't block creation; deleteAfterRun + stable
            # name keep duplicates bounded.
            pass

        try:
            _um = (Path(workspace) / "USER.md").read_text(encoding="utf-8")
            # 值捕获 [^\n*][^\n]*? : Name 为空时不误抓下一行的 `- **What to call them:**`
            # 标签(见 user-md-name-parse-bug);解析不到则兜底 "你"。
            _m = re.search(
                r"^[ \t]*-?[ \t]*\*\*Name:\*\*[ \t]*([^\n*][^\n]*?)[ \t]*$",
                _um, re.MULTILINE | re.IGNORECASE,
            )
            _uname = _m.group(1).strip() if (_m and _m.group(1).strip()) else "你"
        except Exception:
            _uname = "你"

        message = (
            "First run this exec command: "
            f"bash {_BUILD_SEND_CARD} --account {account_id} --kind profile\n"
            "Then reply with EXACTLY this text and nothing else:\n"
            f"{_uname}！🎉 偷偷数了下，你已经坚持记录整整一周啦～太可以了吧！\n"
            "这几天你吃了啥、体重怎么变的，我可全都给你存进专属减脂档案里咯 📒✨\n"
            "之后想看数据随时发送“减脂档案”"
        )

        created = subprocess.run(
            [
                "bash", _CREATE_REMINDER,
                "--agent", agent_id,
                "--name", name,
                "--at", at_iso,
                "--message", message,
            ],
            capture_output=True, text=True, timeout=60,
        )
        if created.returncode != 0:
            return f"error: {(created.stderr or created.stdout).strip()[:200]}"
        return f"scheduled:{at_iso}"
    except Exception as e:
        return f"error: {e}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark onboarding as completed in health-profile.md"
    )
    parser.add_argument("--workspace", required=True,
                        help="Path to agent workspace (contains health-profile.md)")
    parser.add_argument("--tz-name", default=None,
                        help="IANA timezone name (default: read from USER.md, fallback Asia/Shanghai)")
    args = parser.parse_args()

    workspace = args.workspace
    path = os.path.join(workspace, "health-profile.md")
    if not os.path.exists(path):
        print(json.dumps({"ok": False, "error": f"health-profile.md not found: {path}"}))
        return 1

    tz_name = args.tz_name or _read_timezone_from_user_md(workspace) or "Asia/Shanghai"
    now = _now(tz_name)
    date_str = now.strftime("%Y-%m-%d")
    iso_ts = now.strftime("%Y-%m-%dT%H:%M:%S%z")
    # Insert colon in the offset: +0800 -> +08:00
    iso_ts = iso_ts[:-2] + ":" + iso_ts[-2:]

    try:
        with open(path, "r", encoding="utf-8") as f:
            original = f.read()
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"read failed: {e}"}))
        return 1

    # 1. If a bad standalone section exists, salvage its value if today's
    #    arg wasn't provided — actually we always use today's date, but
    #    removing the bad section first ensures we don't end up with both.
    content = _remove_bad_standalone_sections(original)

    # 2. Upsert the correct Automation field.
    content, mode = _upsert_automation_field(content, date_str)

    # 3. Refresh the top-level Updated timestamp.
    content = _update_updated_header(content, iso_ts)

    if content == original:
        # No actual change (already correct with today's date).
        mpcard_cron = _schedule_d7_mpcard_cron(workspace, now)
        print(json.dumps({"ok": True, "date": date_str, "mode": "noop",
                          "mpcard_cron": mpcard_cron}))
        return 0

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"write failed: {e}"}))
        return 1

    mpcard_cron = _schedule_d7_mpcard_cron(workspace, now)
    print(json.dumps({"ok": True, "date": date_str, "mode": mode,
                      "mpcard_cron": mpcard_cron}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
