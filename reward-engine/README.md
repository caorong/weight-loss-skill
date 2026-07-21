# Reward Engine — Calorie Target Badge

> **⚠️ 已退役为纯程序资产(2026-07-21,audit Phase 3.2)。**
> 徽章链路由 gateway `badge-trigger` 插件全程接管:`after_tool_call` 钩子在每次
> `meal_checkin` 后直接 exec `scripts/badge-calc.py`,level-up 时调度 celebration
> cron——agent(LLM)全程不参与,不需要知道本 skill 存在。
> 本文件从 SKILL.md 改名 README.md 以摘出 skill 发布(加载器只认 SKILL.md),
> description 不再进每请求 prompt(省 ~200 token/请求)。
> `scripts/` `assets/` `templates/` 仍被 badge-trigger 依赖,**不可删**。
> 以下为原设计文档,供维护 badge-calc.py 时参考。

> ⚠️ **SILENT OPERATION:** Never narrate internal actions or tool calls.

## Philosophy

1. **Celebrate accumulation, never punish gaps.** Count goes up only — never down.
2. **Reward the behavior (eating on target), not the outcome (weight loss).**
3. **One celebration per level-up, then move on.** Don't revisit past badges.
4. **Personalize the image.** User name + their data on the badge.

## Qualification Rules

A day counts as "qualified" when ALL THREE conditions are met:

1. **Complete meals logged:** Number of main meals (breakfast/lunch/dinner) logged today ≥ expected meal count
   - Default: 3 meals
   - Intermittent fasting (16:8 or explicit "两餐" in PLAN.md): 2 meals
   - `snack` does NOT count as a main meal
2. **Calories in target range:** Total daily calories within the `Daily Calorie Range` from PLAN.md (e.g., 1,430 - 1,630)
   - Falls back to target ± 10% if only a single target value exists
3. **Above safety floor:** Total daily calories ≥ BMR × 0.8
   - Prevents rewarding dangerous under-eating

## Script

```bash
python3 {baseDir}/scripts/badge-calc.py check \
  --workspace-dir {workspaceDir} \
  --tz-offset {tz_offset}
```

### Output

```json
{
  "qualified_today": true,
  "already_counted": false,
  "current_count": 14,
  "current_level": 3,
  "level_up": true,
  "new_badge": {
    "level": 3,
    "name": "⭐⭐⭐ 自由支配者",
    "message": "两周的精准分配。你不是在\"控制饮食\"，你是真的会吃了。",
    "milk_tea_cups": 28,
    "progress_bar": "━━━━━━━━━━░░░░ 14/21 → 下一级：稳定输出选手"
  },
  "badge_image": "{workspaceDir}/data/badges/badge_level_3.png"
}
```

- `qualified_today`: whether today meets all 3 conditions
- `already_counted`: true if today was already counted (prevents double-counting)
- `level_up`: true only when a NEW level is reached this check
- `new_badge`: null if no level-up
- `badge_image`: path to generated badge image (null if no level-up)

## Level Definitions

| Level | Days | Name | Message |
|-------|------|------|---------|
| 1 | 3 | ⭐ 稳稳吃饭的人 | 有3天热量刚好落在目标区间。这不是运气——是你对"一顿饭多少热量"有感觉了。 |
| 2 | 7 | ⭐⭐ 热量掌控达人 | 7天达标。你已经知道什么时候该多吃、什么时候该收着。这种判断力比任何食谱都管用。 |
| 3 | 14 | ⭐⭐⭐ 自由支配者 | 两周的精准分配。你不是在"控制饮食"，你是真的会吃了。 |
| 4 | 21 | ⭐⭐⭐⭐ 稳定输出选手 | 三周的节奏，已经不是"在坚持"了，是你的正常状态。 |
| 5 | 30 | ⭐⭐⭐⭐⭐ 精准生活家 | 一个月。你对热量的直觉已经内化了——不看数字也不会差太远。 |
| 6 | 45 | ⭐×6 持续进化者 | 45天。一个半月的稳定输出，这不是阶段性的表现，是你的新常态。 |
| 7 | 60 | ⭐×7 长期主义玩家 | 60天。不需要鸡汤，数据说明一切。 |
| 8 | 90 | ⭐×8 不可撼动 | 90天。多数人的计划活不过两周，你走完了全程。这不是关于减肥了，这是你证明了自己能长期做好一件事。 |



## Badge Image Generation

When `level_up == true`, generate a personalized badge image:

- **Template:** from `{baseDir}/assets/` (operator provides base template)
- **Dynamic text overlay:**
  - User's name (from USER.md)
  - Badge level + name
  - Cumulative days count
  - Milk tea cups equivalent
  - Progress bar to next level
- **Output:** saved to `{workspaceDir}/data/badges/badge_level_{N}.png`
- **Format:** PNG, dimensions TBD (pending operator's style reference)

> ⚠️ Image template assets pending — operator will provide reference design.
> Until then, badge_image will be null and celebration is text-only.

## Data Storage

File: `{workspaceDir}/data/badges.json`

```json
{
  "calorie_target": {
    "current_level": 3,
    "current_count": 14,
    "next_level_target": 21,
    "qualified_dates": ["2026-05-01", "2026-05-03", "2026-05-04"],
    "unlocked_at": {
      "1": "2026-04-15",
      "2": "2026-04-28",
      "3": "2026-05-10"
    },
    "daily_deficit": 385,
    "last_calculated": "2026-05-19"
  }
}
```

## Integration Point

Called by `diet-tracking-analysis` after `meal_checkin` returns with `action: "create"` or `action: "append"`:

```
# After composing the normal meal reply (①②③):
1. Run badge-calc.py check
2. If level_up == true AND badge_image exists:
   → Append badge celebration text after normal reply
   → Send badge image via MEDIA: directive
3. If level_up == true AND badge_image is null/empty:
   → Say nothing (silent skip — do NOT fall back to text)
4. If qualified_today == true but no level_up:
   → Say nothing (silent accumulation)
5. If qualified_today == false:
   → Say nothing about badges
```
