"""sfctl 與 Frame Link 之間的 A2A 接線。規則在 agent_link，這裡只碰分頁與 peer。"""

import os
import threading

import agent_link
import agent_route
import tab_delivery
import sf_lang


def _enabled(api) -> bool:
    try:
        from api_host import main
        cfg = main.load_config()
        return bool((cfg.get("settings") or {}).get("experimental_a2a", False))
    except Exception:
        return False


def _label(api, sid: str) -> str:
    session = api.sessions.get(sid) if sid else None
    if not session:
        return sid or ""
    return getattr(session, "_custom_label", None) or sid


class _Ctx:
    def __init__(self, api):
        self.api = api

    def enabled(self) -> bool:
        return _enabled(self.api)

    def roster(self) -> dict:
        from api_host import main
        return self.api._agent_roster_config(main.load_config())

    def peers(self) -> dict:
        return self.api._link().peers()

    def frame_name(self) -> str:
        return self.api._link().frame_name()

    def frame_id(self) -> str:
        return self.api._link().frame_id()

    def resolve(self, role: str, sid: str):
        if sid:
            if sid not in self.api.sessions:
                return "", ""
            return sid, _label(self.api, sid)
        roster = self.roster()
        resolved, entry = self.api._resolve_agent_role(role, roster)
        if not entry:
            return "", ""
        label = entry.get("label") or resolved or role
        if self.external(resolved or role):
            return "", label        # lives outside: never type into a local tab with its name
        found, _session = self.api._find_session_by_label(label)
        return found or "", label

    def _key(self, role: str) -> str:
        resolved, _entry = self.api._resolve_agent_role(role, self.roster())
        return resolved or role

    def external(self, role: str) -> bool:
        return self.api._bots_external_target(self._key(role)) is not None

    def deliver_external(self, role: str, payload: dict) -> dict:
        return self.api._bots_external_deliver(self._key(role), payload)

    sender = ""         # agent_link sets the sending sid before each send
    now = False         # --now: go in even mid-turn

    def send(self, sid: str, text: str) -> dict:
        """Through the per-tab queue: never into a running turn, a decision menu or
        the user's half-typed text (tab_delivery). A timeout tells the sender."""
        if sid not in self.api.sessions:
            return {"success": False, "message": f"No such session: {sid}"}
        sender, api = self.sender, self.api
        label = _label(api, sid)
        return tab_delivery.deliver(
            api, sid, text, now=self.now, from_sid=sender,
            on_timeout=lambda _r, why: tab_delivery.notify_sender(api, sender, label, why))

    def delegate(self, role: str, task: str) -> dict:
        return self.api.delegate_task(role, task)

    def remote(self, peer_id: str, payload: dict) -> dict:
        return self.api._link().remote_a2a(peer_id, payload)

    def workers(self) -> dict:
        try:
            from api_host import main
            import a2a_ssh
            return a2a_ssh.normalize_all(main.load_config().get("a2a_workers") or {})
        except Exception:
            return {}

    def run_ssh(self, argv):
        import a2a_ssh
        return a2a_ssh.default_runner(argv)

    def spawn(self, fn):
        """ssh 最久 180 秒，不能卡住逐一處理 sfctl 的那條執行緒。"""
        threading.Thread(target=fn, daemon=True, name="a2a-ssh").start()


def _readings() -> dict:
    try:
        import usage_probe
        return agent_route.collect_readings(usage_probe.probe_data)
    except Exception:
        return {}


def _from(api, args: dict):
    as_role = (args.get("as") or "").strip()
    if as_role:
        # An external bot (e.g. a cloud bot running sfctl through its local-command
        # feature) speaks as itself. Only outside a tab, and only as a roster role
        # marked external — a tab may not put on another bot's name.
        if (args.get("from_sid") or "").strip():
            return None, sf_lang.pick("--as only works outside a ShellBot tab (an agent in a tab always speaks as itself)",
                                      "--as 只能在 ShellFrame 分頁外面用（分頁裡的 agent 一律以自己的名字說話）")
        label = api._bots_external_label(as_role)
        if not label:
            return None, sf_lang.pick(f"--as {as_role}: no external bot by that name in the roster",
                                      f"--as {as_role}：名冊裡沒有這個外部 bot")
        return f"ext:{as_role}", label
    sid = (args.get("from_sid") or os.environ.get("SF_SID") or "").strip()
    label = (args.get("from_label") or "").strip() or _label(api, sid)
    return sid, label


def annotate_delegate(result, task):
    """delegate 的成功訊息維持原樣，只多一路由建議。"""
    return agent_route.annotate(result, task, _readings())


def handle(api, cmd: str, args: dict) -> dict:
    try:
        args = args or {}
        if cmd == "route_hint":
            hint = agent_route.hint(args.get("task") or "", _readings(),
                                    tier=(args.get("tier") or None))
            return {"success": True, "message": hint.get("text") or "",
                    "details": {"route_hint": hint}}
        if cmd == "a2a_log":
            return agent_link.log_text(int(args.get("limit") or 50))
        ctx = _Ctx(api)
        ctx.now = bool(args.get("now"))
        if cmd == "a2a_deliver":
            return agent_link.accept_remote(ctx, args)
        sid, label = _from(api, args)
        if sid is None:
            return {"success": False, "message": label}
        if cmd == "a2a_result":
            return agent_link.report_result(
                ctx, args.get("id") or "", args.get("text") or "",
                from_sid=sid, from_label=label, diff=bool(args.get("diff")))
        if cmd == "a2a_next":
            task = args.get("text") or args.get("task") or ""
            hint = agent_route.hint(task, _readings())
            return agent_link.continue_handoff(
                ctx, args.get("id") or "", task, from_sid=sid, from_label=label,
                hint=hint.get("text") or "", route_hint=hint or None,
                allow_write=bool(args.get("allow_write")))
        if cmd in ("a2a_send", "a2a_handoff"):
            kind = "handoff" if cmd == "a2a_handoff" else "send"
            hint = {}
            if kind == "handoff":
                hint = agent_route.hint(args.get("text") or args.get("task") or "",
                                        _readings())
            return agent_link.dispatch(
                ctx, target=args.get("target") or "",
                text=args.get("text") or args.get("task") or "",
                from_sid=sid, from_label=label, kind=kind,
                hint=hint.get("text") or "", route_hint=hint or None,
                allow_write=bool(args.get("allow_write")))
        return {"success": False, "message": f"unknown {cmd}"}
    except Exception as e:
        return {"success": False, "message": f"{cmd} failed: {e}"}
