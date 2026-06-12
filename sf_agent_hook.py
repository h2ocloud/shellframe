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
"""
import json
import os
import sys
import tempfile
import time


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
        main()
    except Exception:
        pass
