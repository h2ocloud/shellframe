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
  * `now=True` (`--now`) sends at once, as before.

`composer_state` is also what `sfctl peek` and `sfctl state` use to say whether
the input box holds a suggestion or a draft.

Host-agnostic: `api` is the ShellFrame Api (duck-typed), so tests pass a fake.
"""

import collections
import re
import subprocess
import threading
import time

import sf_lang

QUEUE_MAX_WAIT = 1800.0     # seconds an item may wait before the sender is told
POLL = 1.5                  # seconds between checks while waiting
TAKEN_WAIT = 8.0            # after sending, how long to wait for the tab to take it

_CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TOKEN = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])|([^\x1b]+)|(\x1b)")
_RULE = re.compile(r"^[ \t]*─{10,}")
_PROMPT_GLYPHS = "❯›>"
_SPACE = " \t "


# ── what is in the input box ───────────────────────────────────────────────
def _chars_with_dim(line: str):
    """[(char, dim)] for one line with ANSI escapes, tracking SGR 2 (dim)."""
    out, dim = [], False
    for m in _TOKEN.finditer(line):
        params, final, text = m.group(1), m.group(2), m.group(3)
        if text is not None:
            out.extend((ch, dim) for ch in text)
        elif final == "m":
            codes = [c for c in (params or "0").split(";") if c != ""] or ["0"]
            for c in codes:
                if c in ("0", "22"):
                    dim = False
                elif c == "2":
                    dim = True
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
            chars.append(("\n", False))
        chars.extend(row)
    visible = [(c, d) for c, d in chars if c not in _SPACE and c != "\n"]
    text = "".join(c for c, _ in chars).strip(_SPACE + "\n")
    if not visible:
        return "empty", ""
    if any(not d for _, d in visible):
        return "draft", text
    return "suggestion", text


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


def busy_reason(api, sid: str) -> str:
    """'' when text can go in now; otherwise why it should wait."""
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
    kind, _ = composer_state(capture(api, sid))
    return "draft" if kind == "draft" else ""


def _reason_text(reason: str, lang: str = None) -> str:
    return {
        "working": sf_lang.pick("it is in the middle of a turn", "它正在跑", lang),
        "decision": sf_lang.pick("it is waiting on a decision", "它在等人做決定", lang),
        "telegram": sf_lang.pick("a Telegram message to it is in flight", "它正在處理 Telegram 的訊息", lang),
        "draft": sf_lang.pick("the user is typing in it", "輸入框有使用者還沒送出的字", lang),
        "closed": sf_lang.pick("the tab was closed", "分頁已經關了", lang),
        "queued": sf_lang.pick("messages ahead of it are still waiting", "前面還有訊息在排", lang),
    }.get(reason, reason)


# ── the queue ──────────────────────────────────────────────────────────────
_lock = threading.Lock()
_queues = {}                 # sid -> deque of items
_draining = set()


def pending(sid: str = "") -> list:
    """What is waiting (for `sfctl` and tests)."""
    with _lock:
        items = [(k, it) for k, q in _queues.items() for it in q if not sid or k == sid]
    return [{"sid": k, "chars": len(it["text"]), "waited_s": int(time.time() - it["t0"]),
             "from": it.get("from_sid", "")} for k, it in items]


def deliver(api, sid: str, text: str, *, submit: bool = True, now: bool = False,
            from_sid: str = "", on_timeout=None, what: str = "") -> dict:
    """Send `text` to tab `sid` now if it is free (or `now`), else queue it.
    Returns {"success", "message", "queued", "position", "reason"}."""
    s = api.sessions.get(sid)
    if not s:
        return {"success": False, "message": f"No such session: {sid}", "queued": False}
    with _lock:
        waiting = bool(_queues.get(sid))
    reason = "" if now else ("queued" if waiting else busy_reason(api, sid))
    if not reason:
        s._startup_trust_pending = False
        api._send_text_to_session(s, text, submit=submit)
        return {"success": True, "message": f"Sent {len(text)} chars to {sid}", "queued": False}
    item = {"text": text, "submit": submit, "t0": time.time(), "from_sid": from_sid,
            "on_timeout": on_timeout, "what": what}
    with _lock:
        q = _queues.setdefault(sid, collections.deque())
        q.append(item)
        position = len(q)
        start = sid not in _draining
        if start:
            _draining.add(sid)
    if start:
        threading.Thread(target=_drain, args=(api, sid), daemon=True, name=f"deliver-{sid}").start()
    why = _reason_text(reason)
    mins = int(QUEUE_MAX_WAIT // 60)
    return {"success": True, "queued": True, "position": position, "reason": reason,
            "message": sf_lang.pick(
                f"Queued for {sid} ({why}); it goes in when that ends, up to {mins} min. --now sends it at once.",
                f"已排隊等 {sid}（{why}）；結束後自動送出，最多等 {mins} 分鐘。要立刻送請加 --now。")}


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
            outcome = ""
            while True:
                if sid not in api.sessions:
                    outcome = "closed"
                    break
                reason = busy_reason(api, sid)
                if not reason:
                    outcome = "send"
                    break
                if time.time() - item["t0"] > QUEUE_MAX_WAIT:
                    outcome = reason
                    break
                time.sleep(POLL)
            with _lock:
                if _queues.get(sid) and _queues[sid][0] is item:
                    _queues[sid].popleft()
            if outcome == "send":
                s = api.sessions.get(sid)
                if s is not None:
                    sent_at = time.time()
                    s._startup_trust_pending = False
                    api._send_text_to_session(s, item["text"], submit=item["submit"])
                    deadline = time.time() + TAKEN_WAIT
                    while time.time() < deadline and not _taken(api, sid, sent_at):
                        time.sleep(0.25)
                continue
            cb = item.get("on_timeout")
            if cb:
                try:
                    cb(outcome, _reason_text(outcome))
                except Exception:
                    pass
    except Exception:
        with _lock:
            _draining.discard(sid)


def timeout_notice(target_label: str, why: str, mins: int = None) -> str:
    """What the sender reads when its message never went in."""
    mins = int(QUEUE_MAX_WAIT // 60) if mins is None else mins
    return sf_lang.pick(
        f"[[ShellBot: your message to \"{target_label}\" was not delivered after waiting up to {mins} min "
        f"({why}). It was dropped; send it again, or with --now to interrupt.]]",
        f"[[ShellBot：送給「{target_label}」的訊息等了最多 {mins} 分鐘還是沒送進去（{why}），已放棄。"
        f"要的話請再送一次，或加 --now 直接插話。]]")


def notify_sender(api, from_sid: str, target_label: str, why: str):
    """Tell whoever sent a message that never went in. A tab gets a note (queued
    like any other text, without a notice of its own); an external bot gets it
    on its webhook; anything else is only logged."""
    note = timeout_notice(target_label, why)
    try:
        if from_sid and from_sid in api.sessions:
            deliver(api, from_sid, note, what="timeout-notice")
        elif from_sid.startswith("ext:") and hasattr(api, "_bots_external_deliver"):
            api._bots_external_deliver(from_sid[4:], {"kind": "send", "text": note, "from_label": "ShellBot"})
    except Exception:
        pass


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
