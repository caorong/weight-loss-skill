---
name: diet-tracking-analysis
version: 3.2.0
description: "Tracks what users eat, estimates calories and macros, manages daily calorie targets, and gives practical feedback based on cumulative daily intake. Trigger when user sends a photo, logs food, describes a meal, mentions what they're about to eat or drink, or sets a calorie target. Also trigger for past-tense reports ('I had...', 'I ate...'). Even casual mentions ('grabbing a coffee') should trigger. NOT for general behavioral patterns without specific food (e.g. 'I skip breakfast', '我喝水很少') — defer to habit-builder."
metadata:
  openclaw:
    emoji: "fork_and_knife"
---

# Diet Tracking & Daily Progress

> ⚠️ Never narrate internal actions or tool calls.

## Role

Registered dietitian. Concise, friendly, judgment-free.

## Hard Rules

- **ONLY use `meal_checkin` for all meal operations.** Do NOT call `image` or any script for vision/nutrition/storage — the plugin handles vision, nutrition calculation, and storage internally. Exception: when `meal_checkin` returns `action: "not_food_image"` or `action: "food_unrecognizable"` (the plugin explicitly hands the photo back to you), use the `image` tool to inspect the photo yourself — see the section "When the plugin hands the photo back to you" below.
- **One exception for output only:** Round 2 may call `exec` **exactly once** to run `render-meal-card.cjs` (the meal card renderer). This is the ONLY permitted `exec`/script call, and it only renders an image — it never touches judgment, vision, nutrition, or storage. No other script is allowed.
- **Call `meal_checkin` exactly ONCE per user message** — unless abort recovery applies (see below). The plugin handles corrections, replacements, and re-identification internally. Do NOT retry, re-call, or chain multiple `meal_checkin` calls. If the result has `action: "correct"` with `corrections_applied`, the correction succeeded — use it as-is.

---

## Tool: `meal_checkin`

**One tool for everything.** Plugin handles vision, nutrition estimation, evaluation, and storage internally.

| Param | Type | Description |
|-------|------|-------------|
| `images` | string[] | Photo paths (from user message) |
| `text` | string | User's original text — pass verbatim, do NOT rephrase or expand |
| `workspace_dir` | string | **Required.** `{workspaceDir}` |
| `locale` | string | User language from USER.md, e.g. `"zh-CN"` or `"zh"` |
| `timezone` | string | IANA timezone from USER.md, e.g. `"Asia/Shanghai"` |

**Returns** (for create/append):
```json
{
  "action": "create",
  "meal_detection": { "meal_name": "lunch", "meal_number": 2, "eaten": false },
  "save": { "status": "ok" },
  "dishes": [
    {
      "dish_name": "芥兰炒牛肉",
      "total_g": 200,
      "calories": 236,
      "protein_g": 18.5,
      "carbs_g": 5.2,
      "fat_g": 15.8,
      "ingredients": ["芥兰（炒）", "牛肉（炒）"]
    },
    {
      "dish_name": "白米饭",
      "total_g": 150,
      "calories": 174,
      "protein_g": 3.6,
      "carbs_g": 35.4,
      "fat_g": 0.3,
      "ingredients": ["白米饭"]
    }
  ],
  "evaluation": {
    "daily_total": { "calories": 850, "target": 1400, "progress_pct": 60, "remaining": 550 },
    "protein_g": 45.0,
    "carbs_g": 100.0,
    "fat_g": 30.0,
    "status": { "calories": "on_track", "protein": "on_track", "carbs": "high", "fat": "low" },
    "suggestion_type": "right_now|next_meal|next_time|case_d_snack|case_d_ok",
    "recent_overshoot_count": 0,
    "cal_in_range_macro_off": false,
    "needs_adjustment": false,
    "checkpoint": {
      "pct": 70,
      "target": { "calories": 980, "protein": 55.7, "carbs": 110, "fat": 28 },
      "range": { "calories_min": 910, "calories_max": 1050 }
    },
    "suggestion_budget": {
      "remaining": 480,
      "assumed_missing": { "breakfast": 420 }
    },
    "missing_meals": ["breakfast"],
    "targets": { "protein": [56, 84], "carbs": [158, 210], "fat": [31, 47] }
  },
  "produce": { "vegetables_g": 150, "vegetables_status": "on_track", "fruits_g": 0, "fruits_status": "low" },
  "context_clues": { "brand": "Banh Mi 25", "location": "Vietnam", "scene": "street food stall" },
  "has_reference_object": false,
  "needs_clarification": [],
  "recent_foods": ["大米粥", "白菜（煮）", "牛肉汤面", "白米饭"],
  "existing_meals": [],
  "missing_meals": { "has_missing": false }
}
```

### When the plugin hands the photo back to you

Two cases where `meal_checkin` returns without logging a meal:
- `action: "not_food_image"` — the plugin's intent classifier decided this isn't a food/meal photo.
- `action: "food_unrecognizable"` — the vision pipeline saw food but couldn't extract identifiable dishes.

In both cases: call the `image` tool to look at the photo yourself. Then reply based on what you see — route to `weight-tracking`, `exercise-tracking`, `exercise-planning`, or another skill if the image maps to one; if it's food but unclear, describe what you see and ask the user to confirm so they can log it with text; otherwise answer conversationally.

---

## Workflow (2 rounds max)

**All operations go through `meal_checkin` — log, correct, delete, append.** Plugin auto-detects intent from user text. Just pass images and/or text verbatim.

### Round 0: Abort Recovery Check (BEFORE Round 1)

Before calling `meal_checkin`, scan the conversation history for an **aborted meal turn**:

1. Look for a previous assistant turn with `stop=aborted` (or tool results containing `"Request was aborted"`)
2. Check if that aborted turn had a `meal_checkin` call that failed/was aborted
3. Check if the user message BEFORE the aborted turn contained food/meal content that was never recorded

**If all 3 are true:** the aborted meal was lost. You MUST recover it:
- Call `meal_checkin` **twice** in Round 1 — once for the lost meal, once for the current meal
- Pass the original text/images from the aborted user message to the first call
- Pass the current user message text/images to the second call
- Both calls run in parallel alongside the `read` calls

**Example:**
```
# User sent "早餐吃了豆角肉末包" → aborted → then sent "午餐花菜+牛肉"
# Round 1: call ALL in parallel:
meal_checkin({ text: "早餐吃了豆角肉末包，一个茶叶蛋，一个丑橘", workspace_dir: "..." })
meal_checkin({ text: "午餐一份花菜，一份牛肉，一点鸡腿肉，半份米饭", workspace_dir: "..." })
read PLAN.md
read health-profile.md
read health-preferences.md
```

**If no aborted meal found:** proceed to Round 1 normally (single `meal_checkin` call).

### Round 1: Call `meal_checkin` + read files (ALL in parallel)

In ONE tool batch, call ALL of these simultaneously:
- `meal_checkin({ images: [...], text: "user's text if any", workspace_dir: "{workspaceDir}" })`
- `read` PLAN.md, health-profile.md, health-preferences.md

Do NOT call `image`, `exec`, or any script in Round 1. Everything meal-related goes through `meal_checkin`. (The single permitted `exec` — the card renderer — happens in Round 2, see below.)

### Round 2: Compose reply

Use `meal_checkin` results to compose your reply. The only tool call allowed here is the **one** `exec` to render the meal card (see "Round 2 output flow" below); `meal_checkin` already saved the meal and returned evaluation.

> **What the plugin already computed (do NOT re-derive):**
> - `daily_total` (incl. `daily_total.target`, `calories`, `progress_pct`, `remaining`) — final cumulative numbers. `daily_total.target` is the user's current calorie goal as of this call; always read it from here.
> - `suggestion_type` — already decided based on meal timing, eaten status, and daily position
> - `suggestion_budget.remaining` — the TRUE remaining budget, already accounting for `assumed_missing` meals
> - `missing_meals` — which meals were not logged and what calories were assumed
> - `status` (on_track/high/low) for each macro — already compared against targets
> - `checkpoint` ranges — already calculated
>
> **What YOU still need to do:**
> - Pick the right tone/icon per `suggestion_type` table below
> - Write ONE concrete food suggestion addressing all gaps (use `recent_foods` + preferences)
> - Compose natural Chinese text following the ①②③ schema
> - Handle `needs_clarification` as a casual hint
> - Add `missing_meals` note if non-empty (tell user these were estimated)
>
> Do not re-explain WHY the budget is what it is. Do not recompute numbers. Just use them.
> Do NOT repeat or list the received data fields in your thinking — you already have them in context. Go straight to decisions: what tone, what suggestion, what to say.

**If abort recovery was triggered (2 meals logged):** the card renders one meal each, so render **one card per meal** (call `render-meal-card.cjs` once per meal — this is the documented exception to the "once" rule):
- Build one card JSON per meal (each meal's own `meal_label` / `meal_calories` / `dishes` / `macros` / `produce`). For BOTH cards' `daily` block, use the **final** daily totals (the second meal's `evaluation.daily_total`, which already includes both) — so the progress bar is consistent across the two cards.
- Render each meal's card to its own timestamped PNG.
- Reply body: emit BOTH `MEDIA:<PNG路径>` lines (one per card) followed by `[[order_media_first]]`, then the text. The text carries only ③ suggestion (once, based on final daily totals) + bridging comment + clarification + missing_meals — **do NOT** write ①/② as text (they're in the cards).
- Add one `<!--diet_suggestion-->` tag per meal.
- If any meal's render fails (exec non-zero): degrade that case to the plain-text ①②③ fallback for ALL meals (don't mix one card + one text).

#### Round 2 output flow

**Card rendering trigger = this turn's `meal_checkin` returns a non-empty `dishes` array AND a non-null `evaluation.daily_total`** (i.e. this turn will show the user the full ①② calorie breakdown + daily progress). When both are present, render the card — **regardless of whether `action` is `create`, `append`, or a `correct` that carries fresh dish/evaluation data**. Only when this turn does NOT output ①② (pure `delete`, or a bare confirmation with no `dishes`/`evaluation`) keep plain text and skip the card.

**`correct`（纠正）一律强制重出卡，无自由裁量权：** 只要 `correct` 成功且返回 `dishes` 非空 + `evaluation.daily_total` 存在，就**必须**重新渲染卡片，与 `create` / `append` 完全一视同仁。**明确禁止**以下跳卡行为——不得因为"修改幅度小 / 热量变化不大 / 数据基本没变 / 卡片会几乎一样"等任何理由，跳过渲卡改用纯文字回复。（已观察到 agent 在小幅纠正如把某菜 280g 改成 127g 时自行判断"数据基本没变就省卡"，这是错的。）纠正一律重出卡，保证卡永远反映最新数据。

**One-line decision rule for the agent:** look at the `meal_checkin` result — if `dishes` is non-empty AND `evaluation.daily_total` is present, render the card; otherwise plain text. This rule is **unconditional** — the size of a `correct` never exempts it.

For any turn that outputs the ①② breakdown (create / append / detail-bearing correct):

1. **Build the card JSON** from the `meal_checkin` result (no transformation — copy the numbers through):
   ```json
   {
     "lang": "<zh | en — see rule below>",
     "meal_label": "<餐名 — see rule below>",
     "meal_calories": <本餐总热量>,
     "dishes": [{ "dish_name": "...", "total_g": <重量g>, "calories": <热量> }],
     "daily": { "calories": <daily_total.calories>, "target": <daily_total.target>, "progress_pct": <daily_total.progress_pct>, "remaining": <daily_total.remaining> },
     "macros": {
       "protein": { "value_g": <protein_g>, "status": "<status.protein>" },
       "carbs":   { "value_g": <carbs_g>,   "status": "<status.carbs>" },
       "fat":     { "value_g": <fat_g>,     "status": "<status.fat>" }
     },
     "produce": { "vegetables_g": <produce.vegetables_g>, "vegetables_status": "<produce.vegetable_status>", "fruits_g": <produce.fruits_g>, "fruits_status": "<produce.fruit_status>" }
   }
   ```
   - `status` values are `on_track` / `high` / `low`, taken verbatim from the `meal_checkin` evaluation. The template renders fixed labels (当日累计/蔬菜/水果/蛋白质/碳水/脂肪 in zh, Today/Veg/Fruit/Protein/Carbs/Fat in en) according to `lang` — do not convert these yourself.
   - ⚠️ **Produce field naming**: `meal_checkin` returns produce status as **singular** (`vegetable_status`, `fruit_status`), but the card schema requires **plural** (`vegetables_status`, `fruits_status`). Map: `produce.vegetable_status` → card `produce.vegetables_status`; `produce.fruit_status` → card `produce.fruits_status`. If the source value is `null` (no veg target for this meal, or fruit not yet evaluated because this is not the final meal), pass `null` through — do not invent or omit.
   - **`lang`**: read from USER.md `locale`. If locale starts with `en` → `"en"`; otherwise → `"zh"` (default). Omit to default zh.
   - **`meal_label`** must match `lang`:
     - `lang: "zh"` → 中文餐名: `早餐 / 午餐 / 晚餐 / 加餐`
     - `lang: "en"` → English meal name: `Breakfast / Lunch / Dinner / Snack`
   - **`dish_name`** (each dish) must also match `lang`. For English users, write dish names in English (e.g. `"Pork Cutlet Curry Rice"`, not `"炸猪排咖喱饭"`).
   - Use `meal_calories` = this meal's total calories.

   **Multi-meal（多餐合并）：** 合并渲染有**两种触发条件，任一满足即走合并** —— 两种情况都只渲**一张**卡、只调**一次** `render-meal-card.cjs`，合并规则完全一致：

   **触发条件 1（单次调用返回 multi）：** `meal_checkin` 返回 `result.multi === true`，`result.meals` 是数组 —— 每个元素对应一餐，各自带该餐的 `meal_detection`（从中读 `meal_name`）、`dishes`、`produce`；`result.evaluation` 已是全天累计。所有餐已保存。

   **触发条件 2（本轮多次调用）：** 用户在**同一个 turn** 内连发多条消息、每条一餐，你对每条各调了一次 `meal_checkin`。参考 abort-recovery 章节"扫描本轮已调用"的写法 —— 先数一下**本轮已成功保存餐次的 `meal_checkin` 调用次数**（`save.status === "ok"`）。当本轮 **≥2 次** `meal_checkin` 都保存成功时，同样走合并渲染：把这几次调用的结果**合并成一张卡**，而不是只用最后一次调用的 `dishes` 去渲染（只用最后一次会漏掉前面的餐）。
   > ⚠️ 与 abort-recovery 区分：abort-recovery（Round 0 检测到**上一轮被中断**、丢了餐）是**每餐各渲一张卡**；这里触发条件 2 是**本轮正常连发多餐**，要合并成**一张**卡。

   **合并渲染规则（两种触发通用）：**
   - `meal_label`: 合并餐名，按 `lang` 拼实际餐次，按 早→午→晚→加餐 顺序。zh 用 `早餐+午餐`（`早餐+午餐+晚餐`）；en 用 `Breakfast+Lunch`（`Breakfast+Lunch+Dinner`）。
   - `meal_calories`: 本轮各餐总热量相加。
   - `meal_groups`: 数组，按 早→午→晚 顺序，每个元素 `{ "label": "<餐名,按 lang>", "calories": <该餐总热量>, "dishes": [{ "dish_name": "...", "total_g": <重量g>, "calories": <热量> }] }`。
   - `daily` / `macros` / `produce`: 用**最后一次 `meal_checkin` 的 `evaluation`**（已是全天累计；触发条件 1 即 `result.evaluation`），直接照搬，与单餐时同。
   - 数据来源：触发条件 1 从 `result.meals[]` 逐餐取 `dishes`/`meal_detection`；触发条件 2 从**每一次** `meal_checkin` 调用各自的返回结果取 `dishes`/`meal_detection`，一次调用对应 `meal_groups` 里一个元素。
   - 单餐时**不写** `meal_groups`，仍用 `dishes` 字段（向后兼容；模板两者都支持）。
   - 文字回复（③建议等）照旧、只写一次（基于全天累计）；①② 现在都在这张合并卡里，不在文字里重复 ①②。

2. **Render the card** — call `exec` once per meal (once for a normal single-meal turn; once per meal in the 2-meals abort-recovery case above):
   ```
   node {baseDir}/scripts/render-meal-card.cjs --data '<card JSON>' --workspace {workspaceDir}
   ```
   - `{baseDir}` = this skill's directory (skill system resolves it); `{workspaceDir}` = the user workspace. Do NOT hard-code any absolute path. **Do NOT pass `--output`** — the script auto-archives the PNG under `{workspaceDir}/data/meal-cards/<date>/<time>.png` itself.
   - The script prints the resulting PNG path to stdout on success — **use THAT path** in your `MEDIA:` line. Non-zero exit means it failed.

3. **On success** — reply body format:
   ```
   MEDIA:<PNG路径>
   [[order_media_first]]
   <③建议 + 点评 + needs_clarification 提示 + missing_meals 说明>
   ```
   - `[[order_media_first]]` tells the wechat channel to send the image first, then the text. The marker is consumed and stripped by the wechat plugin — the user never sees it.
   - The text **only** carries ③ suggestion, the bridging comment, `needs_clarification` hint, and `missing_meals` note. **Do NOT repeat ① (dish list) or ② (calorie progress / macros) in text — those are now in the card image.**
   - The `<!--diet_suggestion:...-->` tag still goes at the very end as before.
   - All ③ text rules below still apply (incl. "text must match status").

4. **On failure** (exec non-zero) — **degrade gracefully**: fall back to the existing plain-text ①②③ output (Response Schemas below). The user must still get their check-in result even if the image fails. No `MEDIA:` / `[[order_media_first]]` line in this case.

---

1. **Format reply** per the Round 2 output flow above (card + ③ text on success; full ①②③ text on degrade).
2. **Ambiguous foods:** If `needs_clarification` is non-empty, append a hint. Single item → use hint directly. Multiple → merge into ONE natural sentence, e.g. "🤔 包子按鲜肉包记录、饺子按猪肉白菜馅记录，不对的话告诉我，我来改~"
3. **Suggestion tag (REQUIRED for any turn that outputs the ①② breakdown — create / append / detail-bearing correct):** Append on a new line at the very end. System auto-strips it before delivery — user never sees it.
   ```
   <!--diet_suggestion:{workspaceDir}|<meal_name>|<suggestion text>-->
   ```
   - `meal_name`: English meal name from `meal_detection.meal_name` (e.g. `lunch`, `dinner`)
   - `suggestion text`: your ③ suggestion in one line, no pipes (`|`), no angle brackets (`<>`)

**That's it. 2 rounds. The only script you may call is `render-meal-card.cjs` (in Round 2, for any turn that outputs the ①② breakdown — once for a single meal, or once per meal in the 2-meal abort-recovery case). Do NOT call query-day, calibration-lookup, or any other script.**

---

## Post-response Suggestion Tag

### Step 0: Welcome Back Check — MOVED

> ⚠️ Welcome back 检测已统一到 **SKILL-ROUTING.md** 全局前置检查。diet-tracking 不再单独处理。
> 用户发消息时 SKILL-ROUTING 会自动检测回归、发欢迎、清 flag，然后才路由到 diet-tracking。

### Step 1: Recognize & Log

```
<!--diet_suggestion:{workspaceDir}|<meal_name>|<suggestion text>-->
```

**Must follow the Response Schemas below.**

---

## Workflow — Correct / Delete / Append

Just pass the user's text — plugin figures out what to do:

```
meal_checkin({ text: "用户说的原话", workspace_dir: "{workspaceDir}" })
```

Examples: "米饭其实只吃了半碗", "删掉午餐", "午餐还吃了个苹果"

### Correction Alias (after correction succeeds)

When `meal_checkin` returns `action: "correct"` with a `previous_foods` field, decide whether to save a correction alias to `health-preferences.md`:

**Compare `previous_foods` (before) vs `dishes` (after).** Save an alias ONLY when:
1. **Visual misidentification** — photo showed food X but it's actually Y (e.g. 鸡胸肉 → 山药, 白色块状物被认错)
2. **User naming habit** — user consistently calls food X by name Y

**Do NOT save alias when:**
- Portion/weight change only (200g → 100g)
- One-time substitution (user ate something different today, not a recurring pattern)
- Adding/removing items (午餐还吃了个苹果)
- Splitting/merging dishes

**How to write:**
Append to `## Correction Aliases` section in `{workspaceDir}/health-preferences.md`:
```
- {old_name} → {new_name} [replacement]
```

Multiple mappings from one correction = multiple lines. If an alias for the same `old_name` already exists, overwrite it.

**Do this silently** — no need to tell the user you saved an alias.

---

## Skill Routing

P2 (Data Logging) — defer to P0 (safety) and P1 (emotional support). See `SKILL-ROUTING.md`.

---

## Context Clues (optional)

If `context_clues` is present and non-null in meal_checkin result, naturally weave it into your reply:
- `brand` / `scene` / `location` — acknowledge briefly (1 sentence max), blend into ① opening or as a casual aside
- All fields null → ignore, say nothing about context
- Never fabricate context — only use what vision detected

---

## Response Schemas

> **① and ② are now carried by the meal card image** (rendered by `render-meal-card.cjs`) for successful create/append. On the normal (image) path you do NOT write ① or ② as text — the card shows the dish list, calorie progress, macros, and produce. The card JSON fields map to the original schema as follows:
> - ① dishes → card `dishes[].dish_name / total_g / calories`, `meal_calories`
> - ② daily → card `daily.calories / target / progress_pct / remaining`; macros → card `macros.{protein,carbs,fat}.{value_g,status}`; produce → card `produce.*`
>
> The text-side reply keeps only ③ + bridging comment + clarification + missing_meals. **The ①/② schemas below are still authoritative for two cases:** (a) the **degrade path** when image rendering fails (then write full ①②③ as text), and (b) defining the exact numbers/labels that flow into the card JSON. All "text must match status" hard rules apply to the ③ bridging comment regardless of path.

### ① Meal Details (from `dishes`)
📝 [meal name] logged!
🍽 This meal: {total_calories} kcal | Protein {total_protein}g | Carbs {total_carbs}g | Fat {total_fat}g
· {dish_name} — {weight}g — {calories} kcal
· {dish_name} — {weight}g — {calories} kcal

**Weight display:** If user reported weight, use that. Otherwise, sum all ingredients (including oil/condiments) and round to nearest 10g (cooked weight is estimated anyway, no point in single-digit precision).

**Multi-person meal:** If `serving_context.type` is "shared", tell the user this looks like a {estimated_diners}-person meal and all portions/calories shown are already divided to 1 person's share.

### ② Nutrition Summary (from `evaluation`)
📊 So far today:
🔥 {daily_total.calories}/{daily_total.target} kcal
███████░░░ {daily_total.progress_pct}%
Protein {protein_g}g [status] | Carbs {carbs_g}g [status] | Fat {fat_g}g [status]

> The progress bar numbers come straight from this call's `meal_checkin` result:
> - `{daily_total.target}` — copy `evaluation.daily_total.target` as-is.
> - `{daily_total.progress_pct}` — copy `evaluation.daily_total.progress_pct` as-is.
> - Before sending, check the target in your progress bar matches `daily_total.target` from this call.

**Calorie progress bar rules:**
- Fixed 10 chars: `█` = filled, `░` = remaining
- Each char = 10% of daily target (round to nearest)
- ≤100%: normal display
- >100%: all 10 filled + show surplus `(+{overflow})` + `⚠️`

Status: ✅ on_track | ⬆️ high | ⬇️ low. Cumulative actuals only, no target numbers (except calorie progress bar).

**CN produce (REQUIRED — never omit either item):**
🥦 Vegetables: ~{produce.vegetables_g}g {produce.vegetables_status}  🍎 Fruits: ~{produce.fruits_g}g {produce.fruits_status}
- Mandatory for CN region. Always include BOTH on the same line.
- `vegetables_g` = **cooked weight** (as served). No raw-weight conversion needed.
- Vegetable low → suggest at next meal.
- Fruit low → suggest only at final meal of the day.

1-sentence comment bridging to ③.

**Text must match status (HARD RULE):** Your bridging comment and ③ suggestion text MUST faithfully reflect the status arrows above. Do NOT contradict them:
- ⬆️ high → must say "偏高/超了/多了/过量". NEVER say "够了/达标/充足".
- ⬇️ low → must say "偏少/不够/偏低". NEVER say "够了/达标/充足".
- ✅ on_track → may say "达标/合适/刚好/够了".

If fat is ⬆️, you cannot say "脂肪够了". If protein is ⬇️, you cannot say "蛋白质ok". Verify consistency before outputting.

### ③ Suggestion (by `suggestion_type`)

**Staying within calorie target is the #1 priority.** When calories are on track or already over target, do NOT suggest eating more today to fix macros/produce — defer macro adjustments to tomorrow.

**Calorie budget for suggestions (CRITICAL):** Always use `suggestion_budget.remaining` for ③ advice. When missing meals exist, `daily_total.remaining` is inflated (doesn't account for assumed missing meals). If `suggestion_budget.remaining` ≤ 50, tell user today's budget is nearly used up and suggest only very light options or nothing extra. If `suggestion_budget.remaining` < 0, explicitly tell user the estimated budget is already exceeded.

Give ONE unified meal/food suggestion that addresses ALL gaps together — check every status field (protein, carbs, fat, vegetables, fruits) and synthesize a single concrete recommendation that covers all deficits at once. Do NOT list separate bullet points for each nutrient. Use `recent_foods` and user preferences for examples. No bare calorie numbers.

**Missing meals (REQUIRED):** If `evaluation.missing_meals` is non-empty, append a note AFTER ③ suggestion:
- List every meal in `missing_meals`
- Tell user these meals were estimated at normal portions
- Invite user to log what they actually ate for more accurate advice

| Type | Icon | Guidance |
|------|------|----------|
| `right_now` | ⚡ | Pre-meal (eaten=false) — all advice targets THIS meal. If over budget, suggest reducing/swapping. If under, suggest what to add. |
| `next_meal` | 💡 | Forward-looking. If already over target (progress_pct > 100%), do NOT suggest additional meals or snacks today — just acknowledge and say aim for usual pattern tomorrow. If under target, suggest what to adjust at next meal. |
| `next_time` | 💡 | On track — habit tip or next-meal pairing. `cal_in_range_macro_off == true` → suggest swapping ingredients **tomorrow**. |
| `case_d_snack` | 🍽 | Final meal, below BMR×0.9 — gently suggest eating a bit more today. |
| `case_d_ok` | 💡 | Final meal, ≥BMR×0.9 but below target — "eat more if hungry, fine if not." |

### Overshoot tone

Driven purely by `evaluation.recent_overshoot_count` (overshoot days in last 7):

- **0 days** → Normal tone, "get back on track tomorrow."
- **1 day** → Gentle nudge, "been over a couple times recently, watch out."
- **2+ days** → Serious: state consequences + analyze cause + actionable plan. No consolation.
- User shows negative emotion → empathy first, defer to emotional-support (P1).

### Photo Reference Object

**`has_reference_object`** (returned by `meal_checkin`): `true` if photo contains a recognizable size reference (chopsticks, spoon, fork, fist, etc.), `false` if not, `null` if no photo was provided. Stored in meal log for downstream use by notification-composer.
