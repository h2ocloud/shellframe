#!/usr/bin/env python3
"""A name the person gives a tab is kept: the first message does not rename it.

Found on v0.42.0: `sfctl new claude --label research-1` shows the label until the first
message is sent, then the tab is renamed from what was said (the automatic name, a haiku
slug of the first message). `sfctl rename` did the same, and so did everything that ends in
it: Telegram's /rename, the local HTTP API's rename and a paired machine's rename. The
page's rename dialog was fine: it tells the app the name is the person's choice
(rename_session(..., manual=True)), which turns the automatic name off for that tab. The
sfctl command never said so.

  - sfctl new --label X, then the first message: X stays (the whole command, sfctl's
    two calls included, against the app's own dispatcher)
  - sfctl rename, Telegram's /rename and the HTTP API's rename, then the first message: stays
  - a tab nobody named still gets the automatic name, and so does a tab that only got a
    preset's name (a name nobody chose)
  - a name given while the automatic one is being worked out is not overwritten (the old guard)

Run: .venv/bin/python tests_label_sticks.py
"""
import contextlib
import io
import json
import sys
import tempfile
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-label-"))
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")   # fake tabs stay out of the real log

import main  # noqa: E402
import sfctl  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text("{}", encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


AUTO = "weekly-report"                                  # what the automatic naming would call a tab
main._haiku_slug = lambda text: AUTO if text else ""


def make_api():
    api = Api()
    api.sessions = {}
    api.bridge = api.line_bridge = None
    api._window = None
    api._persist_session_manifest = lambda *a, **k: None
    counter = [0]

    def new_session(cmd, cols, rows, source="manual", handoff=False, inherit_accounts=True):
        counter[0] += 1
        sid = f"s{counter[0]}"
        # a new tab, as Session builds it: the automatic name is armed, nobody has named it
        api.sessions[sid] = types.SimpleNamespace(sid=sid, cmd=cmd, _slug_pending=True,
                                                  _custom_label=None, _tmux_name=None)
        return sid

    api.new_session = new_session
    return api


def first_message(api, sid, text="write the weekly report"):
    """What the first Enter in a tab does (write_input -> _auto_name), and wait for the naming thread."""
    before = set(threading.enumerate())
    api._auto_name(sid, api.sessions[sid], text)
    for t in set(threading.enumerate()) - before:
        t.join(5)
    return api.sessions[sid]._custom_label


def sfctl_run(api, *argv, rename_fails=""):
    """`sfctl <argv>` against the app's own dispatcher: what it sends is what a command file would carry."""
    sent = []

    def rpc(cmd, args=None, timeout=15.0):
        sent.append((cmd, dict(args or {})))
        if rename_fails and cmd == "rename":              # the app did not answer in time
            return {"success": False, "message": rename_fails}
        return api._execute_sfctl(cmd, args or {})

    real_rpc, real_argv = sfctl._rpc, sys.argv
    sfctl._rpc, sys.argv = rpc, ["sfctl", *argv]
    out, code = io.StringIO(), None
    try:
        with contextlib.redirect_stdout(out):
            try:
                code = sfctl.main()
            except SystemExit as e:            # _print_result ends every command with its exit code
                code = e.code
    finally:
        sfctl._rpc, sys.argv = real_rpc, real_argv
    return out.getvalue(), sent, code


print("sfctl new --label")
api = make_api()
out, sent, code = sfctl_run(api, "new", "claude", "--label", "research-1")
check("the tab is made and named, sfctl ends with 0",
      code == 0 and api.sessions["s1"]._custom_label == "research-1" and "research-1" in out, (code, out, sent))
check("…and it counts as named: the first message does not arm a new name",
      api.sessions["s1"]._slug_pending is False, api.sessions["s1"]._slug_pending)
got = first_message(api, "s1")
check("the first message leaves the name alone", got == "research-1", f"it became {got!r}")

api = make_api()
out, sent, code = sfctl_run(api, "new", "claude", "--label", "research-2", rename_fails="Timeout - ShellBot not responding")
check("a name the app did not confirm is not reported as given",
      "as 'research-2'" not in out and "not confirmed" in out and "Timeout" in out and code == 0, (code, out))

print("sfctl rename")
api = make_api()
sid = api.new_session("claude", 200, 50)
out, sent, code = sfctl_run(api, "rename", sid, "ops-notes")
check("sfctl rename names the tab", api.sessions[sid]._custom_label == "ops-notes", (out, sent))
got = first_message(api, sid)
check("the first message leaves that name alone", got == "ops-notes", f"it became {got!r}")
sid = api.new_session("claude", 200, 50)
sfctl_run(api, "rename", sid, "ops-notes")
sfctl_run(api, "rename", sid, "ops-notes-2")
got = first_message(api, sid)
check("a second rename before any message is kept too", got == "ops-notes-2", f"it became {got!r}")

print("the other ways in end in the same command")
api = make_api()
sid_tg, sid_http = api.new_session("claude", 200, 50), api.new_session("claude", 200, 50)
# Telegram's /rename sends {"sid", "name"} to "rename" (bridge_telegram), as the HTTP API does (api_server)
res = api._execute_sfctl("rename", {"sid": sid_tg, "name": "from-phone"})
check("Telegram /rename (the dispatcher's rename): success", res.get("success") is True, res)
got = first_message(api, sid_tg)
check("…and the first message leaves its name", got == "from-phone", f"it became {got!r}")
res = api._execute_sfctl("rename", {"sid": sid_http, "name": "from-api"})
got = first_message(api, sid_http)
check("a name by the HTTP API (the dispatcher's rename) is kept too", got == "from-api", f"it became {got!r}")

print("what still gets the automatic name")
api = make_api()
sid = api.new_session("claude", 200, 50)
check("a tab nobody named", first_message(api, sid) == AUTO.replace("-", " "), api.sessions[sid]._custom_label)
api = make_api()
sid = api.new_session("claude", 200, 50)
api.rename_session(sid, "Claude")                       # a preset's name: the page sends it without manual
check("a tab that only has a preset's name still does", api.sessions[sid]._slug_pending is True)
check("…it is named from the first message", first_message(api, sid) == AUTO.replace("-", " "))
api = make_api()
sid = api.new_session("claude", 200, 50)
api.rename_session(sid, "my-name", manual=True)         # the page's rename dialog
check("the page's rename dialog (manual) was always kept", first_message(api, sid) == "my-name")

print("named while the automatic name is being worked out")
api = make_api()
sid = api.new_session("claude", 200, 50)
real = main._haiku_slug
main._haiku_slug = lambda text: (api.sessions[sid].__setattr__("_custom_label", "renamed-meanwhile"), AUTO)[1]
check("the later name stays", first_message(api, sid) == "renamed-meanwhile")
main._haiku_slug = real

print(f"\n{passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
