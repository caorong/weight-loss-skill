---
name: weight-tracking
version: 1.2.0
description: "Track body weight with full CRUD operations, unit conversion, and trend analysis. Trigger when user reports weight, asks about weight trend, wants to correct a weight entry, or change their unit preference. Trigger phrases: 'I weigh...', '体重...', '称了一下...', 'my weight is...', 'change to pounds', '改成斤', 'weight trend', '体重趋势'. ANY message containing a weight number or weight change — regardless of context (e.g. '我XX斤', '掉了X斤', '称了XX', 'my weight is...') — MUST trigger this skill and be recorded into data/weight.json; never reply verbally without saving."
metadata:
  openclaw:
    emoji: "scales"
    homepage: https://github.com/NanoRhino/weight-loss-skill
---

# Weight Tracking

> ⚠️ **SILENT OPERATION:** Never narrate internal actions, skill transitions, or tool calls to the user. Just do it silently and respond with the result.

## Performance

**Use TOOLS.md Quick Reference first.** If `tz_offset`, `unit_preference`, and `daily_cal_target` are already in the auto-injected TOOLS.md context, use those values directly — do NOT read `timezone.json` or `health-profile.md`.

Only fall back to reading those files if Quick Reference is missing or outdated.

Do NOT read this SKILL.md again if it's already in context from a prior turn.

## Data Storage

**File:** `{workspaceDir}/data/weight.json` — JSON object keyed by ISO-8601 datetime with timezone offset. Each entry: `{ "value": 80, "unit": "kg" }`.

## Hard Rules — Weight Storage (MANDATORY)

These rules are non-negotiable. They exist because agents have stored weight in self-invented files (`weight-log.md`) or in `PLAN.md` as a dated ledger, leaving `data/weight.json` incomplete and breaking badges/dashboards.

1. **Always record, never just talk.** 用户任何形式提及体重数值或体重变化（"我XX斤"、"称了XX"、"体重XX"、"掉了X斤"、"my weight is..."），都必须调用 `save-and-check.py`（或 `weight-tracker.py save`）写入 `data/weight.json`。不允许只口头回复而不记录。 ANY mention of a weight value or weight change MUST be written to `data/weight.json` via the save script — a verbal reply alone is never sufficient.

2. **Single source of truth.** `data/weight.json` 是体重的唯一权威数据源。`data/weight.json` is the single source of truth for body weight.

3. **No alternative weight files.** 禁止新建 `weight-log.md` 或任何其它体重记录文件。 Never create `weight-log.md` or any other file to store weight.

4. **Never write weight into PLAN.md.** 禁止把"当前体重"或"每日体重"写进 `PLAN.md`。`PLAN.md` 只承载静态计划参数（起始体重 start weight、目标体重 target weight、热量目标 calorie target、营养素范围 macro ranges），这些在建档/recalc 时确定，不随每次称重更新。 `PLAN.md` holds only static plan parameters (start weight, target weight, calorie target, macro ranges), set at onboarding/recalc — never updated per weigh-in.

5. **Read forward, never write back.** 需要展示"当前体重"时，从 `data/weight.json` 读取最新值，绝不反向写回 `PLAN.md`。 When you need to show the current weight, read the latest value from `data/weight.json`; never write it back into `PLAN.md`.

## Scripts

Script path: `python3 {baseDir}/scripts/weight-tracker.py`

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
  [--correct]
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
  [--correct]
```

- Auto-detects new vs correction: if last entry ≤ 30 min ago, overwrites. Otherwise creates new.
- `--correct`: force overwrite most recent entry (when user explicitly says "that was wrong")

### Load — `load`

```bash
python3 {baseDir}/scripts/weight-tracker.py load \
  --data-dir {workspaceDir}/data \
  --display-unit kg \
  [--last 7] \
  [--from 2026-02-01 --to 2026-03-06]
```

Returns entries converted to `--display-unit`, rounded to 1 decimal.

### Other Commands

See `references/crud-operations.md` for: `delete`, `update`, `set-unit`.

## Workflow

### User Reports Weight

1. Read `timezone.json` → `tz_offset`; read `health-profile.md` → Unit Preference (parallel, first session only)
2. Call `save-and-check.py`:
   ```bash
   python3 {baseDir}/scripts/save-and-check.py \
     --data-dir {workspaceDir}/data \
     --value <N> --unit <u> --tz-offset <tz> \
     --plan-file {workspaceDir}/PLAN.md \
     --health-profile {workspaceDir}/health-profile.md \
     --user-file {workspaceDir}/USER.md
   ```
3. Read response:
   - `save.action`: `"created"` → "Logged ✓"; `"updated"` → "Updated ✓". Show value in preferred unit.
   - `context.recent_weights`: 最近体重记录
   - `context.plan`: 计划目标（TDEE、目标体重等）
   - `context.active_strategy`: 是否有进行中的干预策略
   - `context.last_intervention_date`: 上次干预日期
4. **自主判断是否需要关注体重趋势**——核心标准：**体重变化方向是否与用户目标一致，且偏离大概率是真实趋势而非日常波动。**
   - 体重每天自然波动 ±0.5-1kg（水分、钠、排便），单日偏离在这个范围内大概率是噪音，不需要反应。需要多天数据才能确认趋势是真实的。
   - **与目标一致（如目标减重，体重在降）**→ 鼓励，给到正面情绪价值
   - **日常噪音**（1-2 天小幅波动，幅度在正常范围内）→ 正常确认，不追问
   - **大概率真实偏离**（连续多天与目标反方向、从低点明显回升超出波动范围）→ 安抚情绪 + 提一句问用户要不要一起看看。不替用户决定"这没事"，把选择权交出去
   - **体重停滞**（看 14 天数据，体重在一个狭窄范围内来回波动，没有向目标方向推进）→ **必须**温和询问用户"最近体重好像没什么变化，要不要一起看看原因？"。不要自己分析原因或给建议，先问用户愿不愿意。用户说好 → 走 cause-check-flow
   - **判断倾向：只在你有 80% 以上信心认为偏离/停滞是真实的时候才提。** 不必要的询问也是打扰
   - 如果 `active_strategy.active: true`（已有进行中的策略），不重复干预，但可以结合 `consensus` 提醒策略还在跑。可以主动查看最近几天的餐食记录（`{workspaceDir}/data/meals/YYYY-MM-DD.json`），结合共识给具体反馈
   - 如果 `last_intervention_date` 在 7 天内，不重复干预（包括停滞询问和偏离询问）
   - **触发询问前先标记**：无论是停滞询问还是偏离询问，只要决定向用户提问，**必须先调用 mark-intervention，再输出询问文本**。顺序：tool call → text output。这确保即使用户不回复，intervention 也已被标记，不会在下次称重时重复触发。
     ```bash
     python3 {baseDir}/scripts/save-and-check.py --data-dir {workspaceDir}/data --tz-offset <tz> --mark-intervention
     ```
     调用成功后，再输出询问文本（如"要不要一起看看原因？"）。
   - **用户视角优先**：不只看科学趋势，也想想用户看到这个数字会怎么想。涨了半斤用户会慌、平台期一周不动用户会焦虑、好不容易降下来又反弹用户会沮丧——回应他们的感受，不是冷冰冰确认一个数字

### User Asks for Trend / History

1. Call `load` with appropriate filters and `--display-unit`
2. Present data (table, summary, or trend description)

### User Wants to Correct / Delete / Change Unit

See `references/crud-operations.md`.

## Interaction Guidelines

- **Record weight whenever mentioned.** Not limited to scheduled days. "今天早上74.5", "刚称了一下79" → call `save`. Exception: past/hypothetical ("我以前80公斤").
- **Never comment on weight changes unprompted.** Just log and confirm. Emotional reactions → `emotional-support` skill.
- **Always display in preferred unit**, rounded to 1 decimal.
- **Accept any common unit**: kg, lb, lbs, 斤 (=0.5 kg), 公斤.
- **Fasting tag**: if user mentions empty stomach / morning weigh-in → note `fasting: true` in context.


## References

| File | Contents |
|------|----------|
| `references/crud-operations.md` | Delete, update, set-unit commands + correction workflow |
| `references/deviation-workflow.md` | Deviation-check severity table, response guide, command |
| `references/integrations.md` | Which other skills use this skill's scripts |
