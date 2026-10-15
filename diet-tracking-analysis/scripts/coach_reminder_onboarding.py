"""Durable reminder choices, separate from schedules and native registration.

Read views never migrate existing settings. Only explicit choice mutations write
the optional onboarding field in the coach-owned reminders registry.
"""
from __future__ import annotations

from coach_store import need, packed


OPTIONS = {
    "daily-review": {"kind": "review", "review_type": "daily"},
    "weekly-review": {"kind": "review", "review_type": "weekly"},
    "breakfast": {"kind": "breakfast"},
    "lunch": {"kind": "lunch"},
    "dinner": {"kind": "dinner"},
    "weight": {"kind": "weight"},
    "exercise": {"kind": "exercise"},
}
DECISIONS = {"accepted", "declined", "skipped"}


def choices(data):
    state = data.get("onboarding", {"version": 1, "choices": {}})
    need(isinstance(state, dict) and state.get("version") == 1
         and isinstance(state.get("choices"), dict), "Invalid reminder onboarding state")
    rows = state["choices"]
    need(not set(rows) - OPTIONS.keys(), "Unknown reminder onboarding option")
    for row in rows.values():
        need(isinstance(row, dict) and row.get("state") in DECISIONS | {"offered"},
             "Invalid reminder onboarding choice")
    return rows


def view(data):
    rows = choices(data)
    legacy = sorted(k for k, s in data["items"].items()
                    if s.get("kind") == "review" and "review_type" not in s)
    entries = {}
    for option, identity in OPTIONS.items():
        existing = sorted(k for k, s in data["items"].items()
                          if all(s.get(field) == value for field, value in identity.items()))
        recorded = rows.get(option, {})
        # Existing arbitrary keys, disabled settings and legacy reviews are never
        # silently replaced by a new canonical-key schedule during onboarding.
        state = ("configured" if existing else "migration_required"
                 if legacy and identity["kind"] == "review" else recorded.get("state", "unasked"))
        entries[option] = {
            **identity, "state": state, "choice": recorded or None,
            "configured_keys": existing,
            "enabled": any(data["items"][k].get("enabled") is True for k in existing),
            "suggested_key": option if option not in data["items"] else None,
        }
    return {
        "revision": data["revision"], "options": entries,
        "unasked": [k for k, s in entries.items() if s["state"] == "unasked"],
        "awaiting_answer": [k for k, s in entries.items() if s["state"] == "offered"],
        "needs_configuration": [k for k, s in entries.items() if s["state"] == "accepted"],
        "legacy_review_keys": legacy,
        "native_status": "verify_with_gateway",
    }


def update(coach, data, payload):
    offered = payload.get("offered", [])
    decisions = payload.get("decisions", {})
    need(isinstance(offered, list) and all(isinstance(k, str) for k in offered)
         and len(offered) == len(set(offered)), "offered must be unique option identifiers")
    need(isinstance(decisions, dict), "decisions must be an option-to-choice object")
    need(offered or decisions, "Supply offered options or explicit decisions")
    need(not (set(offered) | set(decisions)) - OPTIONS.keys(), "Unknown reminder onboarding option")
    need(not set(offered) & set(decisions), "Offer and answer an option in separate operations")
    need(all(isinstance(v, str) and v in DECISIONS for v in decisions.values()),
         "Decision must be accepted, declined or skipped")
    need(not decisions or payload.get("confirmed") is True,
         "Record decisions only after an explicit user answer")
    current = view(data)
    rows = dict(choices(data))
    for option in set(offered) | set(decisions):
        state = current["options"][option]["state"]
        need(state not in {"configured", "migration_required"},
             "Existing reminders require explicit reminder-set edits/migration, not onboarding")
        if option in offered:
            need(state in {"unasked", "offered"}, "Do not re-offer an answered reminder choice")
        rows[option] = {"state": decisions.get(option, "offered"),
                        "updated_at": coach.now.isoformat(), "request_id": payload["request_id"]}
    data["onboarding"] = {"version": 1, "choices": rows}
    data["revision"] += 1
    return {"data/coach-reminders.json": packed(data)}, {
        "revision": data["revision"], "onboarding": view(data),
        "native_status": "not_applied", "schedules_changed": False,
    }
