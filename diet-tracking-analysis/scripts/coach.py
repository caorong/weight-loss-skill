#!/usr/bin/env python3
"""Local coach: profiles, measurements, plans, reviews and native reminder specs.

No network or additional model call. Gateway operations are executed by the
OpenClaw agent using its real native tool, then verified with reminder-bind.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import re
import statistics
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True

from coach_store import (CoachError, Store, fields_text, need, packed, read_fields,
                         replace_section, sha)

spec = importlib.util.spec_from_file_location("standalone_meals", Path(__file__).with_name("meal-log.py"))
meals_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(meals_module)

KINDS = {"goal": "PLAN.md", "meal": "MEAL-PLAN.md", "exercise": "EXERCISE-PLAN.md"}
PROFILE_NUMBERS = {"height_cm": (30, 300), "age_years": (0, 120), "target_weight_kg": (5, 600)}
PREF_LISTS = {"allergies", "dislikes", "likes", "dietary_restrictions", "exercise_preferences", "notes"}
PROFILE_LISTS = {"health_flags", "injuries", "medications", "notes"}
TARGETS = {"calories", "protein", "carbs", "fat"}
ACTIONS = {"context", "profile-set", "preferences-set", "weight-add", "weight-update", "weight-delete",
           "exercise-add", "exercise-update", "exercise-delete", "measurements", "plan-save", "plan-activate",
           "plans", "review", "review-save", "reminder-set", "reminders", "reminder-specs", "reminder-sync-plan",
           "reminder-bind", "reminder-gate", "reminder-onboarding", "reminder-onboarding-set"}
READS = {"context", "measurements", "plans", "review", "reminders", "reminder-specs", "reminder-gate", "reminder-onboarding"}
FIELDS = {
    "profile-set": {"fields", "expected_revision"}, "preferences-set": {"fields", "expected_revision"},
    "weight-add": {"value", "unit", "date", "observed_at", "fasting", "note"},
    "weight-update": {"id", "expected_revision", "value", "unit", "fasting", "note"},
    "weight-delete": {"id", "expected_revision"},
    "exercise-add": {"date", "items"}, "exercise-update": {"id", "expected_revision", "item"},
    "exercise-delete": {"id", "expected_revision"},
    "plan-save": {"kind", "body", "targets", "effective_date", "basis_review_id"},
    "plan-activate": {"plan_id", "confirmed"},
    "review-save": {"start", "end", "expected_snapshot", "analysis", "review_type"},
    "reminder-set": {"key", "settings", "expected_revision", "confirmed"},
    "reminder-bind": {"key", "observed_job", "spec_hash"},
    "reminder-onboarding-set": {"offered", "decisions", "expected_revision", "confirmed"},
}


def label(value, field, limit=1000):
    need(isinstance(value, str) and 0 < len(value.strip()) <= limit, f"{field}: expected nonempty text <= {limit}")
    need("<!-- standalone-coach:" not in value, "Reserved managed-section markers are not user data")
    return value.strip()


def numeric(value, field, lo=0, hi=100000):
    need(type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi,
         f"{field}: finite number in [{lo}, {hi}] required")
    return round(float(value), 4)


def revision(payload, record):
    need(type(payload.get("expected_revision")) is int and
         payload["expected_revision"] == record.get("revision", 0), "Revision conflict; read latest data first")


def iso_day(value):
    need(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "Use YYYY-MM-DD")
    return date.fromisoformat(value)


def weight_kg(value, unit):
    factor = {"kg": 1, "公斤": 1, "lb": 0.45359237, "lbs": 0.45359237, "斤": 0.5}.get(unit)
    need(factor is not None, "Weight unit must be kg, lb/lbs, 公斤 or 斤")
    return numeric(numeric(value, "weight", 0.1, 1500) * factor, "weight_kg", 0.1, 600)


def native_view(job):
    return {k: copy.deepcopy(job[k]) for k in ("name", "agentId", "enabled", "schedule", "sessionTarget", "payload", "delivery") if k in job}


class Coach:
    def __init__(self, workspace, tz, clock=None):
        self.store = Store(workspace)
        self.tz = ZoneInfo(tz)
        self.tz_name = tz
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.now = None
        self.owner = sha(str(self.store.workspace).encode())[:12]

    def day(self, value=None, *, future=False):
        today = self.now.date()
        result = today if value in (None, "today") else today - timedelta(days=1) if value == "yesterday" else iso_day(value)
        need(future or result <= today, "Future dates are not completed intake or exercise")
        return result.isoformat()

    def window(self, start=None, end=None, period="last7"):
        today = self.now.date()
        if start or end:
            need(start is not None and end is not None, "Pass both start and end")
            a, b = iso_day(self.day(start)), iso_day(self.day(end))
        elif period == "today":
            a = b = today
        elif period == "yesterday":
            a = b = today - timedelta(days=1)
        elif period == "this-week":
            a, b = today - timedelta(days=today.weekday()), today
        elif period == "last-week":
            b = today - timedelta(days=today.weekday() + 1)
            a = b - timedelta(days=6)
        else:
            need(period in ("last7", "last14"), "Unsupported period")
            a, b = today - timedelta(days=6 if period == "last7" else 13), today
        need(0 <= (b - a).days < 366, "Range must be 1..366 days")
        return a.isoformat(), b.isoformat()

    def fields(self, kind):
        path = "health-profile.md" if kind == "profile" else "health-preferences.md"
        return read_fields(self.store.read_text(path), kind)

    def plans(self):
        data = self.store.read("data/plans.json", {"version": 1, "entries": []})
        need(data.get("version") == 1 and isinstance(data.get("entries"), list), "Invalid plans registry")
        return data

    @staticmethod
    def active(plans, kind, on_date):
        candidates = [p for p in plans["entries"] if p["kind"] == kind and p["status"] == "confirmed" and p["effective_date"] <= on_date]
        return max(candidates, key=lambda p: (p["effective_date"], p["activated_at"], p["sequence"])) if candidates else None

    def weight_rows(self):
        raw = self.store.read("data/weight.json", {})
        rows = []
        for key, row in raw.items():
            need(isinstance(row, dict) and "value" in row, "Unsupported legacy weight; migrate explicitly")
            day = row.get("date", key[:10])
            iso_day(day)
            rows.append({**row, "id": row.get("id", "legacy_weight_" + sha(key.encode())[:24]),
                         "revision": row.get("revision", 0), "date": day,
                         "value": weight_kg(row["value"], row.get("unit", "kg")), "unit": "kg", "storage_key": key})
        return sorted(rows, key=lambda r: (r["date"], r.get("observed_at") or r.get("recorded_at") or r["storage_key"]))

    def exercise_data(self, *, normalize=False):
        # Caller holds the coach lock. Validate the whole file before assigning
        # identities; existing (including legacy-shaped) IDs are never rewritten.
        raw = self.store.read("data/exercise.json", {})
        ids, missing = set(), []
        for day, block in raw.items():
            iso_day(day)
            need(isinstance(block, dict) and isinstance(block.get("exercises"), list), "Unsupported exercise schema")
            for row in block["exercises"]:
                need(isinstance(row, dict), "Invalid exercise row")
                numeric(row.get("duration_min"), "duration_min", 0.1, 1440)
                if "id" not in row:
                    missing.append(row)
                else:
                    need(isinstance(row["id"], str) and row["id"].strip(), "Invalid exercise ID; reconcile explicitly")
                    need(row["id"] not in ids, "Duplicate exercise ID; reconcile explicitly")
                    ids.add(row["id"])
        if normalize and missing:
            for row in missing:
                identity = "exercise_" + uuid.uuid4().hex
                while identity in ids:
                    identity = "exercise_" + uuid.uuid4().hex
                row["id"] = identity
                ids.add(identity)
            content = packed(raw)
            # Separate from the requested mutation: even a rejected/stale request
            # must leave IDs durable, and recovery must replay these exact UUIDs.
            self.store.commit("exercise-id-normalize:" + uuid.uuid4().hex, sha(content.encode()),
                              {"data/exercise.json": content},
                              {"action": "exercise-id-normalize", "assigned_ids": len(missing)})
        return raw

    def exercise_rows(self, raw=None):
        raw = self.exercise_data() if raw is None else raw
        # Positional IDs are temporary read references only; mutations reject them.
        return [{**row, "id": row.get("id", f"legacy_exercise_{day}_{i}"),
                 "revision": row.get("revision", 0), "date": day, "index": i}
                for day, block in raw.items() for i, row in enumerate(block["exercises"])]

    def reminders(self):
        data = self.store.read("data/coach-reminders.json", {"version": 1, "revision": 0, "items": {}})
        need(data.get("version") == 1 and isinstance(data.get("items"), dict), "Invalid reminders registry")
        return data

    def reminder_onboarding(self):
        from coach_reminder_onboarding import view
        return view(self.reminders())

    def reminder_spec(self, key, settings):
        route = settings["route"]
        hour, minute = map(int, settings["time"].split(":"))
        message = f"Run diet-tracking-analysis reminder-gate for {key} (owner={self.owner})."
        if settings.get("review_type"):
            message += f" Review type: {settings['review_type']}."
        return {"name": f"local-coach-{self.owner}-{key}", "agentId": route["agent"],
                "enabled": settings["enabled"], "schedule": {"kind": "cron", "expr": f"{minute} {hour} * * {','.join(map(str, settings['weekdays']))}", "tz": self.tz_name},
                "sessionTarget": "isolated", "payload": {"kind": "agentTurn", "message": message},
                "delivery": {"mode": "announce", "channel": route["channel"], "to": route["to"]}}

    def specs(self):
        return [{"key": k, "job": self.reminder_spec(k, s),
                 "spec_hash": sha(packed(self.reminder_spec(k, s)).encode()), "binding": s.get("binding")}
                for k, s in sorted(self.reminders()["items"].items())]

    def report(self, a, b, review_type="custom"):
        need(review_type in {"daily", "weekly", "custom"}, "Unsupported review type")
        need(review_type != "daily" or a == b, "Daily review requires one local date")
        need(review_type != "weekly" or (iso_day(a).weekday() == 0 and iso_day(b).weekday() == 6 and (iso_day(b) - iso_day(a)).days == 6), "Weekly review requires a complete Monday-Sunday range")
        need(review_type != "weekly" or iso_day(b) < self.now.date(), "Weekly review requires a closed calendar week; use custom for partial weeks")
        # One coach snapshot; the meal ledger takes its own lock second.
        meal_ledger = meals_module.Ledger(self.store.workspace, self.tz_name)
        with meal_ledger.locked():
            meal_days = []
            cursor = iso_day(a)
            while cursor <= iso_day(b):
                day = cursor.isoformat()
                rows = meal_ledger.load(day)
                meal_days.append({"date": day, "has_records": bool(rows), "recorded_meal_types": sorted({r.get("meal_type", "unknown") for r in rows}),
                                  "totals": meals_module.totals(rows), "records": rows})
                cursor += timedelta(days=1)
        weights = [r for r in self.weight_rows() if a <= r["date"] <= b]
        exercises = [r for r in self.exercise_rows() if a <= r["date"] <= b]
        plans = self.plans()
        for day in meal_days:
            plan = self.active(plans, "goal", day["date"])
            day["goal_plan_id"] = plan["id"] if plan else None
            day["target_calories"] = plan.get("targets", {}).get("calories") if plan else None
        daily_weights = []
        for day in sorted({r["date"] for r in weights}):
            candidates = [r for r in weights if r["date"] == day]
            fasting = [r for r in candidates if r.get("fasting") is True]
            chosen = fasting or candidates
            daily_weights.append({"date": day, "kg": round(statistics.median(r["value"] for r in chosen), 3),
                                  "method": "fasting_median" if fasting else "all_readings_median", "readings": len(chosen)})
        logged = sum(d["has_records"] for d in meal_days)
        total = meals_module.totals([d["totals"] for d in meal_days])
        known_net = [numeric(r["net_calories_kcal"], "net_calories_kcal") for r in exercises if r.get("net_calories_kcal") is not None]
        summary = {"logged_days": logged, "period_days": len(meal_days), "recorded_totals": total,
                   "average_per_logged_day": {k: round(v / logged, 2) for k, v in total.items()} if logged else None,
                   "weight_days": len(daily_weights), "daily_weights": daily_weights,
                   "weight_change_kg": round(daily_weights[-1]["kg"] - daily_weights[0]["kg"], 3) if len(daily_weights) >= 2 else None,
                   "exercise_sessions": len(exercises), "exercise_minutes": round(sum(r["duration_min"] for r in exercises), 2),
                   "known_exercise_net_kcal": round(sum(known_net), 2), "exercise_calorie_unknown_sessions": len(exercises) - len(known_net),
                   "coverage": "recorded_only", "automatic_target_change": False}
        midpoint = iso_day(b) - timedelta(days=6)
        previous_start = iso_day(b) - timedelta(days=13)
        # Always compare adjacent seven-day buckets ending at b, even when the
        # requested report spans a month. Older readings cannot fill sparse weeks.
        previous = [w["kg"] for w in daily_weights if previous_start <= iso_day(w["date"]) < midpoint]
        recent = [w["kg"] for w in daily_weights if iso_day(w["date"]) >= midpoint]
        summary["two_week_mean_change_kg"] = round(statistics.mean(recent) - statistics.mean(previous), 3) if len(previous) >= 3 and len(recent) >= 3 else None
        summary["review_readiness"] = "discussion_only" if logged >= 5 and len(daily_weights) >= 3 else "insufficient_data"
        report = {"start": a, "end": b, "timezone": self.tz_name, "meal_days": meal_days, "weights": weights,
                  "exercises": exercises, "summary": summary,
                  "profiles": {"profile": self.fields("profile"), "preferences": self.fields("preferences")},
                  "current_plans": {k: self.active(plans, k, b) for k in KINDS}}
        from coach_review import enrich_report
        report = enrich_report(self, report, review_type)
        snapshot = copy.deepcopy(report)
        snapshot.pop("cutoff_at", None)
        report["snapshot"] = sha(packed(snapshot).encode())
        return report

    def execute(self, action, payload=None, *, start=None, end=None, period=None, key=None, review_type="custom"):
        need(action in ACTIONS, "Unsupported action")
        self.now = self.clock().astimezone(self.tz)  # Never cache time across operations.
        today = self.now.date().isoformat()
        with self.store.locked():
            if action == "context":
                token = uuid.uuid4().hex
                request_dir = self.store.safe("data/requests")
                request_dir.mkdir(mode=0o700, exist_ok=True)
                plans = self.plans()
                return {"ok": True, "owner": self.owner, "now": self.now.isoformat(), "today": today, "yesterday": self.day("yesterday"),
                        "last7": list(self.window()), "last14": list(self.window(period="last14")),
                        "this_week": list(self.window(period="this-week")), "last_week": list(self.window(period="last-week")),
                        "request_token": token, "request_file": str(request_dir / (token + ".json")),
                        "profile": self.fields("profile"), "preferences": self.fields("preferences"),
                        "active_plans": {k: self.active(plans, k, today) for k in KINDS},
                        "scheduled_plans": [p for p in plans["entries"] if p["status"] == "confirmed" and p["effective_date"] > today],
                        "latest_weight": (self.weight_rows() or [None])[-1],
                        "reminders_revision": self.reminders()["revision"],
                        "reminder_onboarding": self.reminder_onboarding()}
            if action == "measurements":
                a, b = self.window(start, end, period or "last7")
                return {"ok": True, "start": a, "end": b, "weights": [r for r in self.weight_rows() if a <= r["date"] <= b],
                        "exercises": [r for r in self.exercise_rows() if a <= r["date"] <= b]}
            if action == "plans":
                data = self.plans()
                return {"ok": True, **data, "active": {k: self.active(data, k, today) for k in KINDS}}
            if action == "review":
                chosen = period or {"daily": "today", "weekly": "last-week", "custom": "last7"}.get(review_type)
                return {"ok": True, **self.report(*self.window(start, end, chosen), review_type=review_type)}
            if action == "reminders":
                return {"ok": True, **self.reminders(), "native_status": "verify_with_gateway"}
            if action == "reminder-onboarding":
                return {"ok": True, **self.reminder_onboarding()}
            if action == "reminder-specs":
                return {"ok": True, "specs": self.specs(), "native_status": "not_applied"}
            if action == "reminder-gate":
                return self.gate(key)
            if action == "reminder-sync-plan":
                return self.reconcile(payload)
            need(isinstance(payload, dict), "Mutation requires JSON object")
            need(not set(payload) - ({"request_id"} | FIELDS[action]), "Unknown mutation field(s)")
            rkey, fingerprint, prior = self.store.receipt(action + ":" + self.tz_name, payload)
            if prior:
                return prior
            updates, result = self.mutate(action, payload)
            return self.store.commit(rkey, fingerprint, updates, {"action": action, **result})

    def mutate(self, action, p):
        if action in {"profile-set", "preferences-set"}:
            kind = "profile" if action == "profile-set" else "preferences"
            path = "health-profile.md" if kind == "profile" else "health-preferences.md"
            raw = self.store.read_text(path)
            old = self.fields(kind)
            revision(p, old)
            changes = p.get("fields")
            need(isinstance(changes, dict) and changes, "fields must be a nonempty patch")
            allowed = set(PROFILE_NUMBERS) | PROFILE_LISTS | {"biological_sex", "activity_level", "display_name"} if kind == "profile" else PREF_LISTS | {"cooking", "schedule", "usual_portions"}
            need(not set(changes) - allowed, "Unknown profile/preference field")
            for name, value in changes.items():
                if value is None:
                    old.pop(name, None)
                elif name in PROFILE_NUMBERS:
                    old[name] = numeric(value, name, *PROFILE_NUMBERS[name])
                elif name in PROFILE_LISTS | PREF_LISTS:
                    need(isinstance(value, list) and len(value) <= 100, f"{name}: list required")
                    old[name] = [label(v, name) for v in value]
                else:
                    old[name] = label(value, name, 4000)
            old.update({"revision": old.get("revision", 0) + 1, "updated_at": self.now.isoformat()})
            return {path: replace_section(raw, kind, fields_text(old, "Local coach " + kind))}, {kind: old}
        if action.startswith("weight-"):
            return self.mutate_weight(action, p)
        if action.startswith("exercise-"):
            return self.mutate_exercise(action, p)
        if action.startswith("plan-"):
            return self.mutate_plan(action, p)
        if action == "review-save":
            a, b = self.window(p.get("start"), p.get("end"))
            report = self.report(a, b, p.get("review_type", "custom"))
            need(p.get("expected_snapshot") == report["snapshot"], "Review data changed; recompute before saving analysis")
            analysis = label(p.get("analysis"), "analysis", 20000)
            history = self.store.read("data/coach-reviews.json", {"version": 1, "entries": []})
            record = {"id": "review_" + uuid.uuid4().hex, "created_at": self.now.isoformat(), "report": report, "analysis": analysis}
            history["entries"].append(record)
            s = report["summary"]
            suffix = "" if report["review_type"] == "custom" else "-" + report["review_type"]
            path = f"data/reports/coach-review{suffix}-{a}-{b}.md"
            body = (f"# Coach {report['review_type']} review {a} — {b}\n\nID: {record['id']}\nTimezone: {self.tz_name}\n"
                    f"Cutoff: {report['cutoff_at']}\nCoverage: recorded only; full-day completeness unverified.\n"
                    f"\nRecorded meal days: {s['logged_days']}/{s['period_days']}\n"
                    f"Average per logged day (not complete intake): {s['average_per_logged_day']}\n"
                    f"Weight days: {s['weight_days']}\nExercise minutes: {s['exercise_minutes']}\n"
                    f"\n{analysis}\n\nEstimates and incomplete records; no automatic target change.\n")
            return {"data/coach-reviews.json": packed(history), path: body}, {"review_id": record["id"], "path": path, "summary": s}
        if action == "reminder-onboarding-set":
            from coach_reminder_onboarding import update
            data = self.reminders()
            revision(p, data)
            return update(self, data, p)
        if action == "reminder-set":
            data = self.reminders()
            revision(p, data)
            key = self.reminder_key(p.get("key"))
            patch = p.get("settings")
            need(isinstance(patch, dict), "settings patch required")
            need(not set(patch) - {"enabled", "kind", "time", "weekdays", "route", "pause_until", "review_type"}, "Unknown reminder settings")
            old = data["items"].get(key, {})
            setting = {**old, **patch}
            need(type(setting.get("enabled")) is bool, "enabled must be explicit boolean")
            need(p.get("confirmed") is True or setting["enabled"] is False, "Enabling/changing reminders requires user confirmation")
            need(setting.get("kind") in {"breakfast", "lunch", "dinner", "weight", "exercise", "review"}, "Unsupported reminder kind")
            if setting["kind"] == "review":
                # Old entries remain legacy rolling reviews; no silent schedule migration.
                legacy = old.get("kind") == "review" and "review_type" not in old
                need("review_type" in setting or legacy, "New review reminder requires explicit daily or weekly review_type")
                if "review_type" in setting:
                    need(setting["review_type"] in {"daily", "weekly"}, "review_type: daily or weekly")
            else:
                need("review_type" not in setting, "review_type applies only to review reminders")
            need(isinstance(setting.get("time"), str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", setting["time"]), "time must be HH:MM")
            weekdays = setting.get("weekdays")
            need(isinstance(weekdays, list) and weekdays and all(type(d) is int and 0 <= d <= 6 for d in weekdays), "weekdays: 0=Sun..6=Sat")
            setting["weekdays"] = sorted(set(weekdays))
            if setting.get("review_type") == "daily":
                need(setting["weekdays"] == list(range(7)), "Daily reviews run every local day")
            if setting.get("review_type") == "weekly":
                need(len(setting["weekdays"]) == 1, "Weekly reviews require one chosen weekday")
            route = setting.get("route")
            need(isinstance(route, dict) and set(route) == {"agent", "channel", "to"}, "Explicit agent/channel/to required (single resolved account)")
            setting["route"] = {k: label(v, "route." + k, 1000) for k, v in route.items()}
            need(route["channel"] != "last", "Use an explicit channel, not last")
            if setting.get("pause_until") is not None:
                self.timestamp(setting["pause_until"], allow_future=True)
            # Keep old binding for reconciliation/audit, but it is NOT proof of current registration.
            setting["updated_at"] = self.now.isoformat()
            data["items"][key] = setting
            data["revision"] += 1
            return {"data/coach-reminders.json": packed(data)}, {"revision": data["revision"], "key": key, "native_status": "sync_required"}
        if action == "reminder-bind":
            data = self.reminders()
            key = self.reminder_key(p.get("key"))
            need(key in data["items"], "Unknown reminder")
            job = self.reminder_spec(key, data["items"][key])
            digest = sha(packed(job).encode())
            observed = p.get("observed_job")
            need(p.get("spec_hash") == digest, "Reminder changed since gateway sync")
            need(isinstance(observed, dict) and observed.get("id") and self.job_matches(observed, job), "Read back the actual gateway job and match its spec")
            data["items"][key]["binding"] = {"job_id": str(observed["id"]), "spec_hash": digest, "verified_at": self.now.isoformat()}
            data["revision"] += 1
            return {"data/coach-reminders.json": packed(data)}, {"native_status": "registered_verified_at", "binding": data["items"][key]["binding"]}
        raise CoachError("Unsupported mutation")

    def timestamp(self, value, allow_future=False):
        need(isinstance(value, str), "timestamp must be ISO text")
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        need(dt.tzinfo is not None, "Timestamp requires timezone")
        need(allow_future or dt <= self.now, "Observation cannot be in the future")
        return dt.astimezone(self.tz)

    def mutate_weight(self, action, p):
        raw = self.store.read("data/weight.json", {})
        rows = self.weight_rows()
        if action == "weight-add":
            day = self.day(p.get("date"))
            observed = self.timestamp(p["observed_at"]) if p.get("observed_at") else None
            need(not observed or observed.date().isoformat() == day, "Observation date mismatch")
            if observed:
                storage = observed.isoformat()
                need(storage not in raw, "A reading exists at that exact time; inspect before correcting")
            else:
                # Backward-compatible ISO date key, explicitly NOT an observed clock time.
                anchor = datetime.combine(iso_day(day), datetime.min.time(), tzinfo=self.tz)
                storage = anchor.replace(microsecond=int(uuid.uuid4().hex[:8], 16) % 1000000).isoformat()
                while storage in raw:
                    storage = (datetime.fromisoformat(storage) + timedelta(microseconds=1)).isoformat()
            record = {"id": "weight_" + uuid.uuid4().hex, "revision": 1, "date": day,
                      "value": weight_kg(p.get("value"), p.get("unit")), "unit": "kg",
                      "observed_at": observed.isoformat() if observed else None, "time_precision": "instant" if observed else "day",
                      "recorded_at": self.now.isoformat(), "source": "user_reported"}
        else:
            matches = [r for r in rows if r["id"] == p.get("id")]
            need(len(matches) == 1, "Unknown weight ID; query first")
            old = matches[0]
            revision(p, old)
            storage = old["storage_key"]
            if action == "weight-delete":
                raw.pop(storage)
                return {"data/weight.json": packed(raw)}, {"deleted_id": old["id"]}
            record = {**raw[storage], "id": old["id"], "revision": old["revision"] + 1,
                      "value": weight_kg(p.get("value"), p.get("unit")), "unit": "kg", "updated_at": self.now.isoformat()}
        if "fasting" in p:
            need(type(p["fasting"]) is bool, "fasting must be boolean")
            record["fasting"] = p["fasting"]
        if "note" in p:
            record["note"] = label(p["note"], "note")
        raw[storage] = record
        return {"data/weight.json": packed(raw)}, {"weight": record}

    def exercise_item(self, item, old=None):
        need(isinstance(item, dict), "exercise item must be an object")
        need(not set(item) - {"activity", "duration_min", "category", "intensity", "calories_kcal", "net_calories_kcal", "source", "note"}, "Unknown exercise field")
        # exercise-update is a complete replacement, not a patch. Carry only
        # identity/time metadata; omitted activity details must not survive.
        metadata = {k: old[k] for k in ("date", "recorded_at") if old and k in old}
        out = {**metadata, "id": old["id"] if old else "exercise_" + uuid.uuid4().hex,
               "revision": (old or {}).get("revision", 0) + 1,
               "activity": label(item.get("activity"), "activity", 200),
               "duration_min": numeric(item.get("duration_min"), "duration_min", 0.1, 1440),
               "source": item.get("source", "user_reported")}
        need(out["source"] in {"user_reported", "device", "met_estimate"}, "Invalid exercise source")
        for name in ("calories_kcal", "net_calories_kcal"):
            value = item.get(name)
            out[name] = numeric(value, name, 0, 20000) if value is not None else None
        for name in ("category", "intensity", "note"):
            if name in item:
                out[name] = label(item[name], name)
        if old:
            out["updated_at"] = self.now.isoformat()
        else:
            out["recorded_at"] = self.now.isoformat()
        return out

    def mutate_exercise(self, action, p):
        raw = self.exercise_data(normalize=True)
        if action == "exercise-add":
            day = self.day(p.get("date"))
            items = p.get("items")
            need(isinstance(items, list) and 1 <= len(items) <= 30, "Pass 1..30 completed exercises")
            new = [self.exercise_item(item) for item in items]
            block = raw.setdefault(day, {"exercises": []})
            need(isinstance(block, dict) and isinstance(block.get("exercises"), list), "Invalid exercise block")
            block["exercises"].extend(new)
            result = {"date": day, "exercises": new}
        else:
            identity = p.get("id")
            need(not (isinstance(identity, str) and re.fullmatch(r"legacy_exercise_\d{4}-\d{2}-\d{2}_\d+", identity)),
                 "Ambiguous position-based exercise ID; query latest UUIDs first. "
                 "If this ID is stored, reconcile it explicitly before retrying")
            rows = [r for r in self.exercise_rows(raw) if r["id"] == identity]
            need(len(rows) == 1, "Unknown exercise ID; query first")
            row = rows[0]
            revision(p, row)
            day = row["date"]
            block = raw[day]
            if action == "exercise-delete":
                block["exercises"].pop(row["index"])
                result = {"deleted_id": row["id"]}
            else:
                block["exercises"][row["index"]] = self.exercise_item(p.get("item"), row)
                result = {"exercise": block["exercises"][row["index"]]}
        known = [r.get("calories_kcal") for r in block["exercises"]]
        block["total_calories"] = round(sum(x for x in known if x is not None), 2)
        block["calories_complete"] = all(x is not None for x in known)
        return {"data/exercise.json": packed(raw)}, result

    def mutate_plan(self, action, p):
        data = self.plans()
        if action == "plan-save":
            kind = p.get("kind")
            need(kind in KINDS, "kind: goal, meal or exercise")
            body = label(p.get("body"), "body", 30000)
            need("<!-- standalone-coach:" not in body, "Reserved document markers in plan body")
            effective = self.day(p.get("effective_date"), future=True)
            need(0 <= (iso_day(effective) - self.now.date()).days <= 366, "Plan effective date must be today..366 days ahead; no rewriting history")
            targets = p.get("targets", {})
            need(isinstance(targets, dict) and not set(targets) - TARGETS, "Unknown targets")
            need(kind == "goal" or not targets, "Only goal plans own daily targets; meal/exercise plans reference them")
            targets = {k: numeric(v, k, 1, 10000) for k, v in targets.items()}
            baseline = self.active(data, kind, effective)
            record = {"id": "plan_" + uuid.uuid4().hex, "sequence": len(data["entries"]), "kind": kind, "status": "draft",
                      "effective_date": effective, "created_at": self.now.isoformat(), "body": body, "targets": targets,
                      "baseline_id": baseline["id"] if baseline else None, "basis_review_id": p.get("basis_review_id")}
            if record["basis_review_id"]:
                reviews = self.store.read("data/coach-reviews.json", {"version": 1, "entries": []})
                need(any(r["id"] == record["basis_review_id"] for r in reviews["entries"]), "Unknown basis review")
            data["entries"].append(record)
            return {"data/plans.json": packed(data)}, {"plan": record, "active_changed": False}
        need(p.get("confirmed") is True, "Plan activation requires the user's explicit confirmation")
        matches = [r for r in data["entries"] if r["id"] == p.get("plan_id")]
        need(len(matches) == 1, "Unknown plan ID")
        record = matches[0]
        need(record["status"] == "draft", "Plan already confirmed; inspect current plan")
        need(record["effective_date"] >= self.now.date().isoformat(), "Draft effective date is in the past; create a fresh draft")
        baseline = self.active(data, record["kind"], record["effective_date"])
        need((baseline["id"] if baseline else None) == record["baseline_id"], "Active plan changed since draft; review before activation")
        record.update({"status": "confirmed", "activated_at": self.now.isoformat()})
        path = KINDS[record["kind"]]
        relevant = [r for r in data["entries"] if r["kind"] == record["kind"] and r["status"] == "confirmed"]
        body = "## Confirmed coach plans (effective dates, not last-edited time, select the active version)\n"
        for r in sorted(relevant, key=lambda r: (r["effective_date"], r["sequence"])):
            body += f"\n### {r['effective_date']} | {r['id']}\nTargets: {json.dumps(r['targets'], ensure_ascii=False)}\n\n{r['body']}\n"
        return {"data/plans.json": packed(data), path: replace_section(self.store.read_text(path), "plans", body)}, {
            "plan": record, "active_changed": record["effective_date"] == self.now.date().isoformat(), "path": path}

    @staticmethod
    def reminder_key(key):
        need(isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", key), "Reminder key must be lowercase ASCII identifier")
        return key

    @staticmethod
    def job_matches(observed, desired):
        # Gateway may insert harmless extra fields such as staggerMs, timestamps.
        def contains(haystack, needle):
            return isinstance(haystack, dict) and all(
                contains(haystack.get(k), v) if isinstance(v, dict) else haystack.get(k) == v for k, v in needle.items())
        return contains(observed, desired) and not observed.get("payload", {}).get("model")

    def reconcile(self, payload):
        need(isinstance(payload, dict) and payload.get("complete") is True and isinstance(payload.get("jobs"), list),
             "Supply a complete native job listing, including disabled jobs and all pages")
        jobs = payload["jobs"]
        actions = []
        for entry in self.specs():
            job = entry["job"]
            matches = [j for j in jobs if j.get("name") == job["name"]]
            need(len(matches) <= 1, "Duplicate scoped gateway jobs; resolve explicitly instead of creating more")
            if not matches:
                if job["enabled"]:
                    actions.append({"action": "add", "job": job, "key": entry["key"], "spec_hash": entry["spec_hash"]})
                continue
            found = matches[0]
            need(found.get("id") and f"owner={self.owner}" in found.get("payload", {}).get("message", ""),
                 "Name collision with an unowned job; do not modify it")
            need(not found.get("payload", {}).get("model"), "Existing scoped job overrides a model; explicit repair needed")
            if not self.job_matches(found, job):
                actions.append({"action": "update", "jobId": str(found["id"]), "patch": job,
                                "key": entry["key"], "spec_hash": entry["spec_hash"]})
        return {"ok": True, "operations": actions, "native_status": "not_applied",
                "next": "Use the available native scheduler tool; read each job back and reminder-bind. Never report scheduled before verification."}

    def gate(self, key, *, eligibility_only=False):
        key = self.reminder_key(key)
        settings = self.reminders()["items"].get(key)
        if not settings or not settings.get("enabled"):
            return {"ok": True, "send": False, "reason": "disabled_or_missing"}
        expected_hash = sha(packed(self.reminder_spec(key, settings)).encode())
        if settings.get("binding", {}).get("spec_hash") != expected_hash:
            return {"ok": True, "send": False, "reason": "gateway_sync_required"}
        if settings.get("pause_until") and self.now < self.timestamp(settings["pause_until"], allow_future=True):
            return {"ok": True, "send": False, "reason": "paused"}
        if (self.now.weekday() + 1) % 7 not in settings["weekdays"]:
            return {"ok": True, "send": False, "reason": "not_scheduled_weekday"}
        # Avoid a delayed native retry producing an out-of-context next-day nudge.
        hour, minute = map(int, settings["time"].split(":"))
        due = self.now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delay = (self.now - due).total_seconds()
        if not -120 <= delay <= 3600:
            return {"ok": True, "send": False, "reason": "outside_delivery_window"}
        kind, today = settings["kind"], self.now.date().isoformat()
        if kind in {"breakfast", "lunch", "dinner"}:
            ledger = meals_module.Ledger(self.store.workspace, self.tz_name)
            with ledger.locked():
                if any(r.get("meal_type") == kind for r in ledger.load(today)):
                    return {"ok": True, "send": False, "reason": "meal_already_recorded"}
        if kind == "weight" and any(r["date"] == today for r in self.weight_rows()):
            return {"ok": True, "send": False, "reason": "weight_already_recorded"}
        if kind == "exercise" and any(r["date"] == today for r in self.exercise_rows()):
            return {"ok": True, "send": False, "reason": "exercise_already_recorded"}
        if kind == "review" and not eligibility_only:
            from coach_review_reminders import reserve_reviews
            return reserve_reviews(self, key, settings)
        return {"ok": True, "send": True, "kind": kind, "today": today, "timezone": self.tz_name,
                "review_period": "last7" if kind == "review" else None,
                "delivery": "final_text_via_native_announce", "delivered": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--tz", required=True)
    parser.add_argument("--input", default="-")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--period", choices=["today", "yesterday", "last7", "last14", "this-week", "last-week"], default=None)
    parser.add_argument("--review-type", choices=["daily", "weekly", "custom"], default="custom")
    parser.add_argument("--key", help="reminder-gate key")
    args = parser.parse_args()
    try:
        payload = None
        if args.action not in READS:
            if args.input == "-":
                raw = sys.stdin.read(1_000_001)
            else:
                with open(args.input, encoding="utf-8") as handle:
                    raw = handle.read(1_000_001)
            need(len(raw) <= 1_000_000, "Input too large")
            payload = json.loads(raw)
        if args.action not in {"measurements", "review"}:
            need(not (args.start or args.end) and args.period in (None, "last7"), "Range flags apply only to measurements/review; mutation dates belong in JSON")
        need(args.action == "review" or args.review_type == "custom", "--review-type applies only to review; review-save type belongs in JSON")
        result = Coach(args.workspace, args.tz).execute(args.action, payload, start=args.start, end=args.end, period=args.period, key=args.key, review_type=args.review_type)
        print(packed(result), end="")
        return 0
    except (ValueError, OSError, TypeError, KeyError, ImportError) as exc:
        print(packed({"ok": False, "error": str(exc), "recovery": "Do not claim success; retry identical requests only after resolving setup/data errors."}), end="")
        return 1


if __name__ == "__main__":
    sys.exit(main())
