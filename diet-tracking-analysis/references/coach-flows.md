# Local coach workflows

All command prefixes come from installed runtime.json commands.coach / commands.meals.
Do not activate similarly named upstream skills. Read coach-api.md for contracts.
The existing agent model handles language, visual interpretation and plan writing;
Python handles records, calculations, version selection and local verification.

## Food check-ins and pending clarification

Explicit meal check-ins or clearly established intake check-in context confirm
consumption; after identity/portion resolution, save once without another
"吃完了" confirmation. Explicit not-yet-eaten, future or premeal advice overrides
check-in shorthand/context. A standalone photo is not automatically a check-in;
ask once if intent is ambiguous, and retain the answer across clarifications.
Follow the intent decision order in references/standalone-flow.md.

Before estimating or saving food, apply SKILL.md's food identity gate and
references/standalone-flow.md. Identity uncertainty overrides check-in auto-save;
"record lunch" is not permission to invent the dish. Ask a short identity question
and hold the whole meal by default, rather than saving unknown foods as zero.
Ordinary portion uncertainty for identified food can use a labelled estimate.

When the next message is a reply to the pending question, merge the answer with
the original meal and intent before routing it as a new check-in or preference.
Use the original eating date (including a reply after midnight), not the fresh
context's today for this pending meal. Save once after resolution only for a
consumed check-in; advice/future intent stays unlogged. If known items
were already saved at the user's request, append only the missing item by ID.
An unanswered question stays unresolved. If pending context was lost, ask for
the original meal/photo/date; there is no persistent pending-meal registry.
Later leftovers/amount-eaten corrections update the existing food by ID/revision
(or delete wholly uneaten items); they do not add a duplicate meal.

## First use / profile update

1. Get fresh `context`, then read existing USER.md and health Markdown, if any.
2. Use facts already supplied. Do not repeat questions; basic logging is usable
   without onboarding. Ask only what the requested plan genuinely needs.
3. Save explicit profile fields using profile-set, expected_revision from context
   (0 when no managed section exists). Keep units explicit and kg/cm internally.
4. Save likes/allergies/preferences separately via preferences-set. Read-modify-
   merge list values; a patch replaces the specified list, not appends blindly.
5. Confirm what was actually persisted. Do NOT bootstrap reminders or exports.
6. When discussing review setup, reuse the known IANA timezone or ask if missing;
   ask separately about daily opt-in + local time and weekly opt-in + one weekday
   + local time. There is no default opt-in. Either may stay off, and both may be
   changed, paused or disabled independently. This must not block ordinary logging.

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

Choose the review type explicitly for the two regular retrospectives:

- `review --review-type daily`: current local date only, default period today.
  Scheduled daily reviews never silently switch to yesterday. A manually requested
  historical single-day review is allowed for a late correction/backfill.
- `review --review-type weekly`: default last-week, the previous complete local
  Monday–Sunday. Even a Sunday evening run still covers the PREVIOUS complete
  week, not the week that is still in progress. Monday is often convenient, but the
  weekday/time belongs to the user.
- `review --review-type custom` or an existing untyped review: preserve flexible
  ranges and the legacy last7 default. last7 includes today, this-week means
  Monday through today, and last14 covers fourteen local dates. These are custom
  views, not substitutes for the scheduled daily or weekly contracts.

Recompute local boundaries each operation. Explicit ranges are inclusive dates,
not elapsed 24-hour windows; DST does not change which calendar dates are included.
The cutoff is the report's collection time. Do not call any logged day complete
unless the evidence establishes completeness. Zero records are not proven fasting.

The report joins meals, weights, exercise, profiles and active goal BY DAY. Targets
for old dates are not replaced by today's target. Logged-day averages exclude
unrecorded days; they still may contain incomplete days. Do not infer adherence
or a calorie deficit solely from these averages. It does not automatically subtract
exercise from food calories. Known net burn and unknown sessions remain distinct.
An intake-minus-target difference describes recorded intake versus a goal, not
actual energy surplus/deficit, fat change or calories available to eat. Never
double-count activity already included in a TDEE estimate.

### Daily: what happened today, and food choices in context

Keep the reply compact, with these facts before advice:

1. Local date and cutoff; recorded meals and any missing/uncertain coverage.
2. Recorded calories, protein/carbs/fat, and available effective targets. State
   absent targets as unknown, never manufacture a target from an old/future plan.
3. Completed exercise type/duration, estimated calories and source if known. Keep
   gross/net values distinct; unknown estimates do not mean zero exercise.
4. Up to 1–3 specific food tradeoffs grounded in today's actual records, when
   useful. Name the recorded item/portion that supports each observation and
   explain the tradeoff, considering fullness, protein/fiber/nutrient value and
   known tastes/restrictions. An optional lower-energy alternative is hypothetical,
   never a food the user already ate. Omit suggestions when evidence is too weak.

The runtime's food_tradeoff_candidates is only an optional, narrow shortlist of
explicit sweet-drink/dessert evidence, sorted by recorded calories. It is not a
personalized optimizer or the full list of foods worth discussing. Reason over
all recorded food_evidence, including other actual foods, with the user's needs
and preferences. A proportional estimate is allowed when composition is unchanged:
half a recorded portion is approximately half its estimated calories. Explain the
portion/composition assumptions and uncertainty; do not invent substitute-food
nutrition or imply precise savings. A null runtime saving is unknown, not zero.

Examples of the reasoning pattern, not canned recommendations: a recorded sweet
drink might have a less-sweet alternative that preserves the preferred flavor;
an unusually large recorded accompaniment might admit a smaller amount while
preserving the satisfying protein/vegetable components. Do not assume drinks,
sauces, oil, snacks or skipped foods that were not recorded. A lower calorie count
alone does not establish a better meal, and do not recommend reducing an already
insufficient intake. Unknown food identity still follows the clarification gate.

This is a retrospective, not tomorrow's to-do list. Do not write a new meal or
exercise plan, call plan-save/plan-activate, prescribe compensatory fasting or
extra exercise, or frame food as something to earn/burn off. If all that is known
is one meal, say so and review that evidence without extrapolating a complete day.

### Weekly: patterns supported by the completed week

Lead with the date range and coverage. Summarize recorded intake versus each day's
effective goal, supported weight trend, repeat food patterns and completed exercise.
One instance is not a recurring pattern. Discuss exercise-plan adherence only when
the effective plan can be compared meaningfully with adequate actual records;
missing exercise logs are not proof that a planned session was skipped.

Daily weight uses median of fasting readings if supplied; otherwise median of all
readings. This is descriptive smoothing, not diagnosis. Two-week mean change compares only the seven dates ending at the report end
with the immediately preceding seven dates (end-13..end-7), even in a longer
review. It needs at least three recorded days in EACH of those blocks; missing evidence yields null. The
review_readiness flag is a software heuristic, NOT a clinical threshold.

Do not present a null trend or readiness flag as clinical evidence. A single-week
report may have no two-week comparison; request a separate custom last14 view only
when useful, and label its different window. Sparse records call for uncertainty,
not automatic target cuts or fabricated trends.

Write a short interpretation of data limitations, repeatable habits and feasible
options. `review-save` requires the exact report.snapshot, review type and start/end
for typed reports. If any
underlying data changed, recalculate and rewrite the interpretation; do not attach
stale commentary to a fresh snapshot. It produces a local Markdown report and
retains the structured snapshot/version; chat can contain the report directly.

Weekly workflow: a review may generate a draft proposal linked with basis_review_id.
Explain the evidence, proposed change and uncertainty, and ask the user to confirm
before activation. A draft does not change a target or training schedule. Daily
reviews must not create these drafts. For sparse records, preserve the existing plan.
For pain, extreme restriction or distress, prioritize safety/professional input.
Only explicit confirmation activates an adjustment, usually today or a future day.
Existing plans changing while a draft is pending invalidate its baseline.

### Late records, corrections and repeated runs

Attribute a late meal, weigh-in or completed workout to its occurrence date, not
the date of the reply. A correction uses the original record ID/revision. Recompute
and save a new affected report version when requested; keep the old snapshot for
history. Regenerating a report must not repeat its scheduled notification or reset
its notification reservation. Do not notify again just because late data changed
the totals. Manual report reads/saves and scheduled notification eligibility are
separate operations.

## Reminders

Only after user opt-in with actual times, days, Agent and channel recipient, follow
native-reminders.md. Food/weight/exercise reminders and independent daily/weekly
reviews are supported. Daily runs on all seven weekdays; weekly runs on one chosen
weekday. Legacy untyped review reminders keep last7 until explicit migration.
When both are due at the same local time and route, one deterministic eligible
leader reserves both and returns one message with separate daily/weekly sections.
Do not invoke a second send or retry the follower as a standalone notification.
No default meal reminder is created just because a user provided meal times.
Pause/disable immediately updates the local gate; synchronize the actual native job
and verify it too. Pausing suppresses output, but a still-enabled native job can
continue invoking the model until disabled; do not claim it saves invocation costs.
