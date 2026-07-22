---
name: memory-consolidation
version: 1.0.0
description: "Internal memory maintenance — NOT triggered by user messages. Invoked by the agent after conversations and by scheduled consolidation runs (short/medium/long-term promotion and cleanup)."
---

# Memory Consolidation

> ⚠️ **SILENT OPERATION:** Never narrate internal actions, skill transitions, or tool calls to the user. No "Let me check...", "Now I'll transition to...", "Reading your profile...". Just do it silently and respond with the result.


## Role

You are the memory manager for the nutritionist agent. Your job is to maintain
structured, layered memory so the agent can provide continuity across sessions.

**Memory scope:** Record ALL important topics — not just diet/health. Work stress,
family events, social context, hobbies, mood patterns, life changes — anything
that came up in conversation and could influence future interactions.

---

## File Structure

```
{workspaceDir}/memory/
  short-term.json      ← Last 1-2 days of conversation summaries (rolling)
  medium-term.md       ← Topic-based knowledge (1 week – 1 month, incremental)
  long-term.md         ← Core user profile & milestones (permanent)
```

**Script path:** `python3 {baseDir}/scripts/memory-consolidator.py`

---

## Initialization

On first use (e.g. after onboarding), create empty memory files:

```bash
python3 {baseDir}/scripts/memory-consolidator.py init \
  --memory-dir {workspaceDir}/memory
```

Creates `short-term.json`, `medium-term.md`, and `long-term.md` if they don't exist.

---

## Layer 1: Short-Term Memory (`short-term.json`)

### What It Stores

Complete structured summaries of ALL conversations from the past 1-2 days.
Each conversation entry captures the topic, what was discussed, outcomes,
user mood, decisions made, and things to follow up on.

### When to Update

The agent decides when to update short-term memory. Recommended moments:
- After a meaningful exchange (meal log + discussion, emotional support, etc.)
- When a topic reaches a natural conclusion
- Before ending a session

**There is no hard rule** — the agent uses judgment. A quick "ok thanks" doesn't
need a memory entry; a 10-message discussion about changing diet modes does.

### How to Update

```bash
python3 {baseDir}/scripts/memory-consolidator.py short-term-update \
  --memory-dir {workspaceDir}/memory \
  --entry '{
    "date": "2026-03-06",
    "time": "12:15",
    "topic": "午餐打卡 + 工作压力",
    "skills_involved": ["diet-tracking", "emotional-support"],
    "summary": "吃了外卖炒饭，热量超标。用户表达了愧疚感，说最近项目上线压力大",
    "outcome": "做了情绪疏导，强调一顿超标不影响大局。用户情绪缓和",
    "mood": "guilty → relieved",
    "key_decisions": [],
    "follow_ups": ["关注项目上线期间的饮食支持"]
  }'
```

### How to Set Day Summary

After the day's last conversation (or when the agent judges the day is wrapping up):

```bash
python3 {baseDir}/scripts/memory-consolidator.py short-term-set-day-summary \
  --memory-dir {workspaceDir}/memory \
  --date 2026-03-06 \
  --summary "整体执行不错。午餐外卖超标跟项目压力有关。开始对运动感兴趣但有膝盖顾虑。"
```

### How to Read

```bash
python3 {baseDir}/scripts/memory-consolidator.py short-term-read \
  --memory-dir {workspaceDir}/memory
```

**Agent must read this at the start of every session.**

### Rotation

Keep only today and yesterday. Run rotation daily (during daily consolidation):

```bash
python3 {baseDir}/scripts/memory-consolidator.py short-term-rotate \
  --memory-dir {workspaceDir}/memory \
  [--today 2026-03-06]
```

Returns removed days — feed these into the medium-term consolidation step.

---

## Layer 2: Medium-Term Memory (`medium-term.md`)

### What It Stores

A topic-indexed knowledge base that captures patterns, key discussions, current
conclusions, and follow-up items across 1 week to 1 month. Topics grow organically
from conversations — they are NOT predefined.

**⚠️ NEVER record structured operational state in memory.** These states have their
own authoritative storage and change on their own schedule; memory copies of them
go stale and mislead future decisions:

- **Leave / pause / vacation state** — leave.json + lifecycle DB are the ONLY sources
  of truth. Do NOT write "暂停到 X月X日" / "leave until July 2" / "on vacation" /
  "user is frozen" or any date-bounded pause statement into medium-term.md or
  long-term.md. If a leave conversation must be summarized, say only "用户曾请过假
  (详见 leave.json 历史)" without dates or duration.
- **Cron reminder schedule / job IDs** — openclaw cron store is authoritative.
- **Lifecycle stage / days_silent / frozen** — lifecycle DB is authoritative.
- **Current weight / meal calories / streak counts** — data/weight.json, data/meals/,
  data/streak.json are authoritative.

Rule of thumb: if a value is a **date, count, or boolean that changes with system
events**, it belongs in structured storage, not memory. Memory records qualitative
patterns and human context around those events, not the events' state.

### Structure

Each topic follows this template:

```markdown
## [Topic Name]
- **整体表现/状态：** [1-sentence overview]
- **关键讨论：**
  - [MM-DD] [specific discussion point]
  - [MM-DD] [specific discussion point]
- **当前结论：** [current understanding based on discussions]
- **应对策略：** [optional — action guidelines for this topic]
- **待跟进：** [optional — things to confirm or revisit]
```

### When to Update (Daily Consolidation)

Run once per day — either at the end of the day's last conversation, or at the
start of the next day's first conversation. The flow:

1. **Rotate short-term** — get removed days' data
2. **Read medium-term** — load current topics (for context)
3. **For each conversation in the removed days:**
   - Determine which topic it belongs to (existing or new)
   - Use atomic commands to update medium-term.md
4. **Update the `Last consolidated` date**

```bash
# Step 1: Rotate and get removed data
python3 {baseDir}/scripts/memory-consolidator.py short-term-rotate \
  --memory-dir {workspaceDir}/memory

# Step 2: Read current medium-term (for context only)
python3 {baseDir}/scripts/memory-consolidator.py medium-term-read \
  --memory-dir {workspaceDir}/memory

# Step 3: Update using atomic commands (examples):
python3 {baseDir}/scripts/memory-consolidator.py medium-term-set-date \
  --memory-dir {workspaceDir}/memory --date 2026-05-25

python3 {baseDir}/scripts/memory-consolidator.py medium-term-append-discussions \
  --memory-dir {workspaceDir}/memory \
  --section "饮食记录习惯" \
  --entries '[{"date":"05-25","text":"早+午餐打卡，晚餐未记录"}]'

python3 {baseDir}/scripts/memory-consolidator.py medium-term-set-field \
  --memory-dir {workspaceDir}/memory \
  --section "饮食记录习惯" \
  --field conclusion \
  --value "早餐稳定，午晚餐打卡率波动"

# For new topics:
python3 {baseDir}/scripts/memory-consolidator.py medium-term-add-section \
  --memory-dir {workspaceDir}/memory \
  --section "新话题" \
  --overview "概述" \
  --discussions '[{"date":"05-25","text":"内容"}]' \
  --conclusion "结论"
```

⚠️ **NEVER use `edit` or `write` tool on medium-term.md.**
Always use the atomic script commands above.

### How to Read

```bash
python3 {baseDir}/scripts/memory-consolidator.py medium-term-read \
  --memory-dir {workspaceDir}/memory
```

Returns parsed sections. **Agent should read this at session start** (recommended
but not mandatory if short-term + long-term provide sufficient context).

### Stats (for cleanup decisions)

```bash
python3 {baseDir}/scripts/memory-consolidator.py medium-term-stats \
  --memory-dir {workspaceDir}/memory
```

Returns line count, section count, oldest/newest date references, and whether
the soft limit (500 lines) is exceeded.

### Soft Limit: 500 lines

When `over_limit` is true, the agent should use atomic commands:

```bash
# Remove entries older than 1 month
python3 {baseDir}/scripts/memory-consolidator.py medium-term-prune-discussions \
  --memory-dir {workspaceDir}/memory \
  --section "话题名" \
  --before "04-01"
```

Additionally:
1. Use `medium-term-set-field` to merge overlapping observations into updated conclusions
2. Promote stable conclusions to long-term memory
3. Remove resolved 「待跟进」items via `medium-term-set-field --field follow_ups`

---

## Layer 3: Long-Term Memory (`long-term.md`)

### What It Stores

The user's core personality, stable patterns, major milestones, important life
events, and lessons learned across the entire coaching relationship. This is the
"what I know about this person" file.

### Structure

```markdown
# Long-Term Memory

**Last updated:** YYYY-MM-DD

## Personality & Communication
- [stable personality traits and communication preferences]

## Core Health Patterns
- [long-standing diet/exercise/health patterns]

## Life Context
- [work situation, family, social life — things that affect health journey]

## Milestones
- [YYYY-MM-DD] [significant achievement]

## Important Events
- [YYYY-MM-DD] [event that shaped the journey]

## Lessons Learned
- [insights that should inform future coaching]
```

### When to Update (Weekly Consolidation)

Run once per week (can be combined with weekly-report). The flow:

1. **Run `long-term-stats`** — note current `line_count`
2. **`read`** the FULL `{workspaceDir}/memory/long-term.md` file
3. **Read medium-term** (via `medium-term-read`) — identify stable conclusions
4. **Compose** the COMPLETE updated long-term.md in context
5. **`write`** the complete file to `{workspaceDir}/memory/long-term.md` (FULL REPLACEMENT)
6. **Run `long-term-stats`** again — verify new `line_count` ≥ 80% of old

```bash
# Step 1 & 6: Check stats
python3 {baseDir}/scripts/memory-consolidator.py long-term-stats \
  --memory-dir {workspaceDir}/memory
```

⚠️ **NEVER use `edit` tool on long-term.md — always full `read` → `write`.**
⚠️ **Every existing section MUST appear in the written output. Do not truncate.**
⚠️ **Update `**Last updated:** YYYY-MM-DD` in the written content.**

### Soft Limit: 300 lines

When exceeded:
1. Merge redundant personality/pattern observations
2. Archive old milestones (keep only the most significant ones)
3. Condense lessons learned

### How to Read

Agent reads the file directly: `{workspaceDir}/memory/long-term.md`

**Agent must read this at the start of every session.**

---

## Monthly Cleanup

Once per month, run a cleanup pass on medium-term.md:

1. Run `medium-term-stats` to check line count and oldest entries
2. Use `medium-term-prune-discussions` to delete entries older than 1 month
3. Use `medium-term-set-field` to update stale conclusions based on recent data
4. Use `medium-term-set-field --field follow_ups` to clear resolved items
5. Promote any valuable long-standing patterns to long-term.md (via `read` → `write`)

---

## Session Start Checklist

Every new session, the agent should:

1. ✅ Read `memory/short-term.json` (mandatory)
2. ✅ Read `memory/long-term.md` (mandatory)
3. 📋 Read `memory/medium-term.md` (recommended)

This is defined in the agent's AGENTS.md template.

---

## Integration with Other Skills

| Skill | Interaction |
|-------|-------------|
| **All skills** | After handling a user message, the agent may call `short-term-update` to record the conversation |
| **notification-composer** | Can trigger daily consolidation (short→medium) during the morning check |
| **weekly-report** | Can trigger weekly consolidation (medium→long) alongside the report |
| **user-onboarding-profile** | After onboarding completes, call `init` to create memory files, then write initial long-term entries |

---

## Batch Mode (Cron Dispatcher)

When running as a **batch consolidation job** (triggered by the daily dispatcher cron,
not during a live conversation), follow this workflow instead of the real-time flow above.

### Context

You receive a prompt with:
- **User** account ID
- **Workspace** / **Memory dir** / **Session dir** paths
- **Tasks** to execute (one or more of the task types below)

### Step 1: Extract Conversations

Use the script to extract recent user messages from the session file:

```bash
python3 {baseDir}/scripts/memory-consolidator.py extract-conversations \
  --session-dir {sessionDir} \
  --hours 24
```

Returns JSON with `exchanges` — each exchange has `user_messages` and
`assistant_messages` with timestamps and cleaned text (metadata stripped).

### Step 2: Execute Tasks

Execute **only** the tasks listed in your prompt, in order:

#### `init`
Create missing memory files:
```bash
python3 {baseDir}/scripts/memory-consolidator.py init --memory-dir {memoryDir}
```

#### `short-term-update`
Using the extracted conversations from Step 1:
1. For each meaningful exchange, create a short-term entry via `short-term-update`
2. Group related messages into single entries (a quick "ok" doesn't need its own)
3. Write summaries in the user's language
4. **Read `{workspaceDir}/data/meals/{date}.json`** and `{workspaceDir}/PLAN.md` for the
   day's meal data and calorie target
5. **Always set `day_summary`** via `short-term-set-day-summary` — even if there were no
   meaningful conversations, generate a brief daily recap from meal data.
   
   The day summary should read like a nutritionist reviewing the day in 2-3 sentences:
   - How many meals were logged vs expected
   - Total calories vs target (approximate is fine)
   - Whether recommendations from the plan were followed
   - Any notable patterns (e.g., skipped dinner, high-carb lunch, good protein balance)
   
   Examples:
   - "记了三餐，总摄入约1180kcal，目标1400以内，控制得不错。午餐蛋炒饭碳水偏高但晚餐拉回来了，蛋白质整体偏低。"
   - "只记了早午餐（约900kcal），晚餐没记。午餐炸鸡排没按清淡路线走。"
   - "今天没有记餐数据，但聊了工作压力和情绪问题。"

#### `medium-term-consolidate`
1. Run `short-term-rotate` to get removed days
2. Run `medium-term-read` to get current topics (for context — do NOT use for edit)
3. For each removed conversation, determine target section and execute atomic updates:
   - `medium-term-set-date --date YYYY-MM-DD`
   - `medium-term-append-discussions --section "..." --entries '[...]'`
   - `medium-term-set-field --section "..." --field conclusion --value "..."`
   - For new topics: `medium-term-add-section --section "..." ...`
4. ⚠️ Do NOT use `edit` or `write` on medium-term.md — use ONLY atomic script commands

#### `medium-term-cleanup`
1. Run `medium-term-stats` to check state
2. Use `medium-term-prune-discussions --section "..." --before "MM-DD"` for entries >1 month old
3. Use `medium-term-set-field` to merge overlapping observations
4. Use `medium-term-set-field --field follow_ups` to clear resolved items

#### `long-term-promote`
1. Run `long-term-stats` — note current `line_count`
2. `read` the FULL long-term.md file
3. Run `medium-term-read` for promotion candidates
4. Compose the COMPLETE updated long-term.md (all sections, all content, nothing truncated)
5. `write` the complete file to long-term.md
6. Run `long-term-stats` — verify new `line_count` ≥ 80% of old
7. ⚠️ NEVER use `edit` on long-term.md — always full `read` → `write`

### Rules
- **SILENT operation.** No narration.
- Write in the **user's language** (Chinese for Chinese users).
- Memory scope is **BROAD** — diet, work, emotions, social life, hobbies, everything.
- Prefer **over-recording** to under-recording.
- Reply with a brief JSON summary: `{"tasks_completed": [...], "entries_added": N, "topics_updated": N}`


---

## Important Notes

- **The script handles I/O; the agent handles intelligence.** The script reads/writes/rotates files. The agent decides what to summarize, how to classify topics, and what's worth promoting.
- **Memory scope is broad** — not limited to diet/health. Record everything important: work, emotions, social life, hobbies, family, etc.
- **Topic categories grow organically** — don't force conversations into predefined buckets. If a new topic emerges, create a new section.
- **Prefer over-recording to under-recording** — it's easier to clean up excess memory than to recover lost context.
- **Never expose memory internals to the user** — the user should experience natural continuity, not see "I'm updating my memory files."
