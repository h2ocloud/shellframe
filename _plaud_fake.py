"""A fake stdio MCP server standing in for Plaud's, for the connector tests: a small Python script that answers
`initialize` and `tools/call login` in the way `FAKE_MODE` says (ok, timeout, denied, port_busy, failed, never answers,
exits, ...), and a `Spawner` that starts it with the same pipes and process group as the real starter. No network, no
real Plaud, no token. In `ok` mode it writes the token file the real server would, in `$HOME` (a temp folder)."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IDENTITY = "demo-person@example.invalid"           # what the real tool's text could name: must never come back

FAKE = r'''
import json, os, subprocess, sys, time
mode = os.environ.get("FAKE_MODE", "ok")
pids = os.environ.get("FAKE_PIDS", "")
home = os.environ.get("HOME", "")
delay = float(os.environ.get("FAKE_DELAY", "0"))
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
sys.stdout.write("starting up (not json)\n"); sys.stdout.flush()
if mode == "silent":
    time.sleep(600)
for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if m.get("method") == "initialize":
        if mode == "rpc_error":
            out({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -1, "message": "no"}}); continue
        out({"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}})
    elif m.get("method") == "tools/call":
        if mode == "exit":
            sys.exit(3)
        if pids:
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
            open(pids, "w").write(f"{os.getpid()} {child.pid}")
        time.sleep(delay)
        if mode == "never":
            time.sleep(600)
        text, err = {
            "ok": ("Successfully authenticated with Plaud! Logged in as " + os.environ.get("FAKE_WHO", ""), False),
            "timeout": ("Authentication timed out after 2 minutes for " + os.environ.get("FAKE_WHO", ""), True),
            "denied": ("Authorization failed: access_denied by " + os.environ.get("FAKE_WHO", ""), True),
            "port_busy": ("Failed to start callback server: port 8199 is already in use", True),
            "failed": ("Token exchange failed for " + os.environ.get("FAKE_WHO", ""), True),
        }.get(mode, ("???", True))
        if mode == "ok" and home:
            os.makedirs(os.path.join(home, ".plaud"), exist_ok=True)
            open(os.path.join(home, ".plaud", "tokens-mcp.json"), "w").write("{}")
        out({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": text}], "isError": err}})
'''
FAKE_PATH = Path(tempfile.mkdtemp(prefix="sf-plaud-fake-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None)) / "fake_plaud_mcp.py"
FAKE_PATH.write_text(FAKE, encoding="utf-8")


class Spawner:
    """Stands in for the server's starter: runs the fake script (same pipes, same process group as the real one)."""

    def __init__(self, mode="ok", delay=0.0, pids=None):
        self.mode, self.delay, self.pids, self.calls = mode, delay, pids, []

    def __call__(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        e = dict(env, FAKE_MODE=self.mode, FAKE_DELAY=str(self.delay), FAKE_WHO=IDENTITY)
        if self.pids:
            e["FAKE_PIDS"] = str(self.pids)
        return subprocess.Popen([sys.executable, "-I", str(FAKE_PATH)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=e, start_new_session=True)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                              # a zombie is gone for our purposes
        return "Z" not in subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    except Exception:
        return True


def wait_for(fn, limit=15.0, step=0.05):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


