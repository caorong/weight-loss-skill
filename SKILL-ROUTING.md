---
name: skill-routing
version: 1.0.0
description: >
  Central arbitration layer for multi-skill conflict resolution. When a user
  message could trigger two or more skills simultaneously, this document defines
  how to decide priority, merge responses, or sequence execution. Every skill
  in this plugin MUST follow these rules. Read this file at the start of every
  conversation and consult it whenever a message matches multiple skill triggers.
---

# Skill Routing & Conflict Resolution

When a single user message matches triggers for multiple skills, follow the
rules below to decide what happens. The goal: **one coherent response per
message** — never duplicate logging, never give conflicting advice, never
make the user repeat themselves.

---

## Welcome Back Check (Global — ALL Skills)

**Before routing to any skill**, check the **`## User Lifecycle`** section that
is auto-injected into your system prompt by the lifecycle-stage plugin (no script
to run, no file to read — it's already in your context):

1. If that section shows **`welcome_back: TRUE`**:
   - Add a warm, brief welcome (1 sentence) before your normal response.
   - **Do not** clear any flag or run any script — the lifecycle service clears
     `welcome_back` automatically once your reply goes out (the outbound message
     is what marks the welcome as delivered).
   - If the section also shows `frozen: TRUE`, the user is on leave/pause — just
     respond normally, don't nudge for check-ins.
2. Otherwise → proceed normally.

> The `## User Lifecycle` section is the single source of truth for stage /
> welcome-back / frozen. Don't read `engagement.json` stage fields or run
> `check-stage.py` for this — those are deprecated and being removed.

**Welcome style:** Warm but brief. "好久不见！" / "欢迎回来～" — never mention how long they were gone.

---

## Pause/Leave Execution (Global — ALL Skills)

**After Welcome Back Check, before routing to any skill**, detect whether the user is expressing a pause/leave/vacation intent.

### Trigger phrases (inclusive, not exhaustive)

Direct: "暂停提醒"、"停一周"、"放假不打卡"、"pause reminders"
Implicit: "退下吧"、"先不弄了"、"别烦我"、"过段时间再说"、"两周后再来"、"最近不想减肥"、"不用管我了"、"让我休息一阵"

**Any message where the user clearly intends to stop receiving proactive contact for a period — regardless of whether they use the word "提醒" — triggers this rule.**

### Required action

When pause/leave intent is detected, you **MUST** call `leave-manager.py set` to write `data/leave.json`. Writing only to memory/short-term.json is **insufficient** — cron pre-send-check only reads `data/leave.json`.

```bash
python3 {notification-composer:baseDir}/scripts/leave-manager.py set \
  --data-dir {workspaceDir}/data --tz-offset {tz_offset} \
  --start YYYY-MM-DD --end YYYY-MM-DD --reason "用户原话摘要"
```

- `--start`: today
- `--end`: user-specified return date (or today + stated duration). If ambiguous (e.g. "过段时间"), ask user: "要帮你先暂停一周提醒吗？" — wait for confirmation before calling leave-manager.py.
- `--reason`: brief summary of user's words

### Workflow

1. Detect pause intent → read notification-manager SKILL if needed for exact command format
2. Call `leave-manager.py set` with appropriate dates
3. Confirm leave.json was created (check command exit code)
4. Reply to user: 告知具体恢复日期 + "想提前恢复随时跟我说"
5. Optionally also write to memory/short-term.json for LLM context (but this is secondary — system state in leave.json is what matters)

⚠️ 写 memory ≠ 暂停提醒。**只有 leave-manager.py set 才能阻止 cron。**

### What NOT to do

- ❌ Only write to memory/short-term.json without calling leave-manager.py
- ❌ Treat implicit pause requests differently from explicit ones — both require the same system operation
- ❌ Skip this check because the user didn't say "提醒" or "暂停"

---

## Priority Tiers

Skills are organized into priority tiers. Higher-tier skills take precedence
when conflicts arise.

| Tier | Skills | Description |
|------|--------|-------------|
| **P0 — Safety** | `emotional-support` (Category 5: escalation signals) | Crisis intervention. Overrides everything. |
| **P1 — Emotional** | `emotional-support` (Categories 1-4, 6-9) | Emotional presence takes priority over data collection. |
| **P2 — Data Logging** | `diet-tracking-analysis`, `exercise-tracking` | Recording what the user did. |
| **P3 — Planning** | `weight-loss-planner`, `meal-planner`, `restaurant-meal-finder`, `exercise-planning`, `habit-builder` | Designing programs and plans. |
| **P4 — Reporting** | `weekly-report`, `diet-profile-card`, `notification-manager`, `notification-composer` | Summaries and proactive outreach. |
| **P5 — Onboarding** | `user-onboarding-profile` | Profile building (only at start). |

**Rule:** When two skills from different tiers conflict, the higher-priority
skill leads. The lower-priority skill either merges into the response or
defers entirely (see Conflict Patterns below).

**Rule:** When two skills from the same tier conflict, use the Conflict
Patterns table to determine how they coordinate.

---

## Intent Detection

Before routing, parse the user message into **intents**. A single message
can contain multiple intents.

### Short Follow-Up Messages (Context Lookup)

When the user sends a very short message — `?`, `??`, `???`, `。`, `.`,
`怎么不说话`, `说话`, or similar nudge-like messages — **look at the
previous message(s) in the conversation that the agent has not yet responded
to.** These short messages almost always mean: "Why didn't you reply to what
I just said?"

**Resolution:**

1. **Find the last unanswered user message(s)** — scan backward through the
   conversation for the most recent user message(s) that received no agent
   reply (or only a partial/unrelated reply).
2. **Route based on that message, not the nudge.** The "?" itself carries no
   intent — the intent lives in the unanswered message. Parse intents from
   the unanswered message and route normally using the priority tiers below.
3. **Include all context** — if the unanswered message had attachments
   (photos, files), those are part of the context too. Process them together
   with the text of the unanswered message.
4. **Do NOT treat the nudge as a standalone message.** Never route "?" to
   diet-tracking just because there are food photos attached to a prior
   message — first read the text of that prior message to understand the
   full intent (it might be emotional, e.g., "有暴饮暴食了" + photos).

**Example:**
- User sends: `"有暴饮暴食了"` + food photos → (no agent reply)
- User sends: `"?"`
- Correct behavior: Route based on `"有暴饮暴食了"` + photos → emotional
  distress signal detected (binge eating) → `emotional-support` leads (P1),
  diet logging defers per Pattern 2A.
- Wrong behavior: Process only the photos as a food log → misses the
  emotional signal entirely.

### Examples

| User Message | Intents | Skills |
|---|---|---|
| "ran for 30 minutes, about to eat chicken breast" | exercise-log + food-log | exercise-tracking + diet-tracking |
| "I ran 5K! I'm so proud!" | exercise-log + positive-emotion | exercise-tracking + emotional-support |
| "ate too much again, I'm so fat" | after-meal-log + emotional-distress | diet-tracking + emotional-support |
| "weekly summary" (on Sunday) | weekly-report-request + exercise-weekly-summary | weekly-report + exercise-tracking |
| "make me a workout plan and a meal plan" | exercise-planning + meal-planning | exercise-tracking + meal-planner |
| "I'm having oatmeal, feeling great today!" | food-log + positive-emotion | diet-tracking + emotional-support |
| "skipped lunch, don't care anymore" | food-skip + emotional-distress | diet-tracking + emotional-support |
| "中午外面吃什么好？" | restaurant-recommendation | restaurant-meal-finder |
| "I'm at Chipotle, what should I order?" | restaurant-recommendation | restaurant-meal-finder |
| "附近有什么能吃的，帮我推荐一下" | restaurant-recommendation | restaurant-meal-finder |
| "想点外卖，有什么推荐的？" | restaurant-recommendation | restaurant-meal-finder |
| "我喝水很少" / "I don't eat breakfast" | behavior-self-report | habit-builder |
| "我吃饭太快了" / "I snack too much at night" | behavior-self-report | habit-builder |
| "我想养成早睡的习惯" | habit-request | habit-builder |
| "看看我的减脂档案" / "my profile" | profile-card-request | diet-profile-card |
| "给我看看我的减脂曲线 / 我的数据" | profile-card-request | diet-profile-card |
| "今天吃了多少" / "今日进度" | daily-data-query | personal-data-query |
| "这周怎么样 / 周报" | weekly-report-request | weekly-report |

---

## Conflict Patterns

### Pattern 1: Exercise Log + Diet Log (Same Tier — P2)

**Trigger:** User describes both exercise and food in one message.

Example: "ran for 30 minutes, then ate chicken breast"

**Resolution: Merge — single response, both logged.**

1. Parse and separate the exercise portion from the food portion
2. Log the exercise first (call exercise-calc, produce exercise JSON)
3. Log the food second (call nutrition-calc save, call evaluate)
4. Combine into one response:
   - Exercise summary (activity, duration, estimated calories burned)
   - Brief exercise feedback (1 sentence)
   - Meal details (food items, calories, macros)
   - Nutrition checkpoint summary
   - Suggestion (if needed)
5. Do NOT produce two separate response blocks or repeat greetings

**Key:** The exercise calorie burn is informational context — it does NOT
offset the diet checkpoint evaluation. Diet tracking evaluates intake against
the daily calorie target, not against net calories.

---

### Pattern 2: Data Logging + Emotional Signal (P2 vs P1)

**Trigger:** User logs food or exercise AND expresses emotion.

**Resolution: Emotion leads. Data follows silently.**

**Case A: Negative emotion** ("ate too much again, I'm so fat")
1. `emotional-support` takes the lead — acknowledge the feeling first
2. Do NOT log the food or respond with nutrition data in the first reply
3. If the user later provides specifics or calms down, log then
4. Write appropriate `flags.*` entries

**Case B: Positive emotion** ("I ran 5K! So proud!")
1. Celebrate first — `emotional-support` handles the win
2. Log the exercise in the same response, but AFTER the celebration
3. Keep the logging portion brief — don't let data overshadow the moment
4. Format: celebration (2-3 sentences) → brief log confirmation (1 line)

**Case C: Mild/ambiguous emotion** ("had a salad, feeling pretty good")
1. This is primarily a food log with a positive note
2. `diet-tracking-analysis` leads — log the food normally
3. Add a brief warm acknowledgment of the positive feeling (1 sentence)
4. No need to activate full `emotional-support` flow

**Threshold:** Full `emotional-support` activation requires signal strength
matching the categories defined in `emotional-support/SKILL.md`. Mild
positive comments ("good", "not bad") during food logging do not trigger
full emotional support — just acknowledge warmly inline.

---

### Pattern 3: Weekly Report + Exercise Weekly Summary (P4 + P2)

**Trigger:** User asks for a weekly summary, or it's Monday (auto weekly
report) or Sunday (exercise auto-summary).

**Resolution: Merge into weekly-report.**

1. `weekly-report` is the primary owner of weekly summaries
2. Exercise weekly data (total sessions, duration, calories burned, WHO
   comparison) is incorporated into the weekly report as a section
3. `exercise-tracking` does NOT produce a separate weekly summary
   if `weekly-report` is already generating one
4. On **Sunday** specifically: if the user sends a message that triggers
   exercise tracking AND it's Sunday, the exercise skill appends its
   weekly summary to the exercise response as usual. But if the user
   explicitly asks for a "weekly summary/report", route to `weekly-report`
   which includes exercise data
5. On **Monday**: `weekly-report` auto-generates and includes exercise data.
   No separate exercise weekly summary needed.

---

### Pattern 4: Exercise Planning + Meal Planning (Same Tier — P3)

**Trigger:** User asks for both a workout plan and a diet plan.

**Resolution: Sequence — follow user's stated order, with a sensible default.**

1. **Detect order from user message.** Whichever plan the user mentions
   first is the one you start with. E.g.:
   - "帮我做个饮食计划，再安排下训练" → meal first, then exercise
   - "I need a workout plan and a meal plan" → exercise first, then meal
2. Acknowledge both requests: "I'll help with both! Let me start with
   [first], then we'll work on [second]."
3. Complete the first planning conversation (collect profile, design
   plan, present it)
4. Transition to the second: "Now let's sort out [second] to go with
   your [first] plan."
5. The second planner can reference the first plan for alignment (e.g.,
   meal plan can factor in exercise recovery needs, or exercise plan
   can consider dietary constraints)

**Default when order is ambiguous** (user mentions both simultaneously
with no clear sequence): prefer exercise first, because the training
program determines recovery needs which influence meal timing and macro
emphasis. But this is a soft default — if the user redirects, follow
their lead.

---

### Pattern 5: Notification Composer + Other Active Skill (P4 vs Any)

**Trigger:** A scheduled reminder fires while the user is mid-conversation
with another skill.

**Resolution: Defer the notification.**

1. If an active conversation is in progress (user sent a message within
   the last 5 minutes), defer the reminder
2. The reminder fires at the next available gap, or is skipped for this
   slot if the meal gets logged during the conversation anyway
3. Never interrupt an emotional support conversation with a reminder

---

### Pattern 6: Onboarding + Any Other Skill (P5 vs Any)

**Trigger:** User is mid-onboarding but sends a message that triggers
another skill (e.g., logs food before finishing profile setup).

**Resolution: Complete onboarding first, then handle.**

1. Acknowledge what the user said: "Got it — I'll log that in a moment."
2. Finish collecting the remaining onboarding fields
3. After profile is saved, handle the queued action
4. Exception: P0/P1 signals (safety/emotional) always take priority,
   even mid-onboarding

---

### Pattern 7: Habit Check-in + Diet Logging (P3 + P2)

**Trigger:** User responds to a meal reminder that included a habit
mention, and their reply contains both food info and habit status.

**Resolution: Merge — diet leads, habit recorded inline.**

1. `diet-tracking-analysis` processes the food log as primary
2. Record the habit completion/miss to `habits.daily_log.{date}`
3. Include brief habit acknowledgment in the response (1 line max)
4. Do NOT produce separate habit and diet response sections

---

### Pattern 8: Restaurant Recommendation vs Meal Planner (Same Tier — P3)

**Trigger:** User asks about restaurant meals, which could be interpreted as
either an on-the-spot dining decision or a request to incorporate restaurants
into their weekly meal plan.

**Resolution: Intent determines routing.**

1. **Real-time dining decision** — "I'm at a restaurant now", "what should I
   order?", "附近吃什么好？" → `restaurant-meal-finder` leads. This is an
   immediate, actionable need.
2. **Planning context** — "I want more restaurant options in my meal plan",
   "help me plan meals with more eating-out days" → `meal-planner` leads.
   This is about the weekly plan structure.
3. **Ambiguous** — If unclear, default to `restaurant-meal-finder` (assumes
   the user has an immediate need). If the user clarifies they want a plan
   change, transition to `meal-planner`.

After the user selects a restaurant meal, `restaurant-meal-finder` hands off
to `diet-tracking-analysis` for logging. No conflict — sequential handoff.

---

### Pattern 10: Multiple Planning Requests (Same Tier — P3)

**Trigger:** User asks for a weight-loss plan while also wanting to
build a habit or get exercise programming.

**Resolution: Natural conversation flow — one at a time.**

1. Identify the most foundational request (usually: weight-loss-planner
   → meal-planner → exercise-planning → habit-builder)
2. Handle the foundational one first
3. Transition naturally to the next
4. Each planning skill's output can inform the next

---

## Response Merge Rules

When two skills co-execute in a single response, follow these formatting
rules to maintain coherence:

1. **Single greeting/opener** — never greet or open twice
2. **Logical flow** — order sections by what the user did chronologically
   (e.g., exercise happened before eating → exercise summary first)
3. **One suggestion section** — if both skills want to give suggestions,
   combine them or pick the most impactful one
4. **Shared context** — exercise calorie burn can be mentioned in the diet
   section as context ("You burned ~250 kcal running today — your intake
   target stays the same")
5. **One closing** — a single warm sign-off, not two
6. **No premature goodnight** — never add "晚安" / "goodnight" / 🌙 / 💤
   or other sleep-related sign-offs unless the **user** initiates it (e.g.,
   "我去睡了"). Logging the last meal of the day does not mean the
   conversation is over — the user may still want to chat, log snacks, or
   ask questions. Saying goodnight unprompted feels like the bot is ending
   the conversation.

---

## Deference Protocol

When a lower-priority skill must defer to a higher-priority one:

1. **Pause** the lower skill's workflow (no data collection, no logging)
2. **Do NOT queue questions** — don't stack "what did you eat?" behind
   an emotional support conversation
3. **Resume gently** — when the higher-priority skill completes, the lower
   skill can resume, but only if relevant. Use soft re-entry:
   - "Whenever you're ready — did you want to log that meal?"
   - NOT: "OK so back to tracking — what did you eat?"
4. **Write context** — if the lower skill had partial data (e.g., user
   mentioned food but emotional support took over), keep it in memory
   for when logging resumes

---

## Single-Ask Rule (Global)

**All question inquiries — except scheduled reminders — MUST be asked at most once.**

If the user does not answer a question, **do not repeat, rephrase, or follow up
on that question.** Accept the silence as a decline and move on with whatever
information you have. Use `null` or sensible defaults for the missing field.

This rule applies to:
- Onboarding questions (profile fields)
- Planning data collection (diet mode, meal schedule, preferences, exercise profile)
- Habit recommendations
- Follow-up questions during tracking ("want to add details?")
- Any clarifying question across all skills

This rule does NOT apply to:
- **Scheduled reminders** (`notification-composer` cron-based meal/weight reminders) —
  these follow their own lifecycle (Active → Pause → Recall → Silent) and are
  expected to recur on schedule.

**Rationale:** Repeated questions feel like nagging and erode trust. If a user
skips a question, they either don't know, don't care, or aren't ready. In all
three cases, asking again makes things worse. Move forward with what you have.

---

## Edge Cases

**User switches intent mid-conversation:**
If a user starts logging food but pivots to emotional distress, the
routing system re-evaluates on every message. The new intent takes
priority per the tier rules. Partially collected data is preserved.

**Ambiguous intent:**
When it's genuinely unclear which skill should handle a message, prefer
the skill that requires less user effort to correct. Logging a meal
when the user meant to just mention food casually is less disruptive
than ignoring a food log. When truly ambiguous, ask: "Want me to log
that, or were you just sharing?"

**Three or more skills triggered:**
Rare but possible (e.g., Sunday + exercise log + food log + positive
emotion). Apply the rules recursively: resolve the highest-priority
conflict first, then handle remaining conflicts at lower tiers.
Emotional support (P1) leads → exercise + diet merge (P2) → weekly
summary defers or merges (P4).

---

## Skill Cross-Reference

Each skill should be aware of routing. When implementing a skill response:

1. **Check this document** before responding to multi-intent messages
2. **Detect co-triggers** — scan the user message for signals belonging
   to other skills (use the trigger phrases defined in each SKILL.md)
3. **Yield when appropriate** — if another skill has higher priority,
   handle only your portion or defer entirely
4. **Never duplicate** — if another skill is already logging or
   responding to part of the message, don't repeat that work
