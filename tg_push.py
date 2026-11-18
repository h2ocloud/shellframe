"""`sfctl tg-push`: a script's own message to the person's Telegram, registered so a reply
to it goes back to that script's owner (see tg_origins.py).

    sfctl tg-push --from <schedule id> [--env-file PATH] [--token-var NAME] [--chat ID] [--preview] "text"

Links in the text get no preview card unless `--preview` is given (the scripts this replaces sent
`disable_web_page_preview`).

  * ShellBot running: the app sends it (to the person's bound chat) and registers it.
  * ShellBot not running, or it could not send: this process sends it with the Bot API and
    writes the registry file itself. The token comes only from the caller's environment:
    the variable `--token-var` names (default BOT_TOKEN), or the same name in the KEY=VALUE
    lines of `--env-file`, read into a local dict (never exported, copied or stored). It is
    not printed, logged, written or sent anywhere but Telegram; a failure says why without it.
  * Exit 0 when sent, 1 when not, so a script can fall back to its old line.

Leaf module (stdlib, tg_origins): it runs in sfctl under the system python.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

import tg_origins

API_BASE = "https://api.telegram.org"
TOKEN_RE = re.compile(r"[0-9]{3,}:[A-Za-z0-9_-]{10,}")
CHAT_RE = re.compile(r"-?[0-9]{1,20}|@[A-Za-z0-9_]{3,64}")
SEND_TIMEOUT_S = 20
RPC_TIMEOUT_S = 45


def parse_env_file(path) -> dict:
    """KEY=VALUE lines (`export` allowed, quotes stripped, # comments) as a dict; raises
    OSError when the file cannot be read."""
    out = {}
    with open(os.path.expanduser(str(path)), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            k, _, v = line.partition("=")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v.strip()
    return out


def scrub(text, token: str) -> str:
    """`text` without the token, however it got into the words."""
    s = str(text)
    if token:
        s = s.replace(token, "***")
    return TOKEN_RE.sub("***", s)


def _api_base() -> str:
    """Telegram's API; a test may point it at a stand-in server, and a test run never reaches the real one."""
    testing = os.environ.get("SHELLFRAME_TESTS") == "1"
    base = (os.environ.get("SHELLFRAME_TG_API_BASE") or API_BASE) if testing else API_BASE
    if testing and base.startswith("https://api.telegram.org"):
        raise OSError("a test run does not reach Telegram")
    return base.rstrip("/")


def send_one(token: str, chat: str, text: str, preview: bool = False):
    """(message_id, "") on success, (None, why) on failure; the why never holds the token."""
    try:
        url = f"{_api_base()}/bot{token}/sendMessage"
        body = {"chat_id": int(chat) if re.fullmatch(r"-?\d+", chat) else chat, "text": text}
        if not preview:
            body["disable_web_page_preview"] = True
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode())
        mid = tg_origins.message_id_of(body)
        return (mid, "") if body.get("ok") and mid is not None else (None, scrub(body.get("description") or "no answer", token)[:120])
    except urllib.error.HTTPError as e:
        why = ""
        try:
            why = str(json.loads(e.read().decode()).get("description") or "")
        except Exception:
            pass
        return None, scrub(f"HTTP {e.code} {why}".strip(), token)[:120]
    except urllib.error.URLError as e:
        return None, scrub(f"network: {e.reason}", token)[:120]
    except Exception as e:
        return None, f"{type(e).__name__}"          # nothing of the message: it may hold the URL


def send_direct(token: str, chat: str, text: str, schedule_id: str, preview: bool = False):
    """Send (split at Telegram's cap) and register every part. (parts sent, "") or (parts sent, why)."""
    parts = tg_origins.split_for_telegram(text)
    sent = 0
    for part in parts:
        mid, why = send_one(token, chat, part, preview)
        if mid is None:
            return sent, why
        sent += 1
        try:      # it went: a registry that cannot be written must not turn that into a failure (the script would send it again)
            tg_origins.record(chat, mid, tg_origins.schedule_origin(schedule_id), part)
        except Exception:
            pass
    return sent, ""


def cli(args, rpc, app_up, environ=None, stdin=None, out=None, err=None) -> int:
    """sfctl's handler. `rpc(cmd, args, timeout)` talks to the running app, `app_up()` says
    whether there is one. Nothing escapes as a traceback (it could show the token)."""
    try:
        return _cli(args, rpc, app_up, environ, stdin, out, err)
    except Exception as e:
        print(f"ERR tg-push failed ({type(e).__name__})", file=err or sys.stderr)
        return 1


def _cli(args, rpc, app_up, environ=None, stdin=None, out=None, err=None) -> int:
    environ = os.environ if environ is None else environ
    out, err = out or sys.stdout, err or sys.stderr

    def fail(msg):
        print(f"ERR {msg}", file=err)
        return 1

    sid = str(args.sched or "").strip()
    if not sid or len(sid) > 120 or any(ord(c) < 32 for c in sid):
        return fail("--from needs the schedule's id (a label, up to 120 characters)")
    text = args.text
    if text in (None, "-"):
        text = (stdin or sys.stdin).read()
    text = str(text or "").strip()
    if not text:
        return fail("nothing to send")
    var = str(args.token_var or "BOT_TOKEN")
    try:
        fvars = parse_env_file(args.env_file) if args.env_file else {}
    except OSError:
        return fail("the env file cannot be read")
    chat = str(args.chat or fvars.get("CHAT_ID") or environ.get("CHAT_ID") or "").strip()
    if chat and not CHAT_RE.fullmatch(chat):
        return fail("the chat id is not a number")

    why = ""
    if app_up():
        preview = bool(getattr(args, "preview", False))
        r = rpc("tg_push", {"text": text, "schedule": sid, "chat": chat, "preview": preview}, timeout=RPC_TIMEOUT_S) or {}
        if r.get("success"):
            print(f"OK {r.get('message') or 'sent by ShellBot'}", file=out)
            return 0
        if (r.get("details") or {}).get("sent"):
            return fail(f"{r.get('message') or 'only part of it was sent'}")        # some went: a second send would repeat them
        why = str(r.get("message") or "")
    token = str(fvars.get(var) or environ.get(var) or "").strip()
    if not token or not TOKEN_RE.fullmatch(token):
        return fail((why + "; " if why else "ShellBot is not running; ")
                    + f"no usable Telegram token in ${var}")
    if not chat:
        return fail((why + "; " if why else "") + "no chat: pass --chat or set CHAT_ID")
    sent, bad = send_direct(token, chat, text, sid, bool(getattr(args, "preview", False)))
    if bad:
        return fail(f"Telegram refused it: {bad}" + (f" ({sent} part(s) went before)" if sent else ""))
    print(f"OK sent directly ({sent} message{'s' if sent != 1 else ''})", file=out)
    return 0
