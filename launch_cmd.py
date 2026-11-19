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
import shlex

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


_SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh"}


def _exe_name(token: str) -> str:
    """The program's own name: no folders (either slash), no .cmd/.bat/.exe, lower case."""
    name = (token or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith((".cmd", ".bat", ".exe")) else name


def launches_claude(cmd: str, _depth: int = 0) -> bool:
    """Does this command start Claude Code, as its program (not as a word somewhere in a path or an
    argument)? `claude`, `claude.cmd`, a folder in front of it, `sf-claude-home` style launchers, behind
    `cd … &&` / `env …` / `VAR=… `, `npx claude`, and `zsh -lc '…claude…'`. A `pi` tab whose folder or
    session file is under `.claude/` is not: it was given `--session-id`, which pi does not know, and
    ended at once."""
    if _depth > 3:
        return False
    _, rest = split_prefix(cmd or "")
    try:
        tokens = shlex.split(rest.replace("\\", "/"))
    except ValueError:
        return False
    if not tokens:
        return False
    exe = _exe_name(tokens[0])
    if exe == "claude":
        return True
    if exe.startswith("sf-") and "claude" in exe.split("-")[1:]:
        return True
    if exe in ("npx", "bunx", "pnpx") and len(tokens) > 1:
        return _exe_name(tokens[1]) in ("claude", "claude-code") or tokens[1].endswith("/claude-code")
    if exe in _SHELLS:
        for i, t in enumerate(tokens[1:], 1):
            if t.startswith("-") and not t.startswith("--") and "c" in t[1:] and i + 1 < len(tokens):
                return launches_claude(tokens[i + 1], _depth + 1)       # sh -c '<script>': the script's program
    return False


# `<shell> [-opts] -c|-lc|-ic '<inner>'`, the inner script quoted with ' or " and nothing after it.
_WRAP = re.compile(
    r"""^(?P<head>\s*(?:\S*[/\\])?(?:sh|bash|zsh|dash|ksh)(?:\.exe)?(?:\s+-\S+)*?\s+-[A-Za-z]*c[A-Za-z]*\s+)"""
    r"""(?:'(?P<single>[^']*)'|"(?P<double>(?:[^"\\]|\\.)*)")\s*$""", re.S)


def shell_wrapper(cmd: str):
    """(head, quote, inner) for `zsh -lc '<inner>'` (also sh/bash/dash/ksh, -c/-ic, a folder in front of
    the shell, either quote), else None. `head` is everything before the opening quote, verbatim. An
    inner string in double quotes that holds a backslash, `$` or a backtick is not offered: what the
    outer shell makes of it is not what is written, so nothing may be rewritten textually."""
    m = _WRAP.match(cmd or "")
    if not m:
        return None
    if m.group("single") is not None:
        return m.group("head"), "'", m.group("single")
    inner = m.group("double")
    if any(c in inner for c in "\\$`"):
        return None
    return m.group("head"), '"', inner


def runs_in_shell(cmd: str) -> bool:
    """Is the program a shell (`zsh -lc '…'`), whose script is what actually starts the CLI?"""
    try:
        tokens = shlex.split((cmd or "").replace("\\", "/"))
    except ValueError:
        return False
    return bool(tokens) and _exe_name(tokens[0]) in _SHELLS


def _claude_tail(inner: str) -> bool:
    """Is `inner` (the script of a wrapped shell) safe to extend or rewrite by its end: one simple command
    after an optional `cd … &&` / `env …` prefix, the program Claude Code itself (not another shell around
    it), and nothing after it that would make "the end" mean something else (a pipe, `&&`, `;`, `&`,
    a redirect, a subshell, a comment, a newline, `--`)?"""
    if "\n" in inner or "\r" in inner or "`" in inner:
        return False
    _, rest = split_prefix(inner)
    if runs_in_shell(rest) or not launches_claude(rest):
        return False
    try:
        lex = shlex.shlex(rest.replace("\\", "/"), posix=True, punctuation_chars=True)
        lex.whitespace_split, lex.commenters = True, ""
        tokens = list(lex)
    except ValueError:
        return False
    return bool(tokens) and not any(
        t == "--" or t.startswith("#") or all(c in "();<>|&" for c in t) for t in tokens)


def claude_tail(cmd: str) -> bool:
    """`_claude_tail` of the inner script of a `<shell> -c '<inner>'` command; False for anything else."""
    w = shell_wrapper(cmd)
    return bool(w) and _claude_tail(w[2])


def wrapped_session_id(cmd: str, new_id: str):
    """`zsh -lc 'claude --model x'` → `zsh -lc 'claude --model x --session-id <id>'`: the id goes inside the
    quotes, after the invocation's own arguments (put after them it became the shell's own $0/$1, Claude
    ran without it, and the tab recorded an id it never had). None when the command is not wrapped, when
    the inner script already pins/resumes/continues a conversation, or when it is not a plain
    `claude …` at its end (see _claude_tail): then no id is added at all."""
    w = shell_wrapper(cmd)
    if not w or not _claude_tail(w[2]):
        return None
    head, q, inner = w
    low = f" {inner.lower()} "
    if "--session-id" in low or "--resume" in low or "--continue" in low or " -r " in low or " -c " in low:
        return None
    return f"{head}{q}{inner.rstrip()} --session-id {new_id}{q}"


def wrapped_resume(cmd: str, resume_inner):
    """The wrapped command with `resume_inner(inner)` (the same rewrite a bare command gets) in place of its
    inner script, re-quoted for the shell. None when `cmd` is not a wrapped plain `claude …`; `cmd` itself
    when the rewrite changes nothing."""
    w = shell_wrapper(cmd)
    if not w or not _claude_tail(w[2]):
        return None
    new = resume_inner(w[2])
    return cmd if not new or new == w[2] else f"{w[0]}{shlex.quote(new)}"


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


_PI_SESSION_FLAGS = {"--session", "--fork", "--session-dir"}    # take a value
_PI_PICK_FLAGS = {"--continue", "-c", "--resume", "-r", "--no-session"}


def pi_session_of(cmd: str, cwd: str = "") -> str:
    """The session file a pi command was started on (`--session <file>`, the last one) as an absolute
    path, when that file exists; "" otherwise (no flag, an id, a file that is gone). A relaunched pi tab
    names the conversation it took back up here (pi_with_session) and writes nothing until the next
    message, so the file it was started on is the one to read."""
    _, rest = split_prefix(cmd or "")
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return ""
    val = ""
    for i, t in enumerate(tokens):
        if t == "--session" and i + 1 < len(tokens):
            val = tokens[i + 1]
        elif t.startswith("--session="):
            val = t.split("=", 1)[1]
    if not val:
        return ""
    path = os.path.expanduser(val)
    if not os.path.isabs(path):
        path = os.path.join(os.path.expanduser(cwd or "~"), path)
    return path if os.path.isfile(path) else ""


def pi_with_session(cmd: str, path: str) -> str:
    """`pi …` (or an sf-pi-… launcher) started again on the session file it was writing: the other
    ways of choosing a session go (--session/--fork <x>, --continue, --resume, --no-session), the
    rest stays as written, and `--session <path>` is added."""
    prefix, rest = split_prefix(cmd or "")
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return cmd
    if not tokens:
        return cmd
    out, i = [tokens[0]], 1
    while i < len(tokens):
        t = tokens[i]
        name = t.split("=", 1)[0]
        if name in _PI_SESSION_FLAGS:
            i += 1 if "=" in t else 2
            continue
        if t in _PI_PICK_FLAGS:
            i += 1
            continue
        out.append(t)
        i += 1
    out += ["--session", path]
    new = shlex.join(out)
    return f"{prefix} {new}" if prefix else new

