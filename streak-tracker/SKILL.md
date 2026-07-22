---
name: streak-tracker
version: 1.0.0
description: "Returns meal-logging streak data (current_streak, pending_milestone) — called BY other skills (notification-composer at Stage-1 meal reminders; weekly-report), never a standalone conversation skill. Only user-triggered case: explicit streak questions ('我连续打卡几天了', 'what's my streak'). Never proactively mention streaks; silence after a break."
metadata:
  openclaw:
    emoji: "fire"
    homepage: https://github.com/NanoRhino/weight-loss-skill
---

# Streak Tracker

> ⚠️ **SILENT OPERATION:** Never narrate internal actions or tool calls.

## Philosophy

1. **Celebrate presence, never punish absence.** Streak alive → amplify. Breaks → say nothing.
2. **Name the effort, not the number.** Connect milestones to what it took, not just the count.
3. **One celebration, then move on.** Don't revisit past milestones as motivation.
4. **Never compare.** Never reference longest streak or previous streaks.

## What Counts as a "Logged Day"

`data/meals/YYYY-MM-DD.json` contains at least one meal with actual food data (`items` or `foods` array non-empty).

## Script

```bash
# Get streak info (also persists to data/streak.json)
python3 {baseDir}/scripts/streak-calc.py info \
  --data-dir {workspaceDir}/data/meals \
  --workspace-dir {workspaceDir} \
  --tz-offset {tz_offset}

# Mark milestone as celebrated
python3 {baseDir}/scripts/streak-calc.py celebrate \
  --data-dir {workspaceDir}/data/meals \
  --workspace-dir {workspaceDir} \
  --tz-offset {tz_offset} \
  --milestone <number>
```

### `info` output

```json
{
  "current_streak": 7,
  "longest_streak": 14,
  "streak_start_date": "2026-03-26",
  "last_logged_date": "2026-04-01",
  "today": "2026-04-02",
  "pending_milestone": 7,
  "milestones_celebrated": [3]
}
```

- `pending_milestone`: highest uncelebrated milestone reached. `null` if none.
- On streak break (current < max celebrated), `milestones_celebrated` resets automatically.

## Opening Line Rules

### Daily streak line (non-milestone days)

When `current_streak >= 2` and `pending_milestone` is null:

- State count as `current_streak` directly — when today's meal isn't logged yet, the script already returns the streak as of yesterday (it counts runs ending at today OR yesterday), so no adjustment is needed. (Same rule as notification-composer § 每日连续打卡开场白.)
- Add a free half about getting to know the user's eating habits. One sentence. Vary daily.
- Themes: learning their taste, noticing patterns, feeling closer. Always food-related.

### Milestone celebration

When `pending_milestone` is not null, **replace** the daily line with a bigger celebration. After sending, call `streak-calc.py celebrate --milestone <n>`.

**Milestones and examples → `references/streak-milestones.md`**

### No streak line

When `current_streak < 2`: compose the opening normally. No streak mention.

## Break Handling

- Script resets `milestones_celebrated` automatically on new streak.
- **Say nothing.** Never mention a broken streak or compare to previous.
- New streak day 1 starts silently. Milestone 3 re-celebrates as if first time.

## User Asks About Streak

Run `streak-calc.py info` and respond naturally:
- Active: state count and start date.
- None: frame as invitation ("Log a meal and that's day one.").

## Data Schema — `data/streak.json`

Persisted on every `streak-calc.py info` run. Other skills can read directly.

```json
{
  "current_streak": 7,
  "longest_streak": 14,
  "streak_start_date": "2026-03-26",
  "last_logged_date": "2026-04-01",
  "milestones_celebrated": [3, 7]
}
```

`longest_streak` preserved across streak resets.

---

## Skill Routing

**Priority Tier: P4 (Reporting).** Data utility — doesn't own conversations.
See `SKILL-ROUTING.md` for conflict resolution.
