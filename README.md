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

## 交给 AI 安装

**让安装 AI 先读 [AI-INSTALL.md](AI-INSTALL.md)。** 根目录 `AGENTS.md` 也指向它。
安装 AI 负责解析真实 workspace、准备 uv/Python、执行安装、自检并检查 OpenClaw 加载。
不要只复制文件就报告成功，也不要把原仓库全部技能一次装入。

可直接发给 AI：

> 请安装 caorong/weight-loss-skill 的 feat/standalone-native-vision 分支。
> 先读取 AI-INSTALL.md，按文档使用 uv 安装到我实际使用的 OpenClaw Agent workspace，
> 完成脚本和加载验收。不要覆盖个人档案、启用旧后台或修改全局安全策略；
> 图片链路未实测时明确标记待验证。

## uv 运行环境

默认使用 **uv >= 0.10.0 + Python 3.13 系列**；兼容底线 Python 3.10。
仍为零第三方依赖，uv 主要统一 Python 选择、环境隔离、锁文件和执行入口。
`pyproject.toml`、`uv.lock`、`.python-version` 放在技能目录，随安装复制；
不会把上游后台/PDF/浏览器依赖安装进来。不发布 PyPI 包，不使用 `uvx`。

macOS/Linux/WSL 和系统时区数据库仍然必需；uv 不会自动解决 OpenClaw 工具权限、
图片传递或容器挂载。首次准备解释器可能需要网络；日常账本命令使用离线模式。
uv 离线不代表照片处理完全本地，模型仍按你自己的配置运行。

供安装 AI 使用的命令（先解析真实 workspace；不要照抄示例路径）：

```bash
git clone --branch feat/standalone-native-vision https://github.com/caorong/weight-loss-skill.git
cd weight-loss-skill

uv run --locked --project diet-tracking-analysis python scripts/install-standalone.py \
  --workspace "/actual/agent/workspace" --timezone Asia/Tokyo --dry-run
uv run --locked --project diet-tracking-analysis python scripts/install-standalone.py \
  --workspace "/actual/agent/workspace" --timezone Asia/Tokyo
```

workspace 必须存在，Python 3.13 须先准备好；详细环境探测见 AI-INSTALL.md。
默认 `--runner uv`，缺 uv 时明确失败；`--runner python` 仅为明确授权的零依赖备用方案。
安装器只携带独立模块、项目清单和自检脚本，不覆盖 USER/PLAN/AGENTS/体重数据，
不创建提醒。已有同名技能须显式 `--replace`，先备份，失败回滚。

安装器在 `<workspace>/skills/diet-tracking-analysis/.venv` 新建独立环境，不复用
临时克隆的 `.venv`。`runtime.json` 保存实际命令前缀和 workspace/备用时区；
USER.md 的时区优先。日常调用使用已安装目录，例如：

```bash
uv run --locked --offline --no-python-downloads --project "/actual/installed/skill" \
  python "/actual/installed/skill/scripts/meal-log.py" summary \
  --workspace "/actual/agent/workspace" --tz Asia/Tokyo
```

不需要激活 venv，不混用 pip，不在记餐时更新依赖。更换机器/容器路径后重新安装。
不要设置 UV_PROJECT_ENVIRONMENT/UV_WORKING_DIR 指向其他项目。维护新增依赖时，
在开发环境使用 `uv add --project diet-tracking-analysis <package>` 并提交两个清单，
不在用户记餐会话里安装任意包。

安装器自动运行七项临时数据冒烟检查，输出可机器读取的 JSON；
`openclaw_discovery` 和 `model_vision` 初始仍为 `not_tested`，由安装 AI 在实际渠道验收。
既有全局旧技能/cron 不会自动被停用，建议专用 Agent，避免旧写入器冲突。
不要为了让技能运行关闭沙箱。

记录/纠正/汇总 JSON 契约见
[diet-tracking-analysis/references/standalone-flow.md](diet-tracking-analysis/references/standalone-flow.md)。

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
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_meal_log.py' -v
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_standalone_install.py' -v
uv run --locked --project diet-tracking-analysis python -m unittest discover -s tests -p 'test_uv_install.py' -v
```

仅运行这三个独立测试集；上游其他测试可能依赖其部署环境，不把它们计作本版通过项。
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
