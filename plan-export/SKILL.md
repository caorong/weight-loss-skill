---
name: plan-export
version: 2.2.0
description: "Renders Markdown plans (PLAN.md / MEAL-PLAN.md / EXERCISE-PLAN.md) to styled HTML pages with presigned URLs, or PDF. Trigger when a planner skill finishes generating a plan, or the user asks for their plan link ('我的计划链接') / the old link expired. Channel whitelist applies — non-listed channels (wechat) get inline text from the calling skill."

metadata:
  openclaw:
    emoji: "page_facing_up"
---

# Plan Export (HTML Web / PDF)


Convert Markdown plans into professionally styled documents and deliver them to users.

**Primary mode:** HTML web page uploaded to cloud storage — generates a beautiful, mobile-friendly web page and provides a permanent URL.

**Note:** `--bucket` is required. Always pass `--bucket nanorhino-im-plans` (or the appropriate bucket name) when calling the script.

## Channel-Based Export Policy

Not all channels need URL export. Read `{baseDir}/config.json` to get the `urlExportChannels` list, then read the agent workspace's `channel-source.json` to determine the user's channel.

- **If the user's channel is in `urlExportChannels`**: Generate HTML, upload, and send the URL to the user.
- **If the user's channel is NOT in the list**: Skip URL export entirely. The calling skill (weight-loss-planner / meal-planner) already presented the plan as inline text — no additional action needed.

When called by another skill, **always check channel first**. If the channel doesn't need URL export, return silently — do not generate HTML, do not upload, do not send any message.

## Supported Document Types

| Type | Input File | Template | Key | HTML Script |
|---|---|---|---|---|
| Weight Loss Plan | `PLAN.md` | (default) | `weight-loss-plan` | `generate-html.py` |
| Meal Plan | `MEAL-PLAN.md` | `meal-plan` | `meal-plan` | `generate-meal-plan-html.py` |
| Exercise Plan | `EXERCISE-PLAN.md` | `exercise-plan` | `exercise-plan` | `generate-exercise-plan-html.py` |

## Primary Mode: HTML + Cloud Storage Presigned URL

### Storage Backend (AWS S3)

Uploads go to AWS S3 only.

**Prerequisites:**
- S3 bucket with 30-day lifecycle rule (auto-deletion)
- AWS CLI credentials via standard mechanisms (IAM role, env vars `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or `~/.aws/credentials`) with `s3:PutObject` + `s3:GetObject` permission
- Default bucket: `nanorhino-im-plans` (us-west-1)

### How to Use

```bash
# Weight loss plan
URL=$(bash {baseDir}/scripts/generate-and-send.sh \
  --agent <YOUR_AGENT_ID> \
  --input PLAN.md \
  --workspace <AGENT_WORKSPACE_PATH> \
  --key weight-loss-plan)

# Meal plan (diet template — generated during onboarding)
URL=$(bash {baseDir}/scripts/generate-and-send.sh \
  --agent <YOUR_AGENT_ID> \
  --input MEAL-PLAN.md \
  --workspace <AGENT_WORKSPACE_PATH> \
  --key meal-plan)

# 7-day meal plan (detailed weekly plan — only if user requests)
URL=$(bash {baseDir}/scripts/generate-and-send.sh \
  --agent <YOUR_AGENT_ID> \
  --input MEAL-PLAN.md \
  --workspace <AGENT_WORKSPACE_PATH> \
  --template meal-plan \
  --key meal-plan)
```

Parameters:
- `--agent` (required): Your agent ID (e.g., `007-zhuoran`)
- `--input` (required): Path to the Markdown file
- `--bucket` (optional): Storage bucket name. Defaults to `nanorhino-im-plans`.
- `--workspace` (required): Agent workspace path. Used to write `plan-url.json` and to **auto-resolve the username** for the S3 key path (workspace dir → agentId → `agent-registry.json` shortId). No need to pass `--username` manually.
- `--template` (optional): `meal-plan` for meal plan HTML. Omit for default (weight loss plan).
- `--key` (required for HTML mode): Document key, used in both S3 path and `plan-url.json` (e.g., `weight-loss-plan`, `meal-plan`)

The script outputs the public URL to stdout. **You are responsible for sending this URL to the user using the message tool** (works with any channel: Slack, Telegram, etc.).

### After Sending

Tell the user their plan is ready and include the link. Example:

> "你的计划已经生成好了！点击这里查看：[链接] 📄 有什么问题随时问我！"

### When PLAN.md or MEAL-PLAN.md is Updated

**Whenever a plan file is modified by any skill**, **always re-run the upload script** to push the new version to cloud storage. The file is uploaded to the same S3 key (`{username}/{key}.html`), so the public URL stays the same. You only need to send the link once; subsequent updates are reflected automatically at the same URL.

### plan-url.json (Multi-Document)

When `--workspace` and `--key` are provided, the script writes/merges into `plan-url.json`:

```json
{
  "weight-loss-plan": {
    "url": "https://nanorhino.ai/zhuoran/weight-loss-plan.html",
    "uploaded_at": "2026-03-09T04:15:00Z"
  },
  "meal-plan": {
    "url": "https://nanorhino.ai/zhuoran/meal-plan.html",
    "uploaded_at": "2026-03-09T07:00:00Z"
  }
}
```

Each key is updated independently — uploading a new meal plan doesn't affect the weight loss plan entry.

**When a user asks for their plan link:**
1. Read `plan-url.json` → find the relevant key
2. Send the existing `url` — URLs are permanent (no expiry)
3. If the plan content has been updated since last upload, re-run the script to push the new version (URL stays the same)

## SMS/MMS Plan Card → see the `plan-card` skill

The deterministic MMS plan-card pipeline (handoff profile JSON → branded
plan card PNG + PLAN.md markdown) lives in the dedicated **`plan-card`**
skill: the script is `plan-card/scripts/plan-to-image.py` — this is the
path the openclaw-infra Twilio extension must reference in its deploy
config (`planImage.scriptPath`). Plan content follows the canonical Step-3
spec in `user-onboarding-profile/SKILL.md` and all math comes from
`weight-loss-planner/scripts/planner-calc.py forward-calc`. Dependencies
are declared in `plan-card/requirements.txt`.

This skill (plan-export) only converts already-written Markdown plans to
HTML/PDF and delivers them.

## Individual Scripts (Advanced)

### Generate HTML only (weight loss plan)
```bash
python3 {baseDir}/scripts/generate-html.py <input.md> [output.html]
```

### Generate HTML only (meal plan)
```bash
python3 {baseDir}/scripts/generate-meal-plan-html.py <input.md> [output.html]
```

The meal plan script expects Markdown in the schema defined at `meal-planner/references/meal-plan-schema.md`.

### Generate PDF only
```bash
bash {baseDir}/scripts/generate-pdf.sh <input.md> [output.pdf]
```

### Upload to cloud storage only
```bash
bash {baseDir}/scripts/upload-to-s3.sh \
  --file <path.html> \
  --bucket <name> \
  --key <document-key> \
  --workspace <path> \
  [--base-url <url>]
```

## Notes

- Weight loss plan HTML: blue theme, tables, Inter + Noto Sans SC fonts
- Meal plan HTML: green theme, day-card layout, responsive, print-friendly
- PDF mode uses WeasyPrint (Python) — no Chrome/browser dependency
- All formats support Chinese, English, and mixed-language content
- Cloud storage objects: set lifecycle rule on bucket for auto-cleanup (optional)
- Public URLs are permanent (`{base-url}/user/{shortId}/{key}.html`), served via Cloudflare Worker
- Same S3 key is overwritten on each upload — URL stays stable
- Agent-to-Slack-user mapping (PDF fallback) is auto-resolved from `openclaw.json` bindings
