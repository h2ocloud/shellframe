"""A Telegram "Reply" to a push goes to whoever made the push, not to the selected tab.

Before: bridge_telegram ignored `reply_to_message`, so answering a scheduled script's report
(or a tab's message from a while ago) landed in whatever tab the person had open. Now:

  * the origin of the replied-to message comes from tg_origins (a registry of what ShellBot
    and `sfctl tg-push` sent), else from the person's rules file (a push nobody registered);
  * a tab's message → that tab; a schedule's push → the bot that `owners` in schedules.json
    names for it; a bot or tab that sleeps is woken (wake_and_replay) and the
    message goes in once it is up;
  * no owner, or an origin nobody knows → "who gets this?": the bots as buttons, and
    "the current tab"; a message held for ten minutes;
  * what the tab reads says what it is answering ("a reply to schedule X's push", the
    original's first 300 characters, then the person's words), and Telegram says one line,
    "→ 已交給 <name>";
  * one-off: the selected tab does not change. The target's answer reaches the person
    through a short-lived guest entry (guest_chats, read by the bridge's _target_chats_for).

bridge_telegram.py calls `intercept` once in _handle_update (after the allowlist, commands,
voice and media, so a held or woken message carries the text and files already read) and
`on_callback` for the buttons; anything that goes wrong falls back to the old flow.
`tg_push` is the app's side of `sfctl tg-push`. No import of main; bridge_home is imported
lazily (it imports tg_origins, not this).
"""

import threading
import time

import sched_view
import sf_config
import sf_lang
import tg_origins

STOP = object()                  # the message was handled here (held, woken, refused): nothing more to do
HELD_TTL_S = 600.0
GUEST_TTL_S = 3600.0
QUOTE_LEN = 300
BUTTONS_MAX = 10
PREFIX = "rr:"
PUSH_BUDGET_S = 30.0
PUSH_CALL_S = 8.0

_lock = threading.Lock()
_held = {}                       # token -> {"update", "user_id", "chat_id", "options", "at"}
_seq = [0]
_guests = {}                     # sid -> {chat_id: expires}


def reset():
    with _lock:
        _held.clear()
        _guests.clear()


def _bh():
    import bridge_home
    return bridge_home


def _log(msg):
    try:
        import bridge_telegram
        bridge_telegram._blog(msg)
    except Exception:
        pass


# ── recording, and waking a bot for a reply ───────────────────────────────

def note_tab(bridge, sid, chat_id, text, resp):
    """A message a tab caused went to a chat: a reply to it goes back to that tab (tg_origins).
    The tab's bot is looked up on the writer thread, not on the reply loop. Never raises:
    a send that Telegram took must not look failed because the recording did."""
    try:
        tg_origins.note(resp, chat_id, text, lambda: tg_origins.tab_origin(sid, _bh().role_of(bridge, sid)))
    except Exception as e:
        _log(f"[reply-route] note failed: {type(e).__name__}: {e}\n")


def wake_and_replay(bridge, role, label, chat_id, update):
    """Wake a bot (if it is not being woken already) and put `update` back in the bridge's
    queue once its tab is up: bridge_home's wake, shared with a message for the primary bot."""
    bh = _bh()
    with bh._lock:
        waiting = bh._waking.get(role)
        if waiting is not None:
            waiting.append(update)
            return
        bh._waking[role] = [update]
    threading.Thread(target=bh._wake, args=(bridge, role, label, chat_id), daemon=True,
                     name="tg-wake-reply").start()


# ── guests: where a routed tab's answer goes ──────────────────────────────

def add_guest(sid, chat_id):
    with _lock:
        _guests.setdefault(sid, {})[chat_id] = time.time() + GUEST_TTL_S


def guest_chats(sid) -> set:
    """The chats a one-off reply sent to this tab: they get its answers for a while, though
    the tab is not their selected one."""
    if not _guests:
        return set()
    now = time.time()
    with _lock:
        live = {c: t for c, t in (_guests.get(sid) or {}).items() if t > now}
        if live:
            _guests[sid] = live
        else:
            _guests.pop(sid, None)
    return set(live)


# ── who the replied-to message belongs to ─────────────────────────────────

def _from_bot(bridge, reply: dict) -> bool:
    """The replied-to message was sent by this bot (not by the person)."""
    who = reply.get("from") or {}
    if not who.get("is_bot"):
        return False
    mine = (getattr(bridge, "bot_info", None) or {}).get("id")
    return not (mine and who.get("id") and who["id"] != mine)


def _quote(reply: dict) -> str:
    q = str(reply.get("text") or reply.get("caption") or "").strip()
    return q if len(q) <= QUOTE_LEN else q[:QUOTE_LEN] + "…"


def _roster(bridge) -> list:
    """The bots that can be given a message: [{"role", "label", "state": awake|asleep, "sid"}]."""
    api = getattr(bridge, "home_api", None)
    try:
        if not api or not api._bots_enabled():
            return []
        res = api._bots_list_for_sfctl() or {}
    except Exception:
        return []
    return [b for b in (res.get("details") or {}).get("bots") or [] if b.get("state") in ("awake", "asleep")]


def owner_of(schedule_id: str) -> str:
    """The bot (id or name) schedules.json names for a schedule: its own entry, else its
    group's. "" when nobody."""
    cfg, _err = sched_view.read_config(sf_config.config_dir() / sched_view.CONFIG_NAME)
    owners = cfg.get("owners") or {}
    sid = str(schedule_id)
    if owners.get(sid):
        return owners[sid]
    for gid, labels in (cfg.get("groups") or {}).items():
        if sid in labels and (owners.get(f"group:{gid}") or owners.get(gid)):
            return owners.get(f"group:{gid}") or owners.get(gid)
    return owners.get(f"group:{sid}", "")


def _awake_sid(bridge, role: str) -> str:
    bh = _bh()
    return next((s for s in list(bridge._slot_order) if s in bridge.slots and bh.role_of(bridge, s) == role), "")


def _role_target(bridge, role: str) -> dict:
    sid = _awake_sid(bridge, role)
    return {"sid": sid, "role": role} if sid else {"role": role}


def _name_of(bridge, target: dict) -> str:
    role = target.get("role") or ""
    if role:
        row = next((b for b in _roster(bridge) if b.get("role") == role), None)
        return str((row or {}).get("label") or role)
    slot = bridge.slots.get(target.get("sid", ""))
    return str(slot.label if slot else target.get("sid", ""))


def _resolve(bridge, chat_id, reply: dict):
    """(origin | None, target | None, head): where the reply should go. The target is
    {"sid"} for a tab, {"role"} for a bot (with "sid" when it is awake), None when nobody is known."""
    origin = tg_origins.lookup(chat_id, reply.get("message_id")) or tg_origins.match_rule(reply.get("text") or reply.get("caption"))
    if not origin:
        return None, None, sf_lang.pick("a reply to a push", "這是對一則推播的回覆")
    api = getattr(bridge, "home_api", None)
    kind = origin.get("kind")
    if kind == "tab":
        head = sf_lang.pick("a reply to this tab's earlier message", "這是對這個分頁先前訊息的回覆")
        sid, bot = origin.get("sid", ""), origin.get("bot", "")
        if sid in bridge.slots and (not bot or _bh().role_of(bridge, sid) == bot):
            return origin, {"sid": sid}, head
        return origin, (_named_target(bridge, api, bot) if bot else None), head      # closed, or the id was reused: its bot, if it still is one
    if kind == "schedule":
        head = sf_lang.pick(f"a reply to schedule {origin.get('id')}'s push", f"這是對排程 {origin.get('id')} 推播的回覆")
        owner = owner_of(origin.get("id", ""))
    else:
        head = sf_lang.pick("a reply to a push", "這是對一則推播的回覆")
        owner = origin.get("bot", "")
    return origin, (_named_target(bridge, api, owner) if owner else None), head


def _named_target(bridge, api, name: str):
    """A bot (or tab) by the name `owners` or a rule gives; None when unknown, outside ShellBot or bots are off."""
    try:
        res = api._resolve_name(name) if api else {}
    except Exception:
        return None
    if not res.get("ok"):
        return None
    role = res.get("role") or ""
    if role:
        return _role_target(bridge, role) if any(b.get("role") == role for b in _roster(bridge)) else None
    sid = res.get("sid") or ""
    return {"sid": sid} if sid in bridge.slots else None


# ── delivery ──────────────────────────────────────────────────────────────

def _ack(name: str, waking: bool = False) -> str:
    return sf_lang.pick(f"→ Handed to {name}" + (" (waking it; it goes in once it is up)" if waking else ""),
                        f"→ 已交給 {name}" + ("（叫醒中，醒來後送入）" if waking else ""))


def _say(bridge, chat_id, text):
    _bh()._tg(bridge, "sendMessage", {"chat_id": chat_id, "text": text})


def _park(msg: dict, text: str, file_paths, route: dict = None) -> dict:
    """The message as the bridge handles it later: what was read from it (text, downloaded
    files) is in it, so a held or woken message is not downloaded or transcribed twice."""
    m = {"text": text, "from": msg.get("from") or {}, "chat": msg.get("chat") or {},
         "message_id": msg.get("message_id"), "_sf_files": list(file_paths or [])}
    if route:
        m["_sf_route"] = route
    return {"message": m}


def _route(target: dict, kind: str, head: str, quote: str) -> dict:
    r = {"kind": kind, "head": head, "quote": quote}
    r.update({k: v for k, v in target.items() if v})
    return r


def wrap(bridge, route: dict, text: str) -> str:
    """What the tab reads: whom the person answers, the original, then their words. A bare
    digit for a tab that shows a numbered menu goes in as it is (it answers the menu)."""
    slot = bridge.slots.get(route.get("sid", ""))
    t = str(text or "").strip()
    if slot is not None and getattr(slot, "pending_menu", False) and t.isdigit() and 1 <= int(t) <= 9:
        return t
    lines = [sf_lang.pick(f"(Telegram: {route.get('head', '')})", f"（Telegram：{route.get('head', '')}）")]
    if route.get("quote"):
        label = (sf_lang.pick("Original message: ", "原訊息：") if route.get("kind") == "tab"
                 else sf_lang.pick("Original push: ", "原推播："))
        lines.append(label + sf_lang.pick(f'"{route["quote"]}"', f"「{route['quote']}」"))
    lines.append(sf_lang.pick("The person's reply: ", "使用者的回覆：") + (t or sf_lang.pick("(attachments only)", "（只有附件）")))
    return "\n".join(lines)


def _arrive(bridge, route: dict, user_id, chat_id) -> dict:
    """The message goes into route["sid"]: its answers reach this chat too, though the tab
    is not the person's selected one. The selected tab is not touched."""
    if chat_id not in bridge._target_chats_for(route["sid"]) - guest_chats(route["sid"]):
        add_guest(route["sid"], chat_id)                # not already one of its recipients: its answers come here for a while
    return route


def _finish(bridge, route: dict, user_id, chat_id):
    """Second pass: a parked message with its route. Finds the tab (a bot's tab by its role,
    now that it is awake) or says it is gone."""
    sid = route.get("sid") or ""
    if sid not in bridge.slots and route.get("role"):
        sid = _awake_sid(bridge, route["role"])
    if sid not in bridge.slots:
        _say(bridge, chat_id, sf_lang.pick("⚠ That tab is gone; nothing was sent.", "⚠ 那個分頁已經不在了，訊息沒有送出。"))
        return STOP
    return _arrive(bridge, dict(route, sid=sid), user_id, chat_id)


def intercept(bridge, update: dict, msg: dict, user_id, chat_id, text: str, file_paths):
    """None: the old flow (the active tab). STOP: handled here. A route dict: deliver there
    (route["sid"]), wrap the text with `wrap`. Any failure is the old flow."""
    try:
        return _intercept(bridge, update, msg, user_id, chat_id, text, file_paths)
    except Exception as e:
        _log(f"[reply-route] failed, old flow: {type(e).__name__}: {e}\n")
        return None


def _intercept(bridge, update, msg, user_id, chat_id, text, file_paths):
    if isinstance(msg.get("_sf_route"), dict):
        return _finish(bridge, msg["_sf_route"], user_id, chat_id)
    reply = msg.get("reply_to_message")
    if not isinstance(reply, dict) or str(text or "").startswith("/") or not _from_bot(bridge, reply):
        return None
    origin, target, head = _resolve(bridge, chat_id, reply)
    kind = (origin or {}).get("kind", "")
    quote = _quote(reply)
    active = bridge.get_active_sid(user_id)
    if target is None:
        return _ask(bridge, msg, user_id, chat_id, text, file_paths, kind, head, quote, active)
    if target.get("sid") and target["sid"] == active and kind == "tab":
        return None                                    # a reply to the tab it is already going to
    route = _route(target, kind, head, quote)
    name = _name_of(bridge, target)
    if route.get("sid"):
        if route["sid"] != active:
            _say(bridge, chat_id, _ack(name))
        return _arrive(bridge, route, user_id, chat_id)
    _say(bridge, chat_id, _ack(name, waking=True))
    wake_and_replay(bridge, route["role"], name, chat_id, _park(msg, text, file_paths, route))
    return STOP


def _ask(bridge, msg, user_id, chat_id, text, file_paths, kind, head, quote, active):
    """Nobody is known: hold the message and ask whom it is for."""
    bots = _roster(bridge)[:BUTTONS_MAX]
    if not bots:                                       # nobody to choose among: the current tab, with the context
        if not active:
            return None
        route = _route({"sid": active}, kind, head, quote)
        _say(bridge, chat_id, _ack(_name_of(bridge, {"sid": active})))
        return _arrive(bridge, route, user_id, chat_id)
    options = [{"role": b["role"], "label": str(b.get("label") or b["role"])} for b in bots]
    options.append({"current": True, "label": sf_lang.pick("the current tab", "目前分頁")})
    with _lock:
        _seq[0] += 1
        token = str(_seq[0])
        _held[token] = {"update": _park(msg, text, file_paths), "user_id": user_id, "chat_id": chat_id,
                        "options": options, "kind": kind, "head": head, "quote": quote, "at": time.time()}
    rows = []
    for i, o in enumerate(options):
        sleeping = next((b for b in bots if b["role"] == o.get("role")), {}).get("state") == "asleep"
        mark = "💤 " if sleeping else ("➡ " if o.get("current") else "🤖 ")
        rows.append([{"text": (mark + o["label"])[:48], "callback_data": f"{PREFIX}{token}:{i}"}])
    asked = sf_lang.pick("Who gets this?", "這則要交給誰？") + (f"\n「{quote[:40]}{'…' if len(quote) > 40 else ''}」" if quote else "")
    _bh()._tg(bridge, "sendMessage", {"chat_id": chat_id, "text": asked, "reply_markup": {"inline_keyboard": rows}})
    return STOP


def _expire():
    now = time.time()
    with _lock:
        for t in [t for t, h in _held.items() if now - h["at"] > HELD_TTL_S]:
            del _held[t]


def on_callback(bridge, data: str, chat_id, message_id, cq_id) -> bool:
    """A tap on the "who gets this?" buttons. True when the data was ours."""
    if not str(data or "").startswith(PREFIX):
        return False
    bh = _bh()
    _expire()
    try:
        token, idx = data[len(PREFIX):].split(":", 1)
        with _lock:
            held = _held.pop(token, None)
        option = held["options"][int(idx)] if held else None
    except (ValueError, IndexError, KeyError):
        held = option = None
    if not held or not option:
        bh._tg(bridge, "answerCallbackQuery", {"callback_query_id": cq_id, "text": sf_lang.pick(
            "Too old: reply to the push again", "這則已過期，請再回覆一次那則推播")})
        bh._tg(bridge, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": sf_lang.pick(
            "⌛ Expired: nothing was sent.", "⌛ 已過期，訊息沒有送出。")})
        return True
    bh._tg(bridge, "answerCallbackQuery", {"callback_query_id": cq_id})
    user_id = held["user_id"]
    if option.get("current"):
        sid = bridge.get_active_sid(user_id)
        target = {"sid": sid} if sid else None
    else:
        target = _role_target(bridge, option["role"])
    if not target:
        bh._tg(bridge, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": sf_lang.pick(
            "⚠ No tab is open; nothing was sent.", "⚠ 沒有開著的分頁，訊息沒有送出。")})
        return True
    route = _route(target, held["kind"], held["head"], held["quote"])
    name = option["label"] if not option.get("current") else _name_of(bridge, target)
    awake = bool(route.get("sid"))
    bh._tg(bridge, "editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": _ack(name, waking=not awake)})
    upd = held["update"]
    upd["message"]["_sf_route"] = route
    if awake:
        bridge._update_queue.put(upd)
    else:
        wake_and_replay(bridge, route["role"], name, chat_id, upd)
    return True


# ── sfctl tg-push, the app's side ─────────────────────────────────────────

def tg_push(api, text: str, schedule: str, chat: str = "", preview: bool = False) -> dict:
    """Send a script's message to the person's chat and register it as that schedule's. No
    rate limit and no quiet hours (that is `tg-send`'s, for agents): the script is the person's own."""
    bridge = getattr(api, "bridge", None)
    text, schedule = (text or "").strip(), (schedule or "").strip()
    if not text or not schedule:
        return {"success": False, "message": sf_lang.pick("Needs a text and --from", "需要內容和 --from")}
    if not bridge or not getattr(bridge, "active", False):
        return {"success": False, "message": sf_lang.pick("Telegram is not connected", "Telegram 沒有連線")}
    bh = _bh()
    chats = sorted(bh._chats(bridge))
    if chat:
        try:
            want = int(chat)
        except ValueError:
            want = None
        chats = [c for c in chats if c == want]
        if not chats:
            return {"success": False, "message": sf_lang.pick("That chat is not one ShellBot talks to", "ShellBot 沒有這個對話")}
    parts = tg_origins.split_for_telegram(text)
    end = time.monotonic() + PUSH_BUDGET_S
    sent_parts, full = 0, 0
    for chat_id in chats:
        n = 0
        for part in parts:
            if time.monotonic() > end:
                break
            body = {"chat_id": chat_id, "text": part}
            if not preview:
                body["disable_web_page_preview"] = True          # as the scripts this replaces sent it; `--preview` allows one
            r = bh._tg(bridge, "sendMessage", body, timeout=PUSH_CALL_S) or {}
            if not r.get("ok"):
                break
            tg_origins.note(r, chat_id, part, tg_origins.schedule_origin(schedule))
            n += 1
        sent_parts += n
        full += 1 if n == len(parts) else 0
    if full:
        return {"success": True, "message": sf_lang.pick(f"Sent to {full} chat(s)", f"已送到 {full} 個對話"),
                "details": {"sent": sent_parts, "chats": full}}
    return {"success": False, "message": sf_lang.pick("Telegram did not take it", "Telegram 沒有收下"),
            "details": {"sent": sent_parts}}
