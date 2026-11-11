"""Api mixin — bots (settings.experimental_bots). Rules live in bots.py.

js_api: bots_state, bots_set_avatar, bots_set_primary, bots_bind_tab, bots_save, bots_delete, bots_open,
        bots_close, bots_skill_catalog, bots_open_folder, bots_external_info, bots_external_send,
        bots_rename.
Hooks main.py and a2a_host call: _bots_first_prompt (delegate opened a bot's tab),
_bots_divert_close (close_session for a bot tab), _bots_rename_via_tab (rename_session
on a bot's tab renames the bot), _bots_external_target /
_bots_external_deliver / _bots_external_label (A2A to and from a bot that lives
outside ShellFrame).
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import agent_status
import bot_names
import bots
import bridge_home
import sf_lang
import launch_cmd
import sf_notify
import tab_delivery
from api_host import main
from sf_log import _dlog, _swallow

BUSY_WAIT_SEC = 600      # a bot still mid-turn gets this long to finish before it is asked to write
SUMMARY_WAIT_SEC = 180   # then this long to write its summary before the fallback capture
SELF_REASONS = ("bot-memory", "bot-skip")   # close_session reasons this module uses itself

_CLOSING = {}            # sid -> {"role", "phase", "since"}; one Api per process
_ = sf_lang.pick         # _(english, chinese): messages follow the OS language like the prompts
_CLOSING_LOCK = threading.Lock()


def _cfg() -> dict:
    return main.load_config()


def _update(mutator):
    return main.update_config(mutator)


DIALOG_WORDS_MAX = 2000  # a decision's `detail`; past this it is cut and ends in "…"


def dialog_words(lines: list, at: int) -> str:
    """The words of the dialog whose options start at lines[at], whole, as the CLI drew them:
    from the rule it opens with down to its question (the decision's `reason`), rules dropped,
    each line masked as agent_status.redact masks (credentials and any 32+ character token, a
    SHA say, become <redacted>; runs of spaces one). "" when no rule opens a dialog above the
    options: what a question is about is not guessed (Codex draws none); a dialog taller than
    the screen may start from another rule. The call a question is about is no substitute:
    agent_status keeps a command's first line, 40 characters."""
    rule = next((i for i in range(at - 1, -1, -1) if re.fullmatch(r"\s*─{10,}\s*", lines[i])), None)
    if rule is None:
        return ""
    body = [ln.rstrip() for ln in lines[rule + 1:at] if not re.fullmatch(r"\s*[─╌]+\s*", ln)]
    while body and not body[-1].strip():
        body.pop()
    body = body[:-1]                                    # the question
    pad = min((len(ln) - len(ln.lstrip()) for ln in body if ln.strip()), default=0)
    out = []
    for ln in body:
        if ln.strip():
            ln = ln[pad:]
            out.append(ln[:len(ln) - len(ln.lstrip())] + agent_status.redact(ln, 1000))
        elif out and out[-1]:
            out.append("")
    text = "\n".join(out).strip("\n")
    return text if len(text) <= DIALOG_WORDS_MAX else text[:DIALOG_WORDS_MAX].rstrip() + "…"


_CLIS = {"at": 0.0, "list": []}


def _cli_choices() -> list:
    """Every CLI the app can run, for a bot's CLI picker: installed or not, and whether a
    model can be pinned on its command line (agent_model). Cached a minute: phones ask for
    /link/bots every few seconds."""
    if _CLIS["list"] and time.time() - _CLIS["at"] < 60:
        return _CLIS["list"]
    import agent_model
    import usage_probe
    out = []
    for key, spec in usage_probe.PROVIDER_SPECS.items():
        try:
            installed = bool(usage_probe.provider_installed(key))
        except Exception:
            installed = False
        out.append({"key": key, "label": str(spec.get("label") or key), "installed": installed,
                    "models": key in agent_model.PROVIDERS})
    _CLIS.update(at=time.time(), list=out)
    return out


def _model_name(m) -> str:
    """The status monitor's model ({"name", "effort"} or a string) as a person reads it."""
    return str((m.get("name") if isinstance(m, dict) else m) or "")



OPTION_MAX = 120


def option_words(text, limit: int = OPTION_MAX) -> str:
    """An option as the phone, the chat view and Telegram's copies show it: credentials
    masked, one line, and a long one cut with "…" (an "always allow <folder>" option used to
    lose the end of its path with no sign of it)."""
    t = agent_status.redact(text, 100_000)
    return t if len(t) <= limit else t[:limit - 1] + "…"

class BotsApiMixin:
    # ── reading ─────────────────────────────────────────────────────────
    def _bots_enabled(self) -> bool:
        try:
            return (_cfg().get("settings", {}) or {}).get("experimental_bots", False) is True
        except Exception:
            return False

    def _bots_primary_tab(self, cfg: dict = None):
        """(role, sid, label) of the primary bot, sid '' while it sleeps; ('', '', '')
        when bots are off, there is none, or it lives outside ShellBot."""
        if not self._bots_enabled():
            return "", "", ""
        cfg = cfg or _cfg()
        raw = cfg.get("agent_roster") or {}
        role = bots.primary_role(raw)
        if not role or bots.is_external(raw.get(role)):
            return "", "", ""
        sid = next((s for s in list(self.sessions) if self._bots_role_of(s, cfg) == role), "")
        return role, sid, bots.label_of(role, raw[role])

    def _bots_role_of(self, sid: str, cfg: dict = None) -> str:
        """The bot a tab belongs to: its binding, else a tab whose title is a bot's name or
        label (bot_names.role_of_tab: the same rule A2A and delegate use)."""
        cfg = cfg or _cfg()
        return bot_names.role_of_tab(sid, cfg.get("agent_roster") or {}, self._name_tabs(),
                                     cfg.get("bots_sessions") or {},
                                     only=lambda r, e: bots.is_bot(e) and not bots.is_external(e))

    def _name_tabs(self) -> list:
        """The live tabs as bot_names takes them."""
        return [{"sid": sid, "title": getattr(s, "_custom_label", None) or ""}
                for sid, s in list(self.sessions.items()) if getattr(s, "alive", True)]

    def _resolve_name(self, name: str, cfg: dict = None, prefix: bool = True) -> dict:
        """A bot or a tab by any of its names (bot_names.resolve)."""
        cfg = cfg or _cfg()
        return bot_names.resolve(name, cfg.get("agent_roster") or {}, self._name_tabs(),
                                 cfg.get("bots_sessions") or {}, main.AGENT_ROLE_ALIASES, prefix=prefix)

    def _sid_for(self, name: str) -> tuple:
        """sfctl's tab commands given a name instead of a sid: (sid, "") or ("", why not)."""
        res = self._resolve_name(name)
        if not res.get("ok"):
            return "", bot_names.explain(res, name, zh=_("en", "zh") == "zh")
        if not res.get("sid"):
            role = res.get("role")
            return "", _(f'"{role}" is asleep: wake it first, e.g. sfctl a2a-send {role} "…"',
                         f"「{role}」在休息：先叫醒它，例如 sfctl a2a-send {role} \"…\"")
        return res["sid"], ""

    def bots_state(self) -> str:
        cfg = _cfg()
        raw = cfg.get("agent_roster") or {}
        bound = cfg.get("bots_sessions") or {}
        sessions = {}
        for sid in list(self.sessions):
            row = {}
            e = bound.get(sid) or {}
            if e.get("bot") in raw and bots.is_bot(raw.get(e.get("bot"))):
                row["bot"] = e["bot"]
            if bots.normalize_avatar(e.get("avatar")):
                row["avatar"] = e["avatar"]
            if e.get("plain"):
                row["plain"] = True
            with _CLOSING_LOCK:
                ph = _CLOSING.get(sid)
            if ph:
                row["closing"] = ph.get("phase") or "writing"
            if row:
                sessions[sid] = row
        return json.dumps({
            "enabled": (cfg.get("settings", {}) or {}).get("experimental_bots", False) is True,
            "roster": bots.public_roster(raw),
            "sessions": sessions,
            "kinds": {sid: ("temp" if self._tab_cli(s) else "terminal")      # plan A: what a tab is when no bot's
                      for sid, s in list(self.sessions.items())},
            "primary": bots.primary_role(raw),
            "memory_root": str(bots.memory_root()),
        }, ensure_ascii=False)

    def _bots_list_for_sfctl(self) -> dict:
        """`sfctl bots`: who is awake (and in which tab), asleep, or outside ShellFrame."""
        if not self._bots_enabled():
            return {"success": False, "message": _("Bots are experimental: turn them on in Settings first (experimental_bots)",
                                                  "Bots 是實驗功能，請先在設定裡打開（experimental_bots）")}
        cfg = _cfg()
        raw = cfg.get("agent_roster") or {}
        awake = {}
        order = self._ordered_sids(cfg)
        for sid in list(self.sessions):
            role = self._bots_role_of(sid, cfg)
            if role:
                awake.setdefault(role, sid)
        primary = bots.primary_role(raw)
        rows, lines = [], []
        for role, e in raw.items():
            if not bots.is_bot(e):
                continue
            where = ("external" if bots.is_external(e) else ("awake" if role in awake else "asleep"))
            sid = awake.get(role, "")
            doing, said = self._bots_live(sid) if sid else ("", "")
            pos = order.index(sid) + 1 if sid in order else 0
            rows.append({"role": role, "label": bots.label_of(role, e), "state": where,
                         "sid": sid, "position": pos, "doing": doing, "latest": said, "primary": role == primary,
                         "memory": str(bots.bot_dir(role)) if bots.is_memory_bot(e) else "",
                         "responsibility": str(e.get("responsibility") or "")})
            mark = "★ " if role == primary else "  "
            tag = {"external": _("external", "外部"),
                   "awake": _("awake", "醒著") + _(f" · tab #{pos} {sid}", f"・第 {pos} 個分頁 {sid}")
                   + (f"・{doing}" if doing else ""),
                   "asleep": _("asleep", "休息中")}[where]
            lines.append(f"{mark}{role}  [{tag}]  {str(e.get('responsibility') or '')[:60]}")
            if said:
                lines.append(_("      latest: ", "      最新：") + said[:100])
        return {"success": True, "message": "\n".join(lines) or _("(no bots yet)", "(還沒有 bot)"), "details": {"bots": rows}}

    def _bots_live(self, sid: str):
        """(state, latest line) from the status monitor's snapshot — zero cost, no transcript read."""
        try:
            state = self._agent_state_for_list(sid) or "idle"
            snap = self._agent_status_snapshot(sid)
            res = (snap[0] if isinstance(snap, tuple) else snap) or {}
            said = (res.get("action") if state == "working" else "") or res.get("narration") or ""
            return state, " ".join(str(said).split())
        except Exception:
            return "", ""

    # ── the phone's bot view (Frame Link GET /link/bots, POST /link/bots/wake) ────
    LINK_V = 1                  # stays 1: an app sees v > 1 and turns its bot view off; plan A's fields are told by `kind`

    def _bots_link(self, args: dict) -> dict:
        """What a paired phone shows (docs/mobile-link.md): every bot with its face, where
        it is and what it is doing, and every tab's face, state, last answer and latest
        line, by the desktop's own rules (bots.js avatarFor, previewOf, lastAt). Read
        from the status monitor's snapshot, so a phone asking every few seconds costs no
        transcript read; only a tab waiting on a menu has its screen read, for the
        options. `rev` hashes all of it but the clock: a phone that sends its last rev
        gets {"unchanged": true} back."""
        cfg = _cfg()
        on = (cfg.get("settings", {}) or {}).get("experimental_bots", False) is True
        raw = (cfg.get("agent_roster") or {}) if on else {}
        bound = cfg.get("bots_sessions") or {}
        now = time.time()
        tabs, awake = {}, {}
        order = self._ordered_sids(cfg) if on else []
        for sid, s in (list(self.sessions.items()) if on else []):
            role = self._bots_role_of(sid, cfg)
            t = self._link_tab(sid, s, role, bound.get(sid) or {}, raw, now)
            cli = self._tab_cli(s)
            t.update(kind="bot" if role else ("temp" if cli else "terminal"),
                     name=role or getattr(s, "_custom_label", None) or (str(getattr(s, "cmd", "") or "").split() or [""])[0],
                     cli=cli, tg=self._tg_number(sid), position=order.index(sid) + 1 if sid in order else 0)
            tabs[sid] = t
            if role:
                awake.setdefault(role, sid)
        primary = bots.primary_role(raw)
        name_tabs = self._name_tabs()
        rows = []
        for role, e in raw.items():                      # plan A: every role is a bot; memory says which remember
            if not isinstance(e, dict):
                continue
            ext = bots.is_external(e)
            sid = "" if ext else (awake.get(role) or ("" if bots.is_bot(e) else bot_names.bot_tab(role, raw, name_tabs, bound)))
            t = tabs.get(sid) or {}
            cli = "" if ext else (agent_status.worker_kind(str(e.get("cmd") or "")).replace("other", "") or "")
            rows.append({"role": role, "label": bots.label_of(role, e),
                         "responsibility": str(e.get("responsibility") or ""), "model": str(e.get("model") or ""),
                         "runs_on": "" if ext else self._link_runs_on(sid, e),
                         "avatar": bots.resolved_avatar((e.get("bot") or {}).get("avatar"), role),
                         "primary": role == primary, "external": ext,
                         "state": "external" if ext else ("awake" if sid else "asleep"),
                         "sid": sid, "position": order.index(sid) + 1 if sid in order else 0,
                         **{k: t.get(k, d) for k, d in (("activity", ""), ("doing", ""), ("latest", ""), ("last_at", 0),
                                                        ("blocked", ""), ("closing", ""), ("decision", None))},
                         "memory": bots.is_memory_bot(e), "cli": cli, "coordinator": bool(e.get("coordinator")),
                         "tg": t.get("tg", 0)})
        body = {"enabled": on, "primary": primary, "bots": rows, "tabs": tabs,
                "choices": {"shapes": list(bots.SHAPES), "colors": [{"key": c, "hex": bots.PALETTE[c]} for c in bots.COLORS],
                            "custom_color": True,     # what /link/bots/avatar accepts; also any #RRGGBB
                            "clis": _cli_choices()}}  # what /link/bots/settings takes as cli
        calm = lambda r: dict(r, last_at=0) if r.get("activity") == "working" else r   # a working tab's last_at is the clock
        rev = int(hashlib.sha1(json.dumps(dict(body, bots=[calm(b) for b in rows], tabs={k: calm(v) for k, v in tabs.items()}),
                                          sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8], 16)
        if str(args.get("rev") or "") == str(rev):
            return {"success": True, "unchanged": True, "rev": rev}
        return {"success": True, "v": self.LINK_V, "rev": rev, "now": now, **body}

    def _link_tab(self, sid: str, s, role: str, row: dict, raw: dict, now: float) -> dict:
        snap = self._agent_status_snapshot(sid)
        res = (snap[0] if isinstance(snap, tuple) else snap) or {}
        state = str(res.get("state") or "")
        activity = state if state in ("working", "idle", "done", "decision", "stuck") else ""
        if activity == "working":                      # bots.js previewOf
            pick = res.get("action") or res.get("narration") or res.get("summary")
        else:
            pick = res.get("narration") or res.get("task") or (res.get("summary") if state not in ("", "unknown", "idle") else "")
        latest = " ".join(re.sub(r"[`*#>_~]+", "", str(pick or "")).split()) or (
            str((raw.get(role) or {}).get("responsibility") or "") if role else "")
        with _CLOSING_LOCK:
            closing = (_CLOSING.get(sid) or {}).get("phase", "")
        out_ts = float(getattr(s, "_last_output_activity_time", 0.0) or 0.0)
        av = ((raw.get(role) or {}).get("bot") or {}).get("avatar") if role else row.get("avatar")
        return {"bot": role, "avatar": bots.resolved_avatar(av, role or sid), "plain": bool(row.get("plain")),
                "closing": closing, "activity": activity, "model": _model_name(res.get("model")),
                "doing": agent_status.redact(str(res.get("action") or ""), 140) if activity == "working" else "",
                "latest": agent_status.redact(latest, 140),
                "last_at": float(now if activity == "working" else (self._last_activity_at(sid, s, out_ts) or 0)),
                "blocked": agent_status.redact(self._agent_blocked_for_list(sid), 140),
                "decision": self._link_decision(sid, activity, res)}

    @staticmethod
    def _tab_cli(s) -> str:
        """The CLI a tab runs (a provider key), "" for a terminal."""
        kind = agent_status.worker_kind(str(getattr(s, "cmd", "") or ""))
        return "" if kind == "other" else kind

    def _tg_number(self, sid: str) -> int:
        """The tab's /N on Telegram (the bridge's order), 0 when it is not there."""
        try:
            order = getattr(getattr(self, "bridge", None), "_slot_order", None) or []
            return order.index(sid) + 1 if sid in order else 0
        except Exception:
            return 0

    def _link_runs_on(self, sid: str, e: dict) -> str:
        """The model a bot runs: what its tab's transcript says, else its roster command."""
        snap = self._agent_status_snapshot(sid) if sid else None
        mi = ((snap[0] if isinstance(snap, tuple) else snap) or {}).get("model")
        if isinstance(mi, dict) and mi.get("name"):
            return mi["name"] + (f" {mi['effort']}" if mi.get("effort") else "")
        import agent_model
        cmd = str(e.get("cmd") or "")
        return agent_model.describe(agent_model.apply(cmd, e["model"]) if e.get("model") else cmd)

    def _link_decision(self, sid: str, activity: str, res: dict):
        """{"id", "kind", "reason", "options": [{"n", "text"}]} while a tab waits on the person:
        the menu on its screen (with `detail`, its dialog's words: dialog_words), or the
        question its last turn ended on with [[SF:RED]] (kept
        whether or not Telegram is on, which /link/signals depends on). None otherwise.
        `id` fingerprints this very question, for /link/answer: a menu by its whole dialog
        (two permission prompts with the same words differ in the command they ask about),
        a RED question by when it was asked."""
        if activity == "working":
            return None
        qid = lambda *parts: hashlib.sha1("\n".join((sid,) + parts).encode()).hexdigest()[:12]
        if activity == "decision":
            try:
                import bridge_telegram
                # The visible screen only, and its last menu (menu_block): an older menu in the
                # scrollback was offered, and answered, in place of the one being asked.
                lines = [re.sub(r"^\s*│\s?|\s*│\s*$", "", ln.rstrip())          # a boxed dialog's side borders
                         for ln in tab_delivery.visible_text(self, sid).splitlines()]
                opts, at, end = bridge_telegram.menu_block(lines)
                if not opts:
                    raise ValueError("no menu")
                top = next((i for i in range(at - 1, max(-1, at - 16), -1)       # the rule the dialog opens with
                            if re.fullmatch(r"\s*─{10,}\s*", lines[i])), max(0, at - 6))
                reason = next((ln.strip(" ─╌") for ln in reversed(lines[:at]) if ln.strip(" ─╌")), "")
                return {"id": qid("menu", *(ln.strip() for ln in lines[top:end + 1])), "kind": "menu",
                        "reason": agent_status.redact(reason, 300), "detail": dialog_words(lines, at),
                        "options": [{"n": int(o["num"]), "text": option_words(o["text"])} for o in opts[:9]]}
            except Exception:
                pass                                   # no menu read off the screen: maybe a RED question
        red = bridge_home.red_pending(sid)
        if red and red["at"] >= float(res.get("prompt_at") or 0):       # not answered since
            return {"id": qid("red", red["reason"], repr(red["options"]), repr(red["at"])), "kind": "red",
                    "reason": agent_status.redact(red["reason"], 300),
                    "options": [{"n": int(n), "text": option_words(t)} for n, t in red["options"]]}
        return None

    def _bots_link_answer(self, args: dict, via: str = "phone") -> dict:
        """POST /link/answer {"sid", "n", "id"}: the phone answers the question /link/bots
        showed it, only while that is still the tab's question (`id`). Otherwise the answer
        would go into whatever the tab asks next, which is also why a question answered a
        moment ago on Telegram is refused. It goes in as Telegram's choice button sends it
        ("n" and Enter), and the Telegram copies of the question say it was answered on the
        phone (`via`: or on the computer, from the desktop's chat view), their buttons gone.
        /link/input stays as it is: raw keys, no checks."""
        sid = str(args.get("sid") or "")
        s = self.sessions.get(sid)
        if not s:
            return {"success": False, "code": "no_tab", "message": _(f"No tab {sid}", f"沒有 {sid} 這個分頁")}
        snap = self._agent_status_snapshot(sid)
        res = (snap[0] if isinstance(snap, tuple) else snap) or {}
        state = str(res.get("state") or "")
        cur = self._link_decision(sid, state if state in ("working", "idle", "done", "decision", "stuck") else "", res)
        stale = {"success": False, "code": "stale", "decision": cur, "message": _(
            "This question was answered already or has changed; nothing was sent",
            "這題已經回答過了，或題目已經換了；沒有送出")}
        if not cur or cur["id"] != str(args.get("id") or ""):
            return stale
        try:
            n = int(args.get("n"))
        except (TypeError, ValueError):
            n = None
        if n not in [o["n"] for o in cur["options"]]:
            return {"success": False, "code": "no_option", "decision": cur,
                    "message": _(f"There is no option {args.get('n')}", f"沒有選項 {args.get('n')}")}
        claimed = bridge_home.claim_for_phone(sid, n)
        if claimed is None:
            return stale
        sent = tab_delivery.answer_choice(self, sid, n, cur.get("kind", "menu"))
        bridge_home.phone_answered(self, sid, n, claimed, where=via)
        _dlog("bots", f"{sid}: answered {n} from the {via} ({sent!r})")
        self._push_answered(sid, cur["id"], str(args.get("peer_id") or ""))
        return {"success": True, "message": _(f"Answered {n}", f"已選 {n}")}

    def _link_editable(self, role: str):
        """(roster entry, None) for a bot the phone may change, else (None, the refusal).
        An external bot lives elsewhere and is changed there."""
        if not self._bots_enabled():
            return None, {"success": False, "message": _("Bots are experimental: turn them on in Settings first (experimental_bots)",
                                                         "Bots 是實驗功能，請先在設定裡打開（experimental_bots）")}
        e = (_cfg().get("agent_roster") or {}).get(role)
        if not bots.is_bot(e):
            return None, {"success": False, "message": _(f'"{role}" is not a bot', f"「{role}」不是 bot")}
        if bots.is_external(e):
            return None, {"success": False, "message": _(f'"{role}" lives outside ShellBot; change it there',
                                                         f"「{role}」住在 ShellBot 外面，請到它那邊改")}
        return e, None

    def _bots_link_avatar(self, args: dict) -> dict:
        """POST /link/bots/avatar {"sid" | "role", "avatar": {"shape", "color"}}: a face, the page's
        own way. A tab through bots_set_avatar (on a bot's tab that is the bot's face); a sleeping
        bot through the bot editor's save. `avatar: null` gives a plain tab its default face back."""
        sid, role, av = str(args.get("sid") or ""), str(args.get("role") or ""), args.get("avatar")
        clean = bots.normalize_avatar(av)
        if av is not None and not clean:
            return {"success": False, "message": _("Pick the shape and colour from choices in /link/bots (or a #RRGGBB colour)",
                                                  "形狀和顏色請從 /link/bots 的 choices 裡選（顏色也可以用 #RRGGBB）")}
        if sid:
            if not self._bots_enabled():
                return self._link_editable("")[1]
            if sid not in self.sessions:
                return {"success": False, "message": _(f"No tab {sid}", f"沒有 {sid} 這個分頁")}
            if self._bots_role_of(sid) and not clean:
                return {"success": False, "message": _("A bot keeps a face: pick one", "bot 一定要有頭像，請選一個")}
            res = json.loads(self.bots_set_avatar(sid, json.dumps(clean)))
            self._bots_notify_ui()
            return res
        refused = self._link_editable(role)[1] or (None if clean else {"success": False, "message": _(
            "A bot keeps a face: pick one", "bot 一定要有頭像，請選一個")})
        return refused or self._bots_save_for_sfctl({"role": role, "data": json.dumps({"avatar": clean})})

    def _bots_link_settings(self, args: dict) -> dict:
        """POST /link/bots/settings {"role", "responsibility"?, "model"?}: the bot editor's save
        with only what is given; skills, memory, the face and its command stay. An empty model
        means the CLI's default, as in the editor."""
        role = str(args.get("role") or "").strip()
        refused = self._link_editable(role)[1]
        if refused:
            return refused
        if args.get("cli") is not None or args.get("apply"):                  # plan A: another CLI or model (bots_switch)
            if args.get("responsibility") is not None:
                self._bots_save_for_sfctl({"role": role, "data": json.dumps(
                    {"responsibility": str(args["responsibility"]).strip()}, ensure_ascii=False)})
            return self._bots_switch(role, str(args.get("cli") or ""), args.get("model"), str(args.get("apply") or "next_wake"))
        data = {k: str(args[k]).strip() for k in ("responsibility", "model") if args.get(k) is not None}
        if not data:
            return {"success": False, "message": _("Nothing to change: give responsibility or model",
                                                  "沒有要改的欄位：請給 responsibility 或 model")}
        if len(data.get("model", "")) > 80 or re.search(r"\s", data.get("model", "")):
            return {"success": False, "message": _("A model is one word, e.g. sonnet", "模型是一個詞，例如 sonnet")}
        return self._bots_save_for_sfctl({"role": role, "data": json.dumps(data, ensure_ascii=False)})

    def _bots_link_primary(self, args: dict) -> dict:
        """POST /link/bots/primary {"role"}: put a bot on the top card (bots_set_primary)."""
        role = str(args.get("role") or "").strip()
        return self._link_editable(role)[1] or json.loads(self.bots_set_primary(role))

    def _bots_link_wake(self, args: dict) -> dict:
        """POST /link/bots/wake: the phone wakes a bot (its tab opens with its memory; an awake one is found)."""
        if not self._bots_enabled():
            return {"success": False, "message": _("Bots are experimental: turn them on in Settings first (experimental_bots)",
                                                  "Bots 是實驗功能，請先在設定裡打開（experimental_bots）")}
        return json.loads(self.bots_open(str(args.get("role") or "").strip()))

    def bots_set_primary(self, role: str) -> str:
        """Put a bot on the top card (role) or empty the card (''). One primary at a time."""
        role = (role or "").strip()
        out = {}

        def _mut(cfg):
            roster = cfg.get("agent_roster") or {}
            if role and not bots.is_bot(roster.get(role)):
                out["err"] = _(f'"{role}" is not a bot', f"「{role}」不是 bot")
                return
            for r, e in roster.items():
                if bots.is_bot(e):
                    e["bot"]["primary"] = (r == role)
        _update(_mut)
        if out.get("err"):
            return json.dumps({"success": False, "message": out["err"]}, ensure_ascii=False)
        self._bots_notify_ui()
        label = bots.label_of(role, (_cfg().get("agent_roster") or {}).get(role)) if role else ""
        return json.dumps({"success": True, "message": _(f"Primary bot: {label}", f"主要 Bot：{label}") if role
                           else _("No primary bot now", "已取消主要 Bot")},
                          ensure_ascii=False)

    def bots_skill_catalog(self) -> str:
        try:
            return json.dumps({"skills": bots.skill_catalog()}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"skills": [], "message": str(e)})

    # ── writing config ──────────────────────────────────────────────────
    def _bots_notify_ui(self):
        try:
            if getattr(self, "_window", None):
                self._window.evaluate_js("window.SFBots && SFBots.refresh(true)")
        except Exception:
            _swallow("bots:notify_ui")

    def bots_set_avatar(self, sid: str, avatar_json: str) -> str:
        try:
            av = bots.normalize_avatar(json.loads(avatar_json or "null"))
        except ValueError:
            av = None
        if sid not in self.sessions:
            return json.dumps({"success": False, "message": _(f"No tab {sid}", f"沒有 {sid} 這個分頁")})
        role = self._bots_role_of(sid)
        live = set(self.sessions)

        def _mut(cfg):
            if role:
                b = cfg.setdefault("agent_roster", {}).setdefault(role, {}).setdefault("bot", {})
                if av:
                    b["avatar"] = av
                return
            table = cfg.setdefault("bots_sessions", {})
            row = table.setdefault(sid, {})
            if av:
                row["avatar"] = av
            else:
                row.pop("avatar", None)
            for k in [k for k, v in table.items() if k not in live or not v]:
                table.pop(k, None)
        _update(_mut)
        return json.dumps({"success": True, "message": _("Face changed", "已更換頭像"), "details": {"bot": role}})

    def _bots_bind(self, sid: str, role: str):
        live = set(self.sessions)

        def _mut(cfg):
            table = cfg.setdefault("bots_sessions", {})
            row = table.setdefault(sid, {})
            if role:
                row["bot"] = role
                row.pop("avatar", None)        # the bot's own face wins
                row.pop("plain", None)
            else:
                row.pop("bot", None)
                row["plain"] = True            # keep it plain even if its label matches a bot
            for k in [k for k, v in table.items() if k not in live or not v]:
                table.pop(k, None)
        _update(_mut)

    def bots_bind_tab(self, sid: str, role: str = "") -> str:
        """From a conversation's own settings: make this tab a bot's tab (role), or
        stop it being one (''). The bot and its memory are untouched either way."""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "message": _(f"No tab {sid}", f"沒有 {sid} 這個分頁")})
        role = (role or "").strip()
        label = ""
        if role:
            e = (_cfg().get("agent_roster") or {}).get(role)
            if not bots.is_memory_bot(e):
                return json.dumps({"success": False, "message": _(f'"{role}" is not a bot this tab can be',
                                                                  f"「{role}」不是這個分頁能當的 bot")})
            label = bots.label_of(role, e)
        self._bots_bind(sid, role)
        if label and getattr(s, "_custom_label", None) != label:
            self.rename_session(sid, label, manual=True)
        self._bots_notify_ui()
        return json.dumps({"success": True, "message": _(f"This tab is now {label}", f"這個分頁現在是「{label}」")
                           if role else _("This tab is not a bot any more; the bot and its memory stay",
                                          "這個分頁不再是 bot；bot 和它的記憶都保留")}, ensure_ascii=False)

    def bots_save(self, role: str, data_json: str, bind_sid: str = "") -> str:
        name = bots.clean_name(role)
        err = bots.name_error(name)
        if err:
            return json.dumps({"success": False, "message": err})
        try:
            data = json.loads(data_json or "{}")
        except ValueError:
            return json.dumps({"success": False, "message": _("Bad data", "資料格式錯誤")})
        bind_sid = (bind_sid or "").strip()
        tab = self.sessions.get(bind_sid) if bind_sid else None
        if bind_sid and not tab:
            return json.dumps({"success": False, "message": _(f"No tab {bind_sid}", f"沒有 {bind_sid} 這個分頁")})
        cmd = str(data.get("cmd") or (getattr(tab, "cmd", "") if tab else "") or "claude").strip()
        state = {}

        def _mut(cfg):
            roster = cfg.setdefault("agent_roster", {})
            entry = roster.get(name)
            created = not isinstance(entry, dict)
            if created:
                entry = {"label": name, "cmd": cmd, "agent_code": "BOT", "handoff": True}
            prev = entry.get("bot") if isinstance(entry.get("bot"), dict) else {}
            payload = dict(data)
            payload["created_by_bots"] = created or prev.get("created_by_bots", False)
            entry["bot"] = bots.normalize_bot(payload, prev)
            if "responsibility" in data:
                entry["responsibility"] = str(data.get("responsibility") or "").strip()[:bots.RESP_MAX]
            if data.get("cmd"):
                entry["cmd"] = cmd
            if "model" in data:
                entry["model"] = str(data.get("model") or "").strip()
            if entry["bot"].get("primary"):
                for r, e in roster.items():
                    if r != name and isinstance(e, dict) and isinstance(e.get("bot"), dict):
                        e["bot"]["primary"] = False
            roster[name] = entry
            state["label"] = bots.label_of(name, entry)
            state["created"] = created
            if bind_sid:
                table = cfg.setdefault("bots_sessions", {})
                table[bind_sid] = {"bot": name}
        try:
            _update(_mut)
        except ValueError as e:
            return json.dumps({"success": False, "message": str(e)})
        try:
            bots.ensure_dirs(name)
        except OSError:
            _swallow("bots_save:ensure_dirs")
        if tab and state.get("label") and getattr(tab, "_custom_label", None) != state["label"]:
            # The tab carries the bot's name from now on: A2A and delegate find a
            # role's tab by its label, so the two must not drift apart.
            self.rename_session(bind_sid, state["label"], manual=True)
        self._bots_notify_ui()
        return json.dumps({"success": True,
                           "message": (_("Created", "已建立") if state.get("created") else _("Updated", "已更新"))
                           + _(f' "{state.get("label") or name}"', f"「{state.get('label') or name}」"),
                           "details": {"role": name, "label": state.get("label")}}, ensure_ascii=False)

    # ── plan A: a temporary bot kept ─────────────────────────────────────
    def bots_keep(self, sid: str, name: str = "") -> str:
        """Keep a temporary bot: the AI tab becomes a bot named `name` (its title when not
        given) with the face it has, a memory folder and its binding, and from then on it
        writes its memory before it closes. JSON: {"success", "role"} or {"success": false,
        "code", "message", "suggestion"?} (codes in docs/mobile-link.md)."""
        return json.dumps(self._bots_keep(sid, name), ensure_ascii=False)

    def _bots_keep(self, sid: str, name: str = "") -> dict:
        def fail(code, en, zh, **extra):
            return {"success": False, "code": code, "message": _(en, zh), **extra}
        if not self._bots_enabled():
            return fail("bots_off", "Bots are off: turn them on in Settings first", "Bots 沒有打開：請先在設定裡打開")
        sid = str(sid or "").strip()
        s = self.sessions.get(sid)
        if s is None:
            return fail("no_tab", f"No tab {sid}", f"沒有 {sid} 這個分頁")
        if agent_status.worker_kind(getattr(s, "cmd", "") or "") == "other":
            return fail("not_ai", "A terminal tab is not a bot", "終端機分頁不是 bot")
        cfg = _cfg()
        row = (cfg.get("bots_sessions") or {}).get(sid) or {}
        if row.get("plain"):
            return fail("plain", "This tab was marked as not a bot", "這個分頁標過「不是 bot」")
        if self._bots_role_of(sid, cfg):
            return fail("already_bot", "It is a bot already", "它已經是 bot 了")
        name = bots.clean_name(name or getattr(s, "_custom_label", "") or "")
        err = bots.name_error(name)
        if err:
            return fail("bad_name", err, err)
        if self._bot_name_taken(name, cfg):
            return fail("name_taken", f'"{name}" is taken by another bot', f"「{name}」已經有別的 bot 在用",
                        suggestion=self._free_bot_name(name, cfg))
        data = {"cmd": launch_cmd.without_session(getattr(s, "cmd", "")) or "claude",
                "avatar": bots.resolved_avatar(row.get("avatar"), sid)}     # the face it has, not one made from the name
        res = json.loads(self.bots_save(name, json.dumps(data, ensure_ascii=False), sid))
        if not res.get("success"):
            return {"success": False, "code": "bad_name", "message": res.get("message", "")}
        return {"success": True, "role": name, "message": res.get("message", "")}

    @staticmethod
    def _bot_name_taken(name: str, cfg: dict, but: str = "") -> bool:
        """A bot's name is its own on this computer: no other role's key, label or former
        name (they still reach that bot), any case. `but` is a bot being renamed: its own
        names are free to it."""
        want = name.strip().casefold()
        return any(want in {str(r).casefold(), bots.label_of(r, e).casefold(),
                            *(n.casefold() for n in bots.former_names(e))}
                   for r, e in (cfg.get("agent_roster") or {}).items() if isinstance(e, dict) and r != but)

    def _free_bot_name(self, name: str, cfg: dict) -> str:
        for n in range(2, 100):
            cand = f"{name} {n}"
            if not self._bot_name_taken(cand, cfg) and not bots.name_error(cand):
                return cand
        return ""

    # ── plan A: a bot renamed ────────────────────────────────────────────
    def bots_rename(self, role: str, name: str) -> str:
        """A bot gets another name: from its editor, from its tab (rename_session), from
        sfctl or a phone, one path for all. Its face, job, memory, CLI and tab stay, and the
        old name still reaches it. JSON: {"success", "role", "former"} or {"success": false,
        "code", "message", "suggestion"?}; codes in docs/mobile-link.md."""
        return json.dumps(self._bots_rename(role, name), ensure_ascii=False)

    def _bots_rename(self, role: str, name: str) -> dict:
        def fail(code, en, zh, **extra):
            return {"success": False, "code": code, "message": _(en, zh), **extra}
        if not self._bots_enabled():
            return fail("bots_off", "Bots are off: turn them on in Settings first", "Bots 沒有打開：請先在設定裡打開")
        cfg = _cfg()
        roster, bound = cfg.get("agent_roster") or {}, cfg.get("bots_sessions") or {}
        role = str(role or "").strip()
        entry = roster.get(role)
        if not bots.is_bot(entry):
            return fail("no_bot", f'No bot "{role}"', f"沒有「{role}」這個 bot")
        if bots.is_external(entry):
            return fail("external", "A bot outside ShellBot is named where it lives", "外部的 bot，名字要在它那邊改")
        name = bots.clean_name(name)
        err = bots.name_error(name)
        if err:
            return fail("bad_name", err, err)
        if name == role:
            return {"success": True, "role": role, "former": role, "message": _("The name is the same", "名字沒變")}
        if self._bot_name_taken(name, cfg, but=role):
            return fail("name_taken", f'"{name}" is taken by another bot', f"「{name}」已經有別的 bot 在用",
                        suggestion=self._free_bot_name(name, cfg))
        live = bot_names.bot_tab(role, roster, self._name_tabs(), bound)      # its tab, bound or by its title
        sids = sorted({s for s, row in bound.items() if (row or {}).get("bot") == role and s in self.sessions}
                      | ({live} if live else set()))
        with _CLOSING_LOCK:
            if any(s in _CLOSING for s in sids):
                return fail("busy", "It is writing its memory; rename it when it has closed",
                            "它正在寫記憶，關掉之後再改名")
        if bots.move_dir(role, name):
            return fail("folder_taken", f"A folder {bots.bot_dir(name)} is already there; move it away first",
                        f"已經有 {bots.bot_dir(name)} 這個資料夾，請先把它移開")
        state = {}

        def _mut(c):
            table = c.setdefault("bots_sessions", {})
            for s in sids:                     # a tab that was its by title keeps being its, by binding
                table.setdefault(s, {})["bot"] = role
            state["formers"] = bots.rename_in_config(c, role, name)
        try:
            _update(_mut)
        except Exception as e:
            bots.move_dir(name, role)
            return fail("failed", f"Not renamed: {e}", f"沒有改名：{e}")
        for s in sids:
            self.rename_session(s, name, manual=True)      # the tab, Telegram, LINE and the page
        self._bots_notify_ui()
        _dlog("bots", f"renamed {role!r} -> {name!r}; still reached as {state.get('formers')}")
        return {"success": True, "role": name, "former": role,
                "message": _(f'"{role}" is now "{name}"; the old name still reaches it',
                             f"「{role}」改名為「{name}」；用舊名字也還找得到它")}

    def _bots_rename_via_tab(self, sid: str, name: str):
        """rename_session on a tab bound to a bot renames the bot: one name per bot, and its
        tab carries it. None when the tab is no bot's (a temporary bot, a terminal, a tab
        marked not a bot) or the name is the bot's own: then only the tab's title changes."""
        if not self._bots_enabled():
            return None
        cfg = _cfg()
        row = (cfg.get("bots_sessions") or {}).get(sid) or {}
        role = "" if row.get("plain") else str(row.get("bot") or "")
        entry = (cfg.get("agent_roster") or {}).get(role) if role else None
        if not bots.is_bot(entry) or bots.is_external(entry):
            return None
        name = bots.clean_name(name)
        if name in (role, bots.label_of(role, entry)):
            return None
        return json.dumps(self._bots_rename(role, name), ensure_ascii=False)

    # ── plan A: another CLI or model for a bot ───────────────────────────
    def bots_switch(self, role: str, cli: str = "", model=None, apply: str = "next_wake") -> str:
        """A bot on another CLI and/or model. Its name, face, job and memory stay; the
        conversation does not move to the new CLI. apply="next_wake" keeps it for the next
        wake; "now" wakes it on the new one now: an awake bot writes its memory and closes
        first (minutes; {"pending": true}), one asleep is woken at once ({"sid"})."""
        return json.dumps(self._bots_switch(role, cli, model, apply), ensure_ascii=False)

    def _cmd_for_cli(self, cli: str) -> str:
        """How this computer runs a CLI: the first preset of it (its own flags kept), else
        the built-in one."""
        try:
            for g in json.loads(self.preset_groups()) or []:
                if g.get("provider") == cli and g.get("items"):
                    return str(g["items"][0].get("cmd") or "")
        except Exception:
            _swallow("_cmd_for_cli")
        for p in main._DEFAULT_AI_PRESETS:
            if agent_status.worker_kind(p["cmd"]) == cli:
                return p["cmd"]
        return cli

    def _bots_switch(self, role: str, cli: str = "", model=None, apply: str = "next_wake") -> dict:
        def fail(code, en, zh):
            return {"success": False, "code": code, "message": _(en, zh)}
        role = str(role or "").strip()
        cfg = _cfg()
        entry = (cfg.get("agent_roster") or {}).get(role)
        if not isinstance(entry, dict):
            return fail("no_bot", f'No bot "{role}"', f"沒有「{role}」這個 bot")
        if bots.is_external(entry):
            return fail("external", "A bot outside ShellBot runs where it lives", "外部的 bot 用什麼 CLI 不歸這台管")
        current = agent_status.worker_kind(str(entry.get("cmd") or ""))
        cli = str(cli or "").strip()
        data = {}
        if cli and cli != current:
            spec = next((c for c in _cli_choices() if c["key"] == cli), None)
            if not spec or not spec["installed"]:
                return fail("bad_cli", f"{cli} is not installed here", f"這台沒有裝 {cli}")
            data["cmd"] = self._cmd_for_cli(cli)
            if model is None:
                model = ""                     # the last CLI's model would not fit this one
        target = cli or current
        if model is not None:
            model = str(model).strip()
            if model and not next((c["models"] for c in _cli_choices() if c["key"] == target), False):
                return fail("model_fixed", "This CLI picks its model inside it; leave the model empty",
                            "這個 CLI 的模型要在它裡面選，模型請留空")
            if len(model) > 80 or re.search(r"\s", model):
                return fail("bad_model", "A model is one word, e.g. sonnet", "模型是一個詞，例如 sonnet")
            data["model"] = model
        if not data and apply != "now":
            return fail("nothing", "Nothing to change", "沒有要改的")
        sid = next((x for x in list(self.sessions) if self._bots_role_of(x, cfg) == role
                    or bot_names.bot_tab(role, cfg.get("agent_roster") or {}, self._name_tabs(),
                                         cfg.get("bots_sessions") or {}) == x), "")
        with _CLOSING_LOCK:
            if sid and sid in _CLOSING:
                return fail("busy", "It is writing its memory; try again when it has closed", "它正在寫記憶，關掉之後再換")
        if data:
            def _mut(c):
                e = (c.get("agent_roster") or {}).get(role)
                if isinstance(e, dict):
                    e.update(data)             # the command and the model only: no bot block is added or changed
            _update(_mut)
            self._bots_notify_ui()
        if apply != "now":
            return {"success": True, "role": role, "applies": "next_wake"}
        if not sid:
            opened = json.loads(self.bots_open(role))
            return {"success": bool(opened.get("success")), "role": role,
                    "sid": (opened.get("details") or {}).get("sid", ""), "message": opened.get("message", "")}
        threading.Thread(target=self._bots_switch_now, args=(role, sid), daemon=True, name="bots-switch").start()
        return {"success": True, "role": role, "pending": True}

    def _bots_switch_now(self, role: str, sid: str):
        """Close the bot's tab the bot's way (its memory written first), then wake it on its
        new command. Gives up, and says so in the log, if the tab is still open after the
        close flow's own limits."""
        try:
            self.bots_close(sid, "save")
            end = time.time() + BUSY_WAIT_SEC + SUMMARY_WAIT_SEC + 60
            while sid in self.sessions and time.time() < end:
                time.sleep(2)
            if sid in self.sessions:
                _dlog("bots", f"{role}: still open after the close flow; not switched now")
                return
            self.bots_open(role)
        except Exception as e:
            _dlog("bots", f"{role}: switching now failed: {e}")

    # ── plan A: the one-time tidy ────────────────────────────────────────
    def bots_migrate(self, apply: bool = False) -> str:
        return json.dumps(self._bots_migrate(apply), ensure_ascii=False)

    def _bots_migrate(self, apply: bool = False) -> dict:
        """Additive only: a tab a bot is taken from by its title (no binding yet) gets the
        binding, so renaming the tab keeps it that bot's. Tabs marked "not a bot", roster
        keys and the tabs' order (Telegram's /N) are left as they are. A dry run unless
        apply; apply copies config.json to config.json.bak-<time> first. Run twice, it adds
        nothing the second time."""
        cfg = _cfg()
        roster, bound, tabs = cfg.get("agent_roster") or {}, cfg.get("bots_sessions") or {}, self._name_tabs()
        add = []
        for t in tabs:
            row = bound.get(t["sid"]) or {}
            if row.get("plain") or row.get("bot"):
                continue
            role = bot_names.role_of_tab(t["sid"], roster, tabs, bound,
                                         only=lambda r, e: bots.is_bot(e) and not bots.is_external(e))
            if role:
                add.append({"sid": t["sid"], "title": t["title"], "bot": role})
        out = {"success": True, "apply": bool(apply), "bindings": add}
        if not apply or not add:
            return out
        backup = main.CONFIG_FILE.with_name(f"config.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(main.CONFIG_FILE, backup)

        def _mut(c):
            table = c.setdefault("bots_sessions", {})
            for a in add:
                row = table.get(a["sid"]) or {}
                if not row.get("plain") and not row.get("bot"):
                    table[a["sid"]] = dict(row, bot=a["bot"])
        _update(_mut)
        self._bots_notify_ui()
        return dict(out, backup=str(backup))

    def _bots_link_keep(self, args: dict) -> dict:
        """POST /link/bots/keep {"sid", "name"?}: a phone keeps a temporary bot (bots_keep)."""
        return self._bots_keep(str(args.get("sid") or ""), str(args.get("name") or ""))

    def _bots_save_for_sfctl(self, args: dict) -> dict:
        """`sfctl bots-save <role> '<json>' [--bind <sid>]`: the bot editor's save, for
        scripts and agents. The editor sends the whole form; a script names only what
        it changes, so fields left out keep their value (skills, primary, memory…).
        `--bind` makes an open tab the bot's tab, as "Make it a bot…" in the tab's own
        settings does: the conversation goes on, the tab takes the bot's name. A script
        does not see the page, so it may not take another bot's tab or give a bot a
        second one."""
        if not self._bots_enabled():
            return {"success": False, "message": _("Bots are experimental: turn them on in Settings first (experimental_bots)",
                                                  "Bots 是實驗功能，請先在設定裡打開（experimental_bots）")}
        role = str(args.get("role") or "").strip()
        try:
            data = json.loads(args.get("data") or "{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return {"success": False, "message": _("data must be a JSON object", "data 要是 JSON 物件")}
        cfg = _cfg()
        bind = str(args.get("bind") or "").strip()
        if bind:
            if bind not in self.sessions:
                return {"success": False, "message": _(f"No tab {bind}", f"沒有 {bind} 這個分頁")}
            had = self._bots_role_of(bind, cfg)
            if had and had != role:
                return {"success": False, "message": _(
                    f'{bind} is already the tab of "{had}"; make it a plain tab in its own settings first',
                    f"{bind} 已經是「{had}」的分頁；要換，先在它的對話設定裡改回一般分頁")}
            other = [s for s in self.sessions if s != bind and self._bots_role_of(s, cfg) == role]
            if other:
                return {"success": False, "message": _(
                    f'"{role}" already has a tab ({other[0]}); a bot has one tab at a time',
                    f"「{role}」已經有分頁 {other[0]}；一個 bot 同時只有一個分頁")}
        entry = (cfg.get("agent_roster") or {}).get(role) or {}
        prev = entry.get("bot") if isinstance(entry.get("bot"), dict) else {}
        merged = {k: prev[k] for k in ("avatar", "skills", "memory", "primary", "external") if k in prev}
        merged.update(data)
        return json.loads(self.bots_save(role, json.dumps(merged, ensure_ascii=False), bind))

    def _bots_delete_for_sfctl(self, args: dict) -> dict:
        """`sfctl bots-delete <role>`: the bot editor's Delete, for scripts and agents."""
        if not self._bots_enabled():
            return {"success": False, "message": _("Bots are experimental: turn them on in Settings first (experimental_bots)",
                                                  "Bots 是實驗功能，請先在設定裡打開（experimental_bots）")}
        return json.loads(self.bots_delete(str(args.get("role") or "").strip()))

    # ── the primary bot on Telegram (bridge_home does the Telegram side) ─────
    def _tg_bind_user(self, user_id: int):
        """Add a paired person to the allowlist. Binding into an empty list (which
        let anyone in) leaves only that person on it."""
        def _mut(cfg):
            b = cfg.setdefault("bridge", {})
            users = [int(u) for u in (b.get("allowed_users") or [])]
            if user_id not in users:
                users.append(user_id)
            b["allowed_users"] = users
        _update(_mut)
        if self.bridge and user_id not in (self.bridge.config.allowed_users or []):
            self.bridge.config.allowed_users = list(self.bridge.config.allowed_users or []) + [user_id]

    def tg_home_status(self) -> str:
        """For the primary bot's Telegram panel: connection, who is bound, the switches."""
        cfg = _cfg()
        b = self.bridge
        users = [int(u) for u in ((cfg.get("bridge") or {}).get("allowed_users") or [])]
        bot = (getattr(b, "bot_info", None) or {}).get("username", "") if b else ""
        pair = bridge_home.pairing()
        if pair and bot:
            pair["link"] = f"https://t.me/{bot}?start={pair['code']}"
            pair["expires_in"] = max(0, int(pair.pop("until") - time.time()))
        return json.dumps({"configured": bool((cfg.get("bridge") or {}).get("bot_token")),
                           "connected": bool(b and getattr(b, "connected", False)), "bot": bot,
                           "users": users, "allow_all": not users, "primary": self._bots_primary_tab(cfg)[2],
                           "users_info": [{"id": u, "label": bridge_home.user_label(b, u)} for u in users],
                           "prefix_enabled": (cfg.get("bridge") or {}).get("prefix_enabled", True) is not False,
                           "pairing": pair or None, **bridge_home.settings(self)}, ensure_ascii=False)

    def tg_home_set(self, data_json: str) -> str:
        """{default_primary: bool, quiet: {on, from, to}}: only what is given changes."""
        try:
            data = json.loads(data_json or "{}")
        except ValueError:
            return json.dumps({"success": False, "message": _("Bad data", "資料格式錯誤")})

        def _mut(cfg):
            cur = cfg.setdefault("settings", {}).setdefault("tg_home", {})
            if "default_primary" in data:
                cur["default_primary"] = bool(data["default_primary"])
            if isinstance(data.get("quiet"), dict):
                q = dict(cur.get("quiet") or {})
                for k in ("on", "from", "to"):
                    if k in data["quiet"]:
                        q[k] = bool(data["quiet"][k]) if k == "on" else str(data["quiet"][k])[:5]
                cur["quiet"] = q
        _update(_mut)
        return self.tg_home_status()

    def tg_pair_start(self) -> str:
        if not (self.bridge and getattr(self.bridge, "connected", False)):
            return json.dumps({"success": False, "message": _("Connect the Telegram bot first", "請先連上 Telegram bot")})
        bridge_home.pair_start()
        return json.dumps(dict(json.loads(self.tg_home_status()), success=True), ensure_ascii=False)

    def tg_unbind(self, user_id) -> str:
        uid = int(user_id)

        def _mut(cfg):
            b = cfg.setdefault("bridge", {})
            b["allowed_users"] = [int(u) for u in (b.get("allowed_users") or []) if int(u) != uid]
        _update(_mut)
        if self.bridge:
            self.bridge.config.allowed_users = [u for u in (self.bridge.config.allowed_users or []) if u != uid]
        return self.tg_home_status()

    def tg_test(self) -> str:
        b = self.bridge
        if not (b and getattr(b, "connected", False)):
            return json.dumps({"success": False, "message": _("Telegram is not connected", "Telegram 沒有連線")})
        label = self._bots_primary_tab()[2] or "ShellBot"
        n = 0
        for chat_id in bridge_home._chats(b):
            r = bridge_home._tg(b, "sendMessage", {"chat_id": chat_id, "text": _(
                f"👋 This is ⭐ {label}. Write here to talk to me; /home brings you back to me.",
                f"👋 這是 ⭐ {label}。在這裡打字就是跟我說話；/home 隨時回到我這裡。")}) or {}
            n += 1 if r.get("ok") else 0
        return json.dumps({"success": n > 0, "message": _(f"Sent to {n} chat(s)", f"已送到 {n} 個對話")},
                          ensure_ascii=False)

    def bots_delete(self, role: str) -> str:
        out = {}

        def _mut(cfg):
            roster = cfg.get("agent_roster") or {}
            e = roster.get(role)
            if not bots.is_bot(e):
                out["missing"] = True
                return
            if e["bot"].get("created_by_bots"):
                roster.pop(role, None)          # we made the whole role; take it all back
            else:
                e.pop("bot", None)              # a role that existed before keeps working as a worker
                out["kept_role"] = True
            table = cfg.get("bots_sessions") or {}
            for sid in [k for k, v in table.items() if (v or {}).get("bot") == role]:
                table.pop(sid, None)
        _update(_mut)
        if out.get("missing"):
            return json.dumps({"success": False, "message": _(f'"{role}" is not a bot', f"「{role}」不是 bot")})
        self._bots_notify_ui()
        kept = _(f'; "{role}" was in the roster before it became a bot and stays there as a plain worker role',
                 f"；「{role}」在開成 bot 之前就在名冊裡，留下來當一般工作角色") if out.get("kept_role") else ""
        return json.dumps({"success": True, "message": _(f'Removed "{role}" (its memory folder stays at {bots.bot_dir(role)})',
                                       f"已移除「{role}」（記憶資料夾保留在 {bots.bot_dir(role)}）") + kept},
                          ensure_ascii=False)

    def bots_open_folder(self, role: str) -> str:
        d = bots.ensure_dirs(role)
        try:
            opener = "open" if sys.platform == "darwin" else ("explorer" if os.name == "nt" else "xdg-open")
            subprocess.Popen([opener, str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})
        return json.dumps({"success": True, "message": str(d)})

    # ── waking ──────────────────────────────────────────────────────────
    def _bots_brief(self, role: str, entry: dict, raw_roster: dict, with_ack: bool = True) -> str:
        mem, last, last_name = bots.read_memory(role)
        skills = bots.resolve_skills((entry.get("bot") or {}).get("skills") or [])
        return bots.compose_brief(role, entry, raw_roster, memory=mem, last=last, last_name=last_name,
                                  skills=skills, is_primary=bots.primary_role(raw_roster) == role,
                                  with_ack=with_ack)

    def bots_open(self, role: str) -> str:
        """Wake a bot: focus its tab if it has one, else open one and hand it its brief."""
        cfg = _cfg()
        raw = cfg.get("agent_roster") or {}
        entry = raw.get(role)
        if not bots.is_bot(entry):
            return json.dumps({"success": False, "message": _(f'"{role}" is not a bot', f"「{role}」不是 bot")})
        if bots.is_external(entry):
            return json.dumps({"success": False, "message": _(
                f'"{role}" lives outside ShellBot: reach it with A2A or its webhook',
                f"「{role}」住在 ShellBot 外面，用 A2A 或 webhook 找它")})
        label = bots.label_of(role, entry)
        for sid in list(self.sessions):
            if self._bots_role_of(sid, cfg) == role:
                return json.dumps({"success": True, "message": _(f'"{label}" is already awake', f"「{label}」已經醒著"),
                                   "details": {"sid": sid, "existed": True}})
        roster = self._agent_roster_config(cfg)
        cmd = (roster.get(role) or {}).get("cmd") or entry.get("cmd") or "claude"
        sid = self.new_session(cmd, 200, 50, source="bot", handoff=False)
        self.rename_session(sid, label, manual=True)
        self._bots_bind(sid, role)
        bots.ensure_dirs(role)
        if bots.is_memory_bot(entry):
            threading.Thread(target=self._bots_wake, args=(sid, role), daemon=True,
                             name=f"bot-wake-{sid}").start()
        _dlog("bots", f"open role={role!r} sid={sid}")
        return json.dumps({"success": True, "message": _(f'Waking "{label}"', f"叫醒「{label}」"), "details": {"sid": sid}},
                          ensure_ascii=False)

    def _bots_wake(self, sid: str, role: str):
        if not self._wait_until_ready(sid, timeout=90):
            _dlog("bots", f"wake {sid}: never became ready; brief not sent")
            return
        raw = _cfg().get("agent_roster") or {}
        entry = raw.get(role)
        s = self.sessions.get(sid)
        if not s or not bots.is_bot(entry):
            return
        s._startup_trust_pending = False
        s._init_pending = False                 # the brief replaces the generic init prompt
        self._send_text_to_session(s, self._bots_brief(role, entry, raw), submit=True)

    def _bots_first_prompt(self, sid: str, role: str, prompt: str) -> str:
        """delegate (and A2A to a sleeping bot) just opened this role's tab: put the
        bot's brief in front of the task, in the same message, and bind the tab."""
        try:
            if not self._bots_enabled():
                return prompt
            raw = _cfg().get("agent_roster") or {}
            entry = raw.get(role)
            if not bots.is_memory_bot(entry):
                return prompt
            self._bots_bind(sid, role)
            bots.ensure_dirs(role)
            s = self.sessions.get(sid)
            if s:
                s._init_pending = False
            return self._bots_brief(role, entry, raw, with_ack=False) + "\n\n---\n\n" + prompt
        except Exception:
            _swallow("bots:first_prompt")
            return prompt

    # ── closing ─────────────────────────────────────────────────────────
    def bots_close(self, sid: str, mode: str = "save") -> str:
        if sid not in self.sessions:
            return json.dumps({"success": False, "message": _(f"No tab {sid}", f"沒有 {sid} 這個分頁")})
        role = self._bots_role_of(sid)
        entry = (_cfg().get("agent_roster") or {}).get(role)
        if mode == "skip" or not bots.is_memory_bot(entry):
            self.close_session(sid, reason="bot-skip")
            return json.dumps({"success": True, "message": _("Closed", "已關閉")})
        self._bots_start_close(sid, role)
        return json.dumps({"success": True, "message": _("Writing its memory; the tab closes when that is done",
                                                          "正在寫記憶，寫完會自動關閉")})

    def _bots_divert_close(self, sid: str, reason: str) -> bool:
        """close_session hook: a memory bot closed from sfctl, Telegram or the idle
        reaper writes its memory first. True = the flow took over the close."""
        try:
            if reason in SELF_REASONS or sid not in self.sessions or not self._bots_enabled():
                return False
            role = self._bots_role_of(sid)
            entry = (_cfg().get("agent_roster") or {}).get(role)
            if not bots.is_memory_bot(entry):
                return False
            self._bots_start_close(sid, role)
            return True
        except Exception:
            _swallow("bots:divert_close")
            return False

    def _bots_start_close(self, sid: str, role: str):
        with _CLOSING_LOCK:
            if sid in _CLOSING:
                return
            _CLOSING[sid] = {"role": role, "phase": "waiting", "since": time.time()}
        threading.Thread(target=self._bots_close_flow, args=(sid, role), daemon=True,
                         name=f"bot-close-{sid}").start()
        self._bots_notify_ui()

    def _bots_busy(self, sid: str) -> bool:
        return self._agent_state_for_list(sid) == "working"

    def _bots_close_flow(self, sid: str, role: str):
        written = False
        path = None
        try:
            deadline = time.monotonic() + BUSY_WAIT_SEC
            while self._bots_busy(sid) and time.monotonic() < deadline and sid in self.sessions:
                time.sleep(1.5)
            s = self.sessions.get(sid)
            if not s:
                return
            raw = _cfg().get("agent_roster") or {}
            entry = raw.get(role) or {}
            bots.ensure_dirs(role)
            path = bots.new_session_file(role, sid)
            with _CLOSING_LOCK:
                if sid in _CLOSING:
                    _CLOSING[sid]["phase"] = "writing"
            self._bots_notify_ui()
            if self._wait_until_ready(sid, timeout=30):
                s._startup_trust_pending = False
                self._send_text_to_session(
                    s, bots.summary_prompt(role, entry, path, bots.memory_file(role)), submit=True)
                deadline = time.monotonic() + SUMMARY_WAIT_SEC
                while time.monotonic() < deadline and sid in self.sessions:
                    if path.exists() and path.stat().st_size > 0:
                        written = True
                        time.sleep(3)       # MEMORY.md is usually written right after
                        break
                    time.sleep(1.0)
            if not written and sid in self.sessions:
                why = (_("the bot did not write a summary in time", "bot 沒有在時限內寫摘要")
                       if self._wait_until_ready(sid, timeout=1)
                       else _("the tab never came back to its prompt", "分頁沒有回到可輸入的狀態"))
                path.write_text(bots.fallback_summary(bots.label_of(role, entry), sid,
                                                      self._bots_capture(s), why), encoding="utf-8")
        except Exception:
            _swallow(f"bots:close_flow:{sid}")
        finally:
            _dlog("bots", f"close sid={sid} role={role!r} written={written} file={path}")
            with _CLOSING_LOCK:
                _CLOSING.pop(sid, None)
            if sid in self.sessions:
                self.close_session(sid, reason="bot-memory", summary_path=str(path or ""))
            self._bots_notify_ui()

    def _bots_capture(self, s) -> str:
        text = ""
        if getattr(s, "_tmux_name", None):
            try:
                r = subprocess.run(["tmux", "capture-pane", "-p", "-J", "-t", s._tmux_name, "-S", "-300"],
                                   capture_output=True, text=True, timeout=3)
                if r.returncode == 0:
                    text = r.stdout
            except Exception:
                _swallow("bots:capture")
        if not text:
            with s.lock:
                text = bytes(s._recent).decode("utf-8", errors="replace")
        try:
            import agent_status
            text = agent_status.redact(text)
        except Exception:
            _swallow("bots:redact")
        return text[-20000:]

    # ── bots that live outside ShellFrame ───────────────────────────────
    def _bots_external_target(self, role: str):
        e = (_cfg().get("agent_roster") or {}).get(role)
        return e["bot"]["external"] if bots.is_external(e) else None

    def _bots_external_label(self, role: str) -> str:
        e = (_cfg().get("agent_roster") or {}).get(role)
        return bots.label_of(role, e) if bots.is_external(e) else ""

    def _bots_external_deliver(self, role: str, payload: dict) -> dict:
        """POST one A2A message to an external bot's webhook. Never raises."""
        ext = self._bots_external_target(role)
        label = self._bots_external_label(role) or role
        if not ext:
            return {"success": False, "message": _(f'"{label}" is not an external bot', f"「{label}」不是外部 bot")}
        if not ext.get("url"):
            return {"success": False,
                    "message": _(f'"{label}" lives outside ShellBot and has no webhook yet '
                                 "(click it in the sidebar → Edit, paste the webhook)",
                                 f"「{label}」住在 ShellBot 外面，但還沒設定 webhook（在側欄點它 → 編輯，貼上 webhook）")}
        body = bots.external_payload(
            kind=payload.get("kind") or "send", msg_id=payload.get("id") or "",
            text=payload.get("text") or "", from_label=payload.get("from_label") or "",
            from_sid=payload.get("from_sid") or "", to_role=role, machine=socket.gethostname())
        cfg = {"url": ext.get("url") or "", "auth": ext.get("auth") or ""}
        ok, info = sf_notify.send(body, cfg, timeout=8.0)
        if ok:
            return {"success": True, "message": _(f"Sent to {label}'s webhook", f"已送到「{label}」的 webhook")}
        why = sf_notify.scrub(info, cfg)
        return {"success": False, "message": _(f"Could not reach {label}: {why}", f"送不到「{label}」：{why}")}

    def bots_external_info(self, role: str) -> str:
        cfg = _cfg()
        e = (cfg.get("agent_roster") or {}).get(role)
        if not bots.is_external(e):
            return json.dumps({"success": False, "message": _(f'"{role}" is not an external bot', f"「{role}」不是外部 bot")})
        label = bots.label_of(role, e)
        log = []
        try:
            import agent_link
            for x in agent_link.history(200):
                if role in (x.get("target"), x.get("to_role")) or x.get("from_sid") == f"ext:{role}" \
                        or x.get("from_label") == label:
                    log.append({k: x.get(k) for k in ("kind", "from_label", "from_sid", "target", "text", "reason")})
        except Exception:
            _swallow("bots:external_log")
        return json.dumps({"success": True, "routine": bots.routine_instruction(role, label),
                           "log": log[-20:][::-1]}, ensure_ascii=False)

    def bots_external_send(self, role: str, text: str) -> str:
        """The user writes to an external bot from the panel: straight to its webhook,
        recorded in the A2A log so both directions read as one thread."""
        text = (text or "").strip()
        if not text:
            return json.dumps({"success": False, "message": _("Nothing to send", "內容是空的")})
        import uuid
        import agent_link
        msg_id = uuid.uuid4().hex[:12]
        agent_link.remember_outbound(msg_id, origin_sid="ui", target=role,
                                     from_label=_("the user (ShellBot)", "使用者（ShellBot）"))
        res = self._bots_external_deliver(role, {"kind": "send", "id": msg_id, "text": text,
                                                 "from_label": _("the user (ShellBot)", "使用者（ShellBot）"), "from_sid": "ui"})
        try:
            agent_link.record({"kind": "message" if res.get("success") else "failed",
                               "reason": "" if res.get("success") else res.get("message"),
                               "text": text[:200], "target": role, "msg_id": msg_id,
                               "from_sid": "ui", "from_label": _("the user (ShellBot)", "使用者（ShellBot）")})
        except Exception:
            _swallow("bots:external_send_log")
        return json.dumps(res, ensure_ascii=False)
