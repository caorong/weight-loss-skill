#!/usr/bin/env bash
# send-profile-card.sh <accountId> ["可选文字"]
#
# 减脂档案发卡:给指定用户发一张"减脂档案"小程序卡片(固定底图 cover-base.svg +
# 「{Name}的减脂档案」标题,点击进小程序 dashboard)。用于自然语言触发场景
# (用户说"看看我的减脂档案/我的数据/进展/曲线"),由 diet-profile-card skill 调用。
#
# 与周报发卡(send-weekly-card.sh)同构:
#   - robotId 从 agent-registry 反查(profile.rawEvent.robot_id)
#   - 讯企/AWS 密钥从网关进程 environ 取
#   - 底层调 send-fixed-card.mjs(封面=固定档案图,不是周报信封,也不是曲线)
#
# 第二参数"可选文字":skill 传入的一句当下语境短文案。不传则 mjs 用它内置的
#   D7 默认文案(不适合自然语言场景,故 skill 应总是显式传一句)。
#
# 退出码:0 成功;非 0 失败(调用方 agent 据此决定是否回退到文字)。
set -euo pipefail

ACC_RAW="${1:-}"
TEXT_ARG="${2:-}"
if [ -z "$ACC_RAW" ]; then
  echo "用法: send-profile-card.sh <accountId> [\"可选文字\"]" >&2
  exit 2
fi
# accountId 归一化小写:registry key 全小写,agent 传入可能混合大小写。
ACC=$(printf '%s' "$ACC_RAW" | tr '[:upper:]' '[:lower:]')

GW=/home/nanorhino/backend-service/.openclaw-gateway
REGISTRY="$GW/extensions/wechat/agent-registry.json"
CARD_DIR="$GW/extensions/wechat/miniprogram-card"
LOG="$GW/profile-card.log"

# 1. 解析 robotId:从 agent-registry 反查(与周报/生产统一)
ROBOT=$(python3 -c "
import json,sys
d=json.load(open('$REGISTRY'))
e=d.get('agents',{}).get('$ACC')
if not e: sys.exit('no-entry')
r=e.get('profile',{}).get('rawEvent',{}).get('robot_id')
print(r or '', end='')
" 2>/dev/null || true)

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

# 3. 发卡片(固定底图减脂档案卡)
cd "$CARD_DIR"
echo "$(date '+%F %T') [$ACC via $ROBOT] 发减脂档案卡片..." >> "$LOG"
if node send-fixed-card.mjs "$ROBOT" "$ACC" "$TEXT_ARG" >> "$LOG" 2>&1; then
  echo "$(date '+%F %T') [$ACC] ✅ 减脂档案卡片 OK" >> "$LOG"
  echo "ok"
  exit 0
else
  echo "$(date '+%F %T') [$ACC] ❌ 减脂档案卡片 FAIL" >> "$LOG"
  echo "send-fail" >&2
  exit 1
fi
