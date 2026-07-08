#!/usr/bin/env bash
# send-weekly-card.sh <accountId> [robotId]
#
# 周报发送:给指定用户发一张"本周周报"小程序卡片(封面=本周成长报告信封图,点击进小程序周报页)。
# 取代旧的"文字+H5链接"方式 —— 卡片点击后走 loading.html?target=weekly → 小程序内周报页。
#
# robotId 解析:从 agent-registry 反查(profile.rawEvent.robot_id) —— 和生产存储位置统一。
#   registry 由系统维护;测试环境已把各用户 wechat-identity.json 的 robotId 回填到此处。
#   显式第二参数仅作调试/覆盖用。
# 从网关进程取讯企/AWS 密钥环境变量(和 send-card-wrap.sh 同款做法)。
#
# 退出码:0 成功;非 0 失败(调用方 agent 据此决定是否回退到文字链接)。
set -euo pipefail

ACC_RAW="${1:-}"
ROBOT_ARG="${2:-}"
if [ -z "$ACC_RAW" ]; then
  echo "用法: send-weekly-card.sh <accountId> [robotId]" >&2
  exit 2
fi
# accountId 归一化为小写:registry 的 key 是全小写,但 agent 传入的可能是
# 混合大小写(来自 wechat senderId,如 accx5L8ROAg...),不转会查不到 robot_id。
ACC=$(printf '%s' "$ACC_RAW" | tr '[:upper:]' '[:lower:]')

GW=/home/nanorhino/backend-service/.openclaw-gateway
REGISTRY="$GW/extensions/wechat/agent-registry.json"
CARD_DIR="$GW/extensions/wechat/miniprogram-card"
LOG="$GW/weekly-card.log"

# 1. 解析 robotId:显式参数优先,否则从 agent-registry 反查(与生产统一)
if [ -n "$ROBOT_ARG" ]; then
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

if [ -z "$ROBOT" ]; then
  echo "$(date '+%F %T') [$ACC] ERR: registry 查不到 robot_id" >> "$LOG"
  echo "no-robot" >&2
  exit 1
fi

# 2. 从网关进程取密钥环境变量
PID=""
for p in $(pgrep -f openclaw 2>/dev/null); do
  if grep -qa "WC_WECHAT_APP_KEY" "/proc/$p/environ" 2>/dev/null; then PID=$p; break; fi
done
if [ -z "$PID" ]; then
  echo "$(date '+%F %T') [$ACC] ERR: 找不到带密钥的网关进程" >> "$LOG"
  echo "no-env" >&2
  exit 1
fi
while IFS= read -r -d '' kv; do
  case "$kv" in WC_WECHAT_*|AWS_*|WECHAT_MEDIA_*) export "$kv";; esac
done < "/proc/$PID/environ"

# 3. 发卡片(封面=减脂曲线,target=weekly → 点击进小程序周报页)
cd "$CARD_DIR"
echo "$(date '+%F %T') [$ACC via $ROBOT] 发周报卡片..." >> "$LOG"
if node send-card.mjs "$ROBOT" "$ACC" --target=weekly >> "$LOG" 2>&1; then
  echo "$(date '+%F %T') [$ACC] ✅ 周报卡片 OK" >> "$LOG"
  echo "ok"
  exit 0
else
  echo "$(date '+%F %T') [$ACC] ❌ 周报卡片 FAIL" >> "$LOG"
  echo "send-fail" >&2
  exit 1
fi
