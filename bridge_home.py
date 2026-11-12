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
import re
import secrets
import threading
import time

import agent_link
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
    if method == "sendMessage" and "disable_notification" not in payload:
        try:      # the phone rings for it already (sf_push.telegram_quiet: push working, quiet asked for)
            import sf_push
            if sf_push.telegram_quiet():
                payload = dict(payload, disable_notification=True)
        except Exception:
            pass
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


# ── /list, plan A: what each tab is, the bots asleep, the temporary ones to keep ──────
_LIST = {}                   # chat_id -> [("wake", role, label) | ("keep", sid, name)]: the last /list's buttons
LIST_MAX = 12                # buttons per kind: a long roster is still one screen


def _bots_on(api) -> bool:
    try:
        return bool(api) and api._bots_enabled()
    except Exception:
        return False


def kind_note(bridge, sid: str) -> str:
    """After a tab's name on /list: "（臨時）" for an AI tab nobody kept, "（終端機）" for a
    shell; nothing for a bot (its name says it) or with bots off."""
    api = _api(bridge)
    if not _bots_on(api):
        return ""
    try:
        s = api.sessions.get(sid)
        if s is None or api._bots_role_of(sid):
            return ""
        if not api._tab_cli(s):
            return sf_lang.pick(" (terminal)", "（終端機）")
        return sf_lang.pick(" (temporary)", "（臨時）")
    except Exception:
        return ""


def list_snippet(raw: str, limit: int = 220) -> str:
    """The line under a tab's name on /list: the first lines of the last thing on its screen as one plain
    line (agent_link.preview_of). A tab that has not answered yet and has just been sent a message by
    another agent shows that message ("研究：…"), not its [[A2A…]] header."""
    text = "\n".join([x for x in str(raw or "").split("\n") if x.strip()][:3])
    line = agent_link.preview_of(text, limit, agent=not agent_link.header_parts(text))
    return "   " + (line or "(no recent activity)")


def after_list(bridge, chat_id) -> bool:
    """After /list: the bots asleep (a Wake button each), the external ones, and the
    temporary bots (a Keep button each). Nothing with bots off. /N is not touched."""
    api = _api(bridge)
    if not _bots_on(api):
        return False
    try:
        view = api._execute_sfctl("bots_link", {}) or {}
    except Exception:
        return False
    asleep = [b for b in view.get("bots") or [] if b.get("state") == "asleep"][:LIST_MAX]
    outside = [b for b in view.get("bots") or [] if b.get("state") == "external"]
    temp = sorted(((sid, t) for sid, t in (view.get("tabs") or {}).items() if t.get("kind") == "temp" and not t.get("plain")),
                  key=lambda kv: kv[1].get("tg") or 999)[:LIST_MAX]
    if not (asleep or outside or temp):
        return False
    actions, rows, lines = [], [], []
    if asleep:
        lines.append(sf_lang.pick("Asleep: ", "休息中：") + "、".join(b["role"] for b in asleep))
        for b in asleep:
            actions.append(("wake", b["role"], b.get("label") or b["role"]))
            rows.append([{"text": sf_lang.pick(f"Wake {b['role']}", f"叫醒 {b['role']}"), "callback_data": f"bl:{len(actions) - 1}"}])
    if outside:
        lines.append(sf_lang.pick("Outside ShellBot: ", "在 ShellBot 外面：") + "、".join(b["role"] for b in outside)
                     + sf_lang.pick(" (sfctl a2a-send <name>)", "（用 sfctl a2a-send 名字 找它）"))
    if temp:
        lines.append(sf_lang.pick("Temporary (they forget when closed): ", "臨時 bot（關掉就忘了）：")
                     + "、".join((f"/{t['tg']} " if t.get("tg") else "") + str(t.get("name") or sid) for sid, t in temp))
        for sid, t in temp:
            actions.append(("keep", sid, str(t.get("name") or sid)))
            n = f"/{t['tg']} " if t.get("tg") else ""
            rows.append([{"text": sf_lang.pick(f"Keep {n}{t.get('name') or sid}", f"記住 {n}{t.get('name') or sid}"),
                          "callback_data": f"bl:{len(actions) - 1}"}])
    with _lock:
        _LIST[chat_id] = actions
    payload = {"chat_id": chat_id, "text": "\n".join(lines)}
    if rows:
        payload["reply_markup"] = {"inline_keyboard": rows}
    _tg(bridge, "sendMessage", payload)
    return True


def on_list_button(bridge, data: str, chat_id, cq_id) -> bool:
    """A tap on a button after_list sent: wake a bot, or keep a temporary one. True when it
    was one (and the tap is answered here)."""
    if not str(data or "").startswith("bl:"):
        return False
    with _lock:
        actions = list(_LIST.get(chat_id) or [])
    try:
        kind, key, name = actions[int(data.split(":", 1)[1])]
    except (ValueError, IndexError):
        _tg(bridge, "answerCallbackQuery", {"callback_query_id": cq_id, "text": sf_lang.pick(
            "That list is old: send /list again", "這份清單舊了，請再送一次 /list")})
        return True
    _tg(bridge, "answerCallbackQuery", {"callback_query_id": cq_id})
    api = _api(bridge)
    if kind == "wake":
        with _lock:
            fresh = key not in _waking
            if fresh:
                _waking[key] = []
        if fresh:
            threading.Thread(target=_wake, args=(bridge, key, name, chat_id), daemon=True, name="tg-wake").start()
        _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": sf_lang.pick(f"⏳ Waking {name}…", f"⏳ 正在叫醒 {name}…")})
        return True
    res = api._bots_keep(key) if api else {"success": False, "message": "no app"}
    _tg(bridge, "sendMessage", {"chat_id": chat_id, "text": (
        sf_lang.pick(f"✅ Kept {res.get('role')}: it remembers from now on.", f"✅ 已記住「{res.get('role')}」：從現在起它會記得。")
        + sf_lang.pick(" Its job, CLI, model and skills are empty for now: add them in ShellBot on the computer (Edit on its row in the sidebar, "
                       "or Settings → Bots), or in the phone app.",
                       " 執掌、CLI、模型、skill 先空著：到電腦上的 ShellBot 補上（側欄那一列的「編輯」，或 設定 → Bots），或用手機 App。")
        if res.get("success") else "⚠ " + str(res.get("message") or ""))})
    return True


def _chats(bridge) -> set:
    return set(bridge._user_chat.values()) | set(bridge.config.allowed_users or [])


_RED_LINE = re.compile(r"^[\s>❯›⏺•*\-]*\[\[\s*SF\s*:\s*RED\s*(?::[^\]]*)?\]\]\s*$", re.I)
_OPTION = re.compile(r"^\s*(\d)[.)、]\s+(.+?)\s*$")
_TG_TOKEN = re.compile(r"TG_REPLY_[0-9a-fA-F]+")
_pushed_at = {}              # sid -> when a decision was last pushed (the two paths below share it)
PUSH_DEDUP_S = 30.0
# Measured: the same "choice:s344:1" was tapped twice (once on the decision, once on an
# older copy while a newer question was out) and "1" went in twice. Every message with
# choice buttons counts once; a newer question for the tab retires the older copies.
_answered = collections.OrderedDict()   # (chat_id, message_id) -> the option chosen
_retired = collections.OrderedDict()    # (chat_id, message_id) of a question replaced by a newer one
_open = {}                              # sid -> {"text", "copies": [(chat_id, message_id)]}: its newest question
KEEP = 500
_red = {}                               # sid -> {"reason", "options", "at"}: the [[SF:RED]] its last turn ended on


def role_of(bridge, sid) -> str:
    api = _api(bridge)
    try:
        return api._bots_role_of(sid) if api else ""
    except Exception:
        return ""


def red_pending(sid):
    """The question a tab's last turn ended on with [[SF:RED]], with or without Telegram:
    {"reason", "options": [(num, text)…], "at"} or None. For the phone (/link/bots), which
    gets no RED at all from a computer without the Telegram bridge; a prompt sent to the
    tab after `at` answers it (the caller checks)."""
    with _lock:
        r = _red.get(sid)
    return dict(r) if r else None


def forget(sid):
    with _lock:
        _red.pop(sid, None)


# A question answered from one place must not be answered again from another: the second
# answer would go into whatever the tab asks next. Telegram taps and the phone's
# /link/answer both stamp the tab; for a moment after that the screen may still show the
# old question, so the phone may not answer it then.
_answered_at = {}                       # sid -> when its question was last answered, on Telegram or the phone
ANSWER_SETTLE = 3.0


def claim_for_phone(sid, n):
    """The phone wants to answer sid's question with option n. None when that question was
    answered a moment ago (on Telegram, or a second tap on the phone). Otherwise, in one
    step: the answer is stamped, the RED question is dropped, and the Telegram copies of the
    open question count as answered (a tap on them is refused); returns (their text, copies)
    for phone_answered to edit."""
    with _lock:
        if time.time() - _answered_at.get(sid, 0) < ANSWER_SETTLE:
            return None
        _answered_at[sid] = time.time()
        _red.pop(sid, None)
        cur = _open.pop(sid, None) or {"text": "", "copies": []}
        copies = [k for k in cur["copies"] if k not in _answered]
        for k in copies:
            _remember(_answered, k, str(n))
        return cur["text"], copies


def phone_answered(api, sid, n, claimed, where="phone"):
    """After the phone's answer went in (or the desktop chat view's, `where="desktop"`): its
    Telegram copies say so and lose their buttons."""
    text, copies = claimed
    bridge = getattr(api, "bridge", None)
    slot = (getattr(bridge, "slots", None) or {}).get(sid) if bridge else None
    if slot is not None:
        slot.pending_menu, slot.pending_menu_options = False, []
    note = (sf_lang.pick(f"✅ Answered on the computer: {n}", f"✅ 已在電腦上選：{n}") if where == "desktop"
            else sf_lang.pick(f"✅ Answered on the phone: {n}", f"✅ 已在手機上選：{n}"))
    for c, m in copies if bridge else ():
        try:      # editMessageText without reply_markup also takes the buttons away
            _tg(bridge, "editMessageText", {"chat_id": c, "message_id": m, "text": f"{text}\n\n{note}".strip()})
        except Exception:
            pass


def red_block(evs, telegram_too=False):
    """(question, [(num, text)…]) when the newest turn's last words raise [[SF:RED]]
    and the turn did not start from Telegram (the bridge pushes those itself; with
    `telegram_too` they count as well); else None. `evs`: agent_status transcript
    events, oldest first."""
    last_user = max((i for i, e in enumerate(evs or ()) if e.get("kind") == "user_msg"), default=-1)
    if not telegram_too and last_user >= 0 and _TG_TOKEN.search(evs[last_user].get("text") or ""):
        return None
    texts = [e.get("text") or "" for e in (evs or ())[last_user + 1:] if e.get("kind") == "assistant_text"]
    if not texts:
        return None
    lines = texts[-1].splitlines()
    at = next((i for i in range(len(lines) - 1, -1, -1) if _RED_LINE.match(lines[i])), -1)
    if at < 0:
        return None
    # What the person reads is the agent's words without ShellBot's markers (agent_link.preview_of): a Telegram reply
    # may open with its [[TG_REPLY_…]] on the question's own line, and close with its end marker on the last option's.
    question = next((q for q in (agent_link.preview_of(ln, 300, agent=True) for ln in reversed(lines[:at])   # the sentence, not a list item above it
                                 if not _OPTION.match(ln) and not ln.lstrip().startswith(("-", "•", "*"))) if q), "")
    options = [(m.group(1), agent_link.preview_of(m.group(2), 60, agent=True) or m.group(2)[:60])
               for m in (_OPTION.match(ln) for ln in lines[at + 1:]) if m][:9]
    return question, options


def _recently_pushed(sid) -> bool:
    with _lock:
        if time.time() - _pushed_at.get(sid, 0) < PUSH_DEDUP_S:
            return True
        _pushed_at[sid] = time.time()
        return False


def on_turn_end(api, sid: str, read_events=None):
    """A Claude Code turn just stopped (its Stop hook). A question it ended on with
    [[SF:RED]] is kept for the phone, Telegram or not (red_pending). When it is a
    bot's turn that did not start from Telegram, the question also goes to Telegram
    with its options as buttons. Measured: the bridge's own detection missed it
    while an earlier Telegram turn on that tab was still open in its books."""
    try:
        if read_events is None:
            import agent_status
            s = api.sessions.get(sid)
            path = agent_status.resolve_transcript(api._worker_ctx(sid, s)) if s else None
            evs = agent_status._read_tail_events(path)[1] if path else []
        else:
            evs = read_events(sid)
        seen = red_block(evs, telegram_too=True)
    except Exception:
        return
    # Called twice for one turn (the Stop hook, and the status monitor when a phone has push):
    # the same open question keeps its time, or its id (which hashes it) would change under the
    # phone and its lock-screen answer would be refused. Asked again after a prompt, it is new.
    prompt_at = float(((getattr(api, "_hook_events", None) or {}).get(sid) or {}).get("prompt_at") or 0)
    with _lock:
        old = _red.get(sid)
        if seen and old and (old["reason"], old["options"]) == (seen[0], seen[1]) and old["at"] >= prompt_at:
            pass
        elif seen:
            _red[sid] = {"reason": seen[0], "options": seen[1], "at": time.time()}
        else:
            _red.pop(sid, None)
    bridge = getattr(api, "bridge", None)
    if not seen or not bridge or not getattr(bridge, "active", False) or sid not in bridge.slots:
        return
    try:
        role = api._bots_role_of(sid)
    except Exception:
        return
    block = red_block(evs)
    if not role or not block or _recently_pushed(sid):
        return
    question, options = block
    slot = bridge.slots[sid]
    try:
        import bridge_telegram
        bridge_telegram._blog(f"[signal] {sid} state=RED (turn end) options={len(options)} → push\n")
    except Exception:
        pass
    primary = role == api._bots_primary_tab()[0]
    head = sf_lang.pick(f"{'⭐' if primary else '🤖'} {slot.label} needs your decision",
                        f"{'⭐' if primary else '🤖'} {slot.label} 需要你決定")
    text = "\n".join([head] + ([question] if question else []) + [f"{n}. {t}" for n, t in options])
    payload = {"text": text}
    if options:
        slot.pending_menu, slot.pending_menu_options = True, [{"num": n, "text": t} for n, t in options]
        payload["reply_markup"] = {"inline_keyboard": [[{"text": f"{n}. {t}"[:48], "callback_data": f"choice:{sid}:{n}"}]
                                                       for n, t in options]}
    for chat_id in _chats(bridge):
        try:
            opened(bridge, sid, text, chat_id, _tg(bridge, "sendMessage", dict(payload, chat_id=chat_id)))
        except Exception:
            pass


def _remember(table, key, value=True):
    table[key] = value
    while len(table) > KEEP:
        table.popitem(last=False)


def opened(bridge, sid, text, chat_id, result):
    """A question with choice buttons went to a chat (every path that sends one calls
    this with Telegram's reply). The same question to another chat is one more copy;
    a different one retires the older: its buttons go and a tap on them is refused,
    since it would be read as the answer to the new question."""
    mid = ((result or {}).get("result") or {}).get("message_id")
    old = []
    with _lock:
        cur = _open.get(sid)
        if cur and cur["text"] != text:
            old = [k for k in cur["copies"] if k not in _answered]
            for key in old:
                _remember(_retired, key)
            cur = None
        cur = cur or {"text": text, "copies": []}
        if mid:
            cur["copies"].append((chat_id, mid))
        _open[sid] = cur
    for c, m in old:
        _tg(bridge, "editMessageReplyMarkup", {"chat_id": c, "message_id": m, "reply_markup": {"inline_keyboard": []}})


def on_choice(bridge, data: str, chat_id, message_id, cq_id) -> bool:
    """A tap on a numbered-choice button (any path that sent one). True when it must
    not go in: that message was answered already, or a newer question replaced it.
    The first tap goes through, and the other copies of the question lose their
    buttons. Runs before the bridge acknowledges the tap, so the refusal shows."""
    if not str(data or "").startswith("choice:"):
        return False
    key = (chat_id, message_id)
    sid = data.split(":")[1] if data.count(":") >= 2 else ""
    with _lock:
        why = ("answered" if key in _answered else "retired" if key in _retired else "")
        if not why:
            _answered_at[sid] = time.time()          # the phone may not answer it too (claim_for_phone)
            _remember(_answered, key, data.rsplit(":", 1)[-1])
            cur = _open.get(sid) or {"copies": []}
            copies = [k for k in cur["copies"] if k != key] if key in cur["copies"] else []
            if key in cur["copies"]:
                _open.pop(sid, None)
            for k in copies:                 # a tap racing the removal of their buttons
                _remember(_answered, k, _answered[key])
    if why:
        _tg(bridge, "answerCallbackQuery", {"callback_query_id": cq_id, "text": sf_lang.pick(
            "Already answered" if why == "answered" else "A newer question replaced this one",
            "這題已經回答過了" if why == "answered" else "已經有新的題目，這題的按鈕不算了")})
        _tg(bridge, "editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                "reply_markup": {"inline_keyboard": []}})
        return True
    for c, m in copies:
        _tg(bridge, "editMessageReplyMarkup", {"chat_id": c, "message_id": m, "reply_markup": {"inline_keyboard": []}})
    return False


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
    if not role or _recently_pushed(slot.sid):
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


def announce_start(bridge):
    """Said once when a person connects the bot (the panel or Settings), without a
    sound, with the right count. Not on the app's own restarts or reloads: those put
    "Bridge connected … Sessions: none" on the phone every time (no tab is
    registered yet at that point) — reported as noise, and wrong."""
    if not getattr(bridge, "announce_start", True):
        return
    n, bot = len(bridge._slot_order), (getattr(bridge, "bot_info", None) or {}).get("username", "?")
    label = _primary(bridge)[2]
    text = sf_lang.pick(
        f"🔗 ShellBot connected (@{bot}), {n} tabs. "
        + (f"Write here to talk to ⭐ {label}; /home goes back to it, /list shows the tabs."
           if label else "/list shows the tabs, /1 /2 switch."),
        f"🔗 ShellBot 已連線（@{bot}），{n} 個分頁。"
        + (f"直接打字就是跟 ⭐ {label} 說話；/home 回到它，/list 看分頁。" if label else "/list 看分頁，/1 /2 切換。"))
    for uid in (bridge.config.allowed_users or []):
        try:
            _tg(bridge, "sendMessage", {"chat_id": uid, "text": text, "disable_notification": True})
        except Exception:
            pass


# ── who a bound user is: @username, else their name, with the id ────────────
_names = {}                  # user id -> (label, fetched_at)
NAME_RETRY_S = 60.0          # a failed lookup is tried again after this


def user_label(bridge, user_id) -> str:
    """'@username', or 'First Last · <id>', or the bare id when Telegram cannot say."""
    uid = int(user_id)
    hit = _names.get(uid)
    if hit and (hit[0] != str(uid) or time.time() - hit[1] < NAME_RETRY_S):
        return hit[0]
    label = str(uid)
    if bridge is not None:
        try:
            r = _tg(bridge, "getChat", {"chat_id": uid}) or {}
            c = r.get("result") or {}
            name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
            label = f"@{c['username']}" if c.get("username") else (f"{name} · {uid}" if name else str(uid))
        except Exception:
            pass
    _names[uid] = (label, time.time())
    return label


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
