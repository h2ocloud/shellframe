"""Did a chat-bridge injection actually reach the agent? Pure helpers.

The bridge types a message into a tab (bracketed paste + Enter) and then has to
decide whether it landed. Two kinds of evidence exist:

* the agent's own hook said "prompt accepted" (UserPromptSubmit) -- exact,
  independent of what the screen looks like;
* the screen: is the payload still sitting in the input box (it never got
  submitted), or is it only echoed in the conversation above (it did)?

Telling those two screen cases apart is the whole job of `residue_in_composer`.
Matching the payload's tail anywhere on screen cannot, because a submitted
message is echoed in the conversation, so every delivered message looked
"stuck", got a pointless Enter and was then typed in a second time.

The second half answers the opposite question: after a reply went out, is
anything that still looks like a reply really a new one? See `turn_superseded`
and `repaint_of`.
"""
import re
import time

_RULE = re.compile(r"^[ \t]*─{10,}")
_CHIP = re.compile(r"\[Pasted (?:Content|text)[^\]]*\]", re.I)


def prompt_accepted(status_cb, sid: str, injected_at: float) -> bool:
    """True when the agent hooks saw a prompt submitted at or after `injected_at`.

    `status_cb(sid)` is the host's read-only status snapshot; the hook's
    UserPromptSubmit time travels in it as `prompt_at`."""
    if not status_cb:
        return False
    try:
        got = status_cb(sid)
    except Exception:
        return False
    res = got[0] if isinstance(got, tuple) else got
    at = (res or {}).get("prompt_at") or 0.0
    return bool(at) and float(at) >= injected_at


def composer_text(screen: str) -> str:
    """The input box of an AI CLI screen: what sits between its two rules.

    Claude Code draws the input box between two `────` rules; the conversation
    (including the echo of a message just sent) is above the upper one and the
    footer below the lower one. Fewer than two rules in view (a composer taller
    than the sampled rows) -> the whole text, which is the old behaviour."""
    lines = screen.split("\n")
    rules = [i for i, ln in enumerate(lines) if _RULE.match(ln)]
    return "\n".join(lines[rules[-2] + 1:rules[-1]]) if len(rules) >= 2 else screen


def residue_in_composer(screen: str, tail: str) -> bool:
    """True when the payload (its last 24 non-space chars, `tail`) or a paste
    chip is still in the input box, i.e. the submit did not land."""
    box = composer_text(screen)
    return (bool(tail) and tail in re.sub(r"\s+", "", box)) or bool(_CHIP.search(box))


# ── After the reply: is this still the Telegram turn, and is this text new? ──
#
# A tab running Claude Code in full-screen mode (alternate screen) redraws the
# visible conversation on every change, and only the cells that changed. Each
# redraw re-emits the reply markers of a reply that is still in view, with
# whatever happened to be repainted in between: the same reply with letters
# missing, tool progress, half of the screen. Every one of those spans differs,
# so the exact-match dedup never matches, and each was forwarded as a "follow-up".
# Measured: one reply in the transcript, 29 Telegram messages, all sent after
# the user had moved on and was typing in the tab itself.

_TG_TOKEN = re.compile(r"TG_REPLY_[0-9a-fA-F]+")
# User records that are not someone giving the tab a new instruction.
_NOT_A_PROMPT = ("This session is being continued from a previous conversation",
                 "<task-notification", "[Request interrupted")


def latest_prompt_marker(evs):
    """Reply-marker token in the newest real prompt of a transcript tail.

    "TG_REPLY_ab12cd34" when that prompt came from the Telegram bridge, "" when
    it carries none (typed into the tab, or sent by another tool), None when
    the tail holds no real prompt (unknown). Task notifications, interrupts,
    the compaction summary and system-injected `<...>` records are skipped."""
    for e in reversed(evs or ()):
        if e.get("kind") != "user_msg":
            continue
        text = (e.get("text") or "").strip()
        if not text or text.startswith(_NOT_A_PROMPT):
            continue
        m = _TG_TOKEN.search(text)
        if m:
            return m.group(0)
        if not text.startswith("<"):
            return ""
    return None


def turn_superseded(status_cb, sid: str, start_markers, forwarded_at: float) -> bool:
    """True when the tab has taken a newer prompt that is not one of this
    Telegram turn's, so nothing it prints from now on is a reply to Telegram.

    Only a status computed after the last forward counts: before it, the
    newest prompt in the transcript may still be an older one."""
    if not status_cb or not forwarded_at:
        return False
    try:
        got = status_cb(sid)
    except Exception:
        return False
    res, age = got if isinstance(got, tuple) else (got, 0.0)
    pm = (res or {}).get("prompt_marker")
    if pm is None or time.time() - (age or 0.0) <= forwarded_at:
        return False
    return pm not in {m.strip("[]") for m in start_markers if m}


def repaint_of(candidate: str, sent, n: int = 4) -> bool:
    """True when `candidate` is a redraw of text already forwarded this turn,
    not a new reply.

    A redraw is the old text with cells skipped (letters missing) and maybe a
    little other screen mixed in: nearly all of its n-grams were sent before
    and anything new comes in short runs (control-code residue, a spinner).
    A real follow-up says something new, so it has a long new run even when it
    repeats the earlier closing line. Measured on the captured redraws: share
    0.81-1.0; real follow-ups: share under 0.5, new runs of 17+ characters."""
    c = re.sub(r"\s+", "", candidate or "")
    seen = set()
    for t in sent or ():
        t = re.sub(r"\s+", "", t or "")
        seen.update(t[i:i + n] for i in range(len(t) - n + 1))
    if len(c) < n or not seen:
        return False
    covered, hits = [False] * len(c), 0
    for i in range(len(c) - n + 1):
        if c[i:i + n] in seen:
            hits += 1
            covered[i:i + n] = [True] * n
    share = hits / (len(c) - n + 1)
    run = longest = 0
    for x in covered:
        run = 0 if x else run + 1
        longest = max(longest, run)
    return share >= 0.8 or (share >= 0.5 and longest < 8)
