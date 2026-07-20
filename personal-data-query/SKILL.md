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
