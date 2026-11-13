"""bot_skills — a bot's own skills: add, copy from the shared ones, open, delete.

A bot's own skills live in its folder, `bots/<bot>/skills/<skill>/SKILL.md` (bots.skills_dir),
next to its memory. Only that bot gets them: its wake-up brief lists them (bots.brief_skills),
and where a shared skill has the same name the bot's own is the one it is told about. The folder
moves with the bot when it is renamed and does not care which CLI the bot runs on, so nothing
here has to follow either.

This module is what the bot editor's Skills section does (api_bot_memory.py calls it); reading
them for the brief is bots.own_skill_catalog. Everything takes the bot's role and a skill's folder
name, never a path: the folder name is checked (no `/`, no `..`, nothing hidden) and the path built
from it is checked to be inside the bot's skills folder before anything is written or removed.

Leaf module: no `import main`, no UI. Each call returns {"success", "message", ...}; a refusal also
has a "code" the page can tell apart.
"""

import os
import re
import shutil
from pathlib import Path

import bots
import sf_lang

COPY_MAX_FILES = 400                # a shared skill is a folder: refuse one that is really a project
COPY_MAX_BYTES = 8 * 1024 * 1024
SKILL_FILE = "SKILL.md"

_TEMPLATE = {
    "en": """---
name: {name}
description: {desc}
---

# {name}

When to use this, and what to do (steps, commands, files to read). Only this bot gets it.
""",
    "zh": """---
name: {name}
description: {desc}
---

# {name}

什麼時候用、怎麼做（步驟、指令、要讀的檔案）。只有這個 bot 拿得到。
""",
}


def _result(ok, en, zh, code="", **extra):
    out = {"success": ok, "message": sf_lang.pick(en, zh)}
    if code:
        out["code"] = code
    out.update(extra)
    return out


def folder_name(name) -> str:
    """The folder a new skill of this name gets, '' when the name cannot be one: nothing but what a
    folder can hold on every system (bots.slug: spaces become -, CJK stays), no `.` at either end
    (so never `..`), not hidden."""
    raw = bots.clean_name(name)
    if not raw or "/" in raw or "\\" in raw or raw.startswith(".") or re.search(r"[\x00-\x1f]", str(name)):
        return ""
    s = bots.slug(raw)
    if s == "bot" and raw != "bot":          # slug's answer for a name with nothing left in it
        return ""
    return s


def _plain(name) -> bool:
    """A folder name that is one step down and nothing else: what a skill the user made by hand in
    Finder may be called (spaces and all), never a path."""
    n = str(name or "")
    return bool(n) and n == n.strip() and not n.startswith(".") and len(n) <= 120 \
        and not re.search(r'[\x00-\x1f/\\]', n)


def _safe_dir(role, folder):
    """(skills_dir, the skill's folder path), or (None, None) when `folder` is not a plain name
    or its path would leave the bot's skills folder."""
    if not _plain(folder):
        return None, None
    root = bots.skills_dir(role)
    path = root / str(folder)
    try:
        if os.path.realpath(path.parent) != os.path.realpath(root):
            return None, None
    except (OSError, ValueError):
        return None, None
    return root, path


def list_own(role) -> dict:
    """{"success", "skills": [{"name", "folder", "description", "path", "shadows"}], "dir"}.
    `shadows`: a shared skill has this name too, and this one is what the bot is told about."""
    shared = {k["name"] for k in bots.skill_catalog()}
    rows = [dict(k, shadows=k["name"] in shared) for k in bots.own_skill_catalog(role)]
    return {"success": True, "skills": rows, "dir": str(bots.skills_dir(role))}


def create_blank(role, name, description="") -> dict:
    """A new skill with a SKILL.md to fill in. Refused when the name cannot be a folder or the
    folder is already there."""
    folder = folder_name(name)
    if not folder:
        return _result(False, "Give the skill a name (letters, digits, - and _; no / or leading .)",
                       "請給 skill 一個名字（文字、數字、- 和 _；不能有 / 或以 . 開頭）", "bad_name")
    _, path = _safe_dir(role, folder)
    if path is None:
        return _result(False, "That name cannot be a folder", "這個名字不能當資料夾名", "bad_name")
    if path.exists() or path.is_symlink():
        return _result(False, f'"{folder}" is already one of its skills', f"「{folder}」已經是它的 skill", "exists")
    desc = " ".join(str(description or "").split())[:200] or sf_lang.pick(
        "What this skill is for, in one line", "這個 skill 做什麼，一句話")
    try:
        path.mkdir(parents=True)
        (path / SKILL_FILE).write_text(_TEMPLATE[sf_lang.prompt_lang()].format(name=folder, desc=desc),
                                       encoding="utf-8")
    except OSError as e:
        return _result(False, f"Could not create it: {e}", f"建不起來：{e}", "failed")
    return _result(True, f'Added "{folder}"; fill in its SKILL.md', f"已新增「{folder}」；請把它的 SKILL.md 填好",
                   folder=folder, path=str(path / SKILL_FILE))


def _shared_dir(shared_name) -> Path:
    """The folder of the shared skill called `shared_name` (the catalog's name), or None."""
    hit = next((k for k in bots.skill_catalog() if k["name"] == shared_name), None)
    return Path(hit["path"]).parent if hit else None


def _tree(src: Path):
    """(files, bytes) of the regular files under `src`, links not followed."""
    n = size = 0
    for dirpath, _dirs, files in os.walk(src, followlinks=False):
        for f in files:
            p = Path(dirpath) / f
            if p.is_symlink():
                continue
            n += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return n, size


def copy_shared(role, shared_name) -> dict:
    """The shared skill `shared_name` copied whole (its folder: SKILL.md and whatever it brings)
    into the bot's own, under its folder's name. The copy is the bot's to change; the shared one is
    not touched. Links inside it are not copied. Refused when there is none by that name, the
    bot already has a folder of that name, or the skill is bigger than a skill should be."""
    src = _shared_dir(str(shared_name or ""))
    if src is None or not src.is_dir():
        return _result(False, f'There is no shared skill "{shared_name}"', f"找不到共用的 skill「{shared_name}」", "no_skill")
    folder = folder_name(src.name)
    _, dst = _safe_dir(role, folder)
    if dst is None:
        return _result(False, "That skill's folder name cannot be copied", "這個 skill 的資料夾名稱不能複製", "bad_name")
    if dst.exists() or dst.is_symlink():
        return _result(False, f'"{folder}" is already one of its skills', f"「{folder}」已經是它的 skill", "exists")
    files, size = _tree(src)
    if files > COPY_MAX_FILES or size > COPY_MAX_BYTES:
        return _result(False, f'"{shared_name}" is too big to copy ({files} files, {size // 1024} KB)',
                       f"「{shared_name}」太大了，不複製（{files} 個檔案、{size // 1024} KB）", "too_big")
    try:
        shutil.copytree(src, dst, symlinks=True,
                        ignore=lambda d, names: [n for n in names if (Path(d) / n).is_symlink()])
    except (OSError, shutil.Error) as e:
        shutil.rmtree(dst, ignore_errors=True)          # no half a skill left behind
        return _result(False, f"Could not copy it: {e}", f"複製不成：{e}", "failed")
    return _result(True, f'Copied "{shared_name}" into its own skills', f"已把「{shared_name}」複製成它自己的 skill",
                   folder=folder, path=str(dst / SKILL_FILE))


def delete(role, folder) -> dict:
    """Remove one of the bot's own skills. A skill that is a link to a folder elsewhere loses only
    the link. Whether to ask first is the page's job (SFDialog.confirm)."""
    _, path = _safe_dir(role, folder)
    if path is None:
        return _result(False, "That is not one of its skills", "這不是它的 skill", "bad_name")
    if not (path.is_symlink() or path.is_dir()):
        return _result(False, f'"{folder}" is not there', f"「{folder}」不存在", "missing")
    try:
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)
    except OSError as e:
        return _result(False, f"Could not delete it: {e}", f"刪不掉：{e}", "failed")
    return _result(True, f'Deleted "{folder}"', f"已刪除「{folder}」", folder=folder)


def folder_to_open(role, folder="") -> Path:
    """The folder the editor's "open folder" shows: the one skill's, else the bot's skills folder
    (made if it is not there yet, so there is something to open). None when `folder` is not one of
    the bot's skills."""
    if folder:
        _, path = _safe_dir(role, folder)
        return path if path is not None and path.is_dir() else None
    d = bots.skills_dir(role)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d
