#!/usr/bin/env python3
"""
sfctl — ShellFrame remote control CLI.
Used by AI agents running inside ShellFrame to trigger operations
(e.g., hot-reload after modifying bridge code).

Usage:
    python3 ~/.local/apps/shellframe/sfctl.py reload
    python3 ~/.local/apps/shellframe/sfctl.py status
"""

import json
import os
import sys
import tempfile
import time

# Cross-platform temp dir — must match main.py + bridge_telegram.py
# Keep /tmp on Unix for backward compat, %TEMP% on Windows
_TMP = tempfile.gettempdir() if sys.platform == "win32" else "/tmp"
CMD_FILE = os.path.join(_TMP, "shellframe_cmd.json")
RESULT_FILE = os.path.join(_TMP, "shellframe_result.json")

COMMANDS = {
    "reload": "Hot-reload bridge_telegram module (pick up code changes)",
    "status": "Show bridge status (connected, paused, sessions)",
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("sfctl — ShellFrame remote control")
        print("\nCommands:")
        for cmd, desc in COMMANDS.items():
            print(f"  {cmd:10s}  {desc}")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(1)

    # Clean up stale result file
    if os.path.exists(RESULT_FILE):
        os.unlink(RESULT_FILE)

    # Write command
    with open(CMD_FILE, "w") as f:
        json.dump({"cmd": cmd, "ts": time.time()}, f)

    # Wait for result (timeout 15s)
    for _ in range(150):
        time.sleep(0.1)
        if os.path.exists(RESULT_FILE):
            try:
                with open(RESULT_FILE) as f:
                    result = json.load(f)
                os.unlink(RESULT_FILE)
            except (json.JSONDecodeError, IOError):
                continue
            success = result.get("success", False)
            message = result.get("message", "")
            print(f"{'✅' if success else '❌'} {message}")
            if result.get("details"):
                for k, v in result["details"].items():
                    print(f"  {k}: {v}")
            sys.exit(0 if success else 1)

    # Timeout
    # Clean up cmd file
    if os.path.exists(CMD_FILE):
        os.unlink(CMD_FILE)
    print("❌ Timeout — ShellFrame not responding. Is it running?")
    sys.exit(1)


if __name__ == "__main__":
    main()
