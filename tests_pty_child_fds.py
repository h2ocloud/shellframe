#!/usr/bin/env python3
"""A tab's process keeps its terminal and nothing else of ShellBot's (main.close_inherited_fds).

Measured 2026-11-14: a restart reattached 20 tabs while the output loop ran a tmux call. Each
`tmux attach` was forked with every descriptor ShellBot had without close-on-exec, and macOS
has no pipe2, so a pipe subprocess is creating is inheritable for a moment. One `tmux attach`
took subprocess's exec-error pipe with it, and Popen waited on that pipe until the tab closed:
21 hours, past its 1 s timeout, the terminal frozen and Telegram without output.
  - before: an inheritable pipe open while a tab is forked stays open in it; its reader waits
    for the tab to end
  - after: the tab closes it before exec; the reader sees the end at once
  - both places that fork a tab do it, just before execve

Run: .venv/bin/python tests_pty_child_fds.py
"""
import ast
import os
import sys

if os.name == "nt":
    print("SKIP  no pty.fork on Windows (its tabs run under ConPTY / winpty)")
    sys.exit(0)

import pty  # noqa: E402
import select  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-pty-fds-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")
import main  # noqa: E402

passed = failed = 0
HOLD = 2.0           # the tab's process lives this long (a real `tmux attach` lives for hours)


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for_end_of_pipe(close_in_child: bool) -> float:
    """Seconds until the reader of a pipe sees its end, when a tab was forked while the pipe
    could still be inherited and the parent then closed its own write end."""
    r, w = os.pipe()
    os.set_inheritable(w, True)                 # the moment before it is marked close-on-exec
    pid, fd = pty.fork()
    if pid == 0:
        if close_in_child:
            main.close_inherited_fds()
        os.execv("/bin/sleep", ["sleep", str(HOLD)])
    os.close(w)
    t0 = time.time()
    ready, _, _ = select.select([r], [], [], HOLD + 3)
    ended = bool(ready) and os.read(r, 1) == b""
    took = time.time() - t0
    os.waitpid(pid, 0)
    os.close(fd)
    os.close(r)
    return took if ended else float("inf")


print("a pipe that could be inherited when a tab was forked")
before = wait_for_end_of_pipe(close_in_child=False)
check(f"before: its reader waits for the tab to end (~{HOLD:.0f} s)", before >= HOLD - 0.3, f"{before:.2f}s")
after = wait_for_end_of_pipe(close_in_child=True)
check("after: the tab closed it, the reader sees the end at once", after < 0.5, f"{after:.2f}s")
check("the range closed is sane", 256 <= main._FD_CEILING <= 65536, main._FD_CEILING)

print("both places that fork a tab")
tree = ast.parse((HERE / "main.py").read_text(encoding="utf-8"))
session = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Session")
for fn_name in ("_start_tmux", "_start_unix"):
    fn = next(n for n in session.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    calls = [(c.lineno, ast.unparse(c.func)) for c in ast.walk(fn) if isinstance(c, ast.Call)]
    fork = [ln for ln, name in calls if name == "pty.fork"]
    closes = [ln for ln, name in calls if name == "close_inherited_fds"]
    execs = [ln for ln, name in calls if name == "os.execve"]
    check(f"{fn_name}: closes them after pty.fork, before every execve",
          fork and closes and all(fork[0] < closes[0] < ln for ln in execs), (fork, closes, execs))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
