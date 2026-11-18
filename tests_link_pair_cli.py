#!/usr/bin/env python3
"""Pairing from the command line: sfctl link-pair --mode, link-join, link-enable (api_remote link_pair / link_join /
link_enable).

Asked for: pair a computer with no window (a headless Linux VM) and choose the direction, so two Macs can be the
ones in charge and the others members. The Pair window has three directions; the command line had only "duplex",
and the link could be switched on only in Settings. Now:
  - sfctl link-pair --mode duplex|host|member → the window's three directions (duplex, host_controls,
    joiner_controls); no mode (Telegram's /link pair) is duplex as before; the message says which direction
  - sfctl link-join <host> <code> [--port] → join that computer's code (its direction stands)
  - sfctl link-enable on|off → the Settings switch, no restart; it says whether the link listens
  - end to end on two real FrameLinks: a member's code (joiner_controls) lets the joiner control it, not back
Run: .venv/bin/python tests_link_pair_cli.py
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import api_remote  # noqa: E402
import sfctl  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


print("the command line")
sent = []
sfctl._rpc = lambda cmd, args=None, timeout=0: sent.append((cmd, args or {})) or {"success": True, "message": "ok"}
sfctl._print_result = lambda res: None
cases = [
    (["link-pair"], ("link_pair", {"mode": "duplex"})),
    (["link-pair", "--mode", "host"], ("link_pair", {"mode": "host_controls"})),
    (["link-pair", "--mode", "member"], ("link_pair", {"mode": "joiner_controls"})),
    (["link-join", "198.51.100.7", "ABCD-1234"], ("link_join", {"host": "198.51.100.7", "port": 8767, "code": "ABCD-1234"})),
    (["link-join", "198.51.100.7", "ABCD-1234", "--port", "8777"], ("link_join", {"host": "198.51.100.7", "port": 8777, "code": "ABCD-1234"})),
    (["link-enable", "on"], ("link_enable", {"enabled": True})),
    (["link-enable", "off"], ("link_enable", {"enabled": False})),
]
for argv, want in cases:
    sent.clear()
    sys.argv = ["sfctl"] + argv
    try:
        sfctl.main()
    except SystemExit:
        pass
    check("sfctl " + " ".join(argv), sent == [want], sent)
sys.argv = ["sfctl", "link-pair", "--mode", "boss"]
try:
    sfctl.main()
    bad = False
except SystemExit as e:
    bad = e.code not in (0, None)
check("an unknown direction is refused by the parser", bad)

print("the app side")
modes = []
fake_link = types.SimpleNamespace(
    pairing_begin=lambda mode="duplex": modes.append(mode) or {"success": True, "code": "WXYZ-0001", "port": 8767,
                                                             "addresses": ["198.51.100.7"], "expires_in": 120},
    join=lambda host, port, code: {"success": True, "peer_name": "示範主機"})
host = api_remote.RemoteApiMixin.__new__(api_remote.RemoteApiMixin)
host._link = lambda: fake_link
host._notify_ui_sessions_changed = lambda: None
enabled = {}
host.link_set_enabled = lambda on: enabled.update(on=on) or json.dumps({"enabled": on, "running": on, "peers": []})
run = lambda cmd, args: api_remote.RemoteApiMixin._execute_sfctl(host, cmd, args)

r = run("link_pair", {"mode": "joiner_controls"})
check("link_pair passes the direction on", modes[-1] == "joiner_controls" and r["success"], (modes, r))
check("…and its message names it", "這台當從" in r["message"] and "WXYZ-0001" in r["message"], r["message"])
r = run("link_pair", {})
check("no direction (Telegram's /link pair): duplex, as before", modes[-1] == "duplex" and "雙向" in r["message"], (modes, r["message"]))
r = run("link_enable", {"enabled": True})
check("link_enable on turns the switch on and says it listens", enabled == {"on": True} and r["success"] and "在聽" in r["message"], r)
r = run("link_enable", {"enabled": False})
check("…and off", enabled == {"on": False} and r["success"] and "已關" in r["message"], r)
r = run("link_join", {"host": "198.51.100.7", "port": 8767, "code": "WXYZ-0001"})
check("link_join joins and names the computer", r["success"] and "示範主機" in r["message"], r)

print("end to end: a member's code")
from tests_frame_link import Node  # noqa: E402  (an in-process ShellBot stand-in with a real FrameLink on 127.0.0.1)
a, b = Node("demo-member"), Node("demo-mac")
try:
    code = a.link.pairing_begin(mode="joiner_controls")["code"]
    res = b.link.join("127.0.0.1", a.port, code)
    pa, pb = a.link.peers().get(b.fid, {}), b.link.peers().get(a.fid, {})
    check("the joiner (the Mac) joined", res.get("success") is True, res)
    check("the member keeps it as the one in charge of it (slave here), the joiner as its member (master there)",
          pa.get("mode") == "slave" and pb.get("mode") == "master", (pa.get("mode"), pb.get("mode")))
    check("the joiner may control the member; the member may not control the joiner",
          a.link._peer_may_control(b.fid) and not b.link._peer_may_control(a.fid))
finally:
    a.cleanup()
    b.cleanup()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
