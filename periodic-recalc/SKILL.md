---
name: periodic-recalc
version: 2.2.0
description: "Recalculates the user's daily calorie target every 4 weeks based on current weight. Updates PLAN.md with new TDEE, calories, and macro ranges."
---

## ⚠️ Output Format (HARD RULE — Never violate)

当本 skill 需要给用户发消息时（无论是 recalculated 后的复盘+新方案、awaiting_weight 的称重请求、错误提示，还是任何场景），**输出的第一行必须**是以下格式的 section banner（按用户语言选择）：

| USER.md locale | 第一行 |
|---|---|
| `zh` / `zh-CN` / `zh-TW` / 未设置 | `🔄——周期性调整——🔄` |
| `en` / `en-*` | `🔄——Periodic Adjustment——🔄` |

接着是**一个空行**，然后才是正文。

✅ 正确（中文用户）：
```
🔄——周期性调整——🔄

🎉 4周复盘来啦
...
```

✅ 正确（英文用户）：
```
🔄——Periodic Adjustment——🔄

🎉 4-week review time!
...
```

**结构规则（不论语言）：**
- 必须以 `🔄——` 开头、`——🔄` 结尾（双 🔄 + em-dash 包裹）
- 中间文字按 locale 选择，不可自由发挥
- 接着必须空一行再接正文
- 不可用别的 emoji 替换 🔄
- 不可跳过空行直接接正文

❌ 错误（实测踩过的坑）：
- 把 banner 理解成任务标签/上下文标记（cron 注入的提示语只是触发上下文，不是你的输出指令）
- 用单个 🔄 emoji 开头（必须是双 🔄 包夹文字）
- 用别的 emoji 替换（🎉、📋、💡 等都不行）
- 英文用户也输出中文 banner

唯一例外：脚本输出 `action: "skipped"` 时静默退出，不输出任何消息（这种情况根本不发到用户，不存在格式问题）。

---

# Periodic Recalculation

## Step 0: Read PLAN.md + USER.md (MANDATORY — do this FIRST)

Before running the script, **read both `{workspaceDir}/PLAN.md` and `{workspaceDir}/USER.md`** and extract the following values. These files have no fixed format (Chinese/English/bullet/prose all work) — use your language understanding:

**From PLAN.md:**

| Field | CLI arg | Description |
|-------|---------|-------------|
| `current_calories` | `--current-calories` | Current daily calorie target (int) |
| `target_weight` | `--target-weight` | Target weight in kg (float) |
| `tdee` | `--tdee` | Current TDEE estimate (int; if not stated, pass `0` — script handles) |
| `activity` | `--activity` | One of: `sedentary` / `lightly_active` / `moderately_active` / `very_active` |
| `diet_mode` | `--diet-mode` | One of: `balanced` / `high_protein` / `low_carb` / `keto` / `mediterranean` / `plant_based` / `usda` / `if_16_8` / `if_5_2` |
| `cycle_start_date` | `--cycle-start-date` | When the current cycle started (ISO date). Read PLAN.md "Updated"/"Created" if available, else read `data/last-recalc-summary.json` `date`, else use 28 days ago |
| `weekly_rate` (optional) | `--weekly-rate` | Old cycle weekly rate in kg (float). Skip if unclear |
| `bmi_standard` (optional) | `--bmi-standard` | `asian` (default for CN users) or `who` |

**From USER.md:**

| Field | CLI arg | Description |
|-------|---------|-------------|
| `height_cm` | `--height` | Height in cm (float) |
| `age` | `--age` | Age in years (int) |
| `sex` | `--sex` | `male` or `female` |

Defaults if value not explicitly stated:
- `activity`: `lightly_active`
- `diet_mode`: `balanced`
- `tdee`: `0`
- `bmi_standard`: `asian`

---

## Overview

- **Type:** Inline post-weekly-report task
- **Trigger:** Every 4 weeks on Sunday (after weekly-report)
- **Dependencies:** weight-loss-planner, weight-tracking

Recalculates daily calorie target based on current weight. Updates PLAN.md with new TDEE, calorie target, and macro ranges. Does NOT change diet mode.

---

## Trigger Conditions

1. **Primary:** Called inline by weekly-report skill after sending the weekly report (Sunday)
2. **Secondary:** When weight-tracking logs a new weight AND `pending-recalc.json` exists with `reason="awaiting_weight"`

**25 天门的真值源：** `data/last-recalc-summary.json` 的 `date` 字段。`periodic-recalc.py` 在每次 `action="recalculated"` 时自动写入 `date` + 核心数值（weight_from, weight_to, old_calories, new_calories）；LLM 后续按本 SKILL "After sending" 那块补全完整字段（cycle_number / message_sent 等）。PLAN.md 不再用于 25 天判断。

---

## Execution

After extracting values from PLAN.md + USER.md (Step 0), run:

```bash
python3 {baseDir}/scripts/periodic-recalc.py \
  --workspace {workspaceDir} \
  --planner-calc {weight-loss-planner:baseDir}/scripts/planner-calc.py \
  --current-calories <extracted> \
  --target-weight <extracted> \
  --tdee <extracted> \
  --activity <extracted> \
  --diet-mode <extracted> \
  --height <extracted> \
  --age <extracted> \
  --sex <extracted> \
  --cycle-start-date <extracted> \
  [--weekly-rate <extracted>] \
  [--bmi-standard <extracted>]
```

All args are **required** except `--weekly-rate` (skip if unclear) and `--bmi-standard` (defaults to `asian`).

---

## Handling Output

Based on the JSON output `action` field:

### `action: "skipped"`

Less than 25 days since last recalc. Do nothing — silently exit.

### `action: "recalculated"`

**Step N: Rewrite PLAN.md (LLM responsibility — DO THIS IMMEDIATELY)**

The script no longer modifies PLAN.md. After receiving this output, **you** must update `{workspaceDir}/PLAN.md` by replacing the following fields with new values from the script output:

| PLAN.md concept | New value source |
|---|---|
| 每日热量目标 / Daily Calorie Target | `new_calories` |
| 每日热量缺口 / Daily Calorie Deficit | `new_tdee - new_calories` (compute) |
| 每周减脂速度 / Weekly Rate | `new_rate` (kg/week) |
| 当前体重 / Current Weight | `current_weight` |
| TDEE / 每日总能量消耗 | `new_tdee` |
| 三大营养素范围 / Macro Ranges (g) | `macros.protein_g[]` / `macros.carbs_g[]` / `macros.fat_g[]` |
| 更新日期 / Updated | today (ISO date) |

**Preserve the user's original PLAN.md format**: if it's in Chinese with bullets, keep that; if English markdown table, keep that. Only swap the numbers. Do not restructure the document.

**⚠️ PLAN.md 必须在发消息之前写好。** 方案即时生效，不存在"等确认"中间态。写完 PLAN.md 再发消息。

---

Compose a cycle review + new cycle message for the user.

**⚠️ 开头格式（重申）：** 消息第一行必须是 locale 对应的 section banner（见文件顶部 Output Format），接着空行，再接正文。

**Message structure (in user's language — check USER.md):**

1. 🎉 **Celebrate** the completed cycle — the user stuck with it for 4 weeks. Make them feel proud. Reference their weight change.
2. **Clear divider** between old cycle review and new cycle plan. The user should feel "previous page closed, new page begins."
3. **只说变化的，不变的不用说。** 先明确告知本次调整了什么（热量 1300→1360、速度 0.4→0.35 等），再用温暖口吻解释原因。如果某项没变（热量/速度/宏量都一样），不要重复罗列"保持不变"——用户不需要被告知"没变化"。Reference:
   - `weight_change`: how much lost? fast or slow?
   - `old_calories` vs `new_calories`: changed? If same, skip or one sentence only
   - `rate_kg_per_week` change: only mention if different
   - Actual intake vs target (read `data/meals/` last 28 days)
   - If progress underperformed: gently note possible recording gaps or portion underestimation. Never accuse.
   - **Rule:** If `old_calories == new_calories` and `old_rate == new_rate`, the message should be SHORT — just celebrate the cycle, note weight status, and say "继续按当前方案走". Do NOT list all the unchanged numbers again.
4. **New cycle numbers:** daily calorie target, expected rate (kg/week), 4-week forecast
5. **Macro ranges** (protein/carbs/fat in grams) — integers only, no decimals
6. **收尾语气（逻辑，不写死措辞）：** 表达三层意思：①这是新方案 ②先试一段时间 ③不合适随时可以调。禁止征求批准（"你觉得OK吗"）、禁止单方面通知（"方案已经生效"）。语气像朋友商量，不是上级通知。具体措辞由你根据用户语言和场景自由组织。

**Precision:** All nutrition values as integers (e.g. 1359 kcal, protein 70-93g). No decimals.

**Already effective:** PLAN.md 已在 Step N 更新。对用户的表达是"先试一段时间，不合适再调整"。用户后续有异议 → 按其偏好重算并改写 PLAN.md。

**After sending,** write `{workspaceDir}/data/last-recalc-summary.json`:

注：脚本已经预写了 `date` / `weight_from` / `weight_to` / `old_calories` / `new_calories` 字段，你只需要 merge 补全 `cycle_number` / `old_rate` / `new_rate` / `message_sent` 等剩余字段（读现有 JSON、merge、写回）。

```json
{
  "date": "<today>",
  "cycle_number": <N>,
  "weight_from": <previous>,
  "weight_to": <current>,
  "old_calories": <old>,
  "new_calories": <new>,
  "old_rate": <old>,
  "new_rate": <new>,
  "message_sent": "<full message text>"
}
```


### `action: "awaiting_weight"`

**⚠️ 开头格式（重申）：** 消息第一行必须是 locale 对应的 section banner（见文件顶部 Output Format），接着空行，再接正文。

Tell the user: "It's time for your 4-week plan recalculation! Please weigh yourself when you can, and I'll update your plan once you log your weight."

Write `pending-recalc.json`:
```json
{"created_at": "<ISO>", "reason": "awaiting_weight", "cycle_date": "<today>"}
```

### `action: "on_leave"`

Write `pending-recalc.json`:
```json
{"created_at": "<ISO>", "reason": "on_leave", "cycle_date": "<today>"}
```

Do NOT notify the user. Recalc triggers on first Sunday after leave ends.

---

## Secondary Trigger: Weight Logged

When weight-tracking logs a new weight, run:

```bash
python3 {baseDir}/scripts/check-pending-recalc.py --workspace {workspaceDir}
```

If `{"should_trigger": true}`: run the full recalc flow, then delete `pending-recalc.json`.

---

## User Reply Handling (Main Session)

When user replies to the recalc message (in the main session):

The new plan is **already effective** (PLAN.md updated at send time). Handle replies as:
- User has no objection / thanks / acknowledges → no action needed
- User wants changes (different rate, different calories, concerns) → recalculate with their preferences, rewrite PLAN.md, and confirm the update
- User asks "why did you change it" → explain: cite weight change, old vs new calories, and the reasoning from the recalc message. **Never deny having made the change** — it's recorded in `last-recalc-summary.json` and PLAN.md is already updated.

**Semantic mapping:**
- "previous pace / old calories / last cycle's" → `old_rate`, `old_calories`
- "current / new / this plan" → `new_rate`, `new_calories`
- "keep the old pace" = user wants `old_rate`, not the new slower rate → recalculate with that rate, update PLAN.md

---

## Data Dependencies

| File | Access | Purpose |
|------|--------|---------|
| `PLAN.md` | Read + Write | LLM reads for Step 0 extractions; LLM updates with new values after recalc |
| `USER.md` | Read | Height, age, sex (Step 0) |
| `data/weight.json` | Read | Most recent weight |
| `data/leave.json` | Read | Leave status |
| `data/pending-recalc.json` | R/W/Delete | Deferred recalc tracking |
| `data/last-recalc-summary.json` | Write | Recalc history and audit trail |
| `data/meals/*.json` | Read | Actual eating patterns (calorie trend analysis) |

---

## Important Notes

- **Always recalculate** — no "too small to bother" threshold. Each 4-week cycle is a new phase.
- **Macro formula** (must match onboarding):
  - Protein: 1.2–1.6 g/kg × target_weight (high_protein: 1.4–1.8)
  - Fat: diet_mode percentage × daily_calories
  - Carbs: remainder
- If `floor_clamped: true`: weekly rate was reduced because calories hit BMR floor. Mention this to user.
- Delete `pending-recalc.json` after successful recalc.
