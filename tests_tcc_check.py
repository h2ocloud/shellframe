#!/usr/bin/env python3
"""`sfctl permissions --check` says whether tabs can read Documents, Desktop and Downloads, and why not.

Reported: tabs could no longer read ~/Documents, ~/Desktop or ~/Downloads ("Operation not
permitted"), and `--check` only compared tmux's cdhash, so it said nothing useful. Measured with a
copy of the app starting a private tmux server: macOS judges a tab's programs as the app that
started the tmux server; once that app's file is gone and its path too (an update replaced it, the
rename to ShellBot.app moved it), everything is refused, and only a tmux server started by the app
as it is now fixes it.
  - verdict: all readable / denied with a server older than the app (restart the server) /
    denied with a newer server (the app's own permissions) / no server
  - the report gives the steps for each, in plain words, with the tmux path to paste
  - probe() runs in a pane of the server (a private one here, never the tabs' own) and reads a
    failure as a failure: a nested sh once expanded $? early and reported every folder readable
  - sfctl wires it into --check (exit 1 when a tab is refused) and the --tmux text no longer
    says the tabs are judged as tmux

Run: .venv/bin/python tests_tcc_check.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tcc_check  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


ALL_OK = {"Documents": "ok", "Desktop": "ok", "Downloads": "ok"}
DENIED = {"Documents": "denied", "Desktop": "denied", "Downloads": "ok"}

print("verdict")
check("every folder readable: ok", tcc_check.verdict(ALL_OK, 100, 200) == "ok")
check("no tmux server: nothing to check", tcc_check.verdict({}, None, 200) == "no-server")
check("denied, server older than the app: restart the server",
      tcc_check.verdict(DENIED, 100, 200) == "stale-server")
check("denied, server newer than the app: the app's own permissions",
      tcc_check.verdict(DENIED, 300, 200) == "not-granted")
check("denied, times unknown: the app's own permissions (no restart advised blind)",
      tcc_check.verdict(DENIED, None, None) == "not-granted")

print("report")
v, lines = tcc_check.report(DENIED, 100, "/Applications/ShellBot.app", 200)
text = "\n".join(lines)
check("names each folder and says which are refused",
      "文件（~/Documents）：❌" in text and "下載項目（~/Downloads）：✅" in text, text)
check("stale server: quit, kill the server, reopen, in that order, with the path to paste",
      v == "stale-server" and text.index("⌘Q") < text.index("kill-server") < text.index("重新打開 ShellBot")
      and (shutil.which("tmux") or "tmux") + " kill-server" in text, text)
check("…warns that unsent drafts go and which tabs come back to their conversation",
      "還沒送出的字會不見" in text and "Claude Code、Codex、Grok、Cursor" in text, text)
check("…and says granting permissions does not help then", "給權限也沒用" in text, text)
v2, lines2 = tcc_check.report(DENIED, 300, "/Applications/ShellBot.app", 200)
check("not granted: points at Files and Folders, no restart", v2 == "not-granted"
      and "檔案與資料夾" in "\n".join(lines2) and "kill-server" not in "\n".join(lines2))
v3, lines3 = tcc_check.report(ALL_OK, 100, "/Applications/ShellBot.app", 200)
check("all readable: the folder lines only", v3 == "ok" and "原因" not in "\n".join(lines3))

print("probe, in a pane of a private tmux server")
if not shutil.which("tmux"):
    print("  [info] tmux not installed; not run")
else:
    sock = f"sf-test-tcc-{os.getpid()}"
    home = Path(tempfile.mkdtemp(prefix="sf-tcc-home-"))
    (home / "Documents").mkdir()
    (home / "Desktop").mkdir()           # no Downloads: missing
    subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "keep", "sleep 60"], check=True)
    try:
        started = tcc_check.server_started(socket=sock)
        check("the server's start time is read", isinstance(started, float) and started > 0, started)
        r = tcc_check.probe(home=str(home), socket=sock)
        check("readable folders are ok, an absent one missing",
              r == {"Documents": "ok", "Desktop": "ok", "Downloads": "missing"}, r)
        os.chmod(home / "Desktop", 0)     # ls fails ("Permission denied"): not ok, not "denied"
        r = tcc_check.probe(home=str(home), socket=sock)
        check("a folder ls fails on is not reported readable (the $? expanded early bug)",
              r.get("Desktop") == "unknown" and r.get("Documents") == "ok", r)
        left = subprocess.run(["tmux", "-L", sock, "ls"], capture_output=True, text=True).stdout
        check("the probe's session is gone afterwards", "_sf_tcc_probe_" not in left, left)
        check("…and no temp files are left", not [p for p in os.listdir(tempfile.gettempdir())
                                                  if p.startswith("sf-tcc-probe-")])
    finally:
        os.chmod(home / "Desktop", 0o755)
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
        shutil.rmtree(home, ignore_errors=True)

print("wiring")
sf = (HERE / "sfctl.py").read_text(encoding="utf-8")
check("--check runs the folder check first and exits 1 when a tab is refused",
      "tcc_check.report(tcc_check.probe(), tcc_check.server_started(), app, placed)" in sf
      and 'if folders in ("stale-server", "not-granted"):\n        return 1' in sf)
check("--tmux no longer says the tabs are judged as tmux",
      "所有 pane 的 responsible process 都是這個 tmux server 本身" not in sf
      and "sfctl permissions --check" in sf)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
