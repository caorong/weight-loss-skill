#!/usr/bin/env bash
# send-profile-card.sh <workspaceDir|accountId> ["可选文字"]
#
# 减脂档案发卡的瘦转调层(对外接口保持不变,SKILL.md 调用无需改)。
# 实际逻辑已收敛到业务侧统一入口 shared/card/build-and-send-card.sh
# (见 card-send-refactor:系统/业务拆分)。本脚本只做参数转换。
#
# 减脂档案卡:封面「{Name}的减脂档案」,点击进小程序 dashboard 主页。
# 用于自然语言触发(用户说"看看我的减脂档案/我的数据/进展/曲线")。
#
# 退出码:0 成功;非 0 失败(调用方 agent 据此决定是否回退到文字)。
set -euo pipefail

WORKSPACE_OR_ACCOUNT="${1:-}"
TEXT_ARG="${2:-}"
if [ -z "$WORKSPACE_OR_ACCOUNT" ]; then
  echo "用法: send-profile-card.sh <workspaceDir|accountId> [\"可选文字\"]" >&2
  exit 2
fi

# SKILL.md 传 workspaceDir,脚本从目录名读取 accountId。继续兼容直接传 accountId。
WORKSPACE_BASENAME="$(basename "${WORKSPACE_OR_ACCOUNT%/}")"
case "$WORKSPACE_BASENAME" in
  workspace-wechat-dm-*) ACC_RAW="${WORKSPACE_BASENAME#workspace-wechat-dm-}" ;;
  *)                     ACC_RAW="$WORKSPACE_OR_ACCOUNT" ;;
esac

# 定位业务统一入口(shared/card/ 在 skill 仓库根下)。
# 本脚本在 diet-profile-card/scripts/ → 仓库根上两层。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_SEND="$SCRIPT_DIR/../../shared/card/build-and-send-card.sh"

ARGS=(--account "$ACC_RAW" --kind profile)
[ -n "$TEXT_ARG" ] && ARGS+=(--text "$TEXT_ARG")

exec bash "$BUILD_SEND" "${ARGS[@]}"
