"""The office's bookshelf (a built-in office object, see office_objects.py).

What it holds, read only and only on this computer:
  - each bot's MEMORY.md and its latest conversation summaries (`sessions/*.md`) from the bot's own folder,
  - each bot's own skills (`skills/<name>/SKILL.md`),
  - the shared skills (`~/.claude/skills`, `~/.codex/skills`, … `bots.SKILL_ROOTS`), with the programs
    that can read each one.
A skill is its SKILL.md and the `.md` files that SKILL.md links to by a relative link that stays inside
the skill's folder. Nothing else in a skill's folder is listed or opened (scripts carry tokens now and
then: the first defence is never to touch them, the masking in office_objects is the second).

The red number on the furniture is how many memory bots have a MEMORY.md of more than 80 % of
`bots.MEMORY_CAP` characters (counted in characters, as the cap is: not bytes, which triple for CJK).

An `item_id` comes from the page, so nothing here joins it into a path: `_catalog()` lists what exists
(from the roster and the skill roots) and an id is only ever looked up in that listing.
"""

import re
import time
from datetime import datetime
from pathlib import Path

import bots
import office_objects as oo
import sf_lang

NEAR_FULL = 0.8                       # of MEMORY_CAP
SESSIONS_PER_BOT = 5
DOCS_PER_SKILL = 20
READ_MAX = 200_000                    # bytes read from one file
SEARCH_MAX = 64_000                   # bytes of a file searched for a typed query
SHARED = "shared"
ROOT_PROGRAMS = {".claude": "Claude Code", ".codex": "Codex", ".grok": "Grok Build", ".agents": "Agents"}

DECLARATION = {
    "id": "bookshelf",
    "title": {"en": "Bookshelf", "zh": "書櫃"},
    "subtitle": {"en": "Each bot's memory, skills and session summaries, and the shared skills. Read only, this computer.",
                 "zh": "每個 bot 的記憶、技能、對話摘要，和共用的技能。只讀，這台電腦。"},
    "furniture": "bookshelf",
    "place": "lobby:wall",
    "button": True,
    "badge_hint": {"en": "Bots whose MEMORY.md is more than 80% full", "zh": "記憶（MEMORY.md）快滿（超過八成）的 bot 數"},
    "search_hint": {"en": "Search titles and text…", "zh": "搜尋標題和內文…"},
    "empty": {"en": "Nothing on the shelf yet.", "zh": "書櫃還是空的。"},
    "readonly_note": {"en": "Read only: edits happen in your editor or by asking the bot.", "zh": "只讀：要改請用編輯器，或請那個 bot 更新。"},
    "filters": [
        {"id": "owner", "label": {"en": "Whose", "zh": "誰的"}},
        {"id": "type", "label": {"en": "Type", "zh": "類型"},
         "options": [["memory", {"en": "Memory", "zh": "記憶"}], ["session", {"en": "Session summary", "zh": "對話摘要"}],
                     ["skill", {"en": "Skill", "zh": "技能"}]]},
    ],
    "sorts": [["mtime", {"en": "Recently changed", "zh": "最近修改"}], ["title", {"en": "Name", "zh": "名稱"}]],
    "actions": [
        {"id": "open_editor", "label": {"en": "Open in editor", "zh": "用編輯器打開"}},
        {"id": "ask_update", "label": {"en": "Ask {owner} to update", "zh": "請 {owner} 更新"}, "needs_tab": True,
         "primary": True},
    ],
}


def _read(path: Path, cap=READ_MAX) -> str:
    try:
        with path.open("rb") as f:
            return f.read(cap).decode("utf-8", "replace")
    except OSError:
        return ""


def _chars(path: Path) -> int:
    """Characters of a memory file, as MEMORY_CAP counts them (whitespace at the ends does not count)."""
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return 0


def _mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def _stamp(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def _without_frontmatter(text: str) -> str:
    m = re.match(r"\A---\s*\n.*?\n---\s*\n?", text, re.S)
    return text[m.end():] if m else text


_LINK = re.compile(r"\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def linked_docs(skill_dir: Path, skill_md_text: str) -> list:
    """[(relative path as written, resolved Path)] of the `.md` files SKILL.md links to by a relative
    link, in order, each inside `skill_dir` (links with `..`, a scheme, an absolute path or a file that
    is not a regular .md file are dropped; SKILL.md itself is not one of them)."""
    try:
        base = skill_dir.resolve()
    except OSError:
        return []
    out, seen = [], set()
    for m in _LINK.finditer(skill_md_text):
        raw = m.group(1).split("#", 1)[0].split("?", 1)[0]
        if not raw or raw.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", raw) or ".." in raw.split("/"):
            continue
        if not raw.lower().endswith(".md"):
            continue
        try:
            real = (base / raw).resolve()
            real.relative_to(base)
        except (OSError, ValueError, RuntimeError):
            continue
        if real.name.lower() == "skill.md" and real.parent == base or not real.is_file() or real in seen:
            continue
        seen.add(real)
        out.append((raw, real))
        if len(out) >= DOCS_PER_SKILL:
            break
    return out


def _skills_in(root: Path) -> list:
    """[(folder name, folder, SKILL.md)] of a skills folder (a folder that is a link counts; hidden ones not)."""
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    out = []
    for d in dirs:
        md = d / "SKILL.md"
        try:
            if md.is_file() and md.resolve().suffix.lower() == ".md":
                out.append((d.name, d, md))
        except OSError:
            continue
    return out


def _bots_of(ctx) -> list:
    """[(role, label, entry)] of the bots that live on this computer, the primary one first."""
    raw = ctx.roster() or {}
    rows = [(r, bots.label_of(r, e), e) for r, e in raw.items() if bots.is_bot(e) and not bots.is_external(e)]
    rows.sort(key=lambda x: (not x[2]["bot"].get("primary"),))
    return rows


def _roots(ctx) -> list:
    given = ctx.skill_roots()
    return [Path(r).expanduser() for r in (given if given is not None else bots.skill_roots())]


def _catalog(ctx) -> dict:
    """id → entry for everything on the shelf, in shelf order. The only place a path is made."""
    cat = {}
    for role, label, entry in _bots_of(ctx):
        mem = bots.memory_file(role)
        if mem.is_file():
            cat[f"mem:{role}"] = {"kind": "memory", "type": "memory", "role": role, "owner": label, "path": mem,
                                  "title": "MEMORY.md", "group": role}
        try:
            sessions = sorted((p for p in bots.sessions_dir(role).glob("*.md") if p.is_file()), key=lambda p: p.name)
        except OSError:
            sessions = []
        for p in reversed(sessions[-SESSIONS_PER_BOT:]):
            cat[f"sess:{role}:{p.name}"] = {"kind": "session", "type": "session", "role": role, "owner": label,
                                            "path": p, "title": p.name, "group": role}
        _add_skills(cat, _skills_in(bots.skills_dir(role)), f"own.{role}", role, label, role)
    shared = {}
    for root in _roots(ctx):
        prog = ROOT_PROGRAMS.get(root.parent.name, root.parent.name)
        for name, d, md in _skills_in(root):
            slot = shared.setdefault(name, {"dir": d, "md": md, "programs": []})
            if prog not in slot["programs"]:
                slot["programs"].append(prog)
    for name, slot in sorted(shared.items(), key=lambda kv: kv[0].casefold()):
        _add_skills(cat, [(name, slot["dir"], slot["md"])], SHARED, "", "", SHARED, programs=slot["programs"])
    return cat


def _add_skills(cat, skills, scope, role, owner, group, programs=None):
    for name, d, md in skills:
        sid = f"skill:{scope}:{name}"
        cat[sid] = {"kind": "skill", "type": "skill", "role": role, "owner": owner, "path": md, "dir": d,
                    "title": name, "group": group, "programs": programs or [], "skill": name}
        for raw, real in linked_docs(d, _read(md)):
            cat[f"doc:{scope}:{name}:{raw}"] = {"kind": "doc", "type": "skill", "role": role, "owner": owner,
                                                "path": real, "title": f"{name} / {raw}", "group": group,
                                                "programs": programs or [], "skill": name, "parent": sid}


# ── the hooks ────────────────────────────────────────────────────────────────
class Bookshelf(oo.OfficeObject):
    declaration = DECLARATION

    def summary(self, ctx) -> dict:
        full = 0
        for role, _label, entry in _bots_of(ctx):
            if bots.is_memory_bot(entry) and _chars(bots.memory_file(role)) > bots.MEMORY_CAP * NEAR_FULL:
                full += 1
        return {"badge": full}

    def list(self, ctx, query="", filters=None, sort="mtime", offset=0, limit=oo.LIST_MAX) -> dict:
        filters = filters or {}
        cat = _catalog(ctx)
        rows = []
        q = str(query or "").strip().casefold()
        for iid, e in cat.items():
            if filters.get("owner") and filters["owner"] != e["group"]:
                continue
            if filters.get("type") and filters["type"] != e["type"]:
                continue
            if q and q not in e["title"].casefold() and q not in e["owner"].casefold() \
                    and q not in _read(e["path"], SEARCH_MAX).casefold():
                continue
            rows.append((iid, e))
        items = [self._row(ctx, iid, e) for iid, e in rows]
        if sort == "title":
            items.sort(key=lambda r: r["title"].casefold())
        else:
            items.sort(key=lambda r: -r["mtime"])      # stable: shelf order breaks ties
        groups = []
        counts = {}
        for r in items:
            counts[r["group"]] = counts.get(r["group"], 0) + 1
        for role, label, _e in _bots_of(ctx):
            if role in counts:
                groups.append({"id": role, "title": label, "count": counts[role]})
        if SHARED in counts:
            groups.append({"id": SHARED, "title": ctx.t("Shared skills", "共用技能"), "count": counts[SHARED]})
        order = {g["id"]: i for i, g in enumerate(groups)}
        items.sort(key=lambda r: order.get(r["group"], 99))
        return {"groups": groups, "items": items[:limit], "total": len(items)}

    def _row(self, ctx, iid, e) -> dict:
        ts = _mtime(e["path"])
        row = {"id": iid, "group": e["group"], "title": self._title(ctx, e), "type": e["type"], "owner": e["owner"],
               "owner_key": f"b:{e['role']}" if e["role"] else "", "mtime": ts, "tags": []}
        if e["kind"] == "memory":
            n = _chars(e["path"])
            row.update(size=n, limit=bots.MEMORY_CAP, subtitle=ctx.t(f"{n:,} / {bots.MEMORY_CAP:,} chars", f"{n:,} / {bots.MEMORY_CAP:,} 字"))
            if n > bots.MEMORY_CAP * NEAR_FULL:
                row["tags"].append({"id": "near-full", "label": ctx.t("Almost full", "快滿了"), "tone": "warn"})
                row["tone"] = "warn"
        elif e["kind"] == "session":
            row["subtitle"] = _stamp(ts)
        else:
            fm = bots._frontmatter(e["dir"] / "SKILL.md") if e["kind"] == "skill" else {}
            row["subtitle"] = (fm.get("description") or "")[:120] if e["kind"] == "skill" else ctx.t("linked from SKILL.md", "SKILL.md 連過來的")
            for prog in e.get("programs") or []:
                row["tags"].append({"id": "prog", "label": prog, "tone": "dim"})
        return row

    @staticmethod
    def _title(ctx, e) -> str:
        if e["kind"] == "memory":
            return ctx.t("Memory · MEMORY.md", "記憶 · MEMORY.md")
        if e["kind"] == "session":
            return ctx.t("Session summary · ", "對話摘要 · ") + e["title"]
        return (ctx.t("Skill · ", "技能 · ") if e["kind"] == "skill" else "") + e["title"]

    def item(self, ctx, item_id) -> dict:
        e = _catalog(ctx).get(str(item_id))
        if e is None:
            return {}
        path = e["path"]
        text = _read(path)
        ts = _mtime(path)
        meta = [[ctx.t("Last changed", "最後修改"), _stamp(ts)]]
        chips = []
        if e["kind"] == "memory":
            n = _chars(path)
            meta.append([ctx.t("Length", "長度"), ctx.t(f"{n:,} / {bots.MEMORY_CAP:,} chars", f"{n:,} / {bots.MEMORY_CAP:,} 字")])
            if n > bots.MEMORY_CAP * NEAR_FULL:
                chips.append({"text": ctx.t("Almost full: tidy it up", "快滿了：請整理精簡"), "tone": "warn"})
        else:
            meta.append([ctx.t("Size", "大小"), f"{len(text.encode('utf-8')) / 1024:.1f} KB"])
        if e["kind"] in ("skill", "doc"):
            meta.append([ctx.t("Read by", "讀得到的"), ", ".join(e.get("programs") or []) or
                         ctx.t("this bot only", "只有這個 bot")])
        links = []
        if e["kind"] == "skill":
            for raw, _real in linked_docs(e["dir"], text):
                links.append({"label": raw, "object": "bookshelf", "item": f"doc:{self._scope(item_id)}:{e['skill']}:{raw}"})
        elif e["kind"] == "doc":
            links.append({"label": "SKILL.md", "object": "bookshelf", "item": e["parent"]})
        actions = ["open_editor"] + (["ask_update"] if e["role"] and e["kind"] in ("memory", "skill") else [])
        who = e["owner"] or ctx.t("Shared skills", "共用技能")
        return {"title": f"{who} / {self._title(ctx, e)}", "subtitle": e["title"] if e["kind"] == "doc" else "",
                "markdown": _without_frontmatter(text), "meta": meta, "chips": chips, "links": links,
                "actions": actions, "owner": e["owner"], "owner_key": f"b:{e['role']}" if e["role"] else "",
                "note": ctx.t("Only SKILL.md and the .md files it links to are listed.", "只列 SKILL.md 和它連到的 .md 檔。")
                if e["kind"] in ("skill", "doc") else ""}

    @staticmethod
    def _scope(item_id) -> str:
        return str(item_id).split(":", 2)[1]

    def action(self, ctx, item_id, action_id, args) -> dict:
        e = _catalog(ctx).get(str(item_id))
        if e is None:
            return {"ok": False, "message": ctx.t("That entry is gone.", "這一筆已經不在了。")}
        if action_id == "open_editor":
            return {"ok": True, "open": str(e["path"]), "message": ctx.t("Opened in your editor.", "已用編輯器打開。")}
        if action_id == "ask_update" and e["role"] and e["kind"] in ("memory", "skill"):
            if e["kind"] == "memory":
                n = _chars(e["path"])
                text = sf_lang.pick(
                    f"(Bookshelf) Please update your MEMORY.md: add what matters from recent work, drop what is out of date, "
                    f"and keep it under {bots.MEMORY_CAP:,} characters (it is {n:,} now).",
                    f"（書櫃）請更新你的 MEMORY.md：把最近的重點補進去、過時的刪掉，保持在 {bots.MEMORY_CAP:,} 字以內（現在 {n:,} 字）。")
            else:
                text = sf_lang.pick(
                    f"(Bookshelf) Please review your skill \"{e['skill']}\" (its SKILL.md) and update it where it is out of date.",
                    f"（書櫃）請檢查你的技能「{e['skill']}」（SKILL.md），過時的地方請更新。")
            return {"ok": True, "tell": {"to": f"b:{e['role']}", "text": text}}
        return {"ok": False}
