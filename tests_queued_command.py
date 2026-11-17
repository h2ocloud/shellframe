#!/usr/bin/env python3
"""A message taken in mid-turn is the person's turn in the conversation (agent_status._norm_claude).

Reported: a message sent from the chat view while the tab was working stayed "sending" for good, below the reply
that answered it. It had gone in: Claude Code took it into the running turn (2.1.3xx) and wrote it not as a `user`
record but as an `attachment` of type `queued_command` (origin.kind "human"), with a `queue-operation` record
(remove, absorbed_mid_turn) beside it. The chat view only knew `user` records, so it never met its bubble.
  - a queued_command from a person is a user turn, with its own words and time
  - a background task's notification (origin.kind "task-notification") is not someone talking
  - the queue-operation records and other attachments stay out
  - a message that also has a normal `user` record is not doubled (measured on a real transcript: none had both)
Demo data only. Run: .venv/bin/python tests_queued_command.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_status as A  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def queued(prompt, kind="human", ts="2026-11-17T05:43:21.247Z"):
    return {"type": "attachment", "timestamp": ts,
            "attachment": {"type": "queued_command", "prompt": prompt, "source_uuid": "u1", "delivery_id": "d1",
                           "origin": {"kind": kind}, "humanTurn": kind == "human", "commandMode": "prompt"}}


ev = A._norm_claude(queued("示範：中途送進去的"))
check("a person's message taken in mid-turn is a user turn, with its words and its time",
      ev == {"kind": "user_msg", "ts": A._parse_iso("2026-11-17T05:43:21.247Z"), "text": "示範：中途送進去的"}, ev)
check("a background task's notification is not someone talking", A._norm_claude(queued("<task-notification>x</task-notification>", "task-notification")) is None)
check("an empty prompt is nothing", A._norm_claude(queued("   ")) is None)
check("the queue's own bookkeeping stays out", A._norm_claude({"type": "queue-operation", "operation": "remove",
                                                               "reason": "absorbed_mid_turn", "timestamp": "2026-11-17T05:43:27Z"}) is None)
check("another kind of attachment stays out", A._norm_claude({"type": "attachment", "attachment": {"type": "file", "path": "/tmp/x"}}) is None)
check("a normal user record is as before", A._norm_claude({"type": "user", "timestamp": "2026-11-17T05:40:00Z",
                                                           "message": {"role": "user", "content": "示範：一般的訊息"}})["text"] == "示範：一般的訊息")

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
