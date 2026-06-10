"""
agent_status — 自動偵測每個 tab 的 agent 狀態（不靠 agent 自報 [[SF:...]]）。

P1：獨立、可測試的 server 端模組。main.py 在 P2 才接線。
設計原則：
  - 不 import main.py（解耦，純資料進出）。
  - 全部對外 API 包 try/except，**偵測失敗回 None / 'unknown'，永遠不可拋例外影響終端**。
  - transcript 只 seek 檔尾增量讀，500ms 等級成本。

對外 API：
  resolve_transcript(worker)        tab → transcript 路徑（codex=lsof, claude=session-id/newest-mtime）
  StatusTracker().status_for(...)   算某 tab 的 {state, dot, activity, task}

worker 是個 dict（main.py 用 Session 屬性填）：
  {sid, cmd, cwd, tmux_name, session_id(optional)}

狀態機與解析邏輯同 docs/poc/agent_state_detector.py（已對真實 log 驗證）。
"""
from __future__ import annotations

import json
import os
import glob
import time
import subprocess

# ── 狀態機門檻（秒）──
WORKING_FRESH_S = 8
STUCK_IDLE_S = 180        # turn 未結束又超過 180s 全無事件(無 error/spinner/pending tool) → 真的卡住
DONE_QUIET_S = 3

DOT = {"working": "sig-working", "decision": "sig-decision",
       "done": "sig-done", "stuck": "sig-stuck", "idle": "", "unknown": ""}

VERB = {
    "Read": "Reading", "Edit": "Editing", "Write": "Writing", "NotebookEdit": "Editing",
    "Bash": "Running", "Grep": "Searching", "Glob": "Searching", "Task": "Delegating",
    "WebFetch": "Fetching", "WebSearch": "Searching", "AskUserQuestion": "Asking",
    "TodoWrite": "Planning",
    "exec_command": "Running", "shell": "Running", "apply_patch": "Editing",
    "read_file": "Reading", "image_generation_call": "Generating image", "mcp": "Calling",
}

CLAUDE_PROJECTS = os.path.expanduser("~/.claude/projects")
CODEX_SESSIONS = os.path.expanduser("~/.codex/sessions")


# ────────────────────────── tab → transcript 對應 ──────────────────────────

def _worker_kind(cmd: str) -> str:
    c = (cmd or "").lower()
    if "codex" in c:
        return "codex"
    if "claude" in c:
        return "claude"
    return "other"


def _cwd_slug(cwd: str) -> str:
    """Claude Code 的 project 目錄 slug：把 / 與 . 換成 -。"""
    p = os.path.realpath(os.path.expanduser(cwd or "~"))
    return p.replace("/", "-").replace(".", "-")


def _tmux_pane_pid(tmux_name: str):
    if not tmux_name:
        return None
    try:
        r = subprocess.run(["tmux", "list-panes", "-t", tmux_name,
                            "-F", "#{pane_pid}"], capture_output=True,
                           timeout=2, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def _pid_tree(root_pid: int, depth=2):
    """root + 子孫 pid（限定深度，避免無限）。"""
    pids = [root_pid]
    frontier = [root_pid]
    for _ in range(depth):
        nxt = []
        for p in frontier:
            try:
                r = subprocess.run(["pgrep", "-P", str(p)], capture_output=True,
                                   timeout=2, text=True)
                kids = [int(x) for x in r.stdout.split()] if r.returncode == 0 else []
            except Exception:
                kids = []
            nxt.extend(kids)
            pids.extend(kids)
        frontier = nxt
    return pids


def _lsof_open_jsonl(pids, name_contains: str):
    """在 pid 樹中找開啟的、路徑含 name_contains 的 .jsonl（codex 用）。"""
    for p in pids:
        try:
            r = subprocess.run(["lsof", "-p", str(p)], capture_output=True,
                               timeout=3, text=True)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            path = line.split()[-1] if line.split() else ""
            if path.endswith(".jsonl") and name_contains in path:
                return path
    return None


def _proc_start_epoch(pid):
    """Process start time as epoch seconds (macOS `ps -o lstart`)."""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=2)
        s = r.stdout.strip()
        if not s:
            return None
        from datetime import datetime
        return datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp()
    except Exception:
        return None


def _nearest_birth_jsonl(pattern: str, target_epoch: float, max_delta=900):
    """The transcript whose file birth time is closest to target_epoch (a tab's
    claude process start). Each claude session creates its .jsonl at launch, so
    matching process-start↔file-birth maps each tab to its OWN transcript even
    when many share the $HOME slug — unlike newest-mtime, which converges on the
    one currently-active file. Returns None if nothing is within max_delta."""
    best, best_d = None, max_delta
    for f in glob.glob(pattern):
        try:
            b = os.stat(f).st_birthtime
        except (OSError, AttributeError):
            continue
        d = abs(b - target_epoch)
        if d < best_d:
            best, best_d = f, d
    return best


def _newest_jsonl(pattern: str, within_s=3600):
    cutoff = time.time() - within_s
    best, best_m = None, 0.0
    for f in glob.glob(pattern):
        try:
            m = os.path.getmtime(f)
        except OSError:
            continue
        if m < cutoff:
            continue
        if m > best_m:
            best, best_m = f, m
    return best


def resolve_transcript(worker: dict):
    """tab → transcript 路徑。失敗回 None。
    worker: {cmd, cwd, tmux_name, session_id(optional)}
    """
    try:
        kind = _worker_kind(worker.get("cmd", ""))
        if kind == "codex":
            # codex 持續持有 rollout fd → lsof 直接命中（最可靠）
            pane = _tmux_pane_pid(worker.get("tmux_name"))
            if pane:
                hit = _lsof_open_jsonl(_pid_tree(pane), "/.codex/sessions/")
                if hit:
                    return hit
            # fallback：整棵 sessions 樹取最新 mtime
            return _newest_jsonl(os.path.join(CODEX_SESSIONS, "*/*/*/rollout-*.jsonl"))
        if kind == "claude":
            # ONLY map when we have a confident, deterministic id (P3 spawn with
            # --session-id). The newest-mtime guess is deliberately NOT used as a
            # fallback: tabs sharing the $HOME slug would all resolve to whichever
            # transcript was written last, so every idle tab would inherit the one
            # active session's state and falsely show "working". When unmapped we
            # return None → status 'unknown' → the browser per-tab heuristic (which
            # reads each tab's own terminal) drives the dot, which is accurate.
            slug = _cwd_slug(worker.get("cwd", "~"))
            sid = worker.get("session_id")
            if sid:  # P3: deterministic mapping for newly spawned tabs
                p = os.path.join(CLAUDE_PROJECTS, slug, f"{sid}.jsonl")
                if os.path.exists(p):
                    return p
            # Existing tabs (spawned before --session-id): map by matching the
            # tab's claude process start time to the nearest transcript birth
            # time. Distinct per tab, so no false "all show the active one".
            pane = _tmux_pane_pid(worker.get("tmux_name"))
            if pane:
                start = _proc_start_epoch(pane)
                if start:
                    return _nearest_birth_jsonl(
                        os.path.join(CLAUDE_PROJECTS, slug, "*.jsonl"), start)
            return None
    except Exception:
        return None
    return None


# ────────────────────────── JSONL 解析（normalize）──────────────────────────

def _target(tool, inp):
    if not isinstance(inp, dict):
        return ""
    for k in ("file_path", "path", "notebook_path"):
        if inp.get(k):
            return os.path.basename(str(inp[k]))
    if inp.get("pattern"):
        return str(inp["pattern"])[:30]
    for k in ("command", "cmd", "query", "description", "prompt"):
        if inp.get(k):
            return str(inp[k]).strip().splitlines()[0][:40] if str(inp[k]).strip() else ""
    return ""


def _parse_iso(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _epoch(v):
    try:
        return float(v)
    except Exception:
        return None


def _content_text(content):
    """Pull plain text out of a Claude message content (str or block list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                parts.append(c["text"])
            elif isinstance(c, str):
                parts.append(c)
        return " ".join(parts).strip()
    return ""


def _norm_claude(o):
    t = o.get("type")
    ts = _parse_iso(o.get("timestamp"))
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    if t == "user":
        content = m.get("content")
        if isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return {"kind": "tool_result", "ts": ts}
        return {"kind": "user_msg", "ts": ts, "text": _content_text(content)}
    if t == "assistant":
        sr = m.get("stop_reason")
        out = None
        for c in (m.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "tool_use":
                name = c.get("name")
                ev = {"kind": "tool_call", "ts": ts, "tool": name,
                      "target": _target(name, c.get("input"))}
                if name == "AskUserQuestion":
                    ev["kind"] = "decision_req"
                out = ev
        if sr == "end_turn":
            return {"kind": "turn_end", "ts": ts}
        if out:
            return out
        return {"kind": "assistant_text", "ts": ts, "text": _content_text(m.get("content"))}
    return None


def _norm_codex(o):
    outer = o.get("type")
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    pt = p.get("type")
    if outer == "event_msg":
        if pt in ("task_started", "turn_started", "user_message"):
            return {"kind": "user_msg", "ts": _epoch(p.get("started_at"))}
        if pt in ("task_complete", "turn_complete"):
            return {"kind": "turn_end", "ts": _epoch(p.get("completed_at"))}
        if pt in ("exec_approval_request", "apply_patch_approval_request"):
            cmd = " ".join(p.get("command", []) or []) if p.get("command") else "patch"
            return {"kind": "decision_req", "ts": None, "target": cmd[:40]}
        if pt in ("error", "stream_error"):
            return {"kind": "error", "ts": None, "text": str(p.get("message", ""))[:60]}
        if pt == "mcp_tool_call_begin":
            return {"kind": "tool_call", "ts": None, "tool": "mcp",
                    "target": str(p.get("invocation", ""))[:40]}
    if outer == "response_item":
        if pt == "function_call":
            name = p.get("name", "")
            try:
                args = json.loads(p.get("arguments") or "{}")
            except Exception:
                args = {}
            return {"kind": "tool_call", "ts": None, "tool": name,
                    "target": _target(name, args)}
        if pt == "function_call_output":
            return {"kind": "tool_result", "ts": None}
        if pt in ("message", "reasoning"):
            return {"kind": "assistant_text", "ts": None}
        if pt == "image_generation_call":
            return {"kind": "tool_call", "ts": None, "tool": "image_generation_call", "target": ""}
    return None


def _detect_format(first_line):
    try:
        o = json.loads(first_line)
    except Exception:
        return None
    if o.get("type") in ("session_meta", "response_item", "event_msg", "turn_context"):
        return "codex"
    if "sessionId" in o or o.get("type") in ("user", "assistant", "system", "summary"):
        return "claude"
    return None


def _read_tail_events(path, tail_bytes=262144, max_records=300):
    """只讀檔尾 tail_bytes，避免整檔讀（codex log 可達 GB）。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()  # 丟掉被切半的第一行
            raw = f.read()
        # 第一行格式判斷需檔頭；單獨讀一次
        with open(path, "r", errors="replace") as fh:
            head = fh.readline()
    except Exception as e:
        return None, [], str(e)
    fmt = _detect_format(head)
    if not fmt:
        return None, [], "unknown format"
    norm = _norm_claude if fmt == "claude" else _norm_codex
    evs = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[-max_records:]:
        try:
            o = json.loads(line)
        except Exception:
            continue
        e = norm(o)
        if e:
            evs.append(e)
    if evs and evs[-1].get("ts") is None:
        try:
            evs[-1]["ts"] = os.path.getmtime(path)
        except OSError:
            pass
    return fmt, evs, None


# ────────────────────────── 狀態機 ──────────────────────────

def _latest_task(evs):
    """The most recent user instruction — the 'what this agent is on' line."""
    for e in reversed(evs):
        if e.get("kind") == "user_msg" and e.get("text"):
            for line in e["text"].splitlines():
                line = line.strip()
                # skip noise-only lines (markers, separators)
                if line and not line.startswith(("---", "[[SF:", "<")):
                    return line[:90]
    return ""


SPINNER_RE = ("esc to interrupt", "Working (", "Running…", "↑")
MENU_RE = ("❯ 1.", "Do you want", "Would you like to run", "1. Yes", "Esc to cancel")


def compute_state(events, now=None, screen_tail=""):
    now = now or time.time()
    if not events:
        return "idle", {}, "no events"
    last = events[-1]
    last_ts = last.get("ts")
    last_tool = None
    for e in reversed(events):
        if e["kind"] == "tool_call":
            last_tool = e
            break
        if e["kind"] in ("turn_end", "user_msg"):
            break
    pending_tool = last if last["kind"] == "tool_call" else None
    age = (now - last_ts) if last_ts else None
    scr = screen_tail or ""
    spinner = any(x in scr for x in SPINNER_RE)
    menu = any(x in scr for x in MENU_RE)

    activity = {}
    if last_tool:
        activity = {"tool": last_tool.get("tool"),
                    "verb": VERB.get(last_tool.get("tool"), last_tool.get("tool") or "Working"),
                    "target": last_tool.get("target") or ""}

    if last["kind"] == "decision_req" or menu:
        return "decision", activity, "decision_req/menu"
    if last["kind"] == "error":
        return "stuck", activity, "error event"
    if last["kind"] == "turn_end":
        return "done", {}, "turn_end"
    if spinner:
        return "working", activity, "screen spinner"
    # A pending tool call means a tool is RUNNING — long commands (ssh, builds,
    # MCP calls) are normal and must read as working, not stuck.
    if pending_tool:
        return "working", activity, "tool running"
    if age is None or age < WORKING_FRESH_S:
        return "working", activity, "fresh event"
    # Stuck only when the turn never ended AND nothing has happened for a long
    # time (no error, no spinner, no pending tool) — a genuine stall.
    if age > STUCK_IDLE_S:
        return "stuck", activity, f"idle {int(age)}s"
    return "working", activity, "between events"


# ────────────────────────── 對外：StatusTracker ──────────────────────────

class StatusTracker:
    """per-sid 快取 transcript 路徑 + 去抖。main.py 每 ~500ms 呼叫 status_for。"""

    def __init__(self):
        self._path_cache = {}   # sid -> (transcript_path, resolved_at)
        self._last = {}         # sid -> (state, since_ts)
        self._pending = {}      # sid -> (candidate_state, first_seen_ts)

    def _resolve_cached(self, sid, worker, now):
        path, at = self._path_cache.get(sid, (None, 0))
        # codex 用 lsof 較貴 + 路徑可能變，30s 重解一次；claude 便宜可 10s
        ttl = 30 if _worker_kind(worker.get("cmd", "")) == "codex" else 10
        if path and os.path.exists(path) and (now - at) < ttl:
            return path
        path = resolve_transcript(worker)
        self._path_cache[sid] = (path, now)
        return path

    def _debounce(self, sid, state, now):
        """working↔done 需穩定 DONE_QUIET_S；decision/stuck 立即生效。"""
        prev, since = self._last.get(sid, (None, now))
        if state == prev:
            self._pending.pop(sid, None)
            return state
        if state in ("decision", "stuck"):
            self._last[sid] = (state, now)
            self._pending.pop(sid, None)
            return state
        cand, first = self._pending.get(sid, (state, now))
        if cand != state:
            self._pending[sid] = (state, now)
            return prev or state
        if (now - first) >= DONE_QUIET_S or prev is None:
            self._last[sid] = (state, now)
            self._pending.pop(sid, None)
            return state
        return prev

    def status_for(self, sid, worker, screen_tail="", now=None):
        """回傳 {state, dot, activity, summary, why, transcript}。永不拋例外。"""
        now = now or time.time()
        try:
            path = self._resolve_cached(sid, worker, now)
            if not path:
                return {"state": "unknown", "dot": "", "activity": {},
                        "summary": "", "why": "no transcript", "transcript": None}
            fmt, evs, err = _read_tail_events(path)
            if err:
                return {"state": "unknown", "dot": "", "activity": {},
                        "summary": "", "why": err, "transcript": path}
            state, act, why = compute_state(evs, now=now, screen_tail=screen_tail)
            state = self._debounce(sid, state, now)
            summary = state
            if act:
                summary = f"{act.get('verb','')} {act.get('target','')}".strip()
            return {"state": state, "dot": DOT.get(state, ""), "activity": act,
                    "summary": summary, "task": _latest_task(evs),
                    "why": why, "transcript": os.path.basename(path), "fmt": fmt}
        except Exception as e:
            return {"state": "unknown", "dot": "", "activity": {},
                    "summary": "", "why": f"exc:{e}", "transcript": None}


# 模組自測：python3 agent_status.py
if __name__ == "__main__":
    import sys
    tr = StatusTracker()
    # 掃所有近期 active worker（用 fallback 對應，不需 main.py）
    seen = set()
    rows = []
    cutoff = time.time() - 1800
    for f in glob.glob(os.path.join(CLAUDE_PROJECTS, "*/*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        fmt, evs, err = _read_tail_events(f)
        if err or not evs:
            continue
        st, act, why = compute_state(evs)
        s = f"{act.get('verb','')} {act.get('target','')}".strip() or st
        rows.append(("claude", st, s, why, os.path.basename(f)))
    for f in glob.glob(os.path.join(CODEX_SESSIONS, "*/*/*/rollout-*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        fmt, evs, err = _read_tail_events(f)
        if err or not evs:
            continue
        st, act, why = compute_state(evs)
        s = f"{act.get('verb','')} {act.get('target','')}".strip() or st
        rows.append(("codex", st, s, why, os.path.basename(f)))
    for src, st, s, why, fn in sorted(rows, key=lambda x: x[1]):
        print(f"[{src:6}] {st:8} | {s:45} | {why}  ({fn})")
