---
name: diet-tracking-analysis
version: 4.1.0
description: "Standalone meal logging with the existing vision model and an offline Python ledger. Use for food check-ins (photos/text), correcting/appending/deleting logged food, and daily or recent-seven-day intake queries. Food advice or planned meals are NOT automatically consumed intake. No meal_checkin plugin, nutrition API, cloud dashboard or rendered cards required."
metadata: {"openclaw": {"emoji": "🍽️", "requires": {"anyBins": ["uv", "python3"]}}}
---

# Standalone Diet Tracking

Help the user record meals without judgment. You are an AI assistant, not a
licensed clinician. Reply language remains governed by USER.md and agent policy.

## Hard rules

- Use the current model's native vision on the real attached images. If an image
  is not available in context, use an image-reading tool actually exposed by the
  runtime, following its schema. Never assume an `image`/`view_image` tool name,
  invent a file path, or claim to see an inaccessible attachment. Do not invoke
  another model API from Python. A text description is an acceptable fallback.
- ONLY `scripts/meal-log.py` writes the meal ledger. Do not edit daily JSON by
  hand, keep a second Markdown ledger, or call the missing `meal_checkin` tool.
- Read `references/standalone-flow.md` before the first operation in a session.
  It defines the commands, JSON contract, date handling and safe corrections.
- Native estimates are estimates. Never claim database lookup or measured
  precision. Record nutrition and portion sources separately. User-measured
  portions and readable package labels override guesses.
- Record clear check-ins immediately, without requesting redundant confirmation.
  Advice ("can I eat this?"), future plans, menus and hypothetical meals do not
  enter consumed intake. A bare ambiguous photo warrants one short question;
  established check-in context does not. Do not log an entire shared table as
  the user's consumption. Keep ordered photo/text associations and avoid
  counting multiple views of the same meal twice.
- Read the actual saved rows/IDs before a correction. Change only the specified
  food. A portion-only correction uses `scale`, not a fresh estimate of every
  dish. A new message saying "another milk" is a new intake, not a duplicate.
- Pass a stable request ID for every mutation. Reuse the EXACT request ID and
  JSON for transport/tool retries. Never replay an aborted meal with a new ID
  before checking the ledger. See fallback limitations in the reference.
- Say "recorded" only after JSON `ok: true` and `saved: true`. A failed write
  must be reported; do not substitute reassurance for persistence. A duplicate
  response is an old receipt: query `list`/`summary` for the CURRENT totals.
- First release uses plain text, no mandatory PNG, HTML, PDF, remote upload,
  mini-program or delivery script. Output a normal reply via the current channel.
- Do not activate onboarding, reminder-manager, notification-manager,
  notification-composer, weekly-report, cloud exports or product recall flows.
  This skill's `summary` handles recent-seven-day queries locally. Do not alter
  calorie targets automatically, prescribe compensatory fasting, or treat
  missing logs as zero food consumed.

## Context and execution

When installed with `scripts/install-standalone.py`, read `{baseDir}/runtime.json`
once for the actual workspace, `runner`, executable `command` argv prefix, and
timezone fallback. Use the saved `command` followed by operation/arguments; do
not replace it with bare `python`, `pip`, `uvx`, or another environment. The uv
runner uses the installed skill's project, locked dependencies and offline mode.
No daily dependency installation, lockfile update or Python download is allowed.
If uv/Python/the environment is missing, report a setup failure; do not silently
switch runners. Install/repair instructions are in the repository AI-INSTALL.md.
When upgrading a v1 runtime without `command`, reinstall with the new installer;
only an explicitly selected python fallback may use its recorded Python path.
`USER.md` Timezone takes precedence over the installer fallback. Do not infer
locale/timezone from the language of a message. Without runtime.json, use the
actual workspace provided by the runtime and its USER.md timezone; if missing,
request setup rather than guessing a production directory. Pass resolved paths
and timezone explicitly to the CLI; `{workspaceDir}` is NOT a magic variable.

Run `init` once to get local today/yesterday/recent-seven-day start and verify
write access. Read health-preferences.md when available (allergies, usual
portions). An existing confirmed PLAN.md calorie target is optional; pass it
explicitly to `summary --target-calories`, otherwise omit it. Missing profiles
or a plan must NOT prevent basic logging. Do not change those files.

Understand intent -> estimate only from actual text/images -> create structured
food items -> call the ledger -> use its totals to compose one concise reply.
All nutrient numbers supplied in an item are for the ENTERED quantity, not per
100 g. Let Python sum records. Before saving, check grams vs millilitres, raw vs
cooked assumptions, and whether oil/sauce is already included; never add oil
again to a dish estimate that already includes it. Unknown estimates require
stated assumptions, not fake certainty. Unidentifiable food is not logged as 0.

## Plain-text result

For a successful new check-in, show meal name, a compact food breakdown, estimated
meal calories/macros, today's **recorded** totals, and one brief uncertainty note
or practical observation. Source every numeric total from script output. With a
confirmed target, label the remainder "target minus recorded intake", NOT a
prescription to eat that amount. No target -> just report totals.

For a correction, acknowledge the specific change and updated totals; do not
repeat a full lecture. For a range summary, use average_per_logged_day and
logged_days; distinguish missing-record days from confirmed fasting. Never call
it total actual intake or an average of complete days unless coverage is known.

## Data ownership

This skill owns `{workspace}/data/meals/YYYY-MM-DD.json` (array of consumed
meal records), `data/meal-ledger-state.json` (request receipts + recovery journal),
`data/.meal-ledger.lock`, and `data/meals/backups/` (pre-edit copies). Only the
ledger writes them; summaries read them under the same lock. No database or
network dependencies. Backups and receipts retain deleted data until explicitly
purged by the owner; do not claim deletion securely erased all history.

Other legacy writers do NOT share this lock. Install this entry point in a
dedicated agent or explicitly exclude conflicting legacy skills/jobs. Native
vision may still send photographs to the user's configured model provider;
local ledger storage is not a promise of fully local model processing.
