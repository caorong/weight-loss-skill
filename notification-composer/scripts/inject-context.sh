#!/usr/bin/env bash
# inject-context.sh — Inject a message into the user's main session transcript
# via chat.inject (zero LLM cost, no agent turn triggered).
#
# Usage:
#   bash inject-context.sh --workspace-dir /path/to/workspace --message "提醒内容"
#
# The script resolves the real direct-session key from the agent's sessions.json
# (matching the `:direct:` suffix). Real format carries the channel prefix, e.g.:
#   workspace-wechat-dm-accxxx → agent:wechat-dm-accxxx:wechat:default:direct:accxxx
# (The old bare `agent:<id>:direct:<acc>` guess pointed at a nonexistent session.)

set -euo pipefail

WORKSPACE_DIR=""
MESSAGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir) WORKSPACE_DIR="$2"; shift 2 ;;
    --message)       MESSAGE="$2";       shift 2 ;;
    *)               echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$WORKSPACE_DIR" ]]; then
  echo "ERROR: --workspace-dir is required" >&2
  exit 1
fi

if [[ -z "$MESSAGE" ]]; then
  echo "ERROR: --message is required" >&2
  exit 1
fi

# Derive agentId from workspace dir name
# /home/admin/.openclaw/workspace-wechat-dm-accxxx → wechat-dm-accxxx
BASENAME=$(basename "$WORKSPACE_DIR")
AGENT_ID="${BASENAME#workspace-}"

if [[ "$AGENT_ID" == "$BASENAME" ]]; then
  echo "ERROR: could not derive agentId from workspace dir: $WORKSPACE_DIR" >&2
  exit 1
fi

# Derive accountId from agentId
# wechat-dm-accxxx → accxxx
# wechat-group-groupxxx → groupxxx
ACCOUNT_ID=""
if [[ "$AGENT_ID" == wechat-dm-* ]]; then
  ACCOUNT_ID="${AGENT_ID#wechat-dm-}"
elif [[ "$AGENT_ID" == wechat-group-* ]]; then
  ACCOUNT_ID="${AGENT_ID#wechat-group-}"
else
  echo "ERROR: unsupported agent type: $AGENT_ID" >&2
  exit 1
fi

# 真实的 direct session key 带 channel 前缀 (核实过实际格式):
#   agent:wechat-dm-<acc>:wechat:default:direct:<acc>
# 老写法 agent:<id>:direct:<acc> 会指向不存在的 session。
# 优先从该 agent 的 sessions.json 里按 `:direct:` 后缀查真实 key,查不到再退回
# 一个合理默认 (带 wechat:default 前缀), 最后才退回最老的裸格式。
SESSION_KEY=""
STATE_DIR=$(dirname "$(cd "$WORKSPACE_DIR" && pwd)")
SESSIONS_JSON="${STATE_DIR}/agents/${AGENT_ID}/sessions/sessions.json"
if [[ -f "$SESSIONS_JSON" ]]; then
  SESSION_KEY=$(python3 -c "
import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    sys.exit()
best=None
for k,v in d.items():
    if ':direct:' in k and ':cron:' not in k and isinstance(v,dict):
        u=v.get('updatedAt',0) or 0
        if best is None or u>best[0]: best=(u,k)
print(best[1] if best else '')
" "$SESSIONS_JSON")
fi

if [[ -z "$SESSION_KEY" ]]; then
  # 退回默认: 带 channel 前缀 (匹配当前 wechat 渠道注册格式)
  SESSION_KEY="agent:${AGENT_ID}:wechat:default:direct:${ACCOUNT_ID}"
  echo "WARNING: 未从 sessions.json 解析到 direct key, 使用默认 $SESSION_KEY" >&2
fi

# Escape message for JSON (handle quotes, newlines, backslashes)
JSON_MESSAGE=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$MESSAGE")

PARAMS="{\"sessionKey\":\"${SESSION_KEY}\",\"message\":${JSON_MESSAGE}}"

# Call chat.inject via gateway CLI
RESULT=$(openclaw gateway call chat.inject --params "$PARAMS" --json --timeout 5000 2>/dev/null) || {
  echo "ERROR: chat.inject call failed" >&2
  echo "$RESULT" >&2
  exit 1
}

# Check result
OK=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null || echo "False")

if [[ "$OK" == "True" ]]; then
  MSG_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('messageId', ''))" 2>/dev/null || echo "")
  echo "{\"ok\":true,\"messageId\":\"${MSG_ID}\",\"sessionKey\":\"${SESSION_KEY}\"}"
else
  echo "ERROR: chat.inject returned non-ok" >&2
  echo "$RESULT" >&2
  exit 1
fi
