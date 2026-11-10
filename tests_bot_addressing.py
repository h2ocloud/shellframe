#!/usr/bin/env python3
"""Addressing wired through bot_names (plan A): delegate and A2A find a bot's tab by its
binding even after the tab was renamed; a role is never found from the middle of a name;
sfctl's tab commands take a name; A2A to a label, an alias or another case is authorized
as the roster key; delegate names its new tab for good (no auto rename on the first Enter).

Demo roster and tabs. Run: .venv/bin/python tests_bot_addressing.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-addressing-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import agent_link  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


ROSTER = {
    "研究": {"label": "研究-CLD", "cmd": "claude", "bot": {"memory": True}},
    "研究助理": {"label": "研究助理", "cmd": "claude"},
    "Coding": {"label": "Coding-CDX", "cmd": "codex", "bot": {"memory": True}},
    "時程信件": {"label": "時程信件-CLD", "cmd": "claude"},
}
main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True, "experimental_a2a": True},
                                        "agent_roster": ROSTER,
                                        "bots_sessions": {"s2": {"bot": "Coding"}}}, ensure_ascii=False), encoding="utf-8")


class Tab:
    def __init__(self, sid, label):
        self.sid, self._custom_label, self.alive = sid, label, True


api = Api.__new__(Api)
api.sessions = {"s1": Tab("s1", "研究-CLD"), "s2": Tab("s2", "工程師"), "s3": Tab("s3", "查資料")}

print("1. delegate and A2A find a bot's tab")
check("by its label (the title)", api._find_session_by_label("研究-CLD")[0] == "s1")
check("by its binding, though its tab was renamed", api._find_session_by_label("Coding-CDX")[0] == "s2",
      api._find_session_by_label("Coding-CDX"))
check("never a tab that only starts so", api._find_session_by_label("查")[0] == "")
check("a bot asleep: no tab (delegate opens one)", api._find_session_by_label("時程信件-CLD") == ("", None))

print("2. roles by name: no guessing from the middle")
check("a role's key, any case", Api._resolve_agent_role("coding", ROSTER)[0] == "Coding")
check("its old label", Api._resolve_agent_role("研究-CLD", ROSTER)[0] == "研究")
check("the start of one role", Api._resolve_agent_role("時程", ROSTER)[0] == "時程信件")
check("the start of two: none (it used to take the first one containing it)",
      Api._resolve_agent_role("研", ROSTER) == (None, None))
check("the middle of a name: none (an alias such as 信件 is a name, this is not)",
      Api._resolve_agent_role("ding", ROSTER) == (None, None))
r = api.delegate_task("研", "看一下")
check("delegate to an ambiguous name says which ones", not r["success"] and "研究、研究助理" in r["message"], r)

print("3. sfctl's tab commands take a name")
check("a bot's name → its tab", api._sid_for("Coding") == ("s2", ""))
check("a tab's title → that tab", api._sid_for("查資料") == ("s3", ""))
r = api._execute_sfctl("send", {"sid": "研", "text": "hi"})
check("ambiguous: refused before anything is typed, with the candidates", not r["success"] and "研究、研究助理" in r["message"], r)
r = api._execute_sfctl("peek", {"sid": "時程信件"})
check("a bot asleep: said, with how to wake it", not r["success"] and "在休息" in r["message"] and "a2a-send 時程信件" in r["message"], r)
r = api._execute_sfctl("state", {"sid": "沒有這個"})
check("no such name: said", not r["success"] and "找不到「沒有這個」" in r["message"], r)

print("4. A2A to any name of a role is authorized as its roster key")
seen = {}


def fake_authorize(**kw):
    seen.update(kw)
    return False, "stopped by the test", 0


real_authorize = agent_link.authorize
agent_link.authorize = fake_authorize


class Ctx:
    def enabled(self):
        return True

    def resolve(self, role, sid):
        return api._find_session_by_label(role)[0], role

    def roster(self):
        return ROSTER

    def role_key(self, role):
        return Api._resolve_agent_role(role, ROSTER)[0] or ""


try:
    for name in ("Coding-CDX", "coding", "Coding"):
        seen.clear()
        agent_link.deliver(Ctx(), role=name, sid="", text=f"hello {name}", from_sid="s3", from_label="查資料")
        check(f"{name!r} → authorized as 'Coding'", seen.get("to_role") == "Coding", seen.get("to_role"))
finally:
    agent_link.authorize = real_authorize

print("5. delegate names its new tab for good")
src = (HERE / "main.py").read_text(encoding="utf-8")
check("the rename is manual (no auto rename on the first Enter)",
      "self.rename_session(sid, label, manual=True)" in src[src.index("def delegate_task"):])

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
