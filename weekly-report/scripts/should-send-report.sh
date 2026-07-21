#!/bin/bash
set -euo pipefail

# should-send-report.sh — Quick gate check for weekly report eligibility.
# Exit 0 + prints "yes" if report should be sent.
# Exit 0 + prints "no: <reason>" if not.
#
# Usage: bash should-send-report.sh --workspace-dir <path> [--min-days 2]

WORKSPACE=""
MIN_DAYS=2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir) WORKSPACE="$2"; shift 2 ;;
    --min-days) MIN_DAYS="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "$WORKSPACE" ]]; then
  echo "no: missing --workspace-dir"
  exit 0
fi

# 1. health-profile must exist
if [[ ! -f "$WORKSPACE/health-profile.md" ]]; then
  echo "no: no health-profile.md"
  exit 0
fi

# 2. Check lifecycle stage from lifecycle API (single source of truth).
#    Stage >= 3 (recall/inactive) → no weekly report. Fail-open to stage 1 on error.
#    account_id derived from workspace dir name: workspace-wechat-dm-<acc> → <acc>
# 2026-07-01 重命名过渡:新名 DATA_API_URL 优先,老名 LIFECYCLE_API_URL 兼容
LIFECYCLE_API="${DATA_API_URL:-${LIFECYCLE_API_URL:-http://127.0.0.1:3100}}"
ACC=$(basename "$WORKSPACE" | sed -E 's/^workspace-//; s/^(wechat|wecom)-dm-//')
STAGE=$(curl -s --max-time 3 "$LIFECYCLE_API/v1/lifecycle/state/$ACC" 2>/dev/null \
  | python3.11 -c "import sys,json; print(json.load(sys.stdin).get('stage',1))" 2>/dev/null || echo "1")
if [[ "$STAGE" -ge 3 ]]; then
  echo "no: stage=$STAGE (recall/inactive)"
  exit 0
fi

# 3. count days with food data this week (Mon-Sun)
MEALS_DIR="$WORKSPACE/data/meals"
if [[ ! -d "$MEALS_DIR" ]]; then
  echo "no: no meals directory"
  exit 0
fi

# 报告范围 = 本周(current_week) Monday-Sunday,与 SKILL.md Step1 的 current_week 一致。
# 统一用本周:周日 cron 触发时本周刚好完整;平时手动触发看本周进行中的数据。
# (之前 Mon-Sat 用"上一完整周"会和 Step1 的 current_week 打架,导致 agent 有时报本周有时报上周。)
DOW=$(date +%u)  # 1=Mon, 7=Sun
DAYS_SINCE_MON=$((DOW - 1))
MONDAY=$(date -d "$DAYS_SINCE_MON days ago" +%Y-%m-%d)
SUNDAY=$(date -d "$((6 - DAYS_SINCE_MON)) days" +%Y-%m-%d)

DAYS_WITH_DATA=0
for f in "$MEALS_DIR"/*.json; do
  [[ -f "$f" ]] || continue
  FNAME=$(basename "$f" .json)
  # Extract date from filename (format: YYYY-MM-DD or YYYY-MM-DD-meal)
  FDATE="${FNAME:0:10}"
  if [[ "$FDATE" > "$MONDAY" || "$FDATE" == "$MONDAY" ]] && [[ "$FDATE" < "$SUNDAY" || "$FDATE" == "$SUNDAY" ]]; then
    # Check if file has actual food data (not empty/skeleton)
    HAS_DATA=$(python3.11 -c "
import json
d = json.load(open('$f'))
if isinstance(d, list):
    has = any(m.get('items') or m.get('foods') for m in d if isinstance(m, dict))
elif isinstance(d, dict):
    has = bool(d.get('items') or d.get('foods'))
else:
    has = False
print('yes' if has else 'no')
" 2>/dev/null || echo "no")
    if [[ "$HAS_DATA" == "yes" ]]; then
      DAYS_WITH_DATA=$((DAYS_WITH_DATA + 1))
    fi
  fi
done

if [[ "$DAYS_WITH_DATA" -le "$MIN_DAYS" ]]; then
  echo "no: only $DAYS_WITH_DATA days with data (need more than $MIN_DAYS)"
  exit 0
fi

echo "yes"
