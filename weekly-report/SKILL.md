---
name: weekly-report
description: generate personalized weekly progress reports for Nano Rhino users. Use for manual weekly report requests such as "weekly report", "周报", "这周怎么样", and for scheduled Sunday 21:00 user-local cron runs. Always generate a clickable report URL using the bundled weekly report scripts; never return a text-only report.
---

# Weekly Report

> 🚨 **OUTPUT CONTRACT: Every execution MUST produce a clickable report URL. A text-only summary without a URL = FAILED. If scripts fail, report the error — do NOT fall back to plain-text.**

> ⚠️ **SILENT OPERATION:** Never narrate internal actions to the user. Just do it and respond with the result.

> 🚨 **ALL reports are data-only. `generate-report-html.py` produces a JSON payload and PUTs it to data-api (MySQL `weekly_reports`). Dashboard reads DB, SSR-injects, and Vue SPA renders it. WRITING HTML YOURSELF IS FORBIDDEN.**

---

## Principles

1. **Show, don't lecture.** Let data speak. Short commentary.
2. **Celebrate consistency over perfection.** 5/7 is great — don't dwell on the 2.
3. **One week is noise, trends are signal.** No dramatic single-week conclusions.
4. **Personalize everything.** User's name, foods, goals — never generic.
5. **Actionable > informational.** Suggestions must be doable next week.

---

## Execution Flow

### Step 1: Get Date Range

```bash
python3 {diet-tracking-analysis:baseDir}/scripts/nutrition-calc.py local-date --tz-offset {tz_offset}
```

**报告范围 = `current_week`（本周 monday–sunday），永远用本周。** Never calculate dates yourself.

> 🔒 **只用 `current_week`,绝不用 `previous_week`。** 即使今天本周还没过完(如周三/周五触发),也报本周至今的数据——**不要**因为"本周不完整"就改用上周。`previous_week` 字段仅供参考,本 skill 永不使用它。周日 cron 触发时本周刚好完整;平时触发就报本周进行中的数据。Step 3 的 `{monday}`/`{sunday}` 必须取 `current_week` 的值。

### Step 2: Gate Check (cron only)

```bash
bash {baseDir}/scripts/should-send-report.sh --workspace-dir {workspaceDir}
```

If output starts with "no" → reply `NO_REPLY`. Stop.

### Step 3: Collect Data (once, reuse in Step 6)

Read `PLAN.md` (or `health-profile.md`) to extract targets for `--targets`:
- `protein_range`: daily protein grams [min, max] — pass if explicitly stated
- `fat_range`: daily fat grams [min, max] — pass if explicitly stated
- `carb_range`: daily carb grams [min, max] — pass if explicitly stated

**Calorie target:** Do NOT estimate or convert single values into ranges yourself.
Only pass `cal_min` if PLAN.md already has an explicit range (e.g. "1138 - 1390").
If PLAN.md only has a single value (e.g. "1264 kcal"), omit `cal_min` — the script
will read it and convert to ±10% range automatically.

Pass whatever you find verbatim; the script fills all missing fields from health-profile as fallback.

```bash
python3 {baseDir}/scripts/collect-weekly-data.py \
  --workspace-dir {workspaceDir} \
  --start-date {monday} --end-date {sunday} --tz-offset {tz_offset} \
  --targets '{"protein_range":[min,max],"fat_range":[min,max],"carb_range":[min,max]}' \
  2>/dev/null > /tmp/weekly-data-{username}.json
```

This outputs ALL data as JSON. **Do NOT call individual scripts per-day.**
Save to a temp file (use workspace username to avoid multi-user collision) — you'll read it now for analysis and pipe it to the report generator in Step 5.

### Step 4: Read Context

- `USER.md` → name, health flags, language preference
- `health-profile.md` → unit preference
- Previous report log: `data/logs/weekly-report-{prev_monday}.json` → check `next_week_focus`

All calorie/macro targets, weight loss rate, phase, progress bar, and week number are already in the collect output (`meta.*` and `plan.*`). No need to read `PLAN.md` separately.

### Step 5: Generate Report

```bash
cat /tmp/weekly-data-{username}.json | \
python3 {baseDir}/scripts/generate-report-html.py \
  --output {workspaceDir}/data/reports/weekly-data-{start_date}.json \
  --workspace-dir {workspaceDir} \
  --nickname {user_nickname} \
  --tagline '{short fun summary of the week}' \
  --plan-rate {weight_loss_rate_per_week} \
  --commentary '{JSON object}' \
  --highlights '{JSON array}' \
  --suggestions '{JSON array}'
```

**Script stdout = report URL.** Capture it.

**What the script does automatically:**
1. Merges commentary/highlights/suggestions into the JSON payload
2. Writes local JSON copy (for audit + prior-week self-read)
3. **PUTs payload to data-api** (`/v1/dashboard/user/{account_id}/weekly`) —
   dashboard reads straight from MySQL `weekly_reports`, no S3 upload.
4. Writes report log
5. Outputs dashboard card-entry URL

**What YOU provide:**

| Param | Description |
|-------|-------------|
| `--nickname` | User's display name (from USER.md) |
| `--tagline` | Short witty one-liner summarizing the week (spoken Chinese, like a friend roasting with love) |
| `--commentary` | JSON: `{"logging": "...", "calories": "...", "weight": "...", "macros": "..."}` — 2-4 sentences each, casual spoken Chinese, funny/witty, backed by real numbers |
| `--highlights` | JSON array: 2-3 specific data-backed wins |
| `--suggestions` | JSON array of **strings**: 1-2 concrete actionable improvements. Each element MUST be a plain string like `"🍽 标题：描述"`, NOT an object. |
| `--plan-rate` | kg/week — use `meta.plan_rate` from collect output (default 0.5) |
| `--lang` | Report language: `zh` (default) or `en`. Read from user's profile or infer from conversation language |

> 🚨 **ALL parameters are REQUIRED with real content. Empty `'{}'` or `'[]'` = degraded experience. Read the data, think, write real commentary.**

For section-by-section rules: `read references/report-sections.md`
For edge cases (zero data, no plan, ED flags): `read references/edge-cases.md`

### Step 6: Send Mini-Program Card

周报**只发小程序卡片**(封面=「本周成长报告」信封图,点击进小程序内周报页)。
**不再发任何引导文字** —— 卡片本身已承载标题和跳转。

卡片点开走 `--target=weekly` → 小程序 loading → dashboard `/api/weekly?token=` →
读 MySQL `weekly_reports`(Step 5 已 PUT 进去)→ SSR-inject → Vue `Weekly.vue` 渲染。
即:发送走卡片(本步),内容走 DB 动态渲染(Step 5 写的 DB)。

```bash
bash {baseDir}/scripts/send-weekly-card.sh {account_id}
```

- `{account_id}` = workspace 目录名里的 accountId(如 `workspace-wechat-dm-acckyoy8...` → `acckyoy8...`)
- 脚本自动从 registry 反查 robotId,发 `--target=weekly` 卡片
- 输出 `ok` = 成功 → **本轮结束,回复 `NO_REPLY`,不再发任何文字**
- 非 0 退出 = 卡片发送失败 → **仅此时**回退到发一条文字 + 链接(见下方"回退文案"),保证用户至少收到周报

**回退文案(仅卡片发送失败时才用):** `{report_url}` = Step 5 脚本输出的 dashboard 卡片入口 URL(`/api/weekly?week=...`,读 DB 渲染),不是老 S3 链接。

```
📊 第{meta.week_number}周周报
完整分析 👇
{report_url}

{meta.progress_bar} 已走 {meta.progress_pct}%
{meta.start_weight} → {meta.current_weight} {unit} → 目标 {meta.target_weight} {unit}

{data_hook}
```

- Skip progress bar line if `meta.phase` is `"初始"` OR `meta.progress_pct` is `0`
- **快完成 phase:** Append `只差 {remaining} {unit}` after target weight

**data_hook:** ONE sentence citing specific data, sparking curiosity to click report.
For style examples: `read references/hook-examples.md`

**ED/avoid_weight_focus flags:** Omit progress bar, weight fields, and ⚖️ line. Hook focuses on consistency/variety.

---

## Pre-send Checks (cron auto-send)

1. Stage ≥ 3 → skip (still generate if manually requested)
2. < 2 days data in period → short encouragement message instead
3. All clear → generate and send

---

## Schedule & Trigger

- **Auto:** Sunday 21:00 user local time via per-user cron
- **Manual:** User says "周报" / "weekly report" → most recent completed Mon–Sun

---

## Report URLs

- Card-entry: `https://<dashboard-domain>/api/weekly?week={start_date}` (bare, no token)
- Notification-composer / cron adds `&token=<JWT>` when embedding into the message.
- Dashboard reads DB, SSR-injects the payload, and hands off to Vue SPA. No S3.

`account_id` is auto-derived from the workspace directory name (`workspace-wechat-dm-{accountId}`).
`DATA_API_URL` env override supports test / local dev (default `http://127.0.0.1:3100`).

---

## Writes

| Target | When |
|--------|------|
| `data/reports/weekly-data-{start_date}.json` | Local JSON copy (audit + prior-week reference) |
| `data/logs/weekly-report-{start_date}.json` | Report log (auto by script) |
| MySQL `weekly_reports` (via data-api PUT) | Server-of-record — dashboard reads from here |

---

## Skill Routing

Priority Tier P4 (Reporting). Owns all weekly summaries including exercise data.
Exercise-tracking does NOT produce separate weekly summary when this skill generates.

---

## Performance

- Single message, no back-and-forth
- Chat message: scannable in under 10 seconds
- Commentary per section: 2-4 sentences max

