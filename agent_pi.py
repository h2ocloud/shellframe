"""Pi (pi-coding-agent): its session JSONL as conversation turns.

The session file is the one agent_status already finds for a pi tab's model badge
(`_pi_session_file`: the newest file in the tab's cwd slug, created after its process
started). Records are `{"type": "message", "message": {"role", "content", "timestamp"}}`:
a user's text, the assistant's text and toolCall blocks in order, and toolResult
messages. Thinking blocks are left out, as they are for Claude. The turns have the
shape every other CLI's do (agent_status): what sfctl `conversation` returns, so the
phone's chat view and the desktop's read the same thing.
"""

import json
import os

import agent_status

TAIL_BYTES = 4 * 1024 * 1024


def _ts(record, message):
    v = message.get("timestamp")
    if isinstance(v, (int, float)) and v:
        return v / 1000.0 if v > 1e11 else float(v)
    return agent_status._parse_iso(record.get("timestamp"))


def norm(record):
    """One session record → a list of turns (possibly empty)."""
    if not isinstance(record, dict) or record.get("type") != "message":
        return []
    m = record.get("message") if isinstance(record.get("message"), dict) else {}
    role, ts = m.get("role"), _ts(record, m)
    content = m.get("content")
    if role == "user":
        text = agent_status._content_text(content)
        return [{"kind": "user_msg", "ts": ts, "text": text}] if text else []
    if role != "assistant":
        return []                                   # toolResult: the call already shows
    if m.get("errorMessage"):                       # the model call failed; pi retries and writes each
        return [{"kind": "error", "ts": ts, "text": agent_status.redact(m["errorMessage"], 160)}]
    out = []
    for c in content if isinstance(content, list) else []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text" and (c.get("text") or "").strip():
            out.append({"kind": "assistant_text", "ts": ts, "text": c["text"].strip()})
        elif c.get("type") == "toolCall":
            name = str(c.get("name") or "tool")
            out.append({"kind": "tool_call", "ts": ts, "tool": name, "text": "",
                        "target": agent_status._target(name, c.get("arguments"))})
    return out


def turns(worker, limit=120):
    """The last `limit` turns of the tab's pi session, oldest first; [] when there is none."""
    path = agent_status._pi_session_file(worker)
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]                           # the first line was cut by the window
    out = []
    for line in lines:
        try:
            out.extend(norm(json.loads(line)))
        except (ValueError, TypeError):
            continue
    return out[-max(1, int(limit)):]
