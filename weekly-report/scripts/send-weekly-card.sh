#!/usr/bin/env bash
# send-weekly-card.sh <accountId> [robotId]
#
# 周报发卡的瘦转调层(对外接口保持不变,SKILL.md 调用无需改)。
# 实际逻辑已收敛到业务侧统一入口 shared/card/build-and-send-card.sh
# (见 card-send-refactor:系统/业务拆分)。本脚本只做参数转换。
#
# 周报卡:封面=本周成长报告信封图(按用户语言选中/英),点击走
# loading.html?target=weekly → 小程序内周报页。第二参 robotId 仅调试/覆盖用。
#
# 退出码:0 成功;非 0 失败(调用方 agent 据此决定是否回退到文字链接)。
set -euo pipefail

ACC_RAW="${1:-}"
ROBOT_ARG="${2:-}"
if [ -z "$ACC_RAW" ]; then
  echo "用法: send-weekly-card.sh <accountId> [robotId]" >&2
  exit 2
fi

# 定位业务统一入口。本脚本在 weekly-report/scripts/ → 仓库根上两层。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SEND="$SCRIPT_DIR/../../shared/card/build-and-send-card.sh"

ARGS=(--account "$ACC_RAW" --kind weekly)
[ -n "$ROBOT_ARG" ] && ARGS+=(--robot "$ROBOT_ARG")

exec bash "$BUILD_SEND" "${ARGS[@]}"
