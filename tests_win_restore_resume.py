#!/usr/bin/env python3
"""After ShellBot restarts on Windows, Claude tabs resume their conversation (`--resume <uuid>`).

Measured on a Windows machine: every Claude tab came back with `--session-id <new uuid>` and no
`--resume`: _cmd_with_resume had no uuid. There is no tmux there, so the restart rebuilds each tab
from config.session_manifest (cmd + claude_session_id, kept current by the hooks).

Root cause (reproduced here, IS_WIN patched, no real Windows needed): the Telegram bridge's autostart
runs on a thread from launch, before the page asks for the restore, and ends in
_start_bridge_locked → _persist_session_manifest. With no tab open yet that wrote an EMPTY
session_manifest; _manifest_entries then fell back to the old soft list (`session_list`: the cmd as
first typed, no uuid), so Session() minted a new --session-id. tmux hides it on macOS (the tabs are
reattached), so it only showed on Windows (and on a Mac after a reboot).

  1. the flow: tabs and hook-reported uuids → "restart" → autostart's persist before restore → restore:
     each tab comes back `--resume <its current uuid>`, no new --session-id, the manifest still has
     every entry and uuid afterwards
  2. the manifest is held while the restore has not run (and given up after a while); a persist during
     the restore does not drop tabs that are not back yet; the restore's own persist is not held;
     a closed tab still leaves the manifest once restored
  3. Windows paths: _cmd_with_resume on `C:\\...\\claude.cmd`, quoted paths with spaces
  4. hooks on Windows run the Python that runs ShellBot (`python3` is often missing there), forward
     slashes; an old `python3` entry is not "installed" any more so it is rewritten
  5. a plain `bash` on Windows is Git Bash when it is installed, not the WSL launcher in System32
  6. a tab whose process ends right after it started says so in the debug log

Run: .venv/bin/python tests_win_restore_resume.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-winresume-"))
os.environ["HOME"] = str(TMP / "home")
os.environ["USERPROFILE"] = str(TMP / "home")
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_DIR = TMP / "cfg"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


U1, U2, U3 = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
              "33333333-3333-4333-8333-333333333333")
CLAUDE = "claude --permission-mode bypassPermissions --dangerously-skip-permissions"


def transcript(uuid):
    d = TMP / "home" / ".claude" / "projects" / "C--Users-demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{uuid}.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")


def make_api():
    api = object.__new__(Api)
    api.sessions = {}
    api._counter = 0
    api._output_event = threading.Event()
    api._start_startup_trust_watcher = lambda sid, s: None
    api._sync_bridge_sessions = lambda: None
    api.bridge = api.line_bridge = None
    api._hook_events = {}
    api._status_cache = {}
    api._bots_tab_launch_cmd = lambda sid, cmd: cmd
    return api


def cfg():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))


real_start = main.Session._start
main.Session._start = lambda self, cols, rows: None
main.IS_WIN = True
main._has_tmux = lambda: False
main.tab_delivery.resume = lambda api: None


def open_tab(api, sid, cmd, label):
    """What new_session does for the parts that matter here."""
    s = main.Session(sid, cmd, 120, 30, on_data=api._output_event.set)
    s._custom_label = label
    api.sessions[sid] = s
    api._save_soft_session(sid, cmd)
    api._persist_session_manifest()
    return s


print("1. the flow, with the autostart's persist before the restore")
main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "bridge": {"bot_token": "x"}}), encoding="utf-8")
a1 = make_api()
s1 = open_tab(a1, "s1", CLAUDE, "寫程式")
s2 = open_tab(a1, "s2", CLAUDE, "研究")
s3 = open_tab(a1, "s3", "powershell", "終端")
check("a new claude tab starts on a --session-id of its own", s1.session_id and f"--session-id {s1.session_id}" in s1.cmd, s1.cmd)
spawn1 = s1.session_id
a1._on_agent_event({"sid": "s1", "event": "Stop", "session_id": U2, "transcript_path": ""})     # /clear: the hook reports the current one
transcript(U2)
transcript(s2.session_id)
m = {e["sid"]: e for e in cfg()["session_manifest"]}
check("the hook's uuid is in the manifest (s1), the spawn uuid for the tab that never reported (s2)",
      m["s1"]["claude_session_id"] == U2 and m["s2"]["claude_session_id"] == s2.session_id and "claude_session_id" not in m["s3"], m)
spawn2 = s2.session_id
# /resume in a tab fires only SessionStart (no prompt, no tool, no stop): it must still record the conversation, with no state
check("SessionStart is one of the installed hook events (so /resume and /clear are seen)", "SessionStart" in Api._HOOK_EVENTS, Api._HOOK_EVENTS)
before_state = (a1._hook_events.get("s1") or {}).get("state")
r = a1._on_agent_event({"sid": "s1", "event": "SessionStart", "session_id": U3, "transcript_path": ""})
check("a SessionStart (/resume) records the tab's new conversation in the manifest, and changes no state",
      {e["sid"]: e for e in cfg()["session_manifest"]}["s1"]["claude_session_id"] == U3
      and (a1._hook_events.get("s1") or {}).get("state") == before_state and "ignored" in str(r.get("message", "")), (r, before_state))
a1._on_agent_event({"sid": "s1", "event": "Stop", "session_id": U2, "transcript_path": ""})     # back to U2 for what follows

a2 = make_api()                                   # ShellBot started again: no tabs yet
import inspect  # noqa: E402
check("Api.__init__ starts with the hold on", "_manifest_hold_until = time.monotonic()" in inspect.getsource(Api.__init__))
a2._manifest_hold_until = time.monotonic() + Api._MANIFEST_HOLD_S      # what __init__ does
a2._persist_session_manifest()                    # the bridge autostart (api_bridges._start_bridge_locked) reaches this first
after_autostart = cfg().get("session_manifest")
check("the autostart's persist before the restore leaves the manifest as it was", after_autostart and len(after_autostart) == 3, after_autostart)
out = json.loads(a2.restore_tmux_sessions(120, 30))
cmds = {e["sid"]: e["cmd"] for e in out}
check("s1 comes back --resume with the current (hook) uuid, not a new --session-id",
      f"--resume {U2}" in cmds["s1"] and "--session-id" not in cmds["s1"] and spawn1 not in cmds["s1"], cmds.get("s1"))
check("s2 comes back --resume with its uuid", f"--resume {spawn2}" in cmds["s2"] and "--session-id" not in cmds["s2"], cmds.get("s2"))
check("the shell tab is left alone", cmds["s3"] == "powershell", cmds.get("s3"))
check("after the restore the manifest holds all three, with the uuids",
      {e["sid"]: e.get("claude_session_id") for e in cfg()["session_manifest"]}.get("s1") == U2 and len(cfg()["session_manifest"]) == 3,
      cfg()["session_manifest"])

print("2. holding the manifest while the restore has not run")
a3 = make_api()
a3._manifest_hold_until = time.monotonic() + 60
before = json.dumps(cfg()["session_manifest"])
a3._persist_session_manifest()
check("held: a persist writes nothing", json.dumps(cfg()["session_manifest"]) == before)
a3._manifest_hold_until = time.monotonic() - 1
a3._persist_session_manifest()
check("the hold ends by itself (an app with no page): the persist writes again", cfg()["session_manifest"] == [])
a4 = make_api()
main.CONFIG_FILE.write_text(json.dumps({"session_manifest": json.loads(before)}), encoding="utf-8")
a4._manifest_hold_until = time.monotonic() + 60
a4.restore_tmux_sessions(120, 30)
check("the restore ends the hold, and its own persist is not held", a4._manifest_hold_until == 0 and len(cfg()["session_manifest"]) == 3, cfg())
a4.close_session = None
a4._drop_soft_session("s3")
a4.sessions.pop("s3")
a4._persist_session_manifest()
check("once restored, a closed tab leaves the manifest", [e["sid"] for e in cfg()["session_manifest"]] == ["s1", "s2"])

print("3. Windows paths")
R = Api._cmd_with_resume
w = R(r"C:\Users\demo\AppData\Roaming\npm\claude.cmd --permission-mode bypassPermissions", U1)
check("a backslash path to claude.cmd gets --resume (shlex used to eat the backslashes)", f"--resume {U1}" in w and "--permission-mode bypassPermissions" in w, w)
w = R(r'"C:\Program Files\Claude\claude.exe" --model opus', U1)
check("a quoted path with a space", f"--resume {U1}" in w and "--model opus" in w, w)
check("a path with a session id already on it", f"--resume {U1}" in R(r"C:\x\claude.exe --session-id " + U3, U1) and U3 not in R(r"C:\x\claude.exe --session-id " + U3, U1))
check("codex and a shell are not touched", R(r"C:\x\bash.exe -l", U1) == r"C:\x\bash.exe -l")

print("4. the status hook on Windows")
import api_status  # noqa: E402
a5 = make_api()
api_status._IS_WIN = True
real_exe = sys.executable
sys.executable = r"C:\Users\demo\AppData\Local\Programs\Python\Python312\pythonw.exe"
cmd = a5._sf_hook_command()
check("it runs the Python that runs ShellBot (the console one next to pythonw), forward slashes, not python3",
      cmd.startswith('"C:/Users/demo/AppData/Local/Programs/Python/Python312/python.exe" "') and "python3" not in cmd and "sf_agent_hook.py" in cmd, cmd)
sys.executable = real_exe
main.IS_WIN = False
api_status._IS_WIN = False
check("elsewhere it is python3 as before", a5._sf_hook_command().startswith("python3 "))
main.IS_WIN = True
api_status._IS_WIN = True
settings = TMP / "settings.json"
a5._CLAUDE_SETTINGS_PATH = settings
old = {"hooks": {ev: [{"matcher": "", "hooks": [{"type": "command", "command": 'python3 "C:\\app\\sf_agent_hook.py"'}]}] for ev in a5._HOOK_EVENTS}}
old["hooks"]["Stop"].append({"matcher": "", "hooks": [{"type": "command", "command": 'python3 "C:\\app\\sf_agent_hook.py" --stop-check'}]})
settings.write_text(json.dumps(old), encoding="utf-8")
check("an old python3 entry is not counted as installed on Windows", a5._hooks_installed_in(settings) is False)
check("…and the rewrite replaces it, one entry per event, the other hooks kept",
      a5._write_status_hooks(settings, True) == "" and "python3" not in settings.read_text(encoding="utf-8")
      and all(len(json.loads(settings.read_text())["hooks"][ev]) == (2 if ev == "Stop" else 1) for ev in a5._HOOK_EVENTS)
      and a5._hooks_installed_in(settings) is True, settings.read_text()[:300])
main.IS_WIN = False
api_status._IS_WIN = False
settings.write_text(json.dumps(old), encoding="utf-8")
check("on a Mac an old entry stays installed", a5._hooks_installed_in(settings) is True)
main.IS_WIN = True
api_status._IS_WIN = True

print("5. bash on Windows")
import win_shell  # noqa: E402
env = {"ProgramFiles": r"C:\Program Files", "LOCALAPPDATA": r"C:\Users\demo\AppData\Local"}
git_bash = r"C:\Program Files\Git\bin\bash.exe"
wsl = r"C:\Windows\system32\bash.exe"
which = lambda name, path=None: {"bash": wsl, "git": r"C:\Program Files\Git\cmd\git.exe"}.get(name)
exists = lambda p: p == git_bash
check("plain `bash` with Git Bash installed → Git Bash, not the WSL launcher",
      win_shell.resolve("bash", "PATH", which=which, env=env, isfile=exists) == git_bash)
check("…`bash.exe` too", win_shell.resolve("bash.exe", "PATH", which=which, env=env, isfile=exists) == git_bash)
check("Git Bash is found through git on PATH when it is not under Program Files",
      win_shell.resolve("bash", "PATH", which=which, env={}, isfile=lambda p: p.replace("/", "\\") == r"C:\Program Files\Git\bin\bash.exe") == git_bash)
check("no Git Bash: what PATH finds", win_shell.resolve("bash", "PATH", which=which, env=env, isfile=lambda p: False) == wsl)
check("a path the person typed is theirs", win_shell.resolve(r"C:\tools\bash.exe", "PATH", which=lambda n, path=None: n, env=env, isfile=exists) == r"C:\tools\bash.exe")
check("another program is what PATH finds", win_shell.resolve("claude", "PATH", which=lambda n, path=None: r"C:\c\claude.cmd", env=env, isfile=exists) == r"C:\c\claude.cmd")

check("Session._start_win starts the program win_shell.resolve names", "win_shell.resolve(args[0]" in inspect.getsource(main.Session._start_win))

print("6. a tab that ends right after it started")
main.Session._start = real_start
tab = object.__new__(main.Session)
tab.sid, tab.cmd, tab._spawn_ts = "s7", "bash", time.time() - 2
logged = []
real_dlog = main._dlog
main._dlog = lambda tag, msg: logged.append((tag, msg))
tab._note_exit(1)
main._dlog = real_dlog
check("it is logged with how long it ran, the command and the exit code",
      logged and logged[0][0] == "lifecycle" and "s7" in logged[0][1] and "bash" in logged[0][1] and "exit" in logged[0][1], logged)
logged.clear()
main._dlog = lambda tag, msg: logged.append((tag, msg))
tab._spawn_ts = time.time() - 3600
tab._note_exit(0)
main._dlog = real_dlog
check("a tab that ran for a long time is not reported", not logged, logged)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
