"""Tasks and what happened to them, in one SQLite file: state.db in the state folder.

Bots, schedules and A2A hand-offs used to keep their state in JSON files and in the
conversations themselves, so nothing could answer "who is doing what, how far, stuck on
whom". This module is the one place that opens the database; everything else (the app's
`sfctl task`, later the schedule runner and the phone) goes through it.

Rules:
- One file, WAL, no service. Nothing is opened at import time.
- Deliverables (what a bot hands the person, as references) live in a table of their own (deliverables.py).
- Events are appended, never edited; a task's columns are its current state. History,
  audit and "how long has it waited" all come from the events.
- "Stuck" is not a status: it is an open task whose last event is older than a limit
  (`stuck()`), so nobody has to remember to set it back.
- No secret is stored: every text goes through agent_status.redact first.
- When the file cannot be read, or was written by a newer version, every call raises
  `Unavailable` with a plain sentence and the app goes on as before.
"""
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import sf_config

SCHEMA = 2
STATUSES = ("todo", "doing", "waiting", "done", "dropped")
OPEN = ("todo", "doing", "waiting")
CLOSED = ("done", "dropped")
KINDS = ("work", "decision")
USER = "user"                       # the person, as an owner or a requester
TITLE_MAX = 200
TEXT_MAX = 2000
NAME_MAX = 80
STUCK_S = {"waiting": 2 * 3600, "doing": 24 * 3600, "todo": 7 * 86400}
KEEP_CLOSED_S = 365 * 86400         # a task closed longer ago than this goes, with its events
BACKUP_KEEP = 7
MAINTAIN_EVERY_S = 86400
FIELDS = ("title", "kind", "status", "owner", "waiting_on", "next_step", "due_at", "parent_id")

_MIGRATIONS = {
    1: """
    CREATE TABLE tasks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      kind TEXT NOT NULL DEFAULT 'work',
      status TEXT NOT NULL DEFAULT 'todo',
      owner TEXT NOT NULL DEFAULT '',
      requester TEXT NOT NULL DEFAULT '',
      waiting_on TEXT NOT NULL DEFAULT '',
      next_step TEXT NOT NULL DEFAULT '',
      due_at REAL,
      parent_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
      origin TEXT NOT NULL DEFAULT '',
      sid TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL,
      closed_at REAL
    );
    CREATE TABLE task_deps(
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      depends_on INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      PRIMARY KEY(task_id, depends_on)
    );
    CREATE TABLE events(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
      ts REAL NOT NULL,
      actor TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL,
      text TEXT NOT NULL DEFAULT '',
      ref TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX events_task ON events(task_id, ts);
    CREATE INDEX tasks_open ON tasks(status, owner);
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    2: """
    CREATE TABLE deliverables(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      owner_kind TEXT NOT NULL,
      owner_key TEXT NOT NULL,
      owner_label TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL,
      ref TEXT NOT NULL,
      type TEXT NOT NULL DEFAULT 'file',
      title TEXT NOT NULL DEFAULT '',
      note TEXT NOT NULL DEFAULT '',
      task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
      state TEXT NOT NULL DEFAULT 'new',
      origin TEXT NOT NULL DEFAULT 'sfctl',
      source TEXT NOT NULL DEFAULT '',
      flag TEXT NOT NULL DEFAULT '',
      size INTEGER NOT NULL DEFAULT 0,
      mtime REAL NOT NULL DEFAULT 0,
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL,
      seen_at REAL,
      taken_at REAL,
      UNIQUE(owner_kind, owner_key, ref)
    );
    CREATE INDEX deliverables_owner ON deliverables(owner_kind, owner_key, state);
    CREATE INDEX deliverables_state ON deliverables(state, seen_at);
    """,
}

_lock = threading.Lock()
_ready = set()                      # paths migrated in this process


class Unavailable(Exception):
    """The database cannot be used now; str() is a plain sentence {en, zh} picked by the caller."""

    def __init__(self, code: str, en: str, zh: str):
        super().__init__(en)
        self.code, self.en, self.zh = code, en, zh


class NotFound(Exception):
    pass


def db_path() -> Path:
    return sf_config.state_dir() / "state.db"


def _clean(text, limit: int) -> str:
    """One line, credentials masked, cut to `limit` (the shared rule for anything kept or shown)."""
    import agent_status                         # late: a heavy module, and this one must import cheaply
    return agent_status.redact(str(text or ""), limit=limit)


def _name(text) -> str:
    return _clean(text, NAME_MAX)


def _open(path) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(p), timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
    except (sqlite3.Error, OSError) as e:
        raise Unavailable("open_failed", f"The task database could not be opened ({type(e).__name__}).",
                          f"任務資料庫打不開（{type(e).__name__}）。") from None
    key = str(p)
    if key not in _ready:
        with _lock:
            if key not in _ready:
                try:
                    _migrate(con, p)
                except Unavailable:
                    con.close()
                    raise
                except sqlite3.Error as e:
                    con.close()
                    raise Unavailable("open_failed", f"The task database could not be read ({type(e).__name__}).",
                                      f"任務資料庫讀不了（{type(e).__name__}）。") from None
                _ready.add(key)
    return con


def _migrate(con: sqlite3.Connection, p: Path) -> None:
    """Bring the file up to SCHEMA under a write lock, re-reading the version inside it (another
    process may have just done it). An older file is copied first with the backup API (WAL-safe);
    a newer one (the app was rolled back) is left alone and the store is unavailable."""
    have = con.execute("PRAGMA user_version").fetchone()[0]
    if have > SCHEMA:
        raise Unavailable("newer_schema", "The task database was written by a newer ShellBot; update to use it.",
                          "任務資料庫是較新版的 ShellBot 寫的，更新後才能用。")
    if have == SCHEMA:
        return
    if have > 0:
        # Before the write lock: the backup API reads through this connection and retries
        # for ever while that same connection holds a write transaction.
        dst = sqlite3.connect(str(p.with_name(f"{p.name}.bak-v{have}")))
        try:
            con.backup(dst)
        finally:
            dst.close()
    con.execute("BEGIN IMMEDIATE")
    try:
        have = con.execute("PRAGMA user_version").fetchone()[0]
        for v in range(have + 1, SCHEMA + 1):
            for stmt in [s for s in _MIGRATIONS[v].split(";") if s.strip()]:
                con.execute(stmt)
            con.execute(f"PRAGMA user_version={int(v)}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def available(path=None) -> bool:
    try:
        _open(path).close()
        return True
    except Unavailable:
        return False


# ---- reading ----------------------------------------------------------------------------

def _row(r) -> dict:
    return {k: r[k] for k in r.keys()} if r is not None else None


def _deps(con, task_id: int) -> list:
    return [x[0] for x in con.execute("SELECT depends_on FROM task_deps WHERE task_id=? ORDER BY depends_on", (task_id,))]


def get(task_id: int, *, events: bool = True, limit: int = 50, path=None) -> dict:
    con = _open(path)
    try:
        t = _row(con.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone())
        if not t:
            raise NotFound(task_id)
        t["depends_on"] = _deps(con, t["id"])
        t["children"] = [x[0] for x in con.execute("SELECT id FROM tasks WHERE parent_id=? ORDER BY id", (t["id"],))]
        if events:
            rows = con.execute("SELECT * FROM events WHERE task_id=? ORDER BY ts DESC, id DESC LIMIT ?",
                               (t["id"], int(limit))).fetchall()
            t["events"] = [_row(r) for r in reversed(rows)]
        return t
    finally:
        con.close()


def listing(*, owner=None, requester=None, status="open", kind=None, limit: int = 50, path=None) -> list:
    """Tasks, the open ones by default: due first (soonest), then the most recently touched."""
    where, args = [], []
    if status == "open":
        where.append(f"status IN ({','.join('?' * len(OPEN))})")
        args += list(OPEN)
    elif status == "closed":
        where.append(f"status IN ({','.join('?' * len(CLOSED))})")
        args += list(CLOSED)
    elif status and status != "all":
        where.append("status=?")
        args.append(status)
    for col, val in (("owner", owner), ("requester", requester), ("kind", kind)):
        if val is not None:
            where.append(f"{col}=?")
            args.append(val)
    sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY (due_at IS NULL), due_at, updated_at DESC LIMIT ?"
    con = _open(path)
    try:
        return [_row(r) for r in con.execute(sql, args + [int(limit)])]
    finally:
        con.close()


def find_by_ref(kind: str, ref: str, path=None):
    """The task an event of this kind and ref belongs to (e.g. the hand-off whose id is ref), or None."""
    if not ref:
        return None
    con = _open(path)
    try:
        r = con.execute("SELECT task_id FROM events WHERE kind=? AND ref=? AND task_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                        (kind, _clean(ref, 200))).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def stuck(now: float = None, *, limits: dict = None, path=None) -> list:
    """Open tasks whose last event is older than the limit for their status, oldest first,
    each with `idle_s` (seconds since anything happened to it)."""
    now = time.time() if now is None else now
    limits = limits or STUCK_S
    con = _open(path)
    try:
        rows = con.execute(
            "SELECT t.*, COALESCE((SELECT MAX(ts) FROM events e WHERE e.task_id=t.id), t.updated_at) AS last_ts "
            f"FROM tasks t WHERE t.status IN ({','.join('?' * len(OPEN))})", list(OPEN)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        t = _row(r)
        idle = now - float(t.pop("last_ts") or t["updated_at"])
        if idle >= limits.get(t["status"], float("inf")):
            t["idle_s"] = int(idle)
            out.append(t)
    return sorted(out, key=lambda t: -t["idle_s"])


# ---- writing ----------------------------------------------------------------------------

def _event(con, task_id, ts, actor, kind, text="", ref=""):
    con.execute("INSERT INTO events(task_id, ts, actor, kind, text, ref) VALUES(?,?,?,?,?,?)",
                (task_id, ts, _name(actor), kind, _clean(text, TEXT_MAX), _clean(ref, 200)))


def _check(fields: dict, con) -> None:
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status: {fields['status']}")
    if "kind" in fields and fields["kind"] not in KINDS:
        raise ValueError(f"kind: {fields['kind']}")
    if "title" in fields and not str(fields["title"] or "").strip():
        raise ValueError("title: empty")
    pid = fields.get("parent_id")
    if pid is not None and not con.execute("SELECT 1 FROM tasks WHERE id=?", (int(pid),)).fetchone():
        raise NotFound(pid)


def _norm(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k == "title":
            out[k] = _clean(v, TITLE_MAX)
        elif k == "next_step":
            out[k] = _clean(v, TEXT_MAX)
        elif k in ("owner", "waiting_on"):
            out[k] = _name(v)
        elif k in ("due_at",):
            out[k] = float(v) if v not in (None, "") else None
        elif k == "parent_id":
            out[k] = int(v) if v not in (None, "", 0) else None
        else:
            out[k] = v
    return out


def add(title: str, *, actor: str = "", owner: str = "", requester: str = "", kind: str = "work",
        status: str = "todo", next_step: str = "", waiting_on: str = "", due_at=None, parent_id=None,
        depends_on=(), origin: str = "", sid: str = "", now: float = None, path=None) -> dict:
    now = time.time() if now is None else now
    fields = _norm({"title": title, "kind": kind, "status": status, "owner": owner, "waiting_on": waiting_on,
                    "next_step": next_step, "due_at": due_at, "parent_id": parent_id})
    con = _open(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            _check(fields, con)
            for d in depends_on or ():
                if not con.execute("SELECT 1 FROM tasks WHERE id=?", (int(d),)).fetchone():
                    raise NotFound(d)
            cur = con.execute(
                "INSERT INTO tasks(title, kind, status, owner, requester, waiting_on, next_step, due_at, parent_id,"
                " origin, sid, created_at, updated_at, closed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fields["title"], fields["kind"], fields["status"], fields["owner"], _name(requester),
                 fields["waiting_on"], fields["next_step"], fields["due_at"], fields["parent_id"],
                 _name(origin), _name(sid), now, now, now if fields["status"] in CLOSED else None))
            tid = cur.lastrowid
            for d in depends_on or ():
                con.execute("INSERT OR IGNORE INTO task_deps(task_id, depends_on) VALUES(?,?)", (tid, int(d)))
            _event(con, tid, now, actor, "created", fields["title"], origin)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return get(tid, path=path)


def update(task_id: int, *, actor: str = "", now: float = None, path=None, **changes) -> dict:
    """Change some of a task's columns; one event per column that really changed. Closing sets
    closed_at; reopening clears it. Unknown fields are refused."""
    bad = [k for k in changes if k not in FIELDS]
    if bad:
        raise ValueError(f"fields: {bad}")
    now = time.time() if now is None else now
    new = _norm(changes)
    con = _open(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            t = _row(con.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone())
            if not t:
                raise NotFound(task_id)
            _check(new, con)
            if new.get("parent_id") == t["id"]:
                raise ValueError("parent_id: itself")
            diff = {k: v for k, v in new.items() if t.get(k) != v}
            if diff:
                sets = dict(diff, updated_at=now)
                if "status" in diff:
                    sets["closed_at"] = now if diff["status"] in CLOSED else None
                con.execute("UPDATE tasks SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?",
                            list(sets.values()) + [t["id"]])
                for k, v in diff.items():
                    before = t.get(k)
                    _event(con, t["id"], now, actor, k, f"{_show(k, before)} → {_show(k, v)}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return get(task_id, path=path)


def note(task_id: int, text: str, *, actor: str = "", kind: str = "note", ref: str = "",
         now: float = None, path=None) -> dict:
    """Something that happened to a task without changing its columns: a note, a hand-off sent,
    a result back. It counts as activity (a task with a recent note is not stuck)."""
    if not str(text or "").strip():
        raise ValueError("text: empty")
    now = time.time() if now is None else now
    con = _open(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            if not con.execute("SELECT 1 FROM tasks WHERE id=?", (int(task_id),)).fetchone():
                raise NotFound(task_id)
            _event(con, int(task_id), now, actor, _name(kind) or "note", text, ref)
            con.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now, int(task_id)))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    return get(task_id, path=path)


def _show(field: str, value) -> str:
    if value in (None, ""):
        return "—"
    if field == "due_at":
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    if field == "parent_id":
        return f"#{value}"
    return str(value)


# ---- upkeep -----------------------------------------------------------------------------

def maintain(now: float = None, *, path=None) -> dict:
    """Once a day at most: a backup (backup API, the last BACKUP_KEEP kept), then drop tasks
    closed more than KEEP_CLOSED_S ago with their events. Answers what it did."""
    now = time.time() if now is None else now
    p = Path(path) if path else db_path()
    con = _open(p)
    try:
        last = con.execute("SELECT value FROM meta WHERE key='maintained_at'").fetchone()
        if last and now - float(last[0]) < MAINTAIN_EVERY_S:
            return {"ran": False}
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d")
        dst = sqlite3.connect(str(p.with_name(f"{p.name}.bak-{stamp}")))
        try:
            con.backup(dst)
        finally:
            dst.close()
        old = sorted(p.parent.glob(f"{p.name}.bak-2*"))
        for f in old[:-BACKUP_KEEP]:
            try:
                f.unlink()
            except OSError:
                pass
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute("DELETE FROM tasks WHERE closed_at IS NOT NULL AND closed_at < ?", (now - KEEP_CLOSED_S,))
        gone = cur.rowcount
        con.execute("DELETE FROM events WHERE task_id IS NULL AND ts < ?", (now - KEEP_CLOSED_S,))
        import deliverables                          # late: it imports this module
        deliverables.prune(con, now)
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('maintained_at', ?)", (str(now),))
        con.execute("COMMIT")
        return {"ran": True, "backup": stamp, "removed": gone}
    finally:
        con.close()


# ---- words the callers share --------------------------------------------------------------

_REL = re.compile(r"^\+(\d+(?:\.\d+)?)\s*([hdw])$", re.I)


def parse_due(text: str, now: float = None):
    """'2026-11-20', '2026-11-20 18:00', '+3h', '+2d', '+1w' → epoch seconds (local time);
    '' or 'none' → None. A date alone means the end of that day (23:59). Anything else: ValueError."""
    s = str(text or "").strip()
    if not s or s.lower() in ("none", "-", "清除"):
        return None
    now = time.time() if now is None else now
    m = _REL.match(s)
    if m:
        n, unit = float(m.group(1)), m.group(2).lower()
        return now + n * {"h": 3600, "d": 86400, "w": 7 * 86400}[unit]
    for fmt, end_of_day in (("%Y-%m-%d %H:%M", False), ("%Y-%m-%dT%H:%M", False), ("%Y-%m-%d", True)):
        try:
            d = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if end_of_day:
            d = d.replace(hour=23, minute=59)
        return d.timestamp()
    raise ValueError(s)
