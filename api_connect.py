"""Api mixin — connections: the page, sfctl, the phone and a bot's [[SF:RED:connect:<service>]] all
reach the connector core (connectors/, rules in docs/connectors.md) through here.

js_api: connect_list, connect_describe, connect_status, connect_start, connect_step, connect_test,
        connect_disconnect, connect_signal.
Also: connect_refresh_hourly (the status monitor calls `_connect_tick` once a pass; it starts a
one-shot thread at most once an hour), `_connect_sfctl` (sfctl connect list|status|start|test|disconnect),
`_connect_compact` (the phone's /link/bots `connections`), `_connect_line` (one sentence for a RED tag).

Every method answers a JSON string with `success`; a failure has a `code` and `message` {text, next}
(both languages: the page has its own language setting). Nothing secret is ever in an answer, a log
line or the state file: a pasted key goes straight to the system's key store.
"""

import json
import os
import subprocess
import sys
import threading
import time

import agent_status
import sf_lang
from api_host import main
from connect_tabs import TabKit
from connectors import clis as clis_mod, engine as eng_mod, health, registry as reg_mod, secrets
from connectors.adapter import Ctx
from sf_log import _dlog, _swallow

HOURLY_S = 3600.0
FIRST_TICK_S = 120.0
_LOCK = threading.Lock()


def _answer(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False, default=str)


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _open_path(path: str) -> None:
    """Show a folder in Finder / Explorer / the file manager."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["/usr/bin/open" if sys.platform == "darwin" else "xdg-open", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ConnectApiMixin:
    # ── the engine, built once with what only the app knows ────────────────
    def _connect(self):
        eng = getattr(self, "_connect_engine", None)
        if eng is None:
            with _LOCK:
                eng = getattr(self, "_connect_engine", None)
                if eng is None:
                    eng = eng_mod.Engine(clis=clis_mod.Clis(env=self._connect_cli_env, win=sys.platform == "win32"),
                                         ctx_factory=self._connect_ctx)
                    self._connect_engine = eng
        return eng

    def _connect_cli_env(self, cli: str = "claude") -> dict:
        """The environment an AI program is run with: the app's own, with the account folder a new tab of
        that program would get (so what is set up is what the tabs read), and without any key or token."""
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")}
        if cli in ("claude", "codex"):
            try:
                ref = main.ACCOUNT_MANAGER.session_refs(main.load_config()).get(cli)
                env.update(main.ACCOUNT_MANAGER.env_for(cli, ref) if ref else {})
            except Exception:
                _swallow("connect:env")
        return env

    def _connect_ctx(self, service: str, route: str) -> Ctx:
        return Ctx(service=service, route=route, cfg=main.load_config(), env=self._connect_cli_env("claude"),
                   win=sys.platform == "win32", open_tab=self._connect_open_tab, open_path=_open_path, tabs=TabKit(self))

    def _connect_open_tab(self, cmd: str, title: str) -> str:
        return TabKit(self).open(cmd, title)

    # ── js_api ─────────────────────────────────────────────────────────────
    def connect_list(self) -> str:
        """{"success", "services": [status…], "clis": [the support table], "store": {supported, store}}."""
        eng = self._connect()
        return _answer(success=True, services=eng.list(), clis=eng.clis.table("local_mcp"),
                       store=secrets.platform_support(eng.backend), broken=sorted(eng.registry.errors))

    def connect_describe(self, service: str) -> str:
        """{"success", "descriptor"}: the service's whole description (every text in both languages), which the
        page draws its cards from. Nothing in it is secret."""
        d = self._connect().registry.get(str(service))
        return _answer(success=True, descriptor=d) if d else _answer(**self._connect().snapshot(str(service)))

    def connect_status(self, service: str, args: str = "") -> str:
        """{"success", "status", "view"}. `args.refresh` asks for the health check first (it keeps its own
        ttl; `args.force` skips that)."""
        a, eng = _args(args), self._connect()
        res = eng.refresh(str(service), force=bool(a.get("force"))) if a.get("refresh") or a.get("force") else eng.snapshot(str(service))
        return _answer(**res)

    def connect_start(self, service: str, args: str = "") -> str:
        """Begin, or go on where it was left. `args.route` picks a way, `args.reconnect` starts again after
        a service needs connecting again. {"success", "status", "view"}."""
        a = _args(args)
        return _answer(**self._connect().start(str(service), route=str(a.get("route") or "") or None, reconnect=bool(a.get("reconnect"))))

    def connect_step(self, service: str, op: str, args: str = "") -> str:
        """op: check | act (args.action, args.options, a key's args.value) | done | back | route (args.route) |
        stuck (args.problem, args.from_sid: the line goes to a bot; {"sid","opened"} comes back)."""
        a, eng = _args(args), self._connect()
        res = eng.step(str(service), str(op), a)
        if op == "stuck" and res.get("success"):
            sent = self._connect_ask_bot(str(service), res.pop("message"), str(a.get("from_sid") or ""))
            res.update(sent)
        return _answer(**res)

    def connect_test(self, service: str, args: str = "") -> str:
        """Start the connection test, or (with `args.handle`) ask how it is going; `args.cancel` stops it.
        {"success", "result": {state, …}, "status", "view"}: only "it works" or which step did not."""
        a = _args(args)
        return _answer(**self._connect().test(str(service), handle=str(a.get("handle") or "") or None, cancel=bool(a.get("cancel"))))

    def connect_disconnect(self, service: str, args: str = "") -> str:
        """Take away what ShellBot added: its own `shellbot-*` entries in the AI programs and the keys it stored."""
        return _answer(**self._connect().disconnect(str(service)))

    def connect_signal(self, reason: str) -> str:
        """A bot's [[SF:RED:connect:<service>]]: the same as connect_start for that service."""
        eng = self._connect()
        sid = reg_mod.parse_connect_tag(reason, known=eng.registry.ids())
        if not sid:
            return _answer(success=False, code="unknown_service")
        return _answer(service=sid, **eng.start(sid))

    # ── asking a bot (the line comes from the engine: route, step, ticked problem; no secret) ─
    def _connect_ask_bot(self, service: str, text: str, from_sid: str) -> dict:
        """To the tab the question came from, else the primary bot if it is awake, else a new Claude tab."""
        sid, opened = self._connect_pick_bot_tab(from_sid)
        if sid and not opened:
            res = self._execute_sfctl("send", {"sid": sid, "text": text, "submit": True}) or {}
            _dlog("connect", f"ask_bot {service}: sent to {sid} ok={bool(res.get('success'))}")
            return {"sid": sid, "opened": False, "sent": bool(res.get("success"))}
        sid = self.new_session("claude", 200, 50, source="connect", handoff=False)
        name = reg_mod.pick((self._connect().registry.get(service) or {}).get("name") or service)
        self.rename_session(sid, sf_lang.pick(f"{name} help", f"{name} 求助"), manual=True)
        threading.Thread(target=self._connect_send_when_ready, args=(sid, text), daemon=True, name=f"connect-ask-{sid}").start()
        _dlog("connect", f"ask_bot {service}: opened {sid}")
        return {"sid": sid, "opened": True, "sent": True}

    def _connect_pick_bot_tab(self, from_sid: str):
        def alive_ai(sid):
            s = self.sessions.get(sid) if sid else None
            return bool(s and getattr(s, "alive", True) and agent_status.worker_kind(getattr(s, "cmd", "") or "") != "other")
        if alive_ai(from_sid):
            return from_sid, False
        try:
            _role, sid, _label = self._bots_primary_tab()
        except Exception:
            _swallow("connect:primary")
            sid = ""
        return (sid, False) if alive_ai(sid) else ("", True)

    def _connect_send_when_ready(self, sid: str, text: str):
        if not self._wait_until_ready(sid, timeout=90):
            _dlog("connect", f"{sid}: never became ready; message not sent")
            return
        s = self.sessions.get(sid)
        if s:
            s._startup_trust_pending = False
            self._send_text_to_session(s, text, submit=True)

    # ── the phone, a bot's RED tag, Telegram ───────────────────────────────
    def _connect_compact(self) -> list:
        """What /link/bots carries as `connections`: the short status of every service (a new field; the old ones are untouched)."""
        try:
            return [health.compact(s) for s in self._connect().list()]
        except Exception:
            _swallow("connect:compact")
            return []

    def _connect_line(self, reason: str, lang: str = None) -> str:
        """The one sentence for a RED question that carries a connect tag, else ''."""
        eng = self._connect()
        sid = reg_mod.parse_connect_tag(reason, known=eng.registry.ids())
        if not sid:
            return ""
        return eng_mod.connect_words(reg_mod.pick(eng.registry.get(sid)["name"], lang), lang)

    def _connect_decision(self, reason: str):
        """{"service", "name", "state"} for the phone's `decision.connect`, or None when the tag is not one."""
        eng = self._connect()
        sid = reg_mod.parse_connect_tag(reason, known=eng.registry.ids())
        if not sid:
            return None
        st = eng.status(sid)
        return {"service": sid, "name": st["name"], "state": st["state"]}

    # ── the hourly health check ────────────────────────────────────────────
    def _connect_tick(self, now: float) -> None:
        """Called by the status monitor on every pass: cheap. Once an hour (first one a couple of minutes
        after the app starts) a one-shot thread refreshes every connected service, so the monitor never waits."""
        nxt = getattr(self, "_connect_next", 0.0)
        if not nxt:
            self._connect_next = now + FIRST_TICK_S
            return
        if now < nxt or getattr(self, "_connect_busy", False):
            return
        self._connect_next, self._connect_busy = now + HOURLY_S, True
        threading.Thread(target=self._connect_hourly_run, daemon=True, name="connect-hourly").start()

    def _connect_hourly_run(self):
        try:
            self._connect_tell(self._connect().refresh_all())
        except Exception:
            _swallow("connect:hourly")
        finally:
            self._connect_busy = False

    def _connect_tell(self, out: dict) -> None:
        """Telegram hears once about each service that needs attention (and again only if it comes back to
        needing it after being fine). Quiet hours and an off bridge leave it for the next hour."""
        import bridge_home
        eng, told = self._connect(), self.__dict__.setdefault("_connect_told", set())
        now = {(a["id"], a["state"]) for a in out["attention"]}
        told.intersection_update(now)                       # one that is fine again may be told about again
        for sid, state in sorted(now - told):
            st = eng.status(sid)
            text = "🔌 " + health.words(st) + "\n" + sf_lang.pick("Open ShellBot: Settings → Connections.", "請到 ShellBot 的「設定 → 連接」。")
            if bridge_home.tell_all(self, text):
                told.add((sid, state))
        if now:
            _dlog("connect", "needs attention: " + ", ".join(sorted({i for i, _ in now})))

    def connect_refresh_hourly(self) -> str:
        """The same check on demand (what the monitor does once an hour): {"success", "checked", "attention"}."""
        return _answer(success=True, **self._connect().refresh_all(force=True))

    # ── sfctl connect list|status|start|test|disconnect <service> ──────────
    def _connect_sfctl(self, args: dict) -> dict:
        action, service = str(args.get("action") or ""), str(args.get("service") or "")
        as_json, eng = bool(args.get("json")), self._connect()
        lang = sf_lang.prompt_lang()

        def line(st):
            return health.words(st, lang)

        def done(ok, text, payload):
            return {"success": ok, "message": json.dumps(payload, ensure_ascii=False) if as_json else text}

        if action == "list":
            rows = eng.list()
            return done(True, "\n".join(line(s) for s in rows) or sf_lang.pick("(no services)", "（沒有服務）"),
                        {"services": rows})
        if not eng.registry.get(service):
            ids = ", ".join(eng.registry.ids()) or sf_lang.pick("none", "無")
            return {"success": False, "message": sf_lang.pick(f"Unknown service '{service}'. Known: {ids}", f"不認得「{service}」。可用：{ids}")}
        if action == "status":
            res = eng.refresh(service, force=bool(args.get("refresh"))) if args.get("refresh") else eng.snapshot(service)
            return done(res["success"], line(res["status"]), res)
        if action == "start":
            res = eng.start(service, route=str(args.get("route") or "") or None, reconnect=bool(args.get("reconnect")))
            if not res["success"]:
                return done(False, reg_mod.pick(res["message"]["text"], lang) + " " + reg_mod.pick(res["message"]["next"], lang), res)
            step = (res["view"] or {}).get("step")
            text = line(res["status"])
            if step and res["status"]["state"] != "connected":
                text += "\n" + sf_lang.pick("Now: ", "現在：") + reg_mod.pick(step["title"], lang)
                text += "\n" + sf_lang.pick("Continue in ShellBot: Settings → Connections.", "請到 ShellBot 的「設定 → 連接」繼續。")
            return done(True, text, res)
        if action == "test":
            res = eng.test(service)
            if res["success"] and (res.get("result") or {}).get("state") in ("running", "needs_user"):
                threading.Thread(target=self._connect_poll_test, args=(service,), daemon=True, name=f"connect-test-{service}").start()
                text = line(res["status"]) + "\n" + sf_lang.pick(f"Testing; see the result with: sfctl connect status {service}",
                                                                f"測試進行中；結果請看：sfctl connect status {service}")
                return done(True, text, res)
            text = line(res["status"]) if res["success"] else reg_mod.pick(res["message"]["text"], lang) + " " + reg_mod.pick(res["message"]["next"], lang)
            return done(res["success"], text, res)
        if action == "disconnect":
            res = eng.disconnect(service)
            return done(res["success"], line(res["status"]) if res["success"] else reg_mod.pick(res["message"]["text"], lang), res)
        return {"success": False, "message": "usage: sfctl connect list|status|start|test|disconnect [service]"}

    def _connect_poll_test(self, service: str, limit: float = 240.0):
        """Poll a test that sfctl started until it is done, so the result lands in the saved state without
        the sfctl thread (which runs every command) waiting for it."""
        eng, end = self._connect(), time.time() + limit
        while time.time() < end:
            time.sleep(2.0)
            rec = eng.store.get(service)
            res = eng.test(service, handle=rec.get("test_handle") or None)
            if not res["success"] or (res.get("result") or {}).get("state") == "done":
                return
