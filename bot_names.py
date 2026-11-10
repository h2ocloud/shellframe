"""One name per bot, and one way to find a bot or a tab by it (plan A).

What someone (a person, sfctl, an agent's [[SF:TO:…]], a #tag) may call a bot or a tab,
in the order the steps win:
  1. a tab id: s12;
  2. a bot's name, which is its roster key: any case;
  3. a tab's title: any case;
  4. an alias (AGENT_ROLE_ALIASES) or a bot's old label ("研究-CLD"): any case;
  5. the start of exactly one of the names above.
Two matches at the step that decides is an error that lists them; nothing is guessed from
the middle of a name. (The old last step looked for the name inside every role's key and
label: "研" found "研究", and a name inside another name found the wrong bot.)

Which tab is a bot's: its binding (bots_sessions) first, then a live tab titled with the
bot's name or label that is bound to no other bot and not marked plain ("not a bot").

Everything here is a pure function of the roster, the bindings and the live tabs, each
`{"sid", "title"}`; the callers (main, a2a_host, api_remote, api_bots) build those.
"""

import re

SID_RE = re.compile(r"^s\d+$")


def _fold(text) -> str:
    return str(text or "").strip().casefold()


def label_of(role: str, entry: dict) -> str:
    return str((entry or {}).get("label") or role)


def bot_tab(role: str, roster: dict, tabs: list, bindings: dict) -> str:
    """The live tab that is this bot's, or ""."""
    if role not in (roster or {}):
        return ""
    live = {t["sid"]: t for t in tabs}
    bindings = bindings or {}
    for sid, row in bindings.items():
        if sid in live and (row or {}).get("bot") == role and not (row or {}).get("plain"):
            return sid
    names = {role, label_of(role, roster[role])}
    for want in (names, {_fold(n) for n in names}):
        for t in tabs:
            row = bindings.get(t["sid"]) or {}
            if row.get("plain") or (row.get("bot") and row.get("bot") != role):
                continue
            title = str(t.get("title") or "")
            if (title in want) if want is names else (_fold(title) in want):
                return t["sid"]
    return ""


def role_of_tab(sid: str, roster: dict, tabs: list, bindings: dict, only=None) -> str:
    """The bot a tab belongs to, or "": its binding, else a title that is a bot's name or
    label. `only(role, entry)` narrows which roster entries count (e.g. bots with memory)."""
    roster, bindings = roster or {}, bindings or {}
    row = bindings.get(sid) or {}
    if row.get("plain"):
        return ""
    ok = (lambda r: r in roster and (only is None or only(r, roster[r])))
    bound = row.get("bot") or ""
    if bound and ok(bound):
        return bound
    title = next((str(t.get("title") or "") for t in tabs if t["sid"] == sid), "")
    if not title:
        return ""
    for exact in (True, False):
        for role, entry in roster.items():
            if not ok(role):
                continue
            names = (role, label_of(role, entry))
            if any((title == n) if exact else (_fold(title) == _fold(n)) for n in names):
                live = {t["sid"] for t in tabs}             # a binding to a closed tab holds nothing
                taken = any((r or {}).get("bot") == role for s, r in bindings.items() if s != sid and s in live)
                if not taken:
                    return role
    return ""


def resolve(name: str, roster: dict, tabs: list, bindings: dict, aliases: dict = None,
            prefix: bool = True) -> dict:
    """{"ok": True, "role", "sid", "via"} or {"ok": False, "error": "not_found" | "ambiguous",
    "candidates": [...]}. `role` is a roster key ("" for a tab that is no bot's); `sid` is
    its live tab ("" for a bot asleep). prefix=False stops before step 5: a caller looking
    for a tab by a role's full label must not land on another tab that merely starts so."""
    roster, bindings, aliases = roster or {}, bindings or {}, aliases or {}
    raw = str(name or "").strip()
    want = _fold(raw)
    if not want:
        return {"ok": False, "error": "not_found", "candidates": []}
    live = {t["sid"] for t in tabs}

    def of_role(role, via):
        return {"ok": True, "role": role, "sid": bot_tab(role, roster, tabs, bindings), "via": via}

    def of_tab(sid, via):
        return {"ok": True, "role": role_of_tab(sid, roster, tabs, bindings), "sid": sid, "via": via}

    def pick(found, make, via):
        if len(found) == 1:
            return make(found[0], via)
        return {"ok": False, "error": "ambiguous", "candidates": sorted(found)} if found else None

    if SID_RE.match(raw) and raw in live:
        return of_tab(raw, "sid")
    hit = pick([r for r in roster if _fold(r) == want], of_role, "name")
    if hit:
        return hit
    titled = [t["sid"] for t in tabs if _fold(t.get("title")) == want]
    if titled:
        by_role = {role_of_tab(s, roster, tabs, bindings) for s in titled}
        if len(titled) > 1 and len(by_role) == 1 and "" not in by_role:
            return of_role(by_role.pop(), "title")           # one bot, its tab and a stale twin
        hit = pick(titled, of_tab, "title")
        if hit and not hit["ok"]:
            hit["candidates"] = [f"{_title(tabs, s)} ({s})" for s in titled]
        return hit
    aliased = {aliases.get(want)} - {None, ""}
    aliased |= {r for r, e in roster.items() if _fold(label_of(r, e)) == want}
    hit = pick(sorted(r for r in aliased if r in roster), of_role, "alias")
    if hit:
        return hit
    if not prefix:
        return {"ok": False, "error": "not_found", "candidates": []}
    targets = {}
    for r, e in roster.items():
        for n in (r, label_of(r, e)):
            if _fold(n).startswith(want):
                targets[("role", r)] = r
    for a, r in aliases.items():
        if r in roster and _fold(a).startswith(want):
            targets[("role", r)] = r
    for t in tabs:
        if _fold(t.get("title")).startswith(want):
            role = role_of_tab(t["sid"], roster, tabs, bindings)
            targets[("role", role) if role else ("tab", t["sid"])] = role or f"{t.get('title')} ({t['sid']})"
    if len(targets) == 1:
        (kind, key), = targets
        return of_role(key, "prefix") if kind == "role" else of_tab(key, "prefix")
    return {"ok": False, "error": "ambiguous" if targets else "not_found", "candidates": sorted(targets.values())}


def _title(tabs, sid):
    return next((str(t.get("title") or "") for t in tabs if t["sid"] == sid), sid)


def explain(result: dict, name: str, zh: bool) -> str:
    """The message for a failed resolve, in the language the caller speaks."""
    cands = "、".join(result.get("candidates") or []) if zh else ", ".join(result.get("candidates") or [])
    if result.get("error") == "ambiguous":
        return (f"「{name}」對到不只一個：{cands}。請寫完整的名字。" if zh
                else f'"{name}" matches more than one: {cands}. Give the whole name.')
    return f"找不到「{name}」這個 bot 或分頁。" if zh else f'No bot or tab is called "{name}".'
