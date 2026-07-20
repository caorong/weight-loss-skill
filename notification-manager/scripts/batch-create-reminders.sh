#!/usr/bin/env bash
set -euo pipefail

# batch-create-reminders.sh — Create all reminder cron jobs in parallel.
#
# ⭐ Detach-first execution model:
#   When called without OPENCLAW_BATCH_DETACHED=1, the script re-execs itself
#   under setsid+nohup in a new process group with stdout/stderr redirected to
#   /tmp/batch-create-reminders/<agent>-<epoch>.log, then exits 0 immediately.
#   The parent (agent's exec tool session) can be killed by tool timeout — the
#   detached child survives because it's in its own session/process group.
#
#   This fixes the case where the agent's exec tool has a 30s timeout and the
#   full batch (Phase 1 slot allocation + Phase 2 parallel cron.add's) takes
#   45-90s. Previously the tool timeout would SIGTERM the shell, cascade-kill
#   the 8 background `openclaw cron add` children, and leave 6/8 cron jobs
#   never created.
#
# Two-phase execution (in detached child):
#   Phase 1 (serial)   — collect job definitions, then call find-slot.py
#                        --batch-file once to allocate all time slots in a
#                        single cron list fetch. This prevents race conditions
#                        from concurrent slot lookups.
#   Phase 2 (parallel) — spawn all `create-reminder.sh --exact` calls in
#                        background and wait for all to finish.
#
# Usage:
#   bash batch-create-reminders.sh --agent <id> --channel <ch> --workspace <path> [options]
#
# Required:
#   --agent <agent-id>       Agent ID (e.g. wechat-dm-xxx)
#   --channel <channel>      Channel (e.g. wechat, slack, wecom)
#   --workspace <path>       Path to user workspace (contains USER.md, health-profile.md)
#
# Optional:
#   --dry-run                Print commands without executing (skips slot allocation)
#   --skip-existing          Skip reminders that already exist (checked before queuing)
#   --only <type>            Only create specific type: meal, weight, report,
#                            pattern, tips, firstmeal, all (default: all).
#                            Comma-separated for multiple.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw-gateway}"
CREATE_REMINDER="$SCRIPT_DIR/create-reminder.sh"
FIND_SLOT="$SCRIPT_DIR/find-slot.py"
NOTIFICATION_COMPOSER_BASE="$(cd "$SCRIPT_DIR/../../notification-composer" && pwd)"

AGENT=""
CHANNEL=""
WORKSPACE=""
DRY_RUN=false
SKIP_EXISTING=false
ONLY_TYPE="all"

while [[ $# -gt 0 ]]; do
  case $1 in
    --agent)     AGENT="$2"; shift 2 ;;
    --channel)   CHANNEL="$2"; shift 2 ;;
    --workspace) WORKSPACE="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=true; shift ;;
    --skip-existing) SKIP_EXISTING=true; shift ;;
    --only)      ONLY_TYPE="$2"; shift 2 ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$AGENT" ]];     then echo "ERROR: --agent is required" >&2;     exit 1; fi
if [[ -z "$CHANNEL" ]];   then echo "ERROR: --channel is required" >&2;   exit 1; fi
if [[ -z "$WORKSPACE" ]]; then echo "ERROR: --workspace is required" >&2; exit 1; fi
if [[ ! -d "$WORKSPACE" ]]; then
  echo "ERROR: Workspace directory does not exist: $WORKSPACE" >&2; exit 1
fi

HEALTH_PROFILE="$WORKSPACE/health-profile.md"
USER_MD="$WORKSPACE/USER.md"

# Ensure engagement.json exists (非 stage 业务字段容器;stage 已迁 lifecycle DB,
# 不再写 notification_stage/stage_changed_at — 见 PHASE-1.5-DESIGN.md)
ENGAGEMENT_PATH="$WORKSPACE/data/engagement.json"
if [[ ! -f "$ENGAGEMENT_PATH" ]]; then
  mkdir -p "$(dirname "$ENGAGEMENT_PATH")"
  cat > "$ENGAGEMENT_PATH" << 'ENGAGEMENT_EOF'
{
  "last_recall_date": null,
  "recall_2_sent": false,
  "reminder_config": {}
}
ENGAGEMENT_EOF
  echo "Created engagement.json"
fi

if [[ ! -f "$HEALTH_PROFILE" ]]; then
  echo "ERROR: health-profile.md not found: $HEALTH_PROFILE" >&2; exit 1
fi

# ⭐ Detach guard: re-exec self via setsid+nohup so we survive parent shell death.
# Placed AFTER validation errors so caller learns about invalid args synchronously.
# Dry-run stays foreground (agent needs to see the plan).
if [[ "${OPENCLAW_BATCH_DETACHED:-}" != "1" && "$DRY_RUN" == "false" ]]; then
  LOG_DIR="/tmp/batch-create-reminders"
  mkdir -p "$LOG_DIR"
  LOG_FILE="$LOG_DIR/${AGENT}-$(date +%s).log"
  export OPENCLAW_BATCH_DETACHED=1
  setsid nohup bash "$0" \
    --agent "$AGENT" \
    --channel "$CHANNEL" \
    --workspace "$WORKSPACE" \
    $([[ "$SKIP_EXISTING" == "true" ]] && echo "--skip-existing") \
    $([[ "$ONLY_TYPE" != "all" ]] && echo "--only $ONLY_TYPE") \
    >"$LOG_FILE" 2>&1 </dev/null &
  CHILD_PID=$!
  disown "$CHILD_PID" 2>/dev/null || true
  echo "batch-create-reminders detached: pid=$CHILD_PID log=$LOG_FILE"
  echo "tail -f '$LOG_FILE' to watch progress"
  exit 0
fi

# --- Helpers ---

read_usermd() {
  [[ ! -f "$USER_MD" ]] && return
  grep "^- \*\*${1}:\*\*" "$USER_MD" 2>/dev/null \
    | sed -E 's/^- \*\*[^*]*\*\*[[:space:]]*//' | xargs || true
}

get_timezone() {
  local tz
  tz=$(read_usermd "Timezone")
  echo "${tz:-Asia/Shanghai}"
}

get_meal_schedule() {
  local in_section=false
  # 兼容 onboarding 写的两种 Meal Schedule 格式:
  #   A) 同行加粗:  - **Breakfast:** 08:00
  #   B) Schedule 子项(可缩进):  - **Schedule:** 下的  - Lunch: 12:00 / - Dinner: 19:00
  # 只认真实餐次名(Breakfast/Lunch/Brunch/Dinner/Supper);Meals per Day/Schedule/
  # Snack/Note 等非"正餐时间"行一律跳过(避免"下午加餐(骑车前)"这类描述当时间解析失败)。
  # 时间值交给 extract_time 抽首个 HH:MM(能容忍 ~8:00 / 11:00–12:00 区间 / 带注释)。
  local meal_re='^(Breakfast|Lunch|Brunch|Dinner|Supper)$'
  while IFS= read -r line; do
    [[ "$line" == "## Meal Schedule" ]] && in_section=true && continue
    if [[ "$in_section" == true ]]; then
      [[ "$line" =~ ^##\  ]] && break
      local meal="" time=""
      if [[ "$line" =~ ^-\ \*\*([^*]+):\*\*\ (.+)$ ]]; then           # A) - **Meal:** time
        meal="${BASH_REMATCH[1]}"; time="${BASH_REMATCH[2]}"
      elif [[ "$line" =~ ^[[:space:]]*-\ ([A-Za-z]+):\ *(.+)$ ]]; then  # B) 缩进子项  - Meal: time
        meal="${BASH_REMATCH[1]}"; time="${BASH_REMATCH[2]}"
      fi
      if [[ -n "$meal" && "$meal" =~ $meal_re ]]; then
        local t; t=$(extract_time "$time")
        [[ -n "$t" ]] && echo "$meal $t"
      fi
    fi
  done < "$HEALTH_PROFILE"
}

get_automation_field() {
  local in_section=false
  while IFS= read -r line; do
    [[ "$line" == "## Automation" ]] && in_section=true && continue
    if [[ "$in_section" == true ]]; then
      [[ "$line" =~ ^##\  ]] && break
      [[ "$line" =~ ^-\ \*\*${1}:\*\*\ (.+)$ ]] && echo "${BASH_REMATCH[1]}" && return
    fi
  done < "$HEALTH_PROFILE"
  echo "—"
}


# Extract first HH:MM from text (handles "9:00", "18:00（注释）", "07:00-08:00", etc.)
extract_time() {
  local raw="$1"
  if [[ "$raw" =~ ([0-9]{1,2}):([0-9]{2}) ]]; then
    printf "%02d:%s" "$((10#${BASH_REMATCH[1]}))" "${BASH_REMATCH[2]}"
  else
    echo ""
  fi
}

# Output: "MM HH" for cron
calc_cron_time() {
  local time_str="$1" offset_min="$2"
  [[ ! "$time_str" =~ ^([0-9]{2}):([0-9]{2})$ ]] && echo "ERROR: bad time: $time_str" >&2 && return 1
  local hour=$((10#${BASH_REMATCH[1]})) minute=$((10#${BASH_REMATCH[2]}))
  local total=$(( hour * 60 + minute + offset_min ))
  (( total < 0 ))    && total=$(( total + 1440 ))
  (( total >= 1440 )) && total=$(( total - 1440 ))
  echo "$(( total % 60 )) $(( total / 60 ))"
}

get_existing_cron_names() {
  command -v openclaw &>/dev/null || return
  (cd "$STATE_DIR" && openclaw cron list --json --agent "$AGENT") 2>/dev/null \
    | python3 -c "
import sys, json
try:
    for j in json.load(sys.stdin).get('jobs', []):
        print(j.get('name',''))
except: pass
" || true
}

should_create_type() {
  [[ "$ONLY_TYPE" == "all" ]] && return 0
  [[ ",$ONLY_TYPE," == *",$1,"* ]] && return 0
  return 1
}

# --- Phase 1: Collect job definitions ---

TIMEZONE=$(get_timezone)
TZ_OFFSET=$(TZ_NAME="$TIMEZONE" python3 -c \
  'import os; from datetime import datetime; from zoneinfo import ZoneInfo; print(int(datetime.now(ZoneInfo(os.environ["TZ_NAME"])).utcoffset().total_seconds()))')
echo "Timezone: $TIMEZONE"

EXISTING_JOBS=""
if [[ "$SKIP_EXISTING" == true ]]; then
  EXISTING_JOBS=$(get_existing_cron_names)
fi

MEAL_SCHEDULE=$(get_meal_schedule)
if [[ -z "$MEAL_SCHEDULE" ]]; then
  echo "WARNING: No meal times in health-profile.md > Meal Schedule; skipping meal reminders" >&2
else
  echo "Meal schedule:"
  echo "$MEAL_SCHEDULE"
  echo ""
fi

# Parallel arrays for queued jobs
QUEUED_NAMES=()
QUEUED_MESSAGES=()
QUEUED_BASE_CRONS=()
QUEUED_TYPES=()

# TSV file used as input to find-slot.py --batch-file
TMPJOBDIR=$(mktemp -d)
trap "rm -rf $TMPJOBDIR" EXIT
JOBS_TSV="$TMPJOBDIR/jobs.tsv"  # name<TAB>cron<TAB>type

queue_job() {
  local name="$1" message="$2" cron="$3" type="${4:-other}"

  if [[ "$SKIP_EXISTING" == true ]] && echo "$EXISTING_JOBS" | grep -qxF "$name"; then
    echo "Skipping (exists): $name"
    return
  fi

  QUEUED_NAMES+=("$name")
  QUEUED_MESSAGES+=("$message")
  QUEUED_BASE_CRONS+=("$cron")
  QUEUED_TYPES+=("$type")
  printf '%s\t%s\t%s\n' "$name" "$cron" "$type" >> "$JOBS_TSV"
}

BREAKFAST_TIME=""
DINNER_TIME=""
while IFS= read -r meal_line; do
  [[ "$meal_line" =~ ^Breakfast\ (.+)$ ]] && BREAKFAST_TIME=$(extract_time "${BASH_REMATCH[1]}")
  [[ "$meal_line" =~ ^Dinner\ (.+)$ ]]    && DINNER_TIME=$(extract_time "${BASH_REMATCH[1]}")
done <<< "$MEAL_SCHEDULE"

# 1. Meal reminders
if should_create_type "meal"; then
  while IFS= read -r meal_line; do
    [[ -z "$meal_line" ]] && continue
    meal_name=$(echo "$meal_line" | awk '{print $1}')
    raw_time=$(echo "$meal_line" | cut -d' ' -f2-)
    meal_time=$(extract_time "$raw_time")
    [[ -z "$meal_time" ]] && echo "SKIP: $meal_name — no parseable time in '$raw_time'" && continue
    ct=$(calc_cron_time "$meal_time" -15)
    cm=$(echo "$ct" | awk '{print $1}')
    ch=$(echo "$ct" | awk '{print $2}')
    jname="$(tr '[:lower:]' '[:upper:]' <<< "${meal_name:0:1}")${meal_name:1} reminder"
    msg="Run notification-composer for $(tr '[:upper:]' '[:lower:]' <<< "$meal_name")."
    queue_job "$jname" "$msg" "$cm $ch * * *" meal
  done <<< "$MEAL_SCHEDULE"
fi

# 2. Weight reminders
EARLIEST_TIME="${BREAKFAST_TIME:-}"
if [[ -z "$EARLIEST_TIME" && -n "$MEAL_SCHEDULE" ]]; then
  EARLIEST_TIME=$(echo "$MEAL_SCHEDULE" | awk 'NR==1{print $2}')
fi

if should_create_type "weight" && [[ -n "$EARLIEST_TIME" ]]; then
  ct=$(calc_cron_time "$EARLIEST_TIME" -30)
  cm=$(echo "$ct" | awk '{print $1}'); ch=$(echo "$ct" | awk '{print $2}')
  queue_job "Weight check-in reminder"  "Run notification-composer for weight."                  "$cm $ch * * 3,6" weight

  ct=$(calc_cron_time "$EARLIEST_TIME" -30)
  cm=$(echo "$ct" | awk '{print $1}'); ch=$(echo "$ct" | awk '{print $2}')
  queue_job "Weight morning followup"   "Run notification-composer for weight_morning_followup." "$cm $ch * * 4,0" weight
fi

# 3. Weekly report
if should_create_type "report"; then
  queue_job "Weekly report" "Run weekly-report to generate this week's progress report." "0 21 * * 0" other
fi

# 5. Diet pattern detection
if should_create_type "pattern" && [[ -n "$DINNER_TIME" ]]; then
  pattern_completed=$(get_automation_field "Pattern Detection Completed")
  if [[ "$pattern_completed" == "—" ]]; then
    ct=$(calc_cron_time "$DINNER_TIME" 180)
    cm=$(echo "$ct" | awk '{print $1}'); ch=$(echo "$ct" | awk '{print $2}')
    queue_job "Diet pattern detection" "Run diet-pattern-detection skill." "$cm $ch * * *" other
  fi
fi

# 6. Product tips (daily, prefer 21:00 — slot allocator handles conflicts)
if should_create_type "tips"; then
  queue_job "Product tips" "Run notification-composer for tips." "0 21 * * *" tips
fi

# 8. First-meal nudge (one-shot, activation flow).
# Created at onboarding completion for users who have a populated Meal Schedule
# but have never logged a meal. Two one-shot jobs:
#   - Day 1: fires ~3-4h after completion at the NEXT meal slot today (if a
#            slot remains today and is in the daytime window), else the first
#            meal slot tomorrow.
#   - Day 2: fires at the first meal slot the following day (softer follow-up).
# Each is a one-shot (--at) that auto-deletes after running. Both route through
# pre-send-check.py --meal-type first_meal_nudge, which self-cancels the moment
# any meal is logged or the cap is reached — so they are safe to over-schedule.
# These are scheduled directly via create-reminder.sh (NOT the queue/slot
# pipeline, which is for recurring crons); one-shots skip anti-burst anyway.
# Timing is deliberately offset from meal-reminder minutes (meal reminders fire
# at slot-15min; the nudge fires AT the meal slot) so the user never gets the
# nudge and a meal reminder minutes apart.
if should_create_type "firstmeal" && [[ -n "$MEAL_SCHEDULE" ]]; then
  # Compute the two one-shot ISO timestamps in the user's timezone.
  # Output: two lines, "DAY1 <iso>" and "DAY2 <iso>" (or nothing if skipped).
  FIRST_MEAL_PLAN=$(MEAL_SCHEDULE="$MEAL_SCHEDULE" TIMEZONE="$TIMEZONE" python3 - <<'PYEOF'
import os, sys
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

tz_name = os.environ.get("TIMEZONE", "Asia/Shanghai")
tz = None
if ZoneInfo:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None

now = datetime.now(tz) if tz else datetime.now()

# Parse meal times from "Name <time>" lines, sorted ascending.
# Mirror the shell extract_time(): take the FIRST HH:MM in the value, so
# annotated times like "18:00（注释）" or "07:00-08:00" parse correctly
# (the value is everything after the meal name, which may contain spaces).
import re as _re
slots = []
for line in os.environ.get("MEAL_SCHEDULE", "").splitlines():
    line = line.strip()
    if not line:
        continue
    parts = line.split(None, 1)
    if len(parts) < 2:
        continue
    m = _re.search(r"([0-9]{1,2}):([0-9]{2})", parts[1])
    if not m:
        continue
    hh, mm = int(m.group(1)), int(m.group(2))
    if 0 <= hh < 24 and 0 <= mm < 60:
        slots.append((hh, mm))
if not slots:
    sys.exit(0)
slots.sort()

# Daytime cap: never fire before 08:00 or after 20:00 local.
DAY_START, DAY_END = 8, 20
MIN_LEAD_HOURS = 3  # fire at least ~3h after onboarding completion

def slot_dt(day, hh, mm):
    return day.replace(hour=hh, minute=mm, second=0, microsecond=0)

def clamp_daytime(dt):
    """If dt falls outside the daytime window, push it to next day's DAY_START."""
    if dt.hour < DAY_START:
        return dt.replace(hour=DAY_START, minute=0, second=0, microsecond=0)
    if dt.hour >= DAY_END:
        nxt = dt + timedelta(days=1)
        return nxt.replace(hour=DAY_START, minute=0, second=0, microsecond=0)
    return dt

# Day 1: next meal slot today that is >= now + MIN_LEAD_HOURS and within
# daytime; else first slot tomorrow.
earliest = now + timedelta(hours=MIN_LEAD_HOURS)
day1 = None
for hh, mm in slots:
    cand = slot_dt(now, hh, mm)
    if cand >= earliest and DAY_START <= hh < DAY_END:
        day1 = cand
        break
if day1 is None:
    tomorrow = now + timedelta(days=1)
    hh, mm = slots[0]
    day1 = clamp_daytime(slot_dt(tomorrow, hh, mm))

# Day 2: first meal slot the day AFTER day1.
day2_base = day1 + timedelta(days=1)
hh, mm = slots[0]
day2 = clamp_daytime(slot_dt(day2_base, hh, mm))

def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z") if dt.tzinfo else dt.strftime("%Y-%m-%dT%H:%M:%S")

print(f"DAY1 {iso(day1)}")
print(f"DAY2 {iso(day2)}")
PYEOF
)
  FM_DAY1=$(echo "$FIRST_MEAL_PLAN" | awk '/^DAY1/{print $2}')
  FM_DAY2=$(echo "$FIRST_MEAL_PLAN" | awk '/^DAY2/{print $2}')

  # Cron payload: task instruction only (per CONVENTIONS.md §11 — no
  # self-delivery wrapper). nudge=1 / nudge=2 tells the composer which copy
  # to use (day-1 vs softer follow-up).
  FM_MSG_1="First run: python3 $NOTIFICATION_COMPOSER_BASE/scripts/pre-send-check.py --workspace-dir $WORKSPACE --meal-type first_meal_nudge --tz-offset $TZ_OFFSET. If output is NO_REPLY, stop and output NO_REPLY. Otherwise run notification-composer for first_meal_nudge (nudge=1)."
  FM_MSG_2="First run: python3 $NOTIFICATION_COMPOSER_BASE/scripts/pre-send-check.py --workspace-dir $WORKSPACE --meal-type first_meal_nudge --tz-offset $TZ_OFFSET. If output is NO_REPLY, stop and output NO_REPLY. Otherwise run notification-composer for first_meal_nudge (nudge=2)."

  if [[ -n "$FM_DAY1" ]]; then
    if [[ "$DRY_RUN" == true ]]; then
      echo "[DRY-RUN] first-meal nudge 1 --at $FM_DAY1"
      echo "[DRY-RUN] first-meal nudge 2 --at $FM_DAY2"
    else
      if ! echo "$EXISTING_JOBS" | grep -qxF "First meal nudge"; then
        bash "$CREATE_REMINDER" --agent "$AGENT" --channel "$CHANNEL" \
          --name "First meal nudge" --message "$FM_MSG_1" --at "$FM_DAY1" || \
          echo "WARNING: failed to create first-meal nudge 1" >&2
      fi
      if [[ -n "$FM_DAY2" ]] && ! echo "$EXISTING_JOBS" | grep -qxF "First meal nudge followup"; then
        bash "$CREATE_REMINDER" --agent "$AGENT" --channel "$CHANNEL" \
          --name "First meal nudge followup" --message "$FM_MSG_2" --at "$FM_DAY2" || \
          echo "WARNING: failed to create first-meal nudge followup" >&2
      fi
    fi
  fi
fi


TOTAL=${#QUEUED_NAMES[@]}
if [[ $TOTAL -eq 0 ]]; then
  echo "No jobs to create."
  exit 0
fi

echo ""
echo "$TOTAL job(s) queued. Running slot allocation..."

# --- Phase 2: Batch slot allocation (serial, one cron list fetch) ---

ADJUSTED_CRONS=()

if [[ "$DRY_RUN" == true ]]; then
  # In dry-run, skip the live cron list fetch — use base crons as-is
  ADJUSTED_CRONS=("${QUEUED_BASE_CRONS[@]}")
else
  # Build JSON for find-slot.py --batch-file
  BATCH_INPUT="$TMPJOBDIR/batch_input.json"
  python3 - "$BATCH_INPUT" "$TIMEZONE" "$JOBS_TSV" <<'PYEOF'
import json, sys

out_file, tz, tsv_file = sys.argv[1], sys.argv[2], sys.argv[3]
jobs = []
with open(tsv_file) as f:
    for line in f:
        line = line.rstrip('\n')
        if not line:
            continue
        parts = line.split('\t')
        name, cron, jtype = parts[0], parts[1], parts[2]
        jobs.append({"name": name, "cron": cron, "type": jtype, "tz": tz})
with open(out_file, 'w') as f:
    json.dump(jobs, f)
PYEOF

  BATCH_OUTPUT="$TMPJOBDIR/batch_output.json"
  python3 "$FIND_SLOT" --batch-file "$BATCH_INPUT" > "$BATCH_OUTPUT"

  # Extract adjusted crons in order (same order as QUEUED_NAMES)
  mapfile -t ADJUSTED_CRONS < <(python3 - "$BATCH_OUTPUT" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    for r in json.load(f):
        print(r["adjusted_cron"])
PYEOF
  )
fi

echo "Slot allocation complete. Creating $TOTAL job(s) in parallel..."
echo ""

# --- Phase 3: Parallel job creation (with sequential retry for transient failures) ---
#
# Parallel mode opens N concurrent WebSocket handshakes to the gateway.  Under
# load this occasionally drops one with "gateway closed (1000 normal closure)"
# even though every individual handshake works fine in isolation.  Past
# incident (2026-06-27 user 虹晨曦): 1 of 10 parallel creates failed with
# exactly this signature, leaving Lunch reminder missing for a real user.
#
# Fix: pair each background job with the index that spawned it (PID→IDX map)
# so a wait failure tells us *which* reminder failed, then retry those
# specific names sequentially with brief backoff.  Sequential retry avoids the
# concurrent-handshake pressure that triggered the original failure.

PIDS=()
PID_TO_IDX=()
FAILED_IDXS=()

for idx in "${!QUEUED_NAMES[@]}"; do
  name="${QUEUED_NAMES[$idx]}"
  message="${QUEUED_MESSAGES[$idx]}"
  adjusted="${ADJUSTED_CRONS[$idx]}"

  if [[ "$DRY_RUN" == true ]]; then
    echo "[DRY-RUN] bash create-reminder.sh --agent \"$AGENT\" --channel \"$CHANNEL\" --exact --name \"$name\" --message \"$message\" --cron \"$adjusted\""
    continue
  fi

  (
    bash "$CREATE_REMINDER" \
      --agent   "$AGENT" \
      --channel "$CHANNEL" \
      --exact \
      --name    "$name" \
      --message "$message" \
      --cron    "$adjusted"
  ) &
  PIDS+=($!)
  PID_TO_IDX+=("$idx")
done

if [[ "$DRY_RUN" == false ]]; then
  for i in "${!PIDS[@]}"; do
    pid="${PIDS[$i]}"
    if ! wait "$pid"; then
      FAILED_IDXS+=("${PID_TO_IDX[$i]}")
    fi
  done
fi

# Retry failed jobs sequentially (parallel pressure was the suspected cause).
# 2 attempts with 1s backoff — single-job WS handshake under no concurrency
# pressure recovers quickly when it recovers at all.
RETRY_FAIL_COUNT=0
if [[ "$DRY_RUN" == false && ${#FAILED_IDXS[@]} -gt 0 ]]; then
  echo ""
  echo "Retrying ${#FAILED_IDXS[@]} failed job(s) sequentially..."
  for idx in "${FAILED_IDXS[@]}"; do
    name="${QUEUED_NAMES[$idx]}"
    message="${QUEUED_MESSAGES[$idx]}"
    adjusted="${ADJUSTED_CRONS[$idx]}"
    success=false
    for attempt in 1 2; do
      echo "  retry $attempt/2: $name"
      if bash "$CREATE_REMINDER" \
          --agent   "$AGENT" \
          --channel "$CHANNEL" \
          --exact \
          --name    "$name" \
          --message "$message" \
          --cron    "$adjusted"; then
        success=true; break
      fi
      [[ $attempt -lt 2 ]] && sleep 1
    done
    if [[ "$success" == false ]]; then
      RETRY_FAIL_COUNT=$((RETRY_FAIL_COUNT + 1))
      echo "  ✗ STILL FAILED: $name" >&2
    fi
  done
fi

FAIL_COUNT=$RETRY_FAIL_COUNT

echo ""
echo "====================================="
if [[ "$DRY_RUN" == true ]]; then
  echo "DRY-RUN: $TOTAL job(s) would be created"
else
  CREATED=$(( TOTAL - FAIL_COUNT ))
  echo "Created $CREATED/$TOTAL job(s)${FAIL_COUNT:+ ($FAIL_COUNT failed after retries)}"
  if [[ $FAIL_COUNT -gt 0 ]]; then
    echo ""
    echo "⚠️  These reminders are NOT created and need manual intervention:" >&2
    for idx in "${FAILED_IDXS[@]}"; do
      # Only list those that didn't succeed in retry — simplest: re-check by
      # whether they were in FAILED_IDXS AND the retry log line said STILL FAILED.
      # We approximate by listing all originally-failed names; operator will see
      # which had "STILL FAILED" in the log above.
      echo "  - ${QUEUED_NAMES[$idx]}" >&2
    done
  fi
fi
echo "====================================="

[[ $FAIL_COUNT -gt 0 ]] && exit 1
exit 0
