#!/usr/bin/env python3
"""
sfctl — ShellFrame remote control CLI.
Used by AI agents running inside ShellFrame (a "master session") to
orchestrate other sessions — create workers, dispatch tasks, peek at
results, rename for clarity, etc.

Usage:
    sfctl status
    sfctl list
    sfctl new claude [--label research-1]
    sfctl send s3 "研究這個主題"
    sfctl peek s3 [--lines 50]
    sfctl rename s3 research-done
    sfctl close s3
    sfctl reload | restart
"""

import argparse
import json
import os
import sys
import tempfile
import time

# Cross-platform temp dir — must match main.py + bridge_telegram.py
_TMP = tempfile.gettempdir() if sys.platform == "win32" else "/tmp"
CMD_FILE = os.path.join(_TMP, "shellframe_cmd.json")
RESULT_FILE = os.path.join(_TMP, "shellframe_result.json")


def _rpc(cmd: str, args: dict = None, timeout: float = 15.0):
    """Send cmd to main.py via file IPC. Returns result dict."""
    if os.path.exists(RESULT_FILE):
        try:
            os.unlink(RESULT_FILE)
        except OSError:
            pass
    payload = {"cmd": cmd, "args": args or {}, "ts": time.time()}
    with open(CMD_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
        if os.path.exists(RESULT_FILE):
            try:
                with open(RESULT_FILE, encoding="utf-8") as f:
                    result = json.load(f)
                os.unlink(RESULT_FILE)
                return result
            except (json.JSONDecodeError, IOError):
                continue
    try:
        os.unlink(CMD_FILE)
    except OSError:
        pass
    return {"success": False, "message": "Timeout — ShellFrame not responding"}


def _print_result(result: dict, verbose: bool = True):
    success = result.get("success", False)
    message = result.get("message", "")
    print(f"{'✅' if success else '❌'} {message}")
    if verbose and result.get("details"):
        d = result["details"]
        # Pretty-print text blobs directly, dict keys indented
        if "text" in d and len(d) == 1:
            print(d["text"])
        elif "sessions" in d and isinstance(d["sessions"], list):
            for s in d["sessions"]:
                alive = "●" if s.get("alive") else "○"
                bridge = "" if s.get("bridge_enabled", True) else " (unbridged)"
                print(f"  {alive} {s.get('sid')}  {s.get('label')}{bridge}  — {s.get('cmd', '')[:60]}")
        else:
            for k, v in d.items():
                print(f"  {k}: {v}")
    sys.exit(0 if success else 1)


def main():
    parser = argparse.ArgumentParser(
        prog="sfctl",
        description="ShellFrame remote control — orchestrate sessions from inside a master session.",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("status", help="Show bridge status")
    sub.add_parser("reload", help="Hot-reload bridge_telegram module")
    sub.add_parser("restart", help="Full app restart (sessions preserved)")
    sub.add_parser("list", help="List all sessions with sid + label + alive state")

    p_new = sub.add_parser("new", help="Create a new session")
    p_new.add_argument("command", nargs="?", default="claude",
                       help="Command to run (default: claude)")
    p_new.add_argument("--label", default=None,
                       help="Optional custom label (defaults to command name)")

    p_send = sub.add_parser("send", help="Send text to a session (submits with Enter by default)")
    p_send.add_argument("sid", help="Session id (e.g. s3) — see `sfctl list`")
    p_send.add_argument("text", help="Text to send")
    p_send.add_argument("--no-submit", dest="submit", action="store_false",
                        help="Don't append Enter after text")

    p_peek = sub.add_parser("peek", help="Read the last N lines of a session's pane (deduped)")
    p_peek.add_argument("sid", help="Session id")
    p_peek.add_argument("--lines", type=int, default=50,
                        help="Max lines to return (default: 50)")

    p_rename = sub.add_parser("rename", help="Change a session's label")
    p_rename.add_argument("sid", help="Session id")
    p_rename.add_argument("name", help="New label")

    p_close = sub.add_parser("close", help="Close a session")
    p_close.add_argument("sid", help="Session id")

    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if args.cmd == "status":
        _print_result(_rpc("status"))
    elif args.cmd == "reload":
        _print_result(_rpc("reload", timeout=20))
    elif args.cmd == "restart":
        _print_result(_rpc("restart", timeout=30))
    elif args.cmd == "list":
        _print_result(_rpc("list"))
    elif args.cmd == "new":
        result = _rpc("new_session", {"cmd": args.command, "cols": 200, "rows": 50}, timeout=20)
        if result.get("success") and args.label:
            sid = result.get("details", {}).get("sid", "")
            if sid:
                _rpc("rename", {"sid": sid, "name": args.label}, timeout=5)
                result["message"] = f"Created {sid} as '{args.label}'"
        _print_result(result)
    elif args.cmd == "send":
        _print_result(_rpc("send", {
            "sid": args.sid, "text": args.text, "submit": args.submit,
        }))
    elif args.cmd == "peek":
        _print_result(_rpc("peek", {"sid": args.sid, "lines": args.lines}))
    elif args.cmd == "rename":
        _print_result(_rpc("rename", {"sid": args.sid, "name": args.name}))
    elif args.cmd == "close":
        _print_result(_rpc("close_session", {"sid": args.sid}))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
