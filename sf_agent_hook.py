#!/usr/bin/env python3
"""Claude Code hook → ShellFrame agent-state event.

Installed into ~/.claude/settings.json by ShellFrame (Settings → 精準狀態偵測).
Claude Code invokes this on UserPromptSubmit / PreToolUse / Stop / Notification /
StopFailure with the hook JSON on stdin. Sessions spawned by ShellFrame carry
SF_SID in their environment (hooks inherit the CLI's env); anything without it
is not ours — exit immediately so non-ShellFrame Claude sessions pay nothing.

Delivery is the same file IPC sfctl uses, but fire-and-forget: drop the cmd
file and exit without waiting for a result (result_file points at a shared
scratch path nobody reads). Never blocks or fails the agent — the hook is
registered async and any exception here is swallowed.

`--stop-check` is the one synchronous use, a separate Stop entry: when this tab
got a hand-off it has not answered, print Claude Code's block decision once, so
the agent reports (sfctl a2a-result) before it stops. It reads the hand-off file
first and asks ShellFrame only when there is one, so an ordinary stop costs a
file read; anything wrong means no output, i.e. the stop goes ahead.
"""
import json
import os
import sys
import tempfile
import time


# agent_link.handoff_path() with its default STATE_DIR (tests check they agree)
HANDOFF_FILE = os.path.join(os.path.expanduser("~"), ".local", "state", "shellframe", "a2a_handoff.json")


def _open_handoff_for(sid: str) -> bool:
    try:
        with open(HANDOFF_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    return any(isinstance(r, dict) and r.get("to_sid") == sid and r.get("delivered_at")
               and not r.get("answered") and not r.get("nudged") for r in data.values())


def stop_check():
    sid = (os.environ.get("SF_SID") or "").strip()
    if not sid:
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    if data.get("stop_hook_active") or not _open_handoff_for(sid):
        return                       # already continuing because of a stop hook, or nothing open
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sfctl
    res = sfctl._rpc("a2a_stop_check", {"sid": sid}, timeout=3) or {}
    d = res.get("details") or {}
    if d.get("block") and d.get("reason"):
        sys.stdout.write(json.dumps({"decision": "block", "reason": d["reason"]}, ensure_ascii=False))


def main():
    sid = (os.environ.get("SF_SID") or "").strip()
    if not sid:
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    tmp_root = tempfile.gettempdir() if sys.platform == "win32" else "/tmp"
    request_id = f"hook-{os.getpid()}-{int(time.time() * 1000)}"
    payload = {
        "cmd": "agent_event",
        "args": {
            "sid": sid,
            "event": str(data.get("hook_event_name") or ""),
            "notification_type": str(data.get("notification_type") or ""),
            "message": str(data.get("message") or "")[:200],
            "tool_name": str(data.get("tool_name") or ""),
            "session_id": str(data.get("session_id") or ""),
            "transcript_path": str(data.get("transcript_path") or ""),
        },
        "ts": time.time(),
        "request_id": request_id,
        "result_file": os.path.join(tmp_root, "shellframe_result_hook.json"),
    }
    cmd_dir = os.path.join(tmp_root, "shellframe_cmds")
    os.makedirs(cmd_dir, exist_ok=True)
    path = os.path.join(cmd_dir, f"shellframe_cmd_{request_id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


if __name__ == "__main__":
    try:
        stop_check() if "--stop-check" in sys.argv[1:] else main()
    except Exception:
        pass
