# Weight Loss Skill — standalone native-vision edition

本 fork 的第一版目标：**用 OpenClaw 现有视觉模型记餐，把饮食账本可靠地存在本地。**
无需额外营养 API、MCP、数据库服务、`meal_checkin` 插件或作者的后台。

> 当前只适配了独立饮食记录入口。原仓库其余教练、提醒、小程序、周报发布等模块仍保留，
> **不代表它们已经兼容独立 OpenClaw**。请使用下面的轻量安装器，不要全量安装旧技能包。
> 本地账本不等于本地模型：照片仍由你配置的视觉模型处理，相关费用和隐私政策照常适用。

## 可以先用什么

- 照片或文字记餐：明确打卡时直接记录估算，不必每次确认。
- 新增、追加、改单项、按比例改份量、删食物/整餐。
- 当天/任意日期/最近七天已记录摄入汇总；没有减肥计划也可以记录。
- 同请求重试去重、并发锁、修改前备份、写入中断恢复。
- 沿用 `data/meals/YYYY-MM-DD.json` 数组，保留原有营养字段。
- 文字回复，不依赖图片卡片、浏览器或远程报告链接。

不包含：专业营养数据库查询、可靠的视觉称重、完整训练计划重构、自动热量目标调整、
提醒/召回/小程序接入。模型给出的份量和营养是估算，不是测量或医疗建议。

## 快速安装

需要 **Python 3.10+、macOS/Linux（Windows 可用 WSL）、系统时区数据库**。
Python 和 workspace 必须在 OpenClaw 实际执行环境里可访问。无 pip 依赖。

```bash
git clone --branch feat/standalone-native-vision https://github.com/caorong/weight-loss-skill.git
cd weight-loss-skill

# 替换为该 Agent 配置的真实 workspace；不是 OpenClaw 安装目录。
WORKSPACE="$HOME/.openclaw/workspace"

# 只预览，不写文件。
python3 scripts/install-standalone.py --workspace "$WORKSPACE" --timezone Asia/Tokyo --dry-run

# 安装一个技能，并检查本地账本写权限。
python3 scripts/install-standalone.py --workspace "$WORKSPACE" --timezone Asia/Tokyo
```

workspace 必须已经存在。安装器只复制以下文件到 `<workspace>/skills/diet-tracking-analysis/`：

```text
SKILL.md
scripts/meal-log.py
references/standalone-flow.md
runtime.json                  # 安装时生成：实际 workspace/Python/备用时区
```

不会覆盖 `USER.md` / `PLAN.md` / `AGENTS.md` / 体重数据；不会修改网关配置、注册模型、
安装后台、读取密钥或创建定时任务。已有同名技能需要显式 `--replace`，先备份后替换。
用户在 `USER.md` 配置的 Timezone 优先于安装时备用时区；所有 CLI 调用都显式传 `--tz`。

**隔离说明：** 安装器不删除或禁用已存在的全局技能/旧 cron。建议使用专用 Agent，
仅启用 `diet-tracking-analysis`；按你的 OpenClaw 版本配置实际可用技能和 exec 权限。
不要让旧 `meal_checkin` 流程、旧饮食写入器、周报发布或自动 onboarding 同时管理这个账本。
不要为了让技能运行关闭沙箱；应给沙箱挂载可持久化的 workspace 并提供 Python。

安装后开启新会话，在手机渠道测试：

```text
记录午餐：[附饭菜照片]
刚才米饭只吃了一半。
再加一杯牛奶。
今天已记录多少？最近七天怎么样？
```

第一次必须确认图片真的传给了视觉模型。不可见图片不能假装识别；可改用文字描述。
如果 Agent 尚未配置 `USER.md` Language，请先按自己的偏好设置，技能不会猜测回复语言。

## 命令行自检（不会生成假餐次）

```bash
SCRIPT="$WORKSPACE/skills/diet-tracking-analysis/scripts/meal-log.py"
python3 "$SCRIPT" init --workspace "$WORKSPACE" --tz Asia/Tokyo
python3 "$SCRIPT" list --workspace "$WORKSPACE" --tz Asia/Tokyo
python3 "$SCRIPT" summary --workspace "$WORKSPACE" --tz Asia/Tokyo
```

`init` 返回本地 today、yesterday、last_seven_days_start 和写权限结果。
JSON 输入、修改示例和错误处理详见
[diet-tracking-analysis/references/standalone-flow.md](diet-tracking-analysis/references/standalone-flow.md)。
营养值按“实际输入份量的合计”传给脚本，不是每 100g 数据；不要把毫升直接当克。

## 存储与可靠性边界

```text
<workspace>/data/
├── meals/YYYY-MM-DD.json       # 权威饮食记录，兼容旧 list 结构
├── meals/backups/<date>/*.json # 修改前原文件字节
├── meal-ledger-state.json      # 请求回执 + 单笔 pending 恢复日志
└── .meal-ledger.lock           # POSIX 互斥锁
```

脚本生成餐次/食物 ID、时间戳和 revision；按食物明细计算总量。缺记录的日子不算成
“摄入 0”，平均值标为“有记录日的已记录摄入平均”，不声称饮食记录一定完整。
每日目标只有用户确认后显式传入，脚本不会改计划。

同一个 request_id + 同一输入会返回旧回执，不再写一次。不同输入重复使用 ID 会报错。
修改/删除也支持去重；删除后重放旧新增请求不会让餐次复活。旧回执不一定反映最新状态，
Agent 在 duplicate=true 后应查询最新汇总。没有渠道稳定消息 ID 时只能保障保留 token
的工具重试，无法保证遗失上下文后整个渠道重投递仍然 exactly-once。

一个事务日志协调日文件和回执：中断后下次调用先恢复，再继续。若 pending 期间发现
外部改动，会报冲突，不覆盖。只保障使用本脚本和同一锁的写入；NFS、任意外部编辑器、
未适配旧脚本不在并发保证范围内。文件 0600，拒绝账本路径中的符号链接。

**删除不是安全擦除：** 备份与请求回执仍含历史数据，而且不会自动清理。需要清理时先
停止 Agent、确认无 pending 事务并做好私有备份。不要将 data/、照片、请求 JSON 提交
到公共仓库。灾难恢复应整体备份 data/，而不是只备份每天的 JSON。

## 开发与测试

```bash
python3 -m unittest discover -s tests -p 'test_meal_log.py' -v
python3 -m unittest discover -s tests -p 'test_standalone_install.py' -v
```

仅运行这两个独立测试集；上游其他测试可能依赖其部署环境，不把它们计作本版通过项。
自动测试覆盖 CRUD/来源字段/兼容旧 list、时区与补记、目标可选、非法数据、重试冲突、
并发重复/非重复写、写前备份、两类中断恢复、外部编辑冲突、符号链接和安装不覆盖档案。
图片准确率、真实模型/渠道、OpenClaw 权限和沙箱必须在实际设备上做端到端验收。

## 与上游的关系

Based on [NanoRhino/weight-loss-skill](https://github.com/NanoRhino/weight-loss-skill), MIT.
保留原作者版权与 LICENSE。本 fork 的独立饮食模块替代缺失插件的调用链，但没有将
整个上游产品后端移植过来。原 `nutrition-calc.py` 及其旧 CLI 不在独立安装清单中，
新账本不调用它，因此其历史 CLI 缺陷不会阻塞本版；不要将它当作已修好的写入入口。

旧 `scripts/package.sh` / `.claude-plugin/plugin.json` 面向上游流程，**不是本版本的
推荐安装/打包方式**。不要按旧文档安装 NanoRhino 的 ClawHub 包来覆盖本 fork。

官方集成参考（2026-10-13 核对）：
- [OpenClaw skills](https://docs.openclaw.ai/tools/skills)
- [Native media understanding](https://docs.openclaw.ai/nodes/media-understanding)

## License

MIT — see [LICENSE](LICENSE).
