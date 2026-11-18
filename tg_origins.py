"""Who a Telegram message came from, so a reply to it can find its way back.

The bridge answers whatever tab the person has selected. A reply to a push (a tab's
answer, a scheduled script's report) is about that push, not about the selected tab.
This file is the memory that makes the difference: `tg_origins.json` in the app's
config folder maps a sent message to where it came from.

    {"v": 1, "items": {"<chat id>:<message id>": {"kind": "tab", "sid": "s3", "bot": "ops",
                                                    "at": 1700000000.0, "head": "first 40 chars"}}}

  * `kind` "tab" (`sid`, and `bot` when the tab was a bot's) or "schedule" (`id`);
  * `at` and `head` only: no token, no full text;
  * entries older than 14 days go, and at most 5,000 stay (the oldest go first);
  * written by the bridge (from a background thread: the reply loop serves every tab and
    does not wait for a disk) and by `sfctl tg-push` (a process of its own), so a write
    takes a lock on a sidecar file (`tg_origins.lock`: the data file's inode changes on
    every atomic replace) and replaces the file atomically; a reader needs no lock.

`tg_reply_rules.json` (same folder) is for pushes nobody registered, a script that still
sends with its own Bot API call:

    [{"prefix": "Nightly report", "schedule": "demo.report"},
     {"prefix": "Backup", "bot": "ops"}]

A message whose text starts with `prefix` (after stripping whitespace) counts as that
schedule's, or that bot's. Nothing is built in: the rules are the person's data.

Leaf module (stdlib and sf_config): sfctl runs it under the system python, so it never
imports the bridge.
"""

import contextlib
import json
import os
import queue
import threading
import time
from pathlib import Path

import sf_config

FILE_NAME = "tg_origins.json"
LOCK_NAME = "tg_origins.lock"
RULES_NAME = "tg_reply_rules.json"
TTL_S = 14 * 86400
MAX_ENTRIES = 5000
HEAD_LEN = 40
LOCK_WAIT_S = 3.0
RULES_MAX = 200

_TLOCK = threading.Lock()          # threads of this process; the lock file covers the others


def path() -> Path:
    return sf_config.config_dir() / FILE_NAME


def key(chat_id, message_id) -> str:
    return f"{chat_id}:{message_id}"


def tab_origin(sid: str, bot: str = "") -> dict:
    o = {"kind": "tab", "sid": str(sid)}
    if bot:
        o["bot"] = str(bot)
    return o


def schedule_origin(schedule_id: str) -> dict:
    return {"kind": "schedule", "id": str(schedule_id)}


# ── the file ──────────────────────────────────────────────────────────────

def _lock_fd(fh):
    try:
        import fcntl
    except ImportError:            # Windows
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fh):
    try:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_UN)
    except ImportError:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def _locked(wait: float = LOCK_WAIT_S):
    """Hold the registry lock (threads, then processes), or raise TimeoutError after `wait`."""
    end = time.monotonic() + wait
    if not _TLOCK.acquire(timeout=wait):
        raise TimeoutError("tg_origins: lock busy")
    fh = None
    try:
        d = path().parent
        d.mkdir(parents=True, exist_ok=True)
        fh = open(d / LOCK_NAME, "a+b")
        if fh.seek(0, os.SEEK_END) == 0:
            fh.write(b"\0")        # msvcrt locks a byte that has to exist
            fh.flush()
        while True:
            try:
                _lock_fd(fh)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise TimeoutError("tg_origins: lock busy")
                time.sleep(0.02)
        try:
            yield
        finally:
            _unlock_fd(fh)
    finally:
        if fh is not None:
            fh.close()
        _TLOCK.release()


def _read() -> dict:
    """The items; a missing, torn or foreign file is an empty registry."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return {k: v for k, v in items.items() if isinstance(v, dict)} if isinstance(items, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _prune(items: dict, now: float) -> dict:
    items = {k: v for k, v in items.items() if now - float(v.get("at") or 0) <= TTL_S}
    if len(items) > MAX_ENTRIES:
        newest = sorted(items.items(), key=lambda kv: float(kv[1].get("at") or 0), reverse=True)[:MAX_ENTRIES]
        items = dict(newest)
    return items


def _write(items: dict):
    p = path()
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps({"v": 1, "items": items}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _entry(origin: dict, text: str, now: float) -> dict:
    e = {k: str(origin[k])[:120] for k in ("kind", "sid", "bot", "id") if origin.get(k)}
    e["at"] = now
    e["head"] = " ".join(str(text or "").split())[:HEAD_LEN]
    return e


def record_many(rows, now: float = None) -> int:
    """Register [(chat_id, message_id, origin, text)] in one write. How many were stored
    (0 when the lock stayed busy: a lost entry only means a reply asks who it is for)."""
    now = time.time() if now is None else now
    rows = [(c, m, o, t) for c, m, o, t in rows if c and m and isinstance(o, dict) and o.get("kind")]
    if not rows:
        return 0
    try:
        with _locked():
            items = _read()
            for chat_id, message_id, origin, text in rows:
                items[key(chat_id, message_id)] = _entry(origin, text, now)
            _write(_prune(items, now))
        return len(rows)
    except (OSError, TimeoutError):
        return 0


def record(chat_id, message_id, origin: dict, text: str = "", now: float = None) -> bool:
    return record_many([(chat_id, message_id, origin, text)], now) == 1


def lookup(chat_id, message_id):
    """The origin of a message we sent, or None (unknown, or older than the retention)."""
    e = _read().get(key(chat_id, message_id))
    if not e or time.time() - float(e.get("at") or 0) > TTL_S:
        return None
    return {k: v for k, v in e.items() if k in ("kind", "sid", "bot", "id", "at", "head")}


# ── recording from the bridge: off the reply thread ───────────────────────

_Q = queue.Queue()
_writer = {"thread": None}


def message_id_of(resp):
    """The new message's id in a Bot API answer, or None (a stub, a failure, no answer)."""
    try:
        mid = resp["result"]["message_id"]
        return mid if isinstance(mid, int) and not isinstance(mid, bool) else None
    except (TypeError, KeyError, IndexError):
        return None


def _drain():
    while True:
        batch = [_Q.get()]
        try:
            while len(batch) < 50:
                batch.append(_Q.get_nowait())
        except queue.Empty:
            pass
        rows = []
        for chat_id, mid, origin, text in batch:
            try:
                rows.append((chat_id, mid, origin() if callable(origin) else origin, text))
            except Exception:
                pass
        try:
            record_many(rows)
        except Exception:
            pass
        for _ in batch:
            _Q.task_done()


def note(resp, chat_id, text, origin):
    """After a send: remember where the message came from. `origin` is a dict, or a function
    that makes it (run on the writer thread: finding a tab's bot reads the config). Nothing
    happens when the answer carries no message id."""
    mid = message_id_of(resp)
    if mid is None or not chat_id:
        return
    if _writer["thread"] is None or not _writer["thread"].is_alive():
        _writer["thread"] = threading.Thread(target=_drain, daemon=True, name="tg-origins")
        _writer["thread"].start()
    _Q.put((chat_id, mid, origin, text))


def flush():
    """Wait until what `note` queued is on disk (tests, and a clean exit)."""
    _Q.join()


# ── rules for pushes nobody registered ────────────────────────────────────

def read_rules() -> list:
    """[{"prefix", "schedule"} | {"prefix", "bot"}] from tg_reply_rules.json; anything
    malformed is skipped, a missing file is no rules."""
    try:
        raw = json.loads((sf_config.config_dir() / RULES_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or len(out) >= RULES_MAX:
            continue
        prefix = r.get("prefix")
        if not isinstance(prefix, str) or not prefix.strip():
            continue
        if isinstance(r.get("schedule"), str) and r["schedule"].strip():
            out.append({"prefix": prefix.strip(), "schedule": r["schedule"].strip()[:120]})
        elif isinstance(r.get("bot"), str) and r["bot"].strip():
            out.append({"prefix": prefix.strip(), "bot": r["bot"].strip()[:60]})
    return out


def match_rule(text: str):
    """The origin a pushed message's text belongs to by the rules file, or None."""
    t = str(text or "").strip()
    if not t:
        return None
    for r in read_rules():
        if t.startswith(r["prefix"]):
            return schedule_origin(r["schedule"]) if "schedule" in r else {"kind": "bot", "bot": r["bot"]}
    return None


# ── Telegram's 4096-character cap ─────────────────────────────────────────

def split_for_telegram(text: str, limit: int = 3900) -> list:
    """Split text into <=limit-char chunks at line boundaries so long replies
    are sent as multiple Telegram messages instead of being truncated.

    Telegram's hard cap is 4096 chars/message; 3900 leaves headroom for the
    session-label prefix. A single oversized line is hard-split as a last
    resort. Never drops content.
    """
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                chunks.append(buf); buf = ""
            chunks.append(line[:limit]); line = line[limit:]
        piece = ("\n" + line) if buf else line
        if len(buf) + len(piece) > limit:
            chunks.append(buf); buf = line
        else:
            buf += piece
    if buf:
        chunks.append(buf)
    return chunks

