---
name: personal-data-query
version: 1.0.0
description: "Query personal health and diet data. Use when user asks about today's intake, daily progress, calorie summary, or 'how am I doing today'. Trigger phrases: 'how many calories today', 'what did I eat', 'today's progress', '今天吃了多少', '今天还剩多少', '今日进度', '今天怎么样了'. Do NOT trigger for logging food or recording weight — those go to diet-tracking-analysis and weight-tracking respectively."
metadata:
  openclaw:
    emoji: "bar_chart"
---

# Personal Data Query

## Role

Concise data reporter. Show the numbers, keep commentary minimal.

## Tool

```
meal_checkin({ action: "query_day", workspace_dir: "{workspaceDir}" })
```

Format result per the response schema below.

---

## First-Time Mini-Program Guidance (append once)

The **first time** a user asks for a daily summary, after the data block tell
them their data is viewable in the mini-program and offer a daily auto-send.
Show this **exactly once per user**, then never again.

**Check first** — read `{workspaceDir}/data/engagement.json` and look at
`activation.daily_summary_prompted`:
- If it's `true` → this user was already prompted. Just return the data block. **Skip everything below.**
- If missing/`false` → this is the first daily-summary ask. Do the steps below.

**Steps (first time only):**

1. Return the normal data block (per the schema below).
2. Append the guidance (language per `USER.md > Language`, zh default):

   > 💡 这些数据在小程序里随时能看哦～点开卡片就能查。
   > 要不要我**每天定时**给你发一次？这样不用主动问也能看到当天的进度 🙌

   (en: "💡 You can check this anytime in the mini-program — just tap the card. Want me to send it to you automatically every day, so you can see your progress without asking? 🙌")
3. **Mark as prompted** (deterministic, atomic — do NOT hand-edit the JSON):
   ```bash
   python3 {notification-manager:baseDir}/scripts/activation-mark-sent.py \
     --workspace-dir {workspaceDir} --flag daily_summary_prompted
   ```

**When the user says yes to the daily send:**

Ask what time they'd like it (don't assume a default), then create a per-user
daily cron that sends the mini-program card. Use the notification-manager
wrapper (never call `openclaw cron add` directly):

```bash
bash {notification-manager:baseDir}/scripts/create-reminder.sh \
  --agent {agentId} --name "[custom] 每日数据卡片" \
  --cron "0 {hour} * * *" \
  --message "先执行 python3 {notification-composer:baseDir}/scripts/pre-send-check.py --workspace-dir {workspaceDir} --meal-type custom --tz-offset {tz_offset}。如果输出 NO_REPLY，直接回复 NO_REPLY，什么都不做。否则执行:发一张 dashboard 小程序卡片(点开进小程序看当日数据)——bash {diet-profile-card:baseDir}/scripts/send-profile-card.sh {account_id} \"今天的数据我给你送来啦,点开看看进度 👇\"。发卡成功回 NO_REPLY;失败才回退到一条文字。User workspace: {workspaceDir}"
```

- `{hour}` = user's chosen local hour; channel/timezone/anti-burst are auto-resolved by the wrapper.
- The card opens the mini-program **dashboard home**, which already shows today's calories/progress — no separate daily page needed.
- Confirm to the user it's set (e.g. "好啦～以后每天 {time} 给你发 📬").

---

## Response Schema

📊 今日摄入：
🔥 XXX/TARGET kcal
███████░░░ XX%
蛋白质 Xg [status] | 碳水 Xg [status] | 脂肪 Xg [status]

🥦 蔬菜：~XXXg ✅/⬇️  🍎 水果：~XXXg ✅/⬇️

**Meals logged:**
· 餐次 — XXX kcal (食物列表简述)

**Calorie progress bar:** 10 chars, `█` filled `░` remaining. >100% → `██████████ XXX% ⚠️`

**Status:** ✅ on_track | ⬆️ high | ⬇️ low
