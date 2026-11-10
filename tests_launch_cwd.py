#!/usr/bin/env python3
"""A tab whose command starts with `cd <dir> && claude …` is read from the right
transcript folder.

Measured after a restart: tabs opened as `cd ~/work/x && claude --model haiku`
had no transcript (no hook yet to say where it was), so their state was a screen
guess and the sidebar could not say when they last answered. Claude Code names
the transcript folder after the directory it starts in, which is the `cd`
target, while the lookup used the tab's own directory.

  - launch_cwd reads `cd <dir> &&` / `cd <dir>;`, quoted or relative
  - no cd, or a directory that is gone: '' (the tab's own directory is used)
  - the tab's resolve context carries the cd target, so the transcript is found

Run: .venv/bin/python tests_launch_cwd.py
"""
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules["webview"] = MagicMock()
sys.modules["bridge_telegram"] = MagicMock()
sys.path.insert(0, str(HERE))

import agent_status as a  # noqa: E402
from launch_cmd import launch_cwd  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


TMP = Path(tempfile.mkdtemp(prefix="sf-launch-cwd-"))
work = TMP / "work dir" / "site check"
work.mkdir(parents=True)

print("launch_cwd")
check("cd <dir> && …", launch_cwd(f"cd {TMP} && claude --model haiku") == str(TMP))
check("quoted, with spaces, and ;", launch_cwd(f"cd '{work}' ; claude") == str(work))
check("relative to the tab's directory", launch_cwd("cd 'work dir' && claude", str(TMP)) == str(TMP / "work dir"))
check("~ is expanded", launch_cwd("cd ~ && claude") == os.path.expanduser("~"))
check("no cd: ''", launch_cwd("claude --resume 1234") == "")
check("a cd that is not at the start: ''", launch_cwd("claude; cd /tmp") == "")
check("a directory that is gone: ''", launch_cwd("cd /no/such/dir/anywhere && claude") == "")

print("the transcript is found")
uuid = "0b9c6a52-2a1f-4d55-9a57-6a1f0c1d2e3f"
cfg = TMP / "profile"
folder = cfg / "projects" / a._cwd_slug(str(work))
folder.mkdir(parents=True)
(folder / f"{uuid}.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")


class Tab:
    def __init__(self, cmd):
        self.cmd = cmd
        self.cwd = "~"
        self.session_id = uuid
        self.alive = True
        self.lock = threading.Lock()
        self._tmux_name = None


api = Api.__new__(Api)
api._live_config_dir = lambda s, provider: str(cfg)
tab = Tab(f"cd '{work}' && claude --model haiku")
ctx = api._worker_ctx("s1", tab)
check("the resolve context carries the cd target", ctx["cwd"] == str(work), ctx["cwd"])
check("…so the transcript in that folder is found",
      a.resolve_transcript(ctx) == str(folder / f"{uuid}.jsonl"), a.resolve_transcript(ctx))
check("without a session uuid, the tab's own directory would not have found it (the bug)",
      a.resolve_transcript(dict(ctx, cwd="~", session_id=None, cmd="claude --model haiku")) is None)
check("with its uuid it is found from any directory (a path over 200 characters, a Windows drive)",
      a.resolve_transcript(dict(ctx, cwd="~")) == str(folder / f"{uuid}.jsonl"), a.resolve_transcript(dict(ctx, cwd="~")))
check("the folder name follows Claude Code: every character but A-Z a-z 0-9 becomes -",
      a._cwd_slug(str(work)).endswith("-work-dir-site-check")
      and all(c.isascii() and (c.isalnum() or c == "-") for c in a._cwd_slug(str(work))), a._cwd_slug(str(work)))
snap = TMP / "starts-with-snapshot.jsonl"     # long-running sessions of older Claude Code versions
snap.write_text('{"type":"file-history-snapshot","messageId":"m1","snapshot":{},"isSnapshotUpdate":false}\n'
                '{"type":"user","sessionId":"x","message":{"role":"user","content":"hello"}}\n', encoding="utf-8")
fmt, evs, err = a._read_tail_events(str(snap))
check("a transcript whose first line is a file-history snapshot is read as Claude Code's",
      fmt == "claude" and not err and any(e.get("kind") == "user_msg" for e in evs), (fmt, err, evs))
plain = api._worker_ctx("s2", Tab("claude --model haiku"))
check("a command without cd keeps the tab's own directory", plain["cwd"] == "~", plain["cwd"])

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
