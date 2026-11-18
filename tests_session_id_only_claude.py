#!/usr/bin/env python3
"""`--session-id <uuid>` is added to a new tab only when the command really starts Claude Code.

Measured: a pi tab whose command held `claude` in a path (`cd ~/.claude/notes && pi`, a session file
under `.claude/`) was given `--session-id`, which pi does not know; it ended at once. The check is now
the program's own name (launch_cmd.launches_claude), not a substring.

  - negatives: pi/codex with `claude` in a folder, a session path or an argument; `bash -c "echo claude"`
  - positives: claude, a path in front of it, claude.cmd, a Windows path, sf-claude-home launchers,
    `cd … &&`, `env A=B`, `VAR=1`, npx claude, and `zsh -lc '…claude…'`
  - a command that already pins or resumes a conversation is left alone
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
       "zsh -lc 'claude --x'", 'bash -lc "cd /tmp && claude"', "zsh -lc 'sf-claude-work'"]
print("what is Claude")
for c in NO:
    out, sid = main._maybe_claude_session_id(c)
    check(f"not claude: {c}", out == c and sid is None and not launch_cmd.launches_claude(c), (out, sid))
for c in YES:
    out, sid = main._maybe_claude_session_id(c)
    check(f"claude: {c}", sid and out == f"{c} --session-id {sid}" and UUID.search(out), (out, sid))

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
