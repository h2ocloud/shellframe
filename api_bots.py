"""Api mixin — bots (settings.experimental_bots). Rules live in bots.py.

js_api: bots_state, bots_set_avatar, bots_set_primary, bots_bind_tab, bots_save, bots_delete, bots_open,
        bots_close, bots_skill_catalog, bots_open_folder, bots_external_info, bots_external_send.
Hooks main.py and a2a_host call: _bots_first_prompt (delegate opened a bot's tab),
_bots_divert_close (close_session for a bot tab), _bots_external_target /
_bots_external_deliver / _bots_external_label (A2A to and from a bot that lives
outside ShellFrame).
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

import bots
import sf_lang
import sf_notify
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


class BotsApiMixin:
    # ── reading ─────────────────────────────────────────────────────────
    def _bots_enabled(self) -> bool:
        try:
            return (_cfg().get("settings", {}) or {}).get("experimental_bots", False) is True
        except Exception:
            return False

    def _bots_role_of(self, sid: str, cfg: dict = None) -> str:
        """The bot a tab belongs to: an explicit binding, else a tab whose label is a bot's label."""
        cfg = cfg or _cfg()
        roster = cfg.get("agent_roster") or {}
        row = (cfg.get("bots_sessions") or {}).get(sid) or {}
        if row.get("plain"):
            return ""                   # the user said this tab is not a bot, whatever its name
        bound = row.get("bot") or ""
        if bound and bots.is_bot(roster.get(bound)):
            return bound
        s = self.sessions.get(sid)
        label = (getattr(s, "_custom_label", None) or "") if s else ""
        if label:
            for role, e in roster.items():
                if bots.is_bot(e) and not bots.is_external(e) and bots.label_of(role, e) == label:
                    return role
        return ""

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

    def _bots_save_for_sfctl(self, args: dict) -> dict:
        """`sfctl bots-save <role> '<json>'`: the bot editor's save, for scripts and
        agents. The editor sends the whole form; a script names only what it
        changes, so fields left out keep their value (skills, primary, memory…)."""
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
        entry = (_cfg().get("agent_roster") or {}).get(role) or {}
        prev = entry.get("bot") if isinstance(entry.get("bot"), dict) else {}
        merged = {k: prev[k] for k in ("avatar", "skills", "memory", "primary", "external") if k in prev}
        merged.update(data)
        return json.loads(self.bots_save(role, json.dumps(merged, ensure_ascii=False)))

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
            table = cfg.get("bots_sessions") or {}
            for sid in [k for k, v in table.items() if (v or {}).get("bot") == role]:
                table.pop(sid, None)
        _update(_mut)
        if out.get("missing"):
            return json.dumps({"success": False, "message": _(f'"{role}" is not a bot', f"「{role}」不是 bot")})
        self._bots_notify_ui()
        return json.dumps({"success": True, "message": _(f'Removed "{role}" (its memory folder stays at {bots.bot_dir(role)})',
                                       f"已移除「{role}」（記憶資料夾保留在 {bots.bot_dir(role)}）")},
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
