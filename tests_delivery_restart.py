#!/usr/bin/env python3
"""A queued message survives a ShellBot restart: it is delivered afterwards, or
its sender is told why not. Never silently gone.

Measured: a message queued at 13:21:11 (position 1, the target tab mid-turn)
vanished with the 13:25:23 restart. The queue lived only in memory, so it was
neither delivered nor reported to the sender.

  - the queue is on disk (owner-only) while anything waits
  - after a restart each item carries on from its original start time
  - a tab that does not come back, a wait that ran out, or an item that was
    being typed in when the app went down is reported to the sender
  - a sender whose own tab is not back yet still gets the note once it is
  - the queue file is only used by the running app, never by tests or imports

The first case kills a real process with the item waiting (os._exit) and
resumes in another one, the way an app restart does.

Run: .venv/bin/python tests_delivery_restart.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")  # fake tabs stay out of the real log

import tab_delivery as td  # noqa: E402

td.POLL, td.TAKEN_WAIT = 0.05, 0.3
td.RESUME_SETTLE = 0.0       # the settle wait has its own check below


class Tab:
    def __init__(self, sid):
        self.sid = sid
        self._tmux_name = None
        self._startup_trust_pending = True


class FakeApi:
    """What tab_delivery reads from the app: tabs, their state, a way to type."""
    def __init__(self, *sids, busy=()):
        self.sessions = {sid: Tab(sid) for sid in sids}
        self.busy = set(busy)
        self.sent = []
        self.prompt_at = {}

    def _agent_state_for_list(self, sid):
        return "working" if sid in self.busy else ""

    def _bridge_session_busy(self, sid):
        return False

    def _send_text_to_session(self, s, text, submit=False):
        self.sent.append((s.sid, text))
        self.prompt_at[s.sid] = time.time()

    def _agent_status_snapshot(self, sid):
        return ({"prompt_at": self.prompt_at.get(sid, 0)}, 0)


if len(sys.argv) > 2 and sys.argv[1] == "--before-restart":
    td.STATE_FILE = Path(sys.argv[2])
    app = FakeApi("s1", "s2", busy=("s1",))
    r = td.deliver(app, "s1", "please review the plan", from_sid="s2", label="coder")
    print("QUEUED", r.get("position"), flush=True)
    os._exit(0)          # the app goes down while the item is still waiting


TMP = Path(tempfile.mkdtemp(prefix="sf-queue-restart-"))
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def on_disk(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def restart_with(rows, name):
    """A fresh start: earlier queues drained, the file holds `rows`."""
    wait_for(lambda: not td.pending() and not td._draining)
    td._resumed_at, td._saved = 0.0, None
    td.STATE_FILE = TMP / f"{name}.json"
    td.STATE_FILE.write_text(json.dumps(rows), encoding="utf-8")
    return td.STATE_FILE


def notes_to(api, sid):
    return [t for s, t in api.sent if s == sid and "[[ShellBot" in t]


print("off unless the app turns it on")
check("importing the module keeps the queue in memory only", td.STATE_FILE is None)
src = (HERE / "main.py").read_text(encoding="utf-8")
entry = src.split("\ndef main():", 1)[1]
check("the app's entry point turns it on (tests never run it)",
      'tab_delivery.STATE_FILE = CONFIG_DIR / "delivery_queue.json"' in entry)
restore = src.split("    def restore_tmux_sessions(", 1)[1].split("\n    def ", 1)[0]
check("tab restore picks the queue up on both of its exits",
      restore.count("tab_delivery.resume(self)") == 2 and restore.count("return json.dumps(restored)") == 2)

print("the incident: queued, the app goes down, it comes back")
state = TMP / "queue.json"
out = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--before-restart", str(state)],
                     capture_output=True, text=True, timeout=30)
rows = on_disk(state) or []
check("before the restart the item was queued and written down",
      "QUEUED 1" in out.stdout and len(rows) == 1 and rows[0]["text"] == "please review the plan",
      (out.stdout, out.stderr[-300:], rows))
if sys.platform == "win32":     # no POSIX permission bits: chmod(0o600) reads back as 0o666
    check("the file is written (Windows: no owner-only permission bits)", state.exists())
else:
    check("the file is readable by its owner only", state.exists() and (state.stat().st_mode & 0o777) == 0o600,
          oct(state.stat().st_mode & 0o777) if state.exists() else "missing")
td.STATE_FILE = state
api = FakeApi("s1", "s2")                       # after the restart the tab is free
check("resume loads it", td.resume(api) == 1)
check("…and it is delivered", wait_for(lambda: ("s1", "please review the plan") in api.sent), api.sent)
check("…and the file is emptied", wait_for(lambda: on_disk(state) == []), on_disk(state))
check("the sender is not bothered", not notes_to(api, "s2"))
check("a second resume (a reattach retry) loads nothing again", td.resume(api) == 0)

print("the wait goes on from the original start")
td.QUEUE_MAX_WAIT = 100.0
restart_with([{"sid": "s1", "text": "old news", "submit": True, "t0": time.time() - 99.0,
               "from_sid": "s2", "label": "coder"}], "carry")
api = FakeApi("s1", "s2", busy=("s1",))
td.resume(api)
check("still busy: it times out after what was left (~1 s), not a fresh 100 s",
      wait_for(lambda: notes_to(api, "s2"), timeout=4.0), api.sent)
check("…the note says why, and that ShellBot restarted",
      any("in the middle of a turn" in t and "restarted" in t for t in notes_to(api, "s2")), notes_to(api, "s2"))
check("…and the text never went in", not any(s == "s1" for s, _ in api.sent))

restart_with([{"sid": "s1", "text": "too late", "submit": True, "t0": time.time() - 500.0,
               "from_sid": "s2", "label": "coder"}], "expired")
api = FakeApi("s1", "s2")
td.resume(api)
check("its time ran out during the restart: not delivered late, the sender is told",
      wait_for(lambda: notes_to(api, "s2")) and not any(s == "s1" for s, _ in api.sent)
      and "waiting time ran out" in notes_to(api, "s2")[0], api.sent)
td.QUEUE_MAX_WAIT = 1800.0

print("tabs that come back late, or not at all")
td.RESTORE_GRACE = 2.0
restart_with([{"sid": "s1", "text": "hello again", "submit": True, "t0": time.time(),
               "from_sid": "s2", "label": "coder"}], "late")
api = FakeApi("s2")
td.resume(api)
time.sleep(0.3)
api.sessions["s1"] = Tab("s1")                  # reattached a moment later
check("a tab that comes back within the grace gets it",
      wait_for(lambda: ("s1", "hello again") in api.sent), api.sent)

td.RESTORE_GRACE = 0.4
restart_with([{"sid": "s9", "text": "anyone there", "submit": True, "t0": time.time(),
               "from_sid": "s2", "label": "reviewer"}], "gone")
api = FakeApi("s2")
td.resume(api)
check("a tab that never comes back: the sender is told it did not come back",
      wait_for(lambda: any("did not come back" in t and "reviewer" in t for t in notes_to(api, "s2"))),
      api.sent)

td.RESTORE_GRACE = 2.0
td.QUEUE_MAX_WAIT = 100.0
restart_with([{"sid": "s1", "text": "for later", "submit": True, "t0": time.time() - 500.0,
               "from_sid": "s2", "label": "coder"}], "sender-late")
api = FakeApi("s1")                             # the sender's own tab is not back yet
td.resume(api)
time.sleep(0.3)
api.sessions["s2"] = Tab("s2")
check("a sender whose tab comes back a moment later still gets the note",
      wait_for(lambda: notes_to(api, "s2")), api.sent)
td.QUEUE_MAX_WAIT = 1800.0

print("typed in when the app went down")
restart_with([{"sid": "s1", "text": "maybe sent", "submit": True, "t0": time.time() - 5,
               "from_sid": "s2", "label": "coder", "sending": time.time() - 1}], "sending")
api = FakeApi("s1", "s2")
td.resume(api)
check("not typed in a second time", wait_for(lambda: notes_to(api, "s2"))
      and ("s1", "maybe sent") not in api.sent, api.sent)
check("…the sender is told it may not have arrived and to look first",
      any("may not have been delivered" in t and "peek" in t for t in notes_to(api, "s2")), notes_to(api, "s2"))

print("states settle before a restored item goes in")
# Measured: 0.6 s after a resume a restored item went into a tab mid-turn: no tab had a
# state yet, so every tab read "not busy".
td.RESUME_SETTLE = 0.6
restart_with([{"sid": "s1", "text": "after settling", "submit": True, "t0": time.time(),
               "from_sid": "s2", "label": "coder"}], "settle")
api = FakeApi("s1", "s2")
td.resume(api)
time.sleep(0.3)
check("not in the first moments after the resume, even if the tab looks free",
      ("s1", "after settling") not in api.sent, api.sent)
check("…but once the states have settled", wait_for(lambda: ("s1", "after settling") in api.sent, 3), api.sent)
td.RESUME_SETTLE = 0.0

print("order and the file")
restart_with([], "order")
api = FakeApi("s1", "s2", busy=("s1",))
td.resume(api)
td.deliver(api, "s1", "first", from_sid="s2")
td.deliver(api, "s1", "second", from_sid="s2")
check("two waiting items are on disk in order",
      [r["text"] for r in on_disk(td.STATE_FILE) or []] == ["first", "second"], on_disk(td.STATE_FILE))
api.busy.clear()
check("…delivered in order", wait_for(lambda: [t for s, t in api.sent if s == "s1"] == ["first", "second"]),
      api.sent)
check("…and the file is empty again", wait_for(lambda: on_disk(td.STATE_FILE) == []))

print("a message queued after the restart, before the resume")
# Measured: resume() runs once every tab has reattached (17:31:20, 15 s after the restart), while
# the queue takes messages from the first seconds. One queued at 17:31:15 was written to the file,
# then resumed as if the previous run had left it: it went in twice. Writing it had also replaced
# whatever the previous run left in the file.
restart_with([{"sid": "s1", "text": "from before the restart", "submit": True, "t0": time.time() - 30,
               "from_sid": "s2", "label": "coder"}], "early")
api = FakeApi("s1", "s2", busy=("s1",))
td.deliver(api, "s1", "queued during the restart", from_sid="s2")
check("the previous run's item stays on disk next to the new one",
      sorted(r["text"] for r in on_disk(td.STATE_FILE) or []) == ["from before the restart", "queued during the restart"],
      on_disk(td.STATE_FILE))
check("resume takes only the previous run's item", td.resume(api) == 1)
check("…so each waits once", sorted(p["chars"] for p in td.pending("s1"))
      == sorted([len("from before the restart"), len("queued during the restart")]), td.pending("s1"))
api.busy.clear()
s1_sent = lambda: sorted(t for s, t in api.sent if s == "s1")
check("…and each goes in exactly once", wait_for(lambda: len(s1_sent()) >= 2) and (time.sleep(0.5) or True)
      and s1_sent() == ["from before the restart", "queued during the restart"], api.sent)
check("…and the file is empty again", wait_for(lambda: on_disk(td.STATE_FILE) == []), on_disk(td.STATE_FILE))

restart_with([], "garbage")
td.STATE_FILE.write_text("{not json", encoding="utf-8")
check("an unreadable file is skipped, not a crash", td.resume(FakeApi("s1")) == 0)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
