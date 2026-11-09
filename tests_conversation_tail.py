#!/usr/bin/env python3
"""The phone's chat view keeps the message just sent, however big the records after it.

Measured on a 28 MB transcript: 195 KB of its last 256 KB were attachment records (one
of 128 KB, written five seconds after the user's message). `/link/conversation` read a
fixed 256 KB tail, so a second after sending, the phone got 1 turn, then 0, then turns
without the message. Replayed at each of those moments, the old read gave 7, 1, 0, 2, 5
turns; the new one gives the 80 asked for, the message among them.

  - a big record written after a message does not push it out of the view
  - it reads back until there are `limit` turns, or a cap
  - an unchanged transcript is not read again; a grown one starts from what sufficed

Run: .venv/bin/python tests_conversation_tail.py
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-convo-tail-"))
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")

import agent_status  # noqa: E402
import api_remote  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def rec(kind, text="", size=0, n=0):
    base = {"parentUuid": None, "isSidechain": False, "sessionId": "test", "uuid": f"u{n}",
            "timestamp": f"2026-01-01T00:{n // 60 % 60:02d}:{n % 60:02d}.000Z"}
    if kind == "user":
        return dict(base, type="user", message={"role": "user", "content": text})
    if kind == "assistant":
        return dict(base, type="assistant", message={"role": "assistant", "content": [{"type": "text", "text": text}]})
    # an attachment carries its payload ahead of its own "type" field, as the real ones do
    return {"parentUuid": None, "isSidechain": False, "attachment": {"type": "environment", "snapshot": "x" * size},
            "type": "attachment", "sessionId": "test", "uuid": f"a{n}"}


def write(path, records):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


# A long conversation, then the message just sent, then big attachment records.
history = []
for i in range(1500):
    history += [rec("user", f"question {i}", n=2 * i), rec("assistant", f"answer {i} " + "y" * 900, n=2 * i + 1)]
sent = rec("user", "please book tomorrow's check-ins", n=3000)
path = TMP / "session.jsonl"
write(path, history + [sent] + [rec("attachment", size=128 * 1024, n=3001), rec("attachment", size=140 * 1024, n=3002)])

print("the message just sent")
fmt, evs, _ = agent_status._read_tail_events(str(path))
old = [e for e in evs if e.get("kind") in api_remote.CONVO_KINDS]
check("the old fixed 256 KB tail had lost it behind the attachments (the bug, reproduced)",
      not any("check-ins" in (e.get("text") or "") for e in old), len(old))
fmt, turns, err = api_remote.conversation_turns(str(path), 80)
check("now it is there, the last turn", not err and turns and turns[-1].get("text") == "please book tomorrow's check-ins",
      (err, turns[-1:] if turns else None))
check("…with the turns before it, 80 in all", len(turns) == 80 and turns[-2].get("text", "").startswith("answer 1499"),
      len(turns))
check("…read as Claude's format", fmt == "claude", fmt)

print("how far back")
window = api_remote._convo_cache[str(path)][1]
check("it read back only until there were 80 turns, not the whole file", 256 * 1024 < window < path.stat().st_size,
      (window, path.stat().st_size))
api_remote._convo_cache.clear()
fmt, few, _ = api_remote.conversation_turns(str(path), 80, cap=300 * 1024)
check("…and stops at the cap, giving what it has", 0 < len(few) < 80 and few[-1].get("text", "").endswith("check-ins"),
      len(few))

print("asking again")
api_remote._convo_cache.clear()
api_remote.conversation_turns(str(path), 80)
reads = []
real = agent_status._read_tail_events
agent_status._read_tail_events = lambda *a, **k: reads.append(k.get("tail_bytes")) or real(*a, **k)
fmt, again, _ = api_remote.conversation_turns(str(path), 80)
check("an unchanged transcript is not read again", reads == [] and again == turns, reads)
with path.open("a", encoding="utf-8") as f:
    f.write(json.dumps(rec("assistant", "booked", n=3003)) + "\n")
fmt, grown, _ = api_remote.conversation_turns(str(path), 80)
check("a grown one is read once, from the window that sufficed plus what was written",
      len(reads) == 1 and reads[0] >= window and grown[-1].get("text") == "booked", reads)
agent_status._read_tail_events = real

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
