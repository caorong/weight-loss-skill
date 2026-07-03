---
name: diet-pattern-detection
version: 1.0.0
description: "One-time diet pattern analysis. Runs via daily cron after onboarding, checks if user's actual eating pattern matches a different diet mode. Self-destructs after successful execution. NOT user-triggered — cron-only."
metadata:
  openclaw:
    emoji: "bar_chart"
    homepage: https://github.com/NanoRhino/weight-loss-skill
---

# Diet Pattern Detection

> ⚠️ **Cron-only skill.** Never triggered by user messages. Activated by a daily cron job created by `notification-manager` at onboarding completion.

## Trigger

Daily cron job. Start date: `Onboarding Completed` + 3 days (from `health-profile.md > Automation`). Cron time: dinner_time + 3h.

## Workflow

1. **Check precondition** — read `health-profile.md > Automation > Pattern Detection Completed`. If already has a date → skip (should not happen, but defensive).

2. **Run detection** (handles data sufficiency check internally):
   ```bash
   python3 {baseDir}/scripts/detect-pattern.py \
     --data-dir {workspaceDir}/data/meals \
     --current-mode <from health-profile.md > Diet Config > Diet Mode> \
     --weight <current weight from latest weight.json entry> \
     --target-weight <target weight from PLAN.md>
   ```
   If result has `reason: "insufficient_data"` (< 3 days) → reply exactly `NO_REPLY` (suppresses delivery; do NOT emit a free-text explanation). Cron preserved, retry tomorrow.

3. **Handle result**:
   - `has_pattern: true` → read `references/diet-pattern-response.md`, compose and send message to user. If user agrees to switch → update `health-profile.md > Diet Config > Diet Mode` and recalculate targets.
   - `has_pattern: false` → no message (pattern matches current mode, all good).

4. **Self-destruct** (when result is `has_pattern` true or false, NOT insufficient_data):
   1. List current agent's cron jobs (`cron list`)
   2. Find job with name containing "diet-pattern" or "Diet pattern"
   3. Delete it (`cron remove`)
   4. Write completion date to `health-profile.md > Automation > Pattern Detection Completed: <YYYY-MM-DD>`

## Reference Files

- `references/diet-pattern-response.md` — user notification template (when pattern detected)
