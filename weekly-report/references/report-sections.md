# Report Sections — Detailed Rules

## Section 1: Logging Overview

Show each day of the week with a status indicator.

**Data logic:**
- Use `collect-weekly-data.py` output → `days[].logged` field
- Count days with `logged: true` → `{X}/7 days logged`

**Commentary rules:**
- 7/7 → `"满勤！🎉"`
- 5-6/7 → `"记录很稳！"`
- 3-4/7 → `"不错的开始，下周争取多记一天。"`
- 1-2/7 → `"每记一天都有价值，慢慢来！"`

---

## Section 2: Calorie Analysis

Show daily calorie intake vs target range with a vertical bar chart and grey target range band.

**Data logic:**
- Use `days[].totals.cal` and `plan.cal_min`
- Compare each day against `Daily Calorie Range` from `PLAN.md`:
  - Below range min → below / Within range → on-target / Above range max → over
- Days with no data → empty

**Commentary rules:**
- Average within range → `"这周热量控制得很好。"`
- Average below range → `"稍微偏低，注意吃够保持精力。"`
- Average above range → `"略微超标，小调整就能回来。"`
- Only 1-2 days of data → Show what's available but note limited data

**Important:** Use `cal_avg_estimated` (meal-filled estimate) for dietary assessment, NOT `cal_avg` (deflated by missed-meal days). If `days_with_missing_meals > 0`, mention incomplete records.

---

## Section 2b: Weekly Low-Calorie Safety Check

Run after Section 2.

**Data logic:**
- Read `summary.safety` from collect-weekly-data.py output (if available), or run:
  ```bash
  python3 {baseDir}/scripts/weekly-low-cal-check.py \
    --data-dir {workspaceDir}/data/meals \
    --bmr <BMR from PLAN.md> \
    --date <end-of-week YYYY-MM-DD> \
    --tz-offset {tz_offset}
  ```

**When `below_floor` is true:** Append safety note:
```
⚠️ 这周平均每日摄入（~X kcal）低于基础代谢（~Y kcal）。
持续低于这个水平可能影响代谢和营养摄入。
其中 [day1, day2, ...] 偏低比较明显，下周可以在这几天多加一餐或增加份量。
```
Tone: informational, never guilt. Offer concrete suggestions.

**When `below_floor` is false:** No mention.

---

## Section 3: Weight Progress

**Data logic:**
- Use `weight.readings[]` and `weight.change`
- If 2+ readings: show change (last − first)
- If 1 reading: compare to previous week's last reading if available
- If 0 readings: skip this section entirely

**Commentary rules:**
- Loss within expected rate → `"进度刚好。"`
- Loss faster than expected → `"进度不错，注意别吃太少。"`
- No change or slight gain → `"体重会波动，一周说明不了什么。💛"`
- No readings → `"这周没有称重记录，下周要不要试试？"`

**Never:** compare to target weight pressuringly, criticize a gain, or suggest weighing more than 2x/week.

---

## Section 4: Macronutrient Analysis

Show daily macro intake as three separate vertical bar charts (碳水/蛋白质/脂肪).

**Data logic:**
- Per-day macro values with meal-fill estimation (missing meals filled with weekly average)
- For each macro, compare against target range:
  - Below min → 浅绿 / Within → 深绿 / Above max → 浅橙
- If `macro_estimated: true`, show footnote about estimation

**Target range source:**
1. Primary: `PLAN.md` macro ranges
2. Fallback: protein=weight×1.2g/kg, fat=20-35% cal, carb=remainder

**Commentary rules:**
- All in range → `"三大营养素都在范围内，继续保持！"`
- Protein below → always flag with actionable fix
- Fat over → mention cooking oils and snacks
- Insufficient data (< 3 days) → show with caveat

---

## Section 5: Habit Progress

**Data logic:**
- Read `habits.active` and `habits.daily_log.{date}` for the week
- Count completions, misses, no_responses per habit
- If no active habits: skip section entirely

**Commentary rules:**
- ≥ 80% completion → `"快变成习惯了。"`
- 50-79% → `"在进步，坚持比完美更重要。"`
- < 50% → `"这周有点难，要不要调整一下？"`
- Graduation criteria met → flag it

---

## Section 6: Key Achievements & Suggestions

### Achievements (max 3)

Scan for genuine wins. Every achievement MUST be backed by actual data. Never fabricate.

Pattern ideas: logging streak, calorie consistency, protein improvement, weight trend, food variety, healthy choices.

If nothing stands out: `"你坚持记录了，这就是最难的一步。"`

### Suggestions (max 2)

- Must reference actual data (e.g., "Protein averaged 82g vs target 84–112g")
- Must include concrete action (e.g., "Add a Greek yogurt to breakfast")
- Respect food restrictions from health-profile.md
- Never suggest weighing more than 2x/week
- Tone: collaborative — "One thing to try:" not "You need to:"
