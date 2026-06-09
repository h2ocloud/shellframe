#!/usr/bin/env python3
"""
POC: 自動偵測 agent 狀態（不依賴 agent 自報 [[SF:...]]）

從 Claude Code transcript (~/.claude/projects/<slug>/<uuid>.jsonl) 或
Codex rollout log (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl) 解析事件，
跑統一狀態機，輸出：
  - state: working / decision / done / stuck
  - activity: 當前在忙什麼（工具 + 目標 + 本輪任務）

這是 stage 4(狀態機) + stage 7(POC) 的可執行驗證件。
不動正式 main.py / web/index.html。

用法:
  python3 agent_state_detector.py <transcript.jsonl>        # 算一次當前狀態
  python3 agent_state_detector.py --tail <transcript.jsonl>  # 持續 tail
  python3 agent_state_detector.py --scan                     # 掃所有 active worker
"""
import json
import os
import sys
import time
import glob
from pathlib import Path

# ── 狀態機門檻（去抖 / timeout，秒）─────────────────────────────
WORKING_FRESH_S = 8       # 最後事件在 8s 內視為仍在動
STUCK_TOOL_S = 90         # tool_use 開出去但超過 90s 無結果且無 spinner → stuck
STUCK_IDLE_S = 45         # turn 未結束但 45s 完全無新事件 → 可能 stuck（配合畫面判斷）
DONE_QUIET_S = 3          # turn 結束後安定 3s 才報 done（去抖）

# tool 名 → 人類動詞（給細節層）
VERB = {
    "Read": "Reading", "Edit": "Editing", "Write": "Writing", "NotebookEdit": "Editing",
    "Bash": "Running", "Grep": "Searching", "Glob": "Searching", "Task": "Delegating",
    "WebFetch": "Fetching", "WebSearch": "Searching web", "AskUserQuestion": "Asking",
    "TodoWrite": "Planning",
    # codex
    "exec_command": "Running", "shell": "Running", "apply_patch": "Editing",
    "read_file": "Reading", "image_generation_call": "Generating image",
}


def _target(tool, inp):
    """從工具 input 抽出簡短目標（檔名 / 指令首段）。"""
    if not isinstance(inp, dict):
        return ""
    for k in ("file_path", "path", "notebook_path"):
        if inp.get(k):
            return os.path.basename(str(inp[k]))
    if inp.get("pattern"):
        return repr(inp["pattern"])[:30]
    for k in ("command", "cmd", "query", "description", "prompt"):
        if inp.get(k):
            return str(inp[k]).strip().splitlines()[0][:40]
    return ""


# ── 解析：把兩種 log 格式 normalize 成統一事件流 ──────────────────
# 統一事件: {kind, ts, tool, target, text}
#   kind ∈ user_msg / tool_call / tool_result / assistant_text / turn_end /
#          error / decision_req

def _norm_claude(o):
    t = o.get("type")
    ts = _parse_ts(o.get("timestamp"))
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    if t == "user":
        content = m.get("content")
        # tool_result 也是 user role
        if isinstance(content, list) and any(
            isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return {"kind": "tool_result", "ts": ts}
        return {"kind": "user_msg", "ts": ts}
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
        return out or {"kind": "assistant_text", "ts": ts}
    return None


def _norm_codex(o):
    outer = o.get("type")
    p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
    pt = p.get("type")
    ts = None  # codex rollout 各 payload 自帶不同時間欄，turn 起訖才有
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
            return {"kind": "tool_call", "ts": None, "tool": "image_generation_call",
                    "target": ""}
    return None


def _parse_ts(s):
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


def detect_format(first_line):
    try:
        o = json.loads(first_line)
    except Exception:
        return None
    if o.get("type") in ("session_meta", "response_item", "event_msg", "turn_context"):
        return "codex"
    if "sessionId" in o or o.get("type") in ("user", "assistant", "system", "summary"):
        return "claude"
    return None


# ── 狀態機：吃 normalize 事件流（最後 ~200 筆）+ 可選畫面 wording ──
SPINNER_RE = ("esc to interrupt", "Working (", "Running…", "tokens")
MENU_RE = ("❯ 1.", "Do you want", "Would you like to run", "1. Yes")


def compute_state(events, now=None, screen_tail=""):
    """events: list[normalized], 時間序。回傳 (state, activity_dict, why)。"""
    now = now or time.time()
    if not events:
        return "idle", {}, "no events"

    last = events[-1]
    last_ts = last.get("ts")
    # 找最近一筆 tool_call（細節層用）+ 是否有未配對 result
    last_tool = None
    pending_tool = None
    for e in reversed(events):
        if e["kind"] == "tool_call" and last_tool is None:
            last_tool = e
        if e["kind"] in ("turn_end", "user_msg"):
            break
    # pending: 最後是 tool_call 且其後無 tool_result
    if last["kind"] == "tool_call":
        pending_tool = last

    age = (now - last_ts) if last_ts else None
    scr = screen_tail or ""
    spinner = any(x in scr for x in SPINNER_RE)
    menu = any(x in scr for x in MENU_RE)

    activity = {}
    if last_tool:
        verb = VERB.get(last_tool.get("tool"), last_tool.get("tool") or "Working")
        tgt = last_tool.get("target") or ""
        activity = {"tool": last_tool.get("tool"), "verb": verb, "target": tgt}

    # ── 判定優先序（高→低）──
    # 1) 等決策：AskUserQuestion / approval / 畫面選單
    if last["kind"] == "decision_req" or menu:
        return "decision", activity, "decision_req/menu"
    # 2) 明確錯誤
    if last["kind"] == "error":
        return "stuck", activity, "error event"
    # 3) turn 結束 → done（去抖 DONE_QUIET_S）
    if last["kind"] == "turn_end":
        if age is None or age >= 0:
            return "done", {}, "turn_end"
    # 4) 畫面 spinner = 確定在動（最可靠的即時訊號）
    if spinner:
        return "working", activity, "screen spinner"
    # 5) tool 開出去卡住
    if pending_tool and age is not None and age > STUCK_TOOL_S and not spinner:
        return "stuck", activity, f"tool pending {int(age)}s no result"
    # 6) 最近有事件 → working
    if age is None or age < WORKING_FRESH_S:
        return "working", activity, "fresh event"
    # 7) turn 未結束但長時間無事件 → stuck（保守）
    if age > STUCK_IDLE_S:
        return "stuck", activity, f"idle {int(age)}s, turn not ended"
    # 8) 中間地帶：剛做完一輪工具但還沒 end_turn，視為 working
    return "working", activity, "between events"


DOT = {"working": "sig-working", "decision": "sig-decision",
       "done": "sig-done", "stuck": "sig-stuck", "idle": ""}


def read_tail_events(path, max_records=300):
    fmt = None
    evs = []
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return None, [], str(e)
    if not lines:
        return None, [], "empty"
    fmt = detect_format(lines[0])
    norm = _norm_claude if fmt == "claude" else _norm_codex
    for l in lines[-max_records:]:
        try:
            o = json.loads(l)
        except Exception:
            continue
        e = norm(o)
        if e:
            evs.append(e)
    # 補時間戳：codex 多數事件無 ts，用檔案 mtime 當「最後事件時間」近似
    if evs and evs[-1].get("ts") is None:
        evs[-1]["ts"] = os.path.getmtime(path)
    return fmt, evs, None


def render(path, screen_tail=""):
    fmt, evs, err = read_tail_events(path)
    if err:
        return {"error": err}
    state, act, why = compute_state(evs, screen_tail=screen_tail)
    line = state
    if act:
        line = f"{state} · {act.get('verb','')} {act.get('target','')}".strip()
    return {"fmt": fmt, "state": state, "dot": DOT[state],
            "activity": act, "why": why, "summary": line,
            "events_parsed": len(evs)}


def scan_active():
    """掃所有 active worker：claude(newest jsonl per slug) + codex(open via lsof-ish)。"""
    rows = []
    # claude: 最近 30 分鐘有更新的 transcript
    cutoff = time.time() - 1800
    for jf in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        try:
            if os.path.getmtime(jf) < cutoff:
                continue
        except OSError:
            continue
        r = render(jf)
        r["file"] = os.path.basename(jf)
        r["src"] = "claude"
        rows.append(r)
    # codex: 整棵 sessions 樹取最近更新
    for rf in glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl")):
        try:
            if os.path.getmtime(rf) < cutoff:
                continue
        except OSError:
            continue
        r = render(rf)
        r["file"] = os.path.basename(rf)
        r["src"] = "codex"
        rows.append(r)
    return rows


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    if args[0] == "--scan":
        for r in sorted(scan_active(), key=lambda x: x.get("state", "")):
            print(f"[{r['src']:6}] {r.get('state','?'):8} | {r.get('summary',''):45} "
                  f"| {r.get('why','')}  ({r['file']})")
        sys.exit(0)
    if args[0] == "--tail":
        path = args[1]
        last = None
        while True:
            r = render(path)
            if r.get("summary") != last:
                print(time.strftime("%H:%M:%S"), "→", r.get("summary"), f"({r.get('why')})")
                last = r.get("summary")
            time.sleep(0.5)
    else:
        import pprint
        pprint.pprint(render(args[0]))
