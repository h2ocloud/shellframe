#!/usr/bin/env python3
"""One Telegram reply, re-sent dozens of times by full-screen redraws.

A tab answered one Telegram message with one [[TG_REPLY]] block (the transcript
holds exactly one). The user then kept working in that tab from the desktop.
The tab runs Claude Code in full-screen mode (alternate screen), which redraws
the visible conversation on every change, and only the cells that changed.
Each redraw re-emitted the old reply's markers with whatever had been
repainted in between: the reply with letters missing, tool progress, half the
screen. Every span differed, so exact-match dedup never matched, and the
bridge forwarded each as a "follow-up": 29 messages in the log window alone.

Three guards, each tested on the real flush loop:
  1. the turn is over once the tab took a newer prompt that is not Telegram's;
  2. a redraw of text already forwarded this turn is not a new reply;
  3. whatever the cause, one reply plus at most N follow-ups per turn.
And the cases that must keep working: a real follow-up (a background task
finishing) is still forwarded, and an unknown or stale status changes nothing.

Run: .venv/bin/python tests_tg_repaint_spam.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_spec = importlib.util.spec_from_file_location("bt", os.path.join(_HERE, "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None

import agent_status as ag      # noqa: E402
import bridge_delivery as bd   # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


TOKEN = "TG_REPLY_5e1f0a9c"
START, END = f"[[{TOKEN}]]", f"[[/{TOKEN}]]"
REPLY = ("今天的會議都排好了，時間到會提醒你。\n"
         "- 上午：部門週會 10:00\n"
         "- 下午：產品評審 14:30\n"
         "- 排之前查過行事曆，沒有衝突。\n"
         "skill／memory 建議：none")


def bridge(status=None):
    """One real _flush_loop tick per tick(); surroundings are no-ops."""
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
    br._live_tail = lambda slot, rows=6: ""
    br._send_tg_file = lambda chat_id, fp: None
    br._on_agent_status = status
    br.sent = []

    def fake_tg_api(token, method, data=None, timeout=35):
        if method == "sendMessage":
            br.sent.append((data or {}).get("text", ""))
        return {"ok": True, "result": {}}
    _bt.tg_api = fake_tg_api
    slot = _bt.SessionSlot("s1", "worker", lambda t: None, 11)
    br.slots["s1"] = slot
    br._slot_order = ["s1"]
    br._user_chat, br._user_active = {42: 999}, {42: "s1"}
    br._begin_turn(slot, {"start": START, "end": END, "prompt": ""}, ["question"])
    return br, slot


def tick(br):
    br.active = True
    br._flush_loop()


def output(slot, text, idle=5.0):
    """New PTY output, quiet for `idle` s, and the marker rescan delay elapsed."""
    now = time.time()
    with slot.output_lock:
        slot.pending_raw += text
        slot._feed_gen = getattr(slot, "_feed_gen", 0) + 1
        slot.last_output_time = now - idle
        if not slot.first_output_time:
            slot.first_output_time = now - idle - 5.0
        slot.marker_next_scan_ts = 0.0


def esc(row, col=1):
    return f"\x1b[{row};{col}H"


def redraw(top, drop=(), extra=""):
    """What a full-screen redraw puts in the PTY stream for the reply block at
    screen row `top`: cursor moves, only the changed cells (`drop` = character
    indexes skipped because those cells did not change), maybe other rows."""
    out = [esc(top) + START]
    for i, line in enumerate(REPLY.split("\n")):
        kept = "".join(ch for j, ch in enumerate(line) if j not in drop)
        out.append(esc(top + 1 + i) + kept)
    if extra:
        out.append(esc(top + 2, 40) + extra)
    out.append(esc(top + 7) + END)
    return "".join(out)


def first_reply(br, slot):
    output(slot, f"{START}\n{REPLY}\n{END}\n")
    tick(br)
    check("the reply itself is forwarded once", len(br.sent) == 1 and "部門週會" in br.sent[0], br.sent)
    time.sleep(0.02)   # a status computed from here on is "after the forward"


# ── 1. the user moves on in the tab itself ──────────────────────────────────

def test_newer_local_prompt_ends_the_turn():
    print("newer local prompt ends the Telegram turn")
    br, slot = bridge(status=lambda sid: ({"prompt_marker": ""}, 0.0))
    first_reply(br, slot)
    output(slot, redraw(3, drop={2, 3, 9}, extra="Reading notes…"))
    tick(br)
    for top in (5, 9, 2):
        output(slot, redraw(top, drop={top, top + 1}))
        tick(br)
    check("nothing after the reply is forwarded", len(br.sent) == 1, br.sent[1:])
    check("the turn stops listening", not slot.expect_marker and not slot.has_user_msg,
          f"expect_marker={slot.expect_marker} has_user_msg={slot.has_user_msg}")


def test_same_turn_prompt_keeps_listening():
    print("the newest prompt is still this turn's")
    br, slot = bridge(status=lambda sid: ({"prompt_marker": TOKEN}, 0.0))
    first_reply(br, slot)
    output(slot, f"{START}\n背景任務完成：週報已經寄出，收件人三位。\n{END}\n")
    tick(br)
    check("a real follow-up is forwarded", len(br.sent) == 2 and "週報已經寄出" in br.sent[-1], br.sent)
    check("still listening", slot.expect_marker and slot.has_user_msg)


def test_stale_or_unknown_status_changes_nothing():
    print("stale or unknown status")
    for label, status in (("computed before the forward", lambda sid: ({"prompt_marker": ""}, 3600.0)),
                          ("no prompt in the transcript tail", lambda sid: ({"prompt_marker": None}, 0.0)),
                          ("no status at all", lambda sid: None),
                          ("no status callback", None)):
        br, slot = bridge(status=status)
        first_reply(br, slot)
        output(slot, f"{START}\n背景任務完成：週報已經寄出，收件人三位。\n{END}\n")
        tick(br)
        check(f"{label}: follow-up still forwarded", len(br.sent) == 2, br.sent)


# ── 2. redraws inside a turn that is still live ─────────────────────────────

def test_redraws_of_the_sent_reply_are_not_follow_ups():
    print("redraws of the forwarded reply")
    br, slot = bridge(status=None)          # no transcript signal: only the redraw guard helps
    first_reply(br, slot)
    for top, drop, extra in ((3, {2, 3, 9}, ""), (9, {4, 5, 6}, ""),
                             (2, set(), "✻ Working…"), (4, {7}, ""), (6, set(), "")):
        output(slot, redraw(top, drop, extra))
        tick(br)
    check("no redraw is forwarded", len(br.sent) == 1, br.sent[1:])
    check("still listening for a real follow-up", slot.expect_marker)
    output(slot, f"{START}\n背景任務完成：週報已經寄出，收件人三位。\nskill／memory 建議：none\n{END}\n")
    tick(br)
    check("a real follow-up with the same closing line still goes out",
          len(br.sent) == 2 and "週報已經寄出" in br.sent[-1], br.sent)


def test_redraws_mixed_with_other_screen_text_are_bounded():
    """A redraw that drags in a long run of other screen text (tool progress,
    half the conversation) does not look like the reply; the cap bounds it."""
    print("redraws mixed with other screen text")
    br, slot = bridge(status=None)
    first_reply(br, slot)
    for i in range(12):
        output(slot, redraw(3 + i % 5, {i % 4}, f"Reading notes part {i}, scanning folder {i * 7}"))
        tick(br)
    cap = _bt.TelegramBridge._MAX_FOLLOWUPS
    # Once one got out, the next ones share its text and count as its redraws.
    check(f"at most reply + {cap} + notice", len(br.sent) <= 1 + cap + 1, f"{len(br.sent)} sent")


def test_repaint_metric():
    print("repaint_of")
    sent = [REPLY]
    garbled = REPLY.replace("時間到會", "時到").replace("10:00", "1:0")
    check("letters missing → redraw", bd.repaint_of(garbled, sent))
    check("reply plus a little screen → redraw", bd.repaint_of(REPLY + "\nReading notes…", sent))
    check("new text sharing the closing line → new",
          not bd.repaint_of("背景任務完成：週報已經寄出，收件人三位。\nskill／memory 建議：none", sent))
    check("nothing forwarded yet → never a redraw", not bd.repaint_of(REPLY, []))
    check("control-code residue in front → redraw", bd.repaint_of("38;5;174m9" + REPLY, sent))
    check("a short new sentence with no shared text → new", not bd.repaint_of("已完成。", sent))
    check("a different subject → new",
          not bd.repaint_of("第一個背景任務完成：報表 A 已產出。", sent))


# ── 3. hard cap ─────────────────────────────────────────────────────────────

def test_follow_up_cap():
    print("follow-up cap")
    br, slot = bridge(status=None)
    first_reply(br, slot)
    news = ["第一個背景任務完成：報表 A 已產出。", "第二個背景任務完成：報表 B 已上傳。",
            "第三個背景任務完成：清單 C 已寄出。", "第四個背景任務完成：備份 D 已建立。",
            "第五個背景任務完成：索引 E 已重建。"]
    for text in news:
        output(slot, f"{START}\n{text}\n{END}\n")
        tick(br)
    cap = _bt.TelegramBridge._MAX_FOLLOWUPS
    check(f"reply + {cap} follow-ups + one notice", len(br.sent) == 1 + cap + 1,
          f"{len(br.sent)} sent")
    check("the last message says forwarding stopped", "/fetch" in br.sent[-1], br.sent[-1:])
    check("the turn stops listening", not slot.expect_marker and not slot.has_user_msg)
    output(slot, f"{START}\n第六個背景任務完成。\n{END}\n")
    tick(br)
    check("nothing more after the notice", len(br.sent) == 1 + cap + 1, br.sent[-1:])


def test_new_message_resets_the_turn_counters():
    print("a new Telegram message starts a fresh turn")
    br, slot = bridge(status=None)
    first_reply(br, slot)
    k2 = "TG_REPLY_77aa00bb"
    br._begin_turn(slot, {"start": f"[[{k2}]]", "end": f"[[/{k2}]]", "prompt": ""}, ["again"])
    check("turn_sent is cleared", slot.turn_sent == [], slot.turn_sent)
    # Nearly the same answer as last turn: the redraw guard is per turn, so it goes out.
    # (Byte-identical text is held back by the older exact dedup, unchanged here.)
    output(slot, f"[[{k2}]]\n{REPLY.replace('10:00', '11:00')}\n[[/{k2}]]\n")
    tick(br)
    check("a near-identical answer to a new question is forwarded",
          len(br.sent) == 2 and "11:00" in br.sent[-1], br.sent)


def test_slot_from_before_a_hot_reload():
    """`sfctl reload` swaps the module but keeps the old slot objects, which
    have no `turn_sent`. The flush thread serves every tab, so an
    AttributeError there would stop replies everywhere."""
    print("slot created before a hot reload")
    br, slot = bridge(status=lambda sid: ({"prompt_marker": TOKEN}, 0.0))
    first_reply(br, slot)
    del slot.turn_sent
    output(slot, f"{START}\n背景任務完成：週報已經寄出，收件人三位。\n{END}\n")
    try:
        tick(br)
        ok = True
    except AttributeError as e:
        ok = False
        print("   ", e)
    check("the flush tick survives", ok)
    check("and the follow-up goes out", len(br.sent) == 2, br.sent)


# ── which prompt is the newest one ──────────────────────────────────────────

def _u(text):
    return {"kind": "user_msg", "text": text}


def test_latest_prompt_marker():
    print("latest_prompt_marker")
    tg = f"最終要回 Telegram 的文字請放在 {START} 和 {END} 之間。\n\n明天的會議幫我排一下"
    init = f"## Environment\nYou are in a tab.\n\n最終要回 Telegram 的文字請放在 {START} 和 {END} 之間。"
    lm = bd.latest_prompt_marker
    check("Telegram prompt → its token", lm([_u("earlier"), _u(tg)]) == TOKEN)
    check("token after an init prompt prefix", lm([_u(init)]) == TOKEN)
    check("token in a directive-framed prompt", lm([_u("<<<SF:SYSTEM>>>\n" + tg)]) == TOKEN)
    check("typed in the tab afterwards → ''", lm([_u(tg), {"kind": "assistant_text", "text": "ok"},
                                                  _u("幫我把下週的卡也排一排")]) == "")
    check("compaction summary is not a prompt",
          lm([_u(tg), _u("This session is being continued from a previous conversation. "
                         f"Earlier: {START}")]) == TOKEN)
    check("task notification is not a prompt",
          lm([_u(tg), _u("<task-notification>\n<status>completed</status>\nresult line\n"
                         "[[TG_REPLY_00000000]]")]) == TOKEN)
    check("interrupt is not a prompt", lm([_u(tg), _u("[Request interrupted by user]")]) == TOKEN)
    check("system reminder is not a prompt", lm([_u(tg), _u("<system-reminder>x</system-reminder>")]) == TOKEN)
    check("tool results are not prompts", lm([_u(tg), {"kind": "tool_result"}]) == TOKEN)
    check("no prompt in the tail → None", lm([{"kind": "assistant_text", "text": "x"}]) is None)
    check("empty → None", lm([]) is None)


def test_turn_superseded_unit():
    print("turn_superseded")
    ts = bd.turn_superseded
    fwd = time.time() - 5
    cb = lambda pm, age=0.0: (lambda sid: ({"prompt_marker": pm}, age))
    check("other prompt, fresh → True", ts(cb(""), "s1", [START], fwd))
    check("same token → False", not ts(cb(TOKEN), "s1", [START], fwd))
    check("an earlier in-flight token → False", not ts(cb(TOKEN), "s1", ["[[TG_REPLY_1]]", START], fwd))
    check("different token → True", ts(cb("TG_REPLY_ffffffff"), "s1", [START], fwd))
    check("unknown → False", not ts(cb(None), "s1", [START], fwd))
    check("computed before the forward → False", not ts(cb("", age=60.0), "s1", [START], fwd))
    check("nothing forwarded yet → False", not ts(cb(""), "s1", [START], 0.0))
    check("callback raises → False", not ts(lambda sid: 1 / 0, "s1", [START], fwd))


def test_status_tracker_reports_prompt_marker():
    print("StatusTracker carries prompt_marker")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "c.jsonl")

        def write(*records):
            with open(path, "w") as f:
                for i, (role, text) in enumerate(records):
                    rec = {"type": role, "timestamp": f"2026-01-01T00:00:{i:02d}Z",
                           "message": {"role": role, "content": [{"type": "text", "text": text}]}}
                    f.write(json.dumps(rec) + "\n")
        worker = {"sid": "s1", "cmd": "claude", "cwd": td, "tmux_name": ""}
        tr = ag.StatusTracker()
        with mock.patch.object(ag.StatusTracker, "_resolve_cached", return_value=path):
            write(("user", f"最終要回 Telegram 的文字請放在 {START} 和 {END} 之間。\n\n問題"),
                  ("assistant", f"{START}\n答案\n{END}"))
            check("Telegram prompt is newest", tr.status_for("s1", worker).get("prompt_marker") == TOKEN)
            write(("assistant", "還在做事"))
            check("no prompt in the tail keeps the last known value",
                  tr.status_for("s1", worker).get("prompt_marker") == TOKEN)
            write(("user", "幫我把下週的卡也排一排"), ("assistant", "好"))
            check("a prompt typed in the tab", tr.status_for("s1", worker).get("prompt_marker") == "")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print(f"\n{'FAIL' if FAILED else 'PASS'}: {len(FAILED)} failed")
    sys.exit(1 if FAILED else 0)
