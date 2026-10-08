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
"""
import re

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
