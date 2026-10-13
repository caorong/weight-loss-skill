# Native OpenClaw reminders (opt-in, no author backend)

These flows are implemented by coach.py plus the actual OpenClaw scheduler tool.
Python does NOT access the gateway, credentials, a lifecycle API or send messages.
The agent executes returned native operations with its available authorized tool.
No extra plugin is required for data storage; native scheduling/tool permissions
and the existing mobile channel must actually be available for reminders.

## 1. Consent and local configuration

Only after user requests reminders, obtain the exact times, weekdays and resolved
recipient. Query actual runtime routing: explicit Agent, channel and recipient.
Do not guess from an Agent name, default to Slack or use channel=last. The first
version supports a single resolved bot account/conversation; if the channel needs
additional accountId/thread routing, stop and adapt the schema rather than dropping
those fields or sending to the wrong chat. Never auto-enable at onboarding.

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
Review can be weekly with weekdays=[0] at a user-chosen time. Timezone comes from
resolved --tz, never a fixed UTC offset. `enabled:false` stops local sending
immediately. A pause_until is a timezone-aware ISO instant; null clears a pause.
Read current revision and merge only requested changes. Pause/disable preserves
history; no blanket deletion of other tasks.

Local save returns native_status=sync_required. It is NOT "reminder is scheduled".

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
No local exactly-once delivery guarantee is claimed; scheduler transport/history
owns delivery and retries. Paused native tasks can still incur invocation costs.

kind=review -> local `review --period last7`, save commentary with review-save,
optionally plan-save a proposal linked to that review. Return plain text. No website,
MySQL/S3, plan-export or mini-program is needed. Never activate the proposal in the
scheduled run; user confirmation is a separate interactive operation.

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
