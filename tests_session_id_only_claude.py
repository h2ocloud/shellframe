#!/usr/bin/env python3
"""`--session-id <uuid>` is added to a new tab only when the command really starts Claude Code.

Measured: a pi tab whose command held `claude` in a path (`cd ~/.claude/notes && pi`, a session file
under `.claude/`) was given `--session-id`, which pi does not know; it ended at once. The check is now
the program's own name (launch_cmd.launches_claude), not a substring.

  - negatives: pi/codex with `claude` in a folder, a session path or an argument; `bash -c "echo claude"`
  - positives: claude, a path in front of it, claude.cmd, a Windows path, sf-claude-home launchers,
    `cd … &&`, `env A=B`, `VAR=1`, npx claude, and `zsh -lc '…claude…'`
  - a command that already pins or resumes a conversation is left alone
  - a wrapped command (`zsh -lc 'claude …'`) gets the id INSIDE the quotes (after it, the shell took it as
    its own $0/$1 and Claude ran without it), is restored with --resume inside the quotes too, and a
    script too complex to rewrite (pipe, `&&` after claude, `;`, `&`, redirect, comment, nested shell,
    `$`/backslash in double quotes) gets no id at all
  - Session itself: a pi tab keeps its command and has no session id; a claude tab gets one

Run: .venv/bin/python tests_session_id_only_claude.py
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.modules["webview"] = MagicMock()
sys.modules.setdefault("bridge_telegram", MagicMock())
sys.path.insert(0, str(Path(__file__).resolve().parent))

import launch_cmd  # noqa: E402
import main  # noqa: E402

passed = failed = 0
UUID = re.compile(r"--session-id [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


NO = ["cd /x/claude-notes && pi", "pi --session ~/.claude/demo/a.jsonl", "/opt/claude-tools/bin/pi",
      "cd ~/.claude/projects/demo && pi --model x", "codex", "sf-codex --model claude-x", "bash",
      'bash -c "echo claude"', "zsh -lc 'pi --foo'", "opencode --dir /home/demo/claude", "cursor-agent --model claude-4"]
YES = ["claude", "claude --permission-mode bypassPermissions --dangerously-skip-permissions", "/usr/local/bin/claude --x",
       "claude.cmd --x", "C:\\Users\\demo\\AppData\\Roaming\\npm\\claude.cmd --x", "sf-claude-home --x",
       "cd ~/work/x && claude --model haiku 'hi'", "env A=B claude", "FOO=1 claude", "npx claude",
       ]
WRAPPED_YES = ["zsh -lc 'claude --x'", 'bash -lc "cd /tmp && claude"', "zsh -lc 'sf-claude-work'"]
print("what is Claude")
for c in NO:
    out, sid = main._maybe_claude_session_id(c)
    check(f"not claude: {c}", out == c and sid is None and not launch_cmd.launches_claude(c), (out, sid))
for c in YES:
    out, sid = main._maybe_claude_session_id(c)
    check(f"claude: {c}", sid and out == f"{c} --session-id {sid}" and UUID.search(out), (out, sid))

print("wrapped in a shell: the id goes inside the quotes")
U0 = "939069ef-4f9e-4ebe-8c04-e2ea62feabcc"
IN = re.compile(r"^(?P<head>.*? )(?P<q>['\"])(?P<inner>.*) --session-id (?P<id>[0-9a-f-]{36})(?P=q)$", re.S)
for c in WRAPPED_YES + ["zsh -lc 'claude --model haiku'", 'zsh -lc "claude --model haiku"', "bash -c 'claude'", "sh -ic 'claude'",
                        "zsh -lc 'claude --model haiku \"hi there\"'", "/bin/zsh -lc 'claude --x'", "zsh -l -c 'claude'",
                        "cd /tmp && zsh -lc 'claude --model haiku'", "env A=B zsh -lc 'cd /w && claude --x'",
                        "zsh -lc 'exec claude'", "zsh -lc 'npx claude --x'"]:
    out, sid = main._maybe_claude_session_id(c)
    m = IN.match(out)
    check(f"id inside: {c}", sid and m and m.group("id") == sid and m.group("head") + m.group("q") + m.group("inner") + m.group("q") == c
          and out.count("--session-id") == 1, (out, sid))
out, sid = main._maybe_claude_session_id("zsh -lc 'claude --model haiku'")
check("the exact reported form", out == f"zsh -lc 'claude --model haiku --session-id {sid}'", out)
out, sid = main._maybe_claude_session_id("bash -lc 'C:\\Users\\demo\\npm\\claude.cmd --x'")
check("a Windows path inside keeps its backslashes", sid and out == f"bash -lc 'C:\\Users\\demo\\npm\\claude.cmd --x --session-id {sid}'", out)
NOID = ["zsh -lc 'claude | tee /tmp/x'", "zsh -lc 'claude && echo done'", "zsh -lc 'claude; echo done'", "zsh -lc 'claude;ls'",
        "zsh -lc 'echo a; claude'", "zsh -lc 'echo a && claude | cat'", "zsh -lc 'claude &'", "zsh -lc 'claude 2>/dev/null'",
        "zsh -lc 'claude --x # note'", "zsh -lc 'claude -- hi'", "zsh -lc '(claude)'", "zsh -lc 'claude $(pwd)'",
        "zsh -lc 'claude `pwd`'", "zsh -lc 'claude\nls'", 'bash -lc "claude $X"', 'bash -lc "claude \\"hi\\""', 'bash -lc "claude `pwd`"',
        "zsh -lc 'claude' extra", "bash -lc \"zsh -lc 'claude'\"", "bash -lc 'bash -lc claude'", "zsh -lc 'claude --resume x --foo'",
        "zsh -lc 'claude --continue'", "zsh -lc 'claude -c'", "zsh -lc 'claude -r'", "zsh -lc 'claude --session-id " + U0 + "'"]
for c in NOID:
    check(f"too complex or pinned, no id: {c!r}", main._maybe_claude_session_id(c) == (c, None), main._maybe_claude_session_id(c))
quoted_ok = main._maybe_claude_session_id("zsh -lc 'claude \"a && b\"'")
check("a && inside a quoted prompt is not shell syntax", quoted_ok[1] and quoted_ok[0].endswith(f' --session-id {quoted_ok[1]}\'') , quoted_ok)

print("restore (Api._cmd_with_resume)")
R = main.Api._cmd_with_resume
cs = "11111111-2222-3333-4444-555555555555"
for c, want in [
    ("zsh -lc 'claude --model haiku'", f"zsh -lc 'claude --resume {cs} --model haiku'"),
    ("zsh -lc 'claude'", f"zsh -lc 'claude --resume {cs}'"),
    ('bash -c "claude --x"', f"bash -c 'claude --resume {cs} --x'"),
    ("cd /w && zsh -lc 'claude --x'", f"cd /w && zsh -lc 'claude --resume {cs} --x'"),
    ("zsh -lc 'cd /w && claude --x'", f"zsh -lc 'cd /w && claude --resume {cs} --x'"),
    (f"zsh -lc 'claude --model haiku --session-id {U0}'", f"zsh -lc 'claude --resume {cs} --model haiku'"),
    (f"zsh -lc 'claude --resume {U0} --x'", f"zsh -lc 'claude --resume {cs} --x'"),
    ("bash -lc \"claude --append-system-prompt 'it is fine' --x\"", None),
    ("zsh -lc 'claude | tee x'", "zsh -lc 'claude | tee x'"), ("zsh -lc 'claude && echo'", "zsh -lc 'claude && echo'"),
    ("zsh -lc 'pi --x'", "zsh -lc 'pi --x'"), ("zsh -lc 'codex'", "zsh -lc 'codex'"), ('bash -lc "claude $X"', 'bash -lc "claude $X"'),
]:
    got = R(c, cs)
    if want is None:                       # a quote inside the inner script: still one valid shell word, and --resume in it
        import shlex as _sh
        parts = _sh.split(got)
        check(f"restore keeps a quote in the script valid: {c}", parts[:2] == ["bash", "-lc"] and f"--resume {cs}" in parts[2], got)
    else:
        check(f"restore: {c}", got == want, got)
rt, sid = main._maybe_claude_session_id("zsh -lc 'claude --model haiku'")
check("round trip: new tab, then restore, then the id in the manifest is the one inside the quotes",
      R(rt, sid) == f"zsh -lc 'claude --resume {sid} --model haiku'", R(rt, sid))
real_win = main.IS_WIN
main.IS_WIN = True
try:
    got = R("bash -lc 'C:\\Users\\demo\\npm\\claude.cmd --x'", cs)
    check("restore on Windows: the program path survives (no eaten backslashes)", got.startswith("bash -lc ") and "Users" in got and "demonpm" not in got and f"--resume {cs}" in got, got)
finally:
    main.IS_WIN = real_win

print("plain forms unchanged")
for c in ["claude --x", "cd ~/w && claude --model haiku 'hi'", "env A=B claude", "FOO=1 claude --x", "npx claude"]:
    out, sid = main._maybe_claude_session_id(c)
    check(f"plain: {c}", sid and out == f"{c} --session-id {sid}", out)
check("plain restore", R("cd /w && claude --model haiku --session-id " + U0, cs) == f"cd /w && claude --resume {cs} --model haiku")

print("already pinned or resumed")
for c in ["claude --resume 939069ef-4f9e-4ebe-8c04-e2ea62feabcc", "claude --continue", "claude -r x", "claude --session-id 939069ef-4f9e-4ebe-8c04-e2ea62feabcc"]:
    check(f"left alone: {c}", main._maybe_claude_session_id(c) == (c, None))

print("Session")
real_start = main.Session._start
main.Session._start = lambda self, cols, rows: None
try:
    pi = main.Session("s1", "cd ~/.claude/notes && pi", 80, 24)
    check("a pi tab under .claude/ keeps its command and has no session id", pi.cmd == "cd ~/.claude/notes && pi" and pi.session_id is None, (pi.cmd, pi.session_id))
    cl = main.Session("s2", "claude --model x", 80, 24)
    check("a claude tab gets one", cl.session_id and cl.cmd.endswith(cl.session_id), (cl.cmd, cl.session_id))
finally:
    main.Session._start = real_start

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
