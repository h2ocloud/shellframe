#!/usr/bin/env python3
"""Telegram replies that were silently lost when two turns overlapped.

Race 1 — a slow send wipes the next turn. The flush loop extracts a reply,
releases the lock and sends it to Telegram, which can take tens of seconds.
A message that arrives meanwhile arms a new turn (has_user_msg, new reply
markers). When the send finishes, the commit for the *old* turn used to reset
has_user_msg / markers (fallback path) or set marker_forwarded (marker path)
on the new turn; the new reply was then drained as stale output, or its
missing-marker fallback was switched off.

Race 2 — a follow-up hides the previous reply. Every message got its own reply
markers, but the bridge only watched the newest pair and every new message
cleared the raw buffer. A follow-up sent while the AI was still answering (or
while the first message was still queued) made the first reply unrecognisable.

The flush loop runs for real, one tick at a time; the interleavings are forced
deterministically (the new message arrives inside the fake sendMessage call).
Run: .venv/bin/python tests_tg_turn_races.py
"""
import importlib.util
import os
import sys
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def markers(tag):
    tok = f"TG_REPLY_{tag}"
    return f"[[{tok}]]", f"[[/{tok}]]"


def bridge(responder):
    """One real _flush_loop tick per call of tick(); surroundings are no-ops."""
    br = object.__new__(_bt.TelegramBridge)
    br.active = True
    br._stop_event = threading.Event()
    br._flush_wake = threading.Event()
    br._perf, br._perf_enabled, br._perf_window_start, br._perf_ticks = {}, False, 0.0, 0
    br.slots, br._slot_order, br._slots_lock = {}, [], threading.Lock()
    br._user_active, br._user_chat = {}, {}
    br._rate_limit_seen, br._last_prune_ts, br.paused = {}, 0.0, False
    br.config = types.SimpleNamespace(bot_token="x")
    br._perf_maybe_emit = lambda: setattr(br, "active", False)
    br._prune_stale_slots = lambda *a, **k: None
    br._detect_rate_limit = lambda slot: None
    br._maybe_auto_compact = lambda slot: None
    br._send_typing = lambda sid: None
    br._maybe_notify_completion = lambda slot: None
    br._detect_and_apply_board = lambda slot, lines: lines
    br._detect_and_fire_signal = lambda slot, lines: lines
    br._extract_file_paths = lambda text: []
    br._live_tail = lambda slot, rows=6: ""      # the CLI's turn has ended
    br._send_tg_file = lambda chat_id, fp: None
    br.sent = []

    def fake_tg_api(token, method, data=None, timeout=35):
        if method == "sendMessage":
            br.sent.append((data or {}).get("text", ""))
        return responder(br, method, data)
    _bt.tg_api = fake_tg_api
    return br


def tick(br):
    br.active = True
    br._flush_loop()


def make_slot(br, sid="s1"):
    slot = _bt.SessionSlot(sid, "worker", lambda t: None, 11)
    br.slots[sid] = slot
    br._slot_order = [sid]
    br._user_chat, br._user_active = {42: 999}, {42: sid}
    return slot


def new_message(br, slot, start, end, text="next question"):
    """What the receive path does when a user message arrives."""
    if hasattr(br, "_begin_turn"):
        return br._begin_turn(slot, {"start": start, "end": end, "prompt": ""}, [text])
    # 0.38.0 receive path, field by field
    with slot.output_lock:
        slot.output_buf = ""
        slot.pending_raw = ""
        slot.first_output_time = 0
        slot.last_output_time = 0
    slot.msg_sent_ts = time.time()
    slot.has_user_msg = True
    slot.awaiting_response = True
    slot.sent_texts.append(text)
    slot.expect_marker = True
    slot.reply_start_marker, slot.reply_end_marker = start, end
    slot.marker_prompt = ""
    slot.marker_next_scan_ts, slot.marker_scan_gen, slot._fb_next_ts = 0.0, -1, 0.0
    slot.marker_forwarded = False


def output(slot, text, idle=5.0):
    """New PTY output that has gone quiet for `idle` seconds."""
    now = time.time()
    with slot.output_lock:
        slot.pending_raw += text
        slot._feed_gen = getattr(slot, "_feed_gen", 0) + 1
        slot.last_output_time = now - idle
        if not slot.first_output_time:
            slot.first_output_time = now - idle - 5.0


_OK = lambda br, m, d: {"ok": True, "result": {}}


def test_fallback_send_does_not_wipe_the_next_turn():
    k0s, k0e = markers("old00000")
    k1s, k1e = markers("new11111")
    fired = []

    def responder(br, method, data):
        if method == "sendMessage" and not fired:
            fired.append(1)
            new_message(br, br.slots["s1"], k1s, k1e)   # arrives while the old reply is in flight
        return {"ok": True, "result": {}}
    br = bridge(responder)
    slot = make_slot(br)
    new_message(br, slot, k0s, k0e, "first question")
    slot.msg_sent_ts = time.time() - 60            # marker never came; fallback is due
    br._marker_fallback_text = lambda s: "answer without markers"
    output(slot, "answer without markers\n")
    tick(br)
    check("the marker-less reply is forwarded by the fallback", "answer without markers" in br.sent, br.sent)
    check("the turn that arrived during the send is still armed",
          slot.has_user_msg and slot.expect_marker and slot.reply_start_marker == k1s,
          f"has_user_msg={slot.has_user_msg} start={slot.reply_start_marker!r}")
    output(slot, f"{k1s}\nsecond answer\n{k1e}\n")
    tick(br)
    check("and its reply reaches Telegram", any("second answer" in t for t in br.sent), br.sent)


def test_marker_send_does_not_switch_off_the_next_turns_fallback():
    k0s, k0e = markers("old00000")
    k1s, k1e = markers("new11111")
    fired = []

    def responder(br, method, data):
        if method == "sendMessage" and not fired:
            fired.append(1)
            new_message(br, br.slots["s1"], k1s, k1e)
        return {"ok": True, "result": {}}
    br = bridge(responder)
    slot = make_slot(br)
    new_message(br, slot, k0s, k0e, "first question")
    output(slot, f"{k0s}\nfirst answer\n{k0e}\n")
    tick(br)
    check("the first reply is forwarded", any("first answer" in t for t in br.sent), br.sent)
    check("the new turn may still fall back if its marker never shows up",
          slot.marker_forwarded is False, f"marker_forwarded={slot.marker_forwarded}")


def test_follow_up_while_the_reply_is_streaming():
    k1s, k1e = markers("aaaa1111")
    k2s, k2e = markers("bbbb2222")
    br = bridge(_OK)
    slot = make_slot(br)
    new_message(br, slot, k1s, k1e, "first question")
    output(slot, f"{k1s}\nthe first answer, part one\n", idle=0.2)   # still streaming
    new_message(br, slot, k2s, k2e, "one more thing")                  # follow-up sent now
    output(slot, f"part two\n{k1e}\n")                                 # first reply finishes
    tick(br)
    check("the reply to the first message is forwarded",
          any("the first answer, part one" in t and "part two" in t for t in br.sent), br.sent)
    output(slot, f"{k2s}\nanswer to the follow-up\n{k2e}\n")
    tick(br)
    check("then the reply to the follow-up", any("answer to the follow-up" in t for t in br.sent), br.sent)
    check("each reply is sent exactly once",
          sum("the first answer" in t for t in br.sent) == 1, br.sent)


def test_second_message_queued_before_the_first_was_typed():
    k1s, k1e = markers("cccc1111")
    k2s, k2e = markers("dddd2222")
    br = bridge(_OK)
    slot = make_slot(br)
    new_message(br, slot, k1s, k1e, "first")
    new_message(br, slot, k2s, k2e, "second")      # first still waiting behind the busy guard
    output(slot, f"{k1s}\nanswer one\n{k1e}\n")     # first is typed and answered
    tick(br)
    check("the first queued message still gets its reply", any("answer one" in t for t in br.sent), br.sent)


def test_answered_turn_still_resets_the_buffer():
    """v0.29.22 guard: once a reply was delivered, the next message starts clean."""
    k1s, k1e = markers("eeee1111")
    k2s, k2e = markers("ffff2222")
    br = bridge(_OK)
    slot = make_slot(br)
    new_message(br, slot, k1s, k1e, "first")
    output(slot, f"{k1s}\nanswer one\n{k1e}\n")
    tick(br)
    new_message(br, slot, k2s, k2e, "second")
    check("the buffer is cleared when nothing is in flight", slot.pending_raw == "", repr(slot.pending_raw[:40]))
    n = len(br.sent)
    output(slot, "unrelated redraw\n")
    tick(br)
    check("the old reply is not sent again", len(br.sent) == n, br.sent[n:])


def test_unanswered_marker_expires():
    k1s, k1e = markers("gggg1111")
    k2s, k2e = markers("hhhh2222")
    br = bridge(_OK)
    if not hasattr(br, "_INFLIGHT_MARKER_TTL"):
        check("unanswered markers expire (feature present)", False, "no _INFLIGHT_MARKER_TTL")
        return
    slot = make_slot(br)
    new_message(br, slot, k1s, k1e, "first")
    slot.reply_markers[0]["ts"] -= br._INFLIGHT_MARKER_TTL + 1     # the model never used it
    output(slot, "something\n", idle=0.2)
    new_message(br, slot, k2s, k2e, "second")
    check("an expired unanswered marker does not keep the buffer alive",
          slot.pending_raw == "" and [m["start"] for m in slot.reply_markers] == [k2s],
          f"markers={[m['start'] for m in slot.reply_markers]}")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("=== ALL PASS ===")
