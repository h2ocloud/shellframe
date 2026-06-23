#!/usr/bin/env python3
"""
sfctl — ShellFrame remote control CLI.
Used by AI agents running inside ShellFrame (a "master session") to
orchestrate other sessions — create workers, dispatch tasks, peek at
results, rename for clarity, etc.

Usage:
    sfctl status
    sfctl list
    sfctl roster
    sfctl delegate 時程信件 "送假單今明兩天居家"
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
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# Cross-platform temp dir — must match main.py + bridge_telegram.py
_TMP = tempfile.gettempdir() if sys.platform == "win32" else "/tmp"
CMD_FILE = os.path.join(_TMP, "shellframe_cmd.json")
CMD_DIR = os.path.join(_TMP, "shellframe_cmds")
RESULT_FILE = os.path.join(_TMP, "shellframe_result.json")
PID_FILE = os.path.join(_TMP, "shellframe.pid")

# Records the tmux binary path + cdhash that was last walked through the
# Full Disk Access grant flow, so we can detect when `brew upgrade tmux`
# swaps the binary (new cdhash → stale TCC grant → prompts come back).
TMUX_TCC_STATE = os.path.expanduser("~/.config/shellframe/tmux_tcc.json")


def _rpc(cmd: str, args: dict = None, timeout: float = 15.0):
    """Send cmd to main.py via file IPC. Returns result dict."""
    request_id = f"{os.getpid()}-{int(time.time() * 1000)}"
    result_file = os.path.join(_TMP, f"shellframe_result_{request_id}.json")
    if os.path.exists(result_file):
        try:
            os.unlink(result_file)
        except OSError:
            pass
    payload = {
        "cmd": cmd,
        "args": args or {},
        "ts": time.time(),
        "request_id": request_id,
        "result_file": result_file,
    }
    os.makedirs(CMD_DIR, exist_ok=True)
    cmd_file = os.path.join(CMD_DIR, f"shellframe_cmd_{request_id}.json")
    tmp_file = cmd_file + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp_file, cmd_file)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
        if os.path.exists(result_file):
            try:
                with open(result_file, encoding="utf-8") as f:
                    result = json.load(f)
                os.unlink(result_file)
                return result
            except (json.JSONDecodeError, IOError):
                continue
    try:
        os.unlink(cmd_file)
    except OSError:
        pass
    try:
        os.unlink(result_file)
    except OSError:
        pass
    return {"success": False, "message": "Timeout — ShellFrame not responding"}


def _print_result(result: dict, verbose: bool = True):
    success = result.get("success", False)
    message = result.get("message", "")
    print(f"{'OK' if success else 'ERR'} {message}")
    if verbose and result.get("details"):
        d = result["details"]
        # Pretty-print text blobs directly, dict keys indented
        if "text" in d and len(d) == 1:
            print(d["text"])
        elif "sessions" in d and isinstance(d["sessions"], list):
            for s in d["sessions"]:
                alive = "*" if s.get("alive") else "-"
                bridge = "" if s.get("bridge_enabled", True) else " (unbridged)"
                print(f"  {alive} {s.get('sid')}  {s.get('label')}{bridge}  - {s.get('cmd', '')[:60]}")
        elif "roles" in d and isinstance(d["roles"], list):
            for r in d["roles"]:
                print(f"  {r.get('role')}  ->  {r.get('label')}  [{r.get('agent_code')}]")
                if r.get("responsibility"):
                    print(f"      {r.get('responsibility')}")
        else:
            for k, v in d.items():
                print(f"  {k}: {v}")
    sys.exit(0 if success else 1)


def _canonical_macos_app_path() -> str:
    candidates = [
        "/Applications/ShellFrame.app",
        os.path.expanduser("~/Applications/ShellFrame.app"),
        os.path.expanduser("~/.local/apps/shellframe/ShellFrame.app"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return ""


def _restart_macos_direct() -> dict:
    """Restart through the canonical .app path without asking the currently
    loaded app process to choose a bundle. This matters during upgrades from
    older builds whose in-memory restart code still preferred ~/Applications."""
    app_path = _canonical_macos_app_path()
    if not app_path:
        return {"success": False, "message": "ShellFrame.app not found"}
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except Exception:
        return {"success": False, "message": "ShellFrame PID file not found"}
    try:
        os.kill(pid, 0)
    except OSError:
        return {"success": False, "message": f"ShellFrame pid {pid} is not running"}

    app_q = shlex.quote(app_path)
    script = (
        f"pid={pid}; app={app_q}; "
        "i=0; "
        "while kill -0 \"$pid\" >/dev/null 2>&1 && [ $i -lt 80 ]; do "
        "  i=$((i+1)); sleep 0.1; "
        "done; "
        "/usr/bin/open -n \"$app\" >/dev/null 2>&1"
    )
    subprocess.Popen(
        ["/bin/sh", "-c", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os.kill(pid, signal.SIGTERM)
    return {
        "success": True,
        "message": f"Restarting ShellFrame via {app_path}",
    }


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


def _tmux_paths() -> tuple:
    """Return (symlink_path, real_path) for tmux. symlink is the stable
    /opt/homebrew/bin/tmux the user should add to FDA; real is the resolved
    Cellar binary (its cdhash is what TCC actually keys on)."""
    sym = shutil.which("tmux")
    if not sym:
        return (None, None)
    try:
        real = os.path.realpath(sym)
    except OSError:
        real = sym
    return (sym, real)


def _tmux_cdhash(path: str) -> str:
    """Read the adhoc cdhash of the tmux binary via codesign. '' on failure."""
    if not path or not os.path.exists(path):
        return ""
    try:
        r = subprocess.run(["codesign", "-dvvv", path],
                           capture_output=True, text=True, timeout=5)
        # cdhash is printed on stderr as e.g. "CDHash=ab12..." (newer) or
        # "cdhash=ab12..."; fall back to the Identifier line if absent.
        for line in (r.stderr + r.stdout).splitlines():
            low = line.strip().lower()
            if low.startswith("cdhash="):
                return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return ""


def _load_tmux_tcc_state() -> dict:
    try:
        with open(TMUX_TCC_STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_tmux_tcc_state(state: dict):
    try:
        os.makedirs(os.path.dirname(TMUX_TCC_STATE), exist_ok=True)
        with open(TMUX_TCC_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _tmux_tcc_check() -> int:
    """Report whether the granted tmux binary still matches what's on disk.
    Returns 0 if matched / no prior grant, 1 if cdhash drifted (re-grant
    needed). Pure read — opens no Privacy pane."""
    sym, real = _tmux_paths()
    if not real:
        print("找不到 tmux（不在 PATH）。略過 TCC 檢查。")
        return 0
    cur = _tmux_cdhash(real)
    state = _load_tmux_tcc_state()
    print(f"tmux 連結路徑 : {sym}")
    print(f"tmux 實體路徑 : {real}")
    print(f"目前 cdhash  : {cur or '(讀取失敗)'}")
    if not state.get("cdhash"):
        print("尚未走過授權流程。請執行 `sfctl permissions --tmux` 完成一次性授權。")
        return 0
    print(f"授權時 cdhash: {state['cdhash']}")
    print(f"授權時路徑   : {state.get('real', '(未記錄)')}")
    if cur and state["cdhash"] and cur != state["cdhash"]:
        print()
        print("⚠️  cdhash 已改變 —— 多半是 `brew upgrade tmux` 換了二進位檔。")
        print("    既有的「完全取用磁碟」授權對新檔失效，prompt 會再出現。")
        print("    請重跑 `sfctl permissions --tmux` 把新的 tmux 重新加入 FDA。")
        return 1
    print()
    print("✅ cdhash 未變，授權仍有效。")
    return 0


def _tmux_fda_flow(args):
    """The one-shot guided flow: grant tmux Full Disk Access once so opening
    new tabs never re-triggers the per-folder 'tmux wants to access data from
    other apps' (TCC SystemPolicyAppData) prompts."""
    sym, real = _tmux_paths()
    if not real:
        print("找不到 tmux —— ShellFrame 在 macOS 用單一持久 tmux server 跑所有 tab。")
        print("請先 `brew install tmux` 再回來執行本流程。")
        return

    cur = _tmux_cdhash(real)
    app = "/Applications/ShellFrame.app"

    print("══ ShellFrame tmux 一次性權限索取 ══\n")
    print("為什麼新 tab 會反覆跳「tmux 想取用其他 App 的資料」：")
    print("  • ShellFrame 用『單一持久 tmux server』跑所有 tab（已確認，非每 tab 一個 server），")
    print("    所有 pane 的 responsible process 都是這個 tmux server 本身。")
    print("  • 那個彈窗是 macOS TCC 的『逐資料夾』授權（SystemPolicyAppData）：")
    print("    每開一個 tab 跑的指令只要碰到一個還沒授權過的受保護資料夾，就會再問一次。")
    print("  • 解法：一次給 tmux『完全取用磁碟(FDA)』，蓋掉所有逐資料夾詢問 —— 點一次，永久不再跳。\n")
    print(f"要加入授權的二進位：{sym}")
    if real != sym:
        print(f"  （實體檔：{real}）")
    print(f"目前 cdhash：{cur or '(讀取失敗)'}\n")

    print("步驟：")
    print("  1. 待會自動開啟『完全取用磁碟』面板。")
    print("  2. 點左下角鎖頭解鎖 → 點『+』。")
    print("  3. 在檔案選擇視窗按 ⌘⇧G，貼上下面這行路徑後 Enter，再選 tmux「打開」：")
    print(f"       {sym}")
    print(f"  4. 同樣方式把 ShellFrame.app 也加進去（若已用 .app 啟動）：")
    print(f"       {app}")
    print("  5. 確認兩者開關都是『開』。\n")

    _prompt("按 Enter 開啟『完全取用磁碟』面板…", default="")
    subprocess.run(
        ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"],
        capture_output=True)

    _prompt("\n加好 tmux（與 ShellFrame.app）後按 Enter 繼續…", default="")

    # Record the cdhash we just granted so we can warn on drift later.
    if cur:
        _save_tmux_tcc_state({
            "cdhash": cur,
            "real": real,
            "symlink": sym,
            "granted_ts": time.time(),
        })
        print(f"\n已記錄授權 cdhash → {TMUX_TCC_STATE}")

    # brew pin recommendation — keeps `brew upgrade` from silently swapping
    # the binary (and invalidating the grant) behind the user's back.
    pinned = False
    try:
        r = subprocess.run(["brew", "list", "--pinned"],
                           capture_output=True, text=True, timeout=10)
        pinned = "tmux" in r.stdout.split()
    except Exception:
        pass
    print()
    if pinned:
        print("✅ tmux 已 `brew pin`，brew upgrade 不會換掉它（授權不會無故失效）。")
    else:
        print("建議：執行 `brew pin tmux` 固定版本，避免 brew upgrade 換 cdhash 讓授權失效。")
        if args.yes or _prompt("現在就 pin tmux？[y/N] ", default="n").strip().lower() == "y":
            r = subprocess.run(["brew", "pin", "tmux"], capture_output=True, text=True)
            print("已 pin tmux。" if r.returncode == 0 else f"pin 失敗：{r.stderr.strip()}")

    # Restarting the server is OPTIONAL and destructive — it kills every tab,
    # including the master/orchestration session. The already-running server
    # picks up the new FDA grant on its next file access without a restart, so
    # we never auto-kill; we only explain it as a last resort.
    print()
    print("驗證：")
    print("  • FDA 是即時生效的——現有 server 下次碰到受保護資料夾就會直接放行，不必重啟。")
    print("  • 開 2~3 個新 tab（跑會碰檔案的指令，如 claude / codex），確認不再跳窗即成功。")
    print("  • 若仍偶發跳窗（極少數情況需要重起 server 才認新授權）：")
    print("      ⚠️ `tmux kill-server` 會關閉所有 tab（含總控自己），請確定沒有跑到一半的工作，")
    print("        通常在所有 tab 關閉後、由全新 server 起第一個 tab 時授權最乾淨。")
    print("\n之後若 `brew upgrade tmux` 換了版本，跑 `sfctl permissions --check` 會提示是否需重新授權。")


def _permissions_macos(args):
    # tmux FDA one-shot is the headline flow — it's what actually silences the
    # 'tmux wants to access data from other apps' prompts ShellFrame triggers.
    if getattr(args, "check", False):
        sys.exit(_tmux_tcc_check())
    if getattr(args, "tmux", False):
        _tmux_fda_flow(args)
        return
    ran_tmux = not args.firewall and not args.panes
    if ran_tmux:
        _tmux_fda_flow(args)
        print("\n" + "─" * 60 + "\n")

    # Generic Privacy-pane walk is now opt-in via --panes; the tmux FDA grant
    # above already covers file access for everything running under ShellFrame.
    do_panes = args.panes
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
    sub.add_parser("roster", help="List configured manual delegation roles")

    p_delegate = sub.add_parser("delegate", help="Delegate a task to a configured worker role")
    p_delegate.add_argument("role", help="Roster role or alias, e.g. 時程信件, Coding, 研究")
    p_delegate.add_argument("task", help="Task text to send with the worker wrapper prompt")

    p_new = sub.add_parser("new", help="Create a new session")
    p_new.add_argument("command", nargs="?", default="claude",
                       help="Command to run (default: claude)")
    p_new.add_argument("--label", default=None,
                       help="Optional custom label (defaults to command name)")
    p_new.add_argument("--source", default="sfctl",
                       help="Lifecycle source label for handoff notes (e.g. scheduler)")
    p_new.add_argument("--handoff", action="store_true",
                       help="Write a short startup handoff note to the main session")

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
    p_close.add_argument("--reason", default="sfctl",
                         help="Lifecycle reason for handoff notes")
    p_close.add_argument("--handoff", action="store_true",
                         help="Write a short close handoff note to the main session")

    p_audit = sub.add_parser(
        "history-audit",
        help="Self-check: diff last AI reply vs scroll-up overlay content. "
             "Dumps full snapshot to ~/.config/shellframe/diag/ for offline analysis.",
    )
    p_audit.add_argument("sid", nargs="?", default="",
                         help="Session id (default: first session)")

    sub.add_parser("board-list", help="List task board (交換區) tasks")

    p_badd = sub.add_parser("board-add", help="Add a task to the board")
    p_badd.add_argument("title", help="Task title")
    p_badd.add_argument("--assignee", default="unassigned", help="Agent tab label, default unassigned")
    p_badd.add_argument("--status", default="todo", choices=["todo", "assigned", "in_progress", "done"])
    p_badd.add_argument("--difficulty", default="medium", choices=["easy", "medium", "hard"])
    p_badd.add_argument("--notes", default="", help="Free-text notes")

    p_bupd = sub.add_parser("board-update", help="Update a task by id")
    p_bupd.add_argument("id", help="Task id")
    p_bupd.add_argument("--title", default=None)
    p_bupd.add_argument("--assignee", default=None)
    p_bupd.add_argument("--status", default=None, choices=["todo", "assigned", "in_progress", "done"])
    p_bupd.add_argument("--difficulty", default=None, choices=["easy", "medium", "hard"])
    p_bupd.add_argument("--notes", default=None)

    p_brm = sub.add_parser("board-remove", help="Remove a task by id")
    p_brm.add_argument("id", help="Task id")

    p_perm = sub.add_parser(
        "permissions",
        help="Pre-grant OS permissions (macOS Privacy panes + firewall; "
             "Windows Defender Firewall rules)",
    )
    p_perm.add_argument("--tmux", action="store_true",
                        help="macOS only: run just the tmux Full Disk Access "
                             "one-shot grant flow (silences the 'tmux wants to "
                             "access data from other apps' prompts)")
    p_perm.add_argument("--check", action="store_true",
                        help="macOS only: report whether the granted tmux "
                             "binary still matches (detects brew-upgrade drift); "
                             "opens no Privacy pane")
    p_perm.add_argument("--panes", action="store_true",
                        help="macOS only: open the generic Privacy panes walk, "
                             "skip firewall")
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
        if sys.platform == "darwin":
            _print_result(_restart_macos_direct())
        _print_result(_rpc("restart", timeout=30))
    elif args.cmd == "list":
        _print_result(_rpc("list"))
    elif args.cmd == "roster":
        _print_result(_rpc("roster"))
    elif args.cmd == "delegate":
        _print_result(_rpc("delegate", {
            "role": args.role,
            "task": args.task,
        }, timeout=20))
    elif args.cmd == "new":
        result = _rpc("new_session", {
            "cmd": args.command,
            "cols": 200,
            "rows": 50,
            "source": args.source,
            "handoff": args.handoff,
        }, timeout=20)
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
        _print_result(_rpc("close_session", {
            "sid": args.sid,
            "reason": args.reason,
            "handoff": args.handoff,
        }))
    elif args.cmd == "history-audit":
        _print_result(_rpc("history_audit", {"sid": args.sid}, timeout=20))
    elif args.cmd == "board-list":
        _print_result(_rpc("board_list"))
    elif args.cmd == "board-add":
        _print_result(_rpc("board_add", {
            "title": args.title, "assignee": args.assignee,
            "status": args.status, "difficulty": args.difficulty, "notes": args.notes,
        }))
    elif args.cmd == "board-update":
        upd = {"id": args.id}
        for k in ("title", "assignee", "status", "difficulty", "notes"):
            v = getattr(args, k)
            if v is not None:
                upd[k] = v
        _print_result(_rpc("board_update", upd))
    elif args.cmd == "board-remove":
        _print_result(_rpc("board_remove", {"id": args.id}))
    elif args.cmd == "permissions":
        _cmd_permissions(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
