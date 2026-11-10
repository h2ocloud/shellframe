"""What a tab's launch command really runs, and how to start it again.

A tab's command is often more than the CLI: `cd <dir> && claude …`,
`env -u CLAUDE_CONFIG_DIR claude …`, `FOO=1 claude …`. Relaunching (⟳, RPC
relaunch, the respawn after a reboot) must change only the CLI part and keep the
rest exactly as written.

Measured: relaunching `cd ~/work/x && claude --model haiku … '<opening prompt>'
--session-id <uuid>` took `cd` for the program, so the command ran again as it
was: the CLI exited at once (that session id was taken) and the tab died. Tabs
started with `env …` came back on the uuid they were first started with.
"""

import os
import re

# One prefix piece at a time, from the start of the command.
_CD = re.compile(r"""\s*cd\s+("[^"]*"|'[^']*'|[^\s;&|]+)\s*(?:&&|;)\s*""")
_ASSIGN = re.compile(r"""\s*[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|[^\s;&|]*)(?=\s)""")
_EXEC = re.compile(r"""\s*exec(?=\s)""")
_ENV = re.compile(r"""\s*env(?=\s)""")
_ENV_OPT = re.compile(r"""\s+(?:-u\s+\S+|--unset=\S+|-i|--ignore-environment"""
                      r"""|[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\S*))(?=\s)""")


def split_prefix(cmd: str):
    """(prefix, rest): what runs before the program (`cd …&&`, `env …`, `VAR=…`,
    `exec`), kept verbatim, and the program with its arguments."""
    s, pos = cmd or "", 0
    while True:
        m = _CD.match(s, pos) or _ASSIGN.match(s, pos) or _EXEC.match(s, pos)
        if m:
            pos = m.end()
            continue
        m = _ENV.match(s, pos)
        if not m:
            break
        pos = m.end()
        while True:
            o = _ENV_OPT.match(s, pos)
            if not o:
                break
            pos = o.end()
    return s[:pos].rstrip(), s[pos:].lstrip()


def launch_cwd(cmd: str, cwd: str = "~") -> str:
    """The directory the CLI really starts in when its command begins with
    `cd <dir> &&` (or `;`). Claude Code names its transcript folder after that
    directory, not after the tab's own. '' when the command does not change
    directory or the directory is gone."""
    m = _CD.match(cmd or "")
    if not m:
        return ""
    d = os.path.expanduser(m.group(1).strip("\"'"))
    if not os.path.isabs(d):
        d = os.path.join(os.path.expanduser(cwd or "~"), d)
    return d if os.path.isdir(d) else ""


# Claude Code's options (claude --help), by what follows them.
_CLAUDE_ONE = {   # one value: `<x>`, and the optional `[x]` ones, which take a value when one follows
    "--agent", "--agents", "--append-system-prompt", "--append-system-prompt-file", "--autocompact",
    "--debug-file", "--effort", "--environment", "--fallback-model", "--input-format", "--json-schema",
    "--max-budget-usd", "--model", "-n", "--name", "--output-format", "--permission-mode",
    "--permission-prompts", "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix",
    "--setting-sources", "--settings", "--system-prompt", "--system-prompt-file", "--system-prompt-snapshot",
    "--cloud", "-d", "--debug", "--from-pr", "--prompt-suggestions", "--remote-control", "--teleport",
    "-w", "--worktree",
}
_CLAUDE_MANY = {"--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--disallowedTools",
                "--disallowed-tools", "--file", "--mcp-config", "--tools"}
_CLAUDE_SESSION = {"--resume", "-r", "--session-id"}      # replaced by the current session
_CLAUDE_CONTINUE = {"--continue", "-c"}                    # would fight --resume
_CLAUDE_COMMANDS = {"config", "mcp", "doctor", "update", "install", "migrate-installer",
                    "setup-token", "plugin", "plugins", "auth"}


_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_PINNED = re.compile(rf"\s+(?:(?:--session-id|--resume|-r)(?:=|\s+){_UUID}|--continue)(?=\s|$)")


def without_session(cmd: str) -> str:
    """The command without the conversation it was pinned to: `--session-id <uuid>` (the
    one ShellBot adds to every Claude tab), `--resume <uuid>`, `--continue`. A bot kept
    from a tab wakes in a new conversation each time; Claude Code will not start a second
    one under the id of the first. Only uuid values are taken, and `-c` is left alone
    (`bash -c` is a shell's)."""
    return _PINNED.sub("", cmd or "").strip()


def claude_resume(tokens: list, csid: str):
    """`claude …` → [program, --resume <csid>, the options as written], or None for
    a subcommand (`claude mcp …`). The old session id goes, and so does an
    opening prompt: started again inside the same conversation it would be sent
    a second time."""
    if any(t in ("&&", "||", ";", "|", "&") or t[:1] in "<>" for t in tokens[1:]):
        return None                                 # more shell after it: not ours to rewrite
    out = [tokens[0], "--resume", csid]
    i, n = 1, len(tokens)
    while i < n:
        t = tokens[i]
        name = t.split("=", 1)[0]
        if t == "--":
            break                                   # only the prompt follows
        if name in _CLAUDE_SESSION or name in _CLAUDE_CONTINUE:
            i += 1
            if name in _CLAUDE_SESSION and "=" not in t and i < n and not tokens[i].startswith("-"):
                i += 1                              # its value
            continue
        if t.startswith("-"):
            out.append(t)
            i += 1
            if "=" in t:
                continue
            if name in _CLAUDE_MANY:
                while i < n and not tokens[i].startswith("-"):
                    out.append(tokens[i])
                    i += 1
            elif name in _CLAUDE_ONE and i < n and not tokens[i].startswith("-"):
                out.append(tokens[i])
                i += 1
            continue
        if t in _CLAUDE_COMMANDS and len(out) == 3:
            return None                             # `claude mcp …`: not a conversation
        i += 1                                      # a positional: the opening prompt
    return out
