#!/usr/bin/env python3
"""agent_status.py regressions for Claude/Codex activity detection."""
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import agent_status as ag

fails = []


def ok(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        fails.append(name)


claude_idle = """
✻ Worked for 12s

────────────────────────────────────────────────────────────────────────────────
❯\u00a0
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
                                        new task? /clear to save 189.1k tokens
"""

codex_idle = """
• Ran python3 tests_agent_status.py
  └ PASS all

› Improve documentation in @filename

  gpt-5.5 high · ~
"""

codex_working = """
• Explored
  └ Read agent_status.py

• Working (1m 19s • esc to interrupt) · 1 background terminal running · /ps to …

› Improve documentation in @filename

  gpt-5.5 high · ~
"""

state, _, why = ag.compute_state([], screen_tail=claude_idle)
ok("Claude prompt screen-only is done", state == "done", why)

state, _, why = ag.compute_state([], screen_tail=codex_idle)
ok("Codex idle prompt screen-only is done", state == "done", why)

state, _, why = ag.compute_state([], screen_tail=codex_working)
ok("Codex working status overrides prompt", state == "working", why)

# A decision_req at the transcript tail must NOT pin 等決策 once the screen is
# back at an idle input prompt (the question was answered/dismissed). Regression
# for idle tabs stuck on 等決策 for their whole lifetime (3394m/979m).
import time as _t
_now = _t.time()
_stale_decision = [
    {"kind": "tool_call", "ts": _now - 200, "tool": "Bash", "target": "open report.html"},
    {"kind": "decision_req", "ts": _now - 200},
]
state, _, why = ag.compute_state(_stale_decision, now=_now, screen_tail=claude_idle)
ok("Stale decision_req + idle screen is done", state == "done", why)

# But a live menu on screen IS a real pending decision.
_menu_screen = "Which approach?\n❯ 1. Option A\n  2. Option B\n  Esc to cancel"
state, _, why = ag.compute_state(_stale_decision, now=_now, screen_tail=_menu_screen)
ok("decision_req + live menu is decision", state == "decision", why)

codex_user = {
    "timestamp": "2026-06-11T14:00:00.000Z",
    "type": "event_msg",
    "payload": {"type": "user_message", "message": "請修 ShellFrame Codex 狀態偵測"},
}
ev = ag._norm_codex(codex_user)
ok("Codex user_message keeps task text",
   ev and ev["kind"] == "user_msg" and "ShellFrame" in ev.get("text", ""),
   repr(ev))

codex_agent = {
    "timestamp": "2026-06-11T14:00:01.000Z",
    "type": "event_msg",
    "payload": {"type": "agent_message", "message": "我會先檢查 agent_status.py"},
}
ev = ag._norm_codex(codex_agent)
ok("Codex agent_message becomes narration",
   ev and ev["kind"] == "assistant_text" and "agent_status.py" in ev.get("text", ""),
   repr(ev))

codex_tool = {
    "timestamp": "2026-06-11T14:00:02.000Z",
    "type": "response_item",
    "payload": {
        "type": "custom_tool_call",
        "name": "apply_patch",
        "call_id": "call_1",
    },
}
ev = ag._norm_codex(codex_tool)
ok("Codex custom_tool_call is a tool_call",
   ev and ev["kind"] == "tool_call" and ev["tool"] == "apply_patch",
   repr(ev))

events = [
    ag._norm_codex(codex_user),
    ag._norm_codex(codex_agent),
    ag._norm_codex({
        "timestamp": "2026-06-11T14:00:03.000Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": '{"cmd":"python3 tests_agent_status.py"}',
        },
    }),
]
action, task, narration = ag._detail(events)
ok("Codex detail includes action/task/narration",
   action.startswith("Running python3") and task.startswith("請修")
   and narration.startswith("我會先"),
   repr((action, task, narration)))

# ── 回歸：模型判定準確性（成因 1–4）──────────────────────────────────────────

# 成因 2：_pretty_model 強化 — ANSI + [1m] 任意位置 + bare alias
ok("pretty_model: opus[1m] alias → Opus",
   ag._pretty_model("opus[1m]") == "Opus")
ok("pretty_model: bare alias sonnet → Sonnet",
   ag._pretty_model("sonnet") == "Sonnet")
ok("pretty_model: bare alias fable → Fable",
   ag._pretty_model("fable") == "Fable")
ok("pretty_model: [1m] mid-string claude-opus-4[1m]8 → still parsed",
   # 移除 [1m] 後剩 claude-opus-48，match group 2=4 group3=None → "Opus 4"
   ag._pretty_model("claude-opus-4[1m]8").startswith("Opus"))
ok("pretty_model: ANSI stripped",
   ag._pretty_model("\x1b[1msonnet\x1b[0m") == "Sonnet")
ok("pretty_model: full id claude-fable-5 → Fable 5",
   ag._pretty_model("claude-fable-5") == "Fable 5")
ok("pretty_model: full id claude-opus-4-8 → Opus 4.8",
   ag._pretty_model("claude-opus-4-8") == "Opus 4.8")
ok("pretty_model: full id claude-sonnet-5 → Sonnet 5",
   ag._pretty_model("claude-sonnet-5") == "Sonnet 5")

# 成因 3：_parse_model_flag 解析 --model flag
ok("parse_model_flag: --model opus",
   ag._parse_model_flag("claude --model opus foo") == "opus")
ok("parse_model_flag: --model=claude-fable-5",
   ag._parse_model_flag("claude --model=claude-fable-5 --dangerously-skip-permissions")
   == "claude-fable-5")
ok("parse_model_flag: no flag → None",
   ag._parse_model_flag("claude --dangerously-skip-permissions") is None)
ok("parse_model_flag: --model opus[1m] strips tag",
   ag._parse_model_flag("claude --model opus[1m]") == "opus")

# 成因 3 + detect_model_info：無 transcript 但有 --model flag → 回模型名
_worker_with_flag = {"cmd": "claude --model fable --dangerously-skip-permissions",
                     "cwd": "/tmp", "tmux_name": "worker-1"}
_info = ag.detect_model_info(_worker_with_flag, transcript_path=None)
ok("detect_model_info: --model flag 無 transcript → 回模型",
   _info is not None and _info.get("name") == "Fable")

# 成因 1：無 transcript 無 flag → None（不吃全域 settings.json）
_worker_bare = {"cmd": "claude --dangerously-skip-permissions",
                "cwd": "/tmp", "tmux_name": "worker-2"}
_info2 = ag.detect_model_info(_worker_bare, transcript_path=None)
ok("detect_model_info: 無 transcript 無 flag → None（不吃全域）",
   _info2 is None)

# 成因 4（isSidechain 過濾）：主 opus + sidechain sonnet → 回 opus
import json as _json, tempfile as _tf, os as _os

_transcript_lines = [
    # 主 chain：opus
    _json.dumps({"type": "assistant", "isSidechain": False,
                 "message": {"model": "claude-opus-4-8", "content": []}}),
    # sidechain：sonnet（Task subagent）
    _json.dumps({"type": "assistant", "isSidechain": True,
                 "message": {"model": "claude-sonnet-5", "content": []}}),
    # 另一筆主 chain（較舊，應被覆蓋）
    _json.dumps({"type": "assistant", "isSidechain": False,
                 "message": {"model": "claude-opus-4-8", "content": []}}),
]
with _tf.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as _f:
    _f.write("\n".join(_transcript_lines) + "\n")
    _tmp_path = _f.name

try:
    _model = ag._parse_claude_transcript_model(_tmp_path)
    ok("isSidechain 過濾：主 opus + sidechain sonnet → opus",
       _model == "claude-opus-4-8", f"got {_model!r}")
    # 透過 detect_model_info 端對端
    _worker_sc = {"cmd": "claude", "cwd": "/tmp", "tmux_name": "worker-3"}
    _info3 = ag.detect_model_info(_worker_sc, transcript_path=_tmp_path)
    ok("detect_model_info isSidechain 端對端 → Opus 4.8",
       _info3 is not None and _info3.get("name") == "Opus 4.8",
       repr(_info3))
finally:
    _os.unlink(_tmp_path)

# ── v0.29.28：badge 判讀修正（Howard 2026-08-06 tab13 顯示 Opus 4.6/xhigh、
#    實際 Opus 5/ultracode）──

# a) cmd uuid 抽取（resume 同檔續寫、birth 極舊，nearest-birth 必錯）
ok("_cmd_session_uuid resume",
   ag._cmd_session_uuid("claude --resume a6c737b5-b155-48b4-bb3f-bff736d775a4 --x")
   == "a6c737b5-b155-48b4-bb3f-bff736d775a4")
ok("_cmd_session_uuid session-id 大寫也收",
   ag._cmd_session_uuid("claude --session-id CEBBB46F-21C9-4B0F-9DA0-1B2A7922A919")
   == "cebbb46f-21c9-4b0f-9da0-1b2a7922a919")
ok("_cmd_session_uuid 無 uuid → None", ag._cmd_session_uuid("claude --model opus") is None)

# b) resolve_transcript：hook hint 最優先（唯一跟得上 /clear uuid 輪替的來源）
with _tf.NamedTemporaryFile(suffix=".jsonl", delete=False) as _f:
    _hint = _f.name
try:
    _got = ag.resolve_transcript({"cmd": "claude", "cwd": "~", "tmux_name": None,
                                  "transcript_hint": _hint})
    ok("resolve_transcript hint 優先", _got == _hint, repr(_got))
finally:
    _os.unlink(_hint)

# c) per-tab effort：transcript 的 /effort 痕跡優先於全域 effortLevel；
#    增量掃描（標記離檔尾很遠也要看得到、追加後更新）
_eff_lines = [
    '{"type":"user","message":{"content":"<local-command-stdout>Set effort level to ultracode (this session only): xhigh + dynamic workflow orchestration</local-command-stdout>"}}',
] + ['{"type":"assistant","message":{"model":"claude-opus-5","content":"filler"}}'] * 50
with _tf.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as _f:
    _f.write("\n".join(_eff_lines) + "\n")
    _eff_path = _f.name
try:
    ok("effort 痕跡（非尾端）→ ultracode",
       ag._parse_claude_transcript_effort(_eff_path) == "ultracode")
    with open(_eff_path, "a") as _f:
        _f.write('{"type":"user","message":{"content":"<local-command-stdout>Set effort level to max</local-command-stdout>"}}\n')
    ok("追加新痕跡 → 增量掃描更新為 max",
       ag._parse_claude_transcript_effort(_eff_path) == "max")
    _info_e = ag.detect_model_info({"cmd": "claude", "cwd": "/tmp", "tmux_name": None},
                                   transcript_path=_eff_path)
    ok("detect_model_info effort 走 per-tab 痕跡",
       _info_e is not None and _info_e.get("effort") == "max", repr(_info_e))
finally:
    _os.unlink(_eff_path)

# d) assistant 行的字串不算 effort 痕跡（對話內容討論這段字不設定）
with _tf.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as _f:
    _f.write('{"type":"assistant","message":{"model":"claude-opus-5","content":"Set effort level to low"}}\n')
    _asst_path = _f.name
try:
    ok("assistant 行不觸發 effort", ag._parse_claude_transcript_effort(_asst_path) is None)
finally:
    _os.unlink(_asst_path)

_TOTAL = 9 + 17 + 8
print(f"\n=== {_TOTAL - len(fails)}/{_TOTAL} groups PASS ===")
sys.exit(1 if fails else 0)
