"""Gmail on the connector core: the adapter. The steps, the words and the FAQ are in gmail.json.

Two routes (ids as in the description):

  connector  Claude's own Gmail connector, connected once on claude.ai. Nothing to install.
  local      A Gmail MCP server on this computer (@dguido/google-workspace-mcp) with a Google Cloud OAuth
             client the person makes. More steps; works whatever way Claude is signed in.

What the description cannot say for itself lives here: the checks (is Node.js there, is a file in place,
how is Claude signed in), the actions the core does not have (open the profile folder, open the sign-in
tab) and the connection test. The core's own `cli_add` adds the server to the AIs' settings; this file
never writes a CLI's settings and never starts a program by itself: every program goes through
`ctx.run`, every tab through `ctx.open_tab`.

The rules this file keeps (docs/connectors.md):

  * It never reads, stores, logs or shows a login file, a token or a client secret. A check asks whether a
    file exists and how big it is; none is ever opened.
  * Every return is a plain dict of codes and numbers. The words are in the description.
  * The test judges from the names of the tools a temporary Claude tab called and the last line it wrote.
    What a tool returned (the mail) is never looked at, never kept and never returned.
"""

import os
import re
import shlex
import threading
import uuid

import sf_config
import sf_lang
from connectors.adapter import Adapter

ID = "gmail"

# ── the local server (the same constants as the description's route.mcp; the test file keeps them equal) ──
# @dguido/google-workspace-mcp keeps, per named profile, credentials.json (the OAuth client the person made)
# and tokens.json (what Google gave back) in <config>/google-workspace-mcp/profiles/<profile>/, where
# <config> is $XDG_CONFIG_HOME when that is an absolute path, else ~/.config, on every OS. A profile of our
# own ("shellbot") keeps ShellBot's files apart from any other program's.
#
# Which permissions Google is asked for depends on the environment of the process that asks:
# GOOGLE_WORKSPACE_SERVICES (which Google services) and GOOGLE_WORKSPACE_READ_ONLY. The sign-in command
# therefore carries the same two the server's settings do; without them Google would be asked for Drive,
# Docs, Calendar and the whole mailbox.
SERVER = "shellbot-gmail"
PROFILE = "shellbot"
PACKAGE = "@dguido/google-workspace-mcp"
SERVICES = "gmail"
NODE_MIN = 22


def config_root(env: dict, home: str = "") -> str:
    xdg = (env or {}).get("XDG_CONFIG_HOME") or ""
    base = xdg if xdg and os.path.isabs(xdg) else os.path.join(home or _home(env), ".config")
    return os.path.join(base, "google-workspace-mcp")


def profile_dir(env: dict, home: str = "") -> str:
    return os.path.join(config_root(env, home), "profiles", PROFILE)


def _home(env: dict) -> str:
    env = env or {}
    return env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")


def auth_command(readonly: bool = True, win: bool = False) -> str:
    """What the sign-in tab runs. The server's own services and read-only choice, and nothing secret.
    (The profile is named on the command line, so it is not repeated as an environment variable.)"""
    env = {"GOOGLE_WORKSPACE_SERVICES": SERVICES}
    if readonly:
        env["GOOGLE_WORKSPACE_READ_ONLY"] = "true"
    base = f"npx -y {PACKAGE} auth --profile {PROFILE}"
    if win:
        sets = "".join(f"$env:{k}='{v}'; " for k, v in env.items())
        return f'powershell -NoProfile -Command "{sets}{base}"'
    return " ".join(f"{k}={v}" for k, v in env.items()) + " " + base


def shown_path(path: str, home: str = "") -> str:
    """A folder as the person should see it: `~/…` under the home folder (the page never shows a user
    name), forward slashes."""
    p, h = str(path or ""), str(home or os.path.expanduser("~"))
    norm = lambda x: x.replace("\\", "/").rstrip("/")
    if h and norm(p).lower().startswith(norm(h).lower() + "/"):
        return "~" + norm(p)[len(norm(h)):]
    return norm(p)


# ── how Claude Code is signed in, from what ShellBot already keeps ────────────
_PLAN_NAMES = {"free": "Free", "pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise"}


def claude_login(cfg: dict) -> dict:
    """{"state": "claude_ai", "plan": "Max"} when the account a new Claude tab would use has a subscription
    plan on record, else {"state": "unknown"}. Reads only the account labels ShellBot keeps in its own
    config (the plan: never a token, not the e-mail either); it opens no login file. A plan on record
    means a claude.ai subscription login; an account with no plan could be a Console (API) one, so it is
    not claimed."""
    accounts = (cfg or {}).get("accounts") or {}
    ref = ((accounts.get("global") or {}).get("claude"))
    profiles = ((accounts.get("profiles") or {}).get("claude")) or []
    item = next((p for p in profiles if isinstance(p, dict) and ref and p.get("id") == ref), None)
    plan = str((item or {}).get("plan") or "").strip()
    if item and plan:
        return {"state": "claude_ai", "plan": _PLAN_NAMES.get(plan.lower(), plan.capitalize())}
    return {"state": "unknown"}


# ── checks and actions: fn(ctx, args) -> {"state": <code>, …} ────────────────
def _readonly(args: dict) -> bool:
    """The person's "Read mail only" choice (default on). It arrives as args["options"] or flat in args."""
    opts = args.get("options") if isinstance(args.get("options"), dict) else args
    return bool(opts.get("readonly", True))


def check_claude_account(ctx, args: dict) -> dict:
    return claude_login(ctx.cfg)


def check_node(ctx, args: dict) -> dict:
    """The version of the node a tab would find. {"state": "ok" | "old" | "missing", "version"}."""
    node = ctx.find("node")
    if not node:
        return {"state": "missing"}
    r = ctx.run([node, "--version"], 10)
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", r.out or "") if r.rc == 0 else None
    if not m:
        return {"state": "missing"}
    return {"state": "ok" if int(m.group(1)) >= NODE_MIN else "old", "version": ".".join(m.groups())}


def check_credentials(ctx, args: dict) -> dict:
    """Is the OAuth client's file in the profile folder. Only names and sizes are looked at: the file is
    never opened. {"state": "ok" | "no_folder" | "missing" | "empty" | "double_ext" | "downloaded_name"}."""
    d = profile_dir(ctx.env)
    if not os.path.isdir(d):
        return {"state": "no_folder"}
    target = os.path.join(d, "credentials.json")
    if os.path.exists(target):
        return {"state": "ok" if os.path.getsize(target) > 0 else "empty"}
    names = [n.lower() for n in os.listdir(d)]
    if "credentials.json.json" in names:
        return {"state": "double_ext"}
    if any(n.startswith("client_secret") and n.endswith(".json") for n in names):
        return {"state": "downloaded_name"}
    return {"state": "missing"}


def check_tokens(ctx, args: dict) -> dict:
    """Has the sign-in finished (the server wrote its tokens.json). Exists or not; never opened."""
    return {"state": "ok" if os.path.exists(os.path.join(profile_dir(ctx.env), "tokens.json")) else "missing"}


def action_open_folder(ctx, args: dict) -> dict:
    """Make the profile folder (private to the person) and open it in Finder / Explorer."""
    d = profile_dir(ctx.env)
    os.makedirs(d, mode=0o700, exist_ok=True)
    ctx.open_path(d)
    return {"state": "opened", "path": shown_path(d, _home(ctx.env))}


def action_auth_tab(ctx, args: dict) -> dict:
    """Open a tab that runs the server's Google sign-in. The browser opens by itself and the tab ends when
    it is done. Nothing is typed into it and nothing is read out of it."""
    sid = ctx.open_tab(auth_command(_readonly(args), bool(ctx.win)), sf_lang.pick("Gmail sign-in", "Gmail 登入"))
    return {"state": "opened", "sid": sid}


# ── the connection test ───────────────────────────────────────────────────────
# A temporary Claude tab is asked to look up the newest e-mail's sender and subject. The adapter judges
# from two things it can already see and nothing else: which tools the tab called (the names) and the last
# line it wrote. Needs from ctx (SA10 progress file lists them for the core): tab_ready, send,
# tab_events, close_tab.
TEST_MARK = "GMAIL-TEST"
PREFIX = {"connector": "mcp__claude_ai_Gmail__", "local": f"mcp__{SERVER}__"}      # a tool's name starts with this
AUTH_TOOL = "mcp__claude_ai_Gmail__authenticate"       # the connector is there but has not been authorized
TEST_TIMEOUT_S = 180.0                                  # from the prompt going in, not counting a wait for the person's OK
READY_TIMEOUT_S = 90.0                                  # the tab getting to its prompt
# Which step to send the person back to, per route and verdict (the description has the words).
FAILED_STEP = {"connector": {"needs_auth": "a2", "no_tools": "a2", "not_ready": "a1"},
               "local": {"no_tools": "b6", "failed": "b7"}}
_VERDICT = re.compile(r"^" + TEST_MARK + r":\s*(OK|FAIL)\b", re.I)


def test_prompt() -> str:
    """The fixed sentence the test tab is given. One line: it is pasted into a prompt box. It follows the OS
    language like every prompt ShellBot writes for an agent."""
    en = ("This is a Gmail connection test. First load the Gmail tools with ToolSearch, then look up only the "
          "sender and the subject of the newest email. Do not open the body, do not send anything and do not "
          f"change anything. Report it in one line. Then write a last line on its own: {TEST_MARK}: OK, or "
          f"{TEST_MARK}: FAIL followed by the reason.")
    zh = ("這是 Gmail 連線測試。先用 ToolSearch 載入 Gmail 工具，只查最新一封信的寄件者和標題，不開內文、"
          f"不寄信、不改任何東西。用一行回報。最後單獨一行寫 {TEST_MARK}: OK，失敗就寫 {TEST_MARK}: FAIL 加原因。")
    return sf_lang.pick(en, zh)


def verdict_line(text) -> str:
    """"OK", "FAIL" or "" from the last lines of a reply. Only the marker line is looked at (markdown around
    it is ignored); the rest of the reply, which may hold the sender and the subject, is dropped here and
    never returned."""
    lines = [re.sub(r"[`*_>\s]+", " ", ln).strip() for ln in str(text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    for ln in reversed(lines[-2:]):
        m = _VERDICT.match(ln)
        if m:
            return m.group(1).upper()
    return ""


def judge(route: str, events: list, tab_state: str = "", elapsed: float = 0.0) -> dict:
    """What the test came to, from the test tab's transcript events (oldest first; only the `kind`, the tool
    name and the last reply's marker line are used).

      {"state": "running", "phase": "asking" | "reading", "calls": n}
      {"state": "needs_allow", "calls": n}                  the tab waits for the person to allow a tool
      {"state": "done", "verdict": <code>, "calls": n}

    verdicts: pass | needs_auth (connector only) | no_tools | failed | said_ok | unclear | timeout."""
    prefix = PREFIX.get(route, "")
    start = max((i for i, e in enumerate(events or ()) if e.get("kind") == "user_msg" and TEST_MARK in (e.get("text") or "")), default=-1)
    if start < 0:                                           # the prompt is not in the transcript yet
        return {"state": "running", "phase": "asking", "calls": 0} if elapsed <= TEST_TIMEOUT_S else {"state": "done", "verdict": "timeout", "calls": 0}
    turn = list(events)[start + 1:]
    names = [str(e.get("tool") or "") for e in turn if e.get("kind") == "tool_call"]
    calls = [n for n in names if prefix and n.startswith(prefix) and n != AUTH_TOOL]
    ended = any(e.get("kind") == "turn_end" for e in turn)
    reply = next((e.get("text") for e in reversed(turn) if e.get("kind") == "assistant_text"), "")
    marker = verdict_line(reply)
    if not ended:
        if tab_state == "decision":
            return {"state": "needs_allow", "calls": len(calls)}
        if elapsed > TEST_TIMEOUT_S:
            return {"state": "done", "verdict": "timeout", "calls": len(calls)}
        return {"state": "running", "phase": "reading" if names else "asking", "calls": len(calls)}
    if route == "connector" and AUTH_TOOL in names and not calls:
        verdict = "needs_auth"
    elif not calls:
        verdict = "said_ok" if marker == "OK" else "no_tools"
    elif marker == "OK":
        verdict = "pass"
    elif marker == "FAIL":
        verdict = "failed"
    else:
        verdict = "unclear"
    return {"state": "done", "verdict": verdict, "calls": len(calls)}


def test_folder() -> str:
    """The folder the test tab starts in: an empty one of ShellBot's own, so the test conversation does not
    land in the person's home folder's history and no project file of theirs is in its way."""
    folder = str(sf_config.config_dir() / "connect-test-gmail")
    os.makedirs(folder, exist_ok=True)
    return folder


def test_cmd(folder: str, win: bool = False) -> str:
    """What the test tab runs: `cd <folder> && claude` where there is a shell that reads it; a plain
    `claude` on Windows (the tab starts in the home folder: `cd … &&` is not what its shell reads)."""
    return "claude" if win else f"cd {shlex.quote(folder)} && claude"


_RUNS = {}                       # handle -> one test (a temporary tab). One process, so a module-level table
_LOCK = threading.Lock()


def _forget_tabs(ctx):
    """Close every test tab of ours that is still open (a test left from before, a cancel, a disconnect)."""
    with _LOCK:
        left = [r for r in _RUNS.values() if not r["closed"]]
        for r in left:
            r["closed"] = True
        for h in [h for h, r in _RUNS.items() if r["closed"]]:
            _RUNS.pop(h, None)
    for r in left:
        try:
            ctx.close_tab(r["sid"])
        except Exception:        # a tab that is already gone is what we wanted
            pass


def _ask_when_ready(ctx, rec: dict):
    """In a thread: wait for the tab's prompt, then put the one question in. The poll reads `phase`."""
    if not ctx.tab_ready(rec["sid"], READY_TIMEOUT_S):
        rec["phase"] = "not_ready"
        return
    if rec["closed"]:
        return
    rec["sent_at"] = rec["last_poll"] = ctx.now()
    rec["phase"] = "asking" if ctx.send(rec["sid"], test_prompt()) else "send_failed"


def _fail(code: str, route: str = "", sid: str = "", calls: int = 0) -> dict:
    return {"state": "done", "ok": False, "code": code, "failed_step": FAILED_STEP.get(route, {}).get(code, ""),
            "calls": calls, "sid": sid}


class GmailAdapter(Adapter):
    id = ID
    checks = {"claude_account": check_claude_account, "node": check_node,
              "credentials": check_credentials, "tokens": check_tokens}
    actions = {"open_folder": action_open_folder, "auth_tab": action_auth_tab}

    def test(self, ctx, route, handle=None) -> dict:
        """Start (handle=None) or poll the test. Only "it works" or "which step did not" comes back, with the
        number of Gmail calls and the test tab's id (so the page can offer "see the test conversation")."""
        if handle is None:
            return self._start(ctx, route)
        return self._poll(ctx, route, str(handle))

    def _start(self, ctx, route: str) -> dict:
        if route not in PREFIX:
            return _fail("start_failed")
        _forget_tabs(ctx)
        try:
            sid = ctx.open_tab(test_cmd(test_folder(), bool(ctx.win)), sf_lang.pick("Gmail test", "Gmail 測試"))
        except Exception:
            return _fail("start_failed", route)
        handle = uuid.uuid4().hex[:10]
        rec = {"handle": handle, "route": route, "sid": sid, "phase": "waiting_ready", "closed": False,
               "sent_at": 0.0, "paused": 0.0, "last_poll": 0.0}
        with _LOCK:
            _RUNS[handle] = rec
        threading.Thread(target=_ask_when_ready, args=(ctx, rec), daemon=True, name=f"connect-test-gmail-{sid}").start()
        return {"state": "running", "handle": handle, "phase": "starting", "calls": 0, "sid": sid}

    def _poll(self, ctx, route: str, handle: str) -> dict:
        rec = _RUNS.get(handle)
        if not rec or rec["closed"]:
            return _fail("closed", route)
        sid = rec["sid"]
        if ctx.cancel:
            _forget_tabs(ctx)
            return _fail("closed", route, sid)
        snap = ctx.tab_events(sid) or {}
        if not snap.get("alive", False):
            rec["closed"] = True
            return _fail("closed", route, sid)
        if rec["phase"] in ("not_ready", "send_failed"):
            return _fail("not_ready", route, sid)
        if rec["phase"] != "asking":
            return {"state": "running", "handle": handle, "phase": rec["phase"], "calls": 0, "sid": sid}
        now = ctx.now()
        tab_state = str(snap.get("state") or "")
        if tab_state == "decision":                      # waiting for the person's OK: that time does not count
            rec["paused"] += now - rec["last_poll"]
        rec["last_poll"] = now
        elapsed = max(0.0, now - rec["sent_at"] - rec["paused"])
        out = judge(rec["route"], snap.get("events") or [], tab_state, elapsed)
        calls = int(out.get("calls") or 0)
        if out["state"] == "needs_allow":
            return {"state": "needs_user", "handle": handle, "code": "needs_allow", "calls": calls, "sid": sid}
        if out["state"] == "running":
            return {"state": "running", "handle": handle, "phase": out.get("phase", "asking"), "calls": calls, "sid": sid}
        if out["verdict"] == "pass":
            return {"state": "done", "ok": True, "calls": calls, "sid": sid}
        return _fail(out["verdict"], rec["route"], sid, calls)

    def health(self, ctx, route) -> dict:
        """The local server's sign-in is the file it wrote: Google taking the permission back (or the client
        being deleted) makes the server delete it. Exists or not; never opened. The connector's state lives
        on claude.ai, which cannot be asked without starting a Claude tab: unknown."""
        if route == "local":
            return {"state": "ok" if check_tokens(ctx, {})["state"] == "ok" else "revoked"}
        return {"state": "unknown"}

    def disconnect(self, ctx, route) -> dict:
        """The core removes the CLI entries it added. This closes a test tab left open. The permission on
        Google's side, the profile folder and its files are the person's and stay where they are (the
        FAQ says how to take the permission back)."""
        _forget_tabs(ctx)
        return {"state": "done"}


ADAPTER = GmailAdapter()
