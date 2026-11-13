"""
tab_model — switch the model of a tab that is already running, the way the CLI itself does it.

A bot's model used to be a launch flag: changing it meant closing the tab. Each AI CLI also has a way
to change it inside a running conversation, and they all differ (docs/tab-model.md, measured on live
tabs):

  * Claude Code   /model opens a menu. `/model <name>` and Enter in the menu both save the pick as the
                  default of every new session; `s` in the menu is this tab only, so that is the key used.
  * Codex         /model opens two numbered menus (model, then reasoning level). `/model <name>` is NOT
                  a command with an argument: it goes to the agent as a prompt, so it is never typed.
  * OpenCode, Pi  a picker with a search box: type part of the name, Enter takes the first match.
  * Grok, Cursor  one typed line, `/model <name> [effort]`.
  * Antigravity, any command ShellBot does not know: no way inside; the tab has to be started again.

This module is the part with no I/O of its own: what each CLI takes (`SWITCH`), how its screen reads
(the `*_menu` parsers and `confirm_*`), and `drive()`, the steps that press the keys. The host (api_tab_model)
gives `drive()` an `io` with `lines()` (the screen as plain lines), `send(*keys)`, `sleep(s)`, `now()` and
`probe()` (what the status monitor reads from the CLI's own files), so the tests run the same steps against
screens written down from the real CLIs.

Rules the steps keep:
  * nothing is pressed that the screen has not shown to be there; a menu that is not where it should be is
    closed with Esc and reported, never answered blind;
  * what a switch did is read back from the screen (or the CLI's files), not assumed: the answer says what the
    CLI reports (`name`, `effort`), and when that is not what was asked, says so;
  * a model the CLI's own list does not hold is "not listed", never a guess at the nearest.
"""

import re

import agent_model
import agent_status
from sf_lang import pick

# ── what each CLI takes ───────────────────────────────────────────────────
_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_LABEL = {"off": ("Off", "關"), "minimal": ("Minimal", "極低"), "low": ("Low", "低"), "medium": ("Medium", "中"),
                "high": ("High", "高"), "xhigh": ("xHigh", "超高"), "max": ("Max", "最高")}

# kind        what agent_status.worker_kind calls it
#   how       "menu": a menu or picker is driven · "command": one typed line · "restart": no way inside
#   open      the line typed to open it ("" = nothing to type)
#   efforts   the levels ShellBot can set, in the CLI's order; () = none
#   persists  the CLI keeps the pick as its own default for new sessions (measured); the page says so
#   verified  measured on a live tab (False: from the CLI's own documentation, the answer says it is unconfirmed)
#   default   the CLI's list has a "default" row that key "" picks
SWITCH = {
    "claude": dict(name="Claude Code", how="menu", open="/model", efforts=_LEVELS, persists=False, verified=True, default=True),
    "codex": dict(name="Codex", how="menu", open="/model", efforts=("low", "medium", "high", "xhigh"), persists=True, verified=True),
    "opencode": dict(name="OpenCode", how="menu", open="/models", efforts=(), persists=True, verified=True),
    "pi": dict(name="Pi", how="menu", open="/model", efforts=("off", "minimal", "low", "medium", "high"), persists=True, verified=True),
    "grok": dict(name="Grok Build", how="command", open="", efforts=("low", "medium", "high", "xhigh"), persists=None, verified=False),
    "cursor": dict(name="Cursor Agent", how="command", open="", efforts=(), persists=None, verified=False),
    "agy": dict(name="Antigravity", how="restart", open="", efforts=(), persists=False, verified=True),
}
CUSTOM_MAX = 80
_SAFE_TEXT = re.compile(r"[^\x00-\x1f\x7f]+")


def kind_of(cmd: str) -> str:
    return agent_status.worker_kind(cmd or "")


def spec_of(cmd: str) -> dict:
    """The row of SWITCH for a command; a command ShellBot does not know has no way inside."""
    k = kind_of(cmd)
    return dict(SWITCH.get(k) or dict(name="", how="restart", open="", efforts=(), persists=False, verified=True),
                kind=k)


def efforts_for(cmd: str, zh: bool = False) -> list:
    return [{"key": e, "label": EFFORT_LABEL[e][1 if zh else 0]} for e in spec_of(cmd)["efforts"]]


def link_fields(cmd: str) -> dict:
    """What a phone needs to know about a tab, beside `choices.models`: can this tab change its model without
    being started again, and which levels does it take (docs/mobile-link.md)."""
    sp = spec_of(cmd)
    return {"in_session": sp["how"] != "restart", "efforts": list(sp["efforts"]), "unverified": not sp["verified"],
            "persists": sp["persists"]}


def models_for(cmd: str, zh: bool = False) -> list:
    """The list the page offers: agent_model's table for this CLI (no other list is kept here), without the
    "CLI default" row unless this CLI has one in its own menu, and without the custom row (`custom_ok` says
    whether a typed name is taken)."""
    sp = spec_of(cmd)
    prov = agent_model.provider_of(cmd) or sp["kind"]
    out = [c for c in agent_model.choices(prov, zh) if not c.get("custom")]
    return [c for c in out if c["key"] or sp.get("default")]


def custom_ok(cmd: str) -> bool:
    return spec_of(cmd)["how"] != "restart"


def plan(cmd: str, model, effort, custom: bool = False) -> dict:
    """{"ok": True, "kind", "model", "effort", "opener"} for a switch ShellBot can type into this tab, else
    {"ok": False, "code", "message"}. `model` None keeps the tab's model (only the level changes)."""
    sp = spec_of(cmd)
    model = None if model is None else str(model).strip()
    effort = (str(effort).strip().lower() or None) if effort is not None else None

    def no(code, en, zh):
        return {"ok": False, "code": code, "message": pick(en, zh)}
    if sp["how"] == "restart":
        return no("restart", f"{sp['name'] or 'This command'} cannot change its model inside a running tab: it has to be started again.",
                  f"{sp['name'] or '這個指令'}沒辦法在對話中換模型，要重開才能換。")
    if model is None and effort is None:
        return no("nothing", "Nothing to change", "沒有要改的")
    if effort is not None and effort not in sp["efforts"]:
        return no("bad_effort", f"{sp['name']} has no thinking level \"{effort}\"", f"{sp['name']} 沒有「{effort}」這個思考強度")
    if model is not None:
        if not model and not sp.get("default"):
            return no("bad_model", "Pick a model", "請選一個模型")
        if model and (len(model) > CUSTOM_MAX or not _SAFE_TEXT.fullmatch(model)):
            return no("bad_model", "A model name is one short line of text", "模型名稱是一小行文字")
        if custom and model and sp["kind"] in agent_model.PROVIDERS:
            res = agent_model.check(cmd, model)
            if not res["ok"]:
                return no(res["code"] or "bad_model", f'"{model}" is not a model name {sp["name"]} takes',
                          f"{sp['name']} 不認得「{model}」這個模型")
    return {"ok": True, "kind": sp["kind"], "how": sp["how"], "model": model, "effort": effort,
            "opener": opener(sp, model, effort)}


def opener(sp: dict, model, effort) -> str:
    """The line typed (and submitted) to start it: the menu's own command, a typed line for Grok and Cursor,
    nothing when only Pi's level changes (a key, no command)."""
    if sp["kind"] == "grok":
        if model:
            return f"/model {model}" + (f" {effort}" if effort else "")
        return f"/effort {effort}"
    if sp["kind"] == "cursor":
        return f"/model {model}"
    if sp["kind"] == "pi" and model is None:
        return ""
    return sp["open"]


# ── reading a screen ──────────────────────────────────────────────────────
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _after_last(lines, pred) -> list:
    """The lines after the last one `pred` accepts (the echo of what was typed); all of them when none is."""
    at = max((i for i, ln in enumerate(lines) if pred(ln)), default=-1)
    return lines[at + 1:]


# Claude Code
_CL_ROW = re.compile(r"^\s*(?P<cur>❯)?\s*(?P<n>\d{1,2})\.\s+(?P<name>\S.*?)(?:\s{2,}(?P<desc>\S.*))?\s*$")
_CL_MORE = re.compile(r"^\s*(?P<cur>❯)?\s*(?P<mark>[▸▾])\s+More models")
_CL_EFFORT = re.compile(r"\b(low|medium|high|xhigh|max)\s+effort\b", re.I)
_CL_FOOT_EFFORT = re.compile(r"[●◐◑◒◓◉◈○]\s*(low|medium|high|xhigh|max)\s*·\s*/effort", re.I)
_CL_DONE = re.compile(r"Set model to (?P<name>.+?)\s+(?P<how>for this session only|and saved as your default)\b"
                      r"(?:.*?\bwith (?P<effort>\w+) effort)?")


def claude_menu(lines):
    """Claude Code's /model menu as {"rows": [{"n", "name", "now"}], "cursor": n or None, "on_more": bool,
    "more": bool (a "More models" row), "expanded": bool, "effort": level under the list or None, "session_key": bool
    (the footer offers `s` for this tab only)}, or None when it is not on the screen."""
    start = max((i for i, ln in enumerate(lines) if ln.strip() == "Select model"), default=-1)
    if start < 0:
        return None
    m = {"rows": [], "cursor": None, "on_more": False, "more": False, "expanded": False, "effort": None, "session_key": False}
    for ln in lines[start + 1:]:
        r = _CL_ROW.match(ln)
        if r:
            name = r.group("name")
            m["rows"].append({"n": int(r.group("n")), "name": name.replace("✔", "").strip(), "now": "✔" in name})
            if r.group("cur"):
                m["cursor"] = int(r.group("n"))
            continue
        more = _CL_MORE.match(ln)
        if more:
            m["more"], m["expanded"], m["on_more"] = True, more.group("mark") == "▾", bool(more.group("cur"))
            continue
        if "to adjust" in ln:
            e = _CL_EFFORT.search(ln)
            m["effort"] = e.group(1).lower() if e else None
        if "s to use this session only" in ln:
            m["session_key"] = True
    return m if len(m["rows"]) >= 2 else None


def _claude_want(key: str) -> str:
    """The row name a model key stands for: an alias (`opus`) is its first row; a full id
    (`claude-opus-4-8`) is the row "Opus 4.8"."""
    k = (key or "").strip().lower()
    f = re.fullmatch(r"claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?(?:-\d{8})?", k)
    if f:
        return f"{f.group(1)} {f.group(2)}" + (f".{f.group(3)}" if f.group(3) else "")
    return k


def claude_row(menu, key: str):
    """The menu row a key picks, or None: "" is the Default row; an alias is the first row of that family."""
    want = _claude_want(key)
    for r in menu["rows"]:
        nm = r["name"].lower()
        if key == "":
            if nm.startswith("default"):
                return r
        elif nm == want or (" " not in want and nm.split()[0] == want and not nm.startswith("default")):
            return r
    return None


def claude_confirm(lines):
    """What Claude Code said after the menu: {"name", "effort", "session_only"} from `⎿  Set model to …`, read
    below the echo of /model; None until it is there."""
    for ln in reversed(_after_last(lines, lambda s: s.strip() in ("❯ /model", "> /model"))):
        m = _CL_DONE.search(ln)
        if m:
            return {"name": re.sub(r"\s*\(default\)$", "", m.group("name")), "effort": (m.group("effort") or "").lower() or None,
                    "session_only": m.group("how").startswith("for this")}
    return None


def claude_footer_effort(lines):
    for ln in reversed(lines):
        m = _CL_FOOT_EFFORT.search(ln)
        if m:
            return m.group(1).lower()
    return None


# Codex
_CX_TITLE = re.compile(r"^\s*Select (?P<what>Model and Effort|Reasoning Level for (?P<model>\S+))\s*$")
_CX_ROW = re.compile(r"^\s*(?P<cur>›)?\s*(?P<n>\d{1,2})\.\s+(?P<name>\S.*?)(?:\s{2,}(?P<desc>\S.*))?\s*$")
_CX_EFFORT_ROW = {"low": "low", "medium": "medium", "high": "high", "extra high": "xhigh"}
_CX_DONE = re.compile(r"Model changed to (?P<name>\S+)(?:\s+(?P<effort>[A-Za-z][A-Za-z ]*?))?\s*$")


def codex_menu(lines):
    """Codex's picker as {"kind": "model"|"effort", "model": id for the effort menu, "rows": [{"n", "id", "default"}],
    "cursor": n or None}, or None. The last title on the screen is the one that counts."""
    start = max((i for i, ln in enumerate(lines) if _CX_TITLE.match(ln)), default=-1)
    if start < 0:
        return None
    t = _CX_TITLE.match(lines[start])
    out = {"kind": "model" if t.group("model") is None else "effort", "model": t.group("model") or "", "rows": [], "cursor": None}
    for ln in lines[start + 1:]:
        r = _CX_ROW.match(ln)
        if not r:
            continue
        name = r.group("name")
        dflt = "(default)" in name
        name = name.replace("(default)", "").strip()
        out["rows"].append({"n": int(r.group("n")), "id": name if out["kind"] == "model" else name.lower(), "default": dflt})
        if r.group("cur"):
            out["cursor"] = int(r.group("n"))
    if out["kind"] == "model":
        for r in out["rows"]:
            r["id"] = r["id"].split()[0] if r["id"] else r["id"]
    else:
        for r in out["rows"]:
            r["id"] = _CX_EFFORT_ROW.get(r["id"], r["id"])
    return out if out["rows"] else None


def codex_confirms(lines) -> list:
    """Every `• Model changed to <id> <level>` on the screen, oldest first. Codex does not echo a slash command, so
    there is no line to read below: the caller compares with what was there before it pressed the last key."""
    out = []
    for ln in lines:
        m = _CX_DONE.search(ln.strip().lstrip("•").strip()) if "Model changed to" in ln else None
        if m:
            eff = (m.group("effort") or "").strip().lower()
            out.append({"name": m.group("name"), "effort": _CX_EFFORT_ROW.get(eff, eff) or None})
    return out


def codex_new_confirm(lines, before: list):
    """The newest confirmation when there is one more than `before`, or the last one changed; else None."""
    now = codex_confirms(lines)
    return now[-1] if now and (len(now) > len(before) or (now[-1] != (before[-1] if before else None))) else None


# OpenCode
_OC_LINE = re.compile(r"(?:^|[┃│]\s*)(?P<agent>[A-Za-z]+)\s·\s(?P<rest>\S.*?)\s*$")


def opencode_open(lines) -> bool:
    return any(ln.strip().endswith("Select model") or re.search(r"Select model\s+esc\s*$", ln) for ln in lines)


def opencode_rows(lines) -> int:
    """How many matches the dialog lists for what was typed in its search line (0: the footer follows at once)."""
    at = max((i for i, ln in enumerate(lines) if re.search(r"Select model\s+esc\s*$", ln)), default=-1)
    if at < 0:
        return 0
    body = [ln for ln in lines[at + 1:] if ln.strip()]
    n = 0
    for ln in body[1:]:                      # the first line is the search box
        if "Connect provider" in ln:
            break
        n += 1
    return n


def opencode_current(lines):
    """The prompt box's `Build · <model> <provider>` line: the model text after the dot, or None."""
    for ln in reversed(lines):
        m = _OC_LINE.search(ln)
        if m and m.group("agent")[:1].isupper():
            return m.group("rest")
    return None


# Pi
_PI_ROW = re.compile(r"^\s*(?P<cur>→)?\s*(?P<id>\S+)\s+\[(?P<prov>[^\]]+)\](?P<now>\s*✓)?\s*$")
_PI_FOOT = re.compile(r"\s{2,}(?P<model>\S+)(?:\s+•\s+(?P<level>thinking off|[a-z]+))?\s*$")
_PI_MODEL = re.compile(r"^\s*Model:\s*(?P<id>\S+)\s*$")
_PI_LEVEL = re.compile(r"^\s*Thinking level:\s*(?P<level>\w+)\s*$")


def pi_selector(lines):
    """Pi's model selector as {"rows": [{"id", "provider", "now", "cursor"}]}, or None."""
    at = max((i for i, ln in enumerate(lines) if ln.strip().startswith("Scope:")), default=-1)
    if at < 0:
        return None
    rows = []
    for ln in lines[at + 1:]:
        m = _PI_ROW.match(ln)
        if m:
            rows.append({"id": m.group("id"), "provider": m.group("prov"), "now": bool(m.group("now")), "cursor": bool(m.group("cur"))})
    return {"rows": rows}


def pi_footer(lines):
    """The footer's right end: {"model", "level"} (`level` None for a model with no thinking, "off" when off)."""
    for ln in reversed([x for x in lines if x.strip()]):
        m = _PI_FOOT.search(ln)
        if m and "/" in ln:
            lvl = m.group("level")
            return {"model": m.group("model"), "level": "off" if lvl == "thinking off" else lvl}
        break
    return None


def pi_confirm(lines):
    """The message Pi prints after a switch: {"model": id} / {"level": …}, newest last on the screen."""
    for ln in reversed(lines):
        m = _PI_MODEL.match(ln) or None
        if m:
            return {"model": m.group("id")}
    return None


# ── driving it ────────────────────────────────────────────────────────────
KEYS = {"Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D", "Enter": "\r", "Esc": "\x1b", "BTab": "\x1b[Z"}
WAIT_OPEN = 6.0       # seconds the opened menu has to show
WAIT_DONE = 5.0       # seconds the CLI has to say it switched
WAIT_PROBE = 10.0     # …for a CLI that says nothing on the screen (Grok, Cursor): its own files
STEP = 0.25


def _until(io, fn, timeout: float, step: float = STEP):
    """fn() until it returns something truthy, at most `timeout` seconds: that value, or None."""
    end = io.now() + timeout
    while True:
        v = fn()
        if v:
            return v
        if io.now() >= end:
            return None
        io.sleep(step)


def _result(ok: bool, code: str = "", en: str = "", zh: str = "", **kw) -> dict:
    return dict({"ok": ok, "code": code, "message": pick(en, zh) if en else "", "name": "", "effort": None}, **kw)


def _close(io, is_open, tries: int = 3):
    """Leave a menu that is still up (Esc, up to `tries` times: Codex's second menu goes back to the first)."""
    for _ in range(tries):
        if not is_open(io.lines()):
            return
        io.send("Esc")
        io.sleep(0.4)


def drive(kind: str, io, model, effort) -> dict:
    """Press what `kind`'s CLI needs, after its opening line is typed; read back what it did.
    {"ok", "code", "message", "name", "effort", "unconfirmed"}: `name` and `effort` are what the CLI reports."""
    try:
        fn = {"claude": _drive_claude, "codex": _drive_codex, "opencode": _drive_opencode, "pi": _drive_pi,
              "grok": _drive_command, "cursor": _drive_command}.get(kind)
        if not fn:
            return _result(False, "restart", "This CLI has no way to switch inside a tab.", "這家沒辦法在對話中換。")
        return fn(io, model, effort)
    except Exception as e:                        # a screen nobody expected must not leave a menu open
        try:
            io.send("Esc")
        except Exception:
            pass
        return _result(False, "error", f"Switching stopped: {e!r}", f"換到一半停了：{e!r}")


def _said(got, want_effort) -> dict:
    """A switch the CLI confirmed, with a note when it is not what was asked."""
    name, eff = got.get("name") or "", got.get("effort")
    note_en = note_zh = ""
    if want_effort and eff and eff != want_effort:
        note_en = f" (it is on {eff}, not {want_effort}: this model does not take it)"
        note_zh = f"（思考強度是 {eff}，不是 {want_effort}：這個模型不接受）"
    return _result(True, "", f"Switched to {name}" + (f" · {eff}" if eff else "") + note_en,
                   f"已換成 {name}" + (f" · {eff}" if eff else "") + note_zh, name=name, effort=eff)


def _press(io, key: str, n: int):
    for _ in range(max(0, n)):
        io.send(key)
        io.sleep(0.12)


def _drive_claude(io, model, effort) -> dict:
    menu = _until(io, lambda: claude_menu(io.lines()), WAIT_OPEN)
    if not menu:
        return _result(False, "no_menu", "Claude Code's model menu did not open.", "Claude Code 的模型選單沒有跳出來。")
    close = lambda: _close(io, lambda ls: claude_menu(ls) is not None)
    if not menu["session_key"]:
        close()
        return _result(False, "no_session_only", "This Claude Code's menu has no 'this session only' key; nothing was changed, "
                       "because any other pick would also change the default of every new session.",
                       "這個版本的 Claude Code 選單沒有「只這個分頁」的按鍵，所以沒有換：用別的方式選會連新分頁的預設一起改掉。")
    if model is not None:
        row = claude_row(menu, model)
        if not row and menu["more"] and not menu["expanded"]:           # the list has more rows behind "More models"
            for _ in range(len(menu["rows"]) + 2):
                if menu["on_more"]:
                    break
                io.send("Down")
                io.sleep(0.15)
                menu = claude_menu(io.lines()) or menu
            if menu["on_more"]:
                io.send("Enter")
                io.sleep(0.4)
                menu = claude_menu(io.lines()) or menu
                row = claude_row(menu, model)
        if not row:
            close()
            have = ", ".join(r["name"] for r in menu["rows"])
            return _result(False, "not_listed", f'Claude Code\'s menu does not list "{model}" ({have}).',
                           f"Claude Code 的選單裡沒有「{model}」（有：{have}）。")
        for _ in range(3):                                              # up or down to the row, then look again
            menu = claude_menu(io.lines()) or menu
            if menu["cursor"] == row["n"]:
                break
            cur = menu["cursor"] if menu["cursor"] is not None else (len(menu["rows"]) + 1 if menu["on_more"] else 1)
            _press(io, "Down" if row["n"] > cur else "Up", abs(row["n"] - cur))
            io.sleep(0.2)
        menu = claude_menu(io.lines())
        if not menu or menu["cursor"] != row["n"]:
            close()
            return _result(False, "menu_changed", "The menu is not where it was; nothing was changed.", "選單的位置不對，所以沒有換。")
    if effort and menu["effort"] in _LEVELS:                            # the level resets to the row's own when the row changes
        for _ in range(2):
            cur = menu["effort"]
            if cur == effort:
                break
            diff = _LEVELS.index(effort) - _LEVELS.index(cur)
            _press(io, "Right" if diff > 0 else "Left", abs(diff))
            io.sleep(0.25)
            menu = claude_menu(io.lines()) or menu
    menu = claude_menu(io.lines())
    if not menu or not menu["session_key"]:
        close()
        return _result(False, "menu_changed", "The menu closed before the pick; nothing was changed.", "選單在選之前就關了，所以沒有換。")
    io.send({"text": "s"})
    got = _until(io, lambda: claude_confirm(io.lines()), WAIT_DONE)
    if not got:
        close()
        return _result(False, "no_confirm", "Pressed s, but Claude Code did not say it switched.", "按了 s，但 Claude Code 沒有說它換了。")
    if not got["session_only"]:                                          # not what was meant: report, do not hide it
        return _result(True, "saved_default", f"Claude Code switched to {got['name']} and saved it as the default for new sessions.",
                       f"Claude Code 換成 {got['name']}，而且存成了新分頁的預設。", name=got["name"], effort=got["effort"])
    if got["effort"] is None:
        got["effort"] = claude_footer_effort(io.lines())
    return _said(got, effort)


def _drive_codex(io, model, effort) -> dict:
    menu = _until(io, lambda: codex_menu(io.lines()), WAIT_OPEN)
    close = lambda: _close(io, lambda ls: codex_menu(ls) is not None)
    if not menu or menu["kind"] != "model":
        close()
        return _result(False, "no_menu", "Codex's model menu did not open.", "Codex 的模型選單沒有跳出來。")
    if model is None:
        close()
        return _result(False, "not_listed", "Codex changes the level only together with a model: pick the model too.",
                       "Codex 要連模型一起選才能換思考強度，請也選一個模型。")
    row = next((r for r in menu["rows"] if norm(r["id"]) == norm(model)), None)
    if not row:
        close()
        have = ", ".join(r["id"] for r in menu["rows"])
        return _result(False, "not_listed", f'Codex\'s list does not hold "{model}" (it lists {have}). '
                       "Start the tab with -m to run an older one.", f"Codex 的清單裡沒有「{model}」（有：{have}）。要用舊的模型，請用 -m 重開分頁。")
    io.send({"text": str(row["n"])} if row["n"] <= 9 else "Enter")
    nxt = _until(io, lambda: (lambda m: m if m and m["kind"] == "effort" else None)(codex_menu(io.lines())), WAIT_OPEN)
    if not nxt:
        close()
        return _result(False, "menu_changed", "Codex's level menu did not follow; nothing was changed.", "Codex 沒有跳出思考強度選單，所以沒有換。")
    pick_row = next((r for r in nxt["rows"] if r["id"] == effort), None) if effort else None
    before = codex_confirms(io.lines())
    if pick_row:
        io.send({"text": str(pick_row["n"])})
    else:
        io.send("Enter")                              # the level it highlights (its default): reported below
    got = _until(io, lambda: codex_new_confirm(io.lines(), before), WAIT_DONE)
    if not got:
        close()
        return _result(False, "no_confirm", "Codex did not say it switched.", "Codex 沒有說它換了。")
    return _said(got, effort)


def _drive_opencode(io, model, effort) -> dict:
    if not _until(io, lambda: opencode_open(io.lines()), WAIT_OPEN):
        return _result(False, "no_menu", "OpenCode's model list did not open.", "OpenCode 的模型清單沒有跳出來。")
    close = lambda: _close(io, opencode_open)
    if not model:
        close()
        return _result(False, "bad_model", "Type part of a model's name.", "請輸入模型名稱的一部分。")
    before = opencode_current(io.lines())
    io.send({"text": re.sub(r"[-_]+", " ", model.split("/")[-1])})
    io.sleep(0.6)
    if opencode_rows(io.lines()) < 1:
        close()
        return _result(False, "no_match", f'OpenCode\'s list has nothing like "{model}".', f"OpenCode 的清單裡找不到像「{model}」的。")
    io.send("Enter")
    if not _until(io, lambda: not opencode_open(io.lines()), WAIT_DONE):
        close()
        return _result(False, "no_confirm", "OpenCode's list stayed open after Enter.", "按了 Enter，OpenCode 的清單還開著。")
    io.sleep(0.4)
    now = opencode_current(io.lines())
    if not now:
        return _result(True, "unconfirmed", "The list closed, but the model line could not be read.", "清單關了，但讀不到模型那一行。",
                       unconfirmed=True)
    if norm(model.split("/")[-1]) not in norm(now) and now == before:
        return _result(False, "no_confirm", "The model line did not change.", "模型那一行沒有變。")
    return _said({"name": now, "effort": None}, None)


def _drive_pi(io, model, effort) -> dict:
    close = lambda: _close(io, lambda ls: pi_selector(ls) is not None)
    name = None
    if model is not None:
        sel = _until(io, lambda: pi_selector(io.lines()), WAIT_OPEN)
        if not sel:
            return _result(False, "no_menu", "Pi's model selector did not open.", "Pi 的模型選擇器沒有跳出來。")
        io.send({"text": model})
        io.sleep(0.5)
        rows = (pi_selector(io.lines()) or {"rows": []})["rows"]
        hit = [r for r in rows if r["id"].lower() == model.lower()] or rows
        if not hit:
            close()
            return _result(False, "no_match", f'Pi\'s list has nothing like "{model}".', f"Pi 的清單裡找不到像「{model}」的。")
        if len(hit) > 1:
            close()
            have = ", ".join(r["id"] for r in hit[:5])
            return _result(False, "ambiguous", f'Several models match "{model}": {have}. Type more of the name.',
                           f"「{model}」對到好幾個模型：{have}。請多打一點。")
        target, cur = rows.index(hit[0]), next((i for i, r in enumerate(rows) if r["cursor"]), 0)
        _press(io, "Down" if target > cur else "Up", abs(target - cur))
        io.send("Enter")
        if not _until(io, lambda: pi_selector(io.lines()) is None, WAIT_DONE):
            close()
            return _result(False, "no_confirm", "Pi's selector stayed open after Enter.", "按了 Enter，Pi 的選擇器還開著。")
        io.sleep(0.4)
        ft = pi_footer(io.lines())
        conf = pi_confirm(io.lines())
        if not (conf or ft) or (ft and norm(ft["model"]) != norm(hit[0]["id"])):
            return _result(False, "no_confirm", "Pi did not show the new model.", "Pi 的畫面沒有顯示新的模型。")
        name = hit[0]["id"]
    if effort:
        ft = pi_footer(io.lines())
        if not ft or ft["level"] is None:
            return _result(name is not None, "no_effort", f"{name or 'This model'} has no thinking level to set." if name else
                           "This model has no thinking level to set.", "這個模型沒有思考強度可以調。", name=name or "")
        order = list(SWITCH["pi"]["efforts"]) + ["xhigh"]
        for _ in range(len(order) + 1):                 # Shift+Tab goes to the next level and wraps: press, read, until it is the one
            ft = pi_footer(io.lines())
            if ft and ft["level"] == effort:
                break
            io.send("BTab")
            io.sleep(0.5)
        ft = pi_footer(io.lines())
        if not ft or ft["level"] != effort:
            return _result(name is not None, "no_effort", f"Pi would not go to {effort}; it is on {ft['level'] if ft else '?'}.",
                           f"Pi 調不到 {effort}，目前是 {ft['level'] if ft else '?'}。", name=name or "", effort=ft["level"] if ft else None)
    ft = pi_footer(io.lines())
    return _said({"name": name or (ft or {}).get("model", ""), "effort": (ft or {}).get("level")}, effort)


def _drive_command(io, model, effort) -> dict:
    """Grok and Cursor: the line is already typed. Neither says it on the screen (not measured), so it is
    read from the CLI's own files; nothing seen in time is reported as unconfirmed, never as done."""
    def seen():
        info = io.probe() or {}
        have = norm(info.get("name", ""))
        got_model = not model or (bool(have) and (norm(model) in have or have in norm(model)))
        got_eff = not effort or info.get("effort") == effort
        return info if have and got_model and got_eff else None
    info = _until(io, seen, WAIT_PROBE, 0.5)
    if not info:
        return _result(True, "unconfirmed", "Sent, but nothing shows the new model yet (this CLI has not been checked).",
                       "已送出，但還看不到換成新的（這家還沒實測過）。", unconfirmed=True)
    return _said({"name": info.get("name", ""), "effort": info.get("effort")}, effort)
