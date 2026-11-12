#!/usr/bin/env python3
"""The person's own words start a fresh A2A chain in that tab (agent_link.clear_chain).

Found while making the chain limit's bounce (v0.42.1): clear_chain() was never called outside the
tests. A tab that once got a message 4 layers deep stayed there: everything it sent afterwards was
layer 5 and stopped, until another A2A message reached it or ShellBot restarted, whatever the
person asked it in between.
  - Enter in the tab's terminal clears its chain; typing without Enter does not
  - a send from the chat view clears it; one that only asks about a draft (nothing sent) does not
  - the Telegram bridge's prepare hook (called only before it types the person's message) clears it
  - what ShellBot itself types in (an A2A delivery, a queued send) does not go through those paths:
    the delivery books its depth and the tab stays where the chain put it
  - end to end: a tab at the end of a chain is refused; the person writes to it; it can send again
Run: .venv/bin/python tests_a2a_chain_reset.py
"""
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")  # fake tabs stay out of the real log

import agent_link  # noqa: E402
agent_link.LOG_FILE = Path(tempfile.mkdtemp(prefix="sf-a2a-")) / "a2a_log.json"   # out of the real A2A log
import tab_delivery  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def api_with_tab(sid="s1"):
    api = Api()
    written = []
    s = types.SimpleNamespace(
        sid=sid, cmd="claude", cwd="~", lock=MagicMock(), write=lambda d: written.append(d),
        _recent=bytearray(), _startup_trust_pending=False, _last_activity_time=0.0,
        _last_user_activity_time=0.0, _idle_reap_state="", _slug_pending=False, _tmux_name=None,
        _init_pending=False, _master_turn=False)
    api.sessions = {sid: s}
    api.bridge = None
    api._arm_awaiting_response = lambda *a, **k: None
    api._auto_name = lambda *a, **k: None
    return api, s, written


def at_end_of_chain(sid="s1"):
    agent_link._inbound_depth.clear()
    agent_link.note_delivered(sid, agent_link.MAX_DEPTH)
    return agent_link.depth_for(sid) == agent_link.MAX_DEPTH + 1


print("1. Enter in the terminal")
api, s, written = api_with_tab()
at_end_of_chain()
api.write_input("s1", "幫我看一下")
check("typing without Enter leaves the chain as it is", agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 1)
api.write_input("s1", "\r")
check("…Enter clears it: what the tab sends next is layer 1", agent_link.depth_for("s1") == 1, agent_link._inbound_depth)

print("2. the chat view")
api, s, written = api_with_tab()
sent = []
api._execute_sfctl = lambda cmd, args: sent.append((cmd, args)) or {"success": True, "details": {}}
real_draft_of = tab_delivery.draft_of
tab_delivery.draft_of = lambda a, sid: "終端機裡的草稿"
at_end_of_chain()
res = json.loads(api.chat_send("s1", "從對話畫面說的"))
check("a send that only asks about a draft sends nothing and leaves the chain",
      res.get("code") == "draft" and not sent and agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 1, res)
tab_delivery.draft_of = lambda a, sid: ""
res = json.loads(api.chat_send("s1", "從對話畫面說的"))
check("…a send that goes clears it", res["success"] and len(sent) == 1 and agent_link.depth_for("s1") == 1)
tab_delivery.draft_of = real_draft_of

print("3. Telegram")
api, s, written = api_with_tab()
at_end_of_chain()
api._prepare_pane_for_input(s)
check("the bridge's prepare hook (before it types the person's message) clears it",
      agent_link.depth_for("s1") == 1)

print("4. what ShellBot types in itself")


class Ctx:
    """What agent_link.deliver needs: two open tabs, s1 (ours, with its terminal) and s2."""
    def __init__(self, api):
        self.api, self.sent = api, []

    def enabled(self):
        return True

    def resolve(self, role, sid):
        return {"示範": ("s1", "示範"), "研究": ("s2", "研究")}.get(role, ("", ""))

    def roster(self):
        return {"示範": {"label": "示範"}, "研究": {"label": "研究"}}

    def send(self, sid, text):
        self.sent.append((sid, text))
        if sid in self.api.sessions:
            self.api._send_text_to_session(self.api.sessions[sid], text, submit=True)
        return {"success": True}


api, s, written = api_with_tab()
agent_link._inbound_depth.clear()
agent_link.note_delivered("s9", agent_link.MAX_DEPTH - 1)        # the sender is 3 layers into a chain
ctx = Ctx(api)
r = agent_link.deliver(ctx, role="示範", sid="", text="照這個做", from_sid="s9", from_label="另一個 AI")
check("an A2A delivery books its depth on the tab, and typing it in does not clear that",
      r["success"] and ctx.sent and any("照這個做" in w for w in written)
      and agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 1, (r, agent_link._inbound_depth))

print("5. end to end")
stopped = agent_link.deliver(ctx, role="研究", sid="", text="再轉一次", from_sid="s1", from_label="示範")
check("at the end of the chain, what the tab sends is stopped",
      stopped["success"] is False and (stopped.get("details") or {}).get("blocked"), stopped)
api.write_input("s1", "\r")
agent_link._recent.clear()
again = agent_link.authorize(enabled=True, from_sid="s1", from_label="示範", to_role="研究", to_sid="",
                             text="新的事", roster=ctx.roster())
check("…the person writes to it, and it may send again, as layer 1", again[0] is True and again[2] == 1, again)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
