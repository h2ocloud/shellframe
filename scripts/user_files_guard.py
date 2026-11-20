#!/usr/bin/env python3
"""What a test run must leave alone: the real home's Claude settings (ShellBot's hook entries in
~/.claude/settings.json and every account profile's) and the office preferences file. The person may use
the office during a run, so that file only counts as touched when demo data (「示範」) shows up in it.

    user_files_guard.py snapshot <file>   write a fingerprint of them to <file>
    user_files_guard.py check <file>      exit 1, naming what changed, if a test touched them

11-19 a test started the status monitor without moving the settings paths and wrote its own folder's
hook path into all three settings files on the developer's machine; nothing failed. run_tests.sh takes a
snapshot before the run and checks it after. Only ShellBot's own entries are compared, so Claude Code
saving a permission meanwhile is not a failure. The real home comes from the password database, not
$HOME. Nothing is printed from the files themselves. Machines without these files (CI) have nothing to
compare.
"""
import hashlib
import json
import os
import sys
from pathlib import Path


def real_home() -> Path:
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        return Path(os.path.expanduser("~"))


def ours(path: Path) -> str:
    """ShellBot's hook entries in one settings.json, as a stable string ('' when none or unreadable)."""
    try:
        hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks") or {}
    except Exception:
        return ""
    keep = {}
    for ev, groups in sorted(hooks.items()):
        cmds = sorted(str(h.get("command", "")) for g in (groups or []) for h in (g.get("hooks") or [])
                      if isinstance(h, dict) and "sf_agent_hook.py" in str(h.get("command", "")))
        if cmds:
            keep[ev] = cmds
    return json.dumps(keep, sort_keys=True)


def fingerprint() -> dict:
    home = real_home()
    out = {}
    targets = [home / ".claude" / "settings.json"]
    profiles = home / ".config" / "shellframe" / "account-profiles" / "claude"
    if profiles.is_dir():
        targets += sorted(p / "settings.json" for p in profiles.iterdir() if p.is_dir())
    for p in targets:
        if p.exists():
            out[str(p)] = hashlib.sha256(ours(p).encode()).hexdigest()
    office = home / ".local" / "state" / "shellframe" / "office.json"
    if office.exists():
        try:
            demo = "示範" in office.read_text(encoding="utf-8")
        except Exception:
            demo = False
        out[str(office)] = "demo data" if demo else "no demo data"
    return out


def main(argv) -> int:
    if len(argv) != 3 or argv[1] not in ("snapshot", "check"):
        print(__doc__.strip().splitlines()[0])
        return 2
    f = Path(argv[2])
    if argv[1] == "snapshot":
        f.write_text(json.dumps(fingerprint()), encoding="utf-8")
        return 0
    try:
        before = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return 0
    after = fingerprint()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    for k in changed:
        print(f"  ! a test changed the real {k.replace(str(real_home()), '~')}")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
