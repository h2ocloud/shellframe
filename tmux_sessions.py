"""sf_* tmux sessions as data: list them, find the ones no tab owns, keep new
tab ids off names that are already taken.

中文摘要：ShellFrame 的分頁底層是 `sf_<sid>` tmux session。app 重開時要把它們
全部接回；接不回的就是「孤兒」——claude／pi 還在背景跑、吃記憶體，卻沒有任何
分頁看得到。這個模組只讀 tmux、不殺任何東西：列出 session、找孤兒、算出下一個
不會撞名的 sid。也可以單獨跑（`python3 tmux_sessions.py [--json]`），不必重開
ShellFrame 就能看現在有哪些孤兒。

Every tmux call here has its own timeout and never raises: a tmux server that
is paged out under memory pressure can take seconds to answer, and one slow
answer must not cost the caller the whole list.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sf_log import _dlog

PREFIX = "sf_"
_SID_RE = re.compile(r"s(\d+)")
_DETAIL_FMT = "\t".join([
    "#{session_name}", "#{session_attached}", "#{session_created}",
    "#{session_activity}", "#{pane_pid}", "#{pane_current_command}",
])


def _run(tmux: str | None, args: list[str], timeout: float = 3.0) -> str | None:
    """stdout of one tmux call, or None when tmux is missing, fails or times out."""
    if not tmux:
        return None
    try:
        r = subprocess.run([tmux, *args], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        _dlog("tmux", f"{args[0]} {' '.join(args[1:3])} failed: {e!r}")
        return None
    return r.stdout if r.returncode == 0 else None


def env_value(tmux: str | None, name: str, key: str, timeout: float = 3.0) -> str:
    out = _run(tmux, ["show-environment", "-t", name, key], timeout) or ""
    return out.strip().split("=", 1)[1] if "=" in out else ""


def list_sf_sessions(tmux: str | None, prefix: str = PREFIX) -> list[dict]:
    """All sf_* sessions as [{name, cmd, sid}], in tmux order.

    A session whose SF_CMD / SF_SID cannot be read (slow tmux) is still listed
    with empty fields; the caller falls back to the manifest and the name. The
    old version returned [] for the whole list on the first timeout, which
    sent startup down the reboot path instead of reattaching.
    """
    if not tmux:
        _dlog("lifecycle", "  tmux binary not found")
        return []
    out = _run(tmux, ["list-sessions", "-F", "#{session_name}"])
    if out is None:
        _dlog("lifecycle", "  tmux list-sessions failed")
        return []
    result = []
    for line in out.splitlines():
        name = line.strip()
        if name.startswith(prefix):
            result.append({"name": name,
                           "cmd": env_value(tmux, name, "SF_CMD"),
                           "sid": env_value(tmux, name, "SF_SID")})
    return result


def session_details(tmux: str | None, prefix: str = PREFIX) -> list[dict]:
    """sf_* sessions with attach count, times and the pane's process, in two
    tmux calls total (no per-session round trips)."""
    out = _run(tmux, ["list-panes", "-a", "-F", _DETAIL_FMT])
    if out is None:
        return []
    seen, rows = set(), []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 6 or not parts[0].startswith(prefix) or parts[0] in seen:
            continue
        seen.add(parts[0])
        name, attached, created, activity, pane_pid, pane_cmd = parts
        rows.append({
            "name": name,
            "sid": name[len(prefix):],
            "attached": int(attached or 0),
            "created": int(created or 0),
            "activity": int(activity or 0),
            "pane_pid": int(pane_pid or 0),
            "pane_cmd": pane_cmd,
        })
    return rows


def highest_sid(names, prefix: str = PREFIX) -> int:
    """Largest N among `sf_sN` names (0 if none). New tab ids must start above
    it, or a new tab named sf_sN reattaches to someone else's running agent."""
    best = 0
    for name in names:
        m = _SID_RE.fullmatch(str(name)[len(prefix):] if str(name).startswith(prefix) else "")
        if m:
            best = max(best, int(m.group(1)))
    return best


def name_taken(tmux: str | None, name: str) -> bool:
    """True when a tmux session with this exact name exists. Unknown (tmux
    missing or too slow) counts as free, which is the old behaviour."""
    if not tmux:
        return False
    try:
        r = subprocess.run([tmux, "has-session", "-t", f"={name}"],
                           capture_output=True, timeout=3)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0


def refresh_client(tmux: str | None, name: str, client_pid: int | None) -> bool:
    """Make tmux redraw the whole screen for the client ShellFrame attached to
    `name` (the one whose pid is ours). Returns True when a client was found."""
    if not name or not client_pid:
        return False
    out = _run(tmux, ["list-clients", "-t", name, "-F", "#{client_pid}\t#{client_name}"])
    for line in (out or "").splitlines():
        pid, _, client = line.partition("\t")
        if pid.strip() == str(client_pid) and client:
            return _run(tmux, ["refresh-client", "-t", client]) is not None
    return False


def find_orphans(details: list[dict], owned_names, labels: dict | None = None) -> list[dict]:
    """sf_* sessions that no ShellFrame tab owns. `owned_names` is the set of
    tmux names the running app has tabs for. Report only: nothing is killed."""
    owned = set(owned_names or ())
    labels = labels or {}
    now = int(time.time())
    out = []
    for d in details:
        if d["name"] in owned:
            continue
        row = dict(d)
        row["label"] = labels.get(d["sid"], "")
        row["idle_min"] = max(0, (now - d["activity"]) // 60) if d["activity"] else None
        out.append(row)
    return out


def rss_mb(pids) -> dict:
    """Resident size per pid tree root, from one `ps` call. RSS undercounts a
    swapped-out process; it is a floor, not the footprint."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True,
                             text=True, timeout=5).stdout
    except (subprocess.TimeoutExpired, OSError):
        return {}
    kids, rss = {}, {}
    for line in out.splitlines():
        try:
            pid, ppid, kb = (int(x) for x in line.split())
        except ValueError:
            continue
        kids.setdefault(ppid, []).append(pid)
        rss[pid] = kb
    result = {}
    for root in pids:
        total, stack = 0, [root]
        while stack:
            p = stack.pop()
            total += rss.get(p, 0)
            stack.extend(kids.get(p, ()))
        result[root] = total // 1024
    return result


def format_orphans(orphans: list[dict]) -> str:
    if not orphans:
        return "no orphan sf_* tmux sessions"
    sizes = rss_mb([o["pane_pid"] for o in orphans])
    lines = [f"{len(orphans)} orphan sf_* tmux session(s) — still running, no tab shows them:"]
    for o in orphans:
        idle = "?" if o["idle_min"] is None else f"{o['idle_min']}m"
        lines.append(f"  {o['name']:<10} pid={o['pane_pid']:<6} {o['pane_cmd']:<10} "
                     f"rss≥{sizes.get(o['pane_pid'], 0)}MB idle={idle} attached={o['attached']} "
                     f"{o['label']}")
    return "\n".join(lines)


def _cli(argv: list[str]) -> int:
    """Read-only report for a running ShellFrame, no restart needed: a session
    counts as owned when some client is attached to it (ShellFrame attaches
    every tab it shows)."""
    tmux = shutil.which("tmux") or shutil.which("tmux", path="/opt/homebrew/bin:/usr/local/bin")
    details = session_details(tmux)
    cfg_path = Path.home() / ".config" / "shellframe" / "config.json"
    try:
        labels = json.loads(cfg_path.read_text(encoding="utf-8")).get("session_labels") or {}
    except (OSError, ValueError):
        labels = {}
    orphans = find_orphans(details, {d["name"] for d in details if d["attached"]}, labels)
    if "--json" in argv:
        print(json.dumps(orphans, ensure_ascii=False, indent=2))
    else:
        print(format_orphans(orphans))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
