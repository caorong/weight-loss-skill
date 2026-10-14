# Weight Loss Skill — 本地减肥教练（standalone v5）

> **GitHub v5 提交版本：** 本次改动基于已合并 PR #1 的 `main`（`af51bc2`），新分支为 `feat/local-coach-v5`。
> 安装 AI 应使用该分支或对应提交的源码，先读取 `AI-INSTALL.md`。只有本次 PR 合并后，主分支才包含 v5。
> 独立版回归测试与模型行为验收分开；真实 OpenClaw／手机图片／原生提醒投递仍需在实际环境分层验收。

本 fork 的目标是：**现有视觉模型负责理解，Python 负责真实记录；资料、计划、每日记录和复盘使用同一套本地数据。**
不搬迁原作者的 SaaS 运营系统，不接缺失的 meal_checkin 插件，不需要营养 API、MCP、MySQL、S3 或微信小程序后台。

只安装一个入口 `diet-tracking-analysis`，它现在包含本地教练的完整基础流程。
原仓库其他技能目录保持原样，**不要因为功能同名而一起装回**。
本地账本不等于本地模型：照片仍交给你配置的视觉模型，费用与隐私条款照常适用。

## 能做什么

| 流程 | 实现方式 |
|---|---|
| 资料和偏好 | 明确提供的数据存到本地健康 Markdown；保留无关旧内容；更新有版本检查 |
| 照片/文字记餐 | 原生视觉估算 + 本地 meal-log.py；支持追加、局部纠正、删除、汇总 |
| 体重/运动记录 | coach.py 保存真实报告、纠正和删除；未提供的运动消耗不当成 0 |
| 减肥目标/食谱/训练计划 | 现有模型生成文字草稿；确认后持久化；按生效日期选择版本 |
| 每日复盘 | 只看当地今天：已记录饮食、当日目标、已完成运动及记录完整度，给有依据的食物取舍；不生成明日计划 |
| 每周复盘 | 默认上个完整周一至周日：有数据支撑的趋势、重复问题和运动执行情况，保存 Markdown 和快照 |
| 计划调整 | 周复盘可提出调整草稿；**确认后才生效**，日复盘不写计划，不自动降低热量或加大训练 |
| 提醒/定时复盘 | 每日／每周分别自愿开启、改时间或关闭；本地配置 + 原生 OpenClaw scheduler，默认无任务 |

提醒代码负责生成配置、比较网关现状、核验注册和发送前 gate；AI 用实际原生工具执行任务操作。
**保存提醒配置 ≠ 已注册定时任务 ≠ 手机收到消息。** 三个状态分开验收。
本仓库测试不向真实手机发送消息，不在你的 OpenClaw 自动创建任务。

## 交给 AI 安装

直接给安装 AI：

> 请安装 caorong/weight-loss-skill 的 feat/local-coach-v5 分支；使用该分支的 v5 源码。
> 先读 AI-INSTALL.md，使用 uv 安装独立本地教练入口，验证资料/饮食/体重/运动/计划/复盘和技能加载。
> 不全量安装上游技能，不覆盖我已有档案，不关闭沙箱。提醒默认关闭；需要时再用原生 scheduler 设置并核验。
> 真实图片传递、模型生成效果或提醒投递未测试时，分别标明未验证。

默认 **uv >= 0.10.0 + Python 3.13 系列**；解释器代码底线 3.10，第三方 Python 依赖仍为零。
平台：macOS/Linux/WSL + 系统时区数据库；POSIX 本地可写持久化 workspace。
uv、Python 和保存路径必须在 Agent 的真实 exec 环境里可访问，不能只在另一个终端能用。

```bash
# 获取 v5 分支（本次 PR 合并前不要用旧 main）。
git clone --branch feat/local-coach-v5 https://github.com/caorong/weight-loss-skill.git
cd weight-loss-skill

# 以下路径必须由安装 AI 查询实际 Agent 配置后替换。
uv run --locked --project diet-tracking-analysis python scripts/install-standalone.py \
  --workspace "/actual/agent/workspace" --timezone Asia/Tokyo --dry-run
uv run --locked --project diet-tracking-analysis python scripts/install-standalone.py \
  --workspace "/actual/agent/workspace" --timezone Asia/Tokyo
```

已有同名入口需明确授权后加 `--replace`：先备份，捕获安装失败后回滚。
只复制经过适配的脚本/说明/uv 清单，不复制旧后台或临时源码的 .venv。
安装器在最终技能目录创建 .venv，并把两类命令写进 runtime.json：

```text
commands.meals   → meal-log.py（饮食）
commands.coach   → coach.py（资料、体重、运动、计划、复盘、提醒配置）
```

日常命令使用 `uv run --locked --offline --no-python-downloads`，不需要激活 venv。
不临时 pip、不在记餐时更换模型或安装包。`--runner python` 是显式授权的零依赖备用，不自动降级。
更换机器/容器路径后重新安装；不要移动 .venv。仓库源码可以删，安装环境不依赖它。

安装器运行两组临时数据冒烟自检，不污染真实健康记录。
它不会自动停用既有全局技能/旧 cron；安装 AI 须检查冲突，按授权处理。
建议使用专用 Agent。`USER.md` / `AGENTS.md` / 网关配置不会被安装器覆盖。

## 本地数据地图：什么记录在哪里，谁说了算

所有路径相对于实际 Agent workspace，不是源代码仓库。

| 文件/目录 | 内容与权威性 | 如何更新 |
|---|---|---|
| `USER.md` | OpenClaw 身份/语言/时区等原有上下文 | 本技能不覆盖；时区优先读它 |
| `health-profile.md` | 身高、年龄、目标体重、健康限制等；标记块内为结构化权威资料 | `profile-set` 只改管理块；保留其他内容 |
| `health-preferences.md` | 过敏、喜好、烹饪条件、时间约束、常见份量等 | `preferences-set`，先读版本、合并列表 |
| `data/meals/YYYY-MM-DD.json` | 当地日期的**已食用**记录数组，食物来源、份量来源、ID、revision | `meal-log.py` 是唯一写入器 |
| `data/weight.json` | 体重读数，统一 kg；实测日期、时间精度、空腹标记、ID、revision | `weight-add/update/delete` |
| `data/exercise.json` | 日期 → 已完成运动数组；时长、可选总消耗/净消耗、来源 | `exercise-add/update/delete` |
| `data/plans.json` | 目标/食谱/训练计划的草稿、确认版本、生效日期、来源复盘 | **计划权威数据源**；`plan-save/plan-activate` |
| `PLAN.md` | 确认过的目标计划可读投影 | 自动更新管理块；不是另一套目标源 |
| `MEAL-PLAN.md` | 确认过的食谱可读投影 | 同上，无网站发布 |
| `EXERCISE-PLAN.md` | 确认过的训练计划可读投影 | 同上，无 S3/卡片 |
| `data/coach-reviews.json` | 每次复盘的数据快照、模型分析、版本 ID | `review-save`；同周期可保存多个版本 |
| `data/reports/coach-review-{daily\|weekly}-START-END.md` | 对应类型／区间最新可读复盘；旧 custom 文件名不变 | 本地 Markdown，直接文字回复也可 |
| `data/coach-reminders.json` | 想要的提醒设置、暂停状态、核验过的原生 job ID/hash | 配置/注册/送达分开处理 |
| `data/coach-review-deliveries.json` | 日／周复盘通知的当地日期或周期预留记录 | gate 发送前去重；不是消息送达证明 |
| `data/requests/*.json` | 每个操作独立的短期输入文件 | 使用新 context 返回的唯一路径；失败待重试时保留 |
| `data/meal-ledger-state.json` | 饮食请求回执和中断恢复 journal | 仅 meal-log.py 维护 |
| `data/coach-state.json` | 其他教练操作的请求回执和多文件恢复 journal | 仅 coach.py 维护 |
| `data/meals/backups/` | 饮食修改/删除前的副本 | 自动保存，不自动清理 |
| `data/coach-backups/` | 资料、体重、运动、计划等修改前副本 | 自动保存，不自动清理 |
| `data/skill-backups/` | 显式升级技能前的旧代码副本 | 安装器维护，可能包含旧 .venv |

### Markdown 与 JSON 不会互相抢“真相”

资料是人类可读的 Markdown，但管理块中的每一行是确定格式的 JSON 值：

```text
<!-- standalone-coach:profile:begin -->
## Local coach profile
- height_cm: 170
- revision: 1
<!-- standalone-coach:profile:end -->
```

以上仅格式示例，不会预写你的身高。实际脚本还记录 updated_at。
AI 不手动改这个块，用相应命令更新；块外原有内容保留。遇到冲突旧文本先核对，不当作没看到。

计划以 `data/plans.json` 为准，三个 Markdown 是可读投影。
查询“当前计划”必须按 **已确认 + effective_date ≤ 查询日期** 选择，不能只读文件末尾。
未来计划今天确认不代表今天生效。同日多个确认版本按序选择最新。
修改计划不重写历史目标；复盘每一天使用那一天有效的版本。

体重保留原 ISO 时间键布局：未提供实际称重时刻时，键只是当天的唯一存储锚点，
`observed_at=null`、`time_precision=day`；**不意味着你凌晨称过体重**。
每次真实报告都保留，纠正必须指向 ID，不再按“30 分钟内”自动覆盖。

运动热量缺失是 null；已知总热量的合计有 `calories_complete` 标记。
复盘单独统计已知净消耗和未知消耗的次数，绝不把运动消耗自动换成“还能再吃多少”。

## 日常完整流程

```text
保存资料/偏好
   ↓
模型生成目标/食谱/训练草稿 → 用户确认 → 按生效日启用
   ↓
照片/文字饮食 + 实际体重 + 已完成运动 → 分别真实落盘
   ↓
手动或经同意的定时复盘 → 读取本地快照、保存文字分析
   ↓
周复盘可提出计划调整草稿 → 再次确认后生效（日复盘不写计划）
```

没有建档也能记餐；没有体重也能记录运动时长；不知道热量不会伪造一个 0。
一条消息有饮食/体重/运动时，每个领域独立请求 ID 和回执。某项失败只报告那项失败，
重试不能把其他已经成功的记录重复新增。它们不是跨领域的一笔全局原子事务。

现有模型负责识图、建议和计划正文；Python 不偷偷重复调用模型。
照片份量和营养是估算。用户称重/清晰包装标签优先；不把菜单、想吃、整桌共享菜当成实际摄入。

## 跨天和“多读数据再过滤”

跨天错误来自把昨天获取的“今天”继续用来写入，不是历史数据读得不够多。
现在要求 **每个新的操作重新运行 context**，按 IANA 时区取 now/today/yesterday/week 区间。
`today` / `yesterday` 写入在脚本首次执行时解析；明确补记传指定 ISO 日期。
重试保留原请求 ID 和原输入，跨过零点也不会把已记录的昨天数据再写一份到今天。
如果可信渠道时间戳表明原消息延迟，应按原消息当地日期解析“今天”，而不是猜。

查询同样重新确定窗口再按记录日期过滤：last7 包含今天，这一天可能不完整；
this-week 是本周一至今，last-week 是上个完整周。筛选负责选数据，不能修复写错的归属日。

## 每日和每周复盘，各做什么

- `review --review-type daily` 默认只复盘**当地今天**，不偷换成近七天或昨天。
  手动更正／补记后可明确查询历史单日；定时日复盘始终是当天。
- `review --review-type weekly` 默认 `last-week`，即**上一个已完整结束的周一至周日**。
  周日晚上触发时，本周仍未结束，所以也不会擅自纳入本周。
- 旧的 `review --period last7` 和 `--review-type custom` 仍支持原来的自选周期。

日复盘先报截止时间和覆盖情况，再列已记录热量、蛋白质／碳水／脂肪与当天生效目标；
运动的项目、时长、已知估算消耗单独列出。不知道的目标、运动消耗或漏记情况保持未知。
有记录依据时给 1–3 条具体的少吃一点／省去／替换思路，同时考虑饱腹感、营养和偏好；
不编造吃过的食物，不为了凑建议而硬减餐，不安排明天任务，不把多运动或少吃作为补偿。
假设的替代选择与实际吃过的食物明确区分，日复盘不保存或激活新计划。

周复盘重点看有证据的体重趋势、重复饮食问题和运动情况。数据不足就说明不足；
未记录不等于没吃、没运动，缺少有效训练计划和足够记录时不判定运动依从性。
调整草稿不改变目标，确认具体草稿后才生效。

**摄入与目标的差值不是真实能量盈亏，也不能推算减脂量。** 不把运动消耗从摄入中减去
做“净热量结算”，也不在已含活动量的 TDEE 上重复加算运动。
晚到记录按实际发生日期保存；可重新生成对应报告，但不重复推送同一天／同一周期通知。

## 手机单人使用与临时文件

单手机、顺序聊天时共享临时文件冲突概率较低，但重试/排队/定时复盘仍可能重叠。
不引入队列或新服务：context 直接返回每操作唯一的 `data/requests/<token>.json`。
这是很小的文件命名防护，不是重新设计并发架构。收到成功回执后可清理对应输入文件。

账本继续用文件锁、原子替换和 write-ahead journal。
重复同一请求返回旧回执，不代表当前状态：AI 要再查当前累计/计划。
没有稳定渠道 message ID 时，仅保证保留 token 的工具重试，不保证上下文丢失后的完整渠道去重。

## 提醒和定时复盘：只有原生 OpenClaw

AI 按 `references/native-reminders.md` 操作：

1. 确认 IANA 时区，分别询问每日是否开启及时间、每周是否开启及星期／时间；
   经用户同意后保存明确的 Agent/channel/to。任一项拒绝都不影响记餐。
2. 读取完整原生任务列表，用 reminder-sync-plan 生成差异。
3. 用实际原生 scheduler 工具添加或更新，重新读取任务并 reminder-bind 核实。
4. 触发时重新读本地状态和时间，检查暂停、停用、是否已记录、是否超时，再输出文字。

不写原作者模型 ARN，不读进程密钥，不访问 lifecycle/data-api。
计划生成和建档都不会偷偷开启提醒。配置保存失败、工具不可用、收件人不明不能声称已设置。
停用本地配置会立刻阻止 gate 发送；仍需同步原生任务 enabled=false。
暂停期内保留的原生任务可能继续调用模型但不发消息，不承诺节省调用费。
触发超过配置时间 60 分钟会跳过，避免断线重试把提醒拖到深夜或次日。

新日／周配置仍使用 `kind=review`，必须明确 `review_type=daily|weekly`。
daily 每天运行，weekly 只选一个星期几；两者可独立修改、暂停或关闭。
已有未带类型的旧 `kind=review` 保持 `last7`，不会升级时静默改成日／周复盘。
迁移需用户明确选择，并同步核验原生任务。

同一路由、同一时间同时到期的日／周复盘由一个确定的主任务合成一条分段消息。
gate 在输出前按明确路由、IANA 时区、复盘类型和当地日期／周期原子预留通知，
重复或并发触发被抑制；同一路由即使配置两个时间，也不会重复推送同类同周期。
这是**本地最多一次发送资格**：预留后进程或 announce 失败可能漏发，不会自动重试，
也不保证原生传输层恰好投递一次。报告保存与通知预留彼此独立，补记重算不重发。
本次代码改动和测试不在用户环境启用任何真实日／周任务。

默认支持单个已明确解析的渠道账户/会话。若渠道要求额外 accountId/thread 字段，
必须扩展路由并在现场验证；不丢弃字段或猜收件人。实际投递状态以网关为准。

## 数据维护、备份、升级

**日常：** 通过脚本/AI 查询、纠正、删除，不直接编辑 JSON。旧格式损坏/管理块异常会停止，
不静默清空。备份和回执是恢复链的一部分。文件默认 0600，本地目录应只供本人访问。

**备份：** 暂停这个教练的写入与定时操作，确认没有正在执行或待恢复事务，然后私密备份
健康 Markdown、三个计划 Markdown 和整个 `data/`。一起保存，不只复制当天的 meal 文件。
备份应放在自己的加密/访问受控存储；不要提交公开 GitHub。无需备份 .venv。

**恢复：** 停止相关操作后把同一备份集一起恢复，再用 AI 安装器重建运行环境并测试。
不要混用不同时间点的 ledger、state 和 plan registry；pending 冲突须核对修复，不删除 state 蒙混过关。

**删除与保留期：** 删除当前餐/体重/运动不会清除旧备份、回执、复盘快照。
这些文件会随时间增长。没有自动清理，避免破坏重试/恢复保证；彻底清理要用户授权，
在停写、无 pending 时制定保留策略。删除回执会弱化旧请求去重；不承诺安全擦除。

**升级：** 在源码中更新脚本和锁文件，重跑测试，再显式 --replace；数据在 workspace/data，
不在技能目录内。安装器只替换代码，不重置健康资料。旧全局技能/任务不会被自动停用。
不要让上游旧写入器和本实现同时管理相同文件，它们不共享锁。

限制：本地 POSIX 文件系统；NFS/多人共享写入/同用户恶意进程不在安全保证范围。
不要为运行技能而关闭沙箱。旧的未适配模块仍不是本地教练依赖。

## 测试与验收

```bash
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_meal_log.py' -v
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_coach.py' -v
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_standalone_install.py' -v
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_uv_install.py' -v
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_coach_integration.py' -v
```

这些是独立版回归测试；原仓库遗留测试不代表新入口的支持范围。
测试全部用合成数据/临时 workspace；native job 列表为模拟数据，未注册实际任务。
真实 OpenClaw 加载、图片传递、模型规划效果、手机提醒投递必须由安装 AI 分别验收。
`diet-tracking-analysis/evals/daily-weekly-reviews.json` 是人工／真实模型验收场景，
不是已经运行的模型测评；离线脚本通过不证明建议质量、真实消息合并或手机投递效果。

接口详见安装后随附的 standalone-flow.md、coach-api.md、coach-flows.md、native-reminders.md。

## License

MIT；保留原作者 Link Heart Limited (NanoRhino) 的版权声明，见 LICENSE。
