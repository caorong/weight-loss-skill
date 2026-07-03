---
name: weight-tracking
version: 1.2.0
description: "Track body weight with full CRUD operations, unit conversion, and trend analysis. Trigger when user reports weight, asks about weight trend, wants to correct a weight entry, or change their unit preference. Trigger phrases: 'I weigh...', '体重...', '称了一下...', 'my weight is...', 'change to pounds', '改成斤', 'weight trend', '体重趋势'. ANY message containing a weight number or weight change — regardless of context (e.g. '我XX斤', '掉了X斤', '称了XX', 'my weight is...') — MUST trigger this skill and be recorded into data/weight.json; never reply verbally without saving. This skill owns recording every weigh-in and judging the everyday trend (encourage / noise / plateau). When weight is on a sustained upward trend and needs deeper intervention (cause analysis or adjustment strategy), defer to weight-gain-strategy; do NOT do that cause-analysis work here."
metadata:
  openclaw:
    emoji: "scales"
    homepage: https://github.com/NanoRhino/weight-loss-skill
---

# Weight Tracking

> ⚠️ **SILENT OPERATION:** Never narrate internal actions, skill transitions, or tool calls to the user. Just do it silently and respond with the result.
>
> Do NOT re-read this SKILL.md if it's already in context from a prior turn.

## Data Storage

**File:** `{workspaceDir}/data/weight.json` — a JSON object keyed by ISO-8601 datetime with timezone offset. Each entry: `{ "value": 80, "unit": "kg" }`, plus optional `"fasting": true` and `"note": "..."`. Only the save scripts write this file — never hand-edit it or write it with `python`/`exec`.

## Hard Rules — Weight Storage (MANDATORY)

Non-negotiable. Agents have historically stashed weight in ad-hoc files (`weight-log.md`) or as a dated ledger inside `PLAN.md`, leaving `data/weight.json` incomplete and breaking badges and dashboards. Don't repeat that.

1. **Always record, never just talk.** Any time the user mentions a weight figure or a change in weight — "72.5 this morning", "down 2 jin", "I weigh 79", "just weighed 80" — save it to `data/weight.json` via `save-and-check.py` (or `weight-tracker.py save`). A spoken reply alone never counts as recording. This is not limited to scheduled weigh-in days. **Exception:** past or hypothetical mentions ("I used to be 80kg") are not current readings — don't save those.

2. **Single source of truth.** `data/weight.json` is the one authoritative record of body weight.

3. **No alternative weight files.** Never create `weight-log.md` or any other file to hold weight data.

4. **Never write weight into `PLAN.md`.** `PLAN.md` carries only static plan parameters — start weight, target weight, calorie target, macro ranges — fixed at onboarding or recalculation. It is never updated per weigh-in.

5. **Read forward, never write back.** To show the current weight, read the latest value from `data/weight.json`. Never copy it back into `PLAN.md`.

6. **Only the scripts write weight.json.** Always save through `save-and-check.py` / `weight-tracker.py`. Never write `data/weight.json` directly with `python`/`exec`/file edits — doing so has produced off-schema entries (`{"morning": 60.45}` instead of `{"value": 60.45, "unit": "kg"}`) that break dashboards, weekly reports, and diagnostics. If you need to attach fasting/note context, use `--fasting` / `--note`, not a hand-built shape.

## Scripts

Script path: `python3 {baseDir}/scripts/weight-tracker.py`

- **Accept any common unit:** kg, lb, lbs, 斤 (=0.5 kg), 公斤. Pass what the user said as `--unit`; the script normalizes it.
- **Fasting tag:** if the user mentions an empty stomach / morning weigh-in, pass `--fasting` to record it on the entry.
- **Note:** pass `--note "..."` to attach free-text context (e.g. "post-workout", "clothed").

### Save + Context — `save-and-check.py` (preferred)

**Use this for all weight saves.** Saves weight + returns recent history and plan context.

```bash
python3 {baseDir}/scripts/save-and-check.py \
  --data-dir {workspaceDir}/data \
  --value 79.5 --unit kg \
  --tz-offset 28800 \
  --plan-file {workspaceDir}/PLAN.md \
  --health-profile {workspaceDir}/health-profile.md \
  --user-file {workspaceDir}/USER.md \
  [--correct] [--fasting] [--note "..."]
```

Returns:
```json
{
  "save": { "action": "created"|"updated", "key": "<datetime>", "value": 79.5, "unit": "kg" },
  "context": {
    "recent_weights": [...],
    "plan": { "target_weight": 55.0, "tdee": 1916, "calorie_target": 1800 },
    "active_strategy": { "active": false },
    "last_intervention_date": null
  }
}
```

### Save only — `weight-tracker.py save`

Use only when deviation-check is not needed (e.g., onboarding initial weight).

```bash
python3 {baseDir}/scripts/weight-tracker.py save \
  --data-dir {workspaceDir}/data \
  --value 79.5 --unit kg \
  --tz-offset 28800 \
  [--correct] [--fasting] [--note "..."]
```

- Auto-detects new vs correction: if last entry ≤ 30 min ago, overwrites. Otherwise creates new.
- `--correct`: force overwrite most recent entry (when user explicitly says "that was wrong")
- `--fasting`: record the reading as a fasting/morning weigh-in. `--note "..."`: attach free-text context.

### Load — `load`

```bash
python3 {baseDir}/scripts/weight-tracker.py load \
  --data-dir {workspaceDir}/data \
  --display-unit kg \
  [--last 7] \
  [--from 2026-02-01 --to 2026-03-06]
```

Always show weight in the user's preferred unit (pass it as `--display-unit`), rounded to 1 decimal.

### Correct / Delete / Change Unit

See `references/crud-operations.md` for `delete`, `update`, and `set-unit`.

## Workflow

### User Reports Weight

1. Get `tz_offset` and Unit Preference. Prefer the auto-injected **TOOLS.md Quick Reference** (`tz_offset`, `unit_preference`, `daily_cal_target`) — use those values directly. Only if Quick Reference is missing or outdated, fall back to reading `timezone.json` and `health-profile.md` (in parallel, first session only).
2. Call `save-and-check.py` with the user's `--value`/`--unit`/`--tz-offset` (full command under [Scripts](#scripts) above). Add `--correct` if the user is fixing a mistaken reading, `--fasting` for an empty-stomach/morning weigh-in, `--note "..."` for any extra context.
3. Read the response:
   - `save.action`: `"created"` → "Logged ✓"; `"updated"` → "Updated ✓". Show the value in the user's preferred unit.
   - `context.recent_weights`: recent weight entries
   - `context.plan`: plan targets (TDEE, target weight, etc.)
   - `context.active_strategy`: whether an intervention strategy is currently running
   - `context.last_intervention_date`: date of the last intervention
4. **Decide for yourself whether the weight trend deserves attention.** Default to just logging and confirming — **never comment on a weight change unprompted**; if the user is emotional about it, hand off to the `emotional-support` skill. Only speak up about the trend itself when it genuinely warrants it. The core test: **is the change moving in the same direction as the user's goal, and is any deviation a genuine trend rather than day-to-day noise?**
   - Weight swings ±0.5–1kg naturally every day (water, sodium, bowel movements). A single day inside that band is almost certainly noise and needs no reaction — it takes several days of data to confirm a trend is real.
   - **On track with the goal** (e.g. losing weight when the goal is to lose) → encourage, offer positive reinforcement.
   - **Everyday noise** (1–2 days of minor movement within the normal band) → acknowledge normally, don't probe.
   - **Likely a real deviation** (several consecutive days against the goal, or a clear rebound off a low that exceeds the normal swing) → reassure first, then gently offer to look into it together. Don't decide "it's nothing" for the user — hand them the choice.
   - **Plateau** (over a 14-day view, weight drifts within a narrow band with no progress toward the goal) → you **must** gently ask, "Your weight seems to have stalled lately — want to look into why together?" Don't diagnose the cause or hand out advice yourself; ask first whether they want to. If they say yes → go to cause-check-flow.
   - **Bar for raising it: only speak up when you're at least 80% confident the deviation or plateau is real.** An unnecessary question is also an intrusion.
   - If `active_strategy.active: true` (a strategy is already running), don't intervene again, but you may use `consensus` to remind them the strategy is still in motion. You can also look at the last few days of meal logs (`{workspaceDir}/data/meals/YYYY-MM-DD.json`) and give concrete feedback in light of that consensus.
   - If `last_intervention_date` is within the past 7 days, don't intervene again (covers both plateau and deviation prompts).
   - **Mark before you ask.** Whenever you decide to raise a question with the user — plateau or deviation alike — you **must call mark-intervention first, then output the question text**. Order: tool call → text output. This ensures the intervention is recorded even if the user never replies, so it won't fire again at the next weigh-in.
     ```bash
     python3 {baseDir}/scripts/save-and-check.py --data-dir {workspaceDir}/data --tz-offset <tz> --mark-intervention
     ```
     Once it succeeds, output the question text (e.g. "Want to look into why together?").
   - **User's perspective first.** Don't just read the scientific trend — think about how the user will feel seeing the number. A half-kilo gain makes people anxious, a week-long plateau breeds frustration, a hard-won drop that bounces back stings. Respond to the feeling, not to a cold number.

### User Asks for Trend / History

1. Call `load` with appropriate filters and `--display-unit`.
2. Present the data (table, summary, or trend description).

## References

| File | Contents |
|------|----------|
| `references/crud-operations.md` | Delete, update, set-unit commands + correction workflow |
| `references/deviation-workflow.md` | Deviation-check severity table, response guide, command |
| `references/integrations.md` | Which other skills use this skill's scripts |
