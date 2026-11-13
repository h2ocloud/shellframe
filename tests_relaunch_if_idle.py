#!/usr/bin/env python3
"""`sfctl relaunch <tab> [--if-idle]`: a tab's CLI restarted on its updated version, the same
conversation resumed, and with --if-idle only while the tab is free.

Asked for a rolling update of every tab to a new Claude Code without cutting any conversation.
The restart itself (relaunch_session, the sidebar's ⟳) already resumed the conversation; what was
missing was a way to run it from sfctl that refuses a tab that is not free at that very moment.
  - --if-idle: a turn, a question, a draft, a Telegram turn or a model switch → refused with the
    reason, nothing restarted; a free tab → restarted
  - without it: restarted as before (the person asked for it)
  - the answer carries the id it resumed, so a script can check it is the same conversation
  - a bot's or tab's name works as for close and send
  - sfctl has the command, and a face's right-click offers "Restart (keep conversation)"

Run: .venv/bin/python tests_relaunch_if_idle.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-relaunch-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")

import api_remote  # noqa: E402
import tab_delivery  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


CSID = "0b1c2d3e-0000-4000-8000-000000000001"
remote = api_remote.RemoteApiMixin.__new__(api_remote.RemoteApiMixin)
remote.sessions = {"s7": object()}
relaunched = []
remote.relaunch_session = lambda sid: (relaunched.append(sid), json.dumps(
    {"success": True, "sid": sid, "provider": "claude", "resumed": CSID, "message": "已重啟"}))[1]
remote._sid_for = lambda name: ("s7", "") if name == "研究" else ("", f"no tab {name}")
busy = {"s7": "working"}
real_reason = tab_delivery.busy_reason
tab_delivery.busy_reason = lambda api, sid: busy.get(sid, "")
run = lambda args: api_remote.RemoteApiMixin._execute_sfctl(remote, "relaunch", args)

print("--if-idle")
for why in ("working", "decision", "draft", "telegram", "model"):
    busy["s7"] = why
    out = run({"sid": "s7", "if_idle": True})
    check(f"not free ({why}): refused with the reason, nothing restarted",
          out.get("success") is False and out.get("code") == "busy" and out.get("reason") == why and not relaunched,
          (out, relaunched))
busy["s7"] = ""
out = run({"sid": "s7", "if_idle": True})
check("free: restarted", out.get("success") and relaunched == ["s7"], (out, relaunched))
check("…and the answer carries the id it resumed", (out.get("details") or {}).get("resumed") == CSID, out)

print("without --if-idle")
busy["s7"] = "working"
relaunched.clear()
out = run({"sid": "s7"})
check("restarted as before, busy or not (the person asked)", out.get("success") and relaunched == ["s7"], out)

print("names")
relaunched.clear()
busy["s7"] = ""
out = run({"sid": "研究", "if_idle": True})
check("a bot's or tab's name works", out.get("success") and relaunched == ["s7"], (out, relaunched))
tab_delivery.busy_reason = real_reason

print("the ways in")
sfctl = (HERE / "sfctl.py").read_text(encoding="utf-8")
check("sfctl relaunch <tab> --if-idle", 'add_parser("relaunch"' in sfctl and '"--if-idle"' in sfctl
      and '_rpc("relaunch", {"sid": args.sid, "if_idle": bool(args.if_idle)}' in sfctl)
bots_js = (HERE / "web" / "bots.js").read_text(encoding="utf-8")
check("a face's right-click offers 'Restart (keep conversation)', through the sidebar's confirm",
      'data-act="relaunch"' in bots_js and "host().confirmRelaunchTab(sid)" in bots_js
      and "confirmRelaunchTab }" in (HERE / "web" / "index.html").read_text(encoding="utf-8"))
accounts = (HERE / "api_accounts.py").read_text(encoding="utf-8")
check("relaunch_session reports the id it resumed", '"resumed": m.group(1) if m else ""' in accounts)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
