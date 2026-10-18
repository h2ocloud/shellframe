"""sfctl 與 Frame Link 之間的 A2A 接線。規則在 agent_link，這裡只碰分頁與 peer。"""

import os

import agent_link
import agent_route


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
        found, _session = self.api._find_session_by_label(label)
        return found or "", label

    def send(self, sid: str, text: str) -> dict:
        session = self.api.sessions.get(sid)
        if not session:
            return {"success": False, "message": f"No such session: {sid}"}
        self.api._send_text_to_session(session, text, submit=True)
        return {"success": True, "message": f"Sent to {sid}"}

    def delegate(self, role: str, task: str) -> dict:
        return self.api.delegate_task(role, task)

    def remote(self, peer_id: str, payload: dict) -> dict:
        return self.api._link().remote_a2a(peer_id, payload)


def _readings() -> dict:
    try:
        import usage_probe
        return agent_route.collect_readings(usage_probe.probe_data)
    except Exception:
        return {}


def _from(api, args: dict):
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
        if cmd == "a2a_deliver":
            return agent_link.accept_remote(ctx, args)
        sid, label = _from(api, args)
        if cmd == "a2a_result":
            return agent_link.report_result(
                ctx, args.get("id") or "", args.get("text") or "",
                from_sid=sid, from_label=label)
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
                hint=hint.get("text") or "", route_hint=hint or None)
        return {"success": False, "message": f"unknown {cmd}"}
    except Exception as e:
        return {"success": False, "message": f"{cmd} failed: {e}"}
