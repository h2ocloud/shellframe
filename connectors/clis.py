"""Adding, removing and looking up an MCP server in each AI program, safely and repeatably.

What this module promises:

  * It only ever touches an entry named `shellbot-…` (ShellBot's own). A name that is not is refused
    before any program is started (`ForeignEntry`).
  * Adding is "remove ours, then add ours", every time, so running it twice, or after a settings change,
    gives the same result. It never relies on an "already exists" message.
  * It never starts a real program unless a caller hands it a real runner. The default runner refuses
    while a test runs; tests pass a stand-in that records the argument list.
  * A program's output is looked at for one thing (is the name there) and dropped. It is never logged or
    returned. A command shown to the person goes through `secrets.mask` first.
  * What a program cannot do is said, not hidden: `table()` lists, per AI program, whether ShellBot can
    set the connection up (`auto`), only describe it (`manual`) or not at all (`unsupported`).

The commands below are from each program's own `mcp --help` (looked up 2026-11, run against no real
account). `claude` and `codex` keep the server in their user settings; `agy` and `grok` the same through
their `mcp add`. OpenCode's `mcp add` asks questions, Cursor Agent has no add command, and Pi reads a
settings file: for those ShellBot shows what to enter (`manual_entry`).
"""

import re
import subprocess
from collections import namedtuple

from . import secrets

OWN_PREFIX = "shellbot-"
TIMEOUT_S = 40.0
CLI_IDS = ("claude", "codex", "pi", "opencode", "agy", "grok", "cursor")     # = usage_probe.PROVIDER_SPECS

Result = namedtuple("Result", "rc out")      # out: stdout+stderr, lowercase, for one phrase search, never shown


def _t(en, zh):
    return {"en": en, "zh": zh}


SUPPORT = {
    "claude": {"label": "Claude Code", "mcp": "auto", "connector": True, "where": "~/.claude.json",
               "note": _t("Also takes Claude's own connectors (set up on claude.ai).", "也可以用 Claude 帳號的連接器（在 claude.ai 設定）。")},
    "codex": {"label": "Codex", "mcp": "auto", "connector": False, "where": "~/.codex/config.toml",
              "note": _t("", "")},
    "agy": {"label": "Antigravity", "mcp": "auto", "connector": False, "where": "~/.gemini/config/mcp_config.json",
            "note": _t("Flags go before the server's name.", "旗標要放在名稱前面。")},
    "grok": {"label": "Grok Build", "mcp": "auto", "connector": False, "where": "~/.grok/config.toml",
             "note": _t("Also reads Claude's settings file, so a server added there may show up in it.", "也會讀 Claude 的設定檔，加在那裡的伺服器它可能也看得到。")},
    "opencode": {"label": "OpenCode", "mcp": "manual", "connector": False, "where": "opencode.json",
                 "note": _t("Its `mcp add` asks questions: ShellBot shows what to answer, or put it under \"mcp\" in opencode.json.",
                            "它的 `mcp add` 是一問一答：ShellBot 列出要填的內容，或自己寫進 opencode.json 的 \"mcp\"。")},
    "cursor": {"label": "Cursor Agent", "mcp": "manual", "connector": False, "where": "~/.cursor/mcp.json",
               "note": _t("No add command: write the server into the file, then run `cursor-agent mcp enable <name>`.",
                          "沒有 add 指令：自己把伺服器寫進設定檔，再跑 `cursor-agent mcp enable <名稱>`。")},
    "pi": {"label": "Pi", "mcp": "manual", "connector": False, "where": "~/.pi/agent/mcp.json",
           "note": _t("MCP is built in from Pi 0.99; an older Pi cannot run an MCP server. Check with `pi --version`.",
                      "Pi 從 0.99 版起內建 MCP，更舊的版本跑不了 MCP 伺服器。用 `pi --version` 看版本。")},
}


class ForeignEntry(ValueError):
    """A server name that is not ShellBot's own. Nothing was run."""
    code = "cli_foreign_entry"


def own(name: str) -> str:
    """`name` when it is ShellBot's own entry, else ForeignEntry."""
    n = str(name or "")
    if not re.fullmatch(re.escape(OWN_PREFIX) + r"[a-z0-9][a-z0-9_-]{0,40}", n):
        raise ForeignEntry(n)
    return n


def default_runner(argv, timeout=TIMEOUT_S, env=None) -> Result:
    """Start a program, wait, give back its exit code and output. Refuses under test: a test that reaches
    here would change a real AI program's settings."""
    import sf_config
    if sf_config._running_tests():
        raise RuntimeError("a test must pass its own runner")
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, env=env)
        return Result(r.returncode, ((r.stdout or "") + (r.stderr or "")).lower())
    except subprocess.TimeoutExpired:
        return Result(-2, "")
    except OSError:
        return Result(-1, "")


def default_finder(cli: str):
    import usage_probe
    return usage_probe.provider_binary_path(cli)


def _env_args(cli: str, env: dict) -> list:
    flag = "--env" if cli == "codex" else "-e"
    out = []
    for k, v in env.items():
        out += [flag, f"{k}={v}"]
    return out


def _argv(cli: str, op: str, binary: str, name: str, command=None, env=None) -> list:
    """The argument list for `op` (add | remove | get | list) on one program. `command` is the server's
    program and arguments; `env` its environment (values may be secrets: never log this list unmasked)."""
    env = env or {}
    if cli == "claude":
        return {"add": [binary, "mcp", "add", *_env_args(cli, env), "--transport", "stdio", "--scope", "user", name, "--", *(command or [])],
                "remove": [binary, "mcp", "remove", name, "--scope", "user"],
                "get": [binary, "mcp", "get", name]}[op]
    if cli == "codex":
        return {"add": [binary, "mcp", "add", name, *_env_args(cli, env), "--", *(command or [])],
                "remove": [binary, "mcp", "remove", name],
                "get": [binary, "mcp", "get", name]}[op]
    if cli == "agy":
        return {"add": [binary, "mcp", "add", *_env_args(cli, env), name, "--", *(command or [])],
                "remove": [binary, "mcp", "remove", name],
                "list": [binary, "mcp", "list"]}[op]
    if cli == "grok":
        return {"add": [binary, "mcp", "add", *_env_args(cli, env), "--scope", "user", name, "--", *(command or [])],
                "remove": [binary, "mcp", "remove", name, "--scope", "user"],
                "list": [binary, "mcp", "list"]}[op]
    raise KeyError(cli)


def _lookup_op(cli: str) -> str:
    return "get" if cli in ("claude", "codex") else "list"


def _has_name(out: str, name: str) -> bool:
    return bool(re.search(r"(?<![\w-])" + re.escape(name.lower()) + r"(?![\w-])", out or ""))


def server_command(mcp: dict, win: bool = False) -> list:
    """The program and arguments an AI program starts for the server."""
    if win and mcp.get("win_command"):
        return list(mcp["win_command"])
    return [mcp["command"], *mcp.get("args", [])]


def mode_for(cli: str, kind: str) -> str:
    """How a route of this kind reaches this AI program: connector | auto | manual | unsupported."""
    spec = SUPPORT.get(cli)
    if not spec:
        return "unsupported"
    if kind == "claude_connector":
        return "connector" if spec["connector"] else "unsupported"
    return spec["mcp"]


class Clis:
    """The AI programs on this computer, reached through `runner(argv, timeout, env) -> Result` and
    `finder(cli) -> path | None` (both replaceable), with `env` as the child environment."""

    def __init__(self, runner=None, finder=None, env=None, win: bool = False, timeout: float = TIMEOUT_S):
        self.runner = runner or default_runner
        self.finder = finder or default_finder
        self.env = env
        self.win = win
        self.timeout = timeout

    def installed(self, cli: str) -> bool:
        return bool(self.finder(cli))

    def table(self, kind: str = "local_mcp") -> list:
        """One row per AI program: {"cli", "label", "installed", "mode", "where", "note"}; `mode` is how a
        route of `kind` reaches it. What ShellBot cannot do is listed as it is."""
        return [{"cli": c, "label": SUPPORT[c]["label"], "installed": self.installed(c), "mode": mode_for(c, kind),
                 "where": SUPPORT[c]["where"], "note": SUPPORT[c]["note"]} for c in CLI_IDS]

    def _gate(self, cli: str, server: str, kind: str = "local_mcp"):
        """None when ShellBot can act on this program, else the result that says why."""
        own(server)
        mode = mode_for(cli, kind)
        if mode in ("manual", "unsupported"):
            return {"state": mode, "cli": cli, "server": server}
        if not self.finder(cli):
            return {"state": "missing", "cli": cli, "server": server}
        return None

    def _run(self, argv):
        return self.runner(argv, self.timeout, self.env)

    def remove(self, cli: str, server: str) -> dict:
        """{"state": "removed" | "absent" | "missing" | "manual" | "unsupported" | "failed"}."""
        why = self._gate(cli, server)
        if why:
            return why
        r = self._run(_argv(cli, "remove", self.finder(cli), server))
        if r.rc == 0:
            return {"state": "removed", "cli": cli, "server": server}
        return {"state": "absent" if r.rc > 0 else "failed", "cli": cli, "server": server}

    def add(self, cli: str, mcp: dict, extra_env: dict = None) -> dict:
        """Put ShellBot's server into one program: remove ours, add ours. `extra_env` holds the values that
        come from the key store (a secret lands in that program's own settings: its allowed place).
        {"state": "added" | "replaced" | "missing" | "manual" | "unsupported" | "failed", "shown": <masked command>}"""
        server = mcp["server"]
        why = self._gate(cli, server)
        if why:
            return why
        binary = self.finder(cli)
        env = {**(mcp.get("env") or {}), **(extra_env or {})}
        gone = self._run(_argv(cli, "remove", binary, server))
        argv = _argv(cli, "add", binary, server, server_command(mcp, self.win), env)
        shown = secrets.mask(" ".join(argv), known=(extra_env or {}).values())
        r = self._run(argv)
        if r.rc != 0:
            return {"state": "failed", "cli": cli, "server": server, "shown": shown}
        return {"state": "replaced" if gone.rc == 0 else "added", "cli": cli, "server": server, "shown": shown}

    def check(self, cli: str, server: str) -> dict:
        """{"state": "present" | "absent" | "unknown" | "missing" | "manual" | "unsupported"}. `unknown`
        when the program did not answer in time (some start the server to answer): not the same as absent."""
        why = self._gate(cli, server)
        if why:
            return why
        op = _lookup_op(cli)
        r = self._run(_argv(cli, op, self.finder(cli), server))
        if r.rc == -2:
            return {"state": "unknown", "cli": cli, "server": server}
        present = (r.rc == 0) if op == "get" else (r.rc == 0 and _has_name(r.out, server))
        return {"state": "present" if present else "absent", "cli": cli, "server": server}

    def manual_entry(self, cli: str, mcp: dict) -> dict:
        """What a person enters by hand for a program ShellBot cannot set up: the program, its arguments
        and the environment names. Values that come from the key store are never shown, only their names."""
        secret_names = sorted((mcp.get("env_from_secret") or {}))
        return {"cli": cli, "where": SUPPORT[cli]["where"], "note": SUPPORT[cli]["note"], "server": mcp["server"],
                "command": server_command(mcp, self.win),
                "env": {k: v for k, v in (mcp.get("env") or {}).items()}, "env_secret_names": secret_names}
