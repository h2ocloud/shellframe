"""Office objects: the extension point behind the office's furniture.

A piece of furniture in the office (the bookshelf, the personnel cabinet; later a deliverables
desk, an ops TV) is an *office object*: a declaration (title, which built-in furniture, where it
stands, the filters, sorts and actions its popup offers) and four small hooks. The office draws
the furniture and one shared popup (web/office_objects.js: a list on the left, a Markdown preview
on the right, actions at the bottom) and asks the hooks for everything it shows. The popup is
read-only: an action either changes nothing (open a file in the person's editor), sends one line to
a bot's tab, or navigates (to another object, a wall view, a bot's settings).

The four hooks (all optional for a plugin; each call is wrapped in try/except and a time limit, so
a broken or stuck object makes only its own popup say "not available now"):

    summary(obj_id)                                  -> {"badge": int, "hint": str}
        for the furniture's red number; a few stats, no content read; the page asks at most
        every 30 s while the office is open
    list(obj_id, query, filters, sort, offset, limit) -> {"groups": [{id, title}], "items": [...],
                                                          "total": int}
        item: {id, group, title, subtitle, type, owner, owner_key, mtime, size, limit, fresh,
               tags: [{id, label, tone}]}; asked when the popup opens and when the person
               types, filters or sorts
    item(obj_id, item_id)                            -> {"title", "subtitle", "markdown", "meta":
                                                         [[label, value]], "chips": [{text, tone}],
                                                         "links": [...], "actions": [action id]}
        asked when one entry is opened; the only place its file is read
    action(obj_id, item_id, action_id, args)         -> {"ok": bool, "message": str,
                                                         "open": path, "tell": {"to", "text"},
                                                         "go": {...}}
        `open` is opened by the app's own opener; `tell` is sent by the page into that bot's tab
        (one line, through the chat view's send); `go` navigates the page

What the host does to every answer (so no object can forget it): strings are cut to a length,
anything that looks like a secret is masked (`redact_text`: line by line, Markdown stays
Markdown), a list is capped, the page's language picks each {en, zh} text. An `item_id` comes
from the page: an object must look it up again in what it lists, never join it into a path.

An object is built in (`OfficeObject` subclasses in office_bookshelf.py / office_personnel.py) or comes
from a plugin: a manifest with `office_objects` (the declaration, a list) and a `Plugin` with the
hook methods `office_summary` / `office_list` / `office_item` / `office_action` (see plugin_sdk.py
and docs/architecture.md). A plugin's hooks are only called while it is enabled and the office is
open. Leaf module: stdlib and agent_status's masking patterns; no `import main`.
"""

import re
import threading

import agent_status

HOOK_TIMEOUT = 3.0           # seconds a hook may take before the popup says it is not available
FURNITURE = ("bookshelf", "cabinet", "noticeboard", "mailbox", "generic")
PLACES = ("lobby:wall", "lobby:near-primary")     # `zone:<id>` is accepted and stands where lobby:wall does
OBJECTS_MAX = 12
LIST_MAX = 400               # entries one answer may carry
GROUPS_MAX = 200
MARKDOWN_MAX = 60_000        # characters of one entry's body
TITLE_MAX = 200
VALUE_MAX = 600
LINKS_MAX = 40
TONES = ("", "ok", "warn", "bad", "info", "dim")
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")

_WORDS = {
    "unavailable": ("This object is not available right now.", "這個物件現在打不開。"),
    "timeout": ("This object took too long to answer.", "這個物件回應太久，現在打不開。"),
    "unknown": ("There is no such object.", "沒有這個物件。"),
    "not_found": ("That entry is gone.", "這一筆已經不在了。"),
    "bad_action": ("That action is not available here.", "這裡沒有這個動作。"),
}


def lang_of(lang) -> str:
    return "zh" if str(lang or "").lower().startswith("zh") else "en"


def loc(value, lang="en") -> str:
    """A text that may be {en, zh}: the one for `lang`, else the other, else the plain string."""
    if isinstance(value, dict):
        want = lang_of(lang)
        for key in (want, "en" if want == "zh" else "zh", "en", "zh"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        return ""
    return "" if value is None else str(value)


def words(code: str, lang="en") -> str:
    en, zh = _WORDS.get(code, _WORDS["unavailable"])
    return zh if lang_of(lang) == "zh" else en


# ── masking ──────────────────────────────────────────────────────────────────
# agent_status.redact() makes one short line of a text; an entry's body has to stay Markdown, so the
# same patterns are applied line by line here, plus the ones a notes file or a config page tends to
# hold: `password: x`, `token = x`, a private key block, an AWS key id, a Slack token.
_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)
_ASSIGN = re.compile(
    r"(?i)\b((?:pass(?:word|wd|phrase)?|pwd|secret|token|api[_ -]?key|access[_ -]?key|private[_ -]?key|"
    r"client[_ -]?secret|credentials?|authorization)[\w-]*[\"']?\s*(?:[:=：]|\bis\b)\s*[\"']?)"
    r"(?!<redacted>)(?:(?:Bearer|Basic)\s+)?([^\s\"',;]{3,})")
_KNOWN = re.compile(r"(AKIA[0-9A-Z]{16}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_\-]{30,}|glpat-[A-Za-z0-9_\-]{16,}"
                    r"|npm_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}"
                    r"|gh[pousr]_[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9._\-]{20,})")
_LONG = re.compile(r"[A-Za-z0-9_\-]{32,}")
_MASK = "<redacted>"


def _long_run(m):
    return m.group(0) if not re.search(r"[A-Za-z0-9]", m.group(0)) else _MASK   # a rule of dashes is not a key


def redact_text(text, strict=True) -> str:
    """`text` with anything that looks like a secret masked, line breaks kept. `strict` also masks
    any 32-character run of letters, digits, `_` and `-` (an unknown key); a title or a name is
    masked without it, so a long skill name stays readable."""
    out = _KEY_BLOCK.sub(_MASK, str(text or ""))
    out = agent_status._URL_CRED_RE.sub(r"\1<redacted>@", out)
    out = _KNOWN.sub(_MASK, out)
    out = _ASSIGN.sub(lambda m: m.group(1) + _MASK, out)
    if strict:
        out = _LONG.sub(_long_run, out)
    return out


def clip(text, n) -> str:
    t = "" if text is None else str(text)
    return t if len(t) <= n else t[:max(0, n - 1)].rstrip() + "…"


def _name(v, n=TITLE_MAX) -> str:
    return clip(redact_text(" ".join(str(v if v is not None else "").split()), strict=False), n)


def _body(v, n=VALUE_MAX) -> str:
    return clip(redact_text(" ".join(str(v if v is not None else "").split())), n)


def _num(v, default=0):
    try:
        return int(v) if v is not None and v is not True and v is not False else default
    except (TypeError, ValueError):
        return default


def _tone(v) -> str:
    return v if v in TONES else ""


def _ident(v, n=300) -> str:
    return clip(str(v if v is not None else ""), n)


# ── the declaration ──────────────────────────────────────────────────────────
def normalize_declaration(raw, lang="en"):
    """A declaration as the page takes it (every text already in `lang`), or None when it has no
    usable id. Only the built-in furniture is known: the extension cannot draw its own."""
    if not isinstance(raw, dict):
        return None
    oid = str(raw.get("id") or "")
    if not _ID.fullmatch(oid):
        return None
    furniture = raw.get("furniture") if raw.get("furniture") in FURNITURE else "generic"
    place = str(raw.get("place") or "lobby:wall")
    if place not in PLACES and not re.fullmatch(r"zone:[\w.\-]{1,40}", place):
        place = "lobby:wall"
    filters = []
    for f in raw.get("filters") or []:
        if isinstance(f, dict) and _ID.fullmatch(str(f.get("id") or "")):
            opts = [[_ident(o[0], 60), _name(loc(o[1], lang), 60)] for o in (f.get("options") or [])
                    if isinstance(o, (list, tuple)) and len(o) >= 2][:30]
            filters.append({"id": f["id"], "label": _name(loc(f.get("label"), lang), 40), "options": opts})
    sorts = [[_ident(s[0], 40), _name(loc(s[1], lang), 40)] for s in (raw.get("sorts") or [])
             if isinstance(s, (list, tuple)) and len(s) >= 2][:8]
    actions = []
    for a in raw.get("actions") or []:
        if isinstance(a, dict) and _ID.fullmatch(str(a.get("id") or "")):
            actions.append({"id": a["id"], "label": _name(loc(a.get("label"), lang), 80),
                            "needs_tab": bool(a.get("needs_tab")), "primary": bool(a.get("primary"))})
    return {
        "id": oid,
        "title": _name(loc(raw.get("title"), lang) or oid, 40),
        "subtitle": _name(loc(raw.get("subtitle"), lang), 160),
        "furniture": furniture,
        "place": place,
        "button": raw.get("button", True) is not False,
        "badge_hint": _name(loc(raw.get("badge_hint"), lang), 120),
        "search_hint": _name(loc(raw.get("search_hint"), lang), 120),
        "empty": _name(loc(raw.get("empty"), lang), 160),
        "readonly_note": _name(loc(raw.get("readonly_note"), lang), 160),
        "filters": filters, "sorts": sorts, "actions": actions,
    }


# ── what comes back from a hook ──────────────────────────────────────────────
def clean_summary(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {"badge": max(0, min(999, _num(raw.get("badge")))), "hint": _name(raw.get("hint"), 120)}


def clean_list(raw, lang="en") -> dict:
    raw = raw if isinstance(raw, dict) else {}
    groups = []
    for g in (raw.get("groups") or [])[:GROUPS_MAX]:
        if isinstance(g, dict) and g.get("id") is not None:
            groups.append({"id": _ident(g["id"]), "title": _name(g.get("title") or g["id"], 80),
                           "note": _name(g.get("note"), 80), "tag": _name(g.get("tag"), 30),
                           "count": max(0, _num(g.get("count")))})
    items = []
    for it in (raw.get("items") or [])[:LIST_MAX * 2]:
        if len(items) >= LIST_MAX:
            break
        if not isinstance(it, dict) or it.get("id") is None:
            continue
        tags = [{"id": _ident(t.get("id"), 40), "label": _name(t.get("label"), 40), "tone": _tone(t.get("tone"))}
                for t in (it.get("tags") or [])[:6] if isinstance(t, dict) and t.get("label")]
        items.append({"id": _ident(it["id"]), "group": _ident(it.get("group")), "title": _name(it.get("title") or it["id"]),
                      "subtitle": _name(it.get("subtitle"), VALUE_MAX), "type": _ident(it.get("type"), 40),
                      "owner": _name(it.get("owner"), 60), "owner_key": _ident(it.get("owner_key"), 80),
                      "mtime": max(0, _num(it.get("mtime"))), "size": max(0, _num(it.get("size"))),
                      "limit": max(0, _num(it.get("limit"))), "fresh": bool(it.get("fresh")),
                      "tone": _tone(it.get("tone")), "tags": tags})
    return {"groups": groups, "items": items, "total": max(len(items), _num(raw.get("total"), len(items))),
            "note": _name(raw.get("note"), 160)}


def clean_link(raw):
    """One link of an entry: to another object (`object` + `filters` / `query` / `item`), a wall
    view of the office (`view`: board | cal, `task` for a card), or a bot's tab (`follow`)."""
    if not isinstance(raw, dict) or not raw.get("label"):
        return None
    out = {"label": _name(raw["label"], 120)}
    if raw.get("object") and _ID.fullmatch(str(raw["object"])):
        out["object"] = str(raw["object"])
        f = raw.get("filters")
        out["filters"] = {str(k)[:40]: _ident(v, 60) for k, v in f.items()} if isinstance(f, dict) else {}
        out["query"] = _name(raw.get("query"), 120)
        if raw.get("item") is not None:
            out["item"] = _ident(raw["item"])
    elif raw.get("view") in ("board", "cal"):
        out["view"] = raw["view"]
        if raw.get("task") is not None:
            out["task"] = _num(raw["task"], None)
    elif raw.get("follow"):
        out["follow"] = _ident(raw["follow"], 80)
    else:
        return None
    if raw.get("note"):
        out["note"] = _name(raw["note"], 80)
    if raw.get("tone") in TONES:
        out["tone"] = raw["tone"]
    return out


def clean_item(raw, declared_actions=()) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    meta = [[_name(m[0], 40), _body(m[1])] for m in (raw.get("meta") or [])[:20]
            if isinstance(m, (list, tuple)) and len(m) >= 2]
    chips = [{"text": _name(c.get("text"), 40), "tone": _tone(c.get("tone"))} for c in (raw.get("chips") or [])[:12]
             if isinstance(c, dict) and c.get("text")]
    links = [x for x in (clean_link(l) for l in (raw.get("links") or [])[:LINKS_MAX]) if x]
    known = {a["id"] for a in declared_actions}
    actions = []
    for a in raw.get("actions") or []:
        aid = a.get("id") if isinstance(a, dict) else a
        if str(aid) in known and str(aid) not in actions:
            actions.append(str(aid))
    sections = []
    for s in (raw.get("sections") or [])[:8]:
        if isinstance(s, dict) and s.get("markdown"):
            sections.append({"title": _name(s.get("title"), 80),
                             "markdown": clip(redact_text(s["markdown"]), MARKDOWN_MAX // 4)})
    return {"title": _name(raw.get("title")), "subtitle": _name(raw.get("subtitle"), VALUE_MAX),
            "markdown": clip(redact_text(raw.get("markdown")), MARKDOWN_MAX),
            "note": _name(raw.get("note"), 200), "meta": meta, "chips": chips, "links": links,
            "sections": sections, "actions": actions,
            "action_hints": {k: _name(v, 120) for k, v in (raw.get("action_hints") or {}).items()
                             if isinstance(raw.get("action_hints"), dict) and k in known},
            "owner": _name(raw.get("owner"), 60), "owner_key": _ident(raw.get("owner_key"), 80)}


def clean_action(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {"ok": raw.get("ok") is not False, "message": _name(raw.get("message"), 200)}
    tell = raw.get("tell")
    if isinstance(tell, dict) and tell.get("to") and str(tell.get("text") or "").strip():
        out["tell"] = {"to": _ident(tell["to"], 80), "text": clip(" ".join(redact_text(tell["text"]).split()), 600)}
    go = raw.get("go")
    if isinstance(go, dict):
        link = clean_link(dict(go, label=go.get("label") or "go")) or ({"settings": _ident(go["settings"], 80)}
                                                                       if go.get("settings") else None)
        if link:
            out["go"] = {k: v for k, v in link.items() if k != "label"}
    if raw.get("open"):
        out["open"] = str(raw["open"])        # a path the object derived itself; the host opens it, the page never sees it
    return out


# ── the objects ──────────────────────────────────────────────────────────────
class Ctx:
    """What a built-in hook is given: the page's language, `t(en, zh)`, and the host's data as
    callables passed by name (`Ctx("en", roster=lambda: {...})`; api_office_objects.py builds the
    real one, a test builds a fake). A provider the host did not give answers None."""

    def __init__(self, lang="en", **providers):
        self.lang = lang_of(lang)
        self._p = providers

    def t(self, en, zh):
        return zh if self.lang == "zh" else en

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._p.get(name) or (lambda *a, **k: None)


class OfficeObject:
    """Base of a built-in object. `declaration` is the manifest's `office_objects` entry; each hook
    gets `ctx` (the host's data: see api_office_objects.py) and returns what the module doc says."""
    declaration = {}

    @property
    def id(self) -> str:
        return str(self.declaration.get("id") or "")

    def summary(self, ctx) -> dict:
        return {"badge": 0}

    def list(self, ctx, query="", filters=None, sort="", offset=0, limit=LIST_MAX) -> dict:
        return {"groups": [], "items": [], "total": 0}

    def item(self, ctx, item_id) -> dict:
        return {}

    def action(self, ctx, item_id, action_id, args) -> dict:
        return {"ok": False}


class PluginObject(OfficeObject):
    """A plugin's declaration and its `office_*` hooks as an object (plugin hooks take no ctx)."""

    def __init__(self, plugin, declaration):
        self.plugin = plugin
        self.declaration = declaration

    def _hook(self, name):
        fn = getattr(self.plugin, name, None)
        return fn if callable(fn) else None

    def summary(self, ctx):
        fn = self._hook("office_summary")
        return fn(self.id) if fn else {"badge": 0}

    def list(self, ctx, query="", filters=None, sort="", offset=0, limit=LIST_MAX):
        fn = self._hook("office_list")
        return fn(self.id, query, filters or {}, sort, offset, limit) if fn else {"groups": [], "items": [], "total": 0}

    def item(self, ctx, item_id):
        fn = self._hook("office_item")
        return fn(self.id, item_id) if fn else {}

    def action(self, ctx, item_id, action_id, args):
        fn = self._hook("office_action")
        return fn(self.id, item_id, action_id, args or {}) if fn else {"ok": False}


def call_limited(fn, *args, timeout=None):
    """(ok, value-or-code): `fn` run on a thread of its own that is given up on after `timeout`
    seconds (a hook stuck on a slow disk must not hold the page's call), exceptions caught."""
    box = {}

    def run():
        try:
            box["v"] = fn(*args)
        except Exception:
            box["e"] = True

    t = threading.Thread(target=run, daemon=True, name="office-object")
    t.start()
    t.join(HOOK_TIMEOUT if timeout is None else timeout)
    if t.is_alive():
        return False, "timeout"
    if "e" in box:
        return False, "unavailable"
    return True, box.get("v")


class Registry:
    """The built-in objects and the enabled plugins' objects, and the one place a hook is called."""

    def __init__(self, builtins, plugins=None):
        self.builtins = list(builtins)
        self.plugins = plugins or (lambda: [])      # -> the enabled plugins (SFPlugin instances)

    def objects(self) -> dict:
        found = {o.id: o for o in self.builtins if o.id}
        try:
            plugins = list(self.plugins() or [])
        except Exception:
            plugins = []
        for p in plugins:
            raw = getattr(getattr(p, "manifest", None), "raw", None) or {}
            for decl in raw.get("office_objects") or []:
                oid = str(decl.get("id") or "") if isinstance(decl, dict) else ""
                if oid and oid not in found and _ID.fullmatch(oid) and len(found) < OBJECTS_MAX:
                    found[oid] = PluginObject(p, decl)
        return found

    def describe(self, ctx, lang="en") -> list:
        """Every object's declaration with its badge (one summary call each, none for an object
        whose summary fails: it shows no number)."""
        out = []
        for obj in self.objects().values():
            decl = normalize_declaration(obj.declaration, lang)
            if not decl:
                continue
            ok, res = call_limited(obj.summary, ctx)
            decl.update(clean_summary(res) if ok else {"badge": 0, "hint": ""})
            if decl["hint"] == "":
                decl["hint"] = decl["badge_hint"]
            out.append(decl)
        return out

    def _run(self, obj_id, hook, ctx, lang, *args):
        obj = self.objects().get(str(obj_id))
        if obj is None:
            return None, {"success": False, "code": "unknown", "message": words("unknown", lang)}
        ok, res = call_limited(getattr(obj, hook), ctx, *args)
        if not ok:
            return obj, {"success": False, "code": res, "message": words(res, lang)}
        return obj, res

    def list(self, obj_id, ctx, lang, query="", filters=None, sort="", offset=0, limit=LIST_MAX) -> dict:
        obj, res = self._run(obj_id, "list", ctx, lang, str(query or "")[:200],
                             filters if isinstance(filters, dict) else {}, str(sort or "")[:40],
                             max(0, _num(offset)), max(1, min(LIST_MAX, _num(limit, LIST_MAX))))
        if obj is None or (isinstance(res, dict) and res.get("success") is False):
            return res
        return dict(clean_list(res, lang), success=True)

    def item(self, obj_id, ctx, lang, item_id) -> dict:
        obj, res = self._run(obj_id, "item", ctx, lang, _ident(item_id))
        if obj is None or (isinstance(res, dict) and res.get("success") is False):
            return res
        if not res:
            return {"success": False, "code": "not_found", "message": words("not_found", lang)}
        decl = normalize_declaration(obj.declaration, lang) or {}
        return {"success": True, "item": clean_item(res, decl.get("actions") or [])}

    def action(self, obj_id, ctx, lang, item_id, action_id, args) -> dict:
        obj = self.objects().get(str(obj_id))
        decl = normalize_declaration(obj.declaration, lang) if obj else None
        if decl is None:
            return {"success": False, "code": "unknown", "message": words("unknown", lang)}
        if str(action_id) not in {a["id"] for a in decl["actions"]}:
            return {"success": False, "code": "bad_action", "message": words("bad_action", lang)}
        obj, res = self._run(obj_id, "action", ctx, lang, _ident(item_id), str(action_id),
                             args if isinstance(args, dict) else {})
        if isinstance(res, dict) and res.get("success") is False:
            return res
        return dict(clean_action(res), success=True)
