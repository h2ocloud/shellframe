#!/usr/bin/env python3
"""The chat view on another computer's tab: POST /link/chat (api_chat._chat_remote) and link_chat.

Asked for: a tab on another computer shown as a conversation, the way this computer shows its own.
  - the computer that has the tab runs only view / send / send_now / answer / login, with their arguments
    by name, through the same chat_* calls its own chat view uses; anything else is refused (a file
    preview or "show in Finder" is a path on that computer)
  - a trouble in one call is an answer, not a dropped request
  - the viewer: the other computer's answer as it is; 404 (an older ShellBot) → old_peer, so the page can
    say to update it; refused → refused; no answer → unreachable
  - the route needs a peer that may control this computer
Run: .venv/bin/python tests_link_chat.py
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.modules.setdefault("webview", MagicMock())
import api_chat  # noqa: E402
import frame_link  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


calls = []


class Host(api_chat.ChatApiMixin):
    def chat_view(self, sid, limit=120, rev=""):
        calls.append(("view", sid, limit, rev))
        return json.dumps({"success": True, "turns": [], "rev": "r9"})

    def chat_send(self, sid, text, clear_draft=False):
        calls.append(("send", sid, text, clear_draft))
        return json.dumps({"success": True, "queued": False})

    def chat_answer(self, sid, n, qid):
        raise RuntimeError("boom")


h = Host()
print("the computer that has the tab")
r = h._chat_remote({"sid": "s7", "action": "view", "args": {"limit": 80, "rev": "r1"}})
check("view: chat_view with its arguments by name", r == {"success": True, "turns": [], "rev": "r9"}
      and calls[-1] == ("view", "s7", 80, "r1"), (r, calls))
h._chat_remote({"sid": "s7", "action": "send", "args": {"text": "示範訊息", "clear_draft": True}})
check("send: chat_send with the text and clear_draft", calls[-1] == ("send", "s7", "示範訊息", True), calls[-1])
h._chat_remote({"sid": "s7", "action": "send", "args": {"text": "x", "extra": "ignored"}})
check("an argument it does not take is left out", calls[-1] == ("send", "s7", "x", None), calls[-1])
for bad in ("file_preview", "reveal", "view_all", "", "__class__"):
    check(f"'{bad}' is refused", h._chat_remote({"sid": "s7", "action": bad}) == {"success": False, "code": "unknown_action"})
r = h._chat_remote({"sid": "s7", "action": "answer", "args": {"n": 1, "qid": "q"}})
check("a call that fails is an answer with a code", r["success"] is False and r["code"] == "error", r)
check("args that are not an object are ignored", h._chat_remote({"sid": "s7", "action": "view", "args": "x"})["success"])

print("the viewer")


class Link:
    def __init__(self, outcome):
        self.outcome, self.sent = outcome, []

    def _peer(self, pid):
        return {"id": pid} if pid == "peer7" else None

    def _addressable(self, peer):
        return True

    def _signed_request(self, peer, method, path, body, timeout=8.0):
        self.sent.append((method, path, json.loads(body)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def viewer(outcome):
    v = Host()
    v._link = lambda: link
    link = Link(outcome)
    return v, link


v, link = viewer({"success": True, "rev": "r2", "turns": [{"kind": "user_msg", "text": "示範"}]})
r = json.loads(v.link_chat("peer7", "r1", "view", json.dumps({"limit": 80, "rev": ""})))
check("the other computer's answer as it is", r["rev"] == "r2" and r["turns"][0]["text"] == "示範", r)
check("…asked with POST /link/chat {sid, action, args}",
      link.sent == [("POST", "/link/chat", {"sid": "r1", "action": "view", "args": {"limit": 80, "rev": ""}})], link.sent)
v, _ = viewer(frame_link.LinkHTTPError(404, "no such route"))
check("404 (an older ShellBot): old_peer", json.loads(v.link_chat("peer7", "r1", "view", "{}"))["code"] == "old_peer")
v, _ = viewer(frame_link.LinkHTTPError(403, "單向配對"))
check("refused: refused", json.loads(v.link_chat("peer7", "r1", "view", "{}"))["code"] == "refused")
v, _ = viewer(OSError("timed out"))
check("no answer: unreachable", json.loads(v.link_chat("peer7", "r1", "view", "{}"))["code"] == "unreachable")
v, _ = viewer({})
check("a peer that is not paired: unreachable", json.loads(v.link_chat("nobody", "r1", "view", "{}"))["code"] == "unreachable")
check("bad JSON for the arguments: sent as none", json.loads(v.link_chat("peer7", "r1", "view", "{oops")) == {})

print("the wiring")
src = (HERE / "frame_link.py").read_text(encoding="utf-8")
route = src.split('if path == "/link/chat":', 1)[1].split("if path", 1)[0]
check("POST /link/chat needs a peer that may control this computer, and runs chat_remote",
      "_peer_may_control(peer_id)" in route and '"chat_remote"' in route)
remote = (HERE / "api_remote.py").read_text(encoding="utf-8")
check("the dispatcher sends chat_remote to _chat_remote", 'elif cmd == "chat_remote":' in remote and "self._chat_remote(args)" in remote)
js = (HERE / "web" / "chat.js").read_text(encoding="utf-8")
check("the page sends a remote tab's calls through link_chat", "a.link_chat(s.peerId, s.rsid, action" in js)
login = (HERE / "web" / "tablogin.js").read_text(encoding="utf-8")
check("…and the sign-in card too", "root.SFChat.call(sid, 'login'" in login)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
