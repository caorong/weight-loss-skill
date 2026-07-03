---
name: notification-manager
version: 1.0.0
description: "Cron infrastructure and reminder lifecycle management for the AI weight loss companion. Creates, syncs, and removes meal/weight reminder cron jobs. Manages the engagement lifecycle (Active → Pause → Recall → Silent). Handles adaptive timing, user reminder setting changes, and leave/vacation management. Use this skill when: meal-planner completes onboarding (to bootstrap reminders), user requests reminder setting changes, user wants to pause/resume reminders for a vacation or holiday ('五一不打卡了', '放假暂停提醒', '2号到4号出去玩', 'pause reminders', 'I'm going on vacation'), or another skill needs to verify/fix cron state. Do NOT use for composing reminder content or handling replies — that is notification-composer's job."
metadata:
  openclaw:
    emoji: "bell"
    homepage: https://github.com/NanoRhino/weight-loss-skill
---

# Notification Manager

> ⚠️ **SILENT OPERATION:** Never narrate internal actions, skill transitions, or tool calls to the user. No "Let me check...", "Now I'll transition to...", "Reading your profile...". Just do it silently and respond with the result.


Orchestration layer for reminders — cron CRUD, lifecycle management, adaptive
timing, and setting changes. This skill decides **when** to send and **whether
to keep sending**. The actual message content is composed by `notification-composer`.

## Cron Infrastructure

### Script

All cron job creation must go through this skill's script:

```bash
bash {baseDir}/scripts/create-reminder.sh
```

This script (migrated from the former `scheduled-reminders` skill) auto-resolves
delivery config for multiple channels (Slack, WeChat, WeCom, etc.).

### One-shot reminder

```bash
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> \
  --channel <channel> \
  --name "Descriptive name" \
  --message "Reminder content" \
  --at "2m"
```

`--at` accepts relative time (`2m`, `1h`, `30s`) or ISO timestamp (`2026-03-04T10:00:00Z`).
One-shot reminders auto-delete after running. Use `--keep` to preserve them.

### Recurring reminder

```bash
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --name "Lunch reminder" \
  --message "Run notification-composer for lunch." \
  --cron "0 12 * * *"
```

`--tz` auto-detects from the agent workspace's `USER.md`. Falls back to `Asia/Shanghai` if not found. You can override explicitly with `--tz`.

### Parameters

| Param | Required | Description |
|-------|----------|-------------|
| `--agent` | ✅ | Your agent ID (e.g. `wechat-dm-xxx`, `007-zhuoran`) |
| `--channel` | ❌ | Delivery channel (`wechat`, `wecom`, `slack`, etc.). Defaults to `slack` if omitted (backward-compatible) |
| `--name` | ✅ | Descriptive job name (shown in cron list) |
| `--message` | ✅ | Prompt sent to user when the job fires |
| `--at` | one of | One-shot: relative time or ISO timestamp |
| `--cron` | one of | Recurring: 5-field cron expression |
| `--tz` | ❌ | Timezone for cron (auto-detects from `timezone.json`, fallback: `Asia/Shanghai`) |
| `--keep` | ❌ | Don't auto-delete one-shot jobs after running |
| `--to` | ❌ | Explicit delivery target. Overrides auto-detection. Required for channels other than `slack`/`wechat`/`wecom` |
| `--type` | ❌ | Job type for anti-burst scheduling: `meal`, `weight`, or `other` (default: `other`). See **Anti-burst scheduling** below |
| `--exact` | ❌ | Skip anti-burst logic, use exact cron time. Use for time-sensitive reminders that must fire at the precise minute |

### How it works

The script resolves the delivery target (`--to`) based on the channel:

| Channel | Auto-detection | Example |
|---------|---------------|---------|
| `slack` (default) | Looks up Slack user ID from `~/.openclaw/openclaw.json` bindings → `user:<id>` | `--agent 007-zhuoran` → `user:U12345` |
| `wechat` / `wecom` | Extracts userId from agent ID (`wechat-dm-xxx` → `xxx`) | `--agent wechat-dm-abc123` → `abc123` |
| Others | No auto-detection — must pass `--to` explicitly | `--to "123456789"` |

Timezone auto-detection searches these paths in order:
1. `~/.openclaw/workspace-$AGENT/USER.md`
2. `~/.openclaw/workspace-nutritionist/$AGENT/USER.md`

Then calls `openclaw cron add` with `sessionTarget = "isolated"`, `payload.kind = "agentTurn"`, and `delivery.mode = "announce"` automatically. The isolated agent composes the reminder and outputs the text — announce delivery sends it to the user and automatically injects the context into the main session.

### Anti-burst scheduling

When creating **recurring** cron jobs (`--cron`), the script automatically avoids
scheduling too many jobs at the same minute to prevent bulk message sends that
could trigger platform rate limits or account bans.

**How it works:**
1. Fetches all existing recurring cron jobs from the gateway
2. Converts all cron times to UTC for cross-timezone comparison
3. Checks if the target minute already has ≥2 jobs
4. If full, scans nearby minutes for an available slot (< 2 jobs per minute)
5. Adjusts the cron expression to the chosen slot

**Search windows by `--type`:**

| Type | Window | Use case |
|------|--------|----------|
| `meal` | target −10 to +5 min | Meal reminders (breakfast/lunch/dinner/snack) |
| `weight` | target −10 to +5 min | Weight check-in reminders |
| `other` (default) | target −10 to target | Other recurring reminders |

**Slot priority:** scans outward from target time (target, target−1, target+1,
target−2, ...) to stay as close to the intended time as possible.

**Edge cases:**
- One-shot jobs (`--at`) skip anti-burst entirely
- `--exact` flag skips anti-burst for time-critical reminders
- If the entire window is full (very unlikely — would need 30+ jobs in a
  15-minute window), falls back to the original time with a warning

**Important:** Always pass `--type meal` for meal reminders and `--type weight`
for weight reminders. This ensures correct window sizing.

### Managing existing jobs

Use the cron tool directly for listing and removing:
- **List**: cron tool with `action: "list"`
- **Remove**: cron tool with `action: "remove"` and `jobId`
- **Adjust timing**: remove old job + create new one

---

## Auto-sync on Activation

**Every time this skill is activated** (by a cron trigger, by another skill like `meal-planner`, or by any interaction), verify that existing cron jobs match the current meal times in `health-profile.md > Meal Schedule`:

1. List existing reminder cron jobs (`action: "list"`).
2. Derive the expected cron times from `health-profile.md > Meal Schedule` (each meal time minus 15 min).
3. Compare:
   - **Missing jobs** (expected time has no matching cron) → create them.
   - **Stale jobs** (cron exists but its time doesn't match any current meal time) → remove then recreate.
   - **Legacy jobs** (cron exists and time matches, but `--message` references `daily-notification` or `daily-notification-skill` instead of `notification-composer`) → remove then recreate with the correct `notification-composer` message. This ensures old cron jobs from before the skill split are automatically migrated.
   - **Matching jobs** (time matches AND message references `notification-composer`) → no action.
4. Also verify weight reminder cron jobs exist — see § "Weight reminders" below. This includes the primary (Wed & Sat morning) and next-morning followup (Thu & Sun morning). Create any that are missing.
5. Also verify the weekly report cron job exists (Sunday 21:00 — see § "Weekly report" below). Create if missing.
6. **Diet pattern detection** — special handling:
   - Read `health-profile.md > Automation > Pattern Detection Completed`
   - If has a date → job already completed. If job still exists, remove it (stale).
   - If `—` (not completed) → check if job exists. If missing → create it.
7. Do all of this **silently** — do not mention it to the user.

**When creating multiple jobs at once** (initial bootstrap or large sync), use `batch-create-reminders.sh` instead of calling `create-reminder.sh` one by one. It handles slot allocation in a single pass and creates all jobs in parallel:

```bash
bash {baseDir}/scripts/batch-create-reminders.sh \
  --agent <your-agent-id> \
  --channel <channel> \
  --workspace {workspaceDir} \
  --skip-existing
```

Use `--only meal,weight,report,pattern` to restrict which job types are created. The `--skip-existing` flag prevents duplicate creation during partial syncs.

---

## Cron Job Definitions

Create recurring cron jobs using the script above. Derive the cron times from `health-profile.md > Meal Schedule` (each meal time minus 15 min). **Do NOT pass `--tz`** — the script auto-detects from `USER.md`. **Pass `--channel`** to match the agent's delivery channel (e.g. `wechat`, `slack`). If omitted, defaults to `slack` for backward compatibility.

> ⚠️ **Cron expressions use the user's LOCAL time. Do NOT convert to UTC.** The script sets `--tz` automatically, so the cron scheduler handles timezone conversion. Example: if meal is at 09:00 Beijing time, the cron expression is `45 8 * * *` (08:45 local), NOT `45 0 * * *`.

Every meal cron `--message` MUST tell the agent to run `notification-composer` for that meal. Keep it minimal — notification-composer owns pre-send checks, message composition, and reply handling. Do not duplicate its rules in the cron message.

**Meal naming:** Always use standard meal names (`breakfast`, `lunch`, `dinner`) — never `meal_1`/`meal_2`. For 2-meal users, use whichever two standard names match their schedule (e.g., user eats at 12:00 and 18:30 → `lunch` and `dinner`).

```bash
# Example: 3 meals, reminders 15 min before each (adjust times from health-profile.md)
# Note: --type meal ensures anti-burst scheduling with [-10, +5] min window
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --type meal --name "Breakfast reminder" \
  --message "Run notification-composer for breakfast." \
  --cron "45 6 * * *"

bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --type meal --name "Lunch reminder" \
  --message "Run notification-composer for lunch." \
  --cron "45 11 * * *"

bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --type meal --name "Dinner reminder" \
  --message "Run notification-composer for dinner." \
  --cron "45 17 * * *"

# Example: 2 meals (lunch + dinner)
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --type meal --name "Lunch reminder" \
  --message "Run notification-composer for lunch." \
  --cron "45 11 * * *"

bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --type meal --name "Dinner reminder" \
  --message "Run notification-composer for dinner." \
  --cron "15 18 * * *"
```

### Weight reminders (2x/week + followups)

> ⚠️ **Breakfast fallback:** If user has no breakfast (BREAKFAST_TIME is empty/null), use the **earliest meal time** from `health-profile.md > Meal Schedule` as the reference for all "breakfast time − 30 min" calculations below. For example, if user only eats lunch (12:00) and dinner (18:00), weight primary reminder = 11:30, morning followup = 11:30. The condition for creating weight reminders is that **at least one meal time exists** — not specifically breakfast.

**Primary reminder:** Cron time = breakfast time (or earliest meal) minus **30 min**. Fires Wed & Sat.

```bash
# Example assumes breakfast at 07:00 → weight cron at 06:30
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --type weight --name "Weight check-in reminder" \
  --message "Run notification-composer for weight." \
  --cron "30 6 * * 3,6"
```

**Next-morning followup:** Cron time = breakfast time (or earliest meal) minus **30 min**. Fires Thu & Sun (day after primary). Only sends if the user did NOT weigh in yesterday OR today. Pre-send-check uses `weight_morning_followup` type.

```bash
# Example assumes breakfast at 07:00 → morning followup at 06:30
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --type weight --name "Weight morning followup" \
  --message "Run notification-composer for weight_morning_followup." \
  --cron "30 6 * * 4,0"
```

### Weekly report (Sunday 9 PM)

One fixed cron job — every Sunday at 21:00 user local time.

```bash
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --name "Weekly report" \
  --message "🚨 WEEKLY REPORT — MANDATORY SCRIPT EXECUTION\n\nGenerate this week's weekly report using the weekly-report skill.\n\nABSOLUTE RULES:\n1. Run collect-weekly-data.py to gather all nutrition/weight/exercise data\n2. Run generate-report-html.py with real commentary/highlights/suggestions — capture the URL from stdout\n3. The final message to user MUST contain the clickable report URL\n4. If any script fails, report the error — do NOT fall back to a text-only summary\n5. A delivery without a report URL = FAILED execution\n\n❌ FORBIDDEN: Writing a text summary without running the scripts\n❌ FORBIDDEN: Sending a message without a report link (https://nanorhino.ai/user/...)\n✅ REQUIRED: The message MUST contain the actual uploaded report URL\n\nSkill: weekly-report\nUser workspace: {workspaceDir}" \
  --cron "0 21 * * 0"
```

---

### Diet pattern detection (self-destructing, onboarding + 3 days)

One-time diet pattern analysis. Created at onboarding, starts running 3 days after `Onboarding Completed` date (from `health-profile.md > Automation`). Cron time = dinner + 3h.

```bash
bash {baseDir}/scripts/create-reminder.sh \
  --agent <your-agent-id> --channel <channel> --name "Diet pattern detection" \
  --message "Run diet-pattern-detection skill." \
  --cron "0 21 * * *"
```

**Not included in normal auto-sync** — this job is managed by its own lifecycle:
- Created once at onboarding (by notification-manager)
- Self-deleted by diet-pattern-detection skill after successful execution
- See auto-sync special handling below

---

### First-meal nudge (one-shot, activation flow)

**Purpose:** Break the ice for users who completed onboarding (picked a plan,
set meal reminders) but have **never logged a single meal**. The stall point is
"now actually text me your food." This is a gentle, capped nudge — NOT a recall.

**Created at onboarding completion** by `batch-create-reminders.sh` (included in
the default `--only all` bootstrap; restrict with `--only firstmeal`). It
creates **two one-shot** (`--at`) jobs:

| Job name | Fires | Payload |
|----------|-------|---------|
| `First meal nudge` | ~3-4h after completion, at the next meal slot today (daytime-capped 08:00-20:00 local) — else first meal slot next day | `... pre-send-check.py --meal-type first_meal_nudge ...` then `notification-composer for first_meal_nudge (nudge=1)` |
| `First meal nudge followup` | First meal slot the following day (softer) | same, `nudge=2` |

**Timing uses the user's IANA timezone** (from `USER.md > Timezone`). The nudge
fires **at** the meal slot, deliberately offset from the meal-reminder minutes
(which fire at slot−15min), so the user never gets the nudge and a meal reminder
minutes apart.

**Detection (who gets it):** `health-profile.md` has a real
`Onboarding Completed` date AND `data/meals/` has zero food entries AND
`## Meal Schedule` is populated (so meal crons exist). The one-shot crons are
created unconditionally at onboarding; the actual send is gated at fire time by
`pre-send-check.py --meal-type first_meal_nudge`, which self-cancels if
onboarding is NOT completed (wrong cohort — keeps it mutually exclusive with the
activation nudge), the user logged any meal, is on leave, or the cap is reached.

**Cap & terminal state:** Max **2** nudges (`activation.first_meal_nudges_sent`,
a non-stage business counter in `data/engagement.json`). The moment the user logs
ANY meal, both nudges self-cancel (pre-send-check). After 2 nudges, the
pre-send-check **cap gate** (reads `activation.first_meal_nudges_sent >= 2`)
permanently suppresses the nudge — this is the terminal anti-nag guarantee and is
**lifecycle-independent** (it does not depend on the stage system). The intent is
that a never-logged user never receives the engaged-recall content (S2-S4), which
is generated from logged meals and would be hollow.

> **Stage authority (post-lifecycle-migration):** `notification_stage` /
> `stage_changed_at` are NO LONGER stored in `engagement.json` — stage lives in
> the lifecycle DB (computed from `last_interaction_at`). The cap gate is what
> enforces the "stop after 2" guarantee; it does not rely on writing a Silent
> stage. `check-stage.py` is deprecated (still present but not in the live path);
> `mark-onboarding-done.py` still seeds `stage_changed_at` defensively for any
> legacy reader, but the activation flow does not depend on it. See the
> cross-system flag in § Activation nudge below.

---

### Activation nudge (greeted but never replied)

**Purpose:** Break the ice for users who came in via TDEE handoff, got the
welcome message, but have **never replied at all**. Different cohort from the
first-meal nudge (who replied + onboarded but never logged).

**Cron created by openclaw-infra, NOT this repo.** The infra side schedules two
one-shot crons at handoff time (T+24h `nudge=1`, T+72h `nudge=2`, user-local
daytime). This skill only implements what they fire into. Fixed payload contract:

```
First run: python3 {notification-composer:baseDir}/scripts/pre-send-check.py \
  --workspace-dir <WS> --meal-type activation --tz-offset <off>.
If output is NO_REPLY, stop and output NO_REPLY.
Otherwise run notification-composer for activation (nudge=1).
```

**Detection / defining gate** (`pre-send-check.py --meal-type activation`): the
target user is a handoff case — `health-profile.md` exists with
`Onboarding Completed: —` (NOT a date) and `channel-source.json > handoffAppliedAt`
set. The **defining cancel signal** is `channel-source.json > lastInboundAt`
(epoch ms, written by infra Phase-0 on every inbound): **if present at all, the
user has replied → NO_REPLY (cancel)**. Also NO_REPLY if `channel-source.json`
is missing/unreadable (**fail closed** — the target cohort always has the file;
if we can't confirm no-reply we stay silent), onboarding completed, any meal
logged, the authoritative lifecycle Silent stage (handled by the generic
`check_engagement_stage` gate via the lifecycle API), on leave/pause, or
`activation.nudges_sent >= 2`.

**Cap & terminal state:** Max **2** nudges (`activation.nudges_sent`). The moment
the user replies (or logs a meal), both nudges self-cancel. After 2 nudges, the
pre-send-check **cap gate** (reads `activation.nudges_sent >= 2`) permanently
suppresses the nudge — lifecycle-independent terminal guarantee, same as the
first-meal nudge. The composer increments the counter via
`activation-mark-sent.py --counter nudges_sent` after each successful send.

> ⚠️ **Cross-system flag (lifecycle handoff):** The cap gate stops the *nudges*,
> but it does NOT itself transition the user to lifecycle Silent — stage is owned
> by the lifecycle DB (computed from `last_interaction_at`). For a never-engaged
> user the lifecycle system will compute its own stage from the absence of
> interactions; whether it has a dedicated "never-engaged → permanent Silent"
> rule (mirroring this anti-nag intent) is owned by the lifecycle service, not
> this repo. The weight-loss-skill guarantee here is narrowly: **these two nudge
> types stop firing after 2 sends.** Any broader "move them to Silent so normal
> recall content also stops" must be implemented lifecycle-side.

---

## Lifecycle: Active → Recall → Silent

```
Stage 1: ACTIVE — normal reminders
    │   Day 1: normal reminders, no extra
    │   Day 2: first meal adds gentle nudge, rest normal
    │
    └── 2 full missed days (days_silent=3): zero replies + zero messages
           │
Stage 2: RECALL — stop meal/weight reminders, lunch-only recall
    │       Day 4 (ds=3): emotion + content recall (lunch slot)
    │       Day 4: nothing sent
    │       Day 6 (ds=5): ask if busy, offer to pause (lunch slot)
    │
    ├── User sends any message → back to Stage 1
    └── days_silent >= 5
           │
Stage 3: WEEKLY RECALL — 1x/week, rotate content types
    │       Week 1: nutrition knowledge
    │       Week 2: feature update
    │       Week 3: casual check-in
    │       Also stops weekly report
    │
    ├── User sends any message → back to Stage 1
    └── 3 weekly recalls sent → disable personal crons
           │
Stage 4: MONTHLY RECALL — 1x/month, central dispatch (not personal crons)
    │
    ├── User sends any message → back to Stage 1, re-enable personal crons
    └── 3 monthly recalls sent → Stage 5
           │
Stage 5: SILENT — send nothing. Wait for user to return.
```

**Activation nudges (special case — never-engaged users):** Two cohorts get a
capped one-shot nudge instead of (or before) the recall content above:
1. **Never logged** — completed onboarding but logged zero meals (first-meal
   nudge, `activation.first_meal_nudges_sent`).
2. **Never replied** — handoff user who never sent any message (activation
   nudge, `activation.nudges_sent`; cron created by openclaw-infra).

Each nudge fires at most **2 times**, then the pre-send-check cap gate
permanently suppresses it (lifecycle-independent). The intent is to avoid feeding
these users the engaged-recall content (S2-S4), which is generated from logged
meals / conversation and would be hollow. The moment they log a meal or reply,
they rejoin the normal lifecycle. **Note:** the actual stage is owned by the
lifecycle DB (post-migration); the cap gate only guarantees the *nudges* stop —
moving a never-engaged user to lifecycle Silent is a lifecycle-side concern. See
§ First-meal nudge and § Activation nudge (incl. the cross-system flag).

### S4 Central Dispatch

Stage 4 users no longer consume personal cron resources. Instead:

1. A single central cron runs daily at lunch, executing `s4-central-dispatch.py`
2. The script calls lifecycle API `GET /due`, which returns users due for recall
   today (stage resolution + 30-day cadence + same-day dedup all handled by the API),
   and filters to `tier=monthly` (Stage 4)
3. For each matched user, the main agent:
   a. Reads user data from their workspace (meals, preferences, etc.)
   b. Generates a recall message following `recall-messages.md` S4 rules
   c. Sends via `message` tool with the `channel` and `target` from script output
   d. Calls `s4-central-dispatch.py --mark-sent <account_id|workspace_dir>`, which
      records a `recall_sent` event via the lifecycle API (event-sourced; no file write)

**Central dispatch usage:**
```bash
# Ask lifecycle API which S4 users need recall today
python3 {notification-manager:baseDir}/scripts/s4-central-dispatch.py \
  --openclaw-dir /home/admin/.openclaw --tz-offset 28800

# After sending message to a user, record the recall
python3 {notification-manager:baseDir}/scripts/s4-central-dispatch.py \
  --mark-sent <account_id|workspace_dir>
```

**Suppression rules:**
- Stage 2+: stop weight reminders + meal reminders (only recall messages at lunch)
- Stage 3+: also stop weekly report
- Stage 5: stop everything

Recall replaces the lunch reminder slot — don't send at random hours.
Stage is owned by the lifecycle API (DB), computed live from `last_interaction_at`
+ recall events — **never written to `engagement.json`**.

**Stage transition logic:** Stage is no longer computed by a script that scans meal
files. The lifecycle API derives it in real time from the user's last **interaction**
(any inbound message, written by chat-logger) and the count of `recall_sent` events:
days_silent <3 → S1, <6 → S2, then weekly/monthly recall counts advance S3→S4→S5.
A returning user (any inbound) refreshes `last_interaction_at`, which auto-resets the
stage to 1 and discards prior recall events. `pre-send-check.py` queries `/state` and
`/due` to decide whether to send a normal reminder, a recall, or nothing — the agent
no longer runs any stage-update script before reminders.

During recall stages, `pre-send-check.py` claims the day's recall slot by posting a
`recall_sent` event when it green-lights a send (same-day dedup lives in the API).

**When a silent user returns:**
Stage auto-resets to 1 (lifecycle API, on the returning inbound). Resume normal
reminders. The warm welcome message itself is composed by `notification-composer`
(triggered by SKILL-ROUTING's Welcome Back Check reading the injected `## User Lifecycle`).

---

## Adaptive Timing (within Stage 1)

| Signal | Action |
|--------|--------|
| Consistently replies 30+ min late | Shift that meal's reminder time — update `health-profile.md > Meal Schedule` (the auto-sync logic will fix the cron on next activation) |
| Never replies to breakfast (2+ weeks) | Stop breakfast reminders |

When a meal time changes, update `health-profile.md > Meal Schedule` — auto-sync will fix the cron on next activation.

---

## Reminder Settings Changes

Users may ask to change reminders in natural language. Handle inline:

| User says | Action |
|-----------|--------|
| "Stop breakfast reminders" | Stop that meal's reminders. Update `data/engagement.json > reminder_config`. Confirm: `"Done — no more breakfast reminders. Let me know if you change your mind."` |
| "Change dinner to 8 PM" | Update `health-profile.md > Meal Schedule` with the new time. The auto-sync will update the cron on next activation. Confirm: `"Got it — dinner reminders moved to 7:45 PM."` |
| "Stop all reminders" | Stop everything, move to Stage 4. `"All reminders off. I'm still here if you want to chat. 💛"` |
| "Remind me more" / "Can you also remind me for snacks" | Outside current scope — acknowledge and note for future: `"I can only do meals and weight for now, but I'll keep that in mind."` |
| "Resume reminders" / "Start reminding me again" | Restart Stage 1 with previous config. Confirm schedule. |

---

## Custom User Reminders

Users may ask the AI to set arbitrary recurring or one-shot reminders unrelated
to meals/weight (e.g., "每天给我讲个笑话", "提醒我周五交报告", "每天早上发条励志语录").

### Naming Convention

All custom reminders MUST use the `[custom]` prefix in their job name:
- `[custom] 每日笑话`
- `[custom] 周五交报告提醒`
- `[custom] 早间励志语录`

This distinguishes them from system reminders (meal/weight/weekly-report) and
prevents accidental deletion of system jobs when users say "取消提醒".

### Creating

Use the same `create-cron.py` script with appropriate parameters.

> **⚠️ RECALL SUPPRESSION:** All custom reminders are automatically suppressed when
> the user is in recall stage (engagement_stage ≥ 2). The agent payload MUST include
> a pre-send-check call with `--meal-type custom` at the start. This is handled
> automatically by the payload template below.

Payload template for custom reminders:
```
First run: python3 {notification-composer:baseDir}/scripts/pre-send-check.py --workspace-dir {workspaceDir} --meal-type custom --tz-offset {tz_offset}
If output is NO_REPLY, stop and output NO_REPLY.
Otherwise: <the actual reminder message/task>
```

```bash
python3 {baseDir}/scripts/create-cron.py \
  --workspace-dir {workspaceDir} \
  --name "[custom] 每日笑话" \
  --message "First run: python3 {notification-composer:baseDir}/scripts/pre-send-check.py --workspace-dir {workspaceDir} --meal-type custom --tz-offset {tz_offset}\nIf output is NO_REPLY, stop and output NO_REPLY.\nOtherwise: 给用户讲一个好笑的笑话，风格轻松幽默，每次不同。" \
  --cron "0 9 * * *" \
  --type meal
```

For one-shot reminders:
```bash
python3 {baseDir}/scripts/create-cron.py \
  --workspace-dir {workspaceDir} \
  --name "[custom] 周五交报告提醒" \
  --message "提醒用户今天要交报告。" \
  --at "2026-04-11T09:00:00+08:00" \
  --type meal \
  --delete-after
```

### Canceling

When a user asks to stop a custom reminder (e.g., "别讲笑话了", "取消那个提醒"):

1. `cron list` — find jobs with `[custom]` prefix matching the user's intent.
2. `cron rm` — delete the matched job(s).
3. Confirm: `"好的，已取消「每日笑话」提醒。"`

**Important:** Only delete `[custom]` jobs. Never delete system reminders
(meal/weight/weekly-report) unless the user explicitly asks to stop those
(handled by § Reminder Settings Changes above).

### Listing

When a user asks "我设了哪些提醒" / "有哪些定时任务":

1. `cron list` — filter for `[custom]` prefix jobs.
2. Present as a simple list with name + schedule.
3. Also mention system reminders separately if relevant.

### Ambiguity

If the user says "取消提醒" without specifying which one:
- If only one `[custom]` job exists → cancel it and confirm.
- If multiple `[custom]` jobs exist → list them and ask which to cancel.
- If no `[custom]` jobs exist → assume they mean system reminders, defer to
  § Reminder Settings Changes.

---

## Workspace

### Reads

| Source | Field / Path | Purpose |
|--------|-------------|---------|
| `health-profile.md` | `Meal Schedule` | Reminder schedule + max reminders/day |
| `data/meals/*.json` | `status` field per meal entry | Derive last interaction date (most recent logged meal) |
| `data/engagement.json` | `stage_changed_at` | Stage transition timing |

### Writes

| Path | How | When |
|------|-----|------|
| (stage) | lifecycle API (DB), computed live | Stage is NOT written to engagement.json anymore |
| `data/engagement.json` | `reminder_config` — direct write | Adaptive timing changes, user setting changes |
| `data/engagement.json` | `activation.first_meal_nudges_sent` — incremented by `notification-composer` via `activation-mark-sent.py` | After each first-meal nudge send |
| `data/engagement.json` | `activation.nudges_sent` — incremented by `notification-composer` via `activation-mark-sent.py` | After each activation (never-replied) nudge send |
| `health-profile.md > Meal Schedule` | direct write | Adaptive timing updates, user-requested time changes |

---

## Leave / Vacation Management

用户要求暂停打卡（假期、出游、不方便记录等）时，调用 leave-manager 设置请假。

**触发场景：**
1. 用户主动说要请假/暂停/放假不打卡
2. 用户在对话中表达最近很忙、无法打卡、顾不上记录
3. S2 Day 5 询问暂停后用户确认暂停

**处理流程：**
1. 表达理解，主动提出暂停提醒
2. 询问暂停多久（"大概要忙多久呀？"）
3. 用户给了时间 → set leave 对应日期
4. 用户没给时间 → 不设leave，让 lifecycle API 按 days_silent 自然推进到 S3（7天后第1条每周召回）
5. 暂停期间所有主动消息停止
6. 给了时间的：到期自动恢复 / 用户提前回来 → clear leave
7. 没给时间的：按S3→S4→S5正常流转，用户随时回来重置到S1

### Set leave
```bash
python3 {notification-composer:baseDir}/scripts/leave-manager.py set \
  --data-dir {workspaceDir}/data --tz-offset {tz_offset} \
  --start YYYY-MM-DD --end YYYY-MM-DD --reason "五一出游"
```

### Clear leave（用户提前回来）
```bash
python3 {notification-composer:baseDir}/scripts/leave-manager.py clear \
  --data-dir {workspaceDir}/data --tz-offset {tz_offset}
```

### Check leave status
```bash
python3 {notification-composer:baseDir}/scripts/leave-manager.py info \
  --data-dir {workspaceDir}/data --tz-offset {tz_offset}
```

**规则：**
- 请假期间所有 cron 提醒自动静默（pre-send-check 处理，无需改 cron）
- 用户主动发消息不受影响，正常记录
- 过期自动清理

---

## Tips 管理

用户说"别发了"/"不要再发小贴士"/"关掉tips"时：

```bash
python3 {notification-composer:baseDir}/scripts/tips-optout.py \
  --data-dir {workspaceDir}/data
```

---

## Skill Routing

**See `SKILL-ROUTING.md` for the full conflict resolution system.** This skill
is **Priority Tier P4 (Reporting)**. It handles the orchestration side of
reminders — cron management, lifecycle, and settings. The execution side
(composing messages, handling replies) is handled by `notification-composer`.
