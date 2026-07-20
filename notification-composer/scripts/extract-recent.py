#!/usr/bin/env python3
"""extract-recent.py — 取用户「最近 N 轮」干净对话 (CLI 包装, 走 data-api)。

数据源 = data-api 的 /v1/conversation/recent 端点, 底层查 chat-logger 的 messages
表 —— 即 reply-filter 投递之后、真正发给用户的干净文本。**不是** session transcript
jsonl (那个 assistant 文本里混着内部 ReAct 推理 + MEDIA/工具串, 直接喂 cron 会误导
agent 并二次泄露)。轮次口径 / 清洗 / 截断都在 data-api 端做, 本脚本只是薄封装,
供调试和 inject-context 复用。cron 主路径由 pre-send-check.py 直接调该端点。

用法:
  python3 extract-recent.py --account <accId> [--turns 10] [--max-tokens 6000] [--api http://127.0.0.1:3100]
  python3 extract-recent.py --workspace-dir /path/to/workspace-wechat-dm-<acc> [--turns 10]

输出:
  stdout = 紧凑对话文本 (成功); 空输出 = 没有可用对话 / 调用失败 (静默, exit 0)。
  设计为「永不因抽取失败而中断」——找不到就返回空。
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

# 2026-07-01 重命名过渡:新名 DATA_API_URL 优先,老名 LIFECYCLE_API_URL 兼容
DEFAULT_API = os.environ.get("DATA_API_URL") or os.environ.get("LIFECYCLE_API_URL", "http://127.0.0.1:3100")


def log(msg):
    sys.stderr.write(f"[extract-recent] {msg}\n")


def _account_from_workspace(workspace_dir):
    """workspace-wechat-dm-<acc> → <acc>。"""
    base = os.path.basename(os.path.normpath(workspace_dir)).replace("workspace-", "")
    for prefix in ("wechat-dm-", "wecom-dm-"):
        if base.startswith(prefix):
            return base[len(prefix):]
    return base


def fetch_recent(account_id, turns=10, max_tokens=6000, api=DEFAULT_API):
    """返回最近 N 轮对话紧凑文本;失败或无内容返回空串。"""
    if not account_id:
        return ""
    try:
        qs = urllib.parse.urlencode({"turns": turns, "maxTokens": max_tokens})
        url = f"{api}/v1/conversation/recent/{account_id}?{qs}"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError) as e:
        log(f"调用失败 for {account_id}: {e}")
        return ""
    return (data.get("text") or "").strip()


def main():
    parser = argparse.ArgumentParser(description="取最近 N 轮干净对话 (走 data-api)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--account", help="account id (accXXXX...)")
    src.add_argument("--workspace-dir", help="用户 workspace 根目录, 自动推导 account")
    parser.add_argument("--turns", type=int, default=10, help="最近几轮 (assistant 回复数), 默认 10")
    parser.add_argument("--max-tokens", type=int, default=6000,
                        help="本地估算 token 上限, 默认 6000")
    parser.add_argument("--api", default=DEFAULT_API, help="data-api base url")
    args = parser.parse_args()

    account_id = args.account or _account_from_workspace(args.workspace_dir)
    out = fetch_recent(account_id, turns=args.turns, max_tokens=args.max_tokens, api=args.api)
    if out:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
