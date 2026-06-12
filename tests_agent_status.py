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

print(f"\n=== {7 - len(fails)}/7 groups PASS ===")
sys.exit(1 if fails else 0)
