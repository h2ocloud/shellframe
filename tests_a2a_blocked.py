#!/usr/bin/env python3
"""A message the chain limit stops, from `sfctl a2a-send` to the tab and back to the sender.

agent_link's own rules are in tests_agent_link.py. This is the whole path an agent takes: the
sfctl command the app runs (a2a_host through Api._execute_sfctl), stand-in tabs behind the real
per-tab queue (tab_delivery), the text the open tab is given, what sfctl prints and exits with,
the log, and what a phone or the chat view reads back from the tab's record (api_remote.with_senders).

  - a message one layer past the cap is typed into the open tab behind the "not passed on" header;
    sfctl prints ERR with the bounce (stopped, depth 5 of 4, the other side has it, reference only)
    and exits 1; a2a-log has one `blocked` entry; the tab's own reply is refused (nothing typed in)
  - a busy tab: it is queued, the bounce says so, and the text goes in as data when the turn ends
  - a hand-off: nothing to report, no hand-off record, the bounce says no a2a-result will come
  - a role with no open tab is not opened for it; a normal message is what it always was
  - the tab's record reads back as the sender's, `from.blocked`, which is what the chat view draws

Run: .venv/bin/python tests_a2a_blocked.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-a2a-blocked-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")  # fake tabs stay out of the real log

import agent_link  # noqa: E402
agent_link.STATE_DIR = TMP / "state"
agent_link.LOG_FILE = agent_link.STATE_DIR / "agent_link.json"
agent_link.STATE_DIR.mkdir(parents=True, exist_ok=True)
import a2a_host  # noqa: E402
import api_remote  # noqa: E402
import main  # noqa: E402
import sfctl  # noqa: E402
import tab_delivery as td  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_a2a": True}, "agent_roster": {
    "coder": {"label": "coder", "cmd": "claude", "responsibility": "code"},
    "boss": {"label": "boss", "cmd": "claude", "responsibility": "plans"},
    "ghost": {"label": "ghost", "cmd": "claude", "responsibility": "a role with no tab open"}}}), encoding="utf-8")
td.POLL, td.TAKEN_WAIT = 0.05, 0.4

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Tab:
    def __init__(self, sid, label):
        self.sid, self._custom_label, self.cmd = sid, label, "claude"
        self.alive, self.lock, self._recent = True, threading.Lock(), bytearray()
        self._tmux_name = None
        self._startup_trust_pending = True
        self._last_output_activity_time = time.time()


def make_api(*tabs):
    api = Api.__new__(Api)
    api.sessions = {sid: Tab(sid, label) for sid, label in tabs}
    api.bridge = api.line_bridge = None
    api._window = None
    api.state = {sid: "" for sid, _ in tabs}
    api.sent = []
    api.opened = []
    api.prompt_at = {}

    def send(s, text, submit=False):
        api.sent.append((s.sid, text))
        api.prompt_at[s.sid] = time.time()
    api._send_text_to_session = send
    api._agent_state_for_list = lambda sid: api.state.get(sid, "")
    api._agent_status_snapshot = lambda sid: ({"prompt_at": api.prompt_at.get(sid, 0)}, 0)
    api.delegate_task = lambda role, task, from_sid="": api.opened.append((role, task)) or {
        "success": True, "message": f"Delegated to {role} (s9)", "details": {"sid": "s9"}}
    return api


td.capture = lambda api, sid: ""                  # no input box on screen: nothing the user typed
api = make_api(("s1", "coder"), ("s2", "boss"))


def printed(result):
    """What sfctl prints for a result, and the exit code it ends with."""
    buf, code = io.StringIO(), None
    with contextlib.redirect_stdout(buf):
        try:
            sfctl._print_result(result)
        except SystemExit as e:
            code = e.code
    return buf.getvalue(), code


def send(**kw):
    """What sfctl a2a-send / a2a-handoff hands the app: the same dict `sfctl` writes."""
    cmd = "a2a_handoff" if kw.pop("handoff", False) else "a2a_send"
    return api._execute_sfctl(cmd, {"from_sid": "s2", **kw})


def reset():
    agent_link._recent.clear()
    agent_link._inbound_depth.clear()
    agent_link._seen_ids.clear()
    agent_link._fingerprints.clear()
    for path in (agent_link.LOG_FILE, agent_link.handoff_path()):
        path.unlink(missing_ok=True)
    api.sent.clear()
    api.opened.clear()
    api.state.update({sid: "" for sid in api.sessions})


print("a message that is not over the cap: what it always was")
reset()
res = send(target="coder", text="relay 的 timeout 改成 30 秒")
out, code = printed(res)
check("it is delivered with the usual header; sfctl prints OK and exits 0",
      res["success"] is True and api.sent[-1][0] == "s1" and api.sent[-1][1].startswith("[[A2A：來自「boss」的訊息，不是使用者本人")
      and out.startswith("OK ") and code == 0, (res, out, code))

print("one layer past the cap: the tab gets it as data, the sender is bounced")
reset()
agent_link._inbound_depth["s2"] = agent_link.MAX_DEPTH            # boss holds a 4th-layer message
res = send(target="coder", text="relay 的 timeout 已改成 30 秒")
text = api.sent[-1][1] if api.sent else ""
check("the open tab is typed the text behind the not-passed-on header (nothing else went in)",
      len(api.sent) == 1 and api.sent[0][0] == "s1" and text.startswith("[[A2A（未轉達）：來自「boss」的訊息因轉發層數超過上限（5／4）被擋下")
      and text.endswith("]]\nrelay 的 timeout 已改成 30 秒") and "a2a-result" not in text, api.sent)
out, code = printed(res)
check("sfctl prints ERR with the bounce: stopped (5 of 4), the other side has it, reference only",
      out.startswith("ERR 被擋下（轉發層數 5，上限 4）；對方已經收到，只當參考。") and "不要重送" in out, out)
check("…the details say it too (blocked, delivered, depth, limit) and the exit code is not 0",
      "  blocked: True" in out and "  delivered: True" in out and "  depth: 5" in out and "  limit: 4" in out
      and code == 1, (out, code))
log = a2a_host.handle(api, "a2a_log", {})
check("a2a-log has the entry: blocked → delivered as data",
      "blocked boss -> coder chain depth 5 over limit 4（擋下 → 以資料送達）" in log["message"], log["message"])
turn = {"kind": "user_msg", "text": text}
api_remote.with_senders([turn])
check("the tab's record reads back as boss's, blocked: what the chat view and a phone draw it from",
      turn.get("from") == {"kind": "a2a", "name": "boss", "handoff": False, "blocked": True}, turn)
n_sent = len(api.sent)
back = api._execute_sfctl("a2a_send", {"target": "boss", "text": "收到，我回你", "from_sid": "s1"})
out, code = printed(back)
check("the tab cannot answer it through A2A: refused, nothing typed into boss, exit 1, said in plain words",
      back["success"] is False and len(api.sent) == n_sent and code == 1
      and out.startswith("ERR 被擋下（轉發層數 6，上限 4）；沒有送達。") and "  delivered: False" in out, (back, out))
entries = [e["kind"] for e in agent_link.history(5)]
check("…and the log has it as a refusal", entries[-2:] == ["blocked", "refused"], entries)

print("English")
reset()
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
agent_link._inbound_depth["s2"] = agent_link.MAX_DEPTH
res = send(target="coder", text="relay timeout is 30 s now")
out, code = printed(res)
check("the header and the bounce are in English",
      api.sent[-1][1].startswith('[[A2A (not passed on): a message from "boss" was blocked because the chain depth went over the limit (5/4)')
      and out.startswith("ERR Blocked (chain depth 5, limit 4): the recipient has it, as reference only.") and code == 1, (api.sent, out))
check("…and the log line", "blocked boss -> coder chain depth 5 over limit 4 (blocked → delivered as data)"
      in a2a_host.handle(api, "a2a_log", {})["message"])
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"

print("a busy tab: queued, and it goes in as data when the turn ends")
reset()
agent_link._inbound_depth["s2"] = agent_link.MAX_DEPTH
api.state["s1"] = "working"
res = send(target="coder", text="這則要排隊")
check("nothing is typed into the running turn; the bounce says it is queued, as reference only",
      api.sent == [] and res["success"] is False and res["details"].get("queued") is True and res["details"].get("delivered") is True
      and "已排進對方的佇列，送達後只當參考" in res["message"], res)
api.state["s1"] = ""
check("…when the turn ends it goes in behind the not-passed-on header",
      wait_for(lambda: any(s == "s1" and t.startswith("[[A2A（未轉達）：") and t.endswith("這則要排隊") for s, t in api.sent)), api.sent)
wait_for(lambda: not td.pending("s1"))

print("a hand-off past the cap: no task, nothing to report")
reset()
agent_link._inbound_depth["s2"] = agent_link.MAX_DEPTH
res = send(target="coder", text="把 relay 的測試補上", handoff=True)
hid = (res.get("details") or {}).get("id")
out, code = printed(res)
check("the tab gets the words as data (no a2a-result to run); sfctl exits 1 and says none will come",
      api.sent[-1][1].startswith("[[A2A（未轉達）：") and "a2a-result" not in api.sent[-1][1]
      and "不會有 a2a-result 回來" in out and code == 1, (api.sent, out))
check("…there is no hand-off to report: a2a-result on its id finds none",
      agent_link._get_handoff(hid) is None
      and api._execute_sfctl("a2a_result", {"id": hid, "text": "做完了", "from_sid": "s1"})["success"] is False)

print("a role with no tab open is not opened for it")
reset()
res = send(target="ghost", text="給還沒開的角色")
check("under the cap it is delegated (a tab is opened), as it always was", res["success"] is True and len(api.opened) == 1, (res, api.opened))
reset()
agent_link._inbound_depth["s2"] = agent_link.MAX_DEPTH
res = send(target="ghost", text="給還沒開的角色")
out, code = printed(res)
check("past the cap nothing is opened or typed; the bounce says it was not delivered, exit 1",
      api.opened == [] and api.sent == [] and code == 1
      and out.startswith("ERR 被擋下（轉發層數 5，上限 4）；沒有送達。"), (api.opened, api.sent, out))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
