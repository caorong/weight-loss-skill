"""Atomic at-most-once review reservations, never evidence of actual delivery."""
from coach_store import need, packed, sha


def reserve_reviews(coach, key, settings):
    # Coach lock held by execute; gate's eligibility-only branch has no writes.
    eligible = []
    for other_key, other in sorted(coach.reminders()["items"].items()):
        if (other.get("kind") == "review" and other.get("time") == settings["time"]
                and other.get("route") == settings["route"]
                and coach.gate(other_key, eligibility_only=True)["send"]):
            kind = other.get("review_type", "custom")
            period = {"daily": "today", "weekly": "last-week", "custom": "last7"}[kind]
            start, end = coach.window(period=period)
            route_id = sha(packed(other["route"]).encode())[:24]
            eligible.append({"key": other_key, "review_type": kind, "review_period": period,
                             "start": start, "end": end,
                             "notification_key": f"{route_id}:{coach.tz_name}:{kind}:{start}:{end}"})
    need(eligible, "Review gate eligibility changed")
    # A deterministic leader prevents simultaneous native jobs from both announcing.
    leader = eligible[0]["key"]
    if key != leader:
        return {"ok": True, "send": False, "reason": "combined_with_other_review", "leader_key": leader}
    path = "data/coach-review-deliveries.json"
    state = coach.store.read(path, {"version": 1, "reservations": {}})
    need(state.get("version") == 1 and isinstance(state.get("reservations"), dict), "Invalid review reservations")
    pending, seen = [], set()
    for entry in eligible:
        token = entry["notification_key"]
        if token not in state["reservations"] and token not in seen:
            pending.append(entry)
            seen.add(token)
    if not pending:
        return {"ok": True, "send": False, "reason": "review_already_reserved"}
    # Fail before reserving if source data is corrupt. Snapshots audit the data
    # seen at reservation; composition re-queries and validates its own snapshot.
    # Later corrections can regenerate manually without repeating notification.
    for entry in pending:
        report = coach.report(entry["start"], entry["end"], entry["review_type"])
        entry["snapshot"] = report["snapshot"]
        state["reservations"][entry["notification_key"]] = {
            "reserved_at": coach.now.isoformat(), "key": entry["key"],
            "snapshot": entry["snapshot"], "delivered": False}
    result = {"send": True, "kind": "review", "today": coach.now.date().isoformat(),
              "timezone": coach.tz_name, "review_type": pending[0]["review_type"],
              "review_period": pending[0]["review_period"], "reviews": pending,
              "notification_reservation": {"at_most_once": True, "delivered": False},
              "delivery": "final_text_via_native_announce", "delivered": False}
    token = "review-reservation:" + sha(packed([r["notification_key"] for r in pending]).encode())
    return coach.store.commit(token, sha(packed(state).encode()), {path: packed(state)}, result)
