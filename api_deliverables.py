"""Api mixin: deliverables (deliverables.py, office_deliverables.py), the host side.

  sfctl deliver add <path-or-url> --title "…" [--note "…"] [--task N]   register what you hand the person
  sfctl deliver list [--state new|kept|taken|candidate] [--owner NAME] [--json]
  sfctl deliver show N

Who registers is the calling tab (SF_SID), as `sfctl task` resolves it (api_tasks._tasks_actor): the bot the tab is,
else the tab itself. A tab's desk is "sid + the time it was opened" (a tab id is used again by a later tab; the tab's
own birth time is tmux's `session_created`, which also survives a restart of the app, else the spawn time), so a new
tab never inherits an old tab's papers and a closed tab's papers go to 「已關閉的分頁」. Keep, take, accept and dismiss
are the person's buttons only (the page's `deliverables_act`, the popup's actions): no sfctl verb, no HTTP route and no
Frame Link route reaches them, and nothing here is sent to a paired computer.

Auto-pickup: at the end of a turn, the status monitor's transition (the same one the notifications use, a tracker of
its own because the phone push returns early without phones) starts a daemon thread that reads the tab's transcript
tail once, takes the last assistant text of the turn and looks for files it names (deliverables.find_candidates).
No polling, no screen read, no model call: one transcript tail read and a few `stat`s per finished turn.

js_api: `deliverables_act(deliverable_id, action, args_json, lang)` (the right-hand list's 打開 / 先放著 / 拿走 / 收下 / 忽略);
the counts and the list come with `tasks_overview` (api_tasks): one more small read in the same call.
"""
import hashlib
import json
import os
import threading

import agent_status
import bot_memory
import bots
import deliverables as dv
import office_deliverables
import office_objects
import sf_lang
import sf_notify
import state_store as ss
import tmux_sessions
from api_host import main
from sf_log import _dlog, _swallow

_ = sf_lang.pick
TURN_MEMO_MAX = 400


class DeliverablesApiMixin:
    # ── who a tab is ──────────────────────────────────────────────────────
    def _deliv_opened(self, sid: str, s, details=None) -> str:
        """When the tab was opened, as a stable string: tmux's session_created (the same after a restart of the app, and a
        new tab with a used id has a new one), else the spawn time. Cached per tab object; `details` is tmux's answer, when
        the caller asked for several tabs at once."""
        cache = self.__dict__.setdefault("_deliv_open_cache", {})
        hit = cache.get(sid)
        if hit and hit[0] == id(s):
            return hit[1]
        opened = ""
        name = getattr(s, "_tmux_name", None)
        if name and not main.IS_WIN:
            try:
                if details is None:
                    details = tmux_sessions.session_details(main._tmux_bin())
                for d in details:
                    if d["name"] == name and d.get("created"):
                        opened = str(d["created"])
                        break
            except Exception:
                _swallow("deliv:opened")
        if not opened:
            opened = str(int(float(getattr(s, "_spawn_ts", 0) or 0)))
        cache[sid] = (id(s), opened)
        return opened

    def _deliv_live_tabs(self) -> dict:
        """{sid: opened} of the tabs that are open (one tmux call for all the tabs not seen before)."""
        out, details = {}, None
        cache = self.__dict__.get("_deliv_open_cache", {})
        for sid, s in list(self.sessions.items()):
            if not getattr(s, "alive", True):
                continue
            if details is None and getattr(s, "_tmux_name", None) and not (cache.get(sid) and cache[sid][0] == id(s)):
                try:
                    details = tmux_sessions.session_details(main._tmux_bin()) if not main.IS_WIN else []
                except Exception:
                    _swallow("deliv:details")
                    details = []
            out[sid] = self._deliv_opened(sid, s, details)
        return out

    def _deliv_tab_labels(self) -> dict:
        return {sid: (getattr(s, "_custom_label", None) or sid) for sid, s in list(self.sessions.items())}

    def _deliv_owner(self, sid: str):
        """(owner_kind, owner_key, label) of a tab: the bot it is, else the tab. None for no such tab."""
        s = self.sessions.get(sid)
        if s is None:
            return None
        try:
            role = self._bots_role_of(sid)
        except Exception:
            _swallow("deliv:role")
            role = ""
        if role:
            entry = (main.load_config().get("agent_roster") or {}).get(role) or {}
            return "bot", role, bots.label_of(role, entry)
        return "tab", f"{sid}@{self._deliv_opened(sid, s)}", getattr(s, "_custom_label", None) or sid

    def _deliv_resolve_owner(self, name: str):
        """`--owner NAME`: a bot (by any of its names) or a tab (by title or sid): (kind, key) or None."""
        res = self._resolve_name(str(name or ""), prefix=False)
        if not res.get("ok"):
            return None
        if res.get("role"):
            return "bot", res["role"]
        sid = res.get("sid")
        s = self.sessions.get(sid) if sid else None
        return ("tab", f"{sid}@{self._deliv_opened(sid, s)}") if s is not None else None

    # ── sfctl deliver ─────────────────────────────────────────────────────
    @staticmethod
    def _deliv_line(r) -> str:
        where = r["ref"] if r["kind"] == "url" else dv.mask_line(r["ref"], 200, strict=False)
        return f"#{r['id']} {r['state']} {r['type']} {r['title']}  {where}" + (f"  — {r['note']}" if r["note"] else "")

    def _deliv_sfctl(self, args: dict) -> dict:
        args = args or {}
        action = str(args.get("action") or "")
        sid = str(args.get("from_sid") or "")
        try:
            return self._deliv_do(action, args, sid)
        except ss.Unavailable as e:
            return {"success": False, "message": _(e.en, e.zh), "details": {"code": e.code}}
        except ss.NotFound as e:
            return {"success": False, "message": _(f"No task or deliverable #{e.args[0]}.", f"沒有 #{e.args[0]} 這一筆。"),
                    "details": {"code": "not_found"}}
        except ValueError as e:
            return {"success": False, "message": _(f"Not accepted: {e}", f"不接受：{e}"), "details": {"code": "invalid"}}

    def _deliv_do(self, action: str, a: dict, sid: str) -> dict:
        if action == "add":
            owner = self._deliv_owner(sid)
            if owner is None:
                raise ValueError(_("run it from a ShellBot tab (it needs to know whose desk)", "要在 ShellBot 的分頁裡執行（要知道放在誰的桌上）"))
            ref = str(a.get("ref") or "").strip()
            r = dv.add(owner[0], owner[1], owner[2], ref, title=str(a.get("title") or ""), note=str(a.get("note") or ""),
                       task_id=a.get("task") or None)
            flagged = _(" Looks like it holds a key: it is not previewed and opening it asks the person first.",
                        " 看起來含金鑰：不會預覽，使用者打開前會再確認。") if r["flag"] else ""
            return {"success": True, "message": _(f"Registered #{r['id']} on {owner[2]}'s desk: ", f"已放上 {owner[2]} 的桌子 #{r['id']}：")
                    + r["title"] + flagged, "details": {"id": r["id"], "state": r["state"], "type": r["type"], "flag": r["flag"]}}
        if action == "list":
            owner = self._deliv_resolve_owner(a["owner"]) if a.get("owner") else None
            if a.get("owner") and owner is None:
                raise ValueError(_(f"no bot or tab called {a['owner']}", f"找不到叫 {a['owner']} 的 bot 或分頁"))
            if owner is None and sid:
                me = self._deliv_owner(sid)
                owner = (me[0], me[1]) if me else None
            st = str(a.get("state") or "")
            if st and st not in dv.STATES:
                raise ValueError(f"--state {st}")
            rows = dv.listing(states=(st,) if st else ("new", "kept"), owner=owner, limit=int(a.get("limit") or 50))
            items = [dict(dv._public(r), id=r["id"], ref=r["ref"] if r["kind"] == "url" else dv.mask_line(r["ref"], 300, strict=False))
                     for r in rows]
            return {"success": True, "message": "\n".join(self._deliv_line(r) for r in rows) or _("(nothing)", "（沒有）"),
                    "details": {"items": items}}
        if action == "show":
            try:
                n = int(str(a.get("id") or "").lstrip("#"))
            except ValueError:
                raise ValueError(_("a number, e.g. 12", "要給編號，例如 12")) from None
            r = dv.get(n)
            return {"success": True, "message": self._deliv_line(r),
                    "details": dict(dv._public(r), id=r["id"], ref=r["ref"] if r["kind"] == "url" else dv.mask_line(r["ref"], 300, strict=False))}
        raise ValueError(_(f"unknown action: {action}", f"不認得的動作：{action}"))

    # ── the page ──────────────────────────────────────────────────────────
    def _deliv_overview(self):
        """Counts per person, the rail's list and the candidates, for tasks_overview (one more small read); None when the
        store cannot be used."""
        try:
            return dv.overview(self._deliv_live_tabs())
        except ss.Unavailable:
            return None
        except Exception:
            _swallow("deliv:overview")
            return None

    def _deliv_ctx(self, lang: str = ""):
        return office_objects.Ctx(
            lang, live_tabs=self._deliv_live_tabs, tab_labels=self._deliv_tab_labels,
            roster=lambda: (main.load_config().get("agent_roster") or {}) if self._bots_enabled() else {},
            task_note=lambda tid, text: ss.note(int(tid), text, actor=ss.USER))

    def deliverables_act(self, deliverable_id, action: str, args_json: str = "{}", lang: str = "") -> str:
        """The right-hand list's buttons (the person's own hand): open, keep, take, accept, dismiss, dismiss_all. JSON like
        office_object_action's: {success, ok, message, confirm?}; the file or link is opened here, the page never gets the path."""
        lang = office_objects.lang_of(lang)
        if action not in ("open", "keep", "take", "accept", "dismiss", "dismiss_all"):
            return json.dumps({"success": False, "message": office_objects.words("bad_action", lang)}, ensure_ascii=False)
        try:
            a = json.loads(args_json or "{}")
            a = a if isinstance(a, dict) else {}
        except ValueError:
            a = {}
        try:
            res = office_deliverables.Deliverables().action(self._deliv_ctx(lang), str(deliverable_id), action, a)
            out = dict(office_objects.clean_action(res, trusted=True), success=True)
        except ss.Unavailable as e:
            return json.dumps({"success": False, "message": _(e.en, e.zh)}, ensure_ascii=False)
        except Exception:
            _swallow("deliv:act")
            return json.dumps({"success": False, "message": office_objects.words("unavailable", lang)}, ensure_ascii=False)
        self._oo_open_result(out, lang)
        return json.dumps(out, ensure_ascii=False)

    # ── auto-pickup, at the end of a turn ───────────────────────────────────
    def _deliv_observe(self, out: dict) -> None:
        """Once per status-monitor pass (api_status._notify_transitions): a tab whose turn just ended is read once.
        Never raises, never waits: the read is on a thread of its own."""
        try:
            tracker = self.__dict__.setdefault("_deliv_tracker", sf_notify.TransitionTracker())
            tracker.prune(out.keys())
            for sid, res in list(out.items()):
                if tracker.observe(sid, res if isinstance(res, dict) else {}) == "DONE":
                    threading.Thread(target=self._deliv_turn_end, args=(sid,), daemon=True, name=f"deliv-turn-{sid}").start()
        except Exception:
            _swallow("deliv:observe")

    def _deliv_turn_end(self, sid: str) -> None:
        try:
            s = self.sessions.get(sid)
            path = agent_status.resolve_transcript(self._worker_ctx(sid, s)) if s else None
            evs = agent_status._read_tail_events(path)[1] if path else []
            if bot_memory.ended_by_request(evs):
                return                                          # a bot saving its memory: nothing was handed over
            texts = [e.get("text") or "" for e in evs if e.get("kind") == "assistant_text"]
            text = texts[-1] if texts else ""
            if not text.strip():
                return
            memo = self.__dict__.setdefault("_deliv_memo", {})
            key = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
            if memo.get(sid) == key:
                return                                          # this reply was read already
            memo[sid] = key
            if len(memo) > TURN_MEMO_MAX:
                memo.pop(next(iter(memo)))
            found = dv.find_candidates(text)
            if not (found["candidates"] or found["skipped"]):
                return
            owner = self._deliv_owner(sid)
            if owner is None:
                return
            n = dv.record_candidates(owner[0], owner[1], owner[2], found)
            if n:
                _dlog("deliv", f"{sid}: {n} possible deliverable(s) offered")
        except ss.Unavailable:
            pass
        except Exception:
            _swallow(f"deliv:turn_end:{sid}")
