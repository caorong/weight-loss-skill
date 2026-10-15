# Native OpenClaw reminders (opt-in, no author backend)

These flows are implemented by coach.py plus the actual OpenClaw scheduler tool.
Python does NOT access the gateway, credentials, a lifecycle API or send messages.
The agent executes returned native operations with its available authorized tool.
No extra plugin is required for data storage; native scheduling/tool permissions
and the existing mobile channel must actually be available for reminders.

## 1. Consent and local configuration

At the first interactive initialization, explicitly offer all seven independent
options in one compact checklist: daily review, weekly review, breakfast, lunch,
dinner, weight and exercise reminders. The user may select a subset, enable all,
decline all, or skip setup. These are choices, not default enabled schedules.
On a resumed/mixed setup, ask only returned unasked entries; display already
configured categories as existing settings and leave answered choices alone.
Installation is noninteractive and creates no choices, schedules or native jobs.
If installation was headless, ask at the first suitable interactive setup turn.
Ordinary food/weight/exercise logging must keep working without an answer.

### Persist the choices before collecting schedules

Read `reminder-onboarding` (also returned as context.reminder_onboarding).
It covers seven canonical option IDs: daily-review, weekly-review, breakfast,
lunch, dinner, weight and exercise. Store changes with reminder-onboarding-set,
a fresh request_id and the latest shared reminders revision. For example, after
actually showing all seven choices (never before an unsent question):

```json
{"request_id":"setup:offered:1","expected_revision":0,"offered":["daily-review","weekly-review","breakfast","lunch","dinner","weight","exercise"]}
```

An explicit partial answer can then be saved without inventing any schedule:

```json
{"request_id":"setup:answer:1","expected_revision":1,"confirmed":true,"decisions":{"daily-review":"accepted","weekly-review":"declined","exercise":"skipped"}}
```

These are synthetic examples, not user consent. `confirmed:true` attests an
explicit answer. Use accepted/declined/skipped only for the options the answer
actually covers. “Enable all” during first setup expands to seven accepted entries
in one decisions object; “disable all” to seven declined entries; “skip for now”
to skipped for all still-open choices. Keep already answered choices unless the
user explicitly changes them. On mixed/resumed setup, an “all” answer applies
only to the still-open choices actually listed in that question, not existing
configured/declined/skipped choices. Clarify if its scope is ambiguous.
Do not interpret silence as skipped or declined.
An exact retry reuses the same request_id and payload; a new answer uses fresh
context/revision and a new ID. A stale revision must be re-read, never forced.

- unasked: has not yet been offered; include in initial setup.
- offered / awaiting_answer: already presented, unanswered, still off. Resume
  only when the user continues setup, not as a repeated question on every meal.
- accepted / needs_configuration: opted in but still no enabled setting or job.
  Preserve this choice while collecting missing details; do not re-ask opt-in.
- declined or skipped: off, excluded from automatic offers. Revisit only when the
  user asks to change reminders or resume a deliberately skipped setup.
- configured: existing settings are authoritative, including disabled entries.
  Use configured_keys to edit actual keys; do not create canonical-key duplicates.
- migration_required: an untyped legacy review exists. Preserve its settings and
  last7 semantics; ask for explicit migration only when requested, never silently
  add daily/weekly jobs beside it. legacy_review_keys identifies these entries.

The optional onboarding choices live in data/coach-reminders.json beside items,
not in native job specs. Choice changes never alter existing settings/bindings.
Read/context/upgrade never write or migrate choices. There is no automatic
“restart onboarding” operation. Existing categories cannot be changed through
reminder-onboarding-set; use an explicitly requested reminder-set edit instead.
If suggested_key is null, that key is occupied: choose an unused explicit key,
never overwrite a different reminder. Legacy migration is also an explicit edit.

### Confirm a complete schedule, then configure

Once choices are known, summarize and obtain confirmation of the selected local
times, days, IANA timezone and destination. Daily review runs every day; weekly
review needs one weekday; exercise needs explicit exercise days. For meals and
weight, confirm the days too (all seven is permitted, not assumed). Reuse known
meal/exercise times or timezone as proposals; they are not permission to schedule.
The user may approve selection plus all complete details in one message; do not
ask the same confirmation again. With incomplete details, leave accepted choices
pending and do not call reminder-set or the scheduler.

Query actual runtime routing: explicit Agent, channel and recipient. Do not guess
from an Agent name, default to Slack or use channel=last. Confirm the resolved
destination. The first version supports a single resolved bot account/conversation;
if the channel needs additional accountId/thread routing, stop and adapt the schema
rather than dropping those fields or sending to the wrong chat. Never auto-enable
at onboarding. A decline/skip does not need fabricated times or a route.

Use reminder-set, expected_revision from `reminders`/context:

```json
{
  "request_id":"incoming-msg:reminder:1",
  "key":"breakfast",
  "expected_revision":0,
  "confirmed":true,
  "settings":{
    "enabled":true,
    "kind":"breakfast",
    "time":"09:00",
    "weekdays":[0,1,2,3,4,5,6],
    "route":{"agent":"ACTUAL_AGENT","channel":"ACTUAL_CHANNEL","to":"ACTUAL_RECIPIENT"},
    "pause_until":null
  }
}
```
This is a schema example, not permission to schedule 09:00. key is a lowercase
identifier; kinds breakfast/lunch/dinner/weight/exercise/review; weekdays 0=Sunday.
New review settings MUST specify review_type; use separate reminder keys:

```json
{
  "key":"daily-review",
  "settings":{
    "enabled":true,
    "kind":"review",
    "review_type":"daily",
    "time":"21:00",
    "weekdays":[0,1,2,3,4,5,6],
    "route":{"agent":"ACTUAL_AGENT","channel":"ACTUAL_CHANNEL","to":"ACTUAL_RECIPIENT"},
    "pause_until":null
  }
}
```

This is a settings fragment; wrap it in reminder-set with a real request_id,
expected_revision and confirmed=true only after consent. The example time is not
a default. For weekly use another key, review_type=weekly, one chosen weekday
(for example [1] for Monday), and the user's chosen time. daily uses all seven
weekdays and covers today; weekly uses one weekday and covers last-week, the
previous complete local Monday–Sunday. The gate resolves review_period from the
type. Do not attach daily/weekly fields to food, weight or exercise reminders.

An already stored kind=review WITHOUT review_type remains the legacy last7
behavior. Do not relabel/migrate it silently or create another legacy review. Ask
which cadence(s) the user wants, then update the appropriate keys and reconcile
native jobs. Ordinary disable/pause/edit of a legacy entry preserves its existing
meaning until that explicit migration. Read current settings before patching; a
daily/weekly type change must update incompatible weekdays too.

Timezone comes from
resolved --tz, never a fixed UTC offset. `enabled:false` stops local sending
immediately. A pause_until is a timezone-aware ISO instant; null clears a pause.
Read current revision and merge only requested changes. Pause/disable preserves
history; no blanket deletion of other tasks.

Local save returns native_status=sync_required. It is NOT "reminder is scheduled".
After an interrupted setup, configured means only that local settings exist.
Inspect specs/bindings and re-LIST native jobs to resume unverified registration,
even when needs_configuration is empty. Do not restart opt-in or re-add matching
jobs; bind the real read-back. Always verify live native status before claiming
registration, including previously bound entries.

## 2. Reconcile native jobs, then verify

1. Use actual cron/automations native list action, include disabled tasks and ALL
   pages. If using CLI, inspect local --help; don't invent version-specific flags.
2. Pass `{"complete":true,"jobs":[ACTUAL_FULL_JOB_OBJECTS]}` to reminder-sync-plan
   via a private JSON input. This action returns operations; no request_id needed.
3. For each operation call the real native scheduler:
   - add: pass the operation.job object as the job.
   - update: pass operation.jobId and operation.patch.
   The returned key/spec_hash are local metadata, NOT native tool arguments.
   Adapt only to the actual exposed schema; if unsupported, report failure.
4. Read back each resulting native job. Compare name/agent/enabled/schedule/
   sessionTarget/payload/delivery, then call reminder-bind:

```json
{"request_id":"incoming-msg:bind:1","key":"breakfast","spec_hash":"HASH_FROM_SPEC","observed_job":{"id":"REAL_NATIVE_ID","name":"...","agentId":"...","enabled":true,"schedule":{},"sessionTarget":"isolated","payload":{},"delivery":{}}}
```

The above is illustrative; observed_job must be the REAL complete read-back.
It cannot be reconstructed from desired settings. Bind rejects mismatch. A native
list may add metadata such as createdAtMs/staggerMs; these are accepted. Existing
model overrides are rejected for explicit repair instead of inheriting author ARN.
Unchanged matching jobs are not created again; changed jobs are updated in place.
A scoped job missing from the gateway can be recreated from desired state. Unrelated
jobs are never changed. Name collisions or duplicates require explicit reconciliation.

Spec names contain a workspace-specific owner hash. No hardcoded provider/model;
isolated agentTurn inherits the target Agent's configured model. Delivery uses
announce + explicit channel/to. Payload contains only an actionable reminder task,
not a second delivery wrapper. Skill instructions own the no-self-send rule.
The CLI alias may be called cron or automations depending on runtime; don't install
or switch OpenClaw versions silently just to match an example.

A native creation timeout can leave a real job behind. Re-LIST and reconcile before
retrying add; never blindly create another. Binding hash + current desired settings
protect stale route/time/enable changes. Registration does not prove delivery: inspect
native run history or an authorized test before reporting end-to-end success.

## 3. Execution-time gate (every run)

Get fresh coach context/time and ensure prompt owner matches local specs. Call
reminder-gate --key KEY. It suppresses missing/disabled/unverified config, pending
sync after timezone/route changes, active pause, wrong weekday, already recorded
meal/weight/exercise, and stale runs outside [-2min,+60min] of the current day's
configured time. It never calls an external lifecycle service; corruption/errors
fail closed. A delayed run beyond that window is intentionally skipped, not replayed
as yesterday's reminder. The gateway's next on-time run proceeds normally.

send=false -> exact NO_REPLY (verify the installed runtime supports this suppression
sentinel). send=true -> brief final text. Do NOT call message/curl/send scripts.
Announce handles delivery; do not record local "delivered" before a native receipt.
Paused native tasks can still incur invocation costs.

### Review reservation and simultaneous due reviews

For kind=review, a successful gate reserves each notification BEFORE returning
send=true, under the local coach lock. data/coach-review-deliveries.json records
reservation/audit state by exact route, IANA timezone, review type and local
date/range. Different configured times on the same route do not authorize a second
notification for an already reserved range. It is not a delivery
receipt. Repeated or concurrent attempts for the same notification return
send=false (review_already_reserved); no second announce is allowed.

When typed daily and weekly reviews are eligible at the same configured local
time and exact route, one deterministic leader handles both. A follower returns
combined_with_other_review. Only eligible, enabled, unpaused and verified settings
are combined. Follow the gate's reviews array, with key, review_type, review_period,
start, end, notification_key and snapshot for each component. Do not discover a second due
review yourself and send separately. Different times/routes are not combined.

This deliberately provides at-most-once LOCAL notification eligibility, not
exactly-once delivery. A crash or announce failure AFTER reservation can mean a
missed message. Do not clear reservations or automatically replay that range to
compensate; inspect actual gateway history and tell the user what is known. A
manual report is still available. Native transport may have its own behavior;
only its receipts prove delivery. Late records and report regeneration do not
reset the reservation or trigger a repeat notification.

### Compose the allowed retrospective

For each returned reviews entry, run review with its review_type and exact range,
then review-save with the same type/range and expected_snapshot. daily=today;
weekly=last-week; an existing untyped legacy entry retains custom/last7. Preserve
the resolved dates across this operation. If both are returned, compose one plain
text message with distinct daily and weekly sections; do not duplicate the facts.
Apply coach-flows.md's rubric:

- Daily: recorded calories/macros versus effective target, completed exercise
  duration/available estimates separately, cutoff/completeness, and 1–3 evidenced
  food tradeoffs only when supported. No tomorrow tasks, plan draft/write or
  compensatory restriction/exercise.
- Weekly: supported trends/patterns and adequately evidenced exercise adherence.
  A linked plan draft may be proposed; never activate it in a scheduled run.
  User confirmation is a separate interactive operation.

Do not call intake-minus-target a true surplus/deficit, subtract exercise into a
daily energy balance, or double-count TDEE activity. No website, MySQL/S3,
plan-export or mini-program is needed.

## 4. Cancellation and failure

Disable locally, synchronize native enabled=false, read back and bind. An old
scheduled turn that runs before sync is suppressed by local disabled state. If
native sync fails, say "local sending stopped; gateway disable pending", not
"all jobs removed". A pause_until gates until expiry without deleting configuration.
Do not stop other Agent tasks. Existing upstream jobs remain a separate installation
conflict and must be disabled only with the user's authorization.

No real gateway call or mobile message is performed by repository unit tests.
At installation, separately report local tests, native tool availability, registered
jobs, and actual delivery status. Zero reminders is the correct default.

Primary references (installation AI must check current/local schemas):
- https://docs.openclaw.ai/cli/cron
- https://docs.openclaw.ai/cron
