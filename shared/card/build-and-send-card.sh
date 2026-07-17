#!/usr/bin/env bash
# build-and-send-card.sh — 业务侧统一发卡入口(减脂档案卡 / 周报卡)。
#
# 职责分层(见 card-send-refactor):
#   业务(本脚本 + render-card-svg.py,在 weight-loss-skill):
#     - 反查 robotId、从网关进程取密钥
#     - 读 USER.md 解析名字、选模板、填标题 → 生成封面 SVG
#     - 决定文案、pagepath/target
#   系统(backend send-miniprogram-card.mjs):
#     - SVG→PNG(sharp) → S3 → 讯企 msg_type:8 发送
#
# 用法:
#   bash build-and-send-card.sh --account <accountId> --kind <profile|weekly> \
#        [--text "<文案>"] [--robot <robotId>]
#
#   --kind profile : 减脂档案卡(点开进小程序 dashboard 主页)
#   --kind weekly  : 周报卡(点开进小程序周报页, target=weekly)
#   --text         : 随卡片发的文字(profile 建议传当下语境文案; 不传则不发文字)
#   --robot        : 显式 robotId(调试/覆盖; 默认从 agent-registry 反查)
#
# 退出码: 0 成功; 非 0 失败(调用方 agent 据此决定是否回退文字)。
set -euo pipefail

ACC_RAW=""; KIND=""; TEXT_ARG=""; ROBOT_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --account) ACC_RAW="$2"; shift 2 ;;
    --kind)    KIND="$2"; shift 2 ;;
    --text)    TEXT_ARG="$2"; shift 2 ;;
    --robot)   ROBOT_ARG="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$ACC_RAW" || -z "$KIND" ]]; then
  echo "用法: build-and-send-card.sh --account <accountId> --kind <profile|weekly> [--text ..] [--robot ..]" >&2
  exit 2
fi
if [[ "$KIND" != "profile" && "$KIND" != "weekly" ]]; then
  echo "--kind 只能是 profile 或 weekly" >&2
  exit 2
fi

# accountId 归一化小写(registry key 全小写; agent 传入可能混合大小写)
ACC=$(printf '%s' "$ACC_RAW" | tr '[:upper:]' '[:lower:]')

GW=/home/nanorhino/backend-service/.openclaw-gateway
REGISTRY="$GW/extensions/wechat/agent-registry.json"
CARD_DIR="$GW/extensions/wechat/miniprogram-card"          # backend 系统层脚本所在
SEND_MJS="$CARD_DIR/send-miniprogram-card.mjs"
WORKSPACE="$GW/workspace-wechat-dm-$ACC"
RENDER_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/render-card-svg.py"
LOG="$GW/card-send.log"

# 1. robotId: 显式参数优先,否则从 agent-registry 反查
if [[ -n "$ROBOT_ARG" ]]; then
  ROBOT="$ROBOT_ARG"
else
  ROBOT=$(python3 -c "
import json,sys
d=json.load(open('$REGISTRY'))
e=d.get('agents',{}).get('$ACC')
if not e: sys.exit('no-entry')
r=e.get('profile',{}).get('rawEvent',{}).get('robot_id')
print(r or '', end='')
" 2>/dev/null || true)
fi
if [[ -z "$ROBOT" ]]; then
  echo "$(date '+%F %T') [$ACC] ERR: registry 查不到 robot_id" >> "$LOG"
  echo "no-robot" >&2; exit 1
fi

# 2. 从网关进程取讯企/AWS 密钥环境变量
PID=""
for p in $(pgrep -f openclaw 2>/dev/null); do
  if grep -qa "WC_WECHAT_APP_KEY" "/proc/$p/environ" 2>/dev/null; then PID=$p; break; fi
done
if [[ -z "$PID" ]]; then
  echo "$(date '+%F %T') [$ACC] ERR: 找不到带密钥的网关进程" >> "$LOG"
  echo "no-env" >&2; exit 1
fi
while IFS= read -r -d '' kv; do
  case "$kv" in WC_WECHAT_*|AWS_*|WECHAT_MEDIA_*) export "$kv";; esac
done < "/proc/$PID/environ"

# 3. 业务:生成封面 SVG(读 USER.md 解析名字、选模板、填标题)。
#    脚本 stdout 回传 "名字<TAB>语言",供选周报标题的语言。
TMP_SVG="$(mktemp /tmp/card-${ACC}-XXXXXX.svg)"
trap 'rm -f "$TMP_SVG"' EXIT
META=$(python3 "$RENDER_PY" --workspace "$WORKSPACE" --kind "$KIND" --out "$TMP_SVG")
LANG_CODE=$(printf '%s' "$META" | cut -f2)

# 4. 组装 backend 通用发卡参数
MJS_ARGS=(--robot "$ROBOT" --account "$ACC" --cover-svg "$TMP_SVG")
[[ -n "$TEXT_ARG" ]] && MJS_ARGS+=(--text "$TEXT_ARG")
if [[ "$KIND" == "weekly" ]]; then
  # 周报标题按语言(与英文信封封面一致)
  if [[ "$LANG_CODE" == "en" ]]; then
    MJS_ARGS+=(--target weekly --title "Your week, all in here!")
  else
    MJS_ARGS+=(--target weekly --title "一周的努力都在这里！")
  fi
fi

# 5. 调系统层发卡
echo "$(date '+%F %T') [$ACC via $ROBOT] 发 $KIND 卡片..." >> "$LOG"
if node "$SEND_MJS" "${MJS_ARGS[@]}" >> "$LOG" 2>&1; then
  echo "$(date '+%F %T') [$ACC] ✅ $KIND 卡片 OK" >> "$LOG"
  echo "ok"; exit 0
else
  echo "$(date '+%F %T') [$ACC] ❌ $KIND 卡片 FAIL" >> "$LOG"
  echo "send-fail" >&2; exit 1
fi
