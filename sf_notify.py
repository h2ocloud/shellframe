"""sf_notify — optional outbound webhook notifications (stdlib only, leaf module).

可選功能：tab 跑完一輪（DONE）或停在等人（BLOCKED）時，POST 一包 JSON 到使用者
自己設定的 webhook。沒設定＝完全關閉：不寫 log、不印字、不連網。

Two ways in, one payload:
  * `sfctl notify` — an agent (or a script) reports DONE / BLOCKED / QUESTION / INFO.
  * auto-notify    — the status monitor (api_status) feeds every pass to
                     `AutoNotifier.process`; only real transitions send anything.

Config: `~/.config/shellframe/notify.json`, e.g.
    {"url": "https://example.com/hook", "auth": "Bearer <token>",
     "auto": true, "tabs": ["s3"], "labels": ["build-*"]}
`SHELLFRAME_NOTIFY_URL` / `SHELLFRAME_NOTIFY_AUTH` override `url` / `auth`
individually. This module never creates or chmods the file.

Hard rules: never raises into the caller, never follows a redirect (the
Authorization header must not travel anywhere else), and every string that is
printed or logged goes through `scrub` so the URL path/query and the token
cannot leak. Must not import main — sfctl.py imports this module too.
"""

import fnmatch
import json
import os
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

CONFIG_PATH = "~/.config/shellframe/notify.json"   # expanded at call time (HOME may change)
ENV_URL = "SHELLFRAME_NOTIFY_URL"
ENV_AUTH = "SHELLFRAME_NOTIFY_AUTH"
STATUSES = ("DONE", "BLOCKED", "QUESTION", "INFO")

MESSAGE_MAX = 4000       # payload message cap
AUTO_TEXT_MAX = 200      # auto-notify detail text cap
MAX_INFLIGHT = 4         # concurrent auto-notify sends before dropping

_WARNED = set()          # (kind, path, mtime) already warned about — once per file version
_WARN_LOCK = threading.Lock()


def config_path() -> str:
    return os.path.expanduser(CONFIG_PATH)


# ── config ─────────────────────────────────────────────────────────────────
def _warn_once(key, msg, warn):
    """Hand `msg` to the `warn` callback once per key. `msg` never carries file
    contents or the URL — only what is wrong."""
    if warn is None:
        return
    with _WARN_LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    try:
        warn(msg)
    except Exception:
        pass


def _valid_url(url) -> bool:
    try:
        p = urllib.parse.urlsplit(url)
        return p.scheme in ("http", "https") and bool(p.hostname)
    except Exception:
        return False


def _read_file_config(path, warn):
    """-> dict of validated file fields, or {} (absent / unusable)."""
    try:
        st = os.stat(path)
    except OSError:
        return {}                                   # missing file is the normal case
    mtime = getattr(st, "st_mtime_ns", 0)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        _warn_once(("parse", path, mtime), "notify config is unreadable or not valid JSON; ignoring it", warn)
        return {}
    ok = isinstance(raw, dict)
    if ok:
        for k in ("url", "auth"):
            ok = ok and (k not in raw or isinstance(raw[k], str))
        ok = ok and ("auto" not in raw or isinstance(raw["auto"], bool))
        for k in ("tabs", "labels"):
            ok = ok and (k not in raw or (isinstance(raw[k], list)
                                          and all(isinstance(x, str) for x in raw[k])))
    if not ok:
        _warn_once(("shape", path, mtime), "notify config has a field of the wrong type; ignoring it", warn)
        return {}
    if os.name == "posix" and st.st_mode & 0o077:
        home = os.path.expanduser("~")
        shown = ("~" + path[len(home):]) if home != "~" and path.startswith(home) else path
        _warn_once(("perm", path, mtime),
                   f"notify config is readable by other users; run chmod 600 {shown}", warn)
    return raw


def load_config(environ=None, path=None, warn=None):
    """-> {"url","auth","auto","tabs","labels"} or None when no URL is configured.

    None means the feature is off: callers must stay completely silent. `warn`
    (optional callable taking one string) is told about a broken or too-open
    config file, at most once per file version. Never raises."""
    try:
        env = os.environ if environ is None else environ
        fcfg = _read_file_config(path or config_path(), warn)
        url = (env.get(ENV_URL) or "").strip() or (fcfg.get("url") or "").strip()
        auth = (env.get(ENV_AUTH) or "").strip() or (fcfg.get("auth") or "").strip()
        if not url:
            return None
        if not _valid_url(url):
            _warn_once(("url", hash(url)), "notify url must be an http:// or https:// address; notifications are off", warn)
            return None
        return {"url": url, "auth": auth, "auto": bool(fcfg.get("auto", False)),
                "tabs": list(fcfg.get("tabs") or []), "labels": list(fcfg.get("labels") or [])}
    except Exception:
        return None


# ── masking ────────────────────────────────────────────────────────────────
def mask_url(url) -> str:
    """scheme://host[:port]/*** — userinfo, path, query and fragment are dropped."""
    try:
        p = urllib.parse.urlsplit(str(url))
        host = p.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{p.port}" if p.port else ""
        base = f"{p.scheme}://{host}{port}"
        return base + ("/***" if (p.path not in ("", "/") or p.query or p.fragment) else "")
    except Exception:
        return "(invalid url)"


def mask_auth(value) -> str:
    return f"*** ({len(value)} chars)" if value else "(none)"


def _secret_parts(cfg):
    """Every substring of the config that must never appear in output."""
    cfg = cfg or {}
    out = set()
    url, auth = str(cfg.get("url") or ""), str(cfg.get("auth") or "")
    if url:
        out.add(url)
        try:
            p = urllib.parse.urlsplit(url)
            # Pieces shorter than 4 characters carry no secret worth the
            # garbling: replacing a path of "/s" would mangle every "s".
            for part in (p.path, p.path.lstrip("/"), p.query, p.fragment, p.username, p.password):
                if part and len(part) >= 4:
                    out.add(part)
        except Exception:
            pass
    if auth:
        out.add(auth)
        bits = auth.split(None, 1)
        if len(bits) == 2:
            out.add(bits[1])
    return sorted(out, key=len, reverse=True)       # longest first: the whole URL before its pieces


def scrub(text, cfg) -> str:
    """Replace the URL, its path/query/userinfo and the auth value with ***."""
    text = str(text)
    for secret in _secret_parts(cfg):
        text = text.replace(secret, "***")
    return text


# ── payload + transport ────────────────────────────────────────────────────
def _version() -> str:
    try:
        p = Path(__file__).resolve().parent / "version.json"
        return str(json.loads(p.read_text(encoding="utf-8")).get("version") or "")
    except Exception:
        return ""


def build_payload(status, message, sid="", label="", now=None) -> dict:
    if now is None:
        dt = datetime.now()
    elif isinstance(now, (int, float)):
        dt = datetime.fromtimestamp(now)
    else:
        dt = now
    return {
        "machine": socket.gethostname(),
        "sid": str(sid or ""),
        "label": str(label or ""),
        "status": str(status or "").upper(),
        "message": str(message or "")[:MESSAGE_MAX],
        "ts": dt.astimezone().isoformat(timespec="seconds"),
        "shellframe_version": _version(),
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A webhook that answers 3xx is misconfigured; following it would forward
    the Authorization header to wherever it points. Refuse instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _reason(exc) -> str:
    """Short, URL-free description of a transport failure."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}" + (" (redirect refused)" if 300 <= exc.code < 400 else "")
    inner = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(inner, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(inner, ConnectionRefusedError):
        return "connection refused"
    if isinstance(inner, socket.gaierror):
        return "DNS lookup failed"
    if isinstance(inner, ssl.SSLError):
        return "TLS error"
    if isinstance(inner, ValueError):
        return "invalid request"
    if isinstance(inner, OSError):
        return "connection error"
    return "error" if isinstance(inner, str) else f"error ({type(inner).__name__})"


def send(payload, cfg, timeout=5.0):
    """POST `payload` as JSON. -> (ok, info); info is short and already scrubbed.
    2xx is ok; everything else (including 3xx) is not. Never raises.

    Returns within about `timeout` seconds overall. urllib's timeout applies to
    each socket operation and not to DNS, so a slow resolver or a server that
    trickles bytes could hold the caller far longer; the request runs on a
    daemon thread and is abandoned at the deadline."""
    box = []
    try:
        t = threading.Thread(target=lambda: box.append(_post(payload, cfg, timeout)),
                             daemon=True)
        t.start()
        t.join(timeout + 0.5)
    except Exception:
        return False, "error"
    return box[0] if box else (False, "timeout")


def _post(payload, cfg, timeout):
    resp = None
    try:
        headers = {"Content-Type": "application/json"}
        ver = _version()
        headers["User-Agent"] = f"ShellFrame/{ver}" if ver else "ShellFrame"
        if cfg.get("auth"):
            headers["Authorization"] = cfg["auth"]
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(cfg["url"], data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        resp = opener.open(req, timeout=timeout)
        code = getattr(resp, "status", 200)
        return (True, f"HTTP {code}") if 200 <= code < 300 else (False, f"HTTP {code}")
    except Exception as e:
        try:
            return False, scrub(_reason(e), cfg)
        except Exception:
            return False, "error"
    finally:
        try:
            if resp is not None:
                resp.close()
        except Exception:
            pass


# ── auto-notify core (pure; api_status wires it to the status monitor) ─────
def classify(result):
    """One status-monitor entry -> "DONE" | "BLOCKED" | None."""
    if not isinstance(result, dict):
        return None
    state = result.get("state") or ""
    if result.get("blocked") or state == "decision":
        return "BLOCKED"
    if state == "done":
        return "DONE"
    return None


class TransitionTracker:
    """Turns a stream of per-pass states into one-shot events.

    Per sid: `armed` = a turn was seen running since the last DONE; `last` = the
    last category reported. Single-threaded (the status monitor thread)."""

    def __init__(self):
        self._recs = {}

    def observe(self, sid, result):
        state = (result.get("state") or "") if isinstance(result, dict) else ""
        cat = classify(result)
        rec = self._recs.get(sid)
        if rec is None:
            # Baseline. A restart must not announce every tab already sitting at done/blocked.
            self._recs[sid] = {"armed": state == "working", "last": cat}
            return None
        if state == "working":
            rec["armed"], rec["last"] = True, None
            return None
        if cat == "BLOCKED":
            fire = rec["last"] != "BLOCKED"
            rec["last"] = "BLOCKED"
            return "BLOCKED" if fire else None
        if cat == "DONE":
            # Only a turn that actually ran counts: idle prompt -> "done" with no
            # work in between (fresh tab, dismissed startup dialog) stays quiet.
            fire = rec["armed"] and rec["last"] != "DONE"
            rec["armed"], rec["last"] = False, "DONE"
            return "DONE" if fire else None
        return None                                  # idle / unknown / stuck: absorb flapping

    def prune(self, live_sids):
        live = set(live_sids)
        for sid in [s for s in self._recs if s not in live]:
            del self._recs[sid]


def matches_filter(cfg, sid, label) -> bool:
    tabs = cfg.get("tabs") or []
    labels = cfg.get("labels") or []
    if not tabs and not labels:
        return True
    if sid in tabs:
        return True
    lab = str(label or "").casefold()
    return any(fnmatch.fnmatchcase(lab, str(p).casefold()) for p in labels)


def _spawn_daemon(fn):
    threading.Thread(target=fn, daemon=True).start()


class AutoNotifier:
    """Called once per status-monitor pass. Cheap when nothing happens; the
    config is only read when a transition fires, and sends run on daemon threads."""

    def __init__(self, loader=load_config, sender=send, log=None, redact=None, spawn=None):
        self._loader = loader
        self._sender = sender
        self._log_fn = log
        self._redact = redact
        self._spawn = spawn or _spawn_daemon
        self._tracker = TransitionTracker()
        self._slots = threading.BoundedSemaphore(MAX_INFLIGHT)

    def _log(self, msg, cfg=None):
        try:
            if self._log_fn:
                self._log_fn("notify", scrub(msg, cfg))
        except Exception:
            pass

    def _load(self):
        try:
            if self._loader is load_config:
                cfg = load_config(warn=lambda m: self._log("WARN " + m))
            else:
                cfg = self._loader()
        except Exception:
            return None
        return cfg if isinstance(cfg, dict) else None

    def _text(self, text):
        try:
            text = self._redact(text) if self._redact else text
        except Exception:
            return ""                                # never send what we could not redact
        return str(text or "")[:AUTO_TEXT_MAX]

    def _message(self, event, result):
        if event == "BLOCKED":
            return self._text(result.get("blocked")) or "Waiting for a permission decision"
        detail = self._text((result.get("task") or result.get("summary") or "").strip())
        return "Turn finished" + (f": {detail}" if detail else "")

    def _deliver(self, payload, cfg):
        tag = f"{payload['status']} sid={payload['sid']} -> {mask_url(cfg.get('url'))}"
        try:
            ok, info = self._sender(payload, cfg)
            self._log(f"sent {tag}" if ok else f"failed {tag}: {info}", cfg)
        except Exception as e:
            self._log(f"failed {tag}: error ({type(e).__name__})", cfg)
        finally:
            self._slots.release()

    def _fire(self, event, sid, result, label_of):
        cfg = self._load()
        if not cfg or not cfg.get("auto"):
            return
        try:
            label = str(label_of(sid) or "")
        except Exception:
            label = ""
        if not matches_filter(cfg, sid, label):
            return
        payload = build_payload(event, self._message(event, result), sid=sid, label=label)
        if not self._slots.acquire(blocking=False):
            self._log(f"dropped {event} sid={sid}: too many sends in flight", cfg)
            return
        try:
            self._spawn(lambda: self._deliver(payload, cfg))
        except Exception:
            try:
                self._slots.release()        # spawn failed before _deliver could
            except ValueError:
                pass

    def process(self, out, label_of):
        """`out` is the monitor's {sid: result} for this pass. Never raises or blocks."""
        try:
            self._tracker.prune(out.keys())
            for sid, result in list(out.items()):
                try:
                    event = self._tracker.observe(sid, result)
                    if event:
                        self._fire(event, sid, result, label_of)
                except Exception:
                    pass
        except Exception:
            pass
