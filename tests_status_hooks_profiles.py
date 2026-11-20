#!/usr/bin/env python3
"""Status hooks reach every Claude tab, and a tab's last activity survives a restart.

Measured: nine tabs never reported their state. They run under ShellFrame account
profiles, whose own settings.json is their user settings; the hooks were only in
~/.claude/settings.json, which such a tab reads as *project* settings, i.e. only
when its working directory is ~. And after a restart every tab looked about 45 s
idle, because the idle time came from PTY output that each reattach repaints.

  - the hooks are written into ~/.claude and every account profile; other keys and
    other hooks in those files are left alone; installing twice adds nothing
  - switching the hooks on once (they are in ~/.claude) is enough: profiles that
    lack them, including new ones, get them when the status monitor starts
  - the settings page counts every file, so a partial install is visible
  - switching off removes them everywhere
  - a tab's last activity comes from its transcript and its hooks, not from the
    repaint a restart causes

Run: .venv/bin/python tests_status_hooks_profiles.py
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")  # fake tabs stay out of the real log

import main  # noqa: E402
from main import Api  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="sf-hooks-"))
main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text("{}", encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


home = TMP / "home" / ".claude" / "settings.json"
home.parent.mkdir(parents=True)
home.write_text(json.dumps({"permissions": {"allow": ["Bash"]},
                            "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "say done"}]}]}}),
                encoding="utf-8")
profiles = TMP / "profiles"
(profiles / "claude-a").mkdir(parents=True)
(profiles / "claude-a" / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "x"}}),
                                                     encoding="utf-8")
(profiles / "claude-b").mkdir(parents=True)          # no settings.json yet

api = Api.__new__(Api)
api.sessions = {}
api._hook_events = {}
api._CLAUDE_SETTINGS_PATH = home
api._CLAUDE_PROFILES_ROOT = profiles
info = lambda: json.loads(api.get_status_hooks_info())
load = lambda p: json.loads(p.read_text(encoding="utf-8"))

print("targets")
labels = [t[0] for t in api._status_hook_targets()]
check("~/.claude and every account profile are targets", labels == ["~/.claude", "profile claude-a", "profile claude-b"],
      labels)
check("nothing installed yet", info()["installed"] is False and info()["installed_count"] == 0 and info()["total"] == 3)

print("sync from ~/.claude")
api._write_status_hooks(home, True)
check("only ~/.claude: the page shows a partial install", info()["installed"] is False and info()["installed_count"] == 1)
check("nothing is synced while the hooks are off", True)
changed = api._sync_status_hooks_to_profiles()
check("the monitor's start-up sync fills in both profiles", changed == 2 and info()["installed"] is True, info())
a = load(profiles / "claude-a" / "settings.json")
check("a profile's own settings are kept", a.get("statusLine") == {"type": "command", "command": "x"})
check("a profile with no settings.json gets one", (profiles / "claude-b" / "settings.json").exists())
check("every event gets the hook", all(any("sf_agent_hook.py" in json.dumps(g) for g in a["hooks"].get(ev, []))
                                       for ev in api._HOOK_EVENTS))
h = load(home)
check("other hooks and keys in ~/.claude survive", h["permissions"] == {"allow": ["Bash"]}
      and any("say done" in json.dumps(g) for g in h["hooks"]["Stop"]))
check("syncing again changes nothing", api._sync_status_hooks_to_profiles() == 0)
api.set_status_hooks_enabled(True)
stop = load(home)["hooks"]["Stop"]
check("installing twice adds no duplicate: one status entry and one stop check on Stop",
      sum("sf_agent_hook.py" in json.dumps(g) and "--stop-check" not in json.dumps(g) for g in stop) == 1
      and sum("--stop-check" in json.dumps(g) for g in stop) == 1, stop)
chk = [h for g in stop for h in g.get("hooks", []) if "--stop-check" in h.get("command", "")][0]
check("the stop check is synchronous with a short timeout (it may block a stop)",
      not chk.get("async") and chk.get("timeout") == 5, chk)

print("an older install gets the stop check")
for path in (home, profiles / "claude-a" / "settings.json"):
    d = load(path)
    d["hooks"]["Stop"] = [g for g in d["hooks"]["Stop"] if "--stop-check" not in json.dumps(g)]
    path.write_text(json.dumps(d), encoding="utf-8")
check("without it the install counts as incomplete", not api._hooks_installed_in(home))
check("start-up sync adds it to ~/.claude and the profile", api._sync_status_hooks_to_profiles() == 2
      and api._hooks_installed_in(home) and api._hooks_installed_in(profiles / "claude-a" / "settings.json"))
check("…and keeps the user's own Stop hook", any("say done" in json.dumps(g) for g in load(home)["hooks"]["Stop"]))

print("partial and off")
api._write_status_hooks(profiles / "claude-b" / "settings.json", False)
check("one profile missing: partial again", info()["installed_count"] == 2 and info()["installed"] is False)
api._hook_events["s1"] = {"state": "working", "ts": time.time()}
r = json.loads(api.set_status_hooks_enabled(False))
check("switching off removes them everywhere", r["installed_count"] == 0 and not api._hook_events)
check("…and leaves the user's own hooks", any("say done" in json.dumps(g) for g in load(home)["hooks"]["Stop"]))
check("…and the profile's other settings", "statusLine" in load(profiles / "claude-a" / "settings.json")
      and "hooks" not in load(profiles / "claude-a" / "settings.json"))
check("off means the start-up sync installs nothing", api._sync_status_hooks_to_profiles() == 0
      and info()["installed_count"] == 0)
r = json.loads(api.set_status_hooks_enabled(True))
check("switching on installs everywhere", r["installed"] is True and r["installed_count"] == 3)

print("another copy's hook path")
# 11-19: a test run from a development folder started the status monitor; the start-up sync wrote that
# folder's hook path for the new SessionStart event into the real settings, and the app, which counted any
# sf_agent_hook.py entry as installed, never put its own back.
other = 'python3 "/some/old/copy/sf_agent_hook.py"'
d = load(home)
d["hooks"]["SessionStart"] = [{"matcher": "", "hooks": [{"type": "command", "command": other, "async": True, "timeout": 10}]}]
for g in d["hooks"]["Stop"]:
    for hk in g.get("hooks") or []:
        if "--stop-check" in hk.get("command", ""):
            hk["command"] = other + " --stop-check"
home.write_text(json.dumps(d), encoding="utf-8")
check("an entry that runs another copy's hook is not installed", not api._hooks_installed_in(home))
check("start-up sync rewrites it (and only that file changes)", api._sync_status_hooks_to_profiles() == 1)
cmds = [hk["command"] for gs in load(home)["hooks"].values() for g in gs for hk in (g.get("hooks") or [])
        if "sf_agent_hook.py" in hk.get("command", "")]
check("every one of our entries now runs this program's hook", cmds and all(
    c in (api._sf_hook_command(), api._sf_hook_command() + " --stop-check") for c in cmds), cmds)
check("…the stop check kept its flag, once", sum("--stop-check" in c for c in cmds) == 1, cmds)
check("…nothing added twice", len(load(home)["hooks"]["SessionStart"]) == 1)
check("…the user's own Stop hook is still there", any("say done" in json.dumps(g) for g in load(home)["hooks"]["Stop"]))

print("a test never writes the real home's settings")
import os  # noqa: E402
try:
    import pwd  # noqa: E402
    real = Path(pwd.getpwuid(os.getuid()).pw_dir)
except ImportError:
    real = None
if real is not None:
    check("the real ~/.claude/settings.json counts as the real one", api._real_user_file(real / ".claude" / "settings.json"))
    check("…and a real account profile", api._real_user_file(
        real / ".config" / "shellframe" / "account-profiles" / "claude" / "x" / "settings.json"))
check("this test's own copy does not", not api._real_user_file(home))
guarded = TMP / "guarded" / "settings.json"
guarded.parent.mkdir()
guarded.write_text("{}", encoding="utf-8")
keep = api._real_user_file
api._real_user_file = lambda p: Path(p) == guarded            # stand-in for the real file: never the real one here
err = api._write_status_hooks(guarded, True)
api._real_user_file = keep
check("writing it during a test is refused and the file is untouched",
      "test" in err and guarded.read_text(encoding="utf-8") == "{}", err)

print("last activity across a restart")


class Tracker:
    def __init__(self, res):
        self.res = res

    def last_result(self, sid, max_age=None):
        return (self.res, 1.0)


class Tab:
    def __init__(self):
        self._custom_label = "worker"
        self.cmd = "claude"
        self.lock = threading.Lock()
        self._recent = bytearray()
        self._last_output_activity_time = time.time()      # the reattach repaint just now


now = time.time()
api._hook_events = {}
api._status_tracker = Tracker({"state": "done", "activity_at": now - 3600})
tab = Tab()
api.sessions = {"s1": tab}
check("the transcript's last write wins over a restart's repaint",
      abs(api._last_activity_at("s1", tab, tab._last_output_activity_time) - (now - 3600)) < 1)
row = api._session_state_row("s1", tab, now, with_error=False)
check("…so the state row says idle for an hour, not 0 s", 3590 < row["idle_for_s"] < 3700, row["idle_for_s"])
api._hook_events["s1"] = {"state": "done", "ts": now - 60}
check("a newer hook event moves it forward", abs(api._last_activity_at("s1", tab) - (now - 60)) < 1)
api._hook_events = {}
api._status_tracker = Tracker({"state": "idle"})
check("with no transcript or hook, the output time is used",
      api._last_activity_at("s1", tab, 123.0) == 123.0)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
