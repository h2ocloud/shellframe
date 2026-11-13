"""The one status every place shows: settings, the sidebar card, sfctl, the phone's /link/bots, Telegram.

`build()` turns a service's description and its saved record into the status dict; `compact()` is the
short form for lists and the phone; `words()` is one sentence for Telegram and sfctl. Nothing here reads
a file or asks a program: it is the same answer wherever it is asked.

States: not_started, waiting_user, verifying, connected, failed, needs_reconnect.
`attention` is true for failed and needs_reconnect: the "needs to be connected again" card.

What an adapter's health check (`adapter.health()`) says becomes a state like this, written once here:

    ok           -> connected
    expired      -> needs_reconnect  (problem: expired)
    revoked      -> needs_reconnect  (problem: revoked)
    unreachable  -> stays connected, with a problem (no attention: a dropped network is not a reason to redo steps)
    unknown      -> nothing changes
"""

from . import clis as clis_mod
from . import problems, registry

STATES = ("not_started", "waiting_user", "verifying", "connected", "failed", "needs_reconnect")
ATTENTION = ("failed", "needs_reconnect")


def apply_health(rec: dict, answer: dict) -> dict:
    """The record after an adapter's health answer: (state, problem code or None). Pure."""
    s = str((answer or {}).get("state") or "unknown")
    if s == "ok":
        return {"state": "connected", "problem": None}
    if s in ("expired", "revoked"):
        return {"state": "needs_reconnect", "problem": {"code": s}}
    if s == "unreachable":
        return {"state": rec.get("state", "connected"), "problem": {"code": "unreachable"}}
    return {"state": rec.get("state", "connected"), "problem": rec.get("problem")}


def _cli_states(desc: dict, route: dict, rec: dict) -> dict:
    kind = (route or {}).get("kind", "")
    saved = rec.get("clis") or {}
    out = {}
    for cli in clis_mod.CLI_IDS:
        via = (((route or {}).get("clis") or {}).get(cli) or {}).get("via", "none")
        mode = clis_mod.mode_for(cli, kind) if via != "none" else "unsupported"
        if via == "none" or mode == "unsupported":
            out[cli] = "unsupported"
        elif mode == "manual":
            out[cli] = saved.get(cli) or "manual"
        elif mode == "connector":
            out[cli] = "connected" if rec.get("state") == "connected" else "unknown"
        else:
            out[cli] = saved.get(cli) or "unknown"
    return out


def build(desc: dict, rec: dict, route: dict = None) -> dict:
    """The status of one service. `rec` is its saved record ({} when never started)."""
    rec = rec or {}
    state = rec.get("state") if rec.get("state") in STATES else "not_started"
    if route is None and rec.get("route"):
        route = next((r for r in desc["routes"] if r["id"] == rec["route"]), None)
    steps = (route or {}).get("steps") or []
    ids = [s["id"] for s in steps]
    cur = rec.get("step") if rec.get("step") in ids else ""
    step = {"id": cur, "index": ids.index(cur) + 1, "total": len(ids)} if cur and state not in ("connected", "not_started") else None
    problem = None
    if rec.get("problem") and rec["problem"].get("code"):
        p = problems.explain(rec["problem"]["code"], desc, route)
        problem = {"code": p["code"], "text": p["text"], "next": p["next"], "step": rec["problem"].get("step", "")}
    return {
        "service": desc["id"], "name": desc["name"], "icon": desc.get("icon", ""),
        "kind": (route or {}).get("kind", ""), "state": state, "route": (route or {}).get("id", ""),
        "step": step, "clis": _cli_states(desc, route, rec) if route else {},
        "scopes": list((route or {}).get("scopes") or []),
        "connected_at": int(rec.get("connected_at") or 0), "checked_at": int(rec.get("checked_at") or 0),
        "problem": problem, "attention": state in ATTENTION,
    }


def compact(status: dict) -> dict:
    """The short form (lists, /link/bots): id, name, state, attention and "i/n" while a step is open."""
    st = status.get("step")
    return {"id": status["service"], "name": status["name"], "icon": status.get("icon", ""), "state": status["state"],
            "attention": bool(status.get("attention")), "step": f"{st['index']}/{st['total']}" if st else ""}


_WORDS = {
    "not_started": ("not connected", "還沒接上"),
    "waiting_user": ("waiting for you{at}", "等你操作{at}"),
    "verifying": ("checking…", "驗證中…"),
    "connected": ("connected", "已接上"),
    "failed": ("did not connect", "沒接成功"),
    "needs_reconnect": ("needs to be connected again", "需要重新接上"),
}


def words(status: dict, lang: str = None) -> str:
    """One line: "<name>: <state>", plus the problem's sentence and what to do when there is one."""
    import sf_lang
    lang = lang or sf_lang.prompt_lang()
    en, zh = _WORDS[status["state"]]
    st = status.get("step")
    at = (f" (step {st['index']}/{st['total']})", f"（第 {st['index']}/{st['total']} 步）") if st else ("", "")
    head = (zh if lang == "zh" else en).format(at=at[1] if lang == "zh" else at[0])
    line = f"{registry.pick(status['name'], lang)}：{head}" if lang == "zh" else f"{registry.pick(status['name'], lang)}: {head}"
    p = status.get("problem")
    if p:
        line += f" — {registry.pick(p['text'], lang)} {registry.pick(p['next'], lang)}"
    return line
