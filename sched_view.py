"""What a LaunchAgent looks like on the Loops panel.

Pure functions over a plist dict and a clock: no launchctl, no home directory,
no `import main`. `api_schedules.py` does the I/O (the plist folder, `launchctl
list`, the platform check) and hands the results here, so every rule below is a
unit test over a fake plist.

Each schedule becomes one row, a dict that survives `json.dumps`:

    id, title, kind, group, when {en, zh}, tip {en, zh} | None, detail, cmd,
    script, enabled, running, last_exit, active, toggle, state, notes
    [{level, code, text {en, zh}, tip}], members (a group of one-offs only)

`kind` is one of interval, daily, weekly, monthly, calendar, once, daemon.

launchd facts the rules rest on:

* A key missing from a StartCalendarInterval entry is a wildcard, so
  `{Hour: 9, Minute: 5}` is every day and `{Weekday: 1, Hour: 9, Minute: 5}` is
  Mondays. Weekday 0 and 7 are both Sunday.
* `{Month: 11, Day: 10, Hour: 9, Minute: 5}` has no year. It fires on 11/10
  **every year**. A one-off armed this way and never unloaded fires again next
  year, which is why a loaded one-off carries a warning.
* A job that is on disk but not loaded in launchd never fires. `loaded` (the pid
  and last exit from `launchctl list`) is what says it is armed.
* launchd does not start a second copy of a job that is still running, so an
  interval is the shortest gap, not a promise.

What this cannot know: `launchctl list` gives a pid and the last exit status, no
history. "One-off, ran 11/10" means 11/10 has passed while launchd had the job
loaded; it cannot tell a run from a time that passed while the machine was off.
A run history is a later phase.

Which agents are listed (`prefixes`), their titles and any explicit groups of
one-offs belong to the user, in `schedules.json` in the app's config folder,
never in this file (see `read_config`).
"""

import json
import re
from datetime import datetime
from pathlib import Path

from sf_log import _swallow

CONFIG_NAME = "schedules.json"

_WEEK_EN = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEK_ZH = ("一", "二", "三", "四", "五", "六", "日")
_SCRIPT_EXTS = (".sh", ".py", ".js", ".mjs", ".ts", ".exp", ".rb", ".zsh", ".bash")
_LABEL_OK = re.compile(r"[A-Za-z0-9._-]+")


def L(en: str, zh: str) -> dict:
    """A user-facing text in both languages; the page picks one."""
    return {"en": en, "zh": zh}


# ── what the user configured ───────────────────────────────────────────────

def valid_prefix(p) -> bool:
    """A label prefix the panel may list and toggle. One that is empty or as short
    as a top-level domain would reach every vendor agent in the folder."""
    return (isinstance(p, str) and _LABEL_OK.fullmatch(p) is not None
            and len(p.rstrip(".")) >= 4)


def valid_label(label) -> bool:
    """A launchd label that is safe to turn into a file name (no path pieces)."""
    return (isinstance(label, str) and _LABEL_OK.fullmatch(label) is not None
            and ".." not in label and not label.startswith("."))


def parse_config(obj) -> dict:
    """The well-formed parts of a decoded `schedules.json`:
    prefixes [str], titles {label or group id: str}, groups {group id: [label]}."""
    obj = obj if isinstance(obj, dict) else {}
    raw_prefixes = obj.get("prefixes")
    prefixes = [p for p in raw_prefixes if valid_prefix(p)] if isinstance(raw_prefixes, list) else []
    raw_titles = obj.get("titles")
    titles = ({str(k): v.strip() for k, v in raw_titles.items() if isinstance(v, str) and v.strip()}
              if isinstance(raw_titles, dict) else {})
    groups = {}
    if isinstance(obj.get("groups"), dict):
        for gid, labels in obj["groups"].items():
            ok = [x for x in labels if valid_label(x)] if isinstance(labels, list) else []
            if len(ok) >= 2:
                groups[str(gid)] = ok
    return {"prefixes": prefixes, "titles": titles, "groups": groups}


def read_config(path) -> tuple:
    """(config, error). A missing file is not an error: nothing is configured yet."""
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return parse_config({}), ""
    except OSError as e:
        return parse_config({}), f"{p.name}: {e}"
    try:
        return parse_config(json.loads(raw)), ""
    except ValueError as e:
        return parse_config({}), f"{p.name}: not valid JSON ({e})"


# ── reading a plist ────────────────────────────────────────────────────────

def program_args(plist: dict) -> list:
    prog = plist.get("ProgramArguments") or ([plist["Program"]] if plist.get("Program") else [])
    return [str(x) for x in prog]


def script_path(prog) -> str:
    """The first argument that looks like a script: what the job runs."""
    for a in prog:
        if str(a).endswith(_SCRIPT_EXTS):
            return str(a)
    return ""


def _entries(sci) -> list:
    """StartCalendarInterval (one dict or a list) as dicts with lower-case keys and
    only the integer fields that were present. Sunday is always 0."""
    out = []
    for it in (sci if isinstance(sci, list) else [sci]):
        if not isinstance(it, dict):
            continue
        e = {}
        for key in ("Month", "Day", "Weekday", "Hour", "Minute"):
            v = it.get(key)
            if isinstance(v, int) and not isinstance(v, bool):
                e[key.lower()] = v
        if "weekday" in e:
            e["weekday"] %= 7
        out.append(e)
    return out


def _class(e: dict) -> str:
    """once (a month and a day), weekly, daily, monthly, or other (a wildcard hour
    or minute, or a mix launchd allows but nobody means)."""
    m, d, wd = e.get("month"), e.get("day"), e.get("weekday")
    if e.get("hour") is None or e.get("minute") is None:
        return "other"
    if m is not None and d is not None and wd is None:
        return "once"
    if m is None and d is None:
        return "weekly" if wd is not None else "daily"
    if m is None and wd is None:
        return "monthly"
    return "other"


def _hm(e: dict) -> str:
    return f"{e['hour']:02d}:{e['minute']:02d}"


def _times(items) -> tuple:
    ts = sorted({_hm(e) for e in items})
    return " / ".join(ts), "／".join(ts)


def _dates(dates) -> tuple:
    """['11/10', '12/1'] -> ('11/10, 12/1', '11/10、12/1')."""
    return ", ".join(dates), "、".join(dates)


# ── frequency text ─────────────────────────────────────────────────────────

def _interval_text(secs: int) -> dict:
    if secs > 0 and secs % 3600 == 0:
        n, en, zh = secs // 3600, "h", "小時"
    elif secs > 0 and secs % 60 == 0:
        n, en, zh = secs // 60, "min", "分鐘"
    else:
        n, en, zh = secs, "s", "秒"
    return L(f"Every {n} {en}", f"每 {n} {zh}")


def _weekdays_text(days) -> tuple:
    """{1..5} -> ('Mon–Fri', '週一～五'); days that are not adjacent are listed."""
    pos = sorted((d - 1) % 7 for d in days)       # Monday first, so Sunday (0) comes last
    runs, cur = [], [pos[0]]
    for p in pos[1:]:
        if p == cur[-1] + 1:
            cur.append(p)
        else:
            runs.append(cur)
            cur = [p]
    runs.append(cur)
    en = ", ".join(_WEEK_EN[r[0]] if len(r) == 1 else f"{_WEEK_EN[r[0]]}–{_WEEK_EN[r[-1]]}" for r in runs)
    zh = "、".join(f"週{_WEEK_ZH[r[0]]}" if len(r) == 1 else f"週{_WEEK_ZH[r[0]]}～{_WEEK_ZH[r[-1]]}"
                   for r in runs)
    return en, zh


def _merged(items, key) -> list:
    """[(frozenset of weekdays or month days, [clock times])]: times that fire on
    the same days are one phrase, so five weekdays x four times reads as one
    group instead of twenty entries."""
    by_time = {}
    for e in items:
        by_time.setdefault(_hm(e), set()).add(e[key])
    groups = {}
    for t, vals in by_time.items():
        groups.setdefault(frozenset(vals), []).append(t)
    return sorted(((k, sorted(v)) for k, v in groups.items()), key=lambda kv: kv[1][0])


def _generic_text(e: dict) -> tuple:
    raw = " ".join(f"{k.capitalize()}={e[k]}" for k in ("month", "day", "weekday", "hour", "minute") if k in e)
    return f"Calendar: {raw or 'every minute'}", f"行事曆：{raw or '每分鐘'}"


def _calendar_parts(entries: list) -> tuple:
    """(kind, [(en, zh)]) for a StartCalendarInterval, merged for reading."""
    by = {}
    for e in entries:
        by.setdefault(_class(e), []).append(e)
    daily = list(by.get("daily", []))
    weekly = []
    for days, ts in (_merged(by["weekly"], "weekday") if by.get("weekly") else []):
        if len(days) == 7:                          # every day of the week is just daily
            daily += [{"hour": int(t[:2]), "minute": int(t[3:])} for t in ts]
        else:
            weekly.append((days, ts))
    parts, kinds = [], []
    if by.get("once"):
        kinds.append("once")
        d_en, d_zh = _dates(sorted({_md(e["month"], e["day"]) for e in by["once"]}))
        t_en, t_zh = _times(by["once"])
        parts.append((f"{d_en} {t_en}", f"{d_zh} {t_zh}"))
    if daily:
        kinds.append("daily")
        t_en, t_zh = _times(daily)
        parts.append((f"Daily {t_en}", f"每天 {t_zh}"))
    for days, ts in weekly:
        kinds.append("weekly")
        d_en, d_zh = _weekdays_text(days)
        parts.append((f"{d_en} {' / '.join(ts)}", f"{d_zh} {'／'.join(ts)}"))
    for days, ts in (_merged(by["monthly"], "day") if by.get("monthly") else []):
        kinds.append("monthly")
        ds = sorted(days)
        parts.append((f"Monthly on day{'s' if len(ds) > 1 else ''} {', '.join(map(str, ds))} {' / '.join(ts)}",
                      f"每月 {'、'.join(map(str, ds))} 日 {'／'.join(ts)}"))
    for e in by.get("other", []):
        kinds.append("calendar")
        parts.append(_generic_text(e))
    return (kinds[0] if len(set(kinds)) == 1 else "calendar"), parts


# ── one-offs: a calendar entry pinned to a month and a day ─────────────────

def _md(month: int, day: int) -> str:
    return f"{month}/{day}"


def _occurrences(entries, year: int) -> list:
    """This year's datetimes for month-and-day entries (Feb 29 in a common year has
    none). A missing hour or minute counts from the start of it."""
    out = []
    for e in entries:
        try:
            out.append(datetime(year, e["month"], e["day"], e.get("hour") or 0, e.get("minute") or 0))
        except ValueError:
            continue
    return sorted(out)


def _once_state(entries, loaded, now: datetime) -> dict:
    """armed: loaded and a time is still ahead this year. ran: loaded and every
    time has passed. unarmed: not loaded, so it will not fire at all."""
    occ = _occurrences(entries, now.year)
    nxt = next((o for o in occ if o > now), None)
    last = next((o for o in reversed(occ) if o <= now), None)
    if loaded is None:
        state = "unarmed"
    elif nxt is not None or last is None:
        state = "armed"
    else:
        state = "ran"
    return {"state": state, "next": nxt, "last": last}


def _at(dt: datetime, now: datetime, with_time: bool) -> tuple:
    """('today 09:05', '今天 09:05') or ('11/13 09:05', …): today is said as today."""
    t = f" {dt:%H:%M}" if with_time else ""
    if dt.date() == now.date():
        return f"today{t}", f"今天{t}"
    return f"{_md(dt.month, dt.day)}{t}", f"{_md(dt.month, dt.day)}{t}"


def _repeat_note(dates: list) -> dict:
    en, zh = _dates(dates)
    return {"level": "warn", "code": "yearly_repeat",
            "text": L(f"Fires again on {en} next year", f"隔年 {zh} 會再觸發"),
            "tip": L("launchd repeats a month-and-day schedule every year. Unload it after it has run, "
                     "or it runs again on the same day next year.",
                     "launchd 的「月＋日」排程每年都會觸發。跑完要解除，否則隔年同一天會再跑一次。")}


def _once_row(rec: dict, entries, loaded, now: datetime) -> None:
    info = _once_state(entries, loaded, now)
    state = info["state"]
    dates = sorted({(e["month"], e["day"]) for e in entries})
    md = [_md(*d) for d in dates]
    t_en, t_zh = _times(entries)
    d_en, d_zh = _dates(md)
    rec.update(kind="once", state=state, toggle=False, active=state == "armed", times=L(t_en, t_zh),
               dates=md, _next=info["next"], _last=info["last"],
               _sort=min((e["month"], e["day"], e.get("hour") or 0, e.get("minute") or 0) for e in entries))
    if state == "unarmed":
        rec["when"] = L("One-off · not armed (waiting for you)", "單次・未排（等你說要排）")
        rec["detail"] = L(f"Set for {d_en} {t_en}, but launchd has not loaded it, so it will not fire.",
                          f"plist 設的是 {d_zh} {t_zh}，但 launchd 沒有載入，不會觸發。")
        return
    if state == "armed":
        en, zh = _at(info["next"], now, True) if info["next"] else (f"{d_en} {t_en}", f"{d_zh} {t_zh}")
        rec["when"] = L(f"One-off · armed {en}", f"單次・已排 {zh}")
    else:
        d = _md(info["last"].month, info["last"].day)
        rec["when"] = L(f"One-off · ran {d}", f"單次・{d} 已執行")
        rec["tip"] = L("Ran means that time has passed while launchd had the job loaded. "
                       "Whether it succeeded is in the job's own log.",
                       "已執行是指那個時間點過了、而且 launchd 當時有載入。成功與否要看這個工作自己的紀錄。")
    rec["notes"].append(_repeat_note(md))


# ── one row per plist ──────────────────────────────────────────────────────

def describe(label: str, plist: dict, *, loaded=None, now: datetime = None, cfg: dict = None):
    """One panel row for a LaunchAgent, or None when it is neither a timer nor a
    KeepAlive daemon. `loaded` is (pid or None, last exit) when launchd has the job
    loaded, None when it does not. A row with a leading underscore key is internal
    to grouping; `build_rows` strips those."""
    now = now or datetime.now()
    cfg = cfg or {}
    prog = program_args(plist)
    pid, last_exit = loaded if loaded is not None else (None, 0)
    rec = {"id": label, "kind": "calendar", "group": False,
           "title": (cfg.get("titles") or {}).get(label) or label.split(".")[-1],
           "when": L("—", "—"), "tip": None, "detail": None,
           "cmd": " ".join(prog), "script": script_path(prog),
           "enabled": loaded is not None, "running": pid is not None, "last_exit": last_exit,
           "active": loaded is not None, "toggle": True, "state": "", "notes": [], "members": None}

    interval = plist.get("StartInterval")
    entries = _entries(plist["StartCalendarInterval"]) if plist.get("StartCalendarInterval") else []
    if interval is None and not entries:
        return _daemon_row(rec, loaded) if plist.get("KeepAlive") else None

    phrases, kinds = [], []
    if interval is not None:
        try:
            secs = int(interval)
        except (TypeError, ValueError):
            secs = 0
        t = _interval_text(secs)
        phrases.append((t["en"], t["zh"]))
        kinds.append("interval")
        rec["tip"] = L("launchd does not start a new run while the last one is still going, "
                       "so the real gap can be longer.",
                       "前一次還沒跑完時 launchd 不會再啟動一份，實際間隔可能比設定的長。")
    if entries:
        kind, parts = _calendar_parts(entries)
        phrases += parts
        kinds.append(kind)
    kind = kinds[0] if len(kinds) == 1 else "calendar"
    if kind == "once":
        _once_row(rec, entries, loaded, now)
        return rec
    rec["kind"] = kind
    rec["when"] = L(" · ".join(p[0] for p in phrases), " · ".join(p[1] for p in phrases))
    return rec


def _daemon_row(rec: dict, loaded) -> dict:
    """A KeepAlive agent is not a timer. One that is not running and whose last exit
    was not 0 is the "keeps dying and being restarted" case the panel used to hide."""
    rec["kind"] = "daemon"
    rec["when"] = L("Kept alive by launchd", "由 launchd 保持常駐")
    if loaded is None:
        rec["state"] = "off"
    elif rec["running"]:
        rec["state"] = "running"
    elif rec["last_exit"]:
        rec["state"] = "failing"
        rec["notes"].append({
            "level": "warn", "code": "daemon_failing",
            "text": L(f"Not running · last exit {rec['last_exit']}", f"沒有在跑・上次 exit {rec['last_exit']}"),
            "tip": L("A KeepAlive job that is not running and exited non-zero is usually being restarted "
                     "in a loop. Check that its program still exists.",
                     "KeepAlive 的工作沒在跑、上次又是非 0 結束，通常是在反覆重啟。先確認它的程式還在。")})
    else:
        rec["state"] = "stopped"
    return rec


# ── groups of one-offs ─────────────────────────────────────────────────────

def _stem(script: str) -> str:
    return re.sub(r"\.[^.]+$", "", script.rsplit("/", 1)[-1])


def _group_title(gid: str, script: str, cfg: dict) -> str:
    """The title the user gave the group; else, for a group they named, its name; else the
    shared script's name (a title keyed by that name works too)."""
    titles = cfg.get("titles") or {}
    if gid in (cfg.get("groups") or {}):
        return titles.get(gid) or gid
    stem = _stem(script) if script else ""
    return titles.get(gid) or (titles.get(stem) if stem else None) or stem or gid


def _member(m: dict) -> dict:
    return {"id": m["id"], "title": m["title"], "times": m["times"], "state": m["state"],
            "dates": m["dates"], "cmd": m["cmd"], "last_exit": m["last_exit"]}


def _make_group(gid: str, members: list, cfg: dict, now: datetime) -> dict:
    """Armed together, shown together. The group is armed only when every member is
    loaded; a member that is not loaded will not fire, and that is the one thing a
    row of three should never hide."""
    script = next((m["script"] for m in members if m["script"]), "")
    loaded = [m for m in members if m["enabled"]]
    if not loaded:
        state = "unarmed"
    elif len(loaded) < len(members):
        state = "partial"
    elif any(m["state"] == "armed" for m in members):
        state = "armed"
    else:
        state = "ran"
    nexts = [m["_next"] for m in members if m["state"] == "armed" and m["_next"]]
    lasts = [m["_last"] for m in members if m["_last"]]
    dates = sorted({d for m in members for d in m["dates"]})
    if state == "armed":
        en, zh = _at(min(nexts), now, False) if nexts else _dates(dates)
        when = L(f"One-off · armed {en}", f"單次・已排 {zh}")
    elif state == "ran":
        en, zh = _dates(sorted({_md(x.month, x.day) for x in lasts}) or dates)
        when = L(f"One-off · ran {en}", f"單次・{zh} 已執行")
    elif state == "partial":
        when = L(f"One-off · only {len(loaded)} of {len(members)} armed",
                 f"單次・{len(members)} 個只排了 {len(loaded)} 個")
    else:
        when = L("One-off · not armed (waiting for you)", "單次・未排（等你說要排）")
    notes = []
    if state == "partial":
        off = [m["title"] for m in members if not m["enabled"]]
        notes.append({"level": "warn", "code": "partial_group",
                      "text": L(f"Not loaded, will not fire: {', '.join(off)}",
                                f"沒有載入、不會觸發：{'、'.join(off)}")})
    if loaded:
        notes.append(_repeat_note(dates))
    exits = [m["last_exit"] for m in loaded if m["last_exit"]]
    return {"id": f"group:{gid}", "kind": "once", "group": True, "title": _group_title(gid, script, cfg),
            "when": when, "tip": None, "detail": None, "cmd": "", "script": script,
            "enabled": bool(loaded), "running": any(m["running"] for m in members),
            "last_exit": exits[0] if exits else 0, "active": state == "armed", "toggle": False,
            "state": state, "notes": notes, "dates": dates, "members": [_member(m) for m in members]}


def _group_once(rows: list, cfg: dict, now: datetime) -> list:
    """One-offs named together in a `groups` entry of the config, or else one-offs
    that run the same script on the same month and day, become one row."""
    once = {r["id"]: r for r in rows if r["kind"] == "once" and not r["group"]}
    plan = {}                                       # label -> (group id, [member labels])
    for gid, labels in (cfg.get("groups") or {}).items():
        members = [x for x in labels if x in once and x not in plan]
        if len(members) >= 2:
            for x in members:
                plan[x] = (gid, members)
    auto = {}
    for r in rows:
        if r["id"] in once and r["id"] not in plan and r["script"]:
            auto.setdefault((r["script"], tuple(r["dates"])), []).append(r["id"])
    for (script, dates), labels in auto.items():
        if len(labels) >= 2:
            for x in labels:
                plan[x] = (f"{script}@{'+'.join(dates)}", labels)
    out, made = [], set()
    for r in rows:
        hit = plan.get(r["id"])
        if hit is None:
            out.append(r)
        elif hit[0] not in made:
            made.add(hit[0])
            members = sorted((once[x] for x in hit[1]), key=lambda m: (m["_sort"], m["id"]))
            out.append(_make_group(hit[0], members, cfg, now))
    return out


def build_rows(items, loaded: dict, cfg: dict, now: datetime = None) -> list:
    """`items`: [(label, plist dict)] in display order. `loaded`: label -> (pid, last
    exit) for what launchd has loaded. Returns the panel's rows."""
    now = now or datetime.now()
    rows = []
    for label, plist in items:
        try:
            rec = describe(label, plist, loaded=loaded.get(label), now=now, cfg=cfg)
        except Exception:                           # one plist nobody can read must not blank the panel
            _swallow("sched_view.build_rows")
            rec = None
        if rec is not None:
            rows.append(rec)
    rows = _group_once(rows, cfg, now)
    for r in rows:
        for k in [k for k in r if k.startswith("_")]:
            del r[k]
    return rows
