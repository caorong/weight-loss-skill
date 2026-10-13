#!/usr/bin/env python3
"""Offline meal ledger for a native-vision agent. Python 3.10+, macOS/Linux.

The model supplies estimates; this module never calls a model or the network.
All writes use one lock, a recoverable journal, backups, and atomic replacement.
Do not run legacy writers against the same workspace concurrently.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NUTRIENTS = ("calories", "protein", "carbs", "fat")
MEALS = {"breakfast", "lunch", "dinner", "snack_am", "snack_pm"}
SOURCES = {"model_estimate", "package_label", "user_provided"}
PORTIONS = {"photo_estimate", "user_description", "user_measured", "package_serving"}
MAX_INPUT = 1_000_000


class LedgerError(ValueError):
    """An expected validation or data-integrity failure."""


def require(condition, message):
    if not condition:
        raise LedgerError(message)


def text(value, field, limit=1000):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit,
            f"{field}: expected nonempty text, at most {limit} characters")
    return value.strip()


def number(value, field, maximum=100_000):
    require(type(value) in (int, float) and math.isfinite(value)
            and 0 <= value <= maximum, f"{field}: expected finite nonnegative number <= {maximum}")
    return round(float(value), 4)


def day_value(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value),
            "date: use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LedgerError("date: invalid calendar date") from exc


def encode(value):
    try:
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise LedgerError("Input must be finite JSON values") from exc


def digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise LedgerError(f"Corrupt JSON; refusing to overwrite: {path}") from exc


def atomic_write(path, raw):
    """Write private files and fsync file + directory; never follow a symlink."""
    require(not path.is_symlink(), f"Refusing symlink: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".meal-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def food_value(raw, existing_id=None):
    require(isinstance(raw, dict), "food: expected object")
    allowed = {"name", "quantity", "unit", "source", "portion_source", "note", *NUTRIENTS}
    require(not set(raw) - allowed, f"Unknown food fields: {sorted(set(raw) - allowed)}")
    require(raw.get("unit") in {"g", "ml", "serving"}, "food.unit: g, ml or serving")
    require(raw.get("source") in SOURCES, "food.source: model_estimate, package_label or user_provided")
    require(raw.get("portion_source") in PORTIONS, "food.portion_source: explicit source required")
    quantity = number(raw.get("quantity"), "food.quantity")
    require(quantity > 0, "food.quantity must be positive")
    food = {"id": existing_id or "food_" + uuid.uuid4().hex,
            "name": text(raw.get("name"), "food.name", 200),
            "quantity": quantity, "unit": raw["unit"],
            "source": raw["source"], "portion_source": raw["portion_source"]}
    for key in NUTRIENTS:
        food[key] = number(raw.get(key), f"food.{key}")
    if raw.get("note"):
        food["note"] = text(raw["note"], "food.note")
    return food


def totals(items):
    return {key: round(sum(number(x.get(key, 0), key, 1_000_000_000) for x in items), 2)
            for key in NUTRIENTS}


def normalize_meals(raw, day):
    """Read old list logs without migrating the file until a successful mutation.

    Stable deterministic IDs make old records correctable after a read. Preserve
    unknown metadata; reject unsupported root formats rather than losing data.
    """
    require(isinstance(raw, list), "Daily meal file must be a JSON array; migrate other formats explicitly")
    aliases = {"cal": "calories", "p": "protein", "c": "carbs", "f": "fat",
               "protein_g": "protein", "carbs_g": "carbs", "fat_g": "fat"}
    result = copy.deepcopy(raw)
    for i, meal in enumerate(result):
        require(isinstance(meal, dict), "Invalid meal record")
        require(meal.get("status", "consumed") == "consumed" and meal.get("eaten", True) is not False,
                "Legacy planned/uneaten record detected; migrate it before reading intake totals")
        meal.setdefault("id", f"legacy_{day}_{i}")
        meal.setdefault("revision", 0)
        if "foods" not in meal and "items" in meal:
            meal["foods"] = meal.pop("items")
        require(isinstance(meal.get("foods", []), list), "foods must be a list")
        for obj in [meal] + meal.get("foods", []):
            require(isinstance(obj, dict), "Invalid food record")
            for old, new in aliases.items():
                if old in obj and new not in obj:
                    obj[new] = obj[old]
            for key in NUTRIENTS:
                if key in obj:
                    number(obj[key], key)
        foods = meal.get("foods", [])
        require(isinstance(foods, list), "foods must be a list")
        for j, food in enumerate(foods):
            food.setdefault("id", f"{meal['id']}_food_{j}")
        # Prefer complete item-level figures; retain legacy meal-level totals if
        # item detail is incomplete. Appending/updating such meals is rejected.
        for key in NUTRIENTS:
            if foods and all(key in f for f in foods):
                meal[key] = round(sum(f[key] for f in foods), 2)
            else:
                require(key in meal, f"Legacy meal missing {key}; correct its data before using totals")
        meal.setdefault("meal_type", meal.get("name", "unknown"))
    require(len({m["id"] for m in result}) == len(result), "Duplicate meal IDs in daily file")
    for meal in result:
        require(len({f["id"] for f in meal.get("foods", [])}) == len(meal.get("foods", [])),
                "Duplicate food IDs")
    return result


class Ledger:
    def __init__(self, workspace, tz_name):
        self.workspace = Path(workspace).expanduser().resolve()
        require(self.workspace.is_dir(), "Workspace must already exist; pass the agent's actual workspace")
        try:
            self.tz = ZoneInfo(tz_name)
        except (ValueError, ZoneInfoNotFoundError, TypeError) as exc:
            raise LedgerError("Supply a valid IANA timezone; install system tzdata if missing") from exc
        self.tz_name = tz_name
        self.data = self.workspace / "data"
        self.meals = self.data / "meals"
        self.state_path = self.data / "meal-ledger-state.json"
        self.lock_path = self.data / ".meal-ledger.lock"

    def safe(self, path):
        require(path.is_relative_to(self.workspace), "Path outside workspace")
        current = path
        while current != self.workspace:
            require(not current.is_symlink(), f"Refusing symlink in ledger path: {current}")
            current = current.parent
        return path

    @contextmanager
    def locked(self):
        try:
            import fcntl
        except ImportError as exc:
            raise LedgerError("File locking requires macOS/Linux (or WSL)") from exc
        self.safe(self.meals)
        self.meals.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.safe(self.lock_path), flags, 0o600)
        with os.fdopen(fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.safe(self.state_path)
            state = read_json(self.state_path) if self.state_path.exists() else {
                "version": 1, "requests": {}, "pending": None}
            require(isinstance(state, dict) and state.get("version") == 1
                    and isinstance(state.get("requests"), dict), "Invalid ledger state; restore backup")
            self.recover(state)
            yield state

    def day_path(self, day):
        day_value(day)
        return self.safe(self.meals / f"{day}.json")

    def load(self, day):
        path = self.day_path(day)
        return normalize_meals(read_json(path), day) if path.exists() else []

    def recover(self, state):
        """Finish an interrupted transaction before allowing another operation."""
        pending = state.get("pending")
        if not pending:
            return
        path = self.day_path(pending["date"])
        raw = path.read_bytes() if path.exists() else None
        after = encode(pending["after"])
        require(digest(raw) in {pending["before_sha"], digest(after)},
                "Pending transaction conflicts with an external edit; restore/reconcile manually")
        if digest(raw) != digest(after):
            if raw is not None:
                backup_dir = self.safe(self.meals / "backups" / pending["date"])
                backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomic_write(self.safe(backup_dir / (uuid.uuid4().hex + ".json")), raw)
            atomic_write(path, after)
        state["requests"][pending["key"]] = {
            "fingerprint": pending["fingerprint"], "result": pending["result"]}
        state["pending"] = None
        atomic_write(self.state_path, encode(state))

    def execute(self, action, payload=None, *, day=None, start=None, end=None, target=None):
        now = datetime.now(self.tz)
        today = now.date().isoformat()
        if target is not None:
            target = number(target, "target_calories")
            require(target > 0, "target_calories must be positive")
        with self.locked() as state:
            if action == "init":
                # Verify write capability without seeding fake meals or profiles.
                fd, probe = tempfile.mkstemp(prefix=".meal-probe-", dir=self.meals)
                os.close(fd)
                os.unlink(probe)
                return {"ok": True, "workspace": str(self.workspace), "timezone": self.tz_name,
                        "today": today, "yesterday": (now.date() - timedelta(days=1)).isoformat(),
                        "last_seven_days_start": (now.date() - timedelta(days=6)).isoformat(),
                        "now": now.isoformat(), "writable": True,
                        "request_token": uuid.uuid4().hex}
            if action == "list":
                selected = day or today
                records = self.load(selected)
                return {"ok": True, "date": selected, "meals": records,
                        "logged_totals": totals(records), "coverage": "recorded_only"}
            if action == "summary":
                first, last = day_value(start or day or today), day_value(end or day or today)
                require(0 <= (last - first).days <= 365, "Summary range must be 1..366 days")
                days = []
                for offset in range((last - first).days + 1):
                    selected = (first + timedelta(days=offset)).isoformat()
                    records = self.load(selected)
                    days.append({"date": selected, "record_count": len(records),
                                 "logged_totals": totals(records),
                                 "has_records": bool(records)})
                aggregate = totals([d["logged_totals"] for d in days])
                logged_days = sum(d["has_records"] for d in days)
                return {"ok": True, "days": days, "logged_days": logged_days,
                        "logged_totals": aggregate, "coverage": "recorded_only",
                        "average_per_logged_day": {k: round(v / logged_days, 2) for k, v in aggregate.items()}
                        if logged_days else None,
                        "target_calories": target,
                        "remaining_vs_logged": round(target - aggregate["calories"], 2)
                        if target is not None and len(days) == 1 else None}
            require(action in {"add", "append", "update", "delete"}, "Unknown action")
            require(isinstance(payload, dict), "Mutation input must be a JSON object")
            fields = {
                "add": {"meal_type", "foods", "consumed_at", "note"},
                "append": {"meal_id", "foods", "expected_revision"},
                "update": {"meal_id", "food_id", "food", "scale", "expected_revision"},
                "delete": {"meal_id", "food_id", "expected_revision"},
            }
            allowed = {"request_id", "date", "status"} | fields[action]
            require(not set(payload) - allowed, f"Unknown input fields: {sorted(set(payload) - allowed)}")
            request_id = text(payload.get("request_id"), "request_id", 256)
            # Fingerprint original request, not server-generated timestamp/UUID.
            # Retrying the same request after midnight remains idempotent.
            fingerprint = digest(encode({"action": action, "payload": payload, "timezone": self.tz_name}))
            key = digest(request_id.encode("utf-8"))
            prior = state["requests"].get(key)
            if prior:
                require(prior["fingerprint"] == fingerprint,
                        "request_id was already used with different input; use a new ID for a new operation")
                return {**prior["result"], "duplicate": True}
            selected = payload.get("date", today)
            require(day_value(selected) <= now.date(), "Future meals are not consumed intake")
            require(payload.get("status", "consumed") == "consumed", "Only consumed meals may be saved")
            records = self.load(selected)
            result_id = None
            if action == "add":
                require(payload.get("meal_type") in MEALS, "Unsupported meal_type")
                require(not set(payload) & {"meal_id", "food_id", "food", "scale", "expected_revision"},
                        "add: unexpected correction fields")
                foods = self.foods(payload.get("foods"))
                eaten_at = payload.get("consumed_at")
                if eaten_at is not None:
                    try:
                        dt = datetime.fromisoformat(eaten_at.replace("Z", "+00:00"))
                    except (ValueError, AttributeError) as exc:
                        raise LedgerError("consumed_at: expected ISO timestamp with timezone") from exc
                    require(dt.tzinfo is not None and dt.utcoffset() is not None,
                            "consumed_at must include timezone")
                    require(dt.astimezone(self.tz).date().isoformat() == selected,
                            "consumed_at does not match date in the chosen timezone")
                    require(dt <= now, "consumed_at cannot be in the future")
                record = {"id": "meal_" + uuid.uuid4().hex, "revision": 1,
                          "name": payload["meal_type"], "meal_type": payload["meal_type"],
                          "status": "consumed", "date": selected, "consumed_at": eaten_at,
                          "recorded_at": now.isoformat(), "timezone": self.tz_name,
                          "source_request_id": request_id, "foods": foods, **totals(foods)}
                if payload.get("note"):
                    record["note"] = text(payload["note"], "note")
                records.append(record)
                result_id = record["id"]
            else:
                require(not set(payload) & {"meal_type", "consumed_at"},
                        "Changing meal type/date/time is not supported; delete and re-add explicitly")
                meal_id = text(payload.get("meal_id"), "meal_id", 256)
                matches = [m for m in records if m["id"] == meal_id]
                require(len(matches) == 1, "meal_id not found on this date; list records first")
                record = matches[0]
                result_id = record["id"]
                if "expected_revision" in payload:
                    require(type(payload["expected_revision"]) is int
                            and payload["expected_revision"] == record["revision"],
                            "Revision conflict; reload before correcting")
                if action == "delete" and "food_id" not in payload:
                    require(not set(payload) & {"food", "foods", "scale"}, "delete: unexpected food data")
                    records.remove(record)
                else:
                    foods = record.get("foods", [])
                    require(foods and all(all(k in f for k in NUTRIENTS) for f in foods),
                            "Legacy meal has incomplete item nutrients; replace the whole meal explicitly")
                    if action == "append":
                        require(not set(payload) & {"food", "food_id", "scale"}, "append: unexpected correction fields")
                        foods.extend(self.foods(payload.get("foods")))
                    else:
                        require("foods" not in payload, "Use food for a single-item replacement")
                        food_id = text(payload.get("food_id"), "food_id", 256)
                        indices = [i for i, f in enumerate(foods) if f["id"] == food_id]
                        require(len(indices) == 1, "food_id not found; list records first")
                        index = indices[0]
                        if action == "delete":
                            require(not set(payload) & {"food", "scale"}, "delete: unexpected correction data")
                            foods.pop(index)
                        elif "scale" in payload:
                            require("food" not in payload, "Use either scale or food, not both")
                            scale = number(payload["scale"], "scale", 100)
                            require(scale > 0, "scale must be positive; use delete to remove food")
                            food = foods[index]
                            food["quantity"] = number(number(food.get("quantity"), "existing food.quantity") * scale, "quantity")
                            for nutrient in NUTRIENTS:
                                food[nutrient] = number(food[nutrient] * scale, nutrient)
                            food["portion_source"] = "user_description"
                        else:
                            foods[index] = food_value(payload.get("food"), food_id)
                    if foods:
                        record.update(totals(foods))
                        record["revision"] += 1
                        record["updated_at"] = now.isoformat()
                    else:
                        records.remove(record)
            result = {"ok": True, "saved": True, "duplicate": False, "action": action,
                      "date": selected, "meal_id": result_id,
                      "meal": next((m for m in records if m["id"] == result_id), None),
                      "logged_totals": totals(records), "coverage": "recorded_only"}
            path = self.day_path(selected)
            state["pending"] = {"date": selected,
                                "before_sha": digest(path.read_bytes()) if path.exists() else None,
                                "after": records, "key": key, "fingerprint": fingerprint,
                                "result": result}
            # Write-ahead intent and day replacement are recovered under the
            # same lock. A lost process cannot create a committed-but-untracked write.
            atomic_write(self.state_path, encode(state))
            self.recover(state)
            return result

    @staticmethod
    def foods(raw):
        require(isinstance(raw, list) and 1 <= len(raw) <= 100, "foods: expected 1..100 items")
        return [food_value(food) for food in raw]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "add", "append", "update", "delete", "list", "summary"])
    parser.add_argument("--workspace", required=True, help="Actual agent workspace (already existing)")
    parser.add_argument("--tz", required=True, help="IANA timezone, e.g. Asia/Tokyo; never server timezone")
    parser.add_argument("--input", default="-", help="Mutation JSON file, or - for stdin")
    parser.add_argument("--date", help="Read date only; mutation date belongs in JSON")
    parser.add_argument("--start", help="Inclusive summary start date")
    parser.add_argument("--end", help="Inclusive summary end date")
    parser.add_argument("--target-calories", type=float, help="Explicit confirmed target; never inferred")
    args = parser.parse_args()
    try:
        payload = None
        if args.action in {"add", "append", "update", "delete"}:
            require(not any((args.date, args.start, args.end, args.target_calories is not None)),
                    "Mutation dates go in input JSON; query flags are not accepted")
            if args.input == "-":
                raw = sys.stdin.read(MAX_INPUT + 1)
            else:
                with open(args.input, encoding="utf-8") as handle:
                    raw = handle.read(MAX_INPUT + 1)
            require(len(raw) <= MAX_INPUT, "Input too large")
            payload = json.loads(raw)
        elif args.action != "summary":
            require(not any((args.start, args.end, args.target_calories is not None)), "Summary-only flags")
        result = Ledger(args.workspace, args.tz).execute(
            args.action, payload, day=args.date, start=args.start, end=args.end, target=args.target_calories)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (LedgerError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "recovery": "Do not claim saved. For interrupted writes, retry the exact request ID/input; do not use a new ID."},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
