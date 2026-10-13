# AI 安装与验收入口

适用：用户委托你把本 fork 的独立饮食记录技能安装到其 OpenClaw。
先阅读本文件、README、安装器及技能说明，再执行。这里只安装现有视觉模型 + 本地账本，
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

在仓库检出目录执行。使用用户选定的分支/提交（当前轻量版在
`feat/standalone-native-vision`，不是旧 main）。将 workspace 和时区替换为步骤 1 的真实值：

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

安装器已自动做临时工作目录中的新增、重试去重、份量纠正、跨进程读取、删除和汇总测试，
并检查真实 workspace 可写。虚构测试餐次只存在临时目录，不进入用户账本。

接着在 Agent 实际 exec 环境读取已安装的 `runtime.json`，用其中 `command` 数组作为
argv 前缀，追加 `list` / `summary`、`--workspace`、`--tz`，确认能运行。
不要根据安装机器的另一个 Python 路径自行重写命令。无需 `source .venv/bin/activate`。

按本地 OpenClaw 的技能检查/会话能力确认 `diet-tracking-analysis` 已加载且 exec 被授权。
如需刷新会话则按该版本支持的方式操作，不自动重启网关或中断其他 Agent。
安装器不自动禁用旧全局技能/cron；发现冲突先告知，按明确授权处理，不全局清空技能。

文字记账验证不等于视觉验证。若没有用户授权的真实饭照，报告 `vision: pending`；
不要凭脚本自检声明图片识别已经正常。首次实际图片应检查图片可见、营养标为估算、保存
成功后新会话可查询。勿创建新模型密钥或在 Python 中重复调用图片 API。

最终向用户报告：源码版本、目标 Agent/workspace、runner、安装结果、脚本自检结果、
OpenClaw 加载状态和视觉状态；明确哪些已验证、哪些尚待实际环境验证。
不输出健康记录明细或密钥作为“证明”。

## 5. 日常使用

安装后的 `SKILL.md` 和 `references/standalone-flow.md` 是操作契约：
- 明确照片/文字打卡 -> 原生模型估算 -> JSON -> meal-log.py 保存 -> 根据真实回执回复。
- 纠正先查 ID，只改指定项；份量变化用 scale，不重算其他菜。
- 重试复用 request_id 和输入；有记录日平均不等于实际全部摄入平均。
- 不激活原来的 meal_checkin、强制卡片、后台周报、自动提醒或运营召回。
- 环境坏了就修安装，不临时 pip、换解释器、移除锁定参数或手改账本。

官方参考：
- https://docs.astral.sh/uv/getting-started/installation/
- https://docs.astral.sh/uv/concepts/projects/sync/
- https://docs.openclaw.ai/tools/skills
