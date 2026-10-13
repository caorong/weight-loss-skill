# Local coach workflows

All command prefixes come from installed runtime.json commands.coach / commands.meals.
Do not activate similarly named upstream skills. Read coach-api.md for contracts.
The existing agent model handles language, visual interpretation and plan writing;
Python handles records, calculations, version selection and local verification.

## Food check-ins and pending clarification

Before estimating or saving food, apply SKILL.md's food identity gate and
references/standalone-flow.md. Identity uncertainty overrides check-in auto-save;
"record lunch" is not permission to invent the dish. Ask a short identity question
and hold the whole meal by default, rather than saving unknown foods as zero.
Ordinary portion uncertainty for identified food can use a labelled estimate.

When the next message is a reply to the pending question, merge the answer with
the original meal and intent before routing it as a new check-in or preference.
Use the original eating date (including a reply after midnight), not the fresh
context's today for this pending meal. Save once after resolution; if known items
were already saved at the user's request, append only the missing item by ID.
An unanswered question stays unresolved. If pending context was lost, ask for
the original meal/photo/date; there is no persistent pending-meal registry.

## First use / profile update

1. Get fresh `context`, then read existing USER.md and health Markdown, if any.
2. Use facts already supplied. Do not repeat questions; basic logging is usable
   without onboarding. Ask only what the requested plan genuinely needs.
3. Save explicit profile fields using profile-set, expected_revision from context
   (0 when no managed section exists). Keep units explicit and kg/cm internally.
4. Save likes/allergies/preferences separately via preferences-set. Read-modify-
   merge list values; a patch replaces the specified list, not appends blindly.
5. Confirm what was actually persisted. Do NOT bootstrap reminders or exports.

Managed fields override only their explicitly named values. Legacy prose remains
visible and unmodified. Do not interpret a missing health flag as a negative screen.
Source memory in this ChatGPT conversation is not seeded into the user's repository.
An installer must not create personal stats or sample meals in the real workspace.

## Weight and exercise in everyday messages

A single message can say "Breakfast ..., weight ..., walked ...". Read context
once for interpretation, but use distinct request files/IDs for each transaction.
Save each appropriate domain; report partial failures accurately. Do not re-log
successful domains with new request IDs when recovering a failed one.

Weight: accept actual kg/lb/斤 reading. Current reports default to today's date;
backfills are explicitly dated. Unknown time remains null. Do not infer body weight
from an exercise photo or stale plan. Multiple readings per day are retained; no
30-minute automatic overwrite. Corrections use a specific ID + revision.

Exercise: record only completed activity. The minimum is activity + duration_min.
Preserve optional source (user_reported/device/met_estimate). Calories are optional:
unknown remains null. A device's "total calories" is not its active/net calories.
No implicit API/device connection is claimed. Text/screenshots can provide inputs.
Correct/delete by ID and revision. Plans are never exercise-completion logs.

## Goal, meal and exercise planning

Read current profile/preferences, recent actual weight, and active plans. For a
weight-loss prescription ask for essential missing stats and safety constraints;
do not produce one for minors or risky medical circumstances just to complete a
workflow. The model is an assistant, not a registered dietitian or certified trainer.
A proposed goal can use supported calorie/strategy calculations in the available
runtime, but must clearly label estimates. Do not call broken upstream helpers.

Each plan is a complete Markdown body, not a remote link:
- goal: agreed goal, approach, optional daily targets, review conditions.
- meal: grams/ml or practical portions, raw/cooked distinctions, allergies,
  realistic shopping/cooking constraints, substitutions; all requested days.
- exercise: available days/equipment, exercises, sets/reps or durations, recovery,
  conservative progression, warm-up/cooldown and injury precautions; all days.
Do not invent trainer credentials or guaranteed completion dates. The target
calorie fields belong only to kind=goal. Meal/exercise plans reference that goal
instead of maintaining another competing calorie target.

Run plan-save -> show draft in normal text -> wait for explicit confirmation ->
plan-activate for that precise ID. A direct user statement "set X as my target"
can constitute explicit confirmation; still save then activate, and preserve the
operation IDs. If the user only asks for an idea, do NOT activate it.
The user may confirm all three drafts in one message: activate each with its own
request ID and check every receipt. File names are PLAN.md / MEAL-PLAN.md /
EXERCISE-PLAN.md; unrelated existing text is retained outside managed markers.
No notification job is created by plan generation or activation.

## Local review and adjustment

`review --period last7` means the last seven local calendar dates, INCLUDING today,
not seven complete 24-hour windows. `this-week` is Monday through today;
`last-week` is the previous complete Monday..Sunday. Use explicit ranges for a
particular period. Recompute the range each operation, not once per long session.
For a two-week trend use last14. A single day can be partial.

The report joins meals, weights, exercise, profiles and active goal BY DAY. Targets
for old dates are not replaced by today's target. Logged-day averages exclude
unrecorded days; they still may contain incomplete days. Do not infer adherence
or a calorie deficit solely from these averages. It does not automatically subtract
exercise from food calories. Known net burn and unknown sessions remain distinct.

Daily weight uses median of fasting readings if supplied; otherwise median of all
readings. This is descriptive smoothing, not diagnosis. Two-week mean change compares only the seven dates ending at the report end
with the immediately preceding seven dates (end-13..end-7), even in a longer
review. It needs at least three recorded days in EACH of those blocks; missing evidence yields null. The
review_readiness flag is a software heuristic, NOT a clinical threshold.

Write a short interpretation of data limitations, repeatable habits and feasible
next steps. `review-save` requires the exact report.snapshot and start/end. If any
underlying data changed, recalculate and rewrite the interpretation; do not attach
stale commentary to a fresh snapshot. It produces a local Markdown report and
retains the structured snapshot/version; chat can contain the report directly.

Automatic workflow: a scheduled review can generate a draft proposal linked with
basis_review_id. It must not activate it or lower targets automatically. For sparse
records, prioritize logging/portion clarification and maintain the existing plan.
For pain, extreme restriction or distress, prioritize safety/professional input.
Only explicit confirmation activates an adjustment, usually today or a future day.
Existing plans changing while a draft is pending invalidate its baseline.

## Reminders

Only after user opt-in with actual times, days, Agent and channel recipient, follow
native-reminders.md. Food/weight/exercise reminders and a weekly review are supported.
No default meal reminder is created just because a user provided meal times.
Pause/disable immediately updates the local gate; synchronize the actual native job
and verify it too. Pausing suppresses output, but a still-enabled native job can
continue invoking the model until disabled; do not claim it saves invocation costs.
