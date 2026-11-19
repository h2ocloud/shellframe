"""Deliverables: what a bot or a tab hands the person (a file, a link), kept as references on its owner's desk.

A bot (or a tab) registers one with `sfctl deliver add <path-or-url>`; when it forgets, the end of its turn finds
the files its last reply mentions and offers them as *candidates* the person takes or ignores. The person decides:
keep it on the desk (先放著), take it away (拿走: off the desk, kept in history 180 days), or send the owner a line.
Only references are stored (state.db, table `deliverables`): never a file's contents. One row per (owner, ref).

States: candidate (guessed, not on the desk, not counted), new (on the desk; `seen_at` empty = never opened, the red
number), kept, taken, dismissed (a candidate the person ignored: not asked again unless the file changed).

This module is the store and the rules; the popup's hooks are office_deliverables.py, the js_api and sfctl side is
api_deliverables.py. The security rules are in here and are hard rules (docs: the deliverables design, section 5):

  - auto-pickup never lists a key or config file (`secret_reason`: the name list, the protected folders, a text file
    whose content matches the masking rules), never a code or config extension, never a file outside `AUTO_EXTS`
  - everything shown is masked by `mask()` (multi-line, not truncated: the host's `redact_text` plus KEY=value names,
    PEM private key blocks and ?token= / &key= URL parameters); a note and the "mentioned in this sentence" line are
    masked before they are stored
  - a registered file that looks like it holds secrets is registered but flagged (`flag = 'secret'`): no preview, no
    thumbnail, opening it asks first
  - nothing here reads a file's content except `secret_reason` (a text file under 256 KB) and the popup's one-file preview

`home`, `now`, `protected` and `path` (the database) can be given, so a test never touches the real ones. Leaf: stdlib,
office_objects (masking), state_store, sf_config.
"""
import fnmatch
import os
import re
import sqlite3
import time
from pathlib import Path

import office_objects as oo
import sf_config
import state_store as ss

DAY = 86400
STATES = ("candidate", "new", "kept", "taken", "dismissed")
DESK = ("new", "kept")                       # what is on the desk
KEEP_TAKEN_S = 180 * DAY
CAND_MAX = 20                                # candidates kept per person
REPLY_MAX = 5                                # candidates taken from one reply
AUTO_WINDOW_S = 2 * 3600                     # a file modified within this is "from this turn"
AUTO_SIZE_MAX = 200 * 1024 * 1024
TEXT_SCAN_MAX = 256 * 1024
TITLE_MAX = 200
NOTE_MAX = 300
SOURCE_MAX = 200
SKIPPED_KEEP = 10
LIST_MAX = 400

# type → extensions (the legend of the desk's small objects); `url` and `folder` have no extension
TYPE_EXTS = {
    "pdf": ("pdf",), "html": ("html", "htm"), "zip": ("zip", "tar", "gz", "tgz", "7z"), "md": ("md", "txt", "markdown"),
    "img": ("png", "jpg", "jpeg", "gif", "webp", "svg"), "pptx": ("pptx", "key"), "xlsx": ("xlsx", "xls", "csv"),
    "docx": ("docx", "doc"), "video": ("mp4", "mov", "webm"),
}
TYPES = tuple(TYPE_EXTS) + ("folder", "url", "file")
_EXT_TYPE = {e: t for t, es in TYPE_EXTS.items() for e in es}
# what auto-pickup may offer: nothing that is code or configuration (py js ts sh css json xml yaml yml are not here)
AUTO_EXTS = frozenset(("pdf", "html", "htm", "md", "txt", "zip", "png", "jpg", "jpeg", "gif", "webp", "svg", "docx",
                       "xlsx", "pptx", "csv", "mp4", "mov", "webm"))
CODE_EXTS = frozenset(("py", "js", "ts", "tsx", "jsx", "sh", "bash", "zsh", "css", "json", "xml", "yaml", "yml", "toml",
                       "ini", "cfg", "conf", "plist", "env", "rb", "go", "rs", "java", "c", "h", "cpp", "sql"))
TEXT_LIKE = frozenset(("md", "txt", "markdown", "csv", "html", "htm", "svg"))      # read for a preview (not html/svg)
NO_SCAN_TYPES = ("pdf", "img", "video", "zip", "pptx", "xlsx", "docx", "folder")   # binary: never text-scanned
SKIP_DIRS = frozenset(("node_modules", ".git", ".venv", "site-packages", ".cache", "__pycache__"))
_SKIP_SEQ = (("library", "caches"),)

# names that are keys, credentials or config (matched case-insensitively on the file's own name)
NAME_GLOBS = (".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*",
              "*.ppk", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd", "*.keychain",
              "*.keychain-db", "*.kdbx")
NAME_WORDS = ("token", "secret", "credential", "password", "passwd")
PROTECTED_HOME = (".ssh", ".aws", ".gnupg", ".kube", "Library/Keychains", ".config", ".claude")
_URL = re.compile(r"https?://[^\s]+", re.I)

# ── masking ──────────────────────────────────────────────────────────────────
# office_objects.redact_text covers sk-…, Bearer, ghp_…, JWT, 32+ character runs, user:pass@ in a URL, `password: x`
# and RSA/EC private key blocks. Three things it does not: KEY=value where the NAME holds KEY / TOKEN / SECRET /
# PASSWORD (API_TOKEN=x: its \b never sees a word inside a name; `=` only), a private key block of another shape (PGP), and the
# secret in a URL's query. Same line-by-line rule, line breaks kept.
_MASK = oo._MASK
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY[A-Z0-9 ]*-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY[A-Z0-9 ]*-----|\Z)", re.S)
_NAMED = re.compile(r"(?i)(?<![\w.\-])([\w.\-]*(?:key|token|secret|password|passwd)[\w.\-]*[\"']?[ \t]*=[ \t]*[\"']?)"
                    r"(?!<redacted>)([^\s\"'`,;&<>]+)")
# `name: value` where the name says what it is (db_password: x, secret_key: x): the host's rule needs a word boundary before the
# word, so it misses a name that starts with something else. Not a bare `key` / `token` / `pass`: "tokens: 300", "passed: 12" are prose.
_COLON = re.compile(r"(?i)(?<![\w.\-])([\w.\-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key)[\w.\-]*[\"']?[ \t]*:[ \t]*[\"']?)"
                    r"(?!<redacted>)([^\s\"'`,;&<>]+)")
_QUERY = re.compile(r"(?i)([?&][\w.\-]*(?:key|token|secret|pass(?:word|wd)?|pwd|auth|sig(?:nature)?|credential)[\w.\-]*=)"
                    r"(?!<redacted>)[^&\s#\"'<>)\]]+")


def mask(text, strict=True) -> str:
    """`text` with anything that looks like a secret masked, line breaks kept and nothing cut. Used for every body,
    preview, note and source; `strict` as office_objects.redact_text (32-character runs: off for a title)."""
    out = _PEM.sub(_MASK, str(text if text is not None else ""))
    out = _QUERY.sub(lambda m: m.group(1) + _MASK, out)
    out = _NAMED.sub(lambda m: m.group(1) + _MASK, out)
    out = _COLON.sub(lambda m: m.group(1) + _MASK, out)
    return oo.redact_text(out, strict=strict)


def mask_line(text, n, strict=True) -> str:
    """One masked line of at most `n` characters (a note, a source sentence, a title)."""
    return oo.clip(" ".join(mask(text, strict).split()), n)


# ── what a reference is ───────────────────────────────────────────────────────
def ext_of(ref) -> str:
    return os.path.splitext(str(ref))[1].lower().lstrip(".")


def type_of(ref, is_dir=False, is_url=False) -> str:
    if is_url:
        return "url"
    if is_dir:
        return "folder"
    return _EXT_TYPE.get(ext_of(ref), "file")


def is_url(ref) -> bool:
    return bool(re.match(r"https?://", str(ref or ""), re.I))


def _cmp(p) -> str:
    return os.path.normpath(str(p)).lower()


def protected_dirs(home=None, extra=()) -> list:
    h = Path(home) if home else Path.home()
    out = [h / d for d in PROTECTED_HOME]
    for getter in (sf_config.config_dir, sf_config.state_dir):
        try:
            out.append(Path(getter()))
        except Exception:
            pass
    return out + [Path(e) for e in extra]


def _inside(path, dirs) -> bool:
    cands = {_cmp(os.path.abspath(path))}
    try:
        cands.add(_cmp(os.path.realpath(path)))        # a link into ~/.ssh is in ~/.ssh
    except OSError:
        pass
    for d in dirs:
        roots = {_cmp(os.path.abspath(d))}
        try:
            roots.add(_cmp(os.path.realpath(d)))
        except OSError:
            pass
        for r in roots:
            if any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for p in cands):
                return True
    return False


def name_is_secret(path) -> bool:
    name = os.path.basename(str(path).rstrip("/\\")).lower()
    if any(fnmatch.fnmatchcase(name, g) for g in NAME_GLOBS):
        return True
    return any(w in name for w in NAME_WORDS)


def content_has_secret(path, strong=False) -> bool:
    """True when a text file under 256 KB holds anything the masking would mask (`strong`: only a private key block or
    a known kind of token: sk-…, ghp_…, AKIA…, a JWT, a Bearer value). Binary files are never read."""
    try:
        p = Path(path)
        if ext_of(p) in ("pdf", "png", "jpg", "jpeg", "gif", "webp", "mp4", "mov", "webm", "zip", "gz", "7z", "docx", "xlsx", "pptx"):
            return False
        if not p.is_file() or p.stat().st_size > TEXT_SCAN_MAX:
            return False
        data = p.read_bytes()
    except OSError:
        return False
    if b"\0" in data:
        return False
    text = data.decode("utf-8", "replace")
    if strong:
        return bool(_PEM.search(text) or oo._KEY_BLOCK.search(text) or oo._KNOWN.search(text))
    return mask(text) != text


def secret_reason(path, home=None, protected=(), content="strong") -> str:
    """Why a file must not be listed, previewed or opened without asking: 'name', 'dir', 'content', or ''. `content`:
    "all" (auto-pickup: anything the masking would mask: a guess is cautious), "strong" (a file a bot registered: a
    private key or a known kind of token; the rest of what looks like a secret is masked in the preview), or False."""
    if name_is_secret(path):
        return "name"
    if _inside(path, protected_dirs(home, protected)):
        return "dir"
    if content and content_has_secret(path, strong=content == "strong"):
        return "content"
    return ""


def url_is_secret(url) -> bool:
    return mask(url) != str(url)


# ── the store ─────────────────────────────────────────────────────────────────
_COLS = ("id", "owner_kind", "owner_key", "owner_label", "kind", "ref", "type", "title", "note", "task_id", "state",
         "origin", "source", "flag", "size", "mtime", "created_at", "updated_at", "seen_at", "taken_at")


def _row(r):
    return {k: r[k] for k in r.keys()} if r is not None else None


def _check_state(state):
    if state not in STATES:
        raise ValueError(f"state: {state}")


def _stat(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"size": 0 if os.path.isdir(path) else int(st.st_size), "mtime": float(st.st_mtime), "dir": os.path.isdir(path)}


def add(owner_kind, owner_key, owner_label, ref, *, title="", note="", task_id=None, origin="sfctl", source="",
        state="new", now=None, home=None, protected=(), path=None) -> dict:
    """Register `ref` (an existing local path, made absolute, or an http(s) URL) on an owner's desk: a new row, or
    the owner's row for it again (re-registering a taken or ignored one puts it back as new; a kept one stays kept
    unless the file changed; a new one stays new). The note and the source are masked first; the title too.
    Raises ValueError for what cannot be registered, ss.NotFound for a task that does not exist."""
    now = time.time() if now is None else now
    ref = str(ref or "").strip()
    if not ref:
        raise ValueError("path or URL: empty")
    if owner_kind not in ("bot", "tab"):
        raise ValueError(f"owner: {owner_kind}")
    if state not in ("new", "candidate"):
        raise ValueError(f"state: {state}")
    if is_url(ref):
        kind, typ, info = "url", "url", {"size": 0, "mtime": 0.0}
        flag = "secret" if url_is_secret(ref) else ""
        shown = ref
    else:
        ref = os.path.abspath(os.path.expanduser(ref))
        info = _stat(ref)
        if info is None:
            raise ValueError(f"not found: {ref}")
        kind, typ = "file", type_of(ref, info["dir"])
        flag = "secret" if secret_reason(ref, home, protected) else ""
        shown = ref
    if task_id not in (None, ""):
        ss.get(int(task_id), events=False, path=path)          # raises ss.NotFound
        task_id = int(task_id)
    else:
        task_id = None
    given = mask_line(title, TITLE_MAX, strict=False)
    title = given or mask_line(os.path.basename(shown.rstrip("/")) or shown, TITLE_MAX, strict=False)
    vals = dict(owner_label=oo.clip(" ".join(str(owner_label or "").split()), 80), kind=kind, ref=ref, type=typ, title=title,
                note=mask_line(note, NOTE_MAX), task_id=task_id, origin=origin if origin in ("sfctl", "auto") else "sfctl",
                source=mask_line(source, SOURCE_MAX), flag=flag, size=info["size"], mtime=info["mtime"])
    con = ss._open(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            old = _row(con.execute("SELECT * FROM deliverables WHERE owner_kind=? AND owner_key=? AND ref=?",
                                   (owner_kind, str(owner_key), ref)).fetchone())
            if old is None:
                cur = con.execute(
                    "INSERT INTO deliverables(owner_kind, owner_key, owner_label, kind, ref, type, title, note, task_id, state,"
                    " origin, source, flag, size, mtime, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (owner_kind, str(owner_key), vals["owner_label"], kind, ref, typ, title, vals["note"], task_id, state,
                     vals["origin"], vals["source"], flag, vals["size"], vals["mtime"], now, now))
                did = cur.lastrowid
            else:
                did = old["id"]
                if not given and old["origin"] == "sfctl" and old["title"]:
                    title = old["title"]                       # registered again with no title: keep the one it had
                changed = old["mtime"] != vals["mtime"] or old["size"] != vals["size"]
                if state == "candidate":                       # a guess never touches what the person already decided
                    if old["state"] == "dismissed" and changed:
                        con.execute("UPDATE deliverables SET state='candidate', source=?, size=?, mtime=?, updated_at=?, flag=?"
                                    " WHERE id=?", (vals["source"], vals["size"], vals["mtime"], now, flag, did))
                    elif old["state"] == "candidate":
                        con.execute("UPDATE deliverables SET source=?, size=?, mtime=?, updated_at=?, flag=? WHERE id=?",
                                    (vals["source"], vals["size"], vals["mtime"], now, flag, did))
                    con.execute("COMMIT")
                    return get(did, path=path)
                new_state = "kept" if old["state"] == "kept" and not changed else "new"
                reopen = new_state == "new" and (old["state"] != "new" or changed)
                con.execute(
                    "UPDATE deliverables SET owner_label=?, kind=?, type=?, title=?, note=?, task_id=?, state=?, origin=?, source=?,"
                    " flag=?, size=?, mtime=?, updated_at=?, seen_at=?, taken_at=? WHERE id=?",
                    (vals["owner_label"], kind, typ, title, vals["note"], task_id, new_state, vals["origin"], vals["source"], flag,
                     vals["size"], vals["mtime"], now, None if reopen else old["seen_at"], None if reopen else old["taken_at"], did))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return get(did, path=path)


def get(did, path=None):
    con = ss._open(path)
    try:
        r = _row(con.execute("SELECT * FROM deliverables WHERE id=?", (int(did),)).fetchone())
    finally:
        con.close()
    if r is None:
        raise ss.NotFound(did)
    return r


def listing(*, states=None, owner=None, task_id=None, query="", limit=LIST_MAX, path=None) -> list:
    """Rows newest first. `owner` is (kind, key); `query` is matched against title, note and owner name."""
    where, args = [], []
    if states:
        where.append(f"state IN ({','.join('?' * len(states))})")
        args += list(states)
    if owner:
        where.append("owner_kind=? AND owner_key=?")
        args += [owner[0], str(owner[1])]
    if task_id is not None:
        where.append("task_id=?")
        args.append(int(task_id))
    q = str(query or "").strip().lower()
    if q:
        where.append("(lower(title) LIKE ? OR lower(note) LIKE ? OR lower(owner_label) LIKE ?)")
        args += [f"%{q}%"] * 3
    sql = "SELECT * FROM deliverables" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    con = ss._open(path)
    try:
        return [_row(r) for r in con.execute(sql, args + [int(limit)])]
    finally:
        con.close()


def mark_seen(did, now=None, path=None) -> None:
    """The person opened it: it stops counting as new (but stays undecided on the desk)."""
    now = time.time() if now is None else now
    con = ss._open(path)
    try:
        con.execute("UPDATE deliverables SET seen_at=? WHERE id=? AND state='new' AND seen_at IS NULL", (now, int(did)))
    finally:
        con.close()


_MOVES = {                               # action → (states it may start from, the state it ends in)
    "keep": (("new", "kept"), "kept"),
    "take": (("new", "kept"), "taken"),
    "accept": (("candidate",), "new"),
    "dismiss": (("candidate",), "dismissed"),
}


def move(did, action, now=None, path=None) -> dict:
    """keep / take / accept / dismiss: only from the states that action makes sense in (else ValueError)."""
    if action not in _MOVES:
        raise ValueError(f"action: {action}")
    starts, end = _MOVES[action]
    now = time.time() if now is None else now
    con = ss._open(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            r = _row(con.execute("SELECT * FROM deliverables WHERE id=?", (int(did),)).fetchone())
            if r is None:
                raise ss.NotFound(did)
            if r["state"] not in starts:
                raise ValueError(f"{action}: it is {r['state']}")
            con.execute("UPDATE deliverables SET state=?, updated_at=?, taken_at=?, seen_at=? WHERE id=?",
                        (end, now, now if end == "taken" else None,
                         now if end in ("kept", "taken") and r["seen_at"] is None else r["seen_at"], int(did)))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return get(did, path=path)


def dismiss_candidates(owner, now=None, path=None) -> int:
    """Ignore every candidate of one person; how many."""
    now = time.time() if now is None else now
    con = ss._open(path)
    try:
        cur = con.execute("UPDATE deliverables SET state='dismissed', updated_at=? WHERE state='candidate' AND owner_kind=?"
                          " AND owner_key=?", (now, owner[0], str(owner[1])))
        return cur.rowcount
    finally:
        con.close()


def set_label(owner_kind, owner_key, label, path=None) -> None:
    """A bot or tab was renamed: its rows say the new name."""
    con = ss._open(path)
    try:
        con.execute("UPDATE deliverables SET owner_label=? WHERE owner_kind=? AND owner_key=? AND owner_label<>?",
                    (oo.clip(" ".join(str(label or "").split()), 80), owner_kind, str(owner_key), str(label or "")))
    finally:
        con.close()


# ── counts: what the desks, the sidebar and the red number read ──────────────
def owner_ref(row, live_tabs) -> str:
    """The key the office and the sidebar use for a row's owner: `b:<role>`, `t:<sid>` for a tab that is open right now
    (`live_tabs`: sid → opened), `x:<owner_key>` for a tab that is closed."""
    if row["owner_kind"] == "bot":
        return "b:" + row["owner_key"]
    sid, _sep, opened = str(row["owner_key"]).partition("@")
    return f"t:{sid}" if str((live_tabs or {}).get(sid, "")) == opened and opened else "x:" + row["owner_key"]


def overview(live_tabs=None, *, rail=30, cand_rail=10, path=None) -> dict:
    """One small read for the page: the red number (new and never opened, everywhere, closed tabs too), per owner
    {count on the desk, new, the newest three types, label}, per task, and the rail's list (undecided ones, the
    candidates). Never reads a file."""
    con = ss._open(path)
    try:
        rows = [_row(r) for r in con.execute(
            "SELECT * FROM deliverables WHERE state IN ('candidate','new','kept') ORDER BY created_at DESC, id DESC")]
    finally:
        con.close()
    by_owner, by_task, items, cands, total_new = {}, {}, [], [], 0
    for r in rows:
        key = owner_ref(r, live_tabs)
        if r["state"] == "candidate":
            cands.append(dict({k: v for k, v in _public(r).items() if k != "source"}, owner=key))      # the sentence (it holds the path) is for the popup
            continue
        fresh = r["state"] == "new" and r["seen_at"] is None
        total_new += 1 if fresh else 0
        slot = by_owner.setdefault(key, {"key": key, "label": r["owner_label"], "count": 0, "new": 0, "types": [],
                                         "closed": key.startswith("x:")})
        slot["count"] += 1
        slot["new"] += 1 if fresh else 0
        if len(slot["types"]) < 3:
            slot["types"].append(r["type"])
        if r["task_id"]:
            by_task[str(r["task_id"])] = by_task.get(str(r["task_id"]), 0) + 1
        if r["state"] == "new":
            items.append(dict(_public(r), owner=key, fresh=fresh))
    return {"new": total_new, "by_owner": by_owner, "by_task": by_task, "rail": items[:rail], "rail_more": max(0, len(items) - rail),
            "candidates": cands[:cand_rail], "candidates_total": len(cands)}


def _public(r) -> dict:
    """A row for the page: its words are already masked; the path of a file is not sent (the page names a row by id)."""
    return {"id": r["id"], "title": r["title"], "note": r["note"], "type": r["type"], "kind": r["kind"], "size": r["size"],
            "mtime": r["mtime"], "created_at": r["created_at"], "state": r["state"], "flag": r["flag"], "task_id": r["task_id"],
            "label": r["owner_label"], "source": r["source"], "origin": r["origin"]}


# ── skipped names (auto-pickup passed over a key-looking file): names only, kept per person ─────────────────────
def _skip_key(owner):
    return f"deliv_skipped:{owner[0]}:{owner[1]}"


def skipped_names(owner, path=None) -> list:
    import json
    con = ss._open(path)
    try:
        r = con.execute("SELECT value FROM meta WHERE key=?", (_skip_key(owner),)).fetchone()
    finally:
        con.close()
    try:
        return [str(x) for x in json.loads(r[0])] if r else []
    except (ValueError, TypeError):
        return []


def all_skipped(path=None) -> list:
    """The names passed over, for every person (newest first, without repeats)."""
    import json
    con = ss._open(path)
    try:
        vals = [r[0] for r in con.execute("SELECT value FROM meta WHERE key LIKE 'deliv_skipped:%'")]
    finally:
        con.close()
    out = []
    for v in vals:
        try:
            out += [str(x) for x in json.loads(v)]
        except (ValueError, TypeError):
            continue
    return list(dict.fromkeys(out))


def _note_skipped(owner, names, path=None) -> None:
    import json
    if not names:
        return
    have = skipped_names(owner, path)
    merged = list(dict.fromkeys([n for n in names if n] + have))[:SKIPPED_KEEP]
    con = ss._open(path)
    try:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (_skip_key(owner), json.dumps(merged, ensure_ascii=False)))
    finally:
        con.close()


def forget_skipped(owner, path=None) -> None:
    con = ss._open(path)
    try:
        con.execute("DELETE FROM meta WHERE key=?", (_skip_key(owner),))
    finally:
        con.close()


# ── auto-pickup: the files a reply mentions ──────────────────────────────────
_PUNCT = re.compile(r"[（）()「」『』【】［］\[\]<>\"'“”‘’*，。；：、！？,;:|]")


def _path_regex():
    """The Telegram bridge's own path detector (so there is one definition of "a path in a reply")."""
    from bridge_telegram import TelegramBridge
    return TelegramBridge._FILE_PATH_RE


# Windows: C:\\x\\y.md, C:/x/y.md and ~\\x\\y.md (the bridge's detector knows only /… and ~/…), same characters per part
_WIN_PATH = re.compile(r"(?:^|\s|`)((?:[A-Za-z]:|~)[\\/](?:[\w.\-]+[\\/])*[\w.\-]+(?:\.\w{1,10})?)(?=\s|`|$|[)\]},;:])")
_DRIVE = re.compile(r"(?<![A-Za-z])([A-Za-z]):(?=[\\/])")     # the colon of C:\ survives _PUNCT


def _normalise(text) -> str:
    t = _DRIVE.sub("\\1\x01", str(text or ""))
    t = _PUNCT.sub(" ", t).replace("\x01", ":")
    t = re.sub(r"\.(?=\s|$)", " ", t)                          # "…report.md." ends a sentence, not the name
    return re.sub(r"(?<=[^\x00-\x7f])(?=~[/\\\\]|[A-Za-z]:[/\\\\])", " ", t)   # 「檔案在~/x.md」「檔案在C:\\x.md」


def _sentence(text, raw) -> str:
    i = text.find(raw)
    if i < 0:
        return ""
    s = text.rfind("\n", 0, i) + 1
    e = text.find("\n", i)
    line = text[s:e if e >= 0 else len(text)]
    j = line.find(raw)
    a = max([line.rfind(c, 0, j) for c in "。.!?！？"] + [-1]) + 1
    ends = [x for x in (line.find(c, j + len(raw)) for c in "。.!?！？") if x >= 0]
    return line[a:min(ends) + 1 if ends else len(line)].strip()


def _in_skip_dir(path) -> bool:
    parts = [p.lower() for p in Path(path).parts]
    if any(p in SKIP_DIRS for p in parts):
        return True
    return any(parts[i:i + len(seq)] == list(seq) for seq in _SKIP_SEQ for i in range(len(parts)))


def find_candidates(text, *, now=None, home=None, protected=()) -> dict:
    """The files a reply names that are worth offering: {"candidates": [{ref, title, type, size, mtime, source}],
    "skipped": [file names passed over because they look like keys or settings]}. A path must be absolute or ~/…,
    exist as a regular file of at most 200 MB, have been modified within the last 2 hours, be outside cache and
    dependency folders, outside the protected folders, not be named like a key or a credential, have an extension
    from AUTO_EXTS (never code or configuration), and (a text file under 256 KB) not match the masking rules.
    At most REPLY_MAX candidates, in the order they appear."""
    now = time.time() if now is None else now
    try:
        rx = _path_regex()
    except Exception:
        return {"candidates": [], "skipped": []}
    cands, skipped, seen = [], [], set()
    norm = _normalise(text)
    for m in sorted(list(rx.finditer(norm)) + list(_WIN_PATH.finditer(norm)), key=lambda x: x.start(1)):
        raw = m.group(1)
        p = os.path.abspath(os.path.expanduser(raw))
        if p in seen or not os.path.isabs(os.path.expanduser(raw)):
            continue
        seen.add(p)
        try:
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
        except OSError:
            continue
        if st.st_size > AUTO_SIZE_MAX or _in_skip_dir(p) or not (now - AUTO_WINDOW_S <= st.st_mtime <= now + 3600):
            continue
        why = "name" if name_is_secret(p) else "dir" if _inside(p, protected_dirs(home, protected)) else ""
        if why:
            skipped.append(os.path.basename(p))
            continue
        ext = ext_of(p)
        if ext not in AUTO_EXTS or ext in CODE_EXTS:
            continue
        if ext in TEXT_LIKE and content_has_secret(p):         # a guess scans for everything the masking masks
            skipped.append(os.path.basename(p))
            continue
        if len(cands) < REPLY_MAX:
            cands.append({"ref": p, "title": os.path.basename(p), "type": type_of(p), "size": int(st.st_size),
                          "mtime": float(st.st_mtime), "source": mask_line(_sentence(str(text), raw), SOURCE_MAX)})
    return {"candidates": cands, "skipped": skipped}


def record_candidates(owner_kind, owner_key, owner_label, found, *, now=None, home=None, protected=(), path=None) -> int:
    """Put what `find_candidates` found on that person's candidate list (no duplicates, a taken or kept one is not
    asked again, an ignored one only when it changed, at most CAND_MAX kept: the oldest go). How many are new."""
    now = time.time() if now is None else now
    added = 0
    for c in found.get("candidates") or []:
        con = ss._open(path)
        try:
            old = _row(con.execute("SELECT state, mtime FROM deliverables WHERE owner_kind=? AND owner_key=? AND ref=?",
                                   (owner_kind, str(owner_key), c["ref"])).fetchone())
        finally:
            con.close()
        if old and old["state"] in ("new", "kept", "taken"):
            continue
        if old and old["state"] == "dismissed" and c["mtime"] <= old["mtime"]:
            continue
        if old and old["state"] == "candidate":
            continue
        try:
            add(owner_kind, owner_key, owner_label, c["ref"], title=c["title"], origin="auto", source=c["source"],
                state="candidate", now=now, home=home, protected=protected, path=path)
            added += 1
        except ValueError:
            continue
    if added:
        con = ss._open(path)
        try:
            con.execute("DELETE FROM deliverables WHERE state='candidate' AND owner_kind=? AND owner_key=? AND id NOT IN ("
                        "SELECT id FROM deliverables WHERE state='candidate' AND owner_kind=? AND owner_key=?"
                        " ORDER BY created_at DESC, id DESC LIMIT ?)", (owner_kind, str(owner_key), owner_kind, str(owner_key), CAND_MAX))
        finally:
            con.close()
    if found.get("skipped"):
        _note_skipped((owner_kind, owner_key), found["skipped"], path)
    return added


def prune(con, now) -> int:
    """Rows of what was taken or ignored more than 180 days ago (never a file). Called by state_store.maintain()."""
    cur = con.execute("DELETE FROM deliverables WHERE state IN ('taken','dismissed') AND updated_at < ?", (now - KEEP_TAKEN_S,))
    return cur.rowcount


__all__ = ["STATES", "add", "get", "listing", "move", "mark_seen", "overview", "find_candidates", "record_candidates", "mask",
           "secret_reason", "sqlite3"]
