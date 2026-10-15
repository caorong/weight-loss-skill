# AI 安装与验收入口

**安装来源：** `main` 的当前源码或用户明确指定的提交；测试未合并 PR 时使用已核实的 PR head。
先确认 checkout 含 `diet-tracking-analysis/scripts/coach.py`、`diet-tracking-analysis/references/coach-api.md` 且技能版本为 5.0.0。
七项提醒初始化还需 `scripts/coach_reminder_onboarding.py` 及 `reminder-onboarding` 命令；
若缺失，说明目标提交尚未包含该功能，不要将旧功能分支描述为最新版。

适用：用户委托你把本 fork 的独立饮食记录技能安装到其 OpenClaw。
先阅读本文件、README、安装器及技能说明，再执行。只安装一个独立入口，包含现有视觉模型 + 本地资料/饮食/体重/运动/计划/复盘/提醒配置，
不是移植上游全部减肥服务。无需用户手工复制文件、激活 venv 或配置新的模型密钥。

## 1. 解析真实环境，不猜路径

从用户指定的 Agent、当前运行时上下文或 OpenClaw 的只读配置/CLI 查询解析实际 workspace。
命令以当地安装版本的 `openclaw --help`、相应子命令帮助为准；不要凭记忆编造参数。
只读取所需 Agent 的 workspace/工具权限，不打印完整配置、令牌或其他用户资料。
有多个 Agent 且无法确定对象时，只澄清目标；不要任选一个写入。

检查实际 exec 环境是 macOS/Linux/WSL、有持久化且可写的 workspace、系统时区数据库。
容器内的路径、uv、Python 与宿主机可能不同；应在 Agent 真正执行脚本的环境安装/验证。
不得通过关闭沙箱、扩大到全部工具权限、读取进程密钥或覆盖配置来绕过问题。

时区采用已有 USER.md/用户明确设置，不从聊天语言推断；本次用户设置是 Asia/Tokyo。
不覆盖 USER.md、PLAN.md、AGENTS.md 或既有健康数据。缺少完整画像不阻止记餐。

## 2. 准备 uv，不混用包管理器

默认 uv >= 0.10.0；Python 默认 3.13 系列（`.python-version`，未锁具体补丁版本）。
解释器兼容底线是 Python 3.10。当前 `dependencies = []`，无第三方 Python 依赖。
三个清单都位于 `diet-tracking-analysis/`，安装时一并携带：
`pyproject.toml`、`uv.lock`、`.python-version`。

uv 不存在：按 Astral 官方安装文档，在用户许可的安装范围内准备 uv；不要使用未知镜像
或自动 sudo。无安装权限/无法联网时说明阻塞。不要未经同意静默切回系统 Python。
必须使用 uv 的绝对路径或确认 Agent 的 PATH 能找到它。

Python 3.13 已有则复用；没有时只在安装阶段用 uv 准备：

```bash
uv python find --no-python-downloads 3.13
# 仅上一条确认不可用且有下载权限时：
uv python install 3.13
```

安装/开发允许按已提交锁文件准备环境；日常记餐禁止更新锁文件、下载 Python 或全局 pip。
`uv run --locked` 不会悄悄重写锁文件。安装后日常命令额外使用 `--offline`。
不要设置 UV_PROJECT_ENVIRONMENT / UV_WORKING_DIR 到其他项目。

## 3. 安装

在已核实的 `main`／指定提交的源码目录执行，也可使用相同版本的安装包。
测试未合并改动时先 checkout 对应 PR head；将 workspace 和时区替换为步骤 1 的真实值：

```bash
uv run --locked --project diet-tracking-analysis python scripts/install-standalone.py \
  --workspace "/actual/agent/workspace" --timezone Asia/Tokyo --dry-run

uv run --locked --project diet-tracking-analysis python scripts/install-standalone.py \
  --workspace "/actual/agent/workspace" --timezone Asia/Tokyo
```

已授权升级同名技能时才增加 `--replace`：先备份，失败回滚。不是把整个仓库复制进 skills。
`--dry-run` 表示安装器不写目标文件；外层首次 `uv run` 仍可能准备源码项目的环境。
要测试完全离线安装，在预先准备好 Python/缓存后给外层 uv 和安装器都加 `--offline`。

安装器在最终技能目录建立新的 `.venv`，不复制源码的虚拟环境。切勿打包/迁移 `.venv`。
返回值必须是单个 JSON，只有退出码 0 且 `ok: true` 才表示安装器成功。
缺 uv、依赖/锁文件不符、写权限或自检错误都应停止，不得声称成功。
显式 `--runner python` 只作为当前零依赖版本的授权备用方案；不自动降级。

## 4. 验收分层，禁止混淆

安装器已自动做临时工作目录中的饮食 CRUD/重试测试，以及资料、偏好、体重、运动、三类计划确认和本地复盘测试，
并检查真实 workspace 可写。虚构测试餐次只存在临时目录，不进入用户账本。

接着在 Agent 实际 exec 环境读取已安装的 `runtime.json`，用其中 `commands.meals` / `commands.coach` 数组作为
argv 前缀，分别执行 list/summary 和 context/measurements/plans/review，带真实 --workspace/--tz。
旧 runtime 没有 commands.coach 必须升级。若实际旧数据格式不支持，先告知并迁移；不清空。
不要根据安装机器的另一个 Python 路径自行重写命令。无需 `source .venv/bin/activate`。

按本地 OpenClaw 的技能检查/会话能力确认 `diet-tracking-analysis` 已加载且 exec 被授权。
如需刷新会话则按该版本支持的方式操作，不自动重启网关或中断其他 Agent。
安装器不自动禁用旧全局技能/cron；发现冲突先告知，按明确授权处理，不全局清空技能。

文字记账验证不等于视觉验证。若没有用户授权的真实饭照，报告 `vision: pending`；
不要凭脚本自检声明图片识别已经正常。首次实际图片应检查图片可见、营养标为估算、保存
成功后新会话可查询。勿创建新模型密钥或在 Python 中重复调用图片 API。

最终向用户报告：源码版本、目标 Agent/workspace、runner、安装结果、脚本自检结果、
OpenClaw 加载状态、视觉状态、模型计划生成状态、原生 scheduler 可用/注册/投递状态；明确哪些已验证、哪些尚待实际环境验证。
不输出健康记录明细或密钥作为“证明”。

## 5. 原生提醒验收（默认不创建）

先读安装后 references/native-reminders.md。首次交互初始化必须明确列出七项独立选择：
日复盘、周复盘、早餐提醒、午餐提醒、晚餐提醒、体重提醒、运动提醒。
可一条清单统一询问，允许部分开启、全部开启、全部不开或暂时跳过，不必逐项问七轮。
用 reminder-onboarding / context.reminder_onboarding 读取进度；问题实际展示后记录 offered，
按明确回答用 reminder-onboarding-set 保存 accepted / declined / skipped，不推断未回答项。
未回答、拒绝、跳过都不启用；选择开启后也先保持待配置。明确拒绝／跳过后不反复追问，
日常记餐不重启问卷、不被阻碍。用户主动继续设置时从保存的选择继续。
全部开启只代表选中七项，随后确认各项当地时间、IANA 时区、投递对象；
周复盘确认星期几，运动提醒确认运动日，餐次和体重提醒也确认适用日期。
复用已有偏好作建议值，不能把文档示例或已知作息当授权。完整确认后才保存 reminder-set，
用实际原生 scheduler 列出完整任务、reconcile、执行差异、读回核实再 bind。
已有配置（含关闭状态、自定义 key、旧未分型复盘）不因升级而改动或重复创建；
旧复盘需明确迁移。安装器不发问题或创建任务，非交互安装留到首次合适的交互初始化询问。
不要在本次代码升级／离线验收中激活真实任务。
不猜工具参数，不把本地配置成功当成网关注册成功；不为了运行而装回旧提醒技能。
没有用户授权的新任务时保持零任务，报告 scheduler=available 或 not_tested，不发送测试消息。
实际手机投递需单独授权和现场测试。工具缺失/权限不足则明确报告，不安装作者后台。
若频道需要多个 accountId/thread 路由，当前单路由模式不足；扩展后验证，不能忽略这些坐标。
新提醒 payload 不覆写 Agent 模型；输出只走 announce，不能再调 message/curl 重复投递。
验证停用/暂停 gate，配置变更后重新核验网关任务。不要清空其他 Agent 的任务。

新复盘设置必须有 `kind=review` 和 `review_type=daily|weekly`：daily 是每天的 today，
weekly 是所选单个星期几触发的 last-week（上个完整周一至周日）。
保留旧未带 review_type 的 review 配置原有 last7 语义，取得明确选择后再迁移。
离线验收应覆盖当天／上周边界、DST／跨月跨年、晚到记录、独立停用、旧配置、并发去重、
同时间同路由日周合并与不同时间／路由不合并。通知预留先于 announce，失败可能漏发；
这是本地最多一次资格，不是真实投递证明，不为了测试而发实际消息。

## 6. 日常使用

安装后的 `SKILL.md` 和 `references/standalone-flow.md` 是操作契约：
- 明确照片/文字打卡 -> 原生模型估算 -> JSON -> meal-log.py 保存 -> 根据真实回执回复。
- 纠正先查 ID，只改指定项；份量变化用 scale，不重算其他菜。
- 重试复用 request_id 和输入；有记录日平均不等于实际全部摄入平均。
- 不激活原来的 meal_checkin、强制卡片、后台周报或运营召回。
- 每个新操作重新 context 取日期；补记用指定日期；重试不换原日期/ID。
- 使用 context 的唯一 request_file；一手机也不共享固定输入文件。
- 资料/偏好用 coach.py 更新；体重/运动必须收到真实保存回执。
- 计划本地草稿→明确确认→按生效日启用；复盘建议不自动改目标。
- 日复盘只看当日已记录事实、有效目标、运动和完整度，有证据才给食物取舍；
  不写明日任务／计划，不要求补偿性少吃或运动。周复盘有数据才谈趋势和执行情况。
- 摄入与目标差值不叫真实能量盈亏；不扣运动算净摄入，不重复计算 TDEE 活动量。
- 晚到记录回到实际发生日，受影响报告可重算，不重复发送该日／周通知。
- 环境坏了就修安装，不临时 pip、换解释器、移除锁定参数或手改账本。

模型行为另用源码 `diet-tracking-analysis/evals/daily-weekly-reviews.json` 和
`diet-tracking-analysis/evals/reminder-onboarding.json` 人工验收；
它是待执行的场景清单，不是离线测试已验证的模型效果。没有实测时如实标为未验证。

官方参考：
- https://docs.astral.sh/uv/getting-started/installation/
- https://docs.astral.sh/uv/concepts/projects/sync/
- https://docs.openclaw.ai/tools/skills
