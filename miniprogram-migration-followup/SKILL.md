---
name: miniprogram-migration-followup
version: 1.0.0
description: "Follow-up onboarding for users migrated from the WeChat miniprogram: fills the coaching setup the miniprogram skipped (meal schedule, reminders, check-in intro). Trigger ONLY when workspace has `.from-miniprogram.json` AND lacks `.migration-followup-completed`. One-shot — never on subsequent messages."

metadata:
  openclaw:
    emoji: "arrows_counterclockwise"
    homepage: https://github.com/NanoRhino/weight-loss-skill
---

# Miniprogram Migration Follow-up

> ⚠️ **SILENT OPERATION.** Never narrate internal actions, skill transitions, or tool calls. No "Let me check...", "Now I'll transition to...", "Reading your profile...". Just do it.

## What this skill is for

Users who tried 小犀牛AI健康 (the WeChat miniprogram) go through a **lightweight onboarding** there — they answered body/activity/diet questions, received a plan, and saw a diet template. But the miniprogram cannot deliver scheduled messages, so these parts of the standard onboarding were **skipped**:

| Skipped in miniprogram | Why skipped | What to do here |
|------------------------|-------------|-----------------|
| Meal Schedule (breakfast/lunch/dinner times) | No reminders to anchor | **Ask once**, write to `health-profile.md > Meal Schedule` |
| Meal reminders (cron, ~15min before each meal) | Miniprogram has no cron | Create via `notification-manager` |
| Weigh-in reminder (cron) | Same | Create via `notification-manager` |
| Weekly review (cron) | Same | Create via `notification-manager` |
| Diet pattern detection (cron) | Same | Create via `notification-manager` |
| Daily check-in flow explanation | Not useful in miniprogram | **Explain once** here |

So the job of this skill is: **in a short, warm handoff, collect meal times, bootstrap all reminders, and explain how the daily check-in works from now on.** Then step out — the user returns to normal coaching.

## Trigger conditions (all three must hold)

Run this skill only when:

1. Workspace has a `.from-miniprogram.json` file at its root (written by the backend migration) — this is the signal the user came from the miniprogram.
2. Workspace does **NOT** have `.migration-followup-completed` at its root — this is the marker written at the end of Step 4 of this skill.
3. The user has just sent a message (any message, even "hi" / "在吗" / "你好").

If any of those fail, **do not run**. In particular:

- If the user is an old WeCom user (no `.from-miniprogram.json`) → do not run; use `user-onboarding-profile` or returning-user flow as usual.
- If this skill already ran once (`.migration-followup-completed` exists) → do not run; the user has been fully onboarded. Handle the message normally (maybe via `diet-tracking-analysis`, `emotional-support`, or whichever skill fits).

## Tone

Users here are **not new** — they already went through the miniprogram and know who you are. Do NOT re-introduce yourself. Do NOT ask their name (already in `USER.md`). Greet them by name warmly and move on. Keep it short.

---

## Step 1 — Warm reconnect

Read `USER.md` to get the user's name. Read `.from-miniprogram.json` to confirm we have migrated data.

Greet by name, acknowledge the transition, and preview what's coming. Keep this to 2 short sentences + 1 short question. Plain text, no markdown (see AGENTS.md R6 — no `**bold**`, no `#` headings, no `-` lists; use `•` if you need a bullet).

Example (adapt tone — don't copy verbatim):

> 嗨 Ivan，欢迎过来！之前咱们在小程序里聊过的都带过来了 😊
>
> 正式版这边能每天陪你打卡、定时提醒。先问一下，你一般几点吃早、中、晚饭？大概时间就行。

Accept flexible answers:
- "7点半、12点、6点半" → breakfast 7:30, lunch 12:00, dinner 18:30
- "我一般不吃早饭" → 2 meals/day, skip breakfast
- "10点早饭，下午三点一顿，晚上七点" → some users eat 3 meals at non-standard times — that's fine, just record what they say
- "不一定，有时候6点有时候9点" → pick the more common time, or ask once more gently

**Single-ask rule:** if the user gives vague or partial info, accept it. Don't drill.

## Step 2 — Save Meal Schedule (silently)

Once you have meal times:

1. Get current timestamp:
   ```bash
   python3 {user-onboarding-profile:baseDir}/scripts/now.py --tz-name <timezone from USER.md, default Asia/Shanghai>
   ```
2. Open `health-profile.md`, find the `## Meal Schedule` section, and fill it in using standard field names:
   - `- **Meals per Day:** 2` or `3` (integer)
   - `- **Breakfast:** HH:MM` (24-hour, only if they eat breakfast)
   - `- **Lunch:** HH:MM`
   - `- **Dinner:** HH:MM`
3. Update the file's `**Updated:**` header to the timestamp from step 1. Keep `**Created:**` unchanged.
4. **Never use "Meal 1"/"Meal 2"/etc.** Always use Breakfast/Lunch/Dinner standard names. If the user eats 2 meals (e.g. skips breakfast), only write Lunch and Dinner — do not write a placeholder Breakfast.

## Step 3 — Bootstrap all reminders

Activate `notification-manager` and run **in the background** (silent to the user):

```bash
bash {notification-manager:baseDir}/scripts/batch-create-reminders.sh \
  --agent <your-agent-id> \
  --channel wechat \
  --workspace {workspaceDir} \
  --skip-existing
```

This creates in one pass:
- Pre-meal reminders (breakfast / lunch / dinner, ~15 min before each)
- Daily weigh-in reminder
- Weekly review reminder (Sunday)
- Diet pattern detection (periodic)

`--skip-existing` makes the run idempotent — if for some reason a reminder already exists it won't be recreated. Safe to re-run.

**Do not narrate this step.** The user should not see any of "I'm setting up reminders" / "creating cron jobs" / etc. Just run it.

If `batch-create-reminders.sh` fails (e.g. script returns non-zero), **do not block the flow** — note the failure in your own internal state, continue to Step 4, and let the user message through normally. You can retry reminder creation later if the user complains about missing reminders.

## Step 4 — Explain the daily check-in flow + mark completed

In one message, confirm the reminders are on and briefly explain how check-in works from now on. Keep it ~5–6 short lines, plain text, maybe 1 emoji.

Example (adapt — don't copy verbatim):

> 好的，我会在早、中、晚饭前大概提前15分钟提醒你一下～
>
> 每天吃饭前，拍张照片或者文字告诉我吃了啥就行，我来估热量和营养。旁边放双筷子或握个拳头入镜，我估量能准很多！
>
> 如果超了或欠了，我会马上告诉你下一餐怎么调。每晚还会有个小复盘，周末给你看看周报～
>
> 除了饮食，你想让我做什么都可以说，比如提醒喝水、给采购建议。觉得哪里不对劲也说，我调。
>
> 咱们开始吧 💪

**After sending this message, write the completion marker:**

```bash
# workspace-relative path
touch "{workspaceDir}/.migration-followup-completed"
```

Also append a note to the existing `.from-miniprogram.json` is NOT required — leave that file untouched (it's an audit record from the backend).

## Step 5 — Hand off

Once the marker is written, this skill is done. The user's next message should be handled by whichever skill fits naturally:

- "刚吃了一碗面" → `diet-tracking-analysis`
- "有点焦虑" → `emotional-support`
- "改提醒时间" → `notification-manager`
- "食谱" → `meal-planner`
- etc.

Do not call this skill again. The marker guarantees it.

---

## What this skill does NOT do

- **Does NOT re-ask identity (name/age/sex/height)** — already in `USER.md`.
- **Does NOT re-ask goal/target weight/activity level** — already in `health-profile.md`.
- **Does NOT regenerate the plan** — `PLAN.md` was migrated from the miniprogram. If the user wants to update the plan, let them ask, then route to `weight-loss-planner`.
- **Does NOT regenerate the diet template** — the miniprogram already showed one. If the user wants a new one, route to `meal-planner`.
- **Does NOT create URL exports (HTML plan/meal-plan)** — the user saw the text version in the miniprogram. If they ask for a shareable link, route to `plan-export`.
- **Does NOT re-introduce the coach** — the user already knows who you are.

## Failure modes and what to do

| Situation | What to do |
|-----------|-----------|
| User's first message doesn't contain meal times and is unrelated ("哈喽"/"在吗") | Ask the meal-time question (Step 1), wait |
| User says "我不想要提醒" / declines reminders | Don't run `batch-create-reminders.sh`. Write `.migration-followup-completed` anyway so we don't re-ask. Move on. |
| User gives only 2 meal times (skips breakfast) | Fine — record 2 meals/day, no Breakfast field, cron only creates 2 pre-meal reminders. |
| `batch-create-reminders.sh` fails | Don't tell the user. Continue to Step 4 with the same explanation minus the "will remind you 15min before" line. Retry next time user asks about reminders. |
| User spontaneously asks "how does check-in work?" before Step 4 | Answer it then, then continue with Step 4 + marker. |

## References

- Miniprogram onboarding skill (what was done): `{miniprogram-onboarding-profile:baseDir}/SKILL.md` (lives in the miniprogram workspace, not in this repo)
- Standard (full) onboarding skill (what was skipped): `{user-onboarding-profile:baseDir}/SKILL.md`
- Reminder infrastructure: `{notification-manager:baseDir}/SKILL.md`
- Schedule file format: `health-profile.md > Meal Schedule` section (conventions in `docs/CONVENTIONS.md`)
