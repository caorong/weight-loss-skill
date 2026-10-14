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
  weight, reminder revision, unique request_token and request_file.
- measurements [--period today|yesterday|last7|last14|this-week|last-week]
  [--start YYYY-MM-DD --end YYYY-MM-DD]: reads weight and exercise.
- plans: all immutable plan versions + active versions selected by local date.
- review: same range flags, default last7, joins all local domains, returns snapshot.
- reminders: desired local settings/known bindings; not live gateway status.
- reminder-specs: scoped native job specs and hashes; does not register them.
- reminder-gate --key KEY: send boolean/reason; read-only, never marks delivery.

Range flags apply only to measurements/review. Always pass both start/end for
explicit intervals (inclusive, <=366 dates). New operations resolve date afresh.
Mutation date/effective_date belong in JSON, not CLI query flags. Data dates accept
ISO/today/yesterday as appropriate; retries keep the original literal and request ID.

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

review-save input: request_id, start, end, expected_snapshot, analysis (text from
the model). Compute review first. A snapshot mismatch fails; do not attach old
analysis to changed data. Saves a new ID in data/coach-reviews.json and a readable
`data/reports/coach-review-START-END.md`. The same period can have multiple saved
versions; the Markdown shows the latest, the registry retains all snapshots.
A proposal uses plan-save with basis_review_id and still requires plan-activate
confirmation. Reports never change active targets on their own.

## Reminder APIs

See native-reminders.md for full examples and the actual Gateway handshake.
Local reminder-set and reminder-bind require mutation request IDs. sync-plan is
read-only: it consumes a complete gateway jobs snapshot, returns proposed native
operations and never contacts the network. Do not mistake its output for execution.

## Transaction and maintenance boundaries

coach.py owns profile sections, measurements, plans, reviews, reminder settings.
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
