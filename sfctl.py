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
import shutil
import subprocess
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


def _prompt(msg: str, default: str = "") -> str:
    """Read a line from the user; return default if stdin isn't a TTY or EOF."""
    try:
        if not sys.stdin.isatty():
            return default
        return input(msg)
    except EOFError:
        return default


def _cmd_permissions(args):
    """Pre-grant OS-level permissions so CLIs under shellframe stop hitting
    blocking dialogs. macOS walks the Privacy panes + optional ALF whitelist;
    Windows adds Defender Firewall inbound rules for the bundled Python."""
    if sys.platform == "darwin":
        _permissions_macos(args)
    elif sys.platform == "win32":
        _permissions_windows(args)
    else:
        print("Linux / other: no per-app permission panes to configure.")
        print("(Firewall rules — if needed — are handled by your distro.)")
    sys.exit(0)


# ── macOS ───────────────────────────────────────────────────────────────────
_MAC_PANES = [
    ("Files & Folders",  "Privacy_FilesAndFolders",
     "Enable your terminal app (Terminal / iTerm / Ghostty) for Downloads, "
     "Documents, Desktop — stops the 'X would like to access' popups."),
    ("Accessibility",    "Privacy_Accessibility",
     "Enable your terminal if tools under it use AppleScript / key events."),
    ("Automation",       "Privacy_Automation",
     "Review the nested list — the first `tell application \"X\"` caused "
     "it. Leave it checked."),
    ("Screen Recording", "Privacy_ScreenCapture",
     "Enable your terminal if any tool takes screenshots / uses vision."),
    ("Full Disk Access", "Privacy_AllFiles",
     "Optional — enable if workflows touch ~/Library, iCloud, or system "
     "paths."),
]


def _permissions_macos(args):
    do_panes = not args.firewall
    do_fw = not args.panes

    if do_panes:
        print("macOS Privacy panes — opening one by one.")
        print("Drag your terminal app (or click + to add it) in each pane "
              "that applies to you, then return here.\n")
        for name, key, hint in _MAC_PANES:
            url = f"x-apple.systempreferences:com.apple.preference.security?{key}"
            print(f"── {name} ──")
            print(f"   {hint}")
            subprocess.run(["open", url], capture_output=True)
            ans = _prompt("   Press Enter for next pane (or q+Enter to stop): ",
                          default="")
            if ans.strip().lower() == "q":
                break
            print()

    if do_fw:
        sf = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        if not os.path.exists(sf):
            print("ALF binary missing — skipping firewall whitelist.")
            return
        targets = _firewall_targets_macos()
        if not targets:
            print("No firewall targets detected (shellframe venv not found).")
            return
        print("\nFirewall (ALF) whitelist — silences 'accept incoming "
              "connections' popups.")
        print("Will run (needs sudo once):")
        for t in targets:
            print(f"  sudo {sf} --add {t}")
            print(f"  sudo {sf} --unblockapp {t}")
        if args.yes or _prompt("Apply now? [y/N] ", default="n").strip().lower() == "y":
            for t in targets:
                subprocess.run(["sudo", sf, "--add", t])
                subprocess.run(["sudo", sf, "--unblockapp", t])
            print("Firewall whitelist applied.")
        else:
            print("Skipped. Re-run `sfctl permissions --firewall` when ready.")


def _firewall_targets_macos() -> list:
    install = os.path.expanduser("~/.local/apps/shellframe")
    py = os.path.join(install, ".venv/bin/python3")
    targets = []
    if os.path.exists(py):
        try:
            targets.append(os.path.realpath(py))
        except OSError:
            targets.append(py)
    bun = shutil.which("bun")
    if bun:
        try:
            targets.append(os.path.realpath(bun))
        except OSError:
            targets.append(bun)
    # de-dup while preserving order
    seen = set()
    out = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ── Windows ─────────────────────────────────────────────────────────────────
def _permissions_windows(args):
    # Windows has no TCC analogue — only Defender Firewall nags when a
    # listening socket opens. Pre-adding inbound allow rules silences it.
    install = os.path.join(os.environ.get("USERPROFILE", ""),
                           ".local", "apps", "shellframe")
    scripts = os.path.join(install, ".venv", "Scripts")
    candidates = [
        os.path.join(scripts, "python.exe"),
        os.path.join(scripts, "pythonw.exe"),
    ]
    targets = [p for p in candidates if os.path.exists(p)]
    if not targets:
        print(f"No venv Python found under {scripts}. Run install.ps1 first.")
        return

    print("Windows Defender Firewall rules — stops the one-time 'Allow "
          "network access' popup when shellframe starts.")
    print("Will add inbound allow rules for:")
    for t in targets:
        print(f"  {t}")

    if not args.yes:
        ans = _prompt("Apply now? (UAC prompt will appear) [y/N] ", default="n")
        if ans.strip().lower() != "y":
            print("Skipped. Re-run `sfctl permissions` when ready.")
            return

    # Build one elevated PowerShell call that adds all rules. Quoting is
    # escaped for cmd → powershell → netsh.
    cmds = []
    for path in targets:
        name = f"ShellFrame ({os.path.basename(path)})"
        cmds.append(
            f'netsh advfirewall firewall add rule name="{name}" dir=in '
            f'action=allow program="{path}" enable=yes profile=any'
        )
    joined = " & ".join(cmds)
    ps = (
        f"Start-Process cmd -Verb RunAs -Wait -ArgumentList "
        f"'/c {joined}'"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
        print("Firewall rules applied (if UAC accepted).")
    except FileNotFoundError:
        print("PowerShell not found — run the netsh commands manually:")
        for c in cmds:
            print(f"  {c}")


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

    p_perm = sub.add_parser(
        "permissions",
        help="Pre-grant OS permissions (macOS Privacy panes + firewall; "
             "Windows Defender Firewall rules)",
    )
    p_perm.add_argument("--panes", action="store_true",
                        help="macOS only: open Privacy panes, skip firewall")
    p_perm.add_argument("--firewall", action="store_true",
                        help="Firewall whitelist only, skip Privacy panes")
    p_perm.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompts (still needs sudo/UAC)")

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
    elif args.cmd == "permissions":
        _cmd_permissions(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
