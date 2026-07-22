---
name: leave-manager
description: "Trigger when the user wants to pause proactive reminders for a period. MUST persist to data/leave.json (the system source of truth — cron auto-silences during leave, auto-resumes after); an empathetic reply without calling this skill = the leave never takes effect and cron keeps disturbing the user. Trigger on ANY pause signal regardless of wording. Explicit: '暂停提醒', '请假', 'pause reminders'. Implicit (MUST also trigger): '过段时间再说', '别烦我', '最近不想减肥', '出差一周', '节假日/春节期间不方便', '这几天聚餐多', '出去玩几天'. Also for ending leave early: '我回来了', '继续吧', 'I'm back'. NOT for one-off '今天不用提醒了' (single skip ≠ leave)."

---

# Leave Manager — 请假 / 暂停提醒

把用户的"暂停一段时间被动联系"意图**落库**到 `data/leave.json`（系统真源），并同步生命周期 `frozen` 状态。请假期间 cron 提醒由 pre-send-check 自动静默（job 原样保留、照常触发、被拦下不发），到期由 lifecycle reactor 自动解冻恢复——**全程不碰 cron job 本身**。

> ⚠️ **SILENT OPERATION：** 不要向用户叙述内部动作/工具调用。默默执行，只输出给用户看的话。

> 🚫 **绝不 disable / 删除 / 修改任何 cron job 来实现暂停。** 暂停只做一件事——写 `leave.json`。
> 动 cron（`edit-reminder.sh --disable` 等）会破坏用户提醒配置且请假结束恢复不回来 = 错误行为。

---

## 核心铁律

**口头答应 ≠ 请假生效。** 只回复"祝你假期愉快 / 回来了告诉我"而不调 `leave-manager.py set` 落库，
等于请假没生效——cron 会照常在请假期打扰用户。**检测到暂停意图，本轮必须执行落库动作。**

写 memory / short-term.json **也不算**暂停——cron 的 pre-send-check 只读 `data/leave.json`。

---

## 执行流程（检测到暂停意图时）

### 1. 确定起止日期
- `--start`：今天（或用户明确说的开始日，如"端午开始"）
- `--end`：用户说的返回日 / 今天 + 用户说的时长（"停一周" → 今天+7）
- **日期模糊**（如"过段时间再说""休息一阵"无明确时长）：先问用户确认
  「要帮你先暂停一周提醒吗？到时想继续随时跟我说～」**等用户确认后再落库**，不要擅自拍一个日期。
- 节假日场景（"端午/国庆出去玩"）：用对应法定假期区间即可（如端午 6/19–6/21）。

### 2. 落库（必做，本轮执行）
```bash
python3 {notification-composer:baseDir}/scripts/leave-manager.py set \
  --data-dir {workspaceDir}/data --tz-offset {tz_offset} \
  --start YYYY-MM-DD --end YYYY-MM-DD --reason "用户原话摘要"
```
脚本会自动：写 `data/leave.json` + 同步调 lifecycle `/silence` 把 DB 状态置 `frozen`（请假期 lifecycle-stage 注入 frozen，agent 不催打卡）。

### 3. 验证
检查命令 exit code = 0、`leave.json` 已写入。失败要重试或告知排查，**不要假装成功**。

### 4. 回复用户
告知**具体恢复日期** + "想提前恢复随时跟我说"。语气自然温暖，不要提"已落库/已设置 leave.json"这类系统术语。
例：「好呀～那 6/22 之前我先不打扰你，安心过节🎋 想提前回来随时跟我说一声就行～」

---

## 提前结束请假（用户说"我回来了"）

用户在请假期内表示回来了 / 想恢复：
```bash
python3 {notification-composer:baseDir}/scripts/leave-manager.py clear \
  --data-dir {workspaceDir}/data --tz-offset {tz_offset}
```
然后用回归语气迎接（"欢迎回来～"），不质问为何离开。clear 会同步解冻 lifecycle 状态。

---

## 查询当前请假状态
```bash
python3 {notification-composer:baseDir}/scripts/leave-manager.py info \
  --data-dir {workspaceDir}/data --tz-offset {tz_offset}
```
不确定用户是否已在请假中时先 info 查一下，避免重复设置。

---

## 边界（不归本 skill）

- **单次"今天不用提醒了"** → 不是请假时段，不写 leave.json（那是单次跳过）。
- **cron 提醒的发不发** → 请假期由 pre-send-check 读 leave.json 自动静默，本 skill 不碰 cron。
- **沉默用户的 cron 治理（churn）** → 由系统 churn-scan 负责，与请假正交（churn 不动 frozen 用户）。
- **创建/修改提醒本身** → `reminder-manager` skill。

> 脚本物理位置目前在 `notification-composer/scripts/` 下（历史遗留），故命令用 `{notification-composer:baseDir}`。
> 本 skill 是「请假/暂停」这件事的 owner，未来脚本可迁入本 skill（技术债）。
