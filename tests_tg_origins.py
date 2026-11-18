#!/usr/bin/env python3
"""The registry of who a Telegram message came from (tg_origins.py).

  - a message is stored with its origin, a time and the first 40 characters: no token, no full text
  - lookups survive a torn or foreign file (an empty registry)
  - entries older than 14 days go, and the count is capped (the oldest go first)
  - several processes writing at once lose nothing (a lock on a sidecar file)
  - the bridge's recording is a silent no-op without a message id, and off the calling thread
  - the rules file: prefix match after stripping whitespace, nothing built in

Run: .venv/bin/python tests_tg_origins.py
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")

import sf_config  # noqa: E402
import tg_origins as to  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def reset():
    for n in (to.FILE_NAME, to.LOCK_NAME, to.RULES_NAME):
        try:
            (sf_config.config_dir() / n).unlink()
        except OSError:
            pass


def raw():
    return json.loads(to.path().read_text(encoding="utf-8"))


print("record and look up")
reset()
check("nothing yet: no origin", to.lookup(1100, 5) is None)
check("a tab's message is stored", to.record(1100, 5, to.tab_origin("s3", "ops"), "  Deploy   finished\nall green ") is True)
e = to.lookup(1100, 5)
check("…with its tab and bot, a time and a short head",
      e["kind"] == "tab" and e["sid"] == "s3" and e["bot"] == "ops" and e["head"] == "Deploy finished all green"
      and abs(e["at"] - time.time()) < 5, e)
check("the same message id in another chat is another message", to.lookup(2200, 5) is None)
to.record(1100, 6, to.schedule_origin("demo.report"), "x" * 400)
e = to.lookup(1100, 6)
check("a schedule's push is stored by its id; the head is cut at 40 characters",
      e["kind"] == "schedule" and e["id"] == "demo.report" and len(e["head"]) == 40, e)
body = to.path().read_text(encoding="utf-8")
check("the file holds nothing but kind, ids, time and head (no full text)", "x" * 41 not in body and set(raw()) == {"v", "items"})
check("a plain tab has no bot field", "bot" not in (to.record(1100, 7, to.tab_origin("s4"), "a") and to.lookup(1100, 7)))
check("an entry without a chat, a message id or a kind is refused",
      to.record_many([(0, 1, to.tab_origin("s1"), "a"), (1, 0, to.tab_origin("s1"), "a"), (1, 2, {}, "a"), (1, 3, None, "a")]) == 0)

print("a torn or foreign file")
to.path().write_text("{not json", encoding="utf-8")
check("torn: an empty registry, no exception", to.lookup(1100, 5) is None)
to.path().write_text(json.dumps([1, 2, 3]), encoding="utf-8")
check("a list: an empty registry", to.lookup(1100, 5) is None)
to.path().write_text(json.dumps({"v": 1, "items": {"1100:5": "nope", "1100:6": {"kind": "tab", "sid": "s1", "at": time.time()}}}), encoding="utf-8")
check("only well-formed entries count", to.lookup(1100, 5) is None and to.lookup(1100, 6)["sid"] == "s1")
check("a write over a torn file repairs it", to.record(1100, 8, to.tab_origin("s2"), "a") and to.lookup(1100, 8)["sid"] == "s2")

print("retention: 14 days, 5000 entries")
reset()
now = time.time()
to.record(1, 1, to.tab_origin("s1"), "old", now=now - 14 * 86400 - 60)
check("a message older than 14 days is not found even before it is pruned", to.lookup(1, 1) is None)
to.record(1, 2, to.tab_origin("s1"), "fresh", now=now - 13 * 86400)
check("…one from 13 days ago is", to.lookup(1, 2) is not None)
to.record(1, 3, to.tab_origin("s1"), "new")
check("the next write drops what expired", "1:1" not in raw()["items"] and "1:2" in raw()["items"])
reset()
rows = [(7, i, to.tab_origin("s1"), "m") for i in range(to.MAX_ENTRIES + 50)]
for k in range(0, len(rows), 1000):
    to.record_many(rows[k:k + 1000], now=now + k)         # later batches are newer
items = raw()["items"]
check(f"never more than {to.MAX_ENTRIES} entries", len(items) == to.MAX_ENTRIES, len(items))
check("…the oldest went first", "7:0" not in items and f"7:{to.MAX_ENTRIES + 49}" in items)

print("several processes at once")
reset()
CHILD = (
    "import sys, os\n"
    f"sys.path.insert(0, {str(HERE)!r})\n"
    "import tg_origins as to\n"
    "n = int(sys.argv[2])\n"
    "for i in range(n):\n"
    "    assert to.record(int(sys.argv[1]), i + 1, to.schedule_origin('demo.p' + sys.argv[1]), 'm')\n"
)
env = dict(os.environ, SHELLFRAME_CONFIG_DIR=str(sf_config.config_dir()))
procs = [subprocess.Popen([sys.executable, "-I", "-c", CHILD, str(100 + p), "40"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) for p in range(6)]
ths = [threading.Thread(target=lambda c=c: [to.record(c, i + 1, to.tab_origin("s9"), "t") for i in range(40)]) for c in (900, 901)]
for t in ths:
    t.start()
codes = [p.wait(60) for p in procs]
for t in ths:
    t.join()
items = raw()["items"]
check("every child ended well", codes == [0] * 6, [p.stderr.read()[-300:] for p in procs if p.returncode])
check("six processes and two threads wrote 320 entries, none lost", len(items) == 6 * 40 + 2 * 40, len(items))
check("…and the file is whole JSON", isinstance(raw()["items"], dict))
check("no temp file is left behind", not [p for p in sf_config.config_dir().iterdir() if p.name.endswith(".tmp")])

print("recording from the bridge")
reset()
to.note({"ok": True}, 1100, "no id", to.tab_origin("s1"))
to.note(None, 1100, "no answer", to.tab_origin("s1"))
to.note({"ok": True, "result": {"message_id": True}}, 1100, "a bool is no id", to.tab_origin("s1"))
to.note({"ok": True, "result": {"message_id": 41}}, 0, "no chat", to.tab_origin("s1"))
to.flush()
check("an answer without a message id (a stub, a failure) records nothing and raises nothing", not to.path().exists())
t0 = time.time()
to.note({"ok": True, "result": {"message_id": 42}}, 1100, "from a tab", lambda: (time.sleep(0.3), to.tab_origin("s5", "ops"))[1])
check("recording does not wait for the lookup or the disk", time.time() - t0 < 0.2)
to.flush()
check("…and lands after it", (to.lookup(1100, 42) or {}).get("bot") == "ops")
to.note({"ok": True, "result": {"message_id": 43}}, 1100, "x", lambda: 1 / 0)
to.note({"ok": True, "result": {"message_id": 44}}, 1100, "after a failure", to.tab_origin("s6"))
to.flush()
check("an origin that fails to resolve is skipped; the next one is still recorded", to.lookup(1100, 43) is None and to.lookup(1100, 44) is not None)

print("the rules file")
reset()
check("no file: no rules and no match", to.read_rules() == [] and to.match_rule("anything") is None)
rules = sf_config.config_dir() / to.RULES_NAME
rules.write_text(json.dumps([
    {"prefix": "Nightly report", "schedule": "demo.report"},
    {"prefix": " Backup ", "bot": "ops"},
    {"prefix": "", "schedule": "demo.all"},
    {"prefix": "x"},
    "junk",
    {"prefix": "Both", "schedule": "demo.both", "bot": "ops"},
]), encoding="utf-8")
check("an empty prefix, a rule without a target and junk are skipped", [r["prefix"] for r in to.read_rules()] == ["Nightly report", "Backup", "Both"])
check("a schedule rule: the text starts with the prefix", to.match_rule("Nightly report failed: 401") == to.schedule_origin("demo.report"))
check("…after stripping whitespace on both sides", to.match_rule("\n  Nightly report failed ") == to.schedule_origin("demo.report"))
check("a bot rule", to.match_rule("Backup ok") == {"kind": "bot", "bot": "ops"})
check("a schedule wins when a rule names both", to.match_rule("Both") == to.schedule_origin("demo.both"))
check("the prefix is the start, not anywhere", to.match_rule("The Nightly report") is None)
check("no text, no match", to.match_rule("") is None and to.match_rule(None) is None)
rules.write_text("not json", encoding="utf-8")
check("a torn rules file is no rules", to.read_rules() == [])
src = (HERE / "tg_origins.py").read_text(encoding="utf-8") + (HERE / "tg_reply_route.py").read_text(encoding="utf-8")
check("no prefix or schedule is built into the code: no file, no rule (above), and no real label in the source", "Scrum" not in src and "晚上收尾" not in src)

print("the Telegram size cap")
import bridge_telegram as bt  # noqa: E402
text = "\n".join(f"line {i} " + "y" * 60 for i in range(300))
parts = to.split_for_telegram(text)
check("a long text is split at line boundaries, nothing lost", len(parts) > 1 and all(len(p) <= 3900 for p in parts) and "\n".join(parts) == text)
check("the bridge splits the same way", bt.split_for_telegram(text) == parts and to.split_for_telegram("short") == ["short"])

reset()
print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
