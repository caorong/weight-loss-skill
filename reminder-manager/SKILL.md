---
name: reminder-manager
description: "User-requested reminder CRUD — covers both user-created custom reminders AND system meal/weight reminders. Trigger on: create ('提醒我...', '两分钟后提醒我', 'remind me...'), view ('我有哪些提醒'), change ('把午餐提醒改到12点'), delete ('取消喝水提醒', 'cancel my reminder'). Pausing ALL reminders for a period (请假) → leave-manager. System reminder bootstrap at onboarding → notification-manager."

---

# Reminder Manager

Manage the user's cron jobs: create, list, edit, delete. All scripts enforce agent-level isolation — a user can only see and modify their own jobs.

## Scripts

All scripts are in `{baseDir}/scripts/`. Always pass `--agent {agentId}` (your own agent ID).

### Create

```bash
bash {baseDir}/scripts/create-reminder.sh \
  --agent {agentId} \
  --name "喝水提醒" \
  --message "提醒用户喝水，简短自然一句话" \
  --at "2m"
```

Channel is auto-detected from agentId (`wechat-dm-*` → wechat, `wecom-dm-*` → wecom). No need to pass `--channel`.

Time options (pick one):
- `--at "2m"` / `--at "1h"` / `--at "30s"` — relative, one-shot (auto-deletes after firing)
- `--at "2026-04-11T09:00:00+08:00"` — absolute ISO timestamp, one-shot
- `--cron "0 9 * * *"` — recurring (add `--tz Asia/Shanghai` if needed)

Add `--keep` to preserve one-shot jobs after firing.

The `--message` is what you (the agent) will receive when the job fires. Write it as an instruction to yourself.

**IMPORTANT: Always prepend a pre-send-check call to the message.** This ensures the reminder is suppressed when the user enters recall stages (Stage 2+). The check also auto-creates/updates `engagement.json`, so it works even if the file doesn't exist yet.

```
先执行 python3 {notification-composer:baseDir}/scripts/pre-send-check.py --workspace-dir {workspaceDir} --meal-type custom --tz-offset {tz_offset}。如果输出 NO_REPLY，直接回复 NO_REPLY，不做任何事。如果输出 SEND，继续执行：[actual reminder instruction]
```

Examples:
- `--message "先执行 python3 {notification-composer:baseDir}/scripts/pre-send-check.py --workspace-dir {workspaceDir} --meal-type custom --tz-offset {tz_offset}。如果输出 NO_REPLY，直接回复 NO_REPLY，不做任何事。如果输出 SEND，继续执行：提醒用户该喝水了，简短自然"`
- `--message "先执行 python3 {notification-composer:baseDir}/scripts/pre-send-check.py --workspace-dir {workspaceDir} --meal-type custom --tz-offset {tz_offset}。如果输出 NO_REPLY，直接回复 NO_REPLY，不做任何事。如果输出 SEND，继续执行：Ask the user if they've had lunch yet"`
- `--message "先执行 python3 {notification-composer:baseDir}/scripts/pre-send-check.py --workspace-dir {workspaceDir} --meal-type custom --tz-offset {tz_offset}。如果输出 NO_REPLY，直接回复 NO_REPLY，不做任何事。如果输出 SEND，继续执行：提醒用户去称体重，鼓励一下"`

### List

```bash
bash {baseDir}/scripts/list-reminders.sh --agent {agentId}
```

Returns JSON:
```json
{
  "ok": true,
  "count": 3,
  "jobs": [
    {
      "id": "abc123...",
      "name": "午餐提醒",
      "enabled": true,
      "schedule_type": "cron",
      "schedule_expr": "0 12 * * *",
      "timezone": "Asia/Shanghai",
      "message": "Run notification-composer for lunch.",
      "session_target": "isolated"
    }
  ]
}
```

Present to user in friendly format: translate cron expressions to natural language (e.g. "每天中午 12:00"), show name and enabled status. Don't expose job IDs or internal fields unless user asks.

### Edit

```bash
bash {baseDir}/scripts/edit-reminder.sh \
  --agent {agentId} --job <jobId> \
  [--name "新名字"] [--cron "30 11 * * *"] [--at "..."] \
  [--message "新内容"] [--enable] [--tz "Asia/Shanghai"]
```

To edit, you need the job ID. If you don't have it, run list first.

> 🚫 **不支持 `--disable`（已移除）。** agent 不得 disable 任何 cron。要让用户暂停接收提醒
> → 走 `notification-composer` 的 `leave-manager.py` 写 `leave.json`（pre-send-check 在请假期
> 自动静默 cron、到期自动恢复，全程不碰 cron）。直接 disable 会导致请假结束提醒恢复不回来。

### Delete

```bash
bash {baseDir}/scripts/delete-reminder.sh --agent {agentId} --job <jobId>
```

Permanently removes the job. To find the job ID, run list first.

## Workflow

1. **User asks to see reminders** → run list → format output as friendly Chinese/English
2. **User asks to create** → confirm what and when → run create → confirm done
3. **User asks to change** → run list to find the job → run edit → confirm change
4. **User asks to delete/cancel** → run list to find the job → confirm which one → run delete → confirm done
5. **Ambiguous request** → ask user to clarify (which reminder? what time?)

## Rules

- Always use `--agent {agentId}` (your own ID). Never pass another agent's ID.
- Scripts enforce ownership server-side — if you accidentally pass a wrong job ID, the script rejects it.
- Users can manage ALL their reminders (including system meal/weight ones).
- When creating recurring reminders, suggest sensible defaults if user is vague ("every morning" → `--cron "0 8 * * *"`).
- For one-shot reminders ("in 5 minutes"), use `--at "5m"`.
