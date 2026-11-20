"""Office objects: the extension point behind the office's furniture.

A piece of furniture in the office (the bookshelf, the personnel cabinet, the deliverables desk; later
an ops TV) is an *office object*: a declaration (title, which built-in furniture, where it
stands, the filters, sorts and actions its popup offers) and four small hooks. The office draws
the furniture and one shared popup (web/office_objects.js: a list on the left, a Markdown preview
on the right, actions at the bottom) and asks the hooks for everything it shows. The popup
changes nothing on its own: an action either opens something in the person's own programs, sends one line to
a bot's tab, navigates (to another object, a wall view, a bot's settings), or is the person's own decision on the
object's records (the deliverables: keep, take).

The four hooks (all optional for a plugin; each call is wrapped in try/except and a time limit, so
a broken or stuck object makes only its own popup say "not available now"):

    summary(obj_id)                                  -> {"badge": int, "hint": str, "by_owner"?, "by_task"?}
        for the furniture's red number; a few stats, no content read; the page asks at most
        every 30 s while the office is open. `by_owner` ({key: {label, count, new, types, closed}}):
        an object with one pile per person (the deliverables), drawn by the office on each desk
    list(obj_id, query, filters, sort, offset, limit) -> {"groups": [{id, title}], "items": [...],
                                                          "total": int}
        item: {id, group, title, subtitle, type, owner, owner_key, mtime, size, limit, fresh,
               tags: [{id, label, tone}]}; asked when the popup opens and when the person
               types, filters or sorts
    item(obj_id, item_id)                            -> {"title", "subtitle", "markdown", "meta":
                                                         [[label, value]], "chips": [{text, tone}],
                                                         "links": [...], "actions": [action id],
                                                         "action_labels", "image", "refresh"}
        asked when one entry is opened; the only place its file is read. `image`: a raster
        data: URL the object read for this entry (kept as it is, shown in an <img>)
    action(obj_id, item_id, action_id, args)         -> {"ok": bool, "message": str,
                                                         "open": path, "reveal": path, "url": link,
                                                         "tell": {"to", "text", "after"},
                                                         "confirm": {...}, "refresh": bool, "go": {...}}
        `open` / `reveal` / `url` are done by the app's own openers; `tell` is sent by the page into that
        bot's tab (one line, through the chat view's send; `after` is asked of the object once it went
        in); `confirm` makes the popup ask yes/no first; `refresh` makes it read the list again; `go`
        navigates the page. An action declared with `input` first asks the person for one line (args.text)

A built-in object may also answer `visible()` (False while its own switch is off: not in the registry) and
`view(ctx)`, a detail screen as widgets (`WIDGET_KINDS`, cleaned by `clean_view`), and its summary may carry a
`level` / `reason` / `since` / `face` for a TV on the wall (`clean_summary`). Plugins have neither in this
version: no `office_view`, no collector (the ops TV, office_ops.py, is the only one).

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
FURNITURE = ("bookshelf", "cabinet", "noticeboard", "mailbox", "tv", "generic")
PLACES = ("lobby:wall", "lobby:near-primary", "button")     # `zone:<id>` stands where lobby:wall does; "button" has no furniture, only the toolbar button
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
    "off": ("This is switched off.", "這個功能沒有打開。"),
    "no_view": ("This object has no detail screen.", "這個物件沒有詳細畫面。"),
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
                            "needs_tab": bool(a.get("needs_tab")), "primary": bool(a.get("primary")),
                            "input": _name(loc(a.get("input"), lang), 120)})      # a one-line box the person fills first (its placeholder)
    return {
        "id": oid,
        "title": _name(loc(raw.get("title"), lang) or oid, 40),
        "short": _name(loc(raw.get("short"), lang), 12),          # the toolbar button's word; the title when there is none
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
LEVELS = ("ok", "warn", "alert", "none", "muted")
PRESSURES = ("normal", "warn", "critical")
FACE_RANGES = {"cpu": (0, 100), "mem": (0, 100), "disk_free_pct": (0, 100), "net_in": (0, 1e12), "net_out": (0, 1e12)}


def _fnum(v, lo, hi):
    """A finite number clamped to lo..hi, else None (a bool, a string and NaN are not numbers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return None
    return max(lo, min(hi, v))


def _clean_face(raw) -> dict:
    """The ops TV's gauges: only the keys the office draws, each number clamped, `spark` at most 12 numbers."""
    raw = raw if isinstance(raw, dict) else {}
    face = {k: _fnum(raw.get(k), *FACE_RANGES[k]) for k in FACE_RANGES}
    face["pressure"] = raw.get("pressure") if raw.get("pressure") in PRESSURES else None
    face["online"] = raw["online"] if isinstance(raw.get("online"), bool) else None
    face["spark"] = [v for v in (_fnum(x, 0, 100) for x in (raw.get("spark") or [])[-12:]
                                 if isinstance(raw.get("spark"), (list, tuple))) if v is not None]
    return face


def clean_summary(raw) -> dict:
    """The badge and hint, and (an object that has one per person) `by_owner`: {key: {label, count, new, types, closed}},
    which the office draws on each person's desk, and `by_task`: {task id: count}. An object that has a face to show
    (the ops TV) adds `level` (one of LEVELS), `reason` (a line, masked), `since` / `muted_until` / `updated` (epoch
    seconds or null) and `face`; they are kept only when the object gave them."""
    raw = raw if isinstance(raw, dict) else {}
    out = {"badge": max(0, min(999, _num(raw.get("badge")))), "hint": _name(raw.get("hint"), 120)}
    if "level" in raw:
        out["level"] = raw["level"] if raw["level"] in LEVELS else "none"
        out["reason"] = _name(raw.get("reason"), 120)
        for k in ("since", "muted_until", "updated"):
            v = _fnum(raw.get(k), 0, 4e9)
            out[k] = int(v) if v is not None else None
        out["face"] = _clean_face(raw.get("face"))
    if isinstance(raw.get("by_owner"), dict):
        by = {}
        for key, v in list(raw["by_owner"].items())[:400]:
            if isinstance(v, dict):
                by[_ident(key, 120)] = {"label": _name(v.get("label"), 60), "count": max(0, _num(v.get("count"))),
                                        "new": max(0, _num(v.get("new"))), "closed": bool(v.get("closed")),
                                        "types": [_ident(t, 20) for t in (v.get("types") or [])[:3]]}
        out["by_owner"] = by
    if isinstance(raw.get("by_task"), dict):
        out["by_task"] = {_ident(k, 20): max(0, _num(v)) for k, v in list(raw["by_task"].items())[:400]}
    return out


# ── a detail screen's widgets (`office_object_view`): eight kinds, drawn by the office ──
WIDGET_KINDS = ("gauge", "bars", "stack", "spark", "strip", "table", "stat", "rules")
STRIP_CELLS = ("ok", "warn", "alert", "none")
SECTIONS_MAX, WIDGETS_MAX, POINTS_MAX = 12, 12, 600


def _vnum(v):
    return _fnum(v, -1e15, 1e15)


def _vtone(v):
    return v if v in TONES else ""


def _switch(raw):
    """A table's optional row of buttons: {"action", "value", "options": [{"id", "label"}], "note"}, or None when it has
    no action id or fewer than two valid options. `value` is one of the option ids (else the first)."""
    if not isinstance(raw, dict) or not _ID.fullmatch(str(raw.get("action") or "")):
        return None
    opts = [{"id": o["id"], "label": _name(o.get("label"), 30)} for o in (raw.get("options") or [])[:6]
            if isinstance(o, dict) and _ID.fullmatch(str(o.get("id") or ""))]
    if len(opts) < 2:
        return None
    value = raw.get("value") if raw.get("value") in [o["id"] for o in opts] else opts[0]["id"]
    return {"action": raw["action"], "value": value, "options": opts, "note": _name(raw.get("note"), 120)}


def _widget(w):
    """One widget as the page may take it, or None (an unknown kind is dropped, not an error)."""
    if not isinstance(w, dict) or w.get("kind") not in WIDGET_KINDS:
        return None
    kind, label = w["kind"], _name(w.get("label"), 80)
    if kind == "gauge":
        return {"kind": kind, "label": label, "value": _fnum(w.get("value"), 0, 100), "text": _name(w.get("text"), 20),
                "tone": _vtone(w.get("tone"))}
    if kind == "bars":
        items = [{"label": _name(i.get("label"), 12), "value": _fnum(i.get("value"), 0, 100),
                  "group": _name(i.get("group"), 8)} for i in (w.get("items") or [])[:128] if isinstance(i, dict)]
        return {"kind": kind, "label": label, "items": items}
    if kind == "stack":
        parts = [{"label": _name(i.get("label"), 30), "value": max(0, _vnum(i.get("value")) or 0), "tone": _vtone(i.get("tone"))}
                 for i in (w.get("parts") or [])[:8] if isinstance(i, dict)]
        return {"kind": kind, "label": label, "total": max(0, _vnum(w.get("total")) or 0), "parts": parts}
    if kind == "spark":
        series = [{"label": _name(s.get("label"), 30), "tone": _vtone(s.get("tone")),
                   "values": [_vnum(x) for x in (s.get("values") or [])[:POINTS_MAX]]}
                  for s in (w.get("series") or [])[:4] if isinstance(s, dict)]
        return {"kind": kind, "label": label, "unit": _name(w.get("unit"), 12), "max": _vnum(w.get("max")), "series": series}
    if kind == "strip":
        return {"kind": kind, "label": label, "start": int(_fnum(w.get("start"), 0, 4e9) or 0),
                "step_s": int(_fnum(w.get("step_s"), 1, 86400) or 1),
                "cells": [c if c in STRIP_CELLS else "none" for c in (w.get("cells") or [])[:POINTS_MAX]]}
    if kind == "table":
        out = {"kind": kind, "columns": [_name(c, 30) for c in (w.get("columns") or [])[:6]],
               "rows": [[_name(c, 80) for c in r[:6]] for r in (w.get("rows") or [])[:20] if isinstance(r, (list, tuple))]}
        sw = _switch(w.get("switch"))
        if sw:
            out["switch"] = sw
        return out
    if kind == "stat":
        return {"kind": kind, "label": label, "value": _name(w.get("value"), 40), "sub": _name(w.get("sub"), 120),
                "tone": _vtone(w.get("tone"))}
    items = []
    for r in (w.get("items") or [])[:20]:
        if isinstance(r, dict) and _ID.fullmatch(str(r.get("id") or "")):
            items.append({"id": r["id"], "label": _name(r.get("label"), 120), "on": bool(r.get("on")),
                          "value": _vnum(r.get("value")), "unit": _name(r.get("unit"), 30),
                          "level": r.get("level") if r.get("level") in ("warn", "alert") else "warn",
                          "min": _vnum(r.get("min")), "max": _vnum(r.get("max")), "step": _vnum(r.get("step")) or 1})
    out = {"kind": "rules", "items": items}
    if w.get("readonly") is True:                        # a paired computer's rules: shown, not changed from here
        out["readonly"] = True
    return out


def clean_view(raw) -> dict:
    """What `office_object_view` answers: a title, a banner, and sections of widgets (WIDGET_KINDS only), every text
    cut and masked and every number a plain finite number. The caller adds `success`."""
    raw = raw if isinstance(raw, dict) else {}
    ban = raw.get("banner") if isinstance(raw.get("banner"), dict) else {}
    since = _fnum(ban.get("since"), 0, 4e9)
    sections = []
    for sec in (raw.get("sections") or [])[:SECTIONS_MAX]:
        if isinstance(sec, dict) and _ID.fullmatch(str(sec.get("id") or "")):
            widgets = [x for x in (_widget(w) for w in (sec.get("widgets") or [])[:WIDGETS_MAX]) if x]
            sections.append({"id": sec["id"], "title": _name(sec.get("title"), 60), "widgets": widgets})
    refresh = _fnum(raw.get("refresh_ms"), 500, 60000)
    return {"refresh_ms": int(refresh) if refresh else 2000, "title": _name(raw.get("title"), 60),
            "banner": {"level": ban.get("level") if ban.get("level") in LEVELS else "none", "text": _name(ban.get("text"), 160),
                       "since": int(since) if since is not None else None, "rule": _name(ban.get("rule"), 40)},
            "sections": sections}


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
    if raw.get("section") and _ID.fullmatch(str(raw["section"])):
        out["section"] = str(raw["section"])        # drawn where the markdown says {{links:<section>}}; else at the end
    if raw.get("tone") in TONES:
        out["tone"] = raw["tone"]
    return out


_IMAGE = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+")
IMAGE_MAX = 6_000_000            # characters of one inline picture (a data: URL)


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
            "action_labels": {k: _name(v, 80) for k, v in (raw.get("action_labels") or {}).items()
                              if isinstance(raw.get("action_labels"), dict) and k in known},
            # a picture the object read for this entry: only a data: URL of a raster image, passed as it is (never masked,
            # never scripted: the page puts it in an <img>); anything else is dropped
            "image": raw["image"] if isinstance(raw.get("image"), str) and len(raw["image"]) <= IMAGE_MAX
            and _IMAGE.fullmatch(raw["image"]) else "",
            "owner": _name(raw.get("owner"), 60), "owner_key": _ident(raw.get("owner_key"), 80),
            "refresh": bool(raw.get("refresh"))}          # opening it changed what the list says (a "seen" mark): the popup reads the list again


def clean_action(raw, trusted=False) -> dict:
    """What an action answered. `trusted` (a built-in object) keeps a long file name in a line to a bot readable: the
    32-character rule is for text nobody checked (a plugin's); the other masking rules apply to both."""
    raw = raw if isinstance(raw, dict) else {}
    out = {"ok": raw.get("ok") is not False, "message": _name(raw.get("message"), 200)}
    tell = raw.get("tell")
    if isinstance(tell, dict) and tell.get("to") and str(tell.get("text") or "").strip():
        out["tell"] = {"to": _ident(tell["to"], 80),
                       "text": clip(" ".join(redact_text(tell["text"], strict=not trusted).split()), 600)}
        after = tell.get("after")             # asked of the object once the line went in (a note on a task)
        if isinstance(after, dict) and _ID.fullmatch(str(after.get("action") or "")):
            out["tell"]["after"] = {"action": after["action"], "args": {str(k)[:40]: _ident(v, 300) for k, v in
                                                                         (after.get("args") or {}).items()}}
    if raw.get("refresh"):
        out["refresh"] = True                 # the list changed: the popup reads it again
    conf = raw.get("confirm")
    if isinstance(conf, dict) and conf.get("text"):
        out["confirm"] = {"text": _name(conf["text"], 200), "label": _name(conf.get("label") or "OK", 40),
                          "args": {str(k)[:40]: _ident(v, 60) for k, v in (conf.get("args") or {}).items()}}
    go = raw.get("go")
    if isinstance(go, dict):
        link = clean_link(dict(go, label=go.get("label") or "go")) or ({"settings": _ident(go["settings"], 80)}
                                                                       if go.get("settings") else None)
        if link:
            out["go"] = {k: v for k, v in link.items() if k != "label"}
    for k in ("open", "reveal", "url"):
        if raw.get(k):
            out[k] = str(raw[k])              # a path or a URL the object derived itself; the host opens it, the page never sees it
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

    def visible(self) -> bool:
        """False while the object's own switch is off (the ops TV): it is then not in the registry at all."""
        return True

    def summary(self, ctx) -> dict:
        return {"badge": 0}

    def list(self, ctx, query="", filters=None, sort="", offset=0, limit=LIST_MAX) -> dict:
        return {"groups": [], "items": [], "total": 0}

    def item(self, ctx, item_id) -> dict:
        return {}

    def view(self, ctx):
        """A detail screen as widgets (`office_object_view`), or None: an object without one. Built-ins only."""
        return None

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
        found = {o.id: o for o in self.builtins if o.id and o.visible()}
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

    def view(self, obj_id, ctx, lang) -> dict:
        """A built-in object's detail screen as widgets, cleaned. A plugin's object has none in this version."""
        obj = self.objects().get(str(obj_id))
        if obj is None:
            return {"success": False, "code": "unknown", "message": words("unknown", lang)}
        if isinstance(obj, PluginObject) or type(obj).view is OfficeObject.view:
            return {"success": False, "code": "no_view", "message": words("no_view", lang)}
        ok, res = call_limited(obj.view, ctx)
        if not ok:
            return {"success": False, "code": res, "message": words(res, lang)}
        if not isinstance(res, dict) or res.get("success") is False:
            return {"success": False, "code": "unavailable", "message": words("unavailable", lang)}
        return dict(clean_view(res), success=True)

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
        return dict(clean_action(res, trusted=not isinstance(obj, PluginObject)), success=True)
