"""The primary bot's place in Telegram (bots on, one shared Telegram bot).

Before: a Telegram user who had not picked a tab landed on the desktop's
selected tab or the first one, so "talk to the primary bot from the phone"
depended on remembering its /N. A bot that needed a decision in a turn not
started from Telegram (an A2A result, the desktop) reached the phone not at
all: the numbered-menu push only ran for Telegram turns. And binding a phone
meant typing a numeric user id, with an empty allowlist meaning anyone.

  * routing: the user's own /N choice, else the primary bot (woken if it
    sleeps, the message handled once its tab is ready), else the old fallbacks;
    /home or /0 goes back to it;
  * decisions: a bot's [[SF:RED]] in any turn puts its question on every bound
    phone, numbered options as buttons that answer that bot;
  * `sfctl tg-send`: the primary bot's milestones and results, rate-limited;
    during quiet hours only decisions are pushed;
  * pairing: a one-time code from the settings, `/start <code>` binds that
    person; binding into an empty allowlist locks it to them.

bridge_telegram.py only calls in here (it is at its size budget). The app
hangs itself on the bridge as `home_api`; without it everything is a no-op.
"""

import collections
import secrets
import threading
import time

import sf_lang

SEND = None                  # tests replace the Telegram call
RATE_PER_HOUR = 20
WAKE_WAIT_S = 90.0
PAIR_TTL_S = 600.0
DEFAULTS = {"default_primary": True, "quiet": {"on": False, "from": "23:00", "to": "08:00"}}

_lock = threading.Lock()
_sent = collections.deque()  # tg-send times, for the hourly limit
_waking = {}                 # role -> [updates to handle once it is awake]
_pair = {"code": "", "until": 0.0}
CACHE_S = 2.0                # the reply loop asks for every tab twice a second: do not reread config each time
_cache = {"at": 0.0, "api": None, "value": ("", "", "")}


def _api(bridge):
    return getattr(bridge, "home_api", None)


def _tg(bridge, method: str, payload: dict):
    if SEND:
        return SEND(method, payload)
    import bridge_telegram
    return bridge_telegram.tg_api(bridge.config.bot_token, method, payload)


def settings(api) -> dict:
    try:
        from api_host import main
        raw = (main.load_config().get("settings") or {}).get("tg_home") or {}
    except Exception:
        raw = {}
    quiet = dict(DEFAULTS["quiet"], **(raw.get("quiet") or {}))
    return {"default_primary": raw.get("default_primary", True) is not False, "quiet": quiet}


def _minutes(hhmm: str) -> int:
    h, _, m = str(hhmm or "0:0").partition(":")
    return (int(h or 0) % 24) * 60 + (int(m or 0) % 60)


def quiet_now(api, now: float = None) -> bool:
    """Inside the quiet hours (off by default); a range may run past midnight."""
    q = settings(api)["quiet"]
    if not q.get("on"):
        return False
    t = time.localtime(now or time.time())
    cur, a, b = t.tm_hour * 60 + t.tm_min, _minutes(q.get("from")), _minutes(q.get("to"))
    return a <= cur < b if a <= b else (cur >= a or cur < b)


def _primary(bridge):
    api = _api(bridge)
    if not api:
        return "", "", ""
    now = time.monotonic()
    with _lock:
        if _cache["api"] is api and now - _cache["at"] < CACHE_S:
            return _cache["value"]
    try:
        value = api._bots_primary_tab() if settings(api)["default_primary"] else ("", "", "")
    except Exception:
        value = ("", "", "")
    with _lock:
        _cache.update(at=now, api=api, value=value)
    return value


def primary_slot(bridge) -> str:
    """The primary bot's tab when it is open and on Telegram, else ''."""
    _role, sid, _label = _primary(bridge)
    return sid if sid and sid in bridge.slots else ""


def home_slot(bridge) -> str:
    """Where a user who has not picked a tab lands: the primary bot, else the first tab."""
    return primary_slot(bridge) or (bridge._slot_order[0] if bridge._slot_order else "")


def is_bot_slot(bridge, slot) -> bool:
    api = _api(bridge)
    try:
        return bool(api and api._bots_role_of(slot.sid))
    except Exception:
        return False


def _picked(bridge, user_id) -> bool:
    sid = bridge._user_active.get(user_id)
    return bool(sid and sid in bridge.slots)


def wake_for(bridge, user_id, chat_id, update) -> bool:
    """A message from a user who has not picked a tab while the primary bot
    sleeps: wake it, and handle the message again once its tab is ready."""
    if _picked(bridge, user_id):
        return False
    role, sid, label = _primary(bridge)
    if not role or sid:
        return False
    with _lock:
        waiting = _waking.get(role)
        if waiting is not None:
            waiting.append(update)
            return True
        _waking[role] = [update]
    _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(
        f"⏳ Waking ⭐ {label} (10–20 s); your message goes in once it is up.",
        f"⏳ 正在叫醒 ⭐ {label}（約 10–20 秒），醒來後就把你的訊息送進去。")})
    threading.Thread(target=_wake, args=(bridge, role, label, chat_id), daemon=True,
                     name="tg-wake-primary").start()
    return True


def _wake(bridge, role, label, chat_id):
    api, sid = _api(bridge), ""
    try:
        import json
        res = json.loads(api.bots_open(role))
        sid = (res.get("details") or {}).get("sid") or ""
        end = time.time() + WAKE_WAIT_S
        while sid and time.time() < end and not (sid in bridge.slots and api.is_session_ready_for_bridge(sid)):
            time.sleep(0.5)
    except Exception:
        sid = ""
    with _lock:
        updates = _waking.pop(role, [])
        _cache["at"] = 0.0           # it is awake now: the next look must not see the cached "asleep"
    if sid and sid in bridge.slots:
        for u in updates:
            bridge._update_queue.put(u)
        return
    _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(
        f"⚠ Could not wake ⭐ {label}; nothing was sent. /list shows the open tabs.",
        f"⚠ 叫不醒 ⭐ {label}，訊息沒有送出。/list 可以看開著的分頁。")})


def handle_command(bridge, cmd: str, user_id, chat_id) -> bool:
    """/home and /0: back to the primary bot (woken if it sleeps)."""
    if cmd not in ("home", "0"):
        return False
    role, sid, label = _primary(bridge)
    if not role:
        _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(
            "There is no primary bot. Set one in ShellBot (drag a bot onto the card).",
            "還沒有主要 Bot。請在 ShellBot 把一個 bot 拖到最上面的卡片。")})
        return True
    bridge._user_chat[user_id] = chat_id
    if sid and sid in bridge.slots:
        bridge._user_active.pop(user_id, None)      # no pick of its own: the primary is home
        _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(
            f"Back to ⭐ {label}.", f"回到 ⭐ {label}。")})
        return True
    bridge._user_active.pop(user_id, None)
    with _lock:
        if role not in _waking:
            _waking[role] = []
            threading.Thread(target=_wake, args=(bridge, role, label, chat_id), daemon=True,
                             name="tg-wake-primary").start()
    _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(
        f"⏳ Waking ⭐ {label}…", f"⏳ 正在叫醒 ⭐ {label}…")})
    return True


def _chats(bridge) -> set:
    return set(bridge._user_chat.values()) | set(bridge.config.allowed_users or [])


def push_decision(bridge, slot, reason: str = ""):
    """A bot raised [[SF:RED]] in a turn not started from Telegram: its question
    goes to every bound phone, numbered options as buttons answering that bot.
    Decisions go out during quiet hours too. Plain tabs keep their old behaviour."""
    api = _api(bridge)
    if not api:
        return
    try:
        role = api._bots_role_of(slot.sid)
        primary = api._bots_primary_tab()[0]
    except Exception:
        return
    if not role:
        return
    head = sf_lang.pick(f"{'⭐' if role == primary else '🤖'} {slot.label} needs your decision",
                        f"{'⭐' if role == primary else '🤖'} {slot.label} 需要你決定")
    menu = bridge._detect_menu_prompt(slot)
    for chat_id in _chats(bridge):
        try:
            if menu:
                slot.pending_menu = True
                bridge._send_choice_menu(chat_id, slot, f"{head}\n{menu}")
            else:
                _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": head + (f"：{reason}" if reason else "")})
        except Exception:
            pass


def tg_send(api, text: str, from_sid: str) -> dict:
    """`sfctl tg-send`: the primary bot tells the phone (milestones, results)."""
    bridge = getattr(api, "bridge", None)
    text = (text or "").strip()
    if not text:
        return {"success": False, "message": sf_lang.pick("Nothing to send", "沒有內容")}
    if not bridge or not getattr(bridge, "active", False):
        return {"success": False, "message": sf_lang.pick("Telegram is not connected", "Telegram 沒有連線")}
    role, sid, label = api._bots_primary_tab()
    if not role or not from_sid or from_sid != sid:
        return {"success": False, "message": sf_lang.pick(
            "Only the primary bot's tab can use tg-send", "只有主要 Bot 的分頁可以用 tg-send")}
    if quiet_now(api):
        return {"success": False, "message": sf_lang.pick(
            "Quiet hours: only decisions go to the phone now; this was not sent",
            "勿擾時段只推「要你決定」的事，這則沒有送出")}
    now = time.time()
    with _lock:
        while _sent and now - _sent[0] > 3600:
            _sent.popleft()
        if len(_sent) >= RATE_PER_HOUR:
            return {"success": False, "message": sf_lang.pick(
                f"Limit of {RATE_PER_HOUR} per hour reached", f"每小時最多 {RATE_PER_HOUR} 則，已經到上限")}
        _sent.append(now)
    sent = 0
    for chat_id in _chats(bridge):
        r = _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": f"⭐ {label}：{text[:3900]}"}) or {}
        sent += 1 if r.get("ok", True) else 0
    return {"success": sent > 0, "message": sf_lang.pick(f"Sent to {sent} chat(s)", f"已送到 {sent} 個對話"),
            "details": {"chats": sent}}


# ── pairing: a code from the settings, `/start <code>` on the phone ─────────
def pair_start(ttl: float = PAIR_TTL_S) -> dict:
    code = secrets.token_hex(4)
    with _lock:
        _pair.update(code=code, until=time.time() + ttl)
    return {"code": code, "expires_in": int(ttl)}


def pairing() -> dict:
    with _lock:
        return dict(_pair) if _pair["code"] and time.time() < _pair["until"] else {}


def try_pair(bridge, user_id, chat_id, text: str) -> bool:
    """`/start <code>` with the live code binds this person, before the allowlist
    check (they are not on it yet). The code works once. Anything else is
    handled as before."""
    parts = (text or "").split()
    if len(parts) != 2 or parts[0].split("@")[0] != "/start":
        return False
    with _lock:
        if not _pair["code"] or time.time() >= _pair["until"] or parts[1] != _pair["code"]:
            return False
        _pair.update(code="", until=0.0)
    api = _api(bridge)
    try:
        api._tg_bind_user(int(user_id))
    except Exception:
        return False
    bridge._user_chat[user_id] = chat_id
    threading.Thread(target=bridge._set_bot_commands, daemon=True).start()
    label = _primary(bridge)[2]
    _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(
        "✅ Bound. " + (f"Your messages now go to ⭐ {label}." if label else "Use /list to pick a tab."),
        "✅ 已綁定。" + (f"現在傳訊息會直接給 ⭐ {label}。" if label else "用 /list 選一個分頁。"))})
    return True
