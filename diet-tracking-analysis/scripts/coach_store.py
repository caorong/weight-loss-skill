"""Private local coach transactions; no network, gateway writes, or model calls.

A separate lock and write-ahead journal protect profiles, plans and measurements.
Lock order for reports: coach first, meal ledger second. Never reverse this order.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path


class CoachError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise CoachError(message)


def packed(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise CoachError("Values must be finite JSON data") from exc


def sha(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def atomic(path, raw):
    need(not path.is_symlink(), f"Refusing symlink: {path}")
    fd, name = tempfile.mkstemp(prefix=".coach-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    OWNED = {"health-profile.md", "health-preferences.md", "PLAN.md", "MEAL-PLAN.md",
             "EXERCISE-PLAN.md", "data/weight.json", "data/exercise.json",
             "data/plans.json", "data/coach-reviews.json", "data/coach-reminders.json", "data/coach-review-deliveries.json"}

    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().resolve()
        need(self.workspace.is_dir(), "Pass an existing actual agent workspace")
        self.state_path = self.safe("data/coach-state.json")
        self.state = None

    def safe(self, relative):
        path = self.workspace / relative
        need(path.is_relative_to(self.workspace) and ".." not in path.parts,
             "Path outside workspace")
        cursor = path
        while cursor != self.workspace:
            need(not cursor.is_symlink(), f"Refusing symlink: {cursor}")
            cursor = cursor.parent
        return path

    def owned(self, relative):
        need(relative in self.OWNED or re.fullmatch(
            r"data/reports/coach-review(?:-daily|-weekly)?-\d{4}-\d{2}-\d{2}-\d{4}-\d{2}-\d{2}\.md", relative),
            f"Not a coach-owned destination: {relative}")
        return self.safe(relative)

    def read_text(self, relative):
        path = self.safe(relative)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def read(self, relative, default):
        raw = self.read_text(relative)
        if not raw:
            need(not self.safe(relative).exists(), f"Empty/corrupt file: {relative}")
            return copy.deepcopy(default)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise CoachError(f"Corrupt file; never replace with empty data: {relative}") from exc
        need(type(value) is type(default), f"Unsupported root format: {relative}")
        return value

    @contextmanager
    def locked(self):
        import fcntl
        self.safe("data").mkdir(mode=0o700, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.safe("data/.coach.lock"), flags, 0o600)
        with os.fdopen(fd, "r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.state = self.read("data/coach-state.json", {"version": 1, "requests": {}, "pending": None})
            need(self.state.get("version") == 1 and isinstance(self.state.get("requests"), dict),
                 "Unsupported coach-state; restore/reconcile, do not reset")
            self.recover()
            yield self

    def receipt(self, action, payload):
        need(isinstance(payload, dict), "Input must be a JSON object")
        token = payload.get("request_id")
        need(isinstance(token, str) and 0 < len(token) <= 256, "A stable request_id is required")
        key = sha(token.encode())
        fingerprint = sha(packed({"action": action, "payload": payload}).encode())
        old = self.state["requests"].get(key)
        if old:
            need(old["fingerprint"] == fingerprint, "request_id conflict; new operations need new IDs")
            return key, fingerprint, {**old["result"], "duplicate": True}
        return key, fingerprint, None

    def commit(self, key, fingerprint, updates, result):
        changes = []
        for relative, content in updates.items():
            path = self.owned(relative)
            before = path.read_bytes() if path.exists() else None
            need(isinstance(content, str), "Transaction content must be text")
            changes.append({"path": relative, "before": sha(before), "after": content})
        result = {"ok": True, "saved": True, "duplicate": False, **result}
        self.state["pending"] = {"id": uuid.uuid4().hex, "changes": changes,
                                 "key": key, "fingerprint": fingerprint, "result": result}
        atomic(self.state_path, packed(self.state).encode())
        self.recover()
        return result

    def recover(self):
        tx = self.state.get("pending")
        if not tx:
            return
        # Validate ALL preimages first; fail closed instead of clobbering an external edit.
        for change in tx["changes"]:
            path = self.owned(change["path"])
            raw = path.read_bytes() if path.exists() else None
            need(sha(raw) in {change["before"], sha(change["after"].encode())},
                 f"Pending coach transaction conflicts with external edit: {change['path']}")
        for change in tx["changes"]:
            path = self.owned(change["path"])
            raw = path.read_bytes() if path.exists() else None
            after = change["after"].encode()
            if sha(raw) == sha(after):
                continue
            if raw is not None:
                backup = self.safe(f"data/coach-backups/{tx['id']}/{change['path']}")
                backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                atomic(backup, raw)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic(path, after)
        self.state["requests"][tx["key"]] = {"fingerprint": tx["fingerprint"], "result": tx["result"]}
        self.state["pending"] = None
        atomic(self.state_path, packed(self.state).encode())


def markers(kind):
    return f"<!-- standalone-coach:{kind}:begin -->", f"<!-- standalone-coach:{kind}:end -->"


def section(raw, kind):
    start, end = markers(kind)
    if start not in raw and end not in raw:
        return None
    need(raw.count(start) == raw.count(end) == 1 and raw.index(start) < raw.index(end),
         f"Malformed managed section {kind}; reconcile manually")
    return raw.split(start, 1)[1].split(end, 1)[0]


def replace_section(raw, kind, body):
    start, end = markers(kind)
    old = section(raw, kind)
    block = f"{start}\n{body.rstrip()}\n{end}"
    if old is None:
        return raw + ("\n\n" if raw else "") + block + "\n"
    a, rest = raw.split(start, 1)
    _, b = rest.split(end, 1)
    return a + block + b


def read_fields(raw, kind):
    block = section(raw, kind)
    if block is None:
        return {}
    fields = {}
    for line in block.splitlines():
        if line.startswith("- "):
            try:
                key, encoded = line[2:].split(": ", 1)
                need(key not in fields, f"Duplicate profile field: {key}")
                fields[key] = json.loads(encoded)
            except (ValueError, TypeError) as exc:
                raise CoachError(f"Corrupt managed profile field in {kind}") from exc
    return fields


def fields_text(fields, title):
    return "## " + title + "\n\n" + "\n".join(
        f"- {k}: {json.dumps(v, ensure_ascii=False, allow_nan=False)}" for k, v in sorted(fields.items()))
