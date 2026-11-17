"""What the services that run a local MCP server share. The file starts with `_`, so the registry never takes
it for a service.

  node_check     the version of the node a tab would find (the check a description's "Node.js" step names)
  call_tool      start an MCP server over stdio, call ONE tool of it, and always take the whole process tree down
                 again. For a tool that blocks until the person has done something in a browser (a sign-in).

`call_tool` is the one place in the connector code that starts a program of its own and not through `ctx.run`
(which waits for a short command and cannot be stopped): the tool may wait two minutes for a person, and the
server it started must be gone afterwards, or the port its sign-in listens on stays taken for the next try.
What the tool says is classified by the caller; this file returns it once and keeps nothing.

  * Never under test: with SHELLFRAME_TESTS set the default spawner refuses, so a test that walks a shipped
    description can never start a real server (a test passes `spawn=` with a stand-in).
  * One deadline for everything (`cap_s`), stoppable from outside (`cancel`, a threading.Event).
  * Nothing the server prints on stderr is kept; a line on stdout that is not the JSON-RPC answer is skipped.
"""

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time


def node_check(ctx, minimum: int) -> dict:
    """The version of the node a tab would find. {"state": "ok" | "old" | "missing", "version"}."""
    node = ctx.find("node")
    if not node:
        return {"state": "missing"}
    r = ctx.run([node, "--version"], 10)
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", r.out or "") if r.rc == 0 else None
    if not m:
        return {"state": "missing"}
    return {"state": "ok" if int(m.group(1)) >= minimum else "old", "version": ".".join(m.groups())}


def spawn_default(argv, env):
    """Start the server with pipes of its own, in a process group of its own (so the whole tree can be taken down)."""
    if os.environ.get("SHELLFRAME_TESTS"):
        raise RuntimeError("a test must not start a real server")
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, **kw)


def kill_tree(proc) -> None:
    """Take the process and everything it started down, and reap it. Safe to call twice."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)            # a group of its own (start_new_session): npx and the node it started
            except (ProcessLookupError, PermissionError):
                pass
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    for pipe in (proc.stdin, proc.stdout):
        try:
            if pipe:
                pipe.close()
        except Exception:
            pass


def _lines(stream, out: "queue.Queue") -> None:
    try:
        for raw in iter(stream.readline, b""):
            out.put(raw)
    except Exception:
        pass
    out.put(None)


def call_tool(argv, env, tool: str, cap_s: float, cancel: "threading.Event" = None, spawn=None,
              arguments: dict = None) -> dict:
    """initialize, notifications/initialized, tools/call <tool>; one deadline. Returns one of

      {"code": "answered", "is_error": bool, "text": str}   the tool answered (the caller classifies `text` and drops it)
      {"code": "rpc_error"}                                  the server answered with a JSON-RPC error
      {"code": "timeout"}                                    the cap ran out (or the server never answered)
      {"code": "cancelled"}                                  `cancel` was set
      {"code": "no_start"}                                   the server could not be started
      {"code": "exited"}                                     the server ended before it answered

    and always kills the process tree before returning."""
    try:
        proc = (spawn or spawn_default)(argv, env)
    except Exception:
        return {"code": "no_start"}
    deadline = time.monotonic() + max(0.1, float(cap_s))
    box = queue.Queue()
    threading.Thread(target=_lines, args=(proc.stdout, box), daemon=True, name="mcp-stdio-reader").start()

    def send(obj) -> bool:
        try:
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            proc.stdin.flush()
            return True
        except Exception:
            return False

    def answer(want_id):
        """The JSON-RPC response with this id, or a code when there will be none."""
        while True:
            if cancel is not None and cancel.is_set():
                return {"code": "cancelled"}
            left = deadline - time.monotonic()
            if left <= 0:
                return {"code": "timeout"}
            try:
                raw = box.get(timeout=min(0.25, left))
            except queue.Empty:
                continue
            if raw is None:
                return {"code": "exited"}
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue                                       # not JSON-RPC (a banner, a log line)
            if isinstance(msg, dict) and msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return {"code": "answered", "msg": msg}

    try:
        if not send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "shellbot", "version": "1"}}}):
            return {"code": "exited"}
        got = answer(1)
        if got["code"] != "answered" or "result" not in got["msg"]:
            return {"code": "rpc_error"} if got["code"] == "answered" else {"code": got["code"]}
        if not send({"jsonrpc": "2.0", "method": "notifications/initialized"}):
            return {"code": "exited"}
        if not send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments or {}}}):
            return {"code": "exited"}
        got = answer(2)
        if got["code"] != "answered":
            return {"code": got["code"]}
        res = got["msg"].get("result")
        if not isinstance(res, dict):
            return {"code": "rpc_error"}
        text = " ".join(str(c.get("text") or "") for c in (res.get("content") or []) if isinstance(c, dict))
        return {"code": "answered", "is_error": bool(res.get("isError")), "text": text}
    finally:
        kill_tree(proc)
