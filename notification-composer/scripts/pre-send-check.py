#!/usr/bin/env python3
"""
pre-send-check.py — Deterministic pre-send checks for meal/weight reminders.

Returns SEND or NO_REPLY (with reason on stderr for logging).
The model should NOT be invoked if this returns NO_REPLY.

Usage:
  python3 pre-send-check.py --workspace-dir <path> --meal-type <type> --tz-offset <seconds>

  --workspace-dir   Agent workspace root (contains health-profile.md, data/, etc.)
  --meal-type       One of: breakfast, lunch, dinner, meal_1, meal_2, weight,
                    weight_morning_followup
  --tz-offset       Timezone offset in seconds from UTC (e.g. 28800 for UTC+8)

Exit code 0 always. Output is exactly "SEND" or "NO_REPLY" on stdout.
Reason is printed to stderr for debugging.
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# 2026-07-01 重命名过渡:新名 DATA_API_URL 优先,老名 LIFECYCLE_API_URL 兼容
LIFECYCLE_API = os.environ.get("DATA_API_URL") or os.environ.get("LIFECYCLE_API_URL", "http://127.0.0.1:3100")


def _normalize_path(p):
    """Lowercase wechat-dm/wecom-dm segment to avoid case-mismatch directories."""
    import re as _re
    return _re.sub(r'(workspace-(?:wechat|wecom)-dm-)([^/]+)', lambda m: m.group(1) + m.group(2).lower(), p)


def log(msg):
    """Log to stderr (not visible to user, only for debugging)."""
    print(f"[pre-send-check] {msg}", file=sys.stderr)


def _account_id(workspace_dir):
    """workspace-wechat-dm-<acc> → <acc>."""
    base = os.path.basename(os.path.normpath(workspace_dir)).replace("workspace-", "")
    for prefix in ("wechat-dm-", "wecom-dm-"):
        if base.startswith(prefix):
            return base[len(prefix):]
    return base


def lifecycle_state(account_id):
    """GET /v1/lifecycle/state/<acc>. None on failure."""
    try:
        with urllib.request.urlopen(f"{LIFECYCLE_API}/v1/lifecycle/state/{account_id}", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None


def lifecycle_is_due(account_id):
    """该用户今天是否该发召回(GET /due 已做 stage 判定+节奏+当天去重)。
    返回 (is_due: bool, tier: str|None)。失败返回 (None, None) 让调用方降级。"""
    try:
        with urllib.request.urlopen(f"{LIFECYCLE_API}/v1/lifecycle/due?limit=1000", timeout=10) as r:
            users = json.loads(r.read().decode("utf-8")).get("users", [])
        for u in users:
            if u.get("account_id") == account_id:
                return True, u.get("tier")
        return False, None
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None, None


def lifecycle_mark_recall(account_id, tier):
    """POST /recall-sent 认领今天的召回位(检查时认领,复刻旧 last_recall_date 语义)。"""
    body = json.dumps({"account_id": account_id, "tier": tier}).encode("utf-8")
    req = urllib.request.Request(f"{LIFECYCLE_API}/v1/lifecycle/recall-sent", data=body,
                                 method="POST", headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        return True
    except (urllib.error.URLError, OSError) as e:
        log(f"mark recall-sent failed for {account_id}: {e}")
        return False


def get_local_date(tz_offset):
    """Get current local date string YYYY-MM-DD."""
    tz = timezone(timedelta(seconds=tz_offset))
    return datetime.now(tz).strftime("%Y-%m-%d")


def get_local_weekday(tz_offset):
    """Get current local weekday (0=Monday, 6=Sunday)."""
    tz = timezone(timedelta(seconds=tz_offset))
    return datetime.now(tz).weekday()


def check_health_profile(workspace_dir):
    """Check 1: health-profile.md exists (user is onboarded)."""
    path = os.path.join(workspace_dir, "health-profile.md")
    if not os.path.exists(path):
        return False, "health-profile.md not found — user not onboarded"
    return True, None


def check_engagement_stage(workspace_dir, meal_type, tz_offset, out=None):
    """Check 2: engagement stage gating.

    Stage 1 (ACTIVE):  SEND — normal reminder
    Stage 2 (RECALL):  SEND once per day (first meal cron only, suppress rest)
                        Weight reminders suppressed entirely.
    Stage 3 (WEEKLY):  SEND once per week (if >= 7 days since last_recall_date)
                        Weight reminders suppressed entirely.
    Stage 4 (MONTHLY): SEND once per month (if >= 30 days since last_recall_date)
                        Weight reminders suppressed entirely.
    Stage 5 (SILENT):  NO_REPLY — suppress everything

    Stage from lifecycle API /state (single source of truth). Recall cadence +
    same-day dedup from /due (event-sourced). On SEND-recall, claims the slot by
    POST /recall-sent immediately (check-time claim, replaces old last_recall_date).
    """
    account_id = _account_id(workspace_dir)
    state = lifecycle_state(account_id)
    if state is None:
        # fail-open: API 不可达时按正常提醒发(Stage 1 行为),不漏发
        log(f"/state unavailable for {account_id}, fail-open as stage 1")
        return True, None

    stage = state.get("stage", 1)
    days_silent_val = state.get("days_silent", 0)
    # 记录"认领前"的 stage/days_silent 给输出用(认领+1 recall 可能改变 stage,
    # 但这条召回的身份应是认领前的 stage)
    if out is not None:
        out["stage"] = stage
        out["days_silent"] = days_silent_val

    if stage >= 5:
        return False, f"stage={stage} — user is in silent mode"

    # first_meal_nudge / activation: the authoritative lifecycle Silent gate
    # (stage >= 5, above) still applies — never nudge a Silent user. Past that,
    # the recall/lunch-only logic below is for the engaged recall stages and is
    # irrelevant to these never-engaged cohorts, so bypass it. The nudge-specific
    # gating (onboarding/already-logged/lastInboundAt/cap) lives in
    # check_first_meal_nudge / check_activation_nudge, which run after this.
    if meal_type in ("first_meal_nudge", "activation"):
        return True, None

    # Stage 2-4: suppress weight reminders entirely
    if stage in (2, 3, 4):
        if meal_type in ("weight", "weight_morning_followup"):
            return False, f"stage={stage} — weight reminders suppressed during recall"

    # Stage 2-4: suppress custom reminders, daily summaries; Stage 3-4: also weekly reports
    if stage in (2, 3, 4):
        if meal_type in ("custom", "daily_summary"):
            return False, f"stage={stage} — {meal_type} suppressed during recall"
        if stage >= 3 and meal_type == "weekly_report":
            return False, f"stage={stage} — weekly_report suppressed at stage 3+"

    # Stage 2: only lunch slot, only on days_silent 3 or 5 (Day 3 / Day 5)
    # Exception: weekly_report allowed through at S2 (user has recent data)
    if stage == 2 and meal_type not in ("weekly_report",):
        if meal_type not in ("lunch", "meal_2"):
            return False, f"stage=2 — only lunch recall allowed, got {meal_type}"
        if days_silent_val not in (3, 5):
            return False, f"stage=2 — days_silent={days_silent_val}, recall only on day 3/5"

    # Stage 3-4: only lunch slot for recall messages
    if stage in (3, 4):
        if meal_type not in ("lunch", "meal_2"):
            return False, f"stage={stage} — only lunch recall allowed, got {meal_type}"

    # Recall cadence + same-day dedup: ask /due (handles 7d/30d cadence + today-dedup).
    # weekly_report at S2 is NOT a recall message — skip the due/claim gate.
    if stage in (2, 3, 4) and meal_type not in ("weekly_report",):
        is_due, tier = lifecycle_is_due(account_id)
        if is_due is None:
            # /due 不可达:降级放行(宁可发,不漏召回),不认领
            log(f"/due unavailable for {account_id}, fail-open recall")
            return True, (f"recall stage={stage} days_silent={days_silent_val}" if stage == 2 else None)
        if not is_due:
            return False, f"stage={stage} — not due for recall today (cadence/dedup)"
        # 检查时认领:立即记 recall_sent,防同 slot 重触发重复发(复刻旧 last_recall_date 语义)
        lifecycle_mark_recall(account_id, tier or ("weekly" if stage in (2, 3) else "monthly"))
        if stage == 2:
            return True, f"recall stage=2 days_silent={days_silent_val}"
        return True, None

    return True, None


def check_meal_logged(workspace_dir, meal_type, tz_offset):
    """Check 3: this meal is not already logged today."""
    local_date = get_local_date(tz_offset)
    meals_file = os.path.join(workspace_dir, "data", "meals", f"{local_date}.json")

    if not os.path.exists(meals_file):
        return True, None  # no meals logged today at all

    try:
        with open(meals_file) as f:
            meals = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log(f"Warning: could not read {meals_file}: {e}")
        return True, None  # fail-open

    for meal in meals:
        mt = meal.get("meal_type", "") or meal.get("name", "")
        if mt == meal_type:
            return False, f"{meal_type} already logged today ({local_date})"

    return True, None


def check_weight_logged(workspace_dir, tz_offset):
    """Check for weight: already weighed today?"""
    local_date = get_local_date(tz_offset)
    return _weight_logged_on(workspace_dir, local_date)


def check_weight_logged_yesterday_or_today(workspace_dir, tz_offset):
    """Check for weight morning followup: suppress if user weighed yesterday or today."""
    tz = timezone(timedelta(seconds=tz_offset))
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    logged_today, _ = _weight_logged_on(workspace_dir, today)
    logged_yesterday, _ = _weight_logged_on(workspace_dir, yesterday)

    if not logged_today:
        return False, f"weight already logged today ({today}) — no morning followup needed"
    if not logged_yesterday:
        return False, f"weight logged yesterday ({yesterday}) — no morning followup needed"
    return True, None


def _weight_logged_on(workspace_dir, date_str):
    """Helper: check if weight was logged on a specific date. Returns (not_logged, reason)."""
    weight_file = os.path.join(workspace_dir, "data", "weight.json")

    if not os.path.exists(weight_file):
        return True, None

    try:
        with open(weight_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log(f"Warning: could not read weight.json: {e}")
        return True, None

    # weight.json keys are ISO-8601 datetimes; check date prefix
    if isinstance(data, dict):
        for key in data:
            if key[:10] == date_str:
                return False, f"weight logged on {date_str}"

    # Also handle list format
    if isinstance(data, list):
        for record in data:
            record_date = record.get("date", "")
            if record_date == date_str:
                return False, f"weight logged on {date_str}"

    records = data.get("records", []) if isinstance(data, dict) else []
    for record in records:
        record_date = record.get("date", "")
        if record_date == date_str:
            return False, f"weight logged on {date_str}"

    return True, None


FIRST_MEAL_NUDGE_CAP = 2


def _meal_has_food(meal):
    """Mirror of check-stage.py's _meal_has_food: a meal entry counts as a
    real food log if it has a non-empty items/foods list."""
    if not isinstance(meal, dict):
        return False
    items = meal.get("items") or meal.get("foods")
    return bool(items)


def _any_meal_ever_logged(workspace_dir):
    """Return True if any meal entry containing food data exists in
    data/meals/*.json (any date). Used by the first-meal nudge: the moment
    the user logs ANY meal, the nudge self-cancels."""
    import glob as _glob
    meals_dir = os.path.join(workspace_dir, "data", "meals")
    if not os.path.isdir(meals_dir):
        return False
    for fp in _glob.glob(os.path.join(meals_dir, "*.json")):
        try:
            with open(fp) as f:
                meals = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        if isinstance(meals, list):
            if any(_meal_has_food(m) for m in meals):
                return True
        elif isinstance(meals, dict):
            if any(_meal_has_food(m) for m in meals.values()):
                return True
    return False


def check_first_meal_nudge(workspace_dir, tz_offset):
    """Gate for the one-shot first-meal nudge (meal_type=first_meal_nudge).

    Suppress (NO_REPLY) when ANY of:
      - onboarding NOT completed (wrong cohort — that's the activation nudge's
        never-replied user; keeps the two cohorts mutually exclusive at the
        gate level, not just at cron-creation time)
      - the user has already logged a meal on any date (nudge self-cancels —
        normal tracking takes over)
      - the nudge cap (FIRST_MEAL_NUDGE_CAP) has already been reached — this is
        the terminal anti-nag guarantee, and it is lifecycle-independent

    Note: leave/pause AND the authoritative lifecycle Silent state (stage 5) are
    handled by the generic check_leave + check_engagement_stage gates that run
    BEFORE this one (check_engagement_stage reads stage from the lifecycle API).
    Stage is NO LONGER read from engagement.json here — that field is deprecated
    (stage lives in the lifecycle DB). Only `activation.*` (a non-stage business
    counter, still owned in engagement.json) is read. This gate does NOT
    increment the counter — notification-composer calls
    activation-mark-sent.py --counter first_meal_nudges_sent after a successful
    compose.
    """
    if not _onboarding_completed(workspace_dir):
        return False, "first_meal_nudge — onboarding not completed (wrong cohort)"

    if _any_meal_ever_logged(workspace_dir):
        return False, "first_meal_nudge — user has already logged a meal, nudge no longer needed"

    eng_path = os.path.join(workspace_dir, "data", "engagement.json")
    if not os.path.exists(eng_path):
        return True, None
    try:
        with open(eng_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return True, None  # fail-open

    nudges_sent = (data.get("activation") or {}).get("first_meal_nudges_sent", 0)
    if nudges_sent >= FIRST_MEAL_NUDGE_CAP:
        return False, f"first_meal_nudge — cap reached ({nudges_sent}/{FIRST_MEAL_NUDGE_CAP})"

    return True, None


ACTIVATION_NUDGE_CAP = 2


def _read_channel_source(workspace_dir):
    """Load channel-source.json from the workspace root.

    Returns (data_dict, readable):
      - (dict, True)  when the file exists and parsed to a dict
      - ({},  False)  when the file is missing OR unreadable/corrupt

    The `readable` flag lets the activation gate fail CLOSED (NO_REPLY) when the
    file is absent/unreadable — the target cohort always has this file (infra
    writes it at registration), so a missing file is anomalous and the
    conservative anti-nag choice is to not nudge when we can't read state.
    """
    path = os.path.join(workspace_dir, "channel-source.json")
    if not os.path.exists(path):
        return {}, False
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data, True
        return {}, False
    except (json.JSONDecodeError, IOError):
        return {}, False


def _onboarding_completed(workspace_dir):
    """True iff health-profile.md has a REAL Onboarding Completed date (not '—').

    Mirrors the field-form regex used by mark-onboarding-done.py /
    onboarding-check.py: `- **Onboarding Completed:** <value>` inside the
    Automation section. A value of '—' (or '-', empty) means NOT completed.
    """
    import re as _re
    path = os.path.join(workspace_dir, "health-profile.md")
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            content = f.read()
    except IOError:
        return False
    m = _re.search(
        r"^\s*-\s*\*\*Onboarding\s+Completed:\*\*\s*(.+?)\s*$",
        content, _re.IGNORECASE | _re.MULTILINE,
    )
    if not m:
        return False
    val = m.group(1).strip()
    return bool(val) and val not in ("—", "-", "none", "None")


def check_activation_nudge(workspace_dir, tz_offset):
    """Gate for the activation nudge (meal_type=activation) — the Part-1
    "greeted but never replied" cohort.

    The DEFINING gate: read channel-source.json > lastInboundAt (epoch ms,
    written by the infra-side Phase-0 instrumentation on every inbound message).
    If lastInboundAt is present AT ALL, the user has replied at least once →
    the nudge's whole reason to exist is gone → NO_REPLY (cancel).

    Also suppress (NO_REPLY) when ANY of:
      - channel-source.json is missing/unreadable (fail closed — can't confirm
        the user hasn't replied, so stay silent)
      - onboarding already completed (they're past the never-replied stall —
        a different cohort, handled by the first-meal nudge)
      - any meal ever logged (they engaged)
      - engagement stage is Silent (5)
      - the cap (ACTIVATION_NUDGE_CAP) has been reached

    Leave/pause AND the authoritative lifecycle Silent state (stage 5) are
    handled by the generic check_leave + check_engagement_stage gates that run
    BEFORE this one (check_engagement_stage reads stage from the lifecycle API).
    Stage is NOT read from engagement.json here — that field is deprecated.
    Only `activation.nudges_sent` (a non-stage business counter, still owned in
    engagement.json) is read; the cap is the terminal anti-nag guarantee and is
    lifecycle-independent. This gate does NOT increment the counter —
    notification-composer calls activation-mark-sent.py --counter nudges_sent
    after a successful compose.
    """
    cs, cs_readable = _read_channel_source(workspace_dir)

    # --- Fail closed: if we can't read channel-source.json at all, do NOT
    # nudge. The target cohort always has this file; a missing/unreadable file
    # is anomalous and the conservative anti-nag choice is silence. ---
    if not cs_readable:
        return False, "activation — channel-source.json missing/unreadable, failing closed (no nudge)"

    # --- The defining check: any inbound at all means the user replied. ---
    if cs.get("lastInboundAt") is not None:
        return False, "activation — lastInboundAt present (user has replied), nudge cancelled"

    # Target user must actually be a handoff/never-replied case: onboarding
    # NOT completed. If onboarding is done, this is the wrong cohort.
    if _onboarding_completed(workspace_dir):
        return False, "activation — onboarding already completed (wrong cohort)"

    # Any meal logged means they engaged (defensive — implies inbound too).
    if _any_meal_ever_logged(workspace_dir):
        return False, "activation — a meal has been logged (user engaged), nudge cancelled"

    eng_path = os.path.join(workspace_dir, "data", "engagement.json")
    if os.path.exists(eng_path):
        try:
            with open(eng_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = {}
        nudges_sent = (data.get("activation") or {}).get("nudges_sent", 0)
        if nudges_sent >= ACTIVATION_NUDGE_CAP:
            return False, f"activation — cap reached ({nudges_sent}/{ACTIVATION_NUDGE_CAP})"

    return True, None


def check_health_flags(workspace_dir, meal_type):
    """Check: skip weight reminders if ED-related flags present."""
    if meal_type != "weight":
        return True, None

    # Check USER.md for health flags
    user_md = os.path.join(workspace_dir, "USER.md")
    if not os.path.exists(user_md):
        return True, None

    try:
        with open(user_md) as f:
            content = f.read().lower()
        if "avoid_weight_focus" in content or "history_of_ed" in content:
            return False, "health flag: avoid_weight_focus or history_of_ed"
    except IOError:
        pass

    return True, None


def check_scheduling_constraints(workspace_dir, meal_type, tz_offset):
    """Check 4: scheduling constraints from health-preferences.md."""
    prefs_path = os.path.join(workspace_dir, "health-preferences.md")
    if not os.path.exists(prefs_path):
        return True, None

    try:
        with open(prefs_path) as f:
            content = f.read().lower()
    except IOError:
        return True, None

    weekday = get_local_weekday(tz_offset)  # 0=Mon, 6=Sun
    weekday_names_en = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    weekday_names_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today_en = weekday_names_en[weekday]
    today_zh = weekday_names_zh[weekday]

    # Check for "skip breakfast on workdays" pattern
    if meal_type == "breakfast" and weekday < 5:  # Mon-Fri
        skip_patterns = [
            "skip breakfast on workday",
            "skips breakfast on workday",
            "always skips breakfast",
            "不吃早餐",
            "跳过早餐",
            "工作日不吃早餐",
        ]
        for pattern in skip_patterns:
            if pattern in content:
                return False, f"scheduling constraint: {pattern}"

    # Check for day-specific constraints like "works late on wednesdays"
    # Look for lines containing today's name + skip/delay keywords
    for line in content.split("\n"):
        if (today_en in line or today_zh in line) and meal_type in line:
            skip_keywords = ["skip", "跳过", "不吃", "不提醒", "no reminder"]
            for kw in skip_keywords:
                if kw in line:
                    return False, f"scheduling constraint: {line.strip()}"

    return True, None


def check_leave(workspace_dir, tz_offset, mock_date=None):
    """Check if user is on leave. If so, suppress all reminders.

    双源严格同步:请假状态有两个真源 —— leave.json(本地,存请假天数/原因业务细节)
    与 DB silence_state='frozen'(lifecycle 权威,plugin 注入靠它)。二者必须一致。
    **以 DB frozen 为权威**对账 leave.json,修正不一致(防漂移):
      - DB frozen=true  → 请假中,拦截(NO_REPLY)。leave.json 缺失则不影响拦截。
      - DB frozen=false 但 leave.json 仍在期内 → DB 已解冻(reactor 到期 / 用户回归 /
        手动改),leave.json 滞后 → 删 leave.json,放行(以 DB 为准)。
      - DB 不可达 → 降级回退到「只看 leave.json」的原逻辑(lifecycle 挂了请假仍按
        leave.json 生效,不因 DB 故障误发提醒)。
    """
    leave_path = os.path.join(workspace_dir, "data", "leave.json")

    # 先查 DB frozen(权威)。account_id 从 workspace 路径取。
    account_id = _account_id(workspace_dir)
    state = lifecycle_state(account_id) if account_id else None
    db_frozen = bool(state.get("frozen")) if isinstance(state, dict) else None

    # 读 leave.json 本地态
    leave_data = None
    if os.path.exists(leave_path):
        try:
            with open(leave_path) as f:
                leave_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            try:
                os.remove(leave_path)  # 损坏文件清理
            except OSError:
                pass

    start = (leave_data or {}).get("start", "")
    end = (leave_data or {}).get("end", "")
    if leave_data is not None and (not start or not end):
        # 无效请假文件 → 清理
        try:
            os.remove(leave_path)
        except OSError:
            pass
        leave_data = None

    if mock_date:
        today = mock_date
    else:
        tz = timezone(timedelta(seconds=tz_offset))
        today = datetime.now(tz).strftime("%Y-%m-%d")

    leave_active_local = leave_data is not None and start <= today <= end

    # ── DB 权威对账 ───────────────────────────────────────────────────────
    if db_frozen is True:
        # DB 说请假中 → 拦截(无论 leave.json 在不在)。
        return False, f"user on leave (db frozen; leave.json {start}~{end})" if leave_data else "user on leave (db frozen)"

    if db_frozen is False:
        # DB 说没请假。leave.json 若仍在期内 = 滞后,删之同步(以 DB 为准)。
        if leave_data is not None:
            try:
                os.remove(leave_path)
                log(f"leave.json 与 DB 不一致(DB 已解冻),删 leave.json 同步")
            except OSError:
                pass
        return True, None

    # ── DB 不可达(db_frozen is None)→ 降级:只看 leave.json(原逻辑) ──────────
    if leave_active_local:
        return False, f"user on leave ({start} to {end}) [db unavailable, leave.json only]"
    # 过期自动清理
    if leave_data is not None and today > end:
        try:
            os.remove(leave_path)
        except FileNotFoundError:
            pass
        except OSError as e:
            log(f"Warning: could not delete expired leave.json: {e}")
    return True, None




def recent_conversation_block(workspace_dir):
    """抽取用户最近对话, 供 cron 提醒承接上下文。

    默认关闭 (环境变量 CRON_RECENT_CONTEXT 未开启时返回空), 灰度期在测试服/生产
    按需打开。调用同目录的 extract-recent.py, 任何失败都静默返回空——绝不因抽取
    问题阻断提醒。

    开关与参数 (环境变量):
      CRON_RECENT_CONTEXT       "1"/"true"/"on" 才启用 (默认关)
      CRON_RECENT_TURNS         最近几轮 (assistant 回复数), 默认 10
      CRON_RECENT_MAX_CHARS     注入字数上限, 默认 1200
    """
    flag = os.environ.get("CRON_RECENT_CONTEXT", "").strip().lower()
    if flag not in ("1", "true", "on", "yes"):
        return ""

    turns = os.environ.get("CRON_RECENT_TURNS", "10").strip()
    max_chars = os.environ.get("CRON_RECENT_MAX_CHARS", "1200").strip()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extract-recent.py")
    if not os.path.exists(script):
        log(f"extract-recent.py 缺失: {script}")
        return ""

    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, script,
             "--workspace-dir", workspace_dir,
             "--turns", turns,
             "--max-chars", max_chars],
            capture_output=True, text=True, timeout=8)
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"extract-recent 调用失败: {e}")
        return ""

    text = (result.stdout or "").strip()
    if not text:
        return ""
    # agent 读到的承接上下文。措辞明确「最近对话」+「据此自然承接」,
    # 但不强制引用, 避免提醒变成复读机。
    return ("\n\n最近和用户的对话(供你自然承接, 不要机械复述):\n"
            + text)


def main():
    parser = argparse.ArgumentParser(description="Pre-send check for meal/weight reminders")
    parser.add_argument("--workspace-dir", required=True, help="Agent workspace root")
    parser.add_argument("--meal-type", required=True,
                        choices=["breakfast", "lunch", "dinner", "meal_1", "meal_2",
                                 "weight", "weight_morning_followup",
                                 "custom", "weekly_report", "daily_summary",
                                 "first_meal_nudge", "activation"],
                        help="Meal type to check")
    parser.add_argument("--tz-offset", required=True, type=int,
                        help="Timezone offset in seconds from UTC")
    parser.add_argument("--mock-date", default=None,
                        help="Mock today's date YYYY-MM-DD (for testing)")
    args = parser.parse_args()
    args.workspace_dir = _normalize_path(args.workspace_dir)

    # Stage 由 lifecycle API 实时计算(check_engagement_stage 内部调 /state),
    # 无需预热 engagement.json。

    stage_info = {"stage": 1}
    checks = [
        ("leave", lambda: check_leave(args.workspace_dir, args.tz_offset, args.mock_date)),
        ("health_profile", lambda: check_health_profile(args.workspace_dir)),
        ("engagement_stage", lambda: check_engagement_stage(
            args.workspace_dir, args.meal_type, args.tz_offset, out=stage_info)),
        ("health_flags", lambda: check_health_flags(args.workspace_dir, args.meal_type)),
        ("scheduling", lambda: check_scheduling_constraints(
            args.workspace_dir, args.meal_type, args.tz_offset)),
    ]

    # Add meal-specific or weight-specific check
    if args.meal_type == "weight":
        checks.append(("weight_logged", lambda: check_weight_logged(
            args.workspace_dir, args.tz_offset)))
    elif args.meal_type == "weight_morning_followup":
        checks.append(("weight_logged_yesterday_or_today", lambda: check_weight_logged_yesterday_or_today(
            args.workspace_dir, args.tz_offset)))
    elif args.meal_type == "first_meal_nudge":
        checks.append(("first_meal_nudge", lambda: check_first_meal_nudge(
            args.workspace_dir, args.tz_offset)))
    elif args.meal_type == "activation":
        checks.append(("activation", lambda: check_activation_nudge(
            args.workspace_dir, args.tz_offset)))
    elif args.meal_type in ("custom", "weekly_report", "daily_summary"):
        pass  # Only stage check needed, no meal-logged check
    else:
        checks.append(("meal_logged", lambda: check_meal_logged(
            args.workspace_dir, args.meal_type, args.tz_offset)))

    # Run all checks
    for check_name, check_fn in checks:
        passed, reason = check_fn()
        if not passed:
            log(f"FAIL [{check_name}]: {reason}")
            print("NO_REPLY")
            return

    log("All checks passed")

    # stage_info 由 engagement_stage check 填入「认领前」的 stage/days_silent
    recent = recent_conversation_block(args.workspace_dir)
    if stage_info["stage"] >= 2:
        print(f"SEND recall stage={stage_info['stage']} days_silent={stage_info.get('days_silent', 0)}{recent}")
    else:
        print(f"SEND{recent}")


if __name__ == "__main__":
    main()
