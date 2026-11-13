#!/usr/bin/env python3
"""The status monitor leaves quiet tabs alone: api_status's real loop over fake Claude tabs.

Reported: the laptop would not charge with the app idle. The monitor (every 0.6 s) worked every tab
out again at least every 15 s even when it had printed nothing: a `tmux capture-pane` fork (from a
big process with many threads, so mostly system time) and two 256 KB transcript reads each; and it
read config.json on every pass. Now:
  - a tab that printed nothing since its last verdict keeps it; an idle or done one is worked out
    again every 25 s (under the 30 s after which the Telegram heartbeat drops a reading), one that is
    working, asking or stuck still every 15 s (its wall-clock steps)
  - when that refresh comes and nothing was printed, the screen is the one read last time: no fork
  - a tab that prints is worked out on the next pass, as before
  - config.json is read at most every 5 s for the on/off switch
Run: .venv/bin/python tests_status_backoff.py
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
TMP = Path(tempfile.mkdtemp(prefix="sf-backoff-"))
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")       # fake tabs stay out of the real log

import agent_status  # noqa: E402
import api_status  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "agent_roster": {}}), encoding="utf-8")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


print("when a verdict stands (pure)")
reuse = Api._status_reusable
now = 1000.0
c = lambda state, age, out=5.0: {"out_ts": out, "computed_at": now - age, "result": {"state": state}}
check("printed since: worked out again", not reuse(c("idle", 1), 6.0, now))
check("idle, quiet for 20 s: still stands", reuse(c("idle", 20), 5.0, now))
check("…done too", reuse(c("done", 20), 5.0, now))
check("idle past 25 s: worked out again", not reuse(c("idle", 26), 5.0, now))
check("an unreadable verdict (unknown) is tried again within 15 s", not reuse(c("unknown", 16), 5.0, now))
import re  # noqa: E402
_hb = re.search(r"HEARTBEAT_STATUS_MAX_AGE_S\s*=\s*([\d.]+)", (HERE / "bridge_telegram.py").read_text(encoding="utf-8"))
check("a quiet tab's reading never gets older than the Telegram heartbeat accepts",
      _hb and Api.STATUS_SETTLED_REFRESH + 1.0 < float(_hb.group(1)), _hb and _hb.group(1))
for st in ("working", "decision", "stuck"):
    check(f"{st}, quiet for 16 s: worked out again (its wall-clock steps)", not reuse(c(st, 16), 5.0, now))
check("working, quiet for 10 s: stands", reuse(c("working", 10), 5.0, now))
check("nothing cached: worked out", not reuse(None, 5.0, now))

print("the loop")
forks, reads = [], []
_run = api_status.subprocess.run


def run(args, **kw):
    if args[:2] == ["tmux", "capture-pane"]:
        forks.append(args[-1] if args[-2] == "-t" else args[args.index("-t") + 1])
    return _run(args, **kw)


api_status.subprocess.run = run
_tail = agent_status._read_tail_events
agent_status._read_tail_events = lambda *a, **k: (reads.append(a[0]), _tail(*a, **k))[1]
cfg_reads = []
_lc = main.load_config
main.load_config = lambda *a, **k: (cfg_reads.append(1), _lc(*a, **k))[1]


class Tab:
    def __init__(self, sid):
        p = TMP / f"{sid}.jsonl"
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 600))
        p.write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "timestamp": ts, "message": {"role": "user", "content": "整理筆記"}},
            {"type": "assistant", "timestamp": ts, "message": {"model": "claude-opus-4-8", "role": "assistant",
                                                              "content": [{"type": "text", "text": "整理好了。"}]}}]) + "\n")
        self.cmd, self.cwd, self.alive = "claude", str(TMP), True
        self.lock = threading.Lock()
        self._recent = bytearray(b"> \r\n")
        self._last_output_activity_time = time.time() - 600
        self._tmux_name = f"sf_backoff_missing_{sid}"          # capture-pane forks and finds nothing
        self._hook_transcript_path = str(p)
        self.session_id = None
        self._config_dir_cache = ("claude", "")


api = Api.__new__(Api)
api.sessions = {f"q{i}": Tab(f"q{i}") for i in range(6)}
api.bridge = api.line_bridge = None
api._window = type("W", (), {"evaluate_js": lambda self, js: None})()
api._status_started = False
api._status_tracker = agent_status.StatusTracker()
api._hook_events, api._status_cache, api._blocked_cache = {}, {}, {}
api._connect_tick = lambda now: None
api._push_observe = lambda out: None
api._auto_accept_startup_trust_prompt = lambda sid, s: None
api_status._AUTO_STATUS[:] = [0.0, True]

api._start_status_monitor()
deadline = time.time() + 10
while time.time() < deadline and len(api._status_cache) < 6:
    time.sleep(0.1)
check("first pass: every tab worked out", len(api._status_cache) == 6, len(api._status_cache))
time.sleep(0.8)
f0, r0, k0 = len(forks), len(reads), len(cfg_reads)
time.sleep(3.0)
check("six quiet tabs, 3 s: no capture-pane fork", len(forks) == f0, forks[f0:])
check("…and no transcript read", len(reads) == r0, len(reads) - r0)
check("config.json read at most once (was every pass)", len(cfg_reads) - k0 <= 1, len(cfg_reads) - k0)

print("a refresh that comes due with nothing printed")
api._status_cache["q0"]["computed_at"] -= 120               # past STATUS_SETTLED_REFRESH
f0, r0 = len(forks), len(reads)
time.sleep(1.5)
check("the verdict is worked out again (the transcript is read)", len(reads) > r0, len(reads) - r0)
check("…on the screen read last time: no fork", "sf_backoff_missing_q0" not in forks[f0:], forks[f0:])

print("a tab that prints")
f0, r0 = len(forks), len(reads)
api.sessions["q1"]._last_output_activity_time = time.time()
time.sleep(1.5)
check("worked out on the next pass, from a fresh screen", "sf_backoff_missing_q1" in forks[f0:], forks[f0:])

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
