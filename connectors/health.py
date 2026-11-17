"""The one status every place shows: settings, the sidebar card, sfctl, the phone's /link/bots, Telegram.

`build()` turns a service's description and its saved record into the status dict; `compact()` is the
short form for lists and the phone; `words()` is one sentence for Telegram and sfctl. Nothing here reads
a file or asks a program: it is the same answer wherever it is asked.

States: not_started, waiting_user, verifying, connected, failed, needs_reconnect.
`attention` is true for failed and needs_reconnect: the "needs to be connected again" card.

What already works counts (detect.py): a service with a route its program checked (✔) is connected through that
route even when ShellBot's own steps never ran there, or stopped half way on another route. `via` says which route
works and whether it is the person's own setting (`adopted`); `protocols` lists every route with what was found.
`route`, `kind`, `step`, `clis` and `scopes` still describe ShellBot's own steps (the wizard), as before.

What an adapter's health check (`adapter.health()`) says becomes a state like this, written once here:

    ok           -> connected
    expired      -> needs_reconnect  (problem: expired)
    revoked      -> needs_reconnect  (problem: revoked)
    unreachable  -> stays connected, with a problem (no attention: a dropped network is not a reason to redo steps)
    unknown      -> nothing changes
"""

from . import clis as clis_mod
from . import detect, problems, registry

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


def _step_of(route: dict, rec: dict):
    ids = [s["id"] for s in (route or {}).get("steps") or []]
    cur = rec.get("step") if rec.get("step") in ids else ""
    return {"id": cur, "index": ids.index(cur) + 1, "total": len(ids)} if cur else None


def protocols(desc: dict, rec: dict, detected: dict = None) -> list:
    """Every route of a service with what is known about it: what the last look found (detect.findings) and
    where ShellBot's own steps are on it. ShellBot's own entry (shellbot-…) that its program checked counts as
    checked only once ShellBot's steps on that route are done: before that its server starts, but whether it is
    signed in is not known (`found`, why `unfinished`). A route ShellBot connected and tested, which no program
    reported, is `checked` (why `tested`)."""
    out = []
    for r in desc.get("routes") or []:
        mine = rec.get("route") == r["id"] and rec.get("state") == "connected"
        places = []
        for p in ((detected or {}).get(r["id"]) or {}).get("places") or []:
            if p.get("own") and p.get("state") == "checked" and not mine:
                p = dict(p, state="found", why="unfinished")
            places.append(dict(p))
        item = detect.best({"places": places})
        if item["state"] == "none" and mine:
            item.update(state="checked", why="tested")
        setup = None
        if rec.get("route") == r["id"] and rec.get("state") in STATES and rec["state"] != "not_started":
            setup = {"state": rec["state"], "step": _step_of(r, rec) if rec["state"] != "connected" else None}
        out.append({"id": r["id"], "kind": r.get("kind", ""), **item, "setup": setup})
    return out


def _pick(protos: list, desc: dict, rec: dict):
    """The route a service works through, of those checked: ShellBot's own first, then the recommended, then the
    description's order."""
    ok = [p for p in protos if p["state"] == "checked"]
    rec_id = rec.get("route")
    first = next((r["id"] for r in desc.get("routes") or [] if r.get("recommended")), "")
    return min(ok, key=lambda p: (p["id"] != rec_id, p["id"] != first, protos.index(p))) if ok else None


def build(desc: dict, rec: dict, route: dict = None, platform: str = None, detected: dict = None, detected_at: int = 0) -> dict:
    """The status of one service. `rec` is its saved record ({} when never started). `available` is false for
    a service the catalog lists that cannot be connected here (registry.available: not yet, or not on this
    kind of computer), with the reason as its `problem`; it then never needs attention. `detected` is what the
    last look found for it (detect.findings), `detected_at` when that look was (0: not yet)."""
    rec = rec or {}
    why = registry.available(desc, platform)
    state = rec.get("state") if rec.get("state") in STATES else "not_started"
    if route is None and rec.get("route"):
        route = next((r for r in desc["routes"] if r["id"] == rec["route"]), None)
    protos = protocols(desc, rec, detected)
    via, found_way = None, False
    if state == "connected" and route:
        via = {"route": route["id"], "kind": route.get("kind", ""), "adopted": False, "detected": False, "cli": "", "entry": ""}
    elif not why:
        p = _pick(protos, desc, rec)
        if p:                      # it works another way (or ShellBot's steps on it never ran): connected through that one
            state, found_way = "connected", True
            via = {"route": p["id"], "kind": p["kind"], "adopted": p["adopted"], "detected": True, "cli": p["cli"], "entry": p["entry"]}
    step = _step_of(route, rec) if state not in ("connected", "not_started") else None
    problem = None
    if rec.get("problem") and rec["problem"].get("code") and not found_way:
        p = problems.explain(rec["problem"]["code"], desc, route)
        problem = {"code": p["code"], "text": p["text"], "next": p["next"], "step": rec["problem"].get("step", "")}
    elif why and state == "not_started":
        p = problems.explain(why, desc)
        problem = {"code": p["code"], "text": p["text"], "next": p["next"], "step": ""}
    return {
        "service": desc["id"], "name": desc["name"], "icon": desc.get("icon", ""),
        "kind": (route or {}).get("kind", ""), "state": state, "route": (route or {}).get("id", ""),
        "step": step, "clis": _cli_states(desc, route, rec) if route else {},
        "scopes": list((route or {}).get("scopes") or []),
        "connected_at": int(rec.get("connected_at") or 0), "checked_at": int(rec.get("checked_at") or 0),
        "problem": problem, "attention": state in ATTENTION, "available": not why,
        "via": via, "protocols": protos, "detected_at": int(detected_at or 0),
    }


def compact(status: dict) -> dict:
    """The short form (lists, /link/bots): id, name, state, attention and "i/n" while a step is open."""
    st = status.get("step")
    return {"id": status["service"], "name": status["name"], "icon": status.get("icon", ""), "state": status["state"],
            "attention": bool(status.get("attention")), "step": f"{st['index']}/{st['total']}" if st else ""}


_WORDS = {
    "not_started": ("not connected", "還沒連結"),
    "waiting_user": ("waiting for you{at}", "連結中，等你操作{at}"),
    "verifying": ("checking…", "驗證中…"),
    "connected": ("connected", "已連結"),
    "failed": ("did not connect", "沒連結成功"),
    "needs_reconnect": ("needs to be connected again", "需要重新連結"),
    "unavailable": ("cannot be connected here yet", "這裡還不能連結"),
}


# A route's kind in a few words: what a connected service's line says it goes through.
KIND_WORDS = {
    "claude_connector": ("the Claude account's connector: Claude tabs only", "Claude 帳號連接器：只有 Claude 分頁能用"),
    "local_mcp": ("a local MCP server", "本機 MCP 伺服器"),
    "api_key": ("an API key", "API 金鑰"),
    "oauth": ("a sign-in", "登入授權"),
}


def via_words(via, lang: str = "en") -> str:
    """How a connected service is reached, in a few words: "(a local MCP server "x", your own setting, in Claude Code)"."""
    if not via or not via.get("kind"):
        return ""
    en, zh = KIND_WORDS.get(via["kind"], (via["kind"], via["kind"]))
    entry = registry.clean_line(via.get("entry") or "", 60)
    where = (clis_mod.SUPPORT.get(via.get("cli") or "") or {}).get("label", "")
    if lang == "zh":
        return "（" + "，".join(x for x in (zh + (f" {entry}" if entry else ""), "你自己的設定" if via.get("adopted") else "",
                                              f"在 {where} 裡" if where and via["kind"] != "claude_connector" else "") if x) + "）"
    return " (" + ", ".join(x for x in (en + (f' "{entry}"' if entry else ""), "your own setting" if via.get("adopted") else "",
                                        f"in {where}" if where and via["kind"] != "claude_connector" else "") if x) + ")"


def words(status: dict, lang: str = None) -> str:
    """One line: "<name>: <state>", plus the problem's sentence and what to do when there is one. A connected
    service says which way it is reached (and whether that is the person's own setting)."""
    import sf_lang
    lang = lang or sf_lang.prompt_lang()
    en, zh = _WORDS["unavailable" if status.get("available") is False and status["state"] == "not_started" else status["state"]]
    st = status.get("step")
    at = (f" (step {st['index']}/{st['total']})", f"（第 {st['index']}/{st['total']} 步）") if st else ("", "")
    head = (zh if lang == "zh" else en).format(at=at[1] if lang == "zh" else at[0])
    if status["state"] == "connected":
        head += via_words(status.get("via"), lang)
    line = f"{registry.pick(status['name'], lang)}：{head}" if lang == "zh" else f"{registry.pick(status['name'], lang)}: {head}"
    p = status.get("problem")
    if p:
        line += f" — {registry.pick(p['text'], lang)} {registry.pick(p['next'], lang)}"
    return line


def step_words(service: str, view, lang: str = None, with_result: bool = True) -> str:
    """For a bot that drives the steps (`sfctl connect step`): the step in front of the person, as lines. Its id, the
    actions ShellBot can do for it, what the last action and the check came to (their own words from the description,
    nothing the service said), and the calls that move it. '' when there is no step."""
    import sf_lang
    lang = lang or sf_lang.prompt_lang()
    step = (view or {}).get("step")
    if not step:
        return ""
    pick = lambda x: registry.pick(x, lang) if x else ""
    zh = lang == "zh"
    out = [("步驟代號：" if zh else "Step id: ") + f"{step['id']} — {pick(step.get('title'))}"]
    if step.get("kind") == "test":
        out.append(f"這一步是測試：跑 sfctl connect test {service}" if zh else f"This step is the test: run sfctl connect test {service}")
        return "\n".join(out)
    acts = step.get("actions") or []
    if acts:
        out.append(("ShellBot 能做的動作：" if zh else "Actions ShellBot can do: ") + ", ".join(f"{a['id']} ({pick(a['label'])})" for a in acts))
    res, chk = step.get("result"), step.get("check")
    if with_result and res and res.get("text"):
        out.append(("上一個動作：" if zh else "Last action: ") + f"{res['action']} → {pick(res['text'])}")
    if chk and chk.get("text"):
        out.append(("檢查：" if zh else "Check: ") + pick(chk["text"]) + ((" — " + pick(chk["hint"])) if chk.get("hint") and not chk.get("passed") else ""))
    ops = (["check"] if step.get("check") else []) + ["done", "back"]
    out.append(("往下走：" if zh else "Move it: ") + " | ".join(
        [f"sfctl connect step {service} act --action <id>"] * bool(acts) + [f"sfctl connect step {service} {o}" for o in ops]))
    return "\n".join(out)
