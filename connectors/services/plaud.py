"""Plaud (voice recorder and transcripts) on the connector core: the adapter. The steps, the words and the FAQ
are in plaud.json.

One route, `local`: the official MCP server (`@plaud-ai/mcp`, pinned) on this computer, added by the core's
`cli_add` to the CLI the primary bot runs on. Its tools are all read-only (the account, the list of recordings,
one recording, its note, its transcript). What the description cannot say for itself lives here:

  node        Node.js 20 or newer (the package's own dependencies)
  tokens      is the person signed in: the server's own token file exists, or the sign-in ShellBot started is
              still waiting / how it ended. The file is never opened.
  login       run the server's own `login` tool: it opens the browser ON THIS COMPUTER at Plaud's page and waits
              (up to two minutes) for the person to allow. It runs on a thread, so the command loop never waits;
              the `tokens` check (and the `login_wait` action, which is the same look) report how it went.
  test        a temporary tab of the primary bot's CLI is asked for one tiny read (_tab_test.py)

The rules this file keeps:

  * It never reads, stores, logs or shows a token. `~/.plaud/tokens-mcp.json` (the server's own file; the
    separate command-line tool keeps another) is only asked about: exists or not.
  * The sign-in is judged by whether the tool reported an error and by a fixed set of codes (ok, timeout,
    denied, port_busy, failed, no_node). What the tool says is classified here and dropped: it may name the
    account.
  * The server is started with PLAUD_TELEMETRY_DISABLED=1 and DO_NOT_TRACK=1 (the package reports who the
    person is to an analytics service unless told not to). The same two are in the description's `mcp.env`.
  * The server is always killed (npx and the node it started) when the sign-in ends, runs out of its cap or is
    cancelled, so the port its sign-in listens on is free for the next try.
  * ShellBot never runs `plaud-mcp install` (it rewrites other programs' settings) and never calls the server's
    `logout` tool (it revokes the person's grant).
"""

import os
import re
import threading

import sf_lang
from connectors.services import _local_mcp
from connectors.services._tab_test import TabTestAdapter

ID = "plaud"
SERVER = "shellbot-plaud"
PACKAGE = "@plaud-ai/mcp@0.3.14"                  # pinned: the version read through (2026-11); plaud.json names the same
NODE_MIN = 20
ENV = {"PLAUD_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1"}
TOKENS = (".plaud", "tokens-mcp.json")            # under the home folder: the server's own file

LOGIN_CAP_S = 150.0                               # the sign-in's hard cap (the tool itself waits two minutes); a test makes it short
SPAWN = None                                      # a test puts a stand-in for the server's starter here
FAILURES = ("timeout", "denied", "port_busy", "failed", "no_node")

MARK = "PLAUD-TEST"
PREFIX = f"mcp__{SERVER}__"
PREFIXES = (PREFIX, PREFIX.replace("-", "_"))     # Codex may write the server's `-` as `_`
TEST_CLIS = ("claude", "codex")
AUTH_TOOLS = tuple(pre + name for pre in PREFIXES for name in ("login", "logout"))    # the sign-in tools: not a read, and never wanted in a test
FAILED_STEP = {"no_tools": "p1", "failed": "p2", "needs_auth": "p2"}      # the step a verdict sends the person back to


def _home(env) -> str:
    env = env or {}
    return env.get("HOME") or env.get("USERPROFILE") or os.path.expanduser("~")


def tokens_path(env=None) -> str:
    return os.path.join(_home(env), *TOKENS)


def server_command(win: bool = False) -> list:
    """What starts the server over stdio (the description's route.mcp, without the program's own path)."""
    return ["cmd", "/c", "npx", "-y", PACKAGE] if win else ["npx", "-y", PACKAGE]


def classify(res: dict) -> str:
    """One code for how `call_tool` ended. Only the tool's isError flag and a few keywords of its message are
    looked at; the message itself is dropped here. '' = stopped on purpose (nothing to report)."""
    code = (res or {}).get("code")
    if code == "cancelled":
        return ""
    if code == "timeout":
        return "timeout"
    if code != "answered":
        return "failed"
    if not res.get("is_error"):
        return "ok"
    t = str(res.get("text") or "").lower()
    if re.search(r"callback server|eaddrinuse|already in use", t):
        return "port_busy"
    if re.search(r"timed? ?out|timeout", t):
        return "timeout"
    if re.search(r"denied|declined|cancel|rejected", t):
        return "denied"
    return "failed"


class _Login:
    """The sign-in ShellBot started: at most one at a time, on a thread; kept in this process only."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"            # idle | running | done
        self.code = ""                 # how the last one ended (a code), "" before any
        self.cancel = threading.Event()
        self.thread = None

    def start(self, argv, env) -> bool:
        """False when one is already running (it is then left alone: a second server would take the port itself)."""
        with self.lock:
            if self.state == "running":
                return False
            self.state, self.code = "running", ""
            cancel = self.cancel = threading.Event()
            cap, spawn = float(LOGIN_CAP_S), SPAWN
        t = self.thread = threading.Thread(target=self._run, args=(argv, env, cancel, cap, spawn), daemon=True, name="plaud-login")
        t.start()
        return True

    def _run(self, argv, env, cancel, cap, spawn) -> None:
        try:
            code = classify(_local_mcp.call_tool(argv, env, "login", cap, cancel, spawn))
        except Exception:
            code = "failed"
        with self.lock:
            if self.cancel is cancel:
                self.state, self.code = ("done", code) if code else ("idle", "")

    def stop(self) -> None:
        with self.lock:
            self.cancel.set()
            t = self.thread
        if t is not None and t.is_alive():
            t.join(8)

    def look(self) -> tuple:
        with self.lock:
            return self.state, self.code


LOGIN = _Login()


# ── checks and actions: fn(ctx, args) -> {"state": <code>} ────────────────────
def check_node(ctx, args: dict) -> dict:
    return _local_mcp.node_check(ctx, NODE_MIN)


def _status(ctx) -> str:
    """waiting (the sign-in is running) | how the one ShellBot ran ended, when it failed | ok | missing (the file)."""
    state, code = LOGIN.look()
    if state == "running":
        return "waiting"
    if code in FAILURES:
        return code
    return "ok" if os.path.exists(tokens_path(ctx.env)) else "missing"


def check_tokens(ctx, args: dict) -> dict:
    """Has the sign-in finished. The file exists or not; it is never opened."""
    return {"state": _status(ctx)}


def action_login(ctx, args: dict) -> dict:
    """Start the server's sign-in on a thread and answer at once: "waiting" (the browser opens on this
    computer; also when one is already running), or "no_node"."""
    node = ctx.find("node") if ctx.find else None
    if not node:
        return {"state": "no_node"}
    env = dict(ctx.env or {})
    env.update(ENV)
    here = os.path.dirname(str(node))
    if here:
        env["PATH"] = here + os.pathsep + env.get("PATH", "")        # npx is a script that finds node on the PATH
    argv = server_command(bool(ctx.win))
    if not ctx.win:
        argv[0] = (ctx.find("npx") if ctx.find else None) or "npx"
    LOGIN.start(argv, env)
    return {"state": "waiting"}


def action_login_wait(ctx, args: dict) -> dict:
    """How the sign-in is going: the same look as the `tokens` check."""
    return {"state": _status(ctx)}


# ── the connection test ───────────────────────────────────────────────────────
class PlaudAdapter(TabTestAdapter):
    """The test asks a temporary tab of the primary bot's CLI to list one recording, and never for what is in it."""

    def __init__(self):
        super().__init__(ID, PREFIX, MARK, ("Plaud", "Plaud"), "local", FAILED_STEP, auth_tools=AUTH_TOOLS, prefixes=PREFIXES, clis=TEST_CLIS)
        self.checks = {"node": check_node, "tokens": check_tokens}
        self.actions = {"login": action_login, "login_wait": action_login_wait}

    def pick_cli(self, ctx) -> str:
        """The CLI the primary bot runs on (where cli_add put the server) when its transcript can be read; else Claude."""
        from connectors import clis as clis_mod
        cli = clis_mod.preferred_cli(getattr(ctx, "cfg", None))
        return cli if cli in TEST_CLIS else "claude"

    def prompt(self, cli: str = "claude") -> str:
        """One line, in the OS language. Claude Code loads MCP tools on demand (ToolSearch); Codex has them from the
        start, so its sentence names the server. It asks for the list of one item and forbids repeating what is in
        it, the sign-in tools and every change."""
        if cli == "codex":
            en_load, zh_load = f"Use the Plaud tools of the {SERVER} MCP server to", f"用 {SERVER} 這個 MCP 伺服器的 Plaud 工具，"
        else:
            en_load, zh_load = "First load the Plaud tools with ToolSearch, then", "先用 ToolSearch 載入 Plaud 的工具，"
        en = (f"This is a Plaud connection test. {en_load} call list_files once, asking for at most 1 item, and count what comes back. "
              "Do not open any recording, note or transcript, do not call login or logout, and do not repeat any title, name or "
              f"content in your reply. Report only the number, in one line. Then write a last line on its own: {MARK}: OK, "
              f"or {MARK}: FAIL followed by the reason.")
        zh = (f"這是 Plaud 連結測試。{zh_load}只呼叫一次 list_files，最多要 1 筆，數一下回來幾筆。不要打開任何錄音、筆記或逐字稿，"
              "不要呼叫 login 或 logout，回覆裡也不要重複任何標題、名稱或內容。用一行只回報數字。"
              f"最後單獨一行寫 {MARK}: OK，失敗就寫 {MARK}: FAIL 加原因。")
        return sf_lang.pick(en, zh)

    def health(self, ctx, route) -> dict:
        """The sign-in is the server's own file: the server deletes it when Plaud takes the permission back. Exists
        or not; never opened."""
        return {"state": "ok" if os.path.exists(tokens_path(ctx.env)) else "revoked"}

    def disconnect(self, ctx, route) -> dict:
        """The core removes the CLI entries it added. This stops a sign-in still waiting (and frees its port) and
        closes a test tab left open. The login file, and the permission at Plaud, are the person's and stay."""
        LOGIN.stop()
        return super().disconnect(ctx, route)


ADAPTER = PlaudAdapter()
