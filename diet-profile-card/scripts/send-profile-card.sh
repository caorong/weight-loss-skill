#!/usr/bin/env bash
# send-profile-card.sh <accountId> ["可选文字"]
#
# 减脂档案发卡的瘦转调层(对外接口保持不变,SKILL.md 调用无需改)。
# 实际逻辑已收敛到业务侧统一入口 shared/card/build-and-send-card.sh
# (见 card-send-refactor:系统/业务拆分)。本脚本只做参数转换。
#
# 减脂档案卡:封面「{Name}的减脂档案」,点击进小程序 dashboard 主页。
# 用于自然语言触发(用户说"看看我的减脂档案/我的数据/进展/曲线")+ 每日数据 cron。
#
# 退出码:0 成功;非 0 失败(调用方 agent 据此决定是否回退到文字)。
set -euo pipefail

ACC_RAW="${1:-}"
TEXT_ARG="${2:-}"
if [ -z "$ACC_RAW" ]; then
  echo "用法: send-profile-card.sh <accountId> [\"可选文字\"]" >&2
  exit 2
fi

# 定位业务统一入口(shared/card/ 在 skill 仓库根下)。
# 本脚本在 diet-profile-card/scripts/ → 仓库根上两层。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SEND="$SCRIPT_DIR/../../shared/card/build-and-send-card.sh"

ARGS=(--account "$ACC_RAW" --kind profile)
[ -n "$TEXT_ARG" ] && ARGS+=(--text "$TEXT_ARG")

exec bash "$BUILD_SEND" "${ARGS[@]}"
