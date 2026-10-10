#!/usr/bin/env python3
"""Grok Build 支援回歸測試：偵測、pin／續接、session 目錄、狀態、模型、用量、TG 忙碌判斷。

grok 的狀態來自它自己的 session 目錄（events.jsonl），不是畫面，所以這裡的 session 檔
是由真實一輪對話縮成的樣本，內嵌在測試裡；不依賴使用者本機的 ~/.grok 或任何工作目錄。
TG 的 marker 抽取用的是真實 PTY 擷取（gzip+base64 內嵌）。

跑法：.venv/bin/python tests_grok_provider.py
"""
import atexit
import base64
import contextlib
import gzip
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()  # main 只需要它能 import；TG 用下面的真檔

import agent_grok as G  # noqa: E402
import agent_model as M  # noqa: E402
import agent_status as A  # noqa: E402
import usage_probe as U  # noqa: E402
from _testsrc import app_source  # noqa: E402
import main as MAIN  # noqa: E402
_REAL_RUN = subprocess.run
from main import Api  # noqa: E402

SID = "97e7251f-c368-468d-8e58-9088a4001027"
SID_NEW = "2c6d1f0a-7b3e-4f58-9a21-6e0d4c8b9f13"
NEW_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OLD = "a6c737b5-b155-48b4-bb3f-bff736d775a4"
NOW = time.time()
IDLE_FOOTER = "Shift+Tab:mode  │  Ctrl+x:shortcuts"
BUSY_FOOTER = "Shift+Tab:mode  │  Ctrl+c:cancel  │  Ctrl+x:shortcuts"
# 有背景任務時忙碌 footer 多一段；同樣要算忙碌
BUSY_FOOTER_BG = "Shift+Tab:mode  │  Ctrl+c:cancel  │  Ctrl+b:send to bg  │  Ctrl+x:shortcuts"
BUSY_FOOTERS = (BUSY_FOOTER, BUSY_FOOTER_BG)
WORKER = {"cmd": f"grok --session-id {SID}", "cwd": "/home/user/work", "tmux_name": ""}
_ROOTS = []
passed = failed = 0

atexit.register(lambda: [shutil.rmtree(r, ignore_errors=True) for r in _ROOTS])


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 測試用的 session 目錄 ──────────────────────────────────────────────────

def new_root():
    """每個測試一個乾淨的 GROK_SESSIONS，並清掉目錄快取。"""
    root = tempfile.mkdtemp(prefix="sf_grok_test_")
    _ROOTS.append(root)
    G.GROK_SESSIONS = os.path.join(root, "sessions")
    G._DIR_CACHE.clear()
    return G.GROK_SESSIONS


def make_session(sid, files, cwd_dir="%2Fhome%2Fuser%2Fwork"):
    d = os.path.join(G.GROK_SESSIONS, cwd_dir, sid)
    os.makedirs(d, exist_ok=True)
    for name, body in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(body)
    return d


def iso(ago_s, now=NOW):
    return datetime.fromtimestamp(now - ago_s, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def events_file(rows, now=NOW):
    """rows: [(幾秒前, {event})]；ts 欄位由這裡產生。"""
    return "".join(json.dumps({"ts": iso(ago, now), **e}) + "\n" for ago, e in rows)


def summary(model="grok-4.7", effort="high", created_ago=600, now=NOW):
    return json.dumps({
        "info": {"id": SID, "cwd": "/home/user/work"},
        "created_at": iso(created_ago, now), "updated_at": iso(0, now),
        "current_model_id": model, "reasoning_effort": effort})


CHAT = [
    {"type": "system", "content": "You are Grok 4.7 … (system prompt elided)"},
    {"type": "user", "content": [{"type": "text", "text": "<user_info>\n(context elided)\n</user_info>"}]},
    {"type": "user", "content": [{"type": "text", "text": "<system-reminder>\n(elided)\n</system-reminder>"}],
     "synthetic_reason": "system_reminder"},
    {"type": "user", "content": [{"type": "text", "text": "<user_query>\nrun `ls -la` then reply with the single word: done\n</user_query>"}],
     "prompt_index": 0},
    {"type": "reasoning", "id": "rs_1", "summary": [], "status": "completed"},
    {"type": "assistant", "content": "I'll run `ls -la` and then reply with the single word you asked for.",
     "tool_calls": [{"id": "call-1", "name": "run_terminal_command",
                     "arguments": "{\"command\":\"ls -la\",\"description\":\"List all files\"}"}],
     "model_id": "grok-4.7-build"},
    {"type": "tool_result", "tool_call_id": "call-1", "content": "exit: 0\ntotal 0"},
    {"type": "reasoning", "id": "rs_2", "summary": [], "status": "completed"},
    {"type": "assistant", "content": "done", "model_id": "grok-4.7-build"},
    {"type": "user", "content": [{"type": "text", "text": "<user_query>\nreply: ok\n</user_query>"}],
     "prompt_index": 1},
    {"type": "assistant", "content": "ok", "model_id": "grok-4.7-build-fast"},
]
CHAT_TEXT = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in CHAT)

# 一輪工具呼叫、權限允許後完成
TOOL_TURN = [
    (40, {"type": "turn_started", "model_id": "grok-4.7"}),
    (40, {"type": "loop_started", "loop_index": 0}),
    (39, {"type": "phase_changed", "phase": "waiting_for_model"}),
    (37, {"type": "first_token"}),
    (37, {"type": "phase_changed", "phase": "tool_execution"}),
    (37, {"type": "tool_started", "tool_name": "run_terminal_command"}),
    (37, {"type": "phase_changed", "phase": "permission_prompt"}),
    (37, {"type": "permission_requested", "tool_name": "run_terminal_command"}),
    (36, {"type": "permission_resolved", "tool_name": "run_terminal_command", "decision": "allow"}),
    (36, {"type": "phase_changed", "phase": "tool_execution"}),
    (35, {"type": "tool_completed", "tool_name": "run_terminal_command", "outcome": "success"}),
]


def status_of(rows, screen="", summary_text=None, chat=None):
    """建一個 session 目錄跑 G.status；回 (state, why)。"""
    new_root()
    files = {"events.jsonl": events_file(rows), "summary.json": summary_text or summary()}
    if chat is not None:
        files["chat_history.jsonl"] = chat
    make_session(SID, files)
    return G.status(WORKER, NOW, screen)


# ── 1. 偵測：grok 認得，agent 不認 ──────────────────────────────────────────

def test_detection():
    for cmd in ("grok", "/home/user/.grok/bin/grok", "grok -m grok-4.7 --effort high", "grok.exe"):
        check(f"grok 偵測：{cmd}", U.detect_ai(cmd) == "grok" and A._worker_kind(cmd) == "grok",
              f"{U.detect_ai(cmd)} / {A._worker_kind(cmd)}")
    check("codex／claude 不受影響",
          A._worker_kind("codex") == "codex" and A._worker_kind("claude") == "claude")


def test_agent_is_not_grok():
    for cmd in ("agent", "/home/user/.local/bin/agent", "agent --help"):
        check(f"agent 不認：{cmd}",
              U.detect_ai(cmd) is None and A._worker_kind(cmd) == "other" and not G.is_grok_cmd(cmd),
              f"{U.detect_ai(cmd)} / {A._worker_kind(cmd)}")


# ── 2. 模型旗標：-m 取代而不是疊加 ─────────────────────────────────────────

def test_model_flag_replaces_existing():
    check("provider_of 認得 grok", M.provider_of("grok -m grok-4.7") == "grok")
    got = M.apply("grok -m grok-4.7 --effort high", "grok-4.6")
    check("apply 取代舊的 -m", got == "grok --effort high -m grok-4.6", got)
    check("apply 沒有 -m 時補上", M.apply("grok", "grok-4.6") == "grok -m grok-4.6")


# ── 3. pin_session_id：只 pin 新開的對話 ───────────────────────────────────

def test_pin_session_id_new_conversations():
    for cmd in ("grok", "grok --permission-mode bypassPermissions", "/home/user/.grok/bin/grok -m grok-4.7"):
        out, sid = G.pin_session_id(cmd)
        check(f"新開會 pin 一次：{cmd}",
              sid and out.count("--session-id") == 1 and out.endswith(sid), out)


def test_pin_session_id_leaves_others_alone():
    for cmd in ("grok --resume " + OLD, "grok -r " + OLD, "grok -c", "grok --continue",
                "grok -s " + OLD, f"grok --session-id={OLD}", "grok -p hi",
                "grok sessions list", "grok login", "zsh -lc 'grok'", "claude", "agent"):
        check(f"不 pin：{cmd}", G.pin_session_id(cmd) == (cmd, None), str(G.pin_session_id(cmd)))


# ── 4. 續接指令：換成 --resume <同一個 id>，不 fork ─────────────────────────

def test_cmd_with_resume_rewrites_to_same_session():
    cases = [
        ("grok", f"grok --resume {NEW_ID}"),
        (f"grok --permission-mode bypassPermissions --session-id {OLD}",
         f"grok --resume {NEW_ID} --permission-mode bypassPermissions"),
        (f"grok -r {OLD} -m x", f"grok --resume {NEW_ID} -m x"),
        (f"grok --resume={OLD}", f"grok --resume {NEW_ID}"),
        ("grok -c", f"grok --resume {NEW_ID}"),
        ("grok --resume --effort low", f"grok --resume {NEW_ID} --effort low"),
        (f"grok --fork-session --resume {OLD}", f"grok --resume {NEW_ID}"),
        (f"sf-grok --session-id {OLD}", f"sf-grok --resume {NEW_ID}"),
    ]
    for cmd, want in cases:
        got = G.cmd_with_resume(cmd, NEW_ID)
        check(f"續接：{cmd}", got == want, got)
    check("非 grok 指令原樣回傳", G.cmd_with_resume("claude --resume " + OLD, NEW_ID) == "claude --resume " + OLD)


def test_api_cmd_with_resume_routes_grok_and_keeps_claude():
    R = Api._cmd_with_resume
    check("Api：grok 走 agent_grok", R("grok --session-id " + OLD, NEW_ID) == f"grok --resume {NEW_ID}",
          R("grok --session-id " + OLD, NEW_ID))
    check("Api：sf-grok 包裝也走 grok",
          R("sf-grok --permission-mode bypassPermissions", NEW_ID)
          == f"sf-grok --resume {NEW_ID} --permission-mode bypassPermissions")
    check("Api：claude 仍是 claude 的規則",
          R("claude --resume " + OLD, NEW_ID).startswith(f"claude --resume {NEW_ID}"))


# ── 5. session 目錄對應 ──────────────────────────────────────────────────

class _Clock:
    """替掉 agent_grok 的 time 模組，讓快取的 TTL 可以被推進。"""

    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


@contextlib.contextmanager
def fake_lsof(pids, table, clock=None, pane=4242):
    """假的 pane 行程樹與 lsof：pids 依序（根在前）；table 是 {pid: [開著的路徑]}。
    產出 calls（每次 lsof 查的 pid）。真正的 _events_open_in 照常跑，只是 lsof 的輸出是假的。"""
    calls = []
    saved = (A._tmux_pane_pid, A._pid_tree, subprocess.run, G.time)

    def run(args, **kw):
        if args[:1] != ["lsof"]:
            raise AssertionError(f"unexpected subprocess: {args}")
        pid = int(args[2])
        calls.append(pid)
        body = "".join(f"grok {pid} user 4u REG 1,2 0 1 {p}\n" for p in table.get(pid, []))
        return types.SimpleNamespace(returncode=0, stdout=body)

    A._tmux_pane_pid = lambda name: pane if name else None
    A._pid_tree = lambda root, depth=2: list(pids)
    subprocess.run = run
    if clock is not None:
        G.time = clock
    try:
        yield calls
    finally:
        A._tmux_pane_pid, A._pid_tree, subprocess.run, G.time = saved
        G._DIR_CACHE.clear()


def test_session_dir_by_cmd_uuid():
    new_root()
    d = make_session(SID, {"summary.json": summary()})
    check("cmd 的 --session-id 找得到目錄", G.session_dir(WORKER) == d, str(G.session_dir(WORKER)))
    check("session_id 是目錄名", G.session_id(WORKER) == SID)


def test_session_dir_by_grok_session_id():
    new_root()
    d = make_session(SID, {"events.jsonl": ""})
    w = {"cmd": "grok", "cwd": "/home/user/work", "tmux_name": "", "grok_session_id": SID}
    check("manifest 的 grok_session_id 找得到", G.session_dir(w) == d)


def test_lsof_newest_created_wins():
    new_root()
    old_d = make_session(SID, {"events.jsonl": "", "summary.json": summary(created_ago=3600)})
    new_d = make_session(SID_NEW, {"events.jsonl": "", "summary.json": summary(created_ago=60)})
    paths = [os.path.join(old_d, "events.jsonl"), os.path.join(new_d, "events.jsonl")]
    with fake_lsof([4242, 4243], {4242: paths}):
        got = G.session_dir({"cmd": "grok", "cwd": "/home/user/work", "tmux_name": "sf_a"})
    check("lsof 同時看到兩份（/new 之後）→ created_at 較新的那份", got == new_d, str(got))


def test_lsof_stops_at_first_hit_and_caches_by_ttl():
    new_root()
    d = make_session(SID, {"events.jsonl": "", "summary.json": summary()})
    ev = os.path.join(d, "events.jsonl")
    pids = [5000, 5001, 5002]                    # 根在前；根就是 grok 本體，握著 events.jsonl
    clock = _Clock(1000.0)
    hit = {"cmd": "grok", "cwd": "/home/user/work", "tmux_name": "sf_hit"}
    with fake_lsof(pids, {5000: [ev]}, clock) as calls:
        got1 = G.session_dir(hit)
        check("命中根行程就停：只 lsof 一次", got1 == d and calls == [5000], f"{got1} {calls}")
        clock.t += 10
        got2 = G.session_dir(hit)
        check("命中後 30 秒內走快取，不再 lsof", got2 == d and calls == [5000], str(calls))
        clock.t += 25                            # 共 35 秒，超過 30 秒的命中 TTL
        G.session_dir(hit)
        check("超過 30 秒才重新查", calls == [5000, 5000], str(calls))
    miss = {"cmd": "grok", "cwd": "/home/user/work", "tmux_name": "sf_miss"}
    with fake_lsof(pids, {}, clock) as calls:
        check("整棵樹都沒有 → None", G.session_dir(miss) is None)
        check("未命中會掃完整棵樹", calls == pids, str(calls))
        clock.t += 3
        G.session_dir(miss)
        check("未命中 5 秒內走快取", calls == pids, str(calls))
        clock.t += 3                             # 共 6 秒，超過 5 秒的未命中 TTL
        G.session_dir(miss)
        check("未命中超過 5 秒就重查", calls == pids + pids, str(calls))
    check("lsof 掃描與 time 已還原", subprocess.run is _REAL_RUN and G.time is time)


def test_same_cwd_without_id_is_none():
    new_root()
    make_session(SID, {"events.jsonl": "", "summary.json": summary()})
    make_session(SID_NEW, {"events.jsonl": "", "summary.json": summary()})
    w = {"cmd": "grok", "cwd": "/home/user/work", "tmux_name": ""}
    check("同 cwd 兩個 session、沒有 id、lsof 也沒有 → None（不猜最新的）",
          G.session_dir(w) is None, str(G.session_dir(w)))
    check("session_exists 只認完整 uuid", not G.session_exists("not-a-uuid") and G.session_exists(SID))


# ── 6. chat_history → 事件 ─────────────────────────────────────────────────

def test_normaliser_on_real_shape():
    new_root()
    make_session(SID, {"chat_history.jsonl": CHAT_TEXT, "summary.json": summary()})
    path = G.transcript_path(WORKER)
    fmt, evs, err = A._read_tail_events(path)
    check("格式認得是 grok", fmt == "grok" and err is None, f"{fmt} {err}")
    kinds = [e["kind"] for e in evs]
    check("事件順序（preamble 與系統提醒被跳過）",
          kinds == ["user_msg", "assistant_text", "tool_call", "tool_result",
                    "assistant_text", "user_msg", "assistant_text"], str(kinds))
    check("提問外殼被剝掉",
          evs[0]["text"] == "run `ls -la` then reply with the single word: done", evs[0]["text"])
    check("工具目標是指令本身", evs[2]["tool"] == "run_terminal_command" and evs[2]["target"] == "ls -la",
          str(evs[2]))
    # 手機 conversation 走的通用路徑：resolve_transcript → _read_tail_events → 依 kind 過濾
    check("resolve_transcript 指到 chat_history.jsonl", A.resolve_transcript(WORKER) == path)
    turns = [e for e in evs if e.get("kind") in ("user_msg", "assistant_text", "tool_call", "error")]
    check("conversation 過濾後保留對話輪次", len(turns) == 6 and turns[-1]["text"] == "ok", str(turns[-1:]))


def test_first_lines_of_other_providers_unchanged():
    claude = json.dumps({"type": "user", "sessionId": SID, "uuid": "u1", "timestamp": "2026-10-10T00:00:00Z",
                         "message": {"role": "user", "content": "hi"}})
    codex = json.dumps({"type": "session_meta", "payload": {"id": SID}})
    check("Claude 第一行仍是 claude", A._detect_format(claude) == "claude")
    check("Codex 第一行仍是 codex", A._detect_format(codex) == "codex")
    check("grok 第一行是 grok", A._detect_format(json.dumps(CHAT[0])) == "grok")


# ── 7. 模型 ──────────────────────────────────────────────────────────────

def test_model_info_from_summary():
    new_root()
    make_session(SID, {"summary.json": summary(model="grok-4.7", effort="high")})
    got = A.detect_model_info(WORKER)
    check("summary.json 的模型與 effort",
          got == {"name": "grok-4.7", "effort": "high", "provider": "grok"}, str(got))


def test_model_info_cmd_fallback():
    new_root()
    make_session(SID, {"events.jsonl": ""})            # 沒有 summary.json
    w = {"cmd": f"grok -m grok-4.6 --effort low --session-id {SID}", "cwd": "/home/user/work", "tmux_name": ""}
    got = A.detect_model_info(w)
    check("沒有 summary 時退回 cmd 的 -m／--effort",
          got == {"name": "grok-4.6", "effort": "low", "provider": "grok"}, str(got))


# ── 8. 狀態 ──────────────────────────────────────────────────────────────

def test_status_working_mid_tool():
    rows = TOOL_TURN[:6]
    st = status_of(rows)
    check("工具執行中 → working", st[0] == "working", str(st))


def test_status_permission_pending_is_decision():
    rows = TOOL_TURN[:8]
    st = status_of(rows)
    check("permission_requested 未解決、畫面空白 → decision", st[0] == "decision", str(st))
    st_idle = status_of(rows, screen=IDLE_FOOTER)
    check("permission_requested 未解決、閒置 footer → decision", st_idle[0] == "decision", str(st_idle))


def test_status_pending_permission_under_busy_footer_is_auto_review():
    # 實測：grok 自己在審，畫面沒有對話框，只有忙碌 footer；不是等人
    rows = TOOL_TURN[:8]
    for footer in BUSY_FOOTERS:
        st = status_of(rows, screen=footer)
        check(f"permission 待決＋忙碌 footer → working（{footer[-30:]}）",
              st[0] == "working" and st[1] == "grok permission auto-review", str(st))


def test_status_permission_resolved_is_not_decision():
    st = status_of(TOOL_TURN)
    check("權限已允許後 → 不是 decision", st[0] == "working", str(st))


def test_status_turn_boundary_clears_old_permission():
    rows = [(200, {"type": "permission_requested", "tool_name": "x"}),
            (30, {"type": "turn_started", "model_id": "grok-4.7"}),
            (29, {"type": "first_token"})]
    st = status_of(rows)
    check("舊 turn 留下的未解決請求，不會釘住新 turn 的燈號", st[0] == "working", str(st))


def test_status_done_then_idle():
    st5 = status_of(TOOL_TURN + [(5, {"type": "turn_ended", "outcome": "completed"})])
    check("turn_ended 5 秒前 → done", st5[0] == "done", str(st5))
    st60 = status_of(TOOL_TURN + [(60, {"type": "turn_ended", "outcome": "completed"})])
    check("turn_ended 60 秒前 → idle", st60[0] == "idle", str(st60))


def test_status_silent_turn_without_busy_footer_is_idle():
    rows = [(120, {"type": "turn_started", "model_id": "grok-4.7"}), (120, {"type": "first_token"})]
    st = status_of(rows, screen=IDLE_FOOTER)
    check("120 秒沒事件、畫面閒置 → idle（中斷的 turn 不永遠 working）", st[0] == "idle", str(st))
    for footer in BUSY_FOOTERS:
        st2 = status_of(rows, screen=footer)
        check(f"同樣 120 秒但畫面仍忙碌 → working（{footer[-30:]}）", st2[0] == "working", str(st2))


def test_status_screen_only_when_no_session():
    new_root()
    busy = G.status(WORKER, NOW, BUSY_FOOTER_BG)
    idle = G.status(WORKER, NOW, IDLE_FOOTER)
    none = G.status(WORKER, NOW, "")
    check("沒有 session + 忙碌 footer（含背景任務）→ working", busy[0] == "working", str(busy))
    check("沒有 session + 閒置 footer → idle", idle[0] == "idle", str(idle))
    check("沒有 session 也沒有畫面 → unknown", none[0] == "unknown", str(none))


def test_status_tracker_end_to_end():
    status_of(TOOL_TURN + [(5, {"type": "turn_ended", "outcome": "completed"})], chat=CHAT_TEXT)
    res = A.StatusTracker().status_for("s1", WORKER, screen_tail="", now=NOW)
    check("status_for 回傳 state", res.get("state") == "done", str(res))
    check("status_for 回傳 model（來自 summary.json）",
          (res.get("model") or {}).get("name") == "grok-4.7", str(res.get("model")))
    check("status_for 的 transcript 是 chat_history.jsonl", res.get("transcript") == "chat_history.jsonl",
          str(res.get("transcript")))


# ── 9. 用量 ──────────────────────────────────────────────────────────────

def test_usage_text_from_session_files():
    new_root()
    usage = {"session": {"totalTokens": 72986, "turnCount": 2}}
    signals = {"contextTokensUsed": 24288, "contextWindowTokens": 256000}
    # 真實目錄一定有 summary.json 或 events.jsonl；只有 usage 的目錄不算 session
    make_session(SID, {"summary.json": summary(), "usage.json": json.dumps(usage),
                       "signals.json": json.dumps(signals)})
    got = G.usage_text(WORKER)
    check("usage_text 含 context 與 tokens、回合數",
          got == "Grok Build 本 session：context 24K/256K · 72,986 tokens · 2 回合", got)
    make_session(SID_NEW, {"summary.json": summary(), "usage.json": json.dumps(usage)})
    no_ctx = G.usage_text({"cmd": "grok", "cwd": "/x", "tmux_name": "", "grok_session_id": SID_NEW})
    check("沒有 signals.json → 只報 tokens 與回合", no_ctx == "Grok Build 本 session：72,986 tokens · 2 回合", no_ctx)
    check("沒有 usage.json → 空字串", G.usage_text({"cmd": "grok", "cwd": "/x", "tmux_name": ""}) == "")


def test_usage_report_full_text():
    new_root()
    usage = {"session": {"totalTokens": 72986, "turnCount": 2}}
    signals = {"contextTokensUsed": 24288, "contextWindowTokens": 256000}
    make_session(SID, {"summary.json": summary(), "usage.json": json.dumps(usage),
                       "signals.json": json.dumps(signals)})
    # 配速數字跟現在的時鐘有關，這裡把窗口釘成「還剩整整 2 天／共 7 天」，
    # 已用 14% → 應累積 71%，落後 57%。不打真正的 billing。
    now = time.time()
    reading = {
        "week": (14, "10-12 08:35"),
        "_reset_epoch": {"week": int(now + 2 * 86400)},
        "_window_minutes": {"week": 7 * 24 * 60},
        "_groups": [{"name": "Grok Build", "used": 12}, {"name": "App Builder", "used": 2}],
    }
    orig_quota, orig_label = G.quota, G.account_label
    G.quota = lambda: reading
    G.account_label = lambda: "user@example.com"
    try:
        got = G.usage_report(WORKER)
        lines = got.split("\n")
        check("usage_report 第一行是品牌", lines[0] == "AI 水位 Grok Build", got)
        check("usage_report 帶帳號，不含權杖字樣",
              lines[1] == "帳號 user@example.com" and "key" not in got, got)
        check("usage_report 寫出週配額與配速",
              lines[2] == "週配額已用 14%｜重置 10-12 08:35｜配速應 71%（落後 57%）", got)
        check("usage_report 列出各產品用量",
              "Grok Build 12%" in lines and "App Builder 2%" in lines, got)
        check("usage_report 末行是本 session 用量",
              lines[-1] == "Grok Build 本 session：context 24K/256K · 72,986 tokens · 2 回合", got)
        G.quota = lambda: {"_error": "auth_required",
                           "_error_message": "尚未登入 Grok Build，請執行 grok login"}
        no_usage = G.usage_report({"cmd": "grok", "cwd": "/x", "tmux_name": ""})
        check("沒登入時說明原因，不再宣稱沒有週配額",
              no_usage.split("\n") == ["AI 水位 Grok Build", "尚未登入 Grok Build，請執行 grok login"]
              and "沒有公開" not in no_usage and "請確認已登入" not in no_usage, no_usage)
    finally:
        G.quota, G.account_label = orig_quota, orig_label


_BILLING = {
    "config": {
        "creditUsagePercent": 14.0,
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "start": "2026-10-05T00:35:42+00:00",
            "end": "2026-10-12T00:35:42+00:00",
        },
        "billingPeriodStart": "2026-10-05T00:35:42+00:00",
        "billingPeriodEnd": "2026-10-12T00:35:42+00:00",
        "productUsage": [
            {"product": "GrokBuild", "usagePercent": 12.0},
            {"product": "GrokAppBuilder", "usagePercent": 2.0},
            {"product": "GrokChat"},
        ],
    }
}


def test_quota_payload_weekly_pace_inputs():
    got = G._quota_from_payload(_BILLING)
    check("週配額百分比是已用 14，不是剩下的", got.get("week", (None,))[0] == 14, str(got))
    check("重置時刻有字", bool(got.get("week", (None, ""))[1]) and got["week"][1] != "?", str(got))
    check("配速要用的重置 epoch 是週期結束",
          (got.get("_reset_epoch") or {}).get("week") == int(datetime(2026, 10, 12, 0, 35, 42, tzinfo=timezone.utc).timestamp()),
          str(got.get("_reset_epoch")))
    check("窗口長度是 7 天", (got.get("_window_minutes") or {}).get("week") == 7 * 24 * 60, str(got))
    check("沒有 5 小時窗口（不編一個）", "5hr" not in got, str(got))
    names = [g["name"] for g in got.get("_groups") or []]
    check("產品拆分含 Grok Build 與 App Builder，略過沒有百分比的項目",
          names == ["Grok Build", "App Builder"], str(names))
    check("沒有百分比的回應說原因，不回 0",
          G._quota_from_payload({"config": {}}).get("_error") == "no_data")


def test_quota_uses_billing_and_caches():
    G._reset_quota_cache()
    calls = {"n": 0}

    def fake_fetch(token):
        calls["n"] += 1
        check("送給 billing 的不是空權杖", token == "test-token-value", token[:4])
        return 200, json.dumps(_BILLING).encode()

    orig_fetch, orig_auth = G._fetch_billing, G.AUTH_FILE
    auth = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"https://auth.example.test": {"key": "test-token-value", "email": "user@example.com"}}, auth)
    auth.close()
    G._fetch_billing = fake_fetch
    G.AUTH_FILE = auth.name
    try:
        first = G.quota()
        second = G.quota()
        check("quota 讀到週配額 14%", (first.get("week") or (None,))[0] == 14, str(first))
        check("60 秒內不打第二次", calls["n"] == 1 and second.get("week") == first.get("week"), str(calls))
        check("帳號標籤是 email", G.account_label() == "user@example.com", G.account_label())
        G._reset_quota_cache()
        G.AUTH_FILE = auth.name + ".missing"
        missing = G.quota()
        check("沒有登入檔就說明要 grok login，而且沒有發請求",
              missing.get("_error") == "auth_required" and calls["n"] == 1
              and "grok login" in missing.get("_error_message", ""), str(missing))
    finally:
        G._fetch_billing, G.AUTH_FILE = orig_fetch, orig_auth
        G._reset_quota_cache()
        os.unlink(auth.name)


def test_probe_data_exposes_pace_fields():
    orig_q, orig_a = G.quota, G.account_label
    now = time.time()
    G.quota = lambda: {
        "week": (14, "10-12 08:35"),
        "_reset_epoch": {"week": int(now + 2 * 86400)},
        "_window_minutes": {"week": 7 * 24 * 60},
        "_groups": [{"name": "Grok Build", "used": 12, "reset": "10-12 08:35",
                     "window": "weekly", "key": "week"}],
    }
    G.account_label = lambda: "user@example.com"
    try:
        d = U.probe_data("grok --session-id x")
        week = d.get("week") or {}
        check("pill 資料是 grok 的週配額", d.get("ai") == "grok" and week.get("pct") == 14, str(d))
        check("pill 帶重置 epoch 與 7 天窗口",
              week.get("reset_epoch") and week.get("window_minutes") == 7 * 24 * 60, str(week))
        check("沒有 5 小時窗口", d.get("five_hr") is None, str(d.get("five_hr")))
        check("帳號進 pill", d.get("account") == "user@example.com", str(d.get("account")))
        check("產品拆分進 groups", (d.get("groups") or [{}])[0].get("pct") == 12, str(d.get("groups")))
    finally:
        G.quota, G.account_label = orig_q, orig_a


# ── 10. TG 忙碌判斷 ──────────────────────────────────────────────────────

def _load_bridge():
    spec = importlib.util.spec_from_file_location("bt", os.path.join(HERE, "bridge_telegram.py"))
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    return bt


def test_tg_turn_busy_regex():
    bt = _load_bridge()
    check("grok 忙碌 footer 算回合進行中", bool(bt._TURN_BUSY_RE.search(BUSY_FOOTER)))
    check("grok 忙碌 footer（含背景任務）也算", bool(bt._TURN_BUSY_RE.search(BUSY_FOOTER_BG)))
    check("Claude 的 esc to interrupt 仍算", bool(bt._TURN_BUSY_RE.search("✻ Thinking… (esc to interrupt)")))
    check("grok 閒置 footer 不算", bt._TURN_BUSY_RE.search(IDLE_FOOTER) is None)


RAW_GZ_B64 = (
    "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"
    "fW7+PwNliu8gUAAA"
)


def test_tg_marker_on_real_capture():
    bt = _load_bridge()
    BR = object.__new__(bt.TelegramBridge)
    start, end = "[[TG_REPLY_ab12cd34]]", "[[/TG_REPLY_ab12cd34]]"
    raw = gzip.decompress(base64.b64decode(RAW_GZ_B64)).decode("utf-8", errors="replace")
    slot = types.SimpleNamespace(
        expect_marker=True, reply_start_marker=start, reply_end_marker=end,
        pending_raw=raw, peek_fn=None,
        marker_prompt=("最終要回 Telegram 的文字請放在 [[TG_REPLY_ab12cd34]] 和 [[/TG_REPLY_ab12cd34]] 之間。"
                       "標記外可以思考或操作，但手機只會收到標記內文字。"),
        marker_next_scan_ts=0.0, marker_scan_gen=-1, _feed_gen=1, sent_responses=set())
    got = BR._try_marker_extract(slot, now=1000.0, total=10.0)
    check("真實 PTY 擷取抽出標記內文字", got == "5完畢", repr(got))


# ── 11. 接線：api_history、manifest、soft restore、usage 指令 ─────────────

def test_history_takes_transcript_path_for_grok():
    new_root()
    make_session(SID, {"chat_history.jsonl": CHAT_TEXT, "summary.json": summary()})
    long_text = "\n".join(["對話內容 " + "x" * 80] * 10)
    stub = types.SimpleNamespace(
        _worker_ctx=lambda sid, s: {"cmd": s.cmd, "cwd": "/home/user/work", "tmux_name": ""},
        _is_opencode_cmd=lambda cmd: False,
        _opencode_history_response=lambda *a, **k: None,
        _TRANSCRIPT_TAIL_BYTES=Api._TRANSCRIPT_TAIL_BYTES,
        _TRANSCRIPT_MAX_RECORDS=Api._TRANSCRIPT_MAX_RECORDS,
        _TRANSCRIPT_GROW_BYTES=Api._TRANSCRIPT_GROW_BYTES,
        _render_transcript_overlay=lambda evs, ansi, cols=0: long_text,
        _ANSI_STRIP_RE=Api._ANSI_STRIP_RE)
    s = types.SimpleNamespace(cmd=f"grok --session-id {SID}", cwd="/home/user/work")
    resp = Api._transcript_history_response(stub, s, "s1", False, 80)
    got = json.loads(resp) if resp else {}
    check("grok 上滾歷史走 transcript", got.get("source") == "transcript (grok)", str(resp)[:120])
    pi = types.SimpleNamespace(cmd="pi", cwd="/home/user/work")
    check("非 claude／codex／grok 的分頁沒有 transcript 路徑",
          Api._transcript_history_response(stub, pi, "s1", False, 80) is None)


def _function_body(src, marker):
    """從 marker 開始到下一個同層級的 def（method 縮排 4 格）為止的原始碼。"""
    i = src.index(marker)
    j = src.find("\n    def ", i + len(marker))
    return src[i:j if j > 0 else len(src)]


def test_wiring_source_checks():
    src = app_source()
    check("manifest：grok 寫 grok_session_id，不寫 claude_session_id",
          'entry["grok_session_id"] = grok_sid' in src and "elif hook_csid:" in src)
    check("soft restore：grok 走自己的 uuid 與存在判斷",
          'elif _session_provider(cmd) == "grok":' in src and "agent_grok.session_exists(csid)" in src)
    check("soft restore：grok 不進 claude 的家目錄搬移",
          'and _session_provider(cmd) != "grok":' in src)
    check("/usage：grok 走 usage_report（不用 probe 的「請確認已登入」）",
          "agent_grok.usage_report(self._worker_ctx(sid, s))" in src)
    check("web 與 sfctl 的 /usage 都走 usage_report",
          src.count("agent_grok.usage_report(self._worker_ctx(sid, s))") >= 2)
    body = _function_body(src, "def _persist_session_manifest")
    lock_at = body.find("with _CONFIG_LOCK:")
    check("manifest：grok 的 lsof 解析在 config 鎖之前，鎖內沒有 lsof 呼叫",
          lock_at > 0 and "agent_grok.session_id(" in body
          and body.rfind("agent_grok.session_id(") < lock_at, f"lock_at={lock_at}")
    check("換帳號／套用更新重開：grok 換成 --resume 同一個 session（不是同 id 的新對話）",
          'elif provider == "grok":' in src and "agent_grok.session_id(self._worker_ctx(sid, old))" in src)
    check("registry 與 preset 都有 grok", "grok" in U.PROVIDER_SPECS and any(
        p["name"] == "Grok Build" for p in MAIN._DEFAULT_AI_PRESETS))


if __name__ == "__main__":
    import traceback
    fails = 0
    for name in sorted(list(globals())):
        if name.startswith("test_") and callable(globals()[name]):
            print(f"\n{name}")
            try:
                globals()[name]()
            except Exception:
                fails += 1
                print(f"  [EXC] {name}")
                traceback.print_exc()
    print(f"\n{passed} checks passed, {failed} failed, {fails} tests raised")
    print("ALL PASS" if not (failed or fails) else "FAILED")
    sys.exit(1 if (failed or fails) else 0)
