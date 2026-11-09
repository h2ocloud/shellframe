"""A Telegram message for a busy tab waits its turn, up to 30 minutes, with a
"⚡ go in now" button.

Before: the bridge waited at most 2 minutes for the tab's turn to end and then
typed the message in anyway, cutting the turn off. With the primary bot as the
phone's default, and the primary bot busy most of the time, that became the
common case. Decided: wait up to 30 minutes like A2A (tab_delivery), let the
person on the phone choose to cut in, apply to every tab.

Busy means: the screen shows a turn running (the bridge's own signal), the
hooks say the agent is working, or the user's unsent text sits in the input box
(it would be wiped by the Ctrl-U before the paste). A decision on screen does
not hold a message back: answering it with a digit has to get through.

bridge_telegram.py calls `wait_turn` inside its per-tab write lock, so a tab's
Telegram messages still go in one at a time, in order.
"""

import secrets
import threading
import time

import sf_lang

MAX_WAIT = 1800.0            # seconds, as tab_delivery.QUEUE_MAX_WAIT
NOTIFY_AFTER = 8.0           # silence this long reads as "not received", so say it is queued
POLL = 0.5

_lock = threading.Lock()
_now = {}                    # token -> Event set by the ⚡ button


def _send(bridge, method, payload):
    import bridge_telegram
    threading.Thread(target=bridge_telegram.tg_api, args=(bridge.config.bot_token, method, payload),
                     kwargs={"timeout": 5}, daemon=True).start()   # never wait on Telegram under the lock


def busy(bridge, slot) -> bool:
    import bridge_telegram
    if bridge_telegram._TURN_BUSY_RE.search(bridge._live_tail(slot)):
        return True
    api = getattr(bridge, "home_api", None)
    if not api:
        return False
    try:
        if api._agent_state_for_list(slot.sid) == "working":
            return True
        import tab_delivery
        return tab_delivery.composer_state(tab_delivery.capture(api, slot.sid))[0] == "draft"
    except Exception:
        return False


def wait_turn(bridge, slot, chat_id, max_wait: float = None) -> str:
    """'ready' when the tab can take the message, 'now' when the person pressed ⚡,
    'expired' when it stayed busy past MAX_WAIT (the phone has been told)."""
    max_wait = MAX_WAIT if max_wait is None else max_wait
    token = secrets.token_hex(4)
    ev = threading.Event()
    with _lock:
        _now[token] = ev
    t0, told = time.time(), False
    try:
        while True:
            if ev.is_set():
                return "now"
            if not busy(bridge, slot):
                return "ready"
            waited = time.time() - t0
            if waited > max_wait:
                mins = int(max_wait // 60)
                _send(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(
                    f"⚠ \"{slot.label}\" stayed busy for {mins} min; this message did not go in. "
                    f"Send it again if it still matters (⚡ then cuts in).",
                    f"⚠ 「{slot.label}」忙了 {mins} 分鐘，這則沒有送進去，已放棄。還需要的話請再傳一次（可以按 ⚡ 直接插話）。")})
                return "expired"
            if not told and waited >= NOTIFY_AFTER:
                told = True
                _send(bridge, "sendMessage", {
                    "chat_id": chat_id,
                    "text": sf_lang.pick(
                        f"⏳ Got it. \"{slot.label}\" is busy; your message goes in when it is free "
                        f"(up to {int(max_wait // 60)} min).",
                        f"⏳ 已收到。「{slot.label}」正在忙，訊息會等它空下來再送（最多 {int(max_wait // 60)} 分鐘）。"),
                    "reply_markup": {"inline_keyboard": [[{
                        "text": sf_lang.pick("⚡ Go in now", "⚡ 立刻插話"), "callback_data": f"tgnow:{token}"}]]},
                })
            ev.wait(POLL)
    finally:
        with _lock:
            _now.pop(token, None)


def on_callback(bridge, data: str, chat_id, message_id) -> bool:
    """The ⚡ button: the waiting message goes in at once (it may cut the turn off)."""
    if not str(data or "").startswith("tgnow:"):
        return False
    with _lock:
        ev = _now.get(data[6:])
    _send(bridge, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": sf_lang.pick(
        "⚡ Going in now." if ev else "This message already went in (or was given up).",
        "⚡ 馬上送進去。" if ev else "這則已經送進去了（或已經放棄）。")})
    if ev:
        ev.set()
    return True
