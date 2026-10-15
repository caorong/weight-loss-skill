# Offline coach CLI and data contract

Resolve executable argv prefixes from runtime.json. Use commands.coach, append
an action, `--workspace ACTUAL_WORKSPACE --tz IANA_ZONE`, and `--input FILE` for
writes; `--input -` reads stdin. JSON success requires exit 0 AND ok=true; saved
mutations also return saved=true. stderr/JSON error is not a save receipt.
No shell interpolation of conversation text. A private unique request path is
provided by context; request_id is a stable transport ID + operation suffix, or
an operation token when transport metadata is unavailable. Retain exact payload
for retries. Receipts can be stale: after duplicate=true read current data.

## Read operations

- context: recomputes now, today/yesterday, last7/last14/this_week/last_week;
  returns profile/preferences/revisions, active and future confirmed plans, latest
  weight, reminder revision, reminder_onboarding choice/progress view, unique request_token and request_file.
- measurements [--period today|yesterday|last7|last14|this-week|last-week]
  [--start YYYY-MM-DD --end YYYY-MM-DD]: reads weight and exercise.
- plans: all immutable plan versions + active versions selected by local date.
- review [--review-type daily|weekly|custom]: joins local domains and returns a
  snapshot. daily defaults to today and requires one local date; weekly defaults
  to last-week and requires a Monday–Sunday seven-day range; custom/untyped keeps
  the legacy last7 default and flexible inclusive ranges.
- reminders: desired local settings/known bindings; not live gateway status.
- reminder-specs: scoped native job specs and hashes; does not register them.
- reminder-gate --key KEY: send boolean/reason. Ordinary reminder checks are
  read-only; an eligible review gate atomically reserves notification eligibility
  before returning send=true. A reservation never marks actual delivery.

Range flags apply only to measurements/review. Always pass both start/end for
explicit intervals (inclusive, <=366 dates). New operations resolve date afresh.
Mutation date/effective_date belong in JSON, not CLI query flags. Data dates accept
ISO/today/yesterday as appropriate; retries keep the original literal and request ID.
--review-type belongs only to review reads; review-save takes review_type in JSON.
Manual historical daily ranges support corrections, while scheduled daily reviews
always use today. weekly defaults to the previous completed week; arbitrary old
complete Monday–Sunday ranges are available when explicitly requested.

## Profile / preferences patches

```json
{"request_id":"msg:profile:1","expected_revision":0,"fields":{"height_cm":170,"age_years":30,"biological_sex":"male","activity_level":"sedentary","health_flags":[]}}
```
Example only; never initialize these synthetic values in the user's workspace.
`profile-set` fields: height_cm, age_years, target_weight_kg, biological_sex,
activity_level, display_name, health_flags[], injuries[], medications[], notes[].
`preferences-set`: allergies[], dislikes[], likes[], dietary_restrictions[],
exercise_preferences[], notes[], cooking (text), schedule (text), usual_portions
(text). Patch replaces only named fields; null removes a field. Merge arrays after
reading to append an item. An empty list explicitly clears; don't use it for unknown.
expected_revision must match the managed section's revision (initially 0).

Values live in marked sections inside health-profile.md / health-preferences.md:
`<!-- standalone-coach:profile:begin -->` ... `:end -->` (preferences similarly).
Values are typed JSON on Markdown bullet lines, parsed deterministically. Outside
text is preserved. USER.md is never rewritten. Broken markers/JSON stop the write.

## Weight

```json
{"request_id":"msg:weight:1","value":80,"unit":"kg","date":"today","fasting":true,"note":"User-reported reading"}
```
weight-add requires value and unit (kg/lb/lbs/公斤/斤). Optional date, fasting bool,
observed_at (actual timezone-aware timestamp matching date), note. No observed time
means observed_at=null and time_precision=day. Existing ISO dictionary keys are
preserved; new day-only keys use a unique midnight anchor for storage compatibility,
NOT an actual measurement time. Read date and observed_at, never interpret that
anchor as evidence of a midnight weigh-in. All new values are stored in kg.

weight-update: request_id, id, expected_revision, value, unit; optional fasting/note.
weight-delete: request_id, id, expected_revision. No automatic overwrite on proximity
in time. Unsupported legacy shapes stop for explicit migration instead of guessing.

## Exercise

```json
{"request_id":"msg:exercise:1","date":"today","items":[{"activity":"Walking","duration_min":30,"source":"user_reported","net_calories_kcal":null}]}
```
exercise-add items each require activity, duration_min; optional category, intensity,
calories_kcal (gross), net_calories_kcal (active), source (user_reported/device/
met_estimate), note. Only completed activities; no future dates.
Unknown calorie estimates remain null. Do not infer active calories from a device's
gross value. Each item receives ID/revision; exercise.json remains date-keyed with
exercises arrays. total_calories is sum of KNOWN gross values; calories_complete
flags whether it covers every item. Reports count unknown net estimates explicitly.

exercise-update: request_id,id,expected_revision,item (complete replacement except
retained metadata). exercise-delete: request_id,id,expected_revision. Read first.

Legacy records without an `id` receive a persistent `exercise_<UUID>` on their first
exercise mutation, under the coach lock. Existing IDs and other fields are
preserved; missing revisions still read as 0. The identity normalization is backed
up and atomically journaled separately before the requested mutation, so IDs remain
stable even if that mutation fails or is interrupted. Measurement/review/reminder
reads do not perform normalization; before the first exercise write they may return
temporary `legacy_exercise_YYYY-MM-DD_INDEX` references. Always address
updates/deletes by a persisted ID, never by array index.

Old `legacy_exercise_YYYY-MM-DD_INDEX` position-based mutation requests first
normalize missing IDs, then reject the requested change; query again for the
persisted UUID and revision before sending a new mutation. If an older
version already stored such a position-shaped ID, it is preserved but cannot safely
be used for update/delete: explicit identity reconciliation is required. Duplicate,
empty or non-string stored IDs also stop exercise access without rewriting the file;
they require explicit reconciliation rather than automatic reassignment.

## Plan draft / activation

```json
{"request_id":"msg:plan:1","kind":"goal","effective_date":"today","body":"Actual proposed strategy in Markdown","targets":{"calories":1800}}
```
Example target is synthetic, not a prescription. plan-save accepts kind goal/meal/
exercise, body, optional targets (only goal, calories/protein/carbs/fat), effective_date
(today..366 days ahead), basis_review_id. It creates a draft, never changes targets.
Unknown target is omitted. No retroactive effective dates. body must not contain
reserved managed-section markers. All revisions live in data/plans.json.

```json
{"request_id":"msg:confirm:1","plan_id":"plan_FROM_RECEIPT","confirmed":true}
```
plan-activate requires actual user confirmation. It rejects stale drafts, already
confirmed IDs, and drafts whose baseline changed. A retry with the SAME request ID
returns the original receipt. Future activation schedules the version, not today's
target. Active = latest confirmed effective_date <= query day; later sequence wins
on equal dates. Scheduled versions are visible separately in context.

PLAN.md / MEAL-PLAN.md / EXERCISE-PLAN.md are readable projections of confirmed
versions in managed blocks, not separate target authorities. Use context/plans to
select active versions; the last physical paragraph may be a future plan.

## Review persistence and proposals

Compute the report first, then use its exact type, start/end and snapshot:

```json
{"request_id":"msg:daily-review:1","review_type":"daily","start":"2026-10-14","end":"2026-10-14","expected_snapshot":"COPY_EXACT_REPORT_SNAPSHOT","analysis":"Evidence-based retrospective from the model"}
```

review-save input: request_id, start, end, expected_snapshot, analysis (text from
the model), plus review_type=daily|weekly|custom. Omitting type retains legacy
custom semantics; do not omit it for a typed report. A snapshot mismatch fails;
recompute the report AND interpretation rather than swapping the hash into stale
analysis. cutoff_at is the query time and excluded from the snapshot hash so time
passing alone does not invalidate a save. Substantive evidence, type, coverage,
effective targets and the prior-week weight comparison remain covered by the hash.

Saves a new ID in data/coach-reviews.json and a readable file:

- daily: data/reports/coach-review-daily-START-END.md
- weekly: data/reports/coach-review-weekly-START-END.md
- custom/legacy: data/reports/coach-review-START-END.md (unchanged)

The same type/period can have multiple saved versions; the Markdown shows the
latest, while the registry retains all snapshots. A manual regeneration after
late records does not reset notification eligibility or send another message.
Daily analysis must not write plans. A weekly proposal may use plan-save with
basis_review_id, but requires explicit confirmation before plan-activate. Reports
never change active targets on their own.

### Report evidence and limits

references/coach-review.schema.json documents the enriched report and the
reviewSaveInput definition. It is an interchange contract, not a live-model
validator or a new Python dependency. Narrative still follows coach-flows.md.

- review_type, start/end, timezone and cutoff_at identify the local window and
  observation cutoff. coverage.full_intake_confirmed is false; a closed calendar
  day is not proof of a complete log.
- Each meal_days entry retains totals/records and adds effective_targets,
  intake_minus_target, target_comparison, completeness, exercise_plan_id,
  exercise_estimate, food_evidence, largest_recorded_food_contributors,
  food_tradeoff_candidates and unknowns. A missing target/gap is null. A numerical
  gap is recorded intake minus effective target, never true energy balance.
- food_evidence keeps date/meal_id/food_id, quantity/unit, nutrition/portion sources
  and nutrients. Tradeoff candidates are conservative optional observations tied
  to those IDs, not food that was eaten, guaranteed savings or mandatory advice.
  The runtime shortlist only recognizes explicit sweet-drink/dessert names and
  ranks their recorded calories; it is not a personalized optimizer. The model
  may reason over ALL actual food_evidence for useful omissions, reductions or
  substitutions, considering satiety/nutrition/preferences. It may transparently
  estimate a proportional change for the SAME composition (for example, half a
  recorded portion is roughly half its estimated calories), disclosing assumptions.
  Runtime estimated_calorie_saving=null does not prohibit that supported arithmetic;
  it does prohibit treating a missing estimate as an established saving. Do not
  invent food, substitute nutrition or savings to reach a quota.
- exercise_estimate gives recorded sessions/minutes and known gross/net estimates
  separately, with unknown counts and sources. It never adjusts food targets or
  subtracts exercise from intake. No recorded sessions does not prove inactivity.
- review_boundaries records no automatic target/plan change, no compensation and
  an empty tomorrow_tasks list. It does not mechanically validate model prose.
- weekly_review.weight_trend compares daily medians from the reviewed complete
  week and the immediately preceding week. It requires at least three weight
  dates in EACH; unsupported change remains null. These are descriptive software
  heuristics, not clinical thresholds. Prior-week evidence is included in snapshot.
- weekly_review.recurring_observations identifies repeated supported observations
  and recording gaps, not causes of weight change. exercise_adherence is currently
  not_assessable: existing free-text plans are not a machine-verified schedule.
  planned_sessions/completion_rate remain null, even when an effective plan exists.
  Never turn these values into an invented adherence percentage.

The original summary fields remain for compatibility. Its two_week_mean_change_kg
uses only weights inside the requested report range, whereas the typed weekly
weight_trend explicitly reads the preceding comparison week. Use the typed weekly
field for that comparison, and name the window accurately.

## Reminder APIs

reminder-onboarding is a read-only seven-option view, also in context. It returns
revision (shared with reminders), options keyed by daily-review/weekly-review/
breakfast/lunch/dinner/weight/exercise, unasked, awaiting_answer,
needs_configuration and legacy_review_keys. Each option contains state, recorded
choice, configured_keys, enabled (actual desired settings only) and suggested_key.
accepted means pending configuration, never enabled. configured reflects existing
settings, including disabled ones; migration_required preserves untyped legacy
reviews. Mapping uses kind/review_type, not an assumed key. No read migration.

reminder-onboarding-set is a normal transactional mutation: request_id,
expected_revision, optional offered array and optional decisions object. Offered
options must have been presented and cannot overwrite answered choices. decisions
maps option IDs to accepted/declined/skipped and requires confirmed=true from an
explicit user answer. Batch all expands to seven entries, never a wildcard. A
partial answer leaves omitted entries unchanged; no defaults for silence.
An option cannot appear in offered and decisions together. Existing configured or
legacy-review categories reject onboarding edits; use reminder-set after explicit
user instructions. Unknown options/states and stale revisions fail atomically.

Choices are stored in an optional onboarding={version:1, choices:{...}} field in
data/coach-reminders.json. Each stored choice has state, updated_at and request_id;
absent entries mean unasked. The result returns schedules_changed=false and
native_status=not_applied. No choice mutation writes items or touches native jobs.
Re-read after retries: an old receipt is not the current setup view. Onboarding
state is not scheduler authorization. Complete times/days/timezone/route confirmation
is still required for reminder-set; accepted choices alone yield no specs.

See native-reminders.md for full examples and the actual Gateway handshake.
Local reminder-set and reminder-bind require mutation request IDs. sync-plan is
read-only: it consumes a complete gateway jobs snapshot, returns proposed native
operations and never contacts the network. Do not mistake its output for execution.
references/coach-reminder-settings.schema.json describes canonical complete NEW
settings, not patches or legacy stored entries. New kind=review settings require
review_type=daily|weekly; daily has all seven weekdays and weekly exactly one.
review_period is derived in gate output (today/last-week); it is not a settings
field. Existing untyped review entries remain custom/last7 until explicit migration.

A successful review gate returns reviews[] with key, review_type, review_period,
start, end, notification_key and snapshot; top-level review_type/review_period
identify the first returned component. notification_reservation.at_most_once=true
and delivered=false mean RESERVED, not delivered. notification_key is scoped by
the exact route, IANA timezone, type and local date/range; the same route's duplicate
schedule times do not earn a second notification for that range. The reservation is stored
in data/coach-review-deliveries.json under the coach lock. Same-time/route eligible
reviews elect a deterministic leader; followers return combined_with_other_review.
Already reserved ranges return review_already_reserved. A crash after reservation
may drop a notification; do not erase it or replay automatically. This local
eligibility guarantee is not an exactly-once native transport guarantee.

## Transaction and maintenance boundaries

coach.py owns profile sections, measurements, plans, reviews, reminder onboarding choices/settings
and review notification reservations (not native delivery receipts).
Writes use data/.coach.lock and data/coach-state.json (version/requests/pending).
All changes in ONE transaction are recovered together before the next operation.
Legacy exercise identity normalization is its own transaction and can remain saved
even when the subsequent requested operation fails. It has a separate internal
receipt and cannot be mistaken for successful completion of that operation.
Preimages are checked and backed up under data/coach-backups/TXID/RELATIVE_PATH.
No operation writes USER.md/AGENTS.md or gateway config. Symlinks are rejected.
Meal ledger keeps its existing separate journal/lock; report lock order is coach
then meals. Mixed-message writes have separate receipts, NOT cross-domain atomicity.
Do not run upstream writers concurrently; they do not honor these locks.

No automatic deletion of backups, receipts or review history. They retain old
health data even after current records are deleted. For consistent backup stop
local coaching activity first and copy all health documents + the entire data/
folder to private storage. Restore the set together, never one half of a pending
transaction. Retention/purge requires owner authorization and a cleared journal;
pruning receipts weakens duplicate protection. No promise of secure erasure, NFS
safety, or protection from arbitrary malicious processes running as the same user.
