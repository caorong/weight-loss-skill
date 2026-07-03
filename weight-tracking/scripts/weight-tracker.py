#!/usr/bin/env python3
"""Weight tracking CRUD with unit conversion.

Data file: {data_dir}/weight.json
Format: JSON object keyed by ISO-8601 datetime with timezone offset.

Commands:
  save       Record a weight entry (auto-detects new vs correction)
  load       Read entries with optional filters and unit conversion
  delete     Remove an entry by datetime key
  update     Modify an existing entry
  set-unit   Change unit preference in health-profile.md
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

def _normalize_path(p):
    """Lowercase wechat-dm/wecom-dm segment to avoid case-mismatch directories."""
    import re as _re
    return _re.sub(r'(workspace-(?:wechat|wecom)-dm-)([^/]+)', lambda m: m.group(1) + m.group(2).lower(), p)


def _validate_data_dir(p):
    """
    Reject --data-dir that points to the workspace root (i.e. missing /data suffix).
    Pre-existing real-world bug: agent occasionally retries after a usage error
    and passes --data-dir <workspaceDir> instead of <workspaceDir>/data, then
    save_data silently creates weight.json in the workspace root, breaking the
    dashboard sync. Fail loudly here so the agent notices and retries correctly.
    """
    import re as _re
    if _re.search(r'workspace-(?:wechat|wecom)-dm-[^/]+/?$', p.rstrip('/')):
        sys.stderr.write(
            "ERROR: --data-dir points to the workspace root; expected '<workspace>/data'. "
            f"Got: {p}\n"
        )
        sys.exit(2)


# ── Constants ────────────────────────────────────────────────────────────────

KG_PER_LB = 0.45359237
LB_PER_KG = 1 / KG_PER_LB
CORRECTION_WINDOW_SECONDS = 30 * 60  # 30 minutes


# ── Helpers ──────────────────────────────────────────────────────────────────

def data_path(data_dir: str) -> Path:
    return Path(data_dir) / "weight.json"


def load_data(data_dir: str) -> dict:
    p = data_path(data_dir)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data_dir: str, data: dict):
    p = data_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Sort keys chronologically
    sorted_data = dict(sorted(data.items()))
    with open(p, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, indent=2, ensure_ascii=False)


def convert_weight(value: float, from_unit: str, to_unit: str) -> float:
    """Convert weight between kg and lb. Returns rounded to 1 decimal."""
    from_unit = normalize_unit(from_unit)
    to_unit = normalize_unit(to_unit)
    if from_unit == to_unit:
        return round(value, 1)
    if from_unit == "kg" and to_unit == "lb":
        return round(value * LB_PER_KG, 1)
    if from_unit == "lb" and to_unit == "kg":
        return round(value * KG_PER_LB, 1)
    raise ValueError(f"Cannot convert from {from_unit} to {to_unit}")


def normalize_unit(unit: str) -> str:
    """Normalize various unit strings to 'kg' or 'lb'."""
    u = unit.lower().strip()
    if u in ("kg", "公斤", "千克"):
        return "kg"
    if u in ("lb", "lbs", "pound", "pounds"):
        return "lb"
    if u in ("斤", "jin"):
        # 1 斤 = 0.5 kg; we store as kg
        return "kg"
    return u


def catty_to_kg(value: float, original_unit: str) -> float:
    """If original unit is 斤/jin, convert value to kg (1斤 = 0.5kg)."""
    if original_unit.lower().strip() in ("斤", "jin"):
        return value * 0.5
    return value


def extract_value_unit(entry):
    """Read (value, unit) from a weight.json entry, tolerating off-schema shapes.

    Canonical entry is {"value": N, "unit": "kg"}. But agents have occasionally
    hand-written weight.json with alternate shapes — {"morning": 60.45},
    {"evening": 61.0, "note": ...}, or a bare number. Read those without crashing
    (returns kg by default) so load/history never breaks on legacy dirt. Returns
    (None, None) if no numeric value can be found.
    """
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


def now_with_offset(tz_offset_seconds: int) -> datetime:
    tz = timezone(timedelta(seconds=tz_offset_seconds))
    return datetime.now(tz)


def format_iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except AttributeError:
        # Python < 3.7 fallback
        import re
        s = s.strip()
        m = re.match(r'(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?([+-]\d{2}:\d{2})?', s)
        if not m:
            raise ValueError(f"Cannot parse datetime: {s}")
        from datetime import timezone, timedelta as _td

        base = datetime.strptime(m.group(1) + 'T' + m.group(2), '%Y-%m-%dT%H:%M:%S')
        if m.group(3):
            sign = 1 if m.group(3)[0] == '+' else -1
            hh, mm = int(m.group(3)[1:3]), int(m.group(3)[4:6])
            tz = timezone(_td(hours=sign * hh, minutes=sign * mm))
            base = base.replace(tzinfo=tz)
        return base


def date_from_key(key: str) -> str:
    """Extract YYYY-MM-DD from an ISO-8601 datetime key."""
    return key[:10]


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_save(args):
    data = load_data(args.data_dir)
    tz_offset = args.tz_offset or 0
    now = now_with_offset(tz_offset)
    key = format_iso(now)

    # Handle 斤 conversion
    value = catty_to_kg(args.value, args.unit)
    unit = normalize_unit(args.unit)

    action = "created"

    if args.correct and data:
        # Force overwrite the most recent entry
        last_key = max(data.keys())
        del data[last_key]
        action = "updated"
    elif data:
        # Check if last entry is within correction window
        last_key = max(data.keys())
        last_dt = parse_iso(last_key)
        # Legacy/off-schema keys may be date-only → naive datetime; align tzinfo to
        # `now` so the subtraction doesn't raise (naive vs aware). A date-only key is
        # never within the 30-min window anyway, so it just falls through to "created".
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=now.tzinfo)
        diff = (now - last_dt).total_seconds()
        if 0 <= diff <= CORRECTION_WINDOW_SECONDS:
            del data[last_key]
            action = "updated"

    entry = {"value": value, "unit": unit}
    # Optional metadata (backward-compatible additive fields). Consumers that only
    # read value/unit are unaffected; these let the agent record context that would
    # otherwise tempt it to hand-write weight.json with an off-schema shape.
    if getattr(args, "fasting", False):
        entry["fasting"] = True
    if getattr(args, "note", None):
        entry["note"] = args.note
    data[key] = entry
    save_data(args.data_dir, data)

    result = {"action": action, "key": key, **entry}
    print(json.dumps(result, ensure_ascii=False))


def cmd_load(args):
    data = load_data(args.data_dir)
    if not data:
        print(json.dumps([], ensure_ascii=False))
        return

    display_unit = normalize_unit(args.display_unit) if args.display_unit else None

    # Filter by date range
    entries = list(data.items())
    if args.from_date:
        entries = [(k, v) for k, v in entries if date_from_key(k) >= args.from_date]
    if args.to_date:
        entries = [(k, v) for k, v in entries if date_from_key(k) <= args.to_date]

    # Sort chronologically
    entries.sort(key=lambda x: x[0])

    # Limit to last N
    if args.last:
        entries = entries[-args.last:]

    # Build output
    result = []
    for key, entry in entries:
        val, u = extract_value_unit(entry)
        if val is None:
            continue  # skip unreadable/off-schema entries rather than crash
        if display_unit:
            val = convert_weight(val, u, display_unit)
            u = display_unit
        else:
            val = round(val, 1)
        result.append({
            "key": key,
            "date": date_from_key(key),
            "value": val,
            "unit": u
        })

    print(json.dumps(result, ensure_ascii=False))


def cmd_delete(args):
    data = load_data(args.data_dir)
    if args.key not in data:
        print(json.dumps({"error": f"Key not found: {args.key}"}, ensure_ascii=False))
        sys.exit(1)
    del data[args.key]
    save_data(args.data_dir, data)
    print(json.dumps({"action": "deleted", "key": args.key}, ensure_ascii=False))


def cmd_update(args):
    data = load_data(args.data_dir)
    if args.key not in data:
        print(json.dumps({"error": f"Key not found: {args.key}"}, ensure_ascii=False))
        sys.exit(1)

    value = catty_to_kg(args.value, args.unit)
    unit = normalize_unit(args.unit)
    data[args.key] = {"value": value, "unit": unit}
    save_data(args.data_dir, data)
    print(json.dumps({"action": "updated", "key": args.key, "value": value, "unit": unit}, ensure_ascii=False))


def cmd_set_unit(args):
    hp_path = Path(args.health_profile)
    if not hp_path.exists():
        print(json.dumps({"error": f"File not found: {args.health_profile}"}, ensure_ascii=False))
        sys.exit(1)

    unit = normalize_unit(args.unit)
    if unit not in ("kg", "lb"):
        print(json.dumps({"error": f"Invalid unit: {args.unit}. Must be kg or lb."}, ensure_ascii=False))
        sys.exit(1)

    content = hp_path.read_text(encoding="utf-8")

    # Replace the Unit Preference line
    pattern = r"(\*\*Unit Preference:\*\*\s*).+"
    replacement = rf"\g<1>{unit}"
    new_content, count = re.subn(pattern, replacement, content)

    if count == 0:
        # Try to add it under ## Body
        body_pattern = r"(## Body\n)"
        body_replacement = rf"\g<1>- **Unit Preference:** {unit}\n"
        new_content, count = re.subn(body_pattern, body_replacement, content)
        if count == 0:
            print(json.dumps({"error": "Could not find ## Body section in health-profile.md"}, ensure_ascii=False))
            sys.exit(1)

    hp_path.write_text(new_content, encoding="utf-8")
    print(json.dumps({"action": "unit_updated", "unit": unit}, ensure_ascii=False))


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Weight tracking CRUD")
    sub = parser.add_subparsers(dest="command")
    # 'required' kwarg not supported in Python 3.6; check manually below

    # save
    p_save = sub.add_parser("save")
    p_save.add_argument("--data-dir", required=True)
    p_save.add_argument("--value", type=float, required=True)
    p_save.add_argument("--unit", required=True)
    p_save.add_argument("--tz-offset", type=int, default=0, help="Timezone offset in seconds")
    p_save.add_argument("--correct", action="store_true", help="Force overwrite most recent entry")
    p_save.add_argument("--fasting", action="store_true", help="Mark this reading as a fasting/morning weigh-in")
    p_save.add_argument("--note", default=None, help="Optional free-text note for this entry")

    # load
    p_load = sub.add_parser("load")
    p_load.add_argument("--data-dir", required=True)
    p_load.add_argument("--display-unit", default=None)
    p_load.add_argument("--last", type=int, default=None)
    p_load.add_argument("--from", dest="from_date", default=None, help="Start date YYYY-MM-DD")
    p_load.add_argument("--to", dest="to_date", default=None, help="End date YYYY-MM-DD")

    # delete
    p_del = sub.add_parser("delete")
    p_del.add_argument("--data-dir", required=True)
    p_del.add_argument("--key", required=True)

    # update
    p_upd = sub.add_parser("update")
    p_upd.add_argument("--data-dir", required=True)
    p_upd.add_argument("--key", required=True)
    p_upd.add_argument("--value", type=float, required=True)
    p_upd.add_argument("--unit", required=True)

    # set-unit
    p_unit = sub.add_parser("set-unit")
    p_unit.add_argument("--health-profile", required=True)
    p_unit.add_argument("--unit", required=True)

    args = parser.parse_args()

    if not args.command:
        parser.error("command is required")

    if hasattr(args, "data_dir") and args.data_dir:
        args.data_dir = _normalize_path(args.data_dir)
        _validate_data_dir(args.data_dir)

    cmds = {
        "save": cmd_save,
        "load": cmd_load,
        "delete": cmd_delete,
        "update": cmd_update,
        "set-unit": cmd_set_unit,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
