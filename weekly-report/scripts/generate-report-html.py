#!/usr/bin/env python3.11
"""
generate-report-html.py — Weekly report data generator + DB writer.

Reads collect-weekly-data.py JSON output, merges commentary/highlights/suggestions,
PUTs to data-api /v1/dashboard/user/{account_id}/weekly, writes a report log.

Historical note (2026-07-07): This script used to upload 3 files (data JSON,
weekly-data-latest.html, weekly-report.html template) to S3. Dashboard now reads
straight from MySQL weekly_reports table via data-api, so we skip S3 entirely.
The name is retained for compatibility with existing skill flow.

Usage:
    python3.11 collect-weekly-data.py ... | python3.11 generate-report-html.py \
        --output /tmp/weekly-data-2026-04-13.json --workspace-dir /path/to/workspace

Outputs the public report URL to stdout (for agent to use in message).
The URL is dashboard's card-entry (SPA + SSR-inject), unchanged by user's token.

Flags:
    --no-write   Skip DB write (local testing / dry-run)
    --no-log     Skip writing local report log (e.g., backfilling history)
"""

import argparse, json, sys, os, urllib.request, urllib.error
from datetime import datetime, timezone


def log(msg):
    print(f"[generate-report-html] {msg}", file=sys.stderr)


def _extract_account_id(workspace_dir: str) -> str:
    """workspace-wechat-dm-<accountId> → accountId. Same pattern as memory-consolidation/dispatcher.py."""
    if not workspace_dir:
        return ""
    dirname = os.path.basename(os.path.abspath(workspace_dir))
    prefix = "workspace-wechat-dm-"
    if dirname.startswith(prefix):
        return dirname[len(prefix):]
    return dirname


def _put_to_data_api(account_id: str, week_start: str, payload: dict, api_base: str, timeout: int = 15) -> bool:
    """PUT the weekly report JSON to data-api. Returns True on success."""
    url = f"{api_base.rstrip('/')}/v1/dashboard/user/{account_id}/weekly"
    body = json.dumps({"week_start": week_start, "data": payload}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            if 200 <= code < 300:
                log(f"DB write OK: {url} ({len(body)} bytes)")
                return True
            log(f"DB write returned HTTP {code}: {url}")
            return False
    except urllib.error.HTTPError as e:
        body_snippet = ""
        try:
            body_snippet = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        log(f"DB write HTTP {e.code}: {url} — {body_snippet}")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"DB write network error: {url} — {e}")
        return False


def _write_report_log(workspace_dir, data, url):
    """Write local report log JSON for cross-session reference (unchanged from pre-DB version)."""
    start_date = data.get('meta', {}).get('start_date', 'unknown')
    log_dir = os.path.join(workspace_dir, 'data', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'weekly-report-{start_date}.json')

    summary = data.get('summary', {})
    commentary = data.get('commentary', {})
    report_log = {
        'start_date': start_date,
        'end_date': data.get('meta', {}).get('end_date', ''),
        'week_number': data.get('meta', {}).get('week_number', 0),
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'url': url or '',
        'summary': {
            'logged_days': summary.get('logged_days', 0),
            'cal_avg': summary.get('cal_avg', 0),
            'cal_avg_estimated': summary.get('cal_avg_estimated', 0),
            'weight_change': summary.get('weight_change'),
            'protein_avg': summary.get('protein_avg', 0),
            'fat_avg': summary.get('fat_avg', 0),
            'carb_avg': summary.get('carb_avg', 0),
        },
        'commentary': commentary,
        'highlights': data.get('highlights', []),
        'suggestions': _normalize_suggestions(data.get('suggestions', [])),
        'nickname': data.get('nickname', ''),
        'tagline': data.get('tagline', ''),
    }

    with open(log_path, 'w') as f:
        json.dump(report_log, f, ensure_ascii=False, indent=2)
    log(f"Report log written to {log_path}")
    return log_path


def _normalize_suggestions(suggestions):
    """Ensure suggestions is a list of strings (not objects)."""
    result = []
    for s in suggestions:
        if isinstance(s, dict):
            icon = s.get('icon', '')
            title = s.get('title', '')
            desc = s.get('desc', '')
            result.append(f"{icon} {title}：{desc}" if desc else f"{icon} {title}")
        else:
            result.append(str(s))
    return result


def main():
    parser = argparse.ArgumentParser(description='Generate weekly report data JSON and write to data-api DB.')
    parser.add_argument('--output', '-o', required=True, help='Output JSON file path (local record)')
    parser.add_argument('--data-file', help='JSON data file (default: read from stdin)')
    parser.add_argument('--workspace-dir', help='Workspace dir (used to derive account_id + log)')
    parser.add_argument('--commentary', help='JSON object with section commentaries')
    parser.add_argument('--highlights', help='JSON array of highlight strings')
    parser.add_argument('--suggestions', help='JSON array of suggestion strings')
    parser.add_argument('--plan-rate', type=float, default=0.5, help='Planned weight loss rate kg/week')
    parser.add_argument('--username', help='(retained) Username; not used for URL anymore')
    parser.add_argument('--nickname', help='User nickname to display in header')
    parser.add_argument('--tagline', help='Short fun summary line for header')
    parser.add_argument('--no-write', action='store_true', help='Skip DB write (dry-run)')
    parser.add_argument('--no-log', action='store_true', help='Skip writing report log')
    parser.add_argument('--lang', default='zh', help='Report language (zh or en)')
    args = parser.parse_args()

    # ── Read input data ──
    if args.data_file:
        with open(args.data_file) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    # ── Parse optional args ──
    commentary = {}
    if args.commentary:
        try:
            commentary = json.loads(args.commentary)
        except (json.JSONDecodeError, ValueError) as e:
            log(f"ERROR: --commentary JSON parse failed: {e}")
            log(f"  Raw input: {args.commentary[:200]}")

    highlights = []
    if args.highlights:
        try:
            highlights = json.loads(args.highlights)
        except (json.JSONDecodeError, ValueError) as e:
            log(f"ERROR: --highlights JSON parse failed: {e}")
            log(f"  Raw input: {args.highlights[:200]}")

    suggestions = []
    if args.suggestions:
        try:
            suggestions = json.loads(args.suggestions)
        except (json.JSONDecodeError, ValueError) as e:
            log(f"ERROR: --suggestions JSON parse failed: {e}")
            log(f"  Raw input: {args.suggestions[:200]}")

    if not isinstance(commentary, dict):
        log(f"WARNING: commentary is not a dict (got {type(commentary).__name__}), resetting to empty")
        commentary = {}
    if not commentary or not any(commentary.values()):
        log("WARNING: commentary is empty — report will show without section analysis")
    if not highlights:
        log("WARNING: highlights is empty — report will use fallback text")
    if not suggestions:
        log("WARNING: suggestions is empty — report will use fallback text")

    data['commentary'] = commentary
    data['highlights'] = highlights
    data['suggestions'] = _normalize_suggestions(suggestions)
    data['nickname'] = args.nickname or ''
    data['tagline'] = args.tagline or ''
    data['plan_rate'] = args.plan_rate
    data['username'] = args.username or 'unknown'
    data.setdefault('meta', {})['lang'] = args.lang

    # ── Auto-detect workspace from output path if not provided ──
    # output is typically {workspace}/data/reports/weekly-data-{date}.json
    workspace_dir = args.workspace_dir
    if not workspace_dir:
        candidate = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(args.output))))
        if os.path.isdir(os.path.join(candidate, 'data')):
            workspace_dir = candidate
            log(f"Auto-detected workspace: {workspace_dir}")

    # ── Write local output JSON (kept for audit + prior-week self-read) ──
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    out_size = os.path.getsize(args.output)
    log(f"Written {out_size} bytes to {args.output}")

    # ── PUT to data-api ──
    # DATA_API_URL priority: env override > default (127.0.0.1:3100 same-host convention)
    api_base = os.environ.get("DATA_API_URL", "http://127.0.0.1:3100")
    account_id = _extract_account_id(workspace_dir) if workspace_dir else ""
    start_date = data.get('meta', {}).get('start_date', '')

    db_ok = False
    if args.no_write:
        log("DB write skipped (--no-write)")
    elif not account_id:
        log("WARNING: could not derive account_id from workspace-dir, DB write skipped")
    elif not start_date:
        log("WARNING: no start_date in payload.meta, DB write skipped")
    else:
        db_ok = _put_to_data_api(account_id, start_date, data, api_base)

    # ── Build report URL ──
    # Dashboard 卡片入口(会通过 wx-login 得到 dashboardPath 决定灰度 / vs /test/)。
    # skill 只输出无 token 的裸 URL,notification-composer / cron 拼消息时签 token 加 &token=...
    # DASHBOARD_URL 可 env 覆盖(测试服/本机 dev 用)。
    dashboard_url = os.environ.get(
        "DASHBOARD_URL",
        "https://nanorhino-openclaw-user-dashboard.chatlingxi.com",
    ).rstrip("/")
    week_query = f"?week={start_date}" if start_date else ""
    report_url = f"{dashboard_url}/api/weekly{week_query}"

    # ── Write local report log ──
    if not args.no_log and workspace_dir:
        _write_report_log(workspace_dir, data, report_url)
    elif args.no_log:
        log("Report log skipped (--no-log)")

    # ── Output URL to stdout ──
    # 即便 DB 写入失败也 fall through 返回本地 output path,让 agent 至少看到有回执
    if db_ok:
        print(report_url)
    else:
        print(os.path.abspath(args.output))


if __name__ == '__main__':
    main()
