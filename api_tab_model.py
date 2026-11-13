"""Api mixin — the model chip on a running AI tab (web/tabmodel.js): which model the tab runs, what it can be
switched to, and the switch itself, done by typing the CLI's own command into the tab.

js_api: tab_model_info, tab_model_set, tab_model_cancel, tab_model_dismiss, tab_model_save_bot.

The rules (what each CLI takes, how its screen reads, the keys to press) are in tab_model.py and are written
down in docs/tab-model.md. This adds what a tab needs around them:
  * the line that opens the CLI's menu goes in through tab_delivery, so a tab in the middle of a turn, on a
    question, or with something typed in its box is waited for, never cut into; while the menu is driven the
    tab is held (tab_delivery.HOLD) so nothing else is typed into it;
  * the answer to "what does this tab run" is what the status monitor reads, except right after a switch: the
    CLI's files do not change until its next turn (Claude Code's transcript has no new message), so the switch's
    own result is shown until the monitor reads something different;
  * a switch is a job the page can ask about (`job` in tab_model_info): waiting, switching, ok, failed.
"""

import json
import threading
import time
import uuid

import agent_model
import agent_status
import tab_delivery
import tab_model
import sf_lang
from api_host import main
from sf_log import _dlog, _swallow

_ = sf_lang.pick
_LOCK = threading.Lock()
_JOBS = {}        # sid -> the latest switch of that tab
_SHOWN = {}       # sid -> {"name", "effort", "sig"} what a finished switch reported, until the monitor reads something else
JOB_KEEP = 180.0  # seconds a finished switch stays visible to the page
WAIT_MAX = 600.0  # seconds a switch waits for the tab to be free
_HOOKED = []


class _HostIO:
    """What tab_model.drive needs from the app: the tab's screen, its keyboard and the clock."""

    def __init__(self, api, sid: str):
        self.api, self.sid = api, sid

    def lines(self) -> list:
        return tab_delivery.visible_text(self.api, self.sid).splitlines()

    def send(self, *keys):
        s = self.api.sessions.get(self.sid)
        if not s:
            return
        for k in keys:
            s.write(k["text"] if isinstance(k, dict) else tab_model.KEYS[k])

    @staticmethod
    def sleep(sec: float):
        time.sleep(sec)

    @staticmethod
    def now() -> float:
        return time.time()

    def probe(self):
        s = self.api.sessions.get(self.sid)
        return agent_status.detect_model_info(self.api._worker_ctx(self.sid, s)) if s else None


def _on_delivered(sid: str, what: str, at: float):
    """tab_delivery typed the opening line of a switch: drive its menu off this thread."""
    if not what.startswith("model:"):
        return
    with _LOCK:
        job = _JOBS.get(sid)
    if job and job["id"] == what[6:] and job.get("api"):
        threading.Thread(target=job["api"]._tm_run, args=(sid, job["id"]), daemon=True, name=f"tab-model-{sid}").start()


class TabModelApiMixin:
    # ── what the chip shows ──────────────────────────────────────────────
    def _tm_current(self, sid: str):
        """{"name", "effort", "from"} the tab runs on, or None (the page says "unknown")."""
        det = None
        try:
            snap = self._agent_status_snapshot(sid)
            mi = ((snap[0] if isinstance(snap, tuple) else snap) or {}).get("model")
            if isinstance(mi, dict) and mi.get("name"):
                det = {"name": str(mi["name"]), "effort": str(mi.get("effort") or "") or None}
        except Exception:
            _swallow("tab_model._tm_current")
        sig = f"{det['name']}|{det['effort'] or ''}" if det else ""
        with _LOCK:
            o = _SHOWN.get(sid)
            if o and o["sig"] != sig:
                _SHOWN.pop(sid, None)             # the monitor read something new: that is the truth again
                o = None
        if o:
            return {"name": o["name"], "effort": o["effort"], "from": "switch"}
        return dict(det, **{"from": "status"}) if det else None

    @staticmethod
    def _tm_key_of(models: list, name: str) -> str:
        """The key of the list's model that `name` (what the tab reports, e.g. "Opus 5.5") is: the longest label inside it."""
        n = tab_model.norm(name)
        hit = [c for c in models if c["key"] and tab_model.norm(c["label"]) and tab_model.norm(c["label"]) in n]
        return max(hit, key=lambda c: len(tab_model.norm(c["label"])))["key"] if hit else ""

    def _tm_job_view(self, sid: str):
        with _LOCK:
            j = _JOBS.get(sid)
            if j and j["state"] in ("ok", "failed", "unconfirmed") and time.time() - j["done_at"] > JOB_KEEP:
                _JOBS.pop(sid, None)
                j = None
            return {k: v for k, v in j.items() if k not in ("api", "item", "done_at")} if j else None

    def tab_model_info(self, sid: str) -> str:
        """{"success", "ai", "cli", "cli_name", "in_session", "restart_note", "current": {"name", "effort", "from"} | null,
        "current_key", "models": [{"key", "label"}], "custom", "efforts": [{"key", "label"}], "persists", "unverified",
        "bot": {"role", "label", "can_save"} | null, "job": {...} | null}. The list is agent_model's table; no other list is kept."""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "code": "no_tab"})
        cmd = str(getattr(s, "cmd", "") or "")
        sp = tab_model.spec_of(cmd)
        if not sp["name"]:
            return json.dumps({"success": True, "ai": False})
        zh = _("en", "zh") == "zh"
        models = tab_model.models_for(cmd, zh)
        cur = self._tm_current(sid)
        bot = None
        try:
            role = self._bots_role_of(sid)
            if role:
                entry = (main.load_config().get("agent_roster") or {}).get(role) or {}
                bot = {"role": role, "label": str(entry.get("label") or role),
                       "can_save": bool(agent_model.provider_of(cmd))}
        except Exception:
            _swallow("tab_model.info bot")
        return json.dumps({
            "success": True, "ai": True, "sid": sid, "cli": sp["kind"], "cli_name": sp["name"],
            "in_session": sp["how"] != "restart",
            "restart_note": "" if sp["how"] != "restart" else tab_model.plan(cmd, "x", None)["message"],
            "current": cur, "current_key": self._tm_key_of(models, cur["name"]) if cur else "",
            "models": models, "custom": tab_model.custom_ok(cmd), "efforts": tab_model.efforts_for(cmd, zh),
            "persists": sp["persists"], "unverified": not sp["verified"], "bot": bot, "job": self._tm_job_view(sid)},
            ensure_ascii=False)

    # ── the switch ───────────────────────────────────────────────────────
    def tab_model_set(self, sid: str, req_json: str = "{}") -> str:
        """Switch the tab: {"model": key | null (keep), "effort": level | null, "custom": bool (a typed name),
        "draft": "wait" | "clear" (what to do with text in the tab's box), "unreadable_ok": bool}.
        Answers at once: {"success": True, "state": "waiting" | "switching", "id"} and the rest through
        tab_model_info's `job`; or {"success": False, "code", "message"} (`draft`: text is in the box and the
        person has not said what to do, `unreadable`: the box cannot be read, so the person is asked first)."""
        try:
            req = json.loads(req_json or "{}")
        except ValueError:
            req = {}
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "code": "no_tab", "message": _("No such tab", "找不到這個分頁")}, ensure_ascii=False)
        cmd = str(getattr(s, "cmd", "") or "")
        p = tab_model.plan(cmd, req.get("model"), req.get("effort"), bool(req.get("custom")))
        if not p["ok"]:
            return json.dumps({"success": False, "code": p["code"], "message": p["message"]}, ensure_ascii=False)
        with _LOCK:
            j = _JOBS.get(sid)
            if j and j["state"] in ("waiting", "switching"):
                return json.dumps({"success": False, "code": "busy", "message": _(
                    "A model switch is already under way for this tab", "這個分頁已經有一個換模型在進行")}, ensure_ascii=False)
        kind, text = tab_delivery.composer_state(tab_delivery.capture(self, sid))
        draft = str(req.get("draft") or "")
        if kind == "draft" and draft not in ("wait", "clear"):
            return json.dumps({"success": False, "code": "draft", "draft": text[:200], "message": _(
                "There is text in this tab's input box that you have not sent.", "這個分頁的輸入框裡有你還沒送出的字。")}, ensure_ascii=False)
        if kind == "unknown" and not req.get("unreadable_ok"):
            return json.dumps({"success": False, "code": "unreadable", "message": _(
                "ShellBot cannot read this tab's input box, so it does not know if something is typed in it. Switching types into the tab.",
                "ShellBot 讀不到這個分頁的輸入框，不知道裡面有沒有你打到一半的字。換模型會在這個分頁裡打字。")}, ensure_ascii=False)
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "state": "waiting", "model": p["model"], "effort": p["effort"], "kind": p["kind"], "message": "",
               "code": "", "name": "", "effort_now": None, "reason": "", "at": time.time(), "done_at": 0.0, "api": self, "item": ""}
        with _LOCK:
            _JOBS[sid] = job
        self._tm_hook()
        r = tab_delivery.deliver(
            self, sid, p["opener"], submit=bool(p["opener"]), what=f"model:{jid}", label=sid, max_wait=WAIT_MAX,
            clear_draft=(kind == "draft" and draft == "clear"),
            on_timeout=lambda outcome, why: self._tm_end(sid, jid, False, outcome, _(
                f"Not switched: {why}", f"沒有換：{why}")))
        if not r.get("success"):
            self._tm_end(sid, jid, False, r.get("code") or "failed", r.get("message") or "")
            return json.dumps({"success": False, "code": r.get("code") or "failed", "message": r.get("message") or ""}, ensure_ascii=False)
        if r.get("queued"):
            with _LOCK:
                job["item"], job["reason"] = r.get("id", ""), r.get("reason", "")
            return json.dumps({"success": True, "state": "waiting", "id": jid, "reason": job["reason"]}, ensure_ascii=False)
        return json.dumps({"success": True, "state": "switching", "id": jid}, ensure_ascii=False)

    @staticmethod
    def _tm_hook():
        if not _HOOKED:
            _HOOKED.append(1)
            tab_delivery.ON_DELIVERED.append(_on_delivered)

    def _tm_end(self, sid: str, jid: str, ok: bool, code: str, message: str, **kw):
        with _LOCK:
            j = _JOBS.get(sid)
            if not j or j["id"] != jid:
                return
            j.update(kw, state="ok" if ok else "failed", code=code, message=message, done_at=time.time())
            if kw.get("unconfirmed"):
                j["state"] = "unconfirmed"
        _dlog("tabmodel", f"{sid}: {'ok' if ok else 'failed'} {code} {message}")

    def _tm_run(self, sid: str, jid: str):
        """Drive the menu the opening line brought up, read what the CLI said, record it. The tab is held meanwhile."""
        with _LOCK:
            j = _JOBS.get(sid)
            if not j or j["id"] != jid or j["state"] not in ("waiting", "switching"):
                return
            j["state"] = "switching"
            kind, model, effort = j["kind"], j["model"], j["effort"]
        tab_delivery.HOLD[sid] = "model"
        try:
            res = tab_model.drive(kind, _HostIO(self, sid), model, effort)
        except Exception as e:
            res = {"ok": False, "code": "error", "message": f"{e!r}", "name": "", "effort": None}
        finally:
            tab_delivery.HOLD.pop(sid, None)
        extra = {"name": res.get("name") or "", "effort_now": res.get("effort")}
        if res.get("unconfirmed"):
            extra["unconfirmed"] = True
        if res.get("ok") and res.get("name") and not res.get("unconfirmed"):
            try:                                   # what the monitor reads now: the switch's answer stands until it reads something else
                snap = self._agent_status_snapshot(sid)
                mi = ((snap[0] if isinstance(snap, tuple) else snap) or {}).get("model")
                sig = f"{mi['name']}|{str(mi.get('effort') or '')}" if isinstance(mi, dict) and mi.get("name") else ""
            except Exception:
                sig = ""
            with _LOCK:
                _SHOWN[sid] = {"name": res["name"], "effort": res.get("effort"), "sig": sig}
        self._tm_end(sid, jid, bool(res.get("ok")), res.get("code") or "", res.get("message") or "", **extra)

    def tab_model_cancel(self, sid: str) -> str:
        """Drop a switch that is still waiting for the tab to be free."""
        with _LOCK:
            j = _JOBS.get(sid)
            item = j["item"] if j and j["state"] == "waiting" else ""
            jid = j["id"] if j else ""
        if not item or not tab_delivery.cancel(sid, item):
            return json.dumps({"success": False, "code": "not_waiting", "message": _(
                "It is not waiting any more", "它已經不在等了")}, ensure_ascii=False)
        self._tm_end(sid, jid, False, "cancelled", _("Cancelled", "已取消"))
        return json.dumps({"success": True})

    def tab_model_dismiss(self, sid: str) -> str:
        """The page has shown a finished switch: forget it."""
        with _LOCK:
            j = _JOBS.get(sid)
            if j and j["state"] in ("ok", "failed", "unconfirmed"):
                _JOBS.pop(sid, None)
        return json.dumps({"success": True})

    def tab_model_save_bot(self, sid: str, model: str = "") -> str:
        """"Also keep this model as the bot's own": the bot of this tab takes it from its next wake on (bots_switch,
        next_wake). Only what the person says yes to; the switch itself saves nothing."""
        role = self._bots_role_of(sid)
        if not role:
            return json.dumps({"success": False, "code": "no_bot", "message": _("This tab is not a bot", "這個分頁不是 bot")}, ensure_ascii=False)
        return self.bots_switch(role, "", model, "next_wake")
