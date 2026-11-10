"""sf_push — tell the paired phones and iPads that a bot needs them, through APNs.

A phone registers its device token with every computer it is paired with
(POST /link/push/register, signed like every Frame Link call). When a bot asks for
a decision, gets stuck, finishes, or runs `sfctl notify`, this computer sends the
notification to Apple itself: no relay, so the conversation and the key stay on
the user's own computer. An answer picked on the lock screen comes back through
the existing /link/answer.

APNs wants a JWT signed with ES256 and HTTP/2, and Python's standard library has
neither. No package is added for them: `openssl` signs the JWT and `curl` sends
the request, both already on every Mac (and most Linux). Windows ships neither in
a usable form, so there `availability()` says push is not available.

Where things live:
  ~/.config/shellframe/push.json   0600. Key path, key id, team id; one registration
                                   per paired phone; the decisions already pushed (24 h)
  the .p8 key                      wherever the user keeps it, 0600. Never logged,
                                   never in the repo, never in a test

The cues come from what already exists (api_push.py wires them): a [[SF:RED]]
question (the `decision` /link/bots shows), a tab stopping on a dialog or a
[[SF:YELLOW]] (stuck), a finished turn of the primary bot or a delegated task
(done), and `sfctl notify` (info). Nothing here depends on the Telegram bridge.

Leaf module: no `import main`. The app injects its log and redaction.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import sf_lang

CONFIG_PATH = "~/.config/shellframe/push.json"
PATH_ENV = "SHELLFRAME_PUSH_FILE"          # tests point this at a temporary file
HOSTS = {"sandbox": "https://api.sandbox.push.apple.com",
         "production": "https://api.push.apple.com"}
KINDS = ("decision", "stuck", "done", "info")
DEFAULT_PREFS = {"kinds": list(KINDS), "hide_content": False, "show_computer": False,
                 "telegram": "quiet", "done": "primary"}
BODY_MAX = 180              # the whole body, options included
OPTION_MAX = 60
BUTTONS_MAX = 4             # SF_DECISION_2 … SF_DECISION_4 are what the app registers
SF_OPTIONS_MAX = 9
PAYLOAD_MAX = 4096
EXPIRY_S = 6 * 3600         # a phone that was off still gets it; later it means nothing
JWT_REFRESH_S = 40 * 60     # Apple: not more often than every 20 min, never older than 60
TAB_GAP_S = 60              # stuck/done: one sound per tab per minute, later ones replace it quietly
DEVICE_HOUR_MAX = 30
SENT_KEEP_S = 24 * 3600
HEALTHY_S = 24 * 3600
MAX_INFLIGHT = 4
NAME_MAX = 60
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{64,512}$")
_TOPIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,154}$")
_KEY_ID_RE = re.compile(r"^[A-Z0-9]{10}$")
_TITLES = {"decision": ("needs your decision", "要你決定"), "stuck": ("is stuck", "卡住了"),
           "done": ("done", "完成")}
_CATEGORY = {"stuck": "SF_STUCK", "done": "SF_DONE", "info": "SF_INFO"}

_lock = threading.RLock()
_cache = {"mtime": None, "cfg": {}}
_jwt = {"key": None, "token": "", "at": 0.0}
_tools = {}


# ── the file ────────────────────────────────────────────────────────────────
def config_path() -> str:
    return os.path.expanduser(os.environ.get(PATH_ENV) or CONFIG_PATH)


def load() -> dict:
    """push.json, re-read only when it changed: the status monitor asks twice a second."""
    path = config_path()
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] == mtime:
            return json.loads(json.dumps(_cache["cfg"]))
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        cfg = cfg if isinstance(cfg, dict) else {}
        _cache.update(mtime=mtime, cfg=cfg)
        return json.loads(json.dumps(cfg))


def update(fn):
    """Read, change, write back atomically (0600 before it replaces the old file)."""
    with _lock:
        cfg = load()
        out = fn(cfg)
        path = config_path()
        folder = os.path.dirname(path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".push-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _cache.update(mtime=None)
        return out


def devices() -> dict:
    return load().get("devices") or {}


# ── can this computer send ──────────────────────────────────────────────────
def tools():
    """(openssl, curl): the system's own on macOS, else whatever is on PATH."""
    with _lock:
        if "pair" not in _tools:
            def pick(name):
                own = f"/usr/bin/{name}"
                return own if os.access(own, os.X_OK) else shutil.which(name)
            openssl, curl = pick("openssl"), pick("curl")
            http2 = False
            if curl:
                try:
                    out = subprocess.run([curl, "-V"], capture_output=True, text=True, timeout=5).stdout
                    http2 = "HTTP2" in out
                except Exception:
                    http2 = False
            _tools["pair"] = (openssl, curl if http2 else None)
        return _tools["pair"]


def availability(cfg=None) -> str:
    """'' when this computer can send; otherwise why not (the phone shows it)."""
    cfg = load() if cfg is None else cfg
    if os.name == "nt":
        return sf_lang.pick("Push is not available on Windows yet (it needs openssl and an HTTP/2 curl)",
                            "Windows 還不支援推播（需要 openssl 和支援 HTTP/2 的 curl）")
    openssl, curl = tools()
    if not openssl or not curl:
        return sf_lang.pick("openssl or an HTTP/2 curl is missing", "找不到 openssl 或支援 HTTP/2 的 curl")
    if not (cfg.get("key_path") and cfg.get("key_id") and cfg.get("team_id")):
        return sf_lang.pick("This computer has no APNs key yet (sfctl push-setup)",
                            "這台電腦還沒設定 APNs key（sfctl push-setup）")
    if not os.path.isfile(os.path.expanduser(cfg["key_path"])):
        return sf_lang.pick("The APNs key file is missing", "找不到 APNs key 檔")
    return ""


def setup(key_path: str, key_id: str, team_id: str, topic: str = "") -> dict:
    """`sfctl push-setup`: remember which key and whose. The key file stays where it is."""
    path = os.path.abspath(os.path.expanduser(key_path or ""))
    key_id, team_id, topic = (key_id or "").strip(), (team_id or "").strip(), (topic or "").strip()
    if not os.path.isfile(path):
        return {"success": False, "message": f"no key file at {path}"}
    if not _KEY_ID_RE.match(key_id) or not _KEY_ID_RE.match(team_id):
        return {"success": False, "message": "key id and team id are 10 capital letters or digits"}
    if topic and not _TOPIC_RE.match(topic):
        return {"success": False, "message": "topic must be the app's bundle id"}
    try:
        if os.name != "nt" and os.stat(path).st_mode & 0o077:
            os.chmod(path, 0o600)          # a private key readable by others is the one thing to fix here
    except OSError:
        pass

    def fn(cfg):
        cfg.update(key_path=path, key_id=key_id, team_id=team_id)
        if topic:
            cfg["topic"] = topic
    update(fn)
    drop_jwt()
    return {"success": True, "message": "saved"}


# ── JWT (ES256, signed by openssl) ──────────────────────────────────────────
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _der_len(buf: bytes, i: int):
    n = buf[i]
    if n < 0x80:
        return n, i + 1
    k = n & 0x7F
    return int.from_bytes(buf[i + 1:i + 1 + k], "big"), i + 1 + k


def der_to_raw(der: bytes) -> bytes:
    """An ECDSA signature as openssl writes it (a DER SEQUENCE of two INTEGERs) into the
    64-byte r||s that a JWS carries."""
    if not der or der[0] != 0x30:
        raise ValueError("not a DER sequence")
    _, i = _der_len(der, 1)
    out = b""
    for _ in range(2):
        if der[i] != 0x02:
            raise ValueError("not a DER integer")
        n, i = _der_len(der, i + 1)
        value = der[i:i + n].lstrip(b"\x00")
        if len(value) > 32:
            raise ValueError("integer longer than P-256 allows")
        out += value.rjust(32, b"\x00")
        i += n
    return out


def openssl_sign(key_path: str, data: bytes) -> bytes:
    openssl, _ = tools()
    if not openssl:
        raise RuntimeError("openssl is missing")
    r = subprocess.run([openssl, "dgst", "-sha256", "-sign", key_path], input=data,
                       capture_output=True, timeout=10)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("openssl could not sign with that key")
    return r.stdout


def make_jwt(key_path: str, key_id: str, team_id: str, now=None, sign=openssl_sign) -> str:
    header = _b64(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iss": team_id, "iat": int(now or time.time())}, separators=(",", ":")).encode())
    signature = der_to_raw(sign(os.path.expanduser(key_path), f"{header}.{claims}".encode()))
    return f"{header}.{claims}.{_b64(signature)}"


def jwt_for(cfg: dict, now=None, sign=openssl_sign) -> str:
    """The provider token, reused for up to JWT_REFRESH_S as Apple asks."""
    now = now or time.time()
    key = (cfg.get("key_path"), cfg.get("key_id"), cfg.get("team_id"))
    with _lock:
        if _jwt["key"] == key and _jwt["token"] and now - _jwt["at"] < JWT_REFRESH_S:
            return _jwt["token"]
    token = make_jwt(*key, now=now, sign=sign)
    with _lock:
        _jwt.update(key=key, token=token, at=now)
    return token


def drop_jwt():
    with _lock:
        _jwt.update(key=None, token="", at=0.0)


# ── registrations ───────────────────────────────────────────────────────────
def clean_prefs(raw) -> dict:
    prefs = json.loads(json.dumps(DEFAULT_PREFS))
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(raw.get("kinds"), list):
        prefs["kinds"] = [k for k in KINDS if k in raw["kinds"]]
    for key in ("hide_content", "show_computer"):
        if key in raw:
            prefs[key] = bool(raw[key])
    if raw.get("telegram") in ("quiet", "both"):
        prefs["telegram"] = raw["telegram"]
    if raw.get("done") in ("primary", "all"):
        prefs["done"] = raw["done"]
    return prefs


def mask_token(token: str) -> str:
    t = str(token or "")
    return f"{t[:6]}…{t[-4:]}" if len(t) > 12 else "…"


def register(peer_id: str, body: dict, now=None) -> dict:
    """One registration per phone (its Frame Link peer id); a new one replaces it whole."""
    body = body if isinstance(body, dict) else {}
    token = str(body.get("token") or "").strip()
    env = str(body.get("env") or "sandbox").strip()
    topic = str(body.get("topic") or "").strip()
    if not peer_id:
        return {"success": False, "message": "unknown peer"}
    if not _TOKEN_RE.match(token):
        return {"success": False, "message": "token must be the device token in hex"}
    if env not in HOSTS:
        return {"success": False, "message": "env must be sandbox or production"}
    if topic and not _TOPIC_RE.match(topic):
        return {"success": False, "message": "topic must be the app's bundle id"}
    rec = {"token": token.lower(), "env": env, "topic": topic,
           "device_name": " ".join(str(body.get("device_name") or "").split())[:NAME_MAX],
           "prefs": clean_prefs(body.get("prefs")), "registered_at": now or time.time()}

    def fn(cfg):
        old = (cfg.get("devices") or {}).get(peer_id) or {}
        if old.get("token") == rec["token"]:          # same phone, new settings: keep its history
            rec.update({k: old[k] for k in ("last_sent_at", "ok_at", "last_error") if k in old})
        cfg.setdefault("devices", {})[peer_id] = rec
    update(fn)
    return {"success": True, "details": {"registered": True, "enabled": not availability()}}


def unregister(peer_id: str) -> dict:
    update(lambda cfg: (cfg.get("devices") or {}).pop(peer_id, None))
    return {"success": True, "details": {"registered": False}}


def status(peer_id: str) -> dict:
    cfg = load()
    dev = (cfg.get("devices") or {}).get(peer_id) or {}
    why = availability(cfg)
    return {"success": True, "enabled": not why, "reason": why, "registered": bool(dev),
            "env": dev.get("env", ""), "prefs": clean_prefs(dev.get("prefs")) if dev else {},
            "last_sent_at": dev.get("last_sent_at", 0), "last_error": dev.get("last_error", "")}


def telegram_quiet(now=None) -> bool:
    """True when a phone asked for a quiet Telegram and push works for it: a success in the
    last 24 hours and no error since. Push failing means Telegram rings again."""
    cfg = load()
    if not cfg.get("devices") or availability(cfg):
        return False
    now = now or time.time()
    return any(clean_prefs(d.get("prefs"))["telegram"] == "quiet" and not d.get("last_error")
               and float(d.get("ok_at") or 0) > now - HEALTHY_S
               for d in (cfg.get("devices") or {}).values())


# ── what goes to Apple ──────────────────────────────────────────────────────
def _one_line(text, limit) -> str:
    return " ".join(str(text or "").split())[:limit]


def collapse_id(kind: str, sid: str, qid: str = "") -> str:
    return f"q.{qid}" if kind == "decision" else f"{kind}.{sid}"


def build(kind, *, frame_id, frame_name="", sid, bot, role="", text="", qid="", options=(),
          prefs=None, topic="", silent=False, title=None, now=None):
    """(headers, payload) for one alert. `bot` is the name shown, `role` the bot behind the tab
    ('' for a tab that is not one); `options` are (n, text) pairs of a decision."""
    prefs = clean_prefs(prefs)
    hide = prefs["hide_content"]
    now = now or time.time()
    head = title or (f"{bot}｜{sf_lang.pick(*_TITLES[kind])}" if kind in _TITLES else str(bot))
    alert = {"title": _one_line(head, 80)}
    if prefs["show_computer"] and frame_name:
        alert["subtitle"] = _one_line(frame_name, 60)
    opts = [(int(n), _one_line(t, OPTION_MAX)) for n, t in options] if kind == "decision" else []
    if hide:
        alert["body"] = sf_lang.pick("Open ShellBot to see it", "打開 ShellBot 查看")
    else:
        lines = [_one_line(text, BODY_MAX)] + [f"{n}. {t}" for n, t in opts[:BUTTONS_MAX]]
        alert["body"] = "\n".join(ln for ln in lines if ln)[:BODY_MAX]
    if kind == "decision":
        category = (f"SF_DECISION_{len(opts)}" if not hide and 2 <= len(opts) <= BUTTONS_MAX
                    else "SF_OPEN")
    else:
        category = _CATEGORY.get(kind, "SF_INFO")
    aps = {"alert": alert, "category": category, "thread-id": f"{frame_id}:{sid}", "mutable-content": 1}
    if not silent:
        aps["sound"] = "default"
    sf = {"v": 1, "kind": kind, "frame": frame_id, "sid": sid, "role": role}
    if kind == "decision":
        sf["qid"] = qid
        if not hide:
            sf["options"] = [{"n": n, "text": t} for n, t in opts[:SF_OPTIONS_MAX]]
    payload = {"aps": aps, "sf": sf}
    while len(json.dumps(payload, ensure_ascii=False).encode()) > PAYLOAD_MAX:
        if sf.get("options"):
            sf.pop("options")
        elif len(alert.get("body", "")) > 20:
            alert["body"] = alert["body"][:len(alert["body"]) // 2]
        else:
            break
    headers = {"apns-push-type": "alert", "apns-topic": topic,
               "apns-priority": "10" if kind in ("decision", "stuck") else "5",
               "apns-collapse-id": collapse_id(kind, sid, qid),
               "apns-expiration": str(int(now + EXPIRY_S))}
    return headers, payload


def build_withdraw(*, frame_id, ids, topic=""):
    """A silent push that takes notifications off the phone (answered elsewhere, or replaced)."""
    headers = {"apns-push-type": "background", "apns-topic": topic, "apns-priority": "5"}
    payload = {"aps": {"content-available": 1},
               "sf": {"v": 1, "kind": "withdraw", "frame": frame_id, "ids": list(ids)}}
    return headers, payload


# ── HTTP/2 through curl ─────────────────────────────────────────────────────
def _quote(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def curl_config(url: str, headers: dict) -> str:
    """curl's config file syntax, read from stdin: the bearer never reaches argv."""
    lines = [f'url = "{_quote(url)}"']
    lines += [f'header = "{_quote(k)}: {_quote(v)}"' for k, v in headers.items() if v != ""]
    return "\n".join(lines) + "\n"


def parse_reply(stdout: str):
    """(HTTP status, Apple's reason) from `-o - -w '\\n%{http_code}'`."""
    body, _, code = (stdout or "").rpartition("\n")
    try:
        status_code = int(code.strip())
    except ValueError:
        return 0, ""
    reason = ""
    if body.strip():
        try:
            reason = str((json.loads(body) or {}).get("reason") or "")
        except (ValueError, AttributeError):
            reason = _one_line(body, 80)
    return status_code, reason


def curl_transport(url: str, headers: dict, body: bytes, timeout: int = 15):
    _, curl = tools()
    if not curl:
        return 0, "no HTTP/2 curl"
    fd, path = tempfile.mkstemp(prefix=".push-body-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        r = subprocess.run([curl, "--http2", "-sS", "--max-time", str(timeout), "-o", "-",
                            "-w", "\n%{http_code}", "--data-binary", f"@{path}", "-K", "-"],
                           input=curl_config(url, headers).encode(), capture_output=True, timeout=timeout + 5)
    except Exception as e:
        return 0, type(e).__name__
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    code, reason = parse_reply(r.stdout.decode("utf-8", "replace"))
    if not code:
        reason = _one_line(r.stderr.decode("utf-8", "replace"), 120) or "no answer"
    return code, reason


# ── sending ─────────────────────────────────────────────────────────────────
def _spawn(fn):
    threading.Thread(target=fn, daemon=True, name="sf-push").start()


class Pusher:
    """Throttling, de-duplication and Apple's answers. Sends never block the caller."""

    def __init__(self, log=None, redact=None, transport=None, sign=None, spawn=None, clock=time.time):
        self._log_fn = log
        self._redact = redact
        self._transport = transport or curl_transport
        self._sign = sign or openssl_sign
        self._spawn = spawn or _spawn
        self._clock = clock
        self._slots = threading.BoundedSemaphore(MAX_INFLIGHT)
        self._last_sound = {}      # (sid, kind) -> when a sound last went out for it
        self._hour = {}            # peer -> send times in the last hour
        self._backoff = {}         # peer -> (until, seconds)

    def _log(self, msg):
        try:
            if self._log_fn:
                self._log_fn("push", msg)
        except Exception:
            pass

    def _text(self, text):
        try:
            return str(self._redact(text) if self._redact else text or "")
        except Exception:
            return ""                         # never send what could not be redacted

    def _room(self, peer_id, now) -> bool:
        times = [t for t in self._hour.get(peer_id, []) if t > now - 3600]
        if len(times) >= DEVICE_HOUR_MAX:
            self._hour[peer_id] = times
            return False
        times.append(now)
        self._hour[peer_id] = times
        return True

    def _first_time(self, key, now) -> bool:
        """A decision goes out once, also across a restart (push.json keeps 24 h of them)."""
        def fn(cfg):
            sent = {k: v for k, v in (cfg.get("sent") or {}).items() if float(v) > now - SENT_KEEP_S}
            new = key not in sent
            sent[key] = sent.get(key, now)
            cfg["sent"] = sent
            return new
        return update(fn)

    def send(self, kind, *, sid, bot, frame_id, frame_name="", role="", text="", qid="", options=(),
             primary=False, title=None, exclude=(), paired=None) -> int:
        """One cue to every registered device that wants it. How many sends were queued."""
        now = self._clock()
        cfg = load()
        devs = cfg.get("devices") or {}
        if not devs or availability(cfg):
            return 0
        if paired is not None:
            gone = [p for p in devs if p not in paired]
            if gone:
                update(lambda c: [(c.get("devices") or {}).pop(p, None) for p in gone])
                self._log(f"dropped {len(gone)} registration(s) of phones no longer paired")
                devs = {p: d for p, d in devs.items() if p not in gone}
        if kind == "decision" and not self._first_time(collapse_id(kind, sid, qid), now):
            return 0
        silent = False
        if kind in ("stuck", "done"):
            silent = now - self._last_sound.get((sid, kind), 0) < TAB_GAP_S
            if not silent:
                self._last_sound[(sid, kind)] = now
        text = self._text(text)
        options = [(n, self._text(t)) for n, t in options]
        queued = 0
        for peer_id, dev in devs.items():
            prefs = clean_prefs(dev.get("prefs"))
            if peer_id in exclude or kind not in prefs["kinds"]:
                continue
            if kind == "done" and prefs["done"] == "primary" and not primary:
                continue
            if self._backoff.get(peer_id, (0, 0))[0] > now:
                continue
            if not self._room(peer_id, now):
                self._log(f"dropped {kind} sid={sid} -> {mask_token(dev.get('token'))}: "
                          f"{DEVICE_HOUR_MAX} an hour reached")
                continue
            headers, payload = build(kind, frame_id=frame_id, frame_name=frame_name, sid=sid, bot=bot,
                                     role=role, text=text, qid=qid, options=options, prefs=prefs,
                                     topic=dev.get("topic") or cfg.get("topic") or "",
                                     silent=silent, title=title, now=now)
            queued += self._queue(peer_id, dev, headers, payload, f"{kind} sid={sid}")
        return queued

    def test(self, peer_id, *, frame_id, frame_name="", text=""):
        """/link/push/test: one info to this phone now, and Apple's answer. (ok, message)."""
        cfg = load()
        dev = (cfg.get("devices") or {}).get(peer_id)
        why = availability(cfg)
        if why:
            return False, why
        if not dev:
            return False, sf_lang.pick("This phone is not registered here", "這支手機還沒在這台電腦註冊")
        headers, payload = build("info", frame_id=frame_id, frame_name=frame_name, sid="", bot=frame_name or "ShellBot",
                                 text=text, prefs=dev.get("prefs"), topic=dev.get("topic") or cfg.get("topic") or "")
        headers["apns-collapse-id"] = "info.test"
        if not self._slots.acquire(timeout=20):
            return False, "busy"
        code, reason = self._deliver(peer_id, dict(dev), headers, payload, "test")
        if code == 200:
            return True, sf_lang.pick("Sent", "已送出")
        return False, f"HTTP {code} {reason}".strip() if code else (reason or "no answer")

    def withdraw(self, ids, *, frame_id, exclude=()) -> int:
        cfg = load()
        devs = cfg.get("devices") or {}
        if not ids or not devs or availability(cfg):
            return 0
        queued = 0
        for peer_id, dev in devs.items():
            if peer_id in exclude:
                continue
            headers, payload = build_withdraw(frame_id=frame_id, ids=ids,
                                              topic=dev.get("topic") or cfg.get("topic") or "")
            queued += self._queue(peer_id, dev, headers, payload, f"withdraw {','.join(ids)}")
        return queued

    def _queue(self, peer_id, dev, headers, payload, tag) -> int:
        if not self._slots.acquire(blocking=False):
            self._log(f"dropped {tag}: {MAX_INFLIGHT} sends already in flight")
            return 0
        try:
            self._spawn(lambda: self._deliver(peer_id, dict(dev), headers, payload, tag))
            return 1
        except Exception:
            self._slots.release()
            return 0

    def _deliver(self, peer_id, dev, headers, payload, tag):
        """Send one (the caller holds a slot). Apple's (status, reason); 0 when it never got there."""
        code, reason = 0, ""
        try:
            cfg = load()
            dest = f"{mask_token(dev.get('token'))} ({dev.get('env')})"
            try:
                bearer = jwt_for(cfg, sign=self._sign)
            except Exception as e:
                reason = f"signing failed ({type(e).__name__})"
                self._note(peer_id, error=reason)
                self._log(f"failed {tag} -> {dest}: could not sign the provider token")
                return code, reason
            url = f"{HOSTS.get(dev.get('env'), HOSTS['sandbox'])}/3/device/{dev.get('token')}"
            body = json.dumps(payload, ensure_ascii=False).encode()
            code, reason = self._transport(url, dict(headers, authorization=f"bearer {bearer}"), body)
            self._answer(peer_id, dev, code, reason, f"{tag} -> {dest}")
        except Exception as e:
            reason = reason or type(e).__name__
            self._log(f"failed {tag}: {type(e).__name__}")
        finally:
            self._slots.release()
        return code, reason

    def _answer(self, peer_id, dev, code, reason, tag):
        now = self._clock()
        if code == 200:
            self._backoff.pop(peer_id, None)
            self._note(peer_id, ok=True)
            self._log(f"sent {tag}")
            return
        why = f"HTTP {code} {reason}".strip() if code else (reason or "no answer")
        if code == 410 or (code == 400 and reason in ("BadDeviceToken", "DeviceTokenNotForTopic")):
            def fn(cfg):
                cur = (cfg.get("devices") or {}).get(peer_id) or {}
                if cur.get("token") == dev.get("token"):     # not a newer registration
                    cfg["devices"].pop(peer_id, None)
            update(fn)
            self._log(f"failed {tag}: {why}; registration removed, the app registers again when opened")
            return
        if code == 403 and reason in ("InvalidProviderToken", "ExpiredProviderToken"):
            drop_jwt()
        if code == 429:
            wait = min(3600, max(60, self._backoff.get(peer_id, (0, 30))[1] * 2))
            self._backoff[peer_id] = (now + wait, wait)
            why += f"; waiting {wait}s"
        self._note(peer_id, error=why)
        self._log(f"failed {tag}: {why}")

    def _note(self, peer_id, ok=False, error=""):
        now = self._clock()

        def fn(cfg):
            dev = (cfg.get("devices") or {}).get(peer_id)
            if dev is None:
                return
            if ok:
                dev.update(last_sent_at=now, ok_at=now, last_error="")
            else:
                dev["last_error"] = error
        try:
            update(fn)
        except Exception:
            pass
