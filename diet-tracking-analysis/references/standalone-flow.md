# Standalone workflow and CLI contract

Python 3.10+, macOS/Linux/WSL; standard library only. No pip/API key needed by
this script. Python, timezone database and the workspace must be accessible
INSIDE OpenClaw's exec environment. Host dependency checks do not prove sandbox
availability. Do not bypass approvals or sandbox boundaries to make it run.

## Invocation

Read `{baseDir}/runtime.json`. Its `command` array is the exact argv prefix;
append the operation and flags. In a shell use separately quoted arguments or
a shell array; never join untrusted text into executable shell code.

Default uv invocation (replace paths with actual runtime values):

```bash
UV="/actual/path/to/uv"
BASE="/actual/skill/path"
WORKSPACE="/actual/agent/workspace"
TZ="Asia/Tokyo"
RUN=("$UV" run --locked --offline --no-python-downloads --project "$BASE" python "$BASE/scripts/meal-log.py")
"${RUN[@]}" init --workspace "$WORKSPACE" --tz "$TZ"
"${RUN[@]}" list --workspace "$WORKSPACE" --tz "$TZ"
"${RUN[@]}" summary --workspace "$WORKSPACE" --tz "$TZ"
```

These are Bash array examples; with another shell use the runtime's argv tool
or quote each argument. Do not assume automatic shell-variable injection.
`--runner python` is an explicit stdlib-only installation fallback; its
runtime.command is `[python_path, script_path]`. Never silently switch to it.
Keep UV_PROJECT_ENVIRONMENT / UV_WORKING_DIR unset so the skill cannot use an
unrelated project's environment. Installation provisions `.venv` at its final
location; do not copy `.venv` from a checkout or another machine. `--offline`
controls uv, not the current model's network use. Failure -> repair setup, not
remove lock/offline flags or install global packages during a meal turn.

`init` returns today, yesterday, last_seven_days_start, now, request_token and a unique request_file.
Run it or coach context for EVERY new operation, not just once per session.
Retain the original input/date/ID when retrying the SAME operation after midnight. A seven-day query uses that start and today as inclusive
`--start` / `--end`. `list --date YYYY-MM-DD` reads history. Date range limit:
366 days. `--target-calories N` is summary-only and requires a confirmed target;
it neither edits a plan nor estimates one. Missing dates return no records,
not an assertion the user ate nothing.

Set REQUEST_FILE to the unique path from fresh init/context; never reuse a fixed request.json across operations.
For mutations, put date in JSON (never `--date`). Read a request using `--input
<file>` or stdin. Prefer the file-write tool to create a private temporary JSON
file under the workspace, then call a quoted fixed command. Never interpolate
raw conversation text into executable Python or an unquoted shell command.
Delete the temporary request after receipt when appropriate. Do not place real
health fixtures or photos in the Git repository.

```bash
"${RUN[@]}" add --workspace "$WORKSPACE" --tz "$TZ" --input "$REQUEST_FILE"
```

## Intent decision order: confirm once, then resolve food

Apply these rules to the whole message and relevant pending conversation, not
isolated keywords, in this order:

1. Explicit not-yet-eaten, future or premeal/advice intent takes precedence over
   "记录午餐"/"午餐打卡" shorthand and any prior intake check-in context.
   "午餐打卡，还没吃，先看看搭配" is advice only; do not write consumed intake.
2. Otherwise an explicit meal check-in ("记录午餐", "午餐打卡", "record lunch")
   or a clearly established intake check-in context confirms consumption. Resolve
   food identity and usable portions, then save once. Do not require another
   "吃完了" or ask "have you eaten?" / "shall I record this?" after resolution.
   Reasonable labelled portion estimates suffice; exact grams are not required.
3. A standalone food photo, a dish name or a calorie question alone is not a
   check-in. Answer clear advice without logging. For genuinely ambiguous intent,
   ask once: "这是饮食打卡，还是吃前想看看建议？" Keep that answer for this meal;
   do not restart intent confirmation after an identity or portion answer.

Identity uncertainty still requires clarification even when intake intent is
confirmed. These are separate gates; a check-in never licenses guessed foods.
Keep original intent/date during short clarification replies (including "半盘"
or "是豆腐"). Only an explicit change such as "现在已经吃了，帮我记上" changes
advice to a check-in; use the newly stated actual eating date, clarifying if needed.
If there is no useful quantity estimate, ask how much of the identified food
was eaten, rather than asking a redundant yes/no consumption question.

After a saved meal, "米饭剩了一半" corrects the original item. Read its current
IDs/revision, determine the new consumed amount relative to the original portion,
and use `update`/`scale` on that food, not `add` for the meal. If an earlier edit
already changed quantity, compute the scale against the CURRENT quantity; do not
blindly halve it again. Use `delete` for a wholly uneaten item, never a zero-quantity
placeholder. Use a new correction request ID and reuse it only for exact retries.
If the referenced meal or amount is ambiguous, ask one targeted correction question.

Acceptance scenarios: `evals/meal-checkin-intent.json`. Offline contract checks
and ledger regressions do not execute a live conversational model or prove its
intent classification; run these scenarios with the deployed model separately.

## Food identity gate before calorie estimation or writes

**Identity clarification overrides check-in auto-save.** A user's instruction to
record lunch confirms intent, not the identity of every visible item. This gate
also applies to calorie-only questions: do not assign an unidentified food a
confident number just because no storage was requested.

Three independent questions:
- Identity: what food is it? Unrecognized or materially ambiguous -> MUST ask.
- Portion: how much was eaten? Known food with a usable estimate -> label the
  assumption and proceed; ask only when there is no useful basis for quantity.
- Intent: consumed check-in, advice or plan? Clarifying a name does not turn advice
  or a future meal into consumed intake. Keep the original intent.

Ask a targeted question such as "The dark dish on the right is unclear; what is
it?" Ask about the user's share if necessary. A supported alternative can be
"Is that tofu or pork?" Do not manufacture candidates, use a percentage threshold
as calibrated confidence, or ask "confirm recording?" instead of the food question.
Readable labels and explicit user corrections take precedence over an image guess.

### Waiting and resuming

Default: no meal write while any food identity remains unresolved. Do NOT use
`add`/`append`/`update` as a place to hold unknown foods. The ledger accepts only
consumed foods with numeric nutrients, not pending dishes. Unknown is not zero;
do not invent a dish or copy another dish's macros just to satisfy validation.
Recognized items may be described, but no whole-meal calorie total is complete
until all items relevant to the user's meal are resolved. Say what is still
unidentified and that this meal has not yet been recorded.

Keep this pending context in the conversation (not in a second health ledger):
original eating date and meal type, original logging/advice intent, recognized
items/portions, unknown item locations/questions, and any IDs already committed.
At the next reply, obtain fresh context/time as usual, then resolve the answer
against THIS pending meal. A reply containing only a dish name is a clarification,
not automatically another meal, a new preference, or a new food-only check-in.

After resolution, combine the earlier evidence and the user's answer, estimate
only the unresolved/corrected item, and submit the complete meal once ONLY if
the original intent was a consumed check-in. Do not request another "吃完了".
For advice or future intent, answer without writing intake. Use the
original resolved ISO eating date even if the clarification arrives after
midnight; the reply date is not the eating date. Allocate a new mutation request
ID when forming new input. Only transport retries reuse the exact submitted
request ID and payload; never reuse a committed ID for changed content. Query
existing rows if a previous save may have succeeded before a turn was interrupted.

No response or an evasive response does not resolve the food identity. Do not
silently guess, time out into a completed log or forget the unresolved item.
No durable pending-meal store is provided: after a lost context or an ambiguous
answer spanning multiple pending meals, ask for the original meal/photo/date.
Do not promise pending questions survive a new empty session.

### Explicitly saving a partial meal

Only if the user explicitly asks to save known items now may you write that
subset. Mark the meal note and reply "incomplete: [unresolved items] excluded";
any total is a KNOWN-ITEM SUBTOTAL, not a full lunch total. Unknown items still
must not be written as zero placeholders. Retain the saved meal ID and food IDs.
When the user later identifies the missing dish, query current IDs/revision and
`append` ONLY the newly resolved food to that meal with a new request ID. A
correction to a saved item uses `update` on that ID. Do not `add` the full meal a
second time. If the user wants analysis only, do not write even the known subset.

### Scope of enforcement and acceptance

This is an agent instruction gate, not a Python vision validator. The script
cannot discover when a model confidently mislabels an image. Show compact dish
names so the user can correct them, and never claim perfect identification.
Behavior scenarios are in `evals/food-identity-confirmation.json` in the source
repository. Offline tests check this contract and fixture structure; they are
NOT evidence that a live model asked the right question or saw an actual photo.

## New record: add

Example numbers below are synthetic API examples, not food reference values.
The agent must replace date with the user's actual eating date and estimates
with those derived from the real input. An unknown eating time is null/omitted,
not fabricated. Recorded_at is generated by the script.

```json
{
  "request_id": "feishu:chat-id:message-id:add:1",
  "date": "2026-10-13",
  "meal_type": "lunch",
  "status": "consumed",
  "foods": [
    {
      "name": "示例米饭",
      "quantity": 150,
      "unit": "g",
      "calories": 200,
      "protein": 4,
      "carbs": 40,
      "fat": 2,
      "source": "model_estimate",
      "portion_source": "photo_estimate",
      "note": "示例数据；实际记录请使用当前图片估算，注明熟重和份量假设。"
    }
  ]
}
```

Required: request_id (max 256 chars), meal_type, nonempty foods. Date defaults
to the supplied timezone's today; today/yesterday literals resolve on FIRST execution.
Explicit ISO dates are recommended for backfilling. Exact retries retain their
original receipt/date even after midnight. Only consumed records, never future dates/times. meal_type is
breakfast/lunch/dinner/snack_am/snack_pm. Route a snack before/after noon to the
appropriate slot; do not append it to an unrelated earlier meal just to reuse
an ID.

Each food requires name, positive quantity, unit (g/ml/serving), nonnegative
finite calories/protein/carbs/fat, source and portion_source. Nutrients describe
that TOTAL quantity. Nutrition source: model_estimate/package_label/user_provided.
Portion source: photo_estimate/user_description/user_measured/package_serving.
Package values per 100 ml must first be scaled to actual ml consumed; do not
assume ml equals grams. Food name should preserve raw/cooked distinctions.
Optional note states uncertainty (unknown oil/recipe/size). No arbitrary numeric
"confidence" is presented as a calibrated probability. Optional consumed_at
must be timezone-aware ISO and match date in the selected timezone.

## Append, correct, delete

Always `list` the relevant date first to get meal.id, food.id and revision.
Use explicit IDs, never overwrite whichever meal happened to be last. For an
ambiguous reference ask one targeted question. No whole-day replacement API.

```json
{"request_id":"message-2:append:1","date":"2026-10-13","meal_id":"meal_...","expected_revision":1,"foods":["FULL FOOD OBJECTS AS ABOVE"]}
```

`append` adds complete food objects, not names alone. The string placeholder
above must be replaced by real objects. Full replacement of one food:

```json
{"request_id":"message-3:update:1","date":"2026-10-13","meal_id":"meal_...","food_id":"food_...","expected_revision":2,"food":{"name":"replacement","quantity":1,"unit":"serving","calories":100,"protein":5,"carbs":15,"fat":2,"source":"model_estimate","portion_source":"user_description"}}
```

Portion-only correction (halve the selected item's CURRENT quantity and
nutrients, not all dishes). Preserve and reuse this request for retries:

```json
{"request_id":"message-4:update:1","date":"2026-10-13","meal_id":"meal_...","food_id":"food_...","expected_revision":3,"scale":0.5}
```

`delete`: date + meal_id + request_id, optionally food_id. Without food_id the
whole meal is deleted. Deleting the final food removes the empty meal. Missing
IDs fail rather than guessing. Same-request retry returns its old receipt.
Moving a record to another day is deliberately not an atomic operation in this
release: preserve the data, obtain explicit direction, delete then re-add with
separate IDs. Do not automate cross-day moves speculatively.

## Request IDs and failures

Prefer channel + conversation + stable incoming message ID + operation suffix.
Multiple meals in one message need distinct suffixes and explicit dates. A
retry MUST reuse the same ID and exact payload; changed content under the same
ID is rejected. A new intentional correction gets a new ID, even with identical
food names. Never deduplicate by food name, text similarity or image alone.

When a stable transport ID is unavailable, create one operation token (e.g.
init.request_token) and retain it with the pending request for retries. This
protects tool retries but CANNOT guarantee deduplication of a completely
redelivered channel message after lost context. Tell the operator that boundary;
do not silently promise exactly-once delivery across all transports.

Successful writes return ok/saved, action, meal_id, meal (null when removed),
logged_totals, coverage=recorded_only and duplicate=false. Duplicate=true is
an ORIGINAL receipt, possibly preceding later edits/deletion. Do not resurrect
it or report its totals as current; query list/summary after a duplicate.

Validation/data/IO failures return nonzero and JSON ok=false. argparse usage
errors use stderr. Never ignore the exit status. A failure after journal
preparation may be an interrupted pending transaction: retry EXACT input/ID,
not a new request. The next command recovers it before proceeding. If an
external writer changed the day during a pending transaction, recovery stops
with a conflict rather than overwriting the change. Reconcile manually.

## Storage / maintenance boundaries

One array per local date, using existing foods/calories/protein/carbs/fat names;
new IDs, revision and source metadata are additive. Complete old list records
and old macro aliases load without an eager migration; missing IDs are stable
until the next committed edit. Unknown metadata is preserved. Dictionary-root
logs, planned legacy rows or insufficient nutrient data are rejected, not
silently discarded or converted to zero. Keep backups before manual migration.

All standalone mutations serialize on one POSIX lock. A same-filesystem atomic
write and private recovery journal coordinate request receipts with the daily
file, including deletes. Previous daily bytes are saved under meals/backups.
Files are mode 0600. Symlinked ledger paths are rejected. This is not isolation
from arbitrary same-user processes: keep the workspace private and do not run
legacy writers concurrently. NFS/non-POSIX locking is not supported/tested.

Receipts/backups retain deleted information and grow over time. No automatic
purge is performed, because deleting receipts changes retry guarantees. Stop
the agent and ensure no pending journal before any manual backup/retention
maintenance. Keep whole data/ in private backups, not a public Git repository.

Meal writes remain owned by this standalone writer; coach.py now adds other domains in the same installed entry. Original nutrition-calc.py,
weekly-report and author-dependent modules are not required or installed by
the lightweight installer; their inherited problems are not magically fixed.
