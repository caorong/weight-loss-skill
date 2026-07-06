#!/usr/bin/env python3
"""extract-recent.py — 抽取用户「最近 N 轮」真实对话，供 cron 提醒承接上下文。

背景:
  isolated cron 每个 job 跑在独立 session(`agent:<id>:cron:<jobId>`),看不到
  用户在 direct channel session(`agent:<id>:...:direct:<acc>`)里的真实对话。
  本脚本从 direct session transcript 抽最近 N 轮 user/assistant 文本,清洗掉
  元数据/工具噪声,拼成紧凑文本注入到 cron agent 的输入,让提醒能承接最近对话。

轮次定义:
  用户口径「agent 每回复一次算一轮」。因此按 assistant 消息计数——从最新往回,
  数到第 N 条 assistant 消息为止,保留区间内所有 user/assistant 文本。

用法:
  # 方式一: 给 workspace-dir,自动推导 direct session 文件 (pre-send-check 用这个)
  python3 extract-recent.py --workspace-dir /path/to/workspace-wechat-dm-<acc> [--turns 10] [--max-chars 1200]

  # 方式二: 直接给 session 文件
  python3 extract-recent.py --session-file /path/to/<sid>.jsonl [--turns 10]

  # 方式三: 给 sessions 目录,自动挑 :direct: session
  python3 extract-recent.py --session-dir /path/to/agents/<id>/sessions [--turns 10]

输出:
  stdout = 紧凑对话文本 (成功);
  空输出 + exit 0 = 没有可用对话 (调用方应静默跳过, 不注入);
  exit != 0 只用于参数错误。
  设计为「永不因抽取失败而阻断提醒」——找不到就返回空。
"""

import argparse
import json
import os
import re
import sys

# --- 与 memory-consolidation/extract-30d.py 对齐的过滤规则 ---
SKIP_TYPES = {"session", "model_change", "thinking_level_change", "custom",
              "custom_message", "toolCall", "toolResult", "thinking", "summary"}


def log(msg):
    sys.stderr.write(f"[extract-recent] {msg}\n")


def _derive_direct_session_file(workspace_dir=None, session_dir=None):
    """从 workspace-dir 或 sessions 目录定位 direct channel session 的 transcript 文件。

    路径约定 (在服务器上核实过):
      {STATE_DIR}/workspace-wechat-dm-<acc>   ← workspace
      {STATE_DIR}/agents/wechat-dm-<acc>/sessions/sessions.json  ← session store

    session store 里 direct session 的 key 形如:
      agent:wechat-dm-<acc>:wechat:default:direct:<acc>
    我们按 `:direct:` 子串且不含 `:cron:` 来匹配 (不硬编码完整格式,防 channel 前缀变化),
    取 updatedAt 最新的一条。
    """
    if session_dir is None and workspace_dir is not None:
        workspace_dir = os.path.normpath(workspace_dir)
        state_dir = os.path.dirname(workspace_dir)
        base = os.path.basename(workspace_dir)
        if not base.startswith("workspace-"):
            log(f"workspace-dir 不含 workspace- 前缀: {workspace_dir}")
            return None
        agent_id = base[len("workspace-"):]
        session_dir = os.path.join(state_dir, "agents", agent_id, "sessions")

    if not session_dir or not os.path.isdir(session_dir):
        log(f"sessions 目录不存在: {session_dir}")
        return None

    store_path = os.path.join(session_dir, "sessions.json")
    if not os.path.exists(store_path):
        log(f"sessions.json 不存在: {store_path}")
        return None

    try:
        with open(store_path, encoding="utf-8") as f:
            store = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log(f"读 sessions.json 失败: {e}")
        return None

    best = None  # (updatedAt, sessionFile)
    for key, entry in store.items():
        if not isinstance(entry, dict):
            continue
        if ":direct:" not in key or ":cron:" in key:
            continue
        sf = (entry.get("sessionFile") or "").strip()
        if not sf:
            sid = entry.get("sessionId")
            if sid:
                sf = os.path.join(session_dir, f"{sid}.jsonl")
        if not sf or not os.path.exists(sf):
            continue
        updated = entry.get("updatedAt", 0) or 0
        if best is None or updated > best[0]:
            best = (updated, sf)

    if best is None:
        log("未找到可用的 direct session transcript")
        return None
    return best[1]


def _clean_user_text(text):
    """剥掉 user 消息里的元数据/运行时噪声块 (与 extract-30d 一致)。"""
    # 媒体附件行 `[media attached: /abs/path (image/png) | /abs/path]` → [图片]
    text = re.sub(r'\[media attached:[^\]]*\]', '[图片]', text)
    # 冗余的「[用户发送了图片]」占位与 [图片] 合并去重
    text = re.sub(r'\[用户发送了图片\]', '', text)
    text = re.sub(
        r'Sender \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*',
        '', text, flags=re.DOTALL)
    text = re.sub(
        r'Conversation info \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*',
        '', text, flags=re.DOTALL)
    text = re.sub(
        r'\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+GMT[^\]]*\]\s*',
        '', text)
    text = re.sub(r'\[Inter-session message\][^\n]*\n?', '', text)
    text = re.sub(
        r'OpenClaw runtime context \(internal\):.*?<<<END_UNTRUSTED_CHILD_RESULT>>>\s*',
        '', text, flags=re.DOTALL)
    text = re.sub(
        r'OpenClaw runtime context \(internal\):.*$',
        '', text, flags=re.DOTALL)
    # 合并连续/重复的 [图片] 标记与多余空白
    text = re.sub(r'(?:\s*\[图片\]\s*)+', ' [图片] ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()


def _extract_text(msg):
    """从 message.content 抽纯文本 (content 可为 str 或 block 列表)。"""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif part.get("type") in ("image_url", "image"):
                    parts.append("[图片]")
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)
    return ""


def _is_noise(role, text):
    """跳过系统/内部/占位消息。"""
    if not text or len(text.strip()) < 2:
        return True
    t = text.strip()
    if t.startswith("System:"):
        return True
    if t in ("HEARTBEAT_OK", "NO_REPLY", "NO_REPLY."):
        return True
    if "Pre-compaction memory flush" in t or "Session was just compacted" in t:
        return True
    if "Read HEARTBEAT.md if it exists" in t:
        return True
    # assistant 发媒体时的裸路径行 (MEDIA:/... 或 光秃秃的 xxx.png)
    if role == "assistant":
        if t.startswith("MEDIA:"):
            return True
        if re.fullmatch(r'[\w\-./]+\.(?:png|jpg|jpeg|webp|gif)', t):
            return True
    return False


def extract_recent(session_file, turns=10, max_chars=1200):
    """返回最近 N 轮对话的紧凑文本;无内容返回空串。"""
    if not session_file or not os.path.exists(session_file):
        return ""

    messages = []  # {ts, role, text}
    try:
        with open(session_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") in SKIP_TYPES:
                    continue
                if entry.get("type") != "message":
                    continue
                msg = entry.get("message", entry)
                role = msg.get("role", "") or entry.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                text = _extract_text(msg)
                if role == "user":
                    text = _clean_user_text(text)
                if _is_noise(role, text):
                    continue
                messages.append({
                    "ts": entry.get("timestamp", ""),
                    "role": role,
                    "text": text.strip(),
                })
    except OSError as e:
        log(f"读 transcript 失败: {e}")
        return ""

    if not messages:
        return ""

    # 按时间排序 (transcript 通常已有序, 但 timestamp 排序更稳)
    messages.sort(key=lambda m: m["ts"])

    # 从尾部往前取 N 轮: assistant 每条 = 1 轮边界
    assistant_seen = 0
    start_idx = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "assistant":
            assistant_seen += 1
            if assistant_seen >= turns:
                start_idx = i
                break
    recent = messages[start_idx:]

    # 拼输出, 从最新往回填直到字数上限, 再翻正序保证时间顺序
    lines_rev = []
    total = 0
    for m in reversed(recent):
        label = "用户" if m["role"] == "user" else "你"
        text = m["text"]
        # assistant 的长输出 (常含 skill 结果) 截断
        if m["role"] == "assistant" and len(text) > 240:
            text = text[:240] + "…"
        elif len(text) > 400:
            text = text[:400] + "…"
        line = f"{label}: {text}"
        if total + len(line) > max_chars:
            break
        lines_rev.append(line)
        total += len(line) + 1

    if not lines_rev:
        return ""
    return "\n".join(reversed(lines_rev))


def main():
    parser = argparse.ArgumentParser(description="抽取最近 N 轮对话供 cron 承接上下文")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--workspace-dir", help="用户 workspace 根目录, 自动推导 direct session")
    src.add_argument("--session-file", help="直接指定 session transcript jsonl")
    src.add_argument("--session-dir", help="sessions 目录, 自动挑 :direct: session")
    parser.add_argument("--turns", type=int, default=10, help="最近几轮 (assistant 回复数), 默认 10")
    parser.add_argument("--max-chars", type=int, default=1200, help="输出字数上限, 默认 1200")
    args = parser.parse_args()

    session_file = args.session_file
    if not session_file:
        session_file = _derive_direct_session_file(
            workspace_dir=args.workspace_dir, session_dir=args.session_dir)

    out = extract_recent(session_file, turns=args.turns, max_chars=args.max_chars)
    if out:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
