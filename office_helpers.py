"""A Claude Code tab's subagents, as the office draws them (part-timers, 工讀生).

Claude Code keeps a conversation's subagents next to its transcript: for `<dir>/<S>.jsonl`,
`<dir>/<S>/subagents/` holds, per subagent, `agent-<id>.meta.json` (small: agentType,
description, model, …, and `"finished": true` once it is done) and `agent-<id>.jsonl` (its
own transcript). Only the folder is listed, the files stat'ed and the small meta files read
(cached by mtime and size); a transcript is never opened: its mtime is the activity signal.

State: written in the last WORKING_S seconds → working; silent for STUCK_S or more while not
finished → stuck; in between → idle. An unfinished one silent for ABANDONED_S is a leftover of
a conversation that ended mid-run (measured: most unfinished metas on a working machine were
weeks old) and is not shown. Pure apart from the file system; no `import main`.
"""
import json
import os
import threading
import time

import agent_status

WORKING_S = 120
STUCK_S = 600
ABANDONED_S = 6 * 3600
META_MAX = 64 * 1024        # a meta file is a few hundred bytes; anything bigger is not one
CACHE_MAX = 4000

_CACHE = {}                 # meta path → ((mtime, size), parsed dict or None)
_LOCK = threading.Lock()


def folder_of(transcript: str) -> str:
    """`<dir>/<S>.jsonl` → `<dir>/<S>/subagents` ('' for anything else)."""
    if not transcript or not str(transcript).endswith(".jsonl"):
        return ""
    return os.path.join(str(transcript)[:-len(".jsonl")], "subagents")


def _born(st) -> float:
    return float(getattr(st, "st_birthtime", 0) or st.st_ctime)


def _meta(path: str, st):
    key = (st.st_mtime, st.st_size)
    with _LOCK:
        hit = _CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    val = None
    if st.st_size <= META_MAX:
        try:
            with open(path, encoding="utf-8") as f:
                got = json.load(f)
            val = got if isinstance(got, dict) else None
        except (OSError, ValueError):
            val = None
    with _LOCK:
        if len(_CACHE) >= CACHE_MAX:
            _CACHE.clear()
        _CACHE[path] = (key, val)
    return val


def scan(folder: str, now: float = None) -> list:
    """The running subagents in `folder`, oldest first:
    [{id, description, model, agent_type, state, started_at, last_at}] (times in epoch seconds)."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(folder) if folder else []
    except OSError:
        return []
    out = []
    for name in names:
        if not (name.startswith("agent-") and name.endswith(".meta.json")):
            continue
        aid = name[len("agent-"):-len(".meta.json")]
        mpath = os.path.join(folder, name)
        try:
            mst = os.stat(mpath)
        except OSError:
            continue
        meta = _meta(mpath, mst)
        if meta is None or meta.get("finished"):
            continue
        try:
            jst = os.stat(os.path.join(folder, f"agent-{aid}.jsonl"))
        except OSError:
            jst = None
        last = max(mst.st_mtime, jst.st_mtime if jst else 0)
        quiet = now - last
        if quiet >= ABANDONED_S:
            continue
        started = min(_born(mst), _born(jst) if jst else _born(mst))
        out.append({"id": aid[:64],
                    "description": agent_status.redact(str(meta.get("description") or ""), 200),
                    "model": str(meta.get("model") or "")[:60],
                    "agent_type": str(meta.get("agentType") or "")[:60],
                    "state": "working" if quiet < WORKING_S else ("stuck" if quiet >= STUCK_S else "idle"),
                    "started_at": round(started, 3), "last_at": round(last, 3)})
    out.sort(key=lambda h: (h["started_at"], h["id"]))
    return out
