#!/usr/bin/env python3
"""Extract user/assistant conversation messages from all session files within a date range.

Outputs a compact text format suitable for LLM summarization.
Skips tool calls, system prompts, thinking, model changes, etc.
Handles both .jsonl and .jsonl.reset.* files.

Usage:
    python3 extract-30d.py --session-dir /path/to/sessions --days 30 [--max-chars 150000]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone


def extract_sessions(session_dir: str, days: int, max_chars: int = 150000, extra_dirs: list = None) -> str:
    """Extract conversations from session files within date range.
    
    Args:
        session_dir: Primary session directory
        days: Number of days to look back
        max_chars: Maximum output characters
        extra_dirs: Additional directories to scan (e.g., backup session dirs)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.isoformat()

    # Find all relevant session files (not deleted)
    candidates = []
    
    # Primary session dir
    if os.path.isdir(session_dir):
        for f in os.listdir(session_dir):
            if ".deleted." in f:
                continue
            if f.endswith(".jsonl") or ".jsonl.reset." in f:
                full = os.path.join(session_dir, f)
                candidates.append(full)
    
    # Extra dirs (e.g., backup/archive dirs)
    if extra_dirs:
        for edir in extra_dirs:
            if not os.path.isdir(edir):
                continue
            for f in os.listdir(edir):
                if ".deleted." in f:
                    continue
                if f.endswith(".jsonl") or ".jsonl.reset." in f:
                    full = os.path.join(edir, f)
                    candidates.append(full)

    if not candidates:
        return "ERROR: no session files found"

    # Extract messages from all files
    all_messages = []
    skip_types = {"session", "model_change", "thinking_level_change", "custom",
                  "toolCall", "toolResult", "thinking", "summary"}

    for fpath in candidates:
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    entry_type = entry.get("type", "")
                    if entry_type in skip_types:
                        continue
                    if entry_type != "message":
                        continue

                    ts = entry.get("timestamp", "")
                    if ts and ts < cutoff_ts:
                        continue

                    # Messages can be at top level or nested in .message
                    msg = entry.get("message", entry)
                    role = msg.get("role", "") or entry.get("role", "")
                    if role not in ("user", "assistant"):
                        continue

                    text = ""
                    content = msg.get("content", "") or entry.get("content", "")
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        # Multi-part content (text + images etc)
                        parts = []
                        for part in content:
                            if isinstance(part, dict):
                                if part.get("type") == "text":
                                    parts.append(part.get("text", ""))
                                elif part.get("type") == "image_url":
                                    parts.append("[图片]")
                            elif isinstance(part, str):
                                parts.append(part)
                        text = " ".join(parts)

                    if not text or len(text.strip()) < 2:
                        continue

                    # Skip system/internal messages
                    text_stripped = text.strip()
                    if text_stripped.startswith("System:"):
                        continue
                    if text_stripped == "HEARTBEAT_OK":
                        continue
                    if text_stripped == "NO_REPLY":
                        continue
                    # Skip compaction summaries
                    if "Pre-compaction memory flush" in text_stripped:
                        continue
                    if "Session was just compacted" in text_stripped:
                        continue
                    # Skip heartbeat prompts
                    if "Read HEARTBEAT.md if it exists" in text_stripped:
                        continue

                    # Clean up metadata blocks from user messages
                    if role == "user":
                        import re
                        # Remove "Sender (untrusted metadata):" blocks (multi-line json)
                        text = re.sub(
                            r'Sender \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*',
                            '', text, flags=re.DOTALL)
                        # Remove "Conversation info (untrusted metadata):" blocks
                        text = re.sub(
                            r'Conversation info \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*',
                            '', text, flags=re.DOTALL)
                        # Remove timestamp lines like [Thu 2026-04-09 15:03 GMT+8]
                        text = re.sub(
                            r'\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+GMT[^\]]*\]\s*',
                            '', text)
                        # Remove [Inter-session message] lines
                        text = re.sub(r'\[Inter-session message\][^\n]*\n?', '', text)
                        # Remove OpenClaw runtime context blocks
                        text = re.sub(
                            r'OpenClaw runtime context \(internal\):.*?<<<END_UNTRUSTED_CHILD_RESULT>>>\s*',
                            '', text, flags=re.DOTALL)
                        text = re.sub(
                            r'OpenClaw runtime context \(internal\):.*$',
                            '', text, flags=re.DOTALL)
                        text = text.strip()
                        if not text:
                            continue

                    all_messages.append({
                        "ts": ts,
                        "role": role,
                        "text": text.strip() if role == "user" else text.strip()
                    })
        except Exception as e:
            sys.stderr.write(f"WARN: error reading {fpath}: {e}\n")
            continue

    if not all_messages:
        return "ERROR: no messages found in date range"

    # Sort by timestamp
    all_messages.sort(key=lambda m: m["ts"])

    # Format output
    output_lines = []
    total_chars = 0

    for msg in all_messages:
        ts = msg["ts"][:16].replace("T", " ")  # "2026-04-01 12:30"
        role_label = "👤" if msg["role"] == "user" else "🤖"
        # Truncate very long assistant messages (often contain skill output)
        text = msg["text"]
        if msg["role"] == "assistant" and len(text) > 500:
            text = text[:500] + "..."

        line = f"{ts} {role_label} {text}"
        if total_chars + len(line) > max_chars:
            output_lines.append(f"\n[... 截断: 已达 {max_chars} 字符上限, 还有更多消息未显示 ...]")
            break
        output_lines.append(line)
        total_chars += len(line) + 1

    header = f"# 对话记录 (最近 {days} 天)\n# 消息数: {len(all_messages)}\n# 时间范围: {all_messages[0]['ts'][:10]} ~ {all_messages[-1]['ts'][:10]}\n"
    return header + "\n".join(output_lines)


def main():
    parser = argparse.ArgumentParser(description="Extract conversations for memory consolidation")
    parser.add_argument("--session-dir", required=True, help="Path to agent's sessions directory")
    parser.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    parser.add_argument("--max-chars", type=int, default=150000, help="Max output chars (default: 150000)")
    parser.add_argument("--extra-dirs", nargs="*", help="Additional session dirs to scan (e.g., backup dirs)")
    args = parser.parse_args()

    result = extract_sessions(args.session_dir, args.days, args.max_chars, extra_dirs=args.extra_dirs)
    print(result)


if __name__ == "__main__":
    main()
