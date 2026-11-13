"""Hand text to a tab the way a person would: not in the middle of its turn, not
into a permission menu, and not on top of something the user is typing.

Before this, `sfctl send`, A2A and delegate typed into the tab at once, while the
documentation said `send` waited for the tab to be idle. An agent that promised
"I'll hand it over once it's done" had to write its own polling loop, and a
different model simply didn't. Waiting is now ShellBot's job, so it holds for
every model and every CLI:

  * the tab is working, waiting on a decision, or a Telegram turn is in flight
    → the text waits in a per-tab queue and goes in when that ends;
  * the input box holds text the user typed and has not sent → it waits too.
    A dim (SGR 2) suggestion that Claude Code paints in the box is *not* the
    user's: measured on live tabs, `❯ \\x1b[2m<suggestion>\\x1b[0m` vs plain text;
  * one tab's queue is first in, first out, and the next item waits until the
    previous one has been taken (hook UserPromptSubmit, or the tab turns busy);
  * after QUEUE_MAX_WAIT (30 min) the item is dropped and the sender is told;
    a send from the chat view has no sender tab: it is kept in GIVEN_UP for the
    view (queue_state), which marks the bubble "not sent" with a resend. Measured
    before: a chat send queued behind a draft in the terminal was dropped after
    30 min with a debug line only, and the bubble that said "waiting" went away;
  * `now=True` (`--now`) sends at once, as before;
  * the queue is kept on disk (STATE_FILE). Measured: an item queued at 13:21:11
    vanished with the 13:25:23 restart, never delivered and the sender never
    told. After a restart each item carries on from its original start time;
    one whose tab does not come back, whose time ran out, or that was being
    typed in when the app went down is reported to the sender instead.

`composer_state` is also what `sfctl peek` and `sfctl state` use to say whether
the input box holds a suggestion or a draft.

Host-agnostic: `api` is the ShellFrame Api (duck-typed), so tests pass a fake.
"""

import collections
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import agent_link
import sf_lang
from sf_log import _dlog

QUEUE_MAX_WAIT = 1800.0     # seconds an item may wait before the sender is told (--ttl changes it)
MAX_TTL = 86400.0           # --ttl 0 (until the tab is free) and any --ttl stop at a day
RESULT_WAIT = 86400.0       # a2a-result: the requester is waiting for it, late beats lost
LATE_NOTE_S = 1800.0        # an item that waited longer goes in with a line saying how long
POLL = 1.5                  # seconds between checks while waiting
TAKEN_WAIT = 8.0            # after sending, how long to wait for the tab to take it
STATE_FILE = None           # set by the app at start-up; None (tests) keeps the queue in memory
RESTORE_GRACE = 180.0       # after a restart, how long an item waits for its tab to come back
RESUME_SETTLE = 20.0        # …and how long before a restored item may go in at all (see _wait_turn)
GIVEN_UP_KEEP = 7200.0      # how long the chat view can still see a send that never went in
CLEAR_KEY = "\x03"         # Ctrl+C: measured in Claude Code 2.1, it empties a draft of any length
                            # (Ctrl+U takes only the cursor's line); once, never twice: a second
                            # one within a few seconds quits the CLI ("Press Ctrl-C again to exit")

_CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TOKEN = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])|([^\x1b]+)|(\x1b)")
_RULE = re.compile(r"^[ \t]*─{10,}")
_PROMPT_GLYPHS = "❯›>"
_SPACE = " \t "


# ── what is in the input box ───────────────────────────────────────────────
def _chars_with_dim(line: str):
    """[(char, dim, inverse)] for one line with ANSI escapes, tracking SGR 2 (dim) and
    SGR 7 (inverse: how Claude Code draws its own cursor in a pane without focus)."""
    out, dim, inv = [], False, False
    for m in _TOKEN.finditer(line):
        params, final, text = m.group(1), m.group(2), m.group(3)
        if text is not None:
            out.extend((ch, dim, inv) for ch in text)
        elif final == "m":
            codes = [c for c in (params or "0").split(";") if c != ""] or ["0"]
            for c in codes:
                if c == "0":
                    dim = inv = False
                elif c == "22":
                    dim = False
                elif c == "2":
                    dim = True
                elif c == "7":
                    inv = True
                elif c == "27":
                    inv = False
    return out


def composer_state(screen_ansi: str):
    """('empty' | 'suggestion' | 'draft' | 'unknown', text) for an AI CLI screen
    captured with its colours. 'unknown' when no input box (two `────` rules)
    is in view — then nothing is assumed about it."""
    lines = (screen_ansi or "").split("\n")
    plain = [_CSI.sub("", ln) for ln in lines]
    rules = [i for i, ln in enumerate(plain) if _RULE.match(ln)]
    if len(rules) < 2:
        return "unknown", ""
    chars = []
    for i, raw in enumerate(lines[rules[-2] + 1:rules[-1]]):
        row = _chars_with_dim(raw)
        if i == 0:   # drop the prompt glyph and the space after it
            k = 0
            while k < len(row) and row[k][0] in _SPACE:
                k += 1
            if k < len(row) and row[k][0] in _PROMPT_GLYPHS:
                k += 1
                while k < len(row) and row[k][0] in _SPACE:
                    k += 1
            row = row[k:]
        if chars:
            chars.append(("\n", False, False))
        chars.extend(row)
    visible = [(c, d, v) for c, d, v in chars if c not in _SPACE and c != "\n"]
    text = "".join(c for c, _, _ in chars).strip(_SPACE + "\n")
    if not visible:
        return "empty", ""
    plain = [x for x in visible if not x[1]]
    # A pane without focus gets Claude Code's own cursor: an inverse block over the first
    # character of the dim suggestion (measured: `\x1b[7mT\x1b[0;2mry …`). That one is the
    # suggestion's too; a typed draft has more than it.
    if plain and not (len(plain) == 1 and plain[0][2] and len(visible) > 1):
        return "draft", text
    return "suggestion", text


# The line that says a turn is running, just above the input box: "esc to interrupt", or
# (newer Claude Code) the spinner's running clock "· Undulating… (12m 53s · ↓ 1.7k tokens)".
# The clock line starts at column 0 with one of Claude Code's spinner glyphs (2.1.319:
# · ✢ ✳ ✶ ✻ ✽, and * on some platforms). Quoted in a conversation the same words are
# indented, bulleted or behind ❯/⏺; an idle tab showing them must not read as running.
# Same rule as agent_status._screen_signals["actively_working"].
_RUNNING_CLOCK = re.compile(r"^[·✢✳✶✻✽*] +[A-Z][^\n…]{0,40}…\s*\((?:\d+h\s*)?(?:\d+m\s*)?\d+s\b")


def status_line(screen: str) -> str:
    """The running-turn line near the bottom of a screen (plain or with colours), or ''.
    A short conversation leaves blank rows under the input box; they do not count."""
    for line in reversed(_CSI.sub("", screen or "").rstrip().splitlines()[-12:]):
        if _RUNNING_CLOCK.match(line) or "esc to interrupt" in line:
            return line.strip()
    return ""


def screen_text(api, sid: str, lines: int = 60) -> str:
    """What the tab's screen shows, as plain text (tmux), for `sfctl peek --raw`."""
    s = (getattr(api, "sessions", None) or {}).get(sid)
    name = getattr(s, "_tmux_name", None) if s else None
    if not name:
        return _CSI.sub("", bytes(getattr(s, "_recent", b"") or b"").decode("utf-8", "replace")) if s else ""
    try:
        r = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-t", name, "-S", f"-{max(lines, 40)}"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def visible_text(api, sid: str) -> str:
    """What the tab's screen shows now, without its scrollback: a menu up there may be one
    answered long ago. tmux's pane; without tmux the Telegram slot's screen, else the recent
    output screen_text reads (the menu reader then takes the last menu in it)."""
    s = (getattr(api, "sessions", None) or {}).get(sid)
    name = getattr(s, "_tmux_name", None) if s else None
    if name:
        try:
            r = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-t", name],
                               capture_output=True, text=True, timeout=3)
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""
    slot = ((getattr(getattr(api, "bridge", None), "slots", None) or {}).get(sid)) if s else None
    if slot is not None and getattr(slot, "screen", None) is not None:
        try:
            return "\n".join(slot.screen.display)
        except Exception:
            pass
    return screen_text(api, sid)


ANSWER_SETTLE_S = 0.8


def answer_choice(api, sid: str, n, kind: str = "menu", write=None) -> str:
    """Type option `n` for the question tab `sid` waits on; returns what went in.
    "red": the agent's own [[SF:RED]] question at its prompt. The number is a message, and
    Enter sends it. "menu": a numbered menu on screen. Some CLIs take the number alone and
    move on, others need Enter, and an Enter after a menu that already moved on answers
    whatever comes next (measured: Codex's start-up update menu answered "2", and the Enter
    accepted the folder-trust prompt behind it). So the number, then Enter only while the
    same menu is still on the screen."""
    s = (getattr(api, "sessions", None) or {}).get(sid)
    write = write or (s.write if s else None)
    if not write:
        return ""
    if kind != "menu":
        write(f"{n}\r")
        return f"{n}\r"
    import bridge_telegram

    def menu_now():
        return [o["text"] for o in bridge_telegram.menu_block(visible_text(api, sid).splitlines())[0]]
    before = menu_now()
    write(str(n))
    gone, deadline = 0, time.time() + ANSWER_SETTLE_S
    while time.time() < deadline:
        time.sleep(0.1)
        gone = gone + 1 if menu_now() != before else 0
        if gone >= 2:                      # twice in a row, so a half-drawn screen does not count
            return str(n)
    write("\r")
    return f"{n}+Enter"


def capture(api, sid: str) -> str:
    """The tab's screen with colours (tmux), or '' when it cannot be read."""
    s = api.sessions.get(sid)
    name = getattr(s, "_tmux_name", None) if s else None
    if not name:
        return ""
    try:
        r = subprocess.run(["tmux", "capture-pane", "-p", "-e", "-t", name, "-S", "-40"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


HOLD = {}                    # sid -> reason: a tab that is being driven key by key (tab_model's menus) takes no text


def busy_reason(api, sid: str) -> str:
    """'' when text can go in now; otherwise why it should wait."""
    if sid in HOLD:
        return HOLD[sid]
    try:
        state = api._agent_state_for_list(sid) or ""
    except Exception:
        state = ""
    if state in ("working", "decision"):
        return state
    try:
        if api._bridge_session_busy(sid):
            return "telegram"
    except Exception:
        pass
    screen = capture(api, sid)
    # No state yet: the status monitor's first look after a restart takes seconds (measured 8-20 s,
    # the tab reads "idle 0s" meanwhile). A message sent then went straight into a running turn.
    if not state and status_line(screen):
        return "working"
    kind, _ = composer_state(screen)
    return "draft" if kind == "draft" else ""


def _reason_text(reason: str, lang: str = None) -> str:
    return {
        "working": sf_lang.pick("it is in the middle of a turn", "它正在跑", lang),
        "decision": sf_lang.pick("it is waiting on a decision", "它在等人做決定", lang),
        "telegram": sf_lang.pick("a Telegram message to it is in flight", "它正在處理 Telegram 的訊息", lang),
        "model": sf_lang.pick("it is switching its model", "它正在換模型", lang),
        "draft": sf_lang.pick("the user is typing in it", "輸入框有使用者還沒送出的字", lang),
        "closed": sf_lang.pick("the tab was closed", "分頁已經關了", lang),
        "queued": sf_lang.pick("messages ahead of it are still waiting", "前面還有訊息在排", lang),
        "expired": sf_lang.pick("the waiting time ran out", "等候時間已用完", lang),
        "lost": sf_lang.pick("ShellBot restarted and the tab did not come back",
                             "ShellBot 重啟後，這個分頁沒有回來", lang),
        "uncertain": sf_lang.pick("ShellBot restarted while typing it in, so it may or may not have gone in",
                                  "ShellBot 在送進去的途中重啟，不確定有沒有送到", lang),
    }.get(reason, reason)


# ── the queue ──────────────────────────────────────────────────────────────
_lock = threading.Lock()
_save_lock = threading.RLock()  # resume() holds it while it queues the old items (_save re-enters)
_queues = {}                 # sid -> deque of items
_draining = set()
_resumed_at = 0.0            # when resume() loaded the queue after a restart
_saved = None                # what the previous run left in STATE_FILE (see _read_saved)
ON_DELIVERED = []            # fn(sid, what, at) once an item is typed in (a2a_host: hand-offs)
_KEEP = ("sid", "text", "submit", "t0", "from_sid", "what", "label", "sending", "max_wait", "id", "clear_draft")
GIVEN_UP = {}                # sid -> deque of {"id", "why", "outcome", "at"}: chat sends that never went in


def wait_limit(ttl_min=None, default: float = None) -> float:
    """`--ttl` minutes → seconds an item may wait: none given → `default` (30 min),
    0 → until the tab is free, everything capped at MAX_TTL."""
    default = QUEUE_MAX_WAIT if default is None else default
    try:
        ttl = float(ttl_min) if ttl_min not in (None, "") else None
    except (TypeError, ValueError):
        ttl = None
    if ttl is None or ttl < 0:
        return default
    return MAX_TTL if ttl == 0 else min(ttl * 60.0, MAX_TTL)


def _waited_text(seconds: float, lang: str = None) -> str:
    h, m = int(seconds // 3600), int(seconds % 3600 // 60)
    return sf_lang.pick(f"{h} h {m} min" if h else f"{m} min", f"{h} 小時 {m} 分" if h else f"{m} 分", lang)


def with_late_note(text: str, waited: float, lang: str = None) -> str:
    """`text` with late_note in front of its words: after the [[A2A…]] header when it starts with one,
    so the header stays first, where agent_link.sender_of, the chat view and a phone look for whose
    message it is (in front of the header, a long-queued agent message read as the person's)."""
    note, parts = late_note(waited, lang), agent_link.header_parts(text)
    rest = parts[2] if parts else ""
    if not parts or (rest and not text.endswith(rest)):
        return note + "\n" + text
    return text[:len(text) - len(rest)].rstrip() + "\n" + note + ("\n" + rest if rest else "")


def late_note(waited: float, lang: str = None) -> str:
    return sf_lang.pick(f"[[ShellBot: this waited {_waited_text(waited, lang)} in the queue before it went in]]",
                        f"[[ShellBot：這則排隊了 {_waited_text(waited, lang)} 才送進來]]", lang)


def pending(sid: str = "") -> list:
    """What is waiting (for `sfctl` and tests)."""
    with _lock:
        items = [(k, it) for k, q in _queues.items() for it in q if not sid or k == sid]
    return [{"sid": k, "chars": len(it["text"]), "waited_s": int(time.time() - it["t0"]),
             "from": it.get("from_sid", "")} for k, it in items]


def queue_state(sid: str) -> dict:
    """For the chat view: the ids of its sends still waiting for tab `sid`, and the
    ones given up in the last GIVEN_UP_KEEP seconds ({"id", "why", "outcome"})."""
    now = time.time()
    with _lock:
        waiting = [it["id"] for it in _queues.get(sid, ()) if it.get("id")]
        gone = [{k: g[k] for k in ("id", "why", "outcome")} for g in GIVEN_UP.get(sid, ())
                if now - g["at"] < GIVEN_UP_KEEP]
    return {"queued": waiting, "given_up": gone}


def take(sid: str, item_id: str):
    """Take the waiting item `item_id` of tab `sid` out of its queue (not one being typed in now)
    and return it; None when it is not there (gone in, given up, or being typed)."""
    with _lock:
        q = _queues.get(sid)
        hit = next((it for it in (q or ()) if it.get("id") == item_id and not it.get("sending")), None)
        if hit is None:
            return None
        q.remove(hit)
        hit["cancelled"] = True           # _wait_turn may be polling it right now
    _save()
    return hit


def cancel(sid: str, item_id: str) -> bool:
    """Take the waiting item `item_id` of tab `sid` out of its queue. True when it was there."""
    return take(sid, item_id) is not None


def draft_of(api, sid: str) -> str:
    """The text the person left unsent in tab `sid`'s input box, or ''. A dim
    suggestion is not a draft. Only CLIs whose box composer_state reads (Claude
    Code's); others say ''."""
    kind, text = composer_state(capture(api, sid))
    return text if kind == "draft" else ""


def clear_draft_now(api, sid: str) -> bool:
    """Empty the input box of tab `sid` with one CLEAR_KEY, only while the tab is not
    working or asking (Ctrl+C would stop its turn), and only when a draft is there.
    True once the box holds no draft."""
    s = api.sessions.get(sid)
    if not s:
        return False
    try:
        state = api._agent_state_for_list(sid) or ""
    except Exception:
        state = ""
    if state in ("working", "decision"):
        return False
    if not draft_of(api, sid):
        return True
    s.write(CLEAR_KEY)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        time.sleep(0.2)
        if not draft_of(api, sid):
            _dlog("deliver", f"{sid}: cleared the draft in the input box, as asked")
            return True
    _dlog("deliver", f"{sid}: the draft in the input box did not clear")
    return False


def _delivered(sid: str, what: str, at: float):
    for fn in list(ON_DELIVERED):
        try:
            fn(sid, what or "", at)
        except Exception:
            pass


def _read_saved() -> list:
    """What the previous run left in STATE_FILE, read once, before this run first
    writes the file. resume() runs only once every tab has reattached (measured 15 s
    after a restart) while the queue takes messages from the first seconds; reading
    the file then found this run's items in it and not the previous run's. Measured:
    a message queued at 17:31:15 was resumed at 17:31:20 as an old one and went in
    twice. Caller holds _save_lock."""
    global _saved
    if _saved is None:
        try:
            rows = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            rows = []
        except Exception as e:
            _dlog("deliver", f"queue file unreadable, nothing resumed: {e!r}")
            rows = []
        _saved = [r for r in (rows if isinstance(rows, list) else [])
                  if isinstance(r, dict) and r.get("sid") and isinstance(r.get("text"), str)
                  and not str(r.get("what") or "").startswith("model:")]    # a model switch is not carried over: nothing would drive its menu
    return _saved


def _save():
    """Write the whole queue to STATE_FILE (atomically, owner-only: it holds
    message text). Called after every change, so a restart finds it current.
    Until resume() takes them, the previous run's items are written back too."""
    if not STATE_FILE:
        return
    with _save_lock:
        old = [] if _resumed_at else _read_saved()
        with _lock:
            rows = old + [{k: it[k] for k in _KEEP if k in it} for q in _queues.values() for it in q]
        path = Path(STATE_FILE)
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception as e:
            _dlog("deliver", f"saving the queue failed: {e!r}")


def _enqueue(api, items) -> int:
    """Append items (oldest first) and start draining their tabs. Returns the
    queue position of the last one."""
    position = 0
    with _lock:
        for it in items:
            q = _queues.setdefault(it["sid"], collections.deque())
            q.append(it)
            position = len(q)
        start = sorted({it["sid"] for it in items} - _draining)
        _draining.update(start)
    _save()
    for sid in start:
        threading.Thread(target=_drain, args=(api, sid), daemon=True, name=f"deliver-{sid}").start()
    return position


def resume(api) -> int:
    """After a restart: carry on with what the previous run left waiting. Each
    item keeps its original start time, so it waits only what is left of its
    QUEUE_MAX_WAIT, and its tab gets RESTORE_GRACE to come back. It queues behind
    anything sent since the restart. Only the first call loads."""
    global _resumed_at, _saved
    if not STATE_FILE or _resumed_at:
        return 0
    with _save_lock:                  # no write in between: the file never lacks them or has them twice
        rows, _saved = list(_read_saved()), []
        _resumed_at = time.time()     # from here _save() writes them from the queue
        items = sorted(({"submit": True, "from_sid": "", "what": "", "label": r["sid"], **r,
                         "t0": float(r.get("t0") or _resumed_at), "restored": True,
                         "grace_until": _resumed_at + RESTORE_GRACE} for r in rows),
                       key=lambda it: it["t0"])
        if items:
            _enqueue(api, items)
    if items:
        _dlog("deliver", f"resumed {len(items)} queued item(s) after a restart")
    return len(items)


def deliver(api, sid: str, text: str, *, submit: bool = True, now: bool = False,
            from_sid: str = "", on_timeout=None, what: str = "", label: str = "",
            max_wait: float = None, clear_draft: bool = False, queue: bool = True) -> dict:
    """Send `text` to tab `sid` now if it is free (or `now`), else queue it.
    `queue=False`: only ever now. A tab that is not free (or has texts waiting ahead) is
    left alone and nothing is queued, so a text that may as well wait for the caller's
    next look (a periodic memory save) never holds up the ones people and agents sent:
    {"success": False, "code": "busy", "reason": why}.
    An item that never goes in is reported to `from_sid` (`label` names the
    target in that note), or handed to `on_timeout(outcome, why)` instead.
    `clear_draft`: the person chose to drop the draft in the input box (the chat
    view's card); it is cleared once the tab is not working, then the text goes in.
    Returns {"success", "message", "queued", "position", "reason", "id"}."""
    s = api.sessions.get(sid)
    if not s:
        return {"success": False, "message": f"No such session: {sid}", "queued": False}
    with _lock:
        waiting = bool(_queues.get(sid))
    reason = "" if now else ("queued" if waiting else busy_reason(api, sid))
    if reason and not queue:
        return {"success": False, "code": "busy", "queued": False, "reason": reason,
                "message": _reason_text(reason)}
    if reason == "draft" and clear_draft:
        if not clear_draft_now(api, sid):
            return {"success": False, "code": "draft_stuck", "queued": False, "message": sf_lang.pick(
                "The draft in the terminal did not clear; nothing was sent.",
                "終端機裡的草稿沒有清掉，所以沒有送出。")}
        reason = busy_reason(api, sid)
    if not reason:
        s._startup_trust_pending = False
        api._send_text_to_session(s, text, submit=submit)
        _delivered(sid, what, time.time())
        return {"success": True, "message": f"Sent {len(text)} chars to {sid}", "queued": False}
    item = {"sid": sid, "text": text, "submit": submit, "t0": time.time(), "from_sid": from_sid,
            "what": what, "label": label or sid, "id": uuid.uuid4().hex[:12]}
    if clear_draft:
        item["clear_draft"] = True
    if max_wait:
        item["max_wait"] = float(max_wait)
    if on_timeout:
        item["on_timeout"] = on_timeout
    position = _enqueue(api, [item])
    why = _reason_text(reason)
    mins = int(_limit(item) // 60)
    return {"success": True, "queued": True, "position": position, "reason": reason, "id": item["id"],
            "message": sf_lang.pick(
                f"Queued for {sid} ({why}); it goes in when that ends, up to {mins} min "
                f"(--ttl changes that). --now sends it at once.",
                f"已排隊等 {sid}（{why}）；結束後自動送出，最多等 {mins} 分鐘（--ttl 可改）。要立刻送請加 --now。")}


def _taken(api, sid: str, since: float) -> bool:
    try:
        snap = api._agent_status_snapshot(sid)
        res = snap[0] if isinstance(snap, tuple) else snap
        if res and float(res.get("prompt_at") or 0) >= since:
            return True
    except Exception:
        pass
    try:
        return api._agent_state_for_list(sid) == "working"
    except Exception:
        return False


def _limit(item: dict) -> float:
    return float(item.get("max_wait") or QUEUE_MAX_WAIT)


def _wait_turn(api, sid: str, item: dict) -> str:
    """Wait until the tab can take `item`: 'send', or why it never will."""
    while True:
        if item.get("cancelled"):
            return "cancelled"
        if item.get("sending"):          # the app went down while typing it in
            return "uncertain"
        if sid not in api.sessions:
            if time.time() < item.get("grace_until", 0):   # just restarted: tab may be on its way
                time.sleep(POLL)
                continue
            return "lost" if item.get("restored") else "closed"
        # Measured: right after a restart no tab has a state yet (no hook event, no status
        # snapshot), so every tab reads "not busy"; a restored item went into a tab 0.6 s
        # after the resume, in the middle of its turn. Let the states settle first.
        if item.get("restored") and time.time() < _resumed_at + RESUME_SETTLE:
            time.sleep(POLL)
            continue
        reason = busy_reason(api, sid)
        if reason == "draft" and item.get("clear_draft") and not item.get("cleared"):
            item["cleared"] = True            # one Ctrl+C at most: a second one quits the CLI
            if clear_draft_now(api, sid):
                reason = busy_reason(api, sid)
        if time.time() - item["t0"] > _limit(item):
            return reason or "expired"
        if not reason:
            return "send"
        time.sleep(POLL)


def _drop(sid: str, item: dict):
    with _lock:
        q = _queues.get(sid)
        if q and q[0] is item:
            q.popleft()
    _save()


def _fail(api, item: dict, outcome: str):
    """Report an item that never went in: to `on_timeout` if one was given,
    else to the sender's tab or webhook."""
    why = _reason_text(outcome)
    if item.get("restored") and outcome not in ("lost", "uncertain"):
        why += sf_lang.pick(" (ShellBot restarted in between)", "（期間 ShellBot 重啟過）")
    _dlog("deliver", f"{item['sid']} not delivered ({outcome}), {len(item['text'])} chars "
                     f"from {item.get('from_sid') or '?'}")
    if item.get("what") == "chat" and item.get("id"):
        with _lock:
            GIVEN_UP.setdefault(item["sid"], collections.deque(maxlen=20)).append(
                {"id": item["id"], "why": why, "outcome": outcome, "at": time.time()})
    cb = item.get("on_timeout")
    if cb:
        try:
            cb(outcome, why)
        except Exception:
            pass
    elif item.get("from_sid"):
        notify_sender(api, item["from_sid"], item.get("label") or item["sid"], why, outcome,
                      mins=int(_limit(item) // 60))


def _drain(api, sid: str):
    try:
        while True:
            with _lock:
                q = _queues.get(sid)
                if not q:
                    _queues.pop(sid, None)
                    _draining.discard(sid)
                    return
                item = q[0]
            outcome = _wait_turn(api, sid, item)
            s = api.sessions.get(sid) if outcome == "send" else None
            if outcome == "send" and s is None:
                outcome = "closed"
            if outcome == "cancelled":
                continue
            if outcome != "send":
                _fail(api, item, outcome)     # first, so the note is saved before the item goes
                _drop(sid, item)
                continue
            sent_at = time.time()
            item["sending"] = sent_at         # on disk while typing: a restart now cannot tell
            _save()
            s._startup_trust_pending = False
            waited = sent_at - item["t0"]
            text = item["text"] if waited <= LATE_NOTE_S else with_late_note(item["text"], waited)
            api._send_text_to_session(s, text, submit=item["submit"])
            _drop(sid, item)
            _delivered(sid, item.get("what", ""), sent_at)
            deadline = time.time() + TAKEN_WAIT
            while time.time() < deadline and not _taken(api, sid, sent_at):
                time.sleep(0.25)
    except Exception as e:
        _dlog("deliver", f"{sid} queue stopped: {e!r}")
        with _lock:
            _draining.discard(sid)


def timeout_notice(target_label: str, why: str, mins: int = None, outcome: str = "") -> str:
    """What the sender reads when its message never went in."""
    if outcome == "uncertain":
        return sf_lang.pick(
            f"[[ShellBot: your message to \"{target_label}\" may not have been delivered ({why}). "
            f"Check with sfctl peek before sending it again.]]",
            f"[[ShellBot：送給「{target_label}」的訊息可能沒送到（{why}）。"
            f"請先用 sfctl peek 看一下，再決定要不要重送。]]")
    if outcome in ("closed", "lost"):
        return sf_lang.pick(
            f"[[ShellBot: your message to \"{target_label}\" was not delivered ({why}). "
            f"It was dropped; send it again if it still matters.]]",
            f"[[ShellBot：送給「{target_label}」的訊息沒有送到（{why}），已放棄。還需要的話請再送一次。]]")
    mins = int(QUEUE_MAX_WAIT // 60) if mins is None else mins
    return sf_lang.pick(
        f"[[ShellBot: your message to \"{target_label}\" was not delivered after waiting up to {mins} min "
        f"({why}). It was dropped; send it again, or with --now to interrupt.]]",
        f"[[ShellBot：送給「{target_label}」的訊息等了最多 {mins} 分鐘還是沒送進去（{why}），已放棄。"
        f"要的話請再送一次，或加 --now 直接插話。]]")


def notify_sender(api, from_sid: str, target_label: str, why: str, outcome: str = "", mins: int = None):
    """Tell whoever sent a message that never went in. A tab gets a note (queued
    like any other text, without a notice of its own); an external bot gets it
    on its webhook; anything else is only logged. Right after a restart the
    sender's tab may not be back yet, so the note waits for it like any item."""
    note = timeout_notice(target_label, why, mins=mins, outcome=outcome)
    try:
        if from_sid and from_sid in api.sessions:
            deliver(api, from_sid, note, what="timeout-notice")
        elif from_sid.startswith("ext:") and hasattr(api, "_bots_external_deliver"):
            api._bots_external_deliver(from_sid[4:], {"kind": "send", "text": note, "from_label": "ShellBot"})
        elif from_sid and time.time() < _resumed_at + RESTORE_GRACE:
            _enqueue(api, [{"sid": from_sid, "text": note, "submit": True, "t0": time.time(),
                            "from_sid": "", "what": "timeout-notice", "label": from_sid, "restored": True,
                            "grace_until": _resumed_at + RESTORE_GRACE}])
        else:
            _dlog("deliver", f"could not tell {from_sid or 'an unknown sender'}: {why}")
    except Exception as e:
        _dlog("deliver", f"telling {from_sid} failed: {e!r}")


def composer_note(kind: str, text: str) -> str:
    """One line for `sfctl peek`/`state` saying what the input box holds."""
    snippet = (text or "").replace("\n", " ")[:80]
    if kind == "suggestion":
        return sf_lang.pick(f"[ShellBot] The input box shows a dim suggestion, not the user's words: \"{snippet}\"",
                            f"[ShellBot] 輸入框裡是灰色建議字，不是使用者打的：「{snippet}」")
    if kind == "draft":
        return sf_lang.pick(f"[ShellBot] The user has unsent text in the input box: \"{snippet}\"",
                            f"[ShellBot] 輸入框有使用者還沒送出的字：「{snippet}」")
    return ""
