"""The office's ops TV (a built-in office object, `settings.experimental_ops`): how this computer is doing.

What it is
  The lobby wall's TV shows the computer's health (CPU, memory, network, disk); something wrong turns it
  red; its popup (`office_object_view`) is a list of widgets the office page draws. The rules and the
  state machine are `ops_rules`, the readings `ops_sampler`; this module is the object's declaration, the
  collector that runs them on a schedule, and the two files it keeps.

The collector (one daemon thread, `threading.Event`, started only while the flag is on)
  usually every 300 s; every 30 s while some rule is over its line (so "10 minutes in a row" is judged to
  within half a minute); every 2 s while the detail screen is open (`live`: the page says so with
  `office_object_live`, and again every 30 s; 60 s with no word and it goes back by itself); the top five
  programs every 10 s while it is open, never otherwise. A watch or normal sample outside live reads only
  what the rules need, except one full sample every 300 s. Nothing is read or written under the lock that
  guards the state, and nothing runs a subprocess inside `evaluate_js`: the page is told through
  `on_level`, called by the collector thread after the lock is released.

What it keeps (the state folder, never the office's own `office.json`, which the page overwrites whole)
  `ops.json`          the rules (on/off and number), the mute time and how the top five is ordered (`top_sort`:
                      auto, cpu or mem)
  `ops_history.json`  24 hours, 288 points, one per 5 minutes: time, CPU, memory %, pressure, swap, free disk %,
                      network speeds, route. Written every 30 minutes and when the collector stops. Per core
                      numbers and the top five are only ever the newest reading, never kept.
  Nothing is sent anywhere.

A system notification is sent once per alert event, when it starts (not while muted, not on recovery).
Plugins do not get a collector or `office_view` in this version: only this built-in object has them.
"""
import json
import os
import tempfile
import threading
import time
from collections import deque

import desktop_notify
import office_objects as oo
import ops_rules
import ops_sampler
import sf_config
import sf_lang
from sf_log import _swallow

NORMAL_S, WATCH_S, LIVE_S, TOP_S = 300.0, 30.0, 2.0, 10.0
LIVE_TIMEOUT_S = 60.0
FULL_EVERY_S = 298.0               # a full sample at least this often (the history point is one of them)
HISTORY_KEEP = 288
HISTORY_STEP_S = 300
HISTORY_FLUSH_S = 1800.0
RECENT_KEEP_S = 1800.0
PRESSURE_CODE = {"normal": 0, "warn": 1, "critical": 2}
HOLD_S = 600.0                     # a reading still stands for the strip this long after it was taken
MUTE_MAX_MIN = 24 * 60
TOP_SORTS = ("auto", "cpu", "mem")
TOP_SORT_DEFAULT = "auto"
# "auto" ranks the top five by memory, not CPU, when the computer is short of memory: the memory pressure is
# not normal, or the memory used is at least this share of the total, or the swap in use is at least this
# much. Otherwise by CPU. Any one of the three is enough; a reading that is missing counts as "not short".
TOP_AUTO_MEM_PCT = 80.0
TOP_AUTO_SWAP_BYTES = 4 * 1024 ** 3

DECLARATION = {
    "id": "ops",
    "title": {"en": "Ops", "zh": "維運"},
    "short": {"en": "Ops", "zh": "維運"},
    "subtitle": {"en": "How this computer is doing: CPU, memory, network, disk. Read only, this computer.",
                 "zh": "這台電腦的健康狀態：CPU、記憶體、網路、磁碟。只讀，這台電腦。"},
    "furniture": "tv",
    "place": "lobby:wall",
    "button": True,
    "badge_hint": {"en": "1 = something is wrong", "zh": "1＝有異常"},
    "actions": [
        {"id": "mute", "label": {"en": "Mute", "zh": "靜音"}},
        {"id": "unmute", "label": {"en": "Unmute", "zh": "取消靜音"}},
        {"id": "set_rule", "label": {"en": "Change a rule", "zh": "調整規則"}},
        {"id": "reset_rules", "label": {"en": "Back to defaults", "zh": "還原預設規則"}},
        {"id": "top_sort", "label": {"en": "Top 5 order", "zh": "前 5 名排序"}},
    ],
}


def state_file():
    return sf_config.state_dir() / "ops.json"


def history_file():
    return sf_config.state_dir() / "ops_history.json"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ops-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ── words ──────────────────────────────────────────────────────────────────────
def fmt_bytes(n) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}" if unit == "GB" else f"{n / size:.0f} {unit}"
    return f"{n:.0f} B"


def fmt_rate(bps) -> str:
    return "-" if bps is None else fmt_bytes(bps) + "/s"


def fmt_clock(t) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _pct_tone(v, warn=70, bad=90):
    return "bad" if v >= bad else "warn" if v >= warn else "ok"


class Collector:
    """The samples, the rules over them, the two files, and the thread. A test builds one with a fake
    `sampler` and `clock` and calls `step()` itself; the app starts the thread with `start()`."""

    START_DELAY_S = 2.0                          # the thread waits this long before its first reading (a test sets 0)

    def __init__(self, sampler=None, clock=time.time, notify=desktop_notify.send, on_level=None, load=True):
        self.sampler = sampler or ops_sampler.Sampler()
        self.clock = clock
        self.notify = notify
        self.on_level = on_level
        self.lang = "zh" if sf_lang.pick("en", "zh") == "zh" else "en"
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.engine = ops_rules.Engine()
        self.recent = deque(maxlen=1500)         # light copies of the newest readings (30 minutes)
        self.history = []                        # [t, cpu, mem %, pressure code, swap, free disk %, in, out, online]
        self.latest = {}                         # the newest value of every field (a light sample keeps the memory ones)
        self.top = {}                            # {"cpu": [...], "mem": [...]}: the newest top five in each order
        self.top_sort = TOP_SORT_DEFAULT
        self.top_t = 0.0
        self.updated = None
        self._live_until = 0.0
        self._last_t = 0.0
        self._last_full_t = 0.0
        self._last_hist_t = 0.0
        self._last_flush_t = clock()            # the first write is 30 minutes after the start, not at once
        self._dirty = False
        self._pushed = None
        if load:
            self._load()

    # ── files ──
    def _load(self):
        saved = _read_json(state_file())
        if isinstance(saved, dict):
            self.engine.load(saved)
            if saved.get("top_sort") in TOP_SORTS:
                self.top_sort = saved["top_sort"]
        got = _read_json(history_file())
        now = self.clock()
        rows = []
        for r in (got.get("points") if isinstance(got, dict) else None) or []:
            if isinstance(r, list) and len(r) == 9 and _num(r[0]) and now - 86400 <= r[0] <= now + 60:
                rows.append(r)
        self.history = rows[-HISTORY_KEEP:]
        if self.history:
            self._last_hist_t = self.history[-1][0]

    def _save_rules(self):
        try:
            with self._lock:
                data = self.engine.export()
                data["top_sort"] = self.top_sort
            _write_json(state_file(), data)
        except Exception:
            _swallow("office_ops.save_rules")

    def flush(self):
        """Write the 24-hour record now (every 30 minutes, and when the collector stops)."""
        try:
            with self._lock:
                data = {"v": 1, "points": list(self.history)}
                self._dirty = False
                self._last_flush_t = self.clock()
            _write_json(history_file(), data)
        except Exception:
            _swallow("office_ops.flush")

    # ── the schedule ──
    def interval(self, now) -> float:
        with self._lock:
            if now < self._live_until:
                return LIVE_S
            return WATCH_S if self.engine.pending() else NORMAL_S

    def live(self, on, now=None) -> None:
        """The detail screen is open (`on`) or closed. An open one must say so again within LIVE_TIMEOUT_S."""
        now = self.clock() if now is None else now
        with self._lock:
            self._live_until = now + LIVE_TIMEOUT_S if on else 0.0
        self._wake.set()

    def is_live(self, now=None) -> bool:
        now = self.clock() if now is None else now
        return now < self._live_until

    def start(self):
        if self.running:
            return
        stop = self._stop = threading.Event()            # one event per thread: a thread still finishing a reading
        self._thread = threading.Thread(target=self._loop, args=(stop,), daemon=True, name="ops-collector")  # after stop() cannot be revived by start()
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(2.0)
        self._thread = None
        if self._dirty:
            self.flush()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _loop(self, stop):
        stop.wait(self.START_DELAY_S)             # let the app finish starting
        while not stop.is_set():
            now = self.clock()
            wait = self._last_t + self.interval(now) - now
            if wait > 0:
                self._wake.wait(wait)
                self._wake.clear()
                continue
            try:
                self.step()
            except Exception:
                _swallow("office_ops.step")
                self._last_t = self.clock()       # a broken reading must not become a busy loop

    # ── one reading ──
    def step(self, now=None) -> None:
        """Read the computer once, feed the rules, keep the history, tell the page and the person."""
        now = self.clock() if now is None else now
        live = self.is_live(now)
        full = live or now - self._last_full_t >= FULL_EVERY_S
        sample = self.sampler.sample(full=full)           # subprocesses: no lock held
        want_top = live and now - self.top_t >= TOP_S
        top = self.sampler.top_both(5) if want_top else None
        started = []
        with self._lock:
            self._last_t = now
            if full:
                self._last_full_t = now
            if top is not None:
                self.top, self.top_t = top, now
            elif not live:
                self.top = {}
            for k, v in sample.items():
                if v is not None or k in ("online",):
                    self.latest[k] = v
            if any(sample.get(k) is not None for k in ("cpu", "pressure", "swap_used", "disk_free_pct")):
                self.updated = now                      # nothing readable (or an unsupported system): no stamp
            self.recent.append({k: sample.get(k) for k in ("t", "cpu", "mem_pct", "pressure", "net_in", "net_out",
                                                          "online", "swap_used")})
            while self.recent and self.recent[0]["t"] < now - RECENT_KEEP_S:
                self.recent.popleft()
            if self.sampler.kind != "none":
                started = self.engine.feed(sample, now)
            if full and sample.get("mem_pct") is not None and now - self._last_hist_t >= HISTORY_STEP_S - 2:
                self._last_hist_t = now
                self.history.append([int(now), sample.get("cpu"), sample.get("mem_pct"),
                                     PRESSURE_CODE.get(sample.get("pressure")), sample.get("swap_used"),
                                     sample.get("disk_free_pct"), sample.get("net_in"), sample.get("net_out"),
                                     None if sample.get("online") is None else bool(sample["online"])])
                del self.history[:-HISTORY_KEEP]
                self._dirty = True
            flush_due = self._dirty and now - self._last_flush_t >= HISTORY_FLUSH_S
        if flush_due:
            self.flush()
        for ev in started:
            if ev.notify:
                self._notify_event(ev, now)
        self.push_level(now)

    def _notify_event(self, ev, now):
        try:
            with self._lock:
                text = self.engine.reason(ev.rule, now, self.lang)
            self.notify("ShellBot", text)
        except Exception:
            _swallow("office_ops.notify")

    def push_level(self, now=None):
        """Tell the page when the level or the event changed (not every minute the duration grows)."""
        now = self.clock() if now is None else now
        snap = self.state(now)
        key = (snap["level"], snap["since"])
        with self._lock:
            if key == self._pushed:
                return
            first = self._pushed is None
            self._pushed = key
        if self.on_level and not (first and snap["level"] in ("ok", "none")):
            try:
                self.on_level(snap)
            except Exception:
                _swallow("office_ops.on_level")

    # ── what the rest of the app reads ──
    def state(self, now=None, lang=None) -> dict:
        """{"level", "reason", "since"}: cheap, memory only. level is none until there is a reading."""
        now = self.clock() if now is None else now
        lang = lang or self.lang
        with self._lock:
            if self.sampler.kind == "none" or self.updated is None:
                return {"level": "none", "reason": "", "since": None, "muted_until": None, "under": "none"}
            st = self.engine.status(now, lang)
        whole = (lambda v: None if v is None else int(v))              # epoch seconds, whole
        return {"level": st["level"], "reason": st["reason"], "since": whole(st["since"]),
                "muted_until": whole(st["muted_until"]), "under": st["under"], "rule": st["rule"]}

    def summary(self, lang) -> dict:
        now = self.clock()
        st = self.state(now, lang)
        zh = lang == "zh"
        t = (lambda en, zhs: zhs if zh else en)
        level = st["level"]
        with self._lock:
            latest = dict(self.latest)
            updated = self.updated
            fresh = bool(self.history) and updated is not None and updated - self.history[-1][0] < 60
            spark = [r[2] for r in self.history[-12 if fresh else -11:] if r[2] is not None]
        if latest.get("mem_pct") is not None and not fresh:
            spark.append(latest["mem_pct"])
        if level == "none":
            hint = t("No readings yet", "還沒有資料") if ops_sampler.supported() else t("Not supported on this system yet", "這個系統還不支援")
        elif level == "muted":
            hint = t(f"Muted until {fmt_clock(st['muted_until'])}", f"已靜音到 {fmt_clock(st['muted_until'])}")
        elif level == "ok":
            hint = t("All normal", "一切正常")
        else:
            hint = st["reason"]
        return {"badge": 1 if level == "alert" else 0, "hint": hint, "level": level,
                "reason": st["reason"] if level in ("warn", "alert") else "",
                "since": st["since"], "muted_until": st["muted_until"], "updated": updated,
                "face": {"cpu": latest.get("cpu"), "mem": latest.get("mem_pct"), "pressure": latest.get("pressure"),
                         "net_in": latest.get("net_in"), "net_out": latest.get("net_out"), "online": latest.get("online"),
                         "disk_free_pct": latest.get("disk_free_pct"), "spark": spark[-12:]}}

    # ── actions ──
    def act(self, action_id, args, lang) -> dict:
        zh = lang == "zh"
        t = (lambda en, zhs: zhs if zh else en)
        now = self.clock()
        args = args if isinstance(args, dict) else {}
        if action_id == "mute":
            try:
                minutes = int(args.get("minutes", 60))
            except (TypeError, ValueError):
                minutes = 60
            with self._lock:
                until = self.engine.mute(max(1, min(MUTE_MAX_MIN, minutes)), now)
            msg = t(f"Muted until {fmt_clock(until)}", f"已靜音到 {fmt_clock(until)}")
        elif action_id == "unmute":
            with self._lock:
                self.engine.unmute()
            msg = t("Unmuted", "已取消靜音")
        elif action_id == "set_rule":
            rid = str(args.get("id") or "")
            with self._lock:
                ok = self.engine.set_rule(rid, args.get("on") if isinstance(args.get("on"), bool) else None,
                                          args.get("value") if _num(args.get("value")) is not None else None)
            if not ok:
                return {"ok": False, "message": t("No such rule", "沒有這條規則")}
            msg = t("Rule saved", "規則已存")
        elif action_id == "top_sort":
            by = args.get("by")
            if by not in TOP_SORTS:
                return {"ok": False, "message": t("No such order", "沒有這種排序")}
            with self._lock:
                self.top_sort = by
            msg = t("Top 5 order saved", "前 5 名排序已存")
        elif action_id == "reset_rules":
            with self._lock:
                self.engine.reset_rules()
            msg = t("Rules back to defaults", "規則已還原預設")
        else:
            return {"ok": False, "message": oo.words("bad_action", lang)}
        self._save_rules()
        self.push_level(now)
        return {"ok": True, "message": msg, "refresh": True}

    # ── the detail screen ──
    def _cells_pressure(self, now, rows, start, step, count):
        """Cells of ok / warn / alert / none: each is the newest reading at or before it, if that one is
        not older than HOLD_S (a normal-mode reading every 5 minutes still colours the strip)."""
        word = {"normal": "ok", "warn": "warn", "critical": "alert"}
        out, i = [], 0
        for n in range(count):
            end = start + (n + 1) * step
            while i + 1 < len(rows) and rows[i + 1][0] <= end:
                i += 1
            cell = "none"
            if rows and rows[i][0] <= end and end - rows[i][0] <= HOLD_S + step:
                cell = word.get(rows[i][1], "none")
            out.append(cell)
        return out

    def view(self, lang) -> dict:
        now = self.clock()
        zh = lang == "zh"
        t = (lambda en, zhs: zhs if zh else en)
        L = "zh" if zh else "en"
        with self._lock:
            latest = dict(self.latest)
            recent = list(self.recent)
            history = list(self.history)
            top = {k: list(v) for k, v in self.top.items()}
            top_sort = self.top_sort
            cfg = {k: dict(v) for k, v in self.engine.cfg.items()}
            rise = self.engine.swap_rise
            active = {a["rule"] for a in self.engine.active()}
            st = self.engine.status(now, L)
            have = self.updated is not None
        out = {"success": True, "refresh_ms": 2000, "title": t("Ops board", "維運戰情")}
        under = st["under"] if have and self.sampler.kind != "none" else "none"
        if not ops_sampler.supported():
            text = t("This system is not supported yet", "這個系統還不支援維運取樣")
        elif not have:
            text = t("No readings yet", "還沒有資料")
        elif under == "ok":
            text = t("All normal", "一切正常")
        else:
            text = st["reason"]
        if st["muted_until"]:
            text += t(f" (muted until {fmt_clock(st['muted_until'])})", f"（已靜音到 {fmt_clock(st['muted_until'])}）")
        out["banner"] = {"level": under, "text": text, "since": st["since"], "rule": st["rule"]}
        sections = []
        if have and self.sampler.kind != "none":
            sections += self._sections(latest, recent, history, top, top_sort, cfg, rise, active, now, t)
        sections.append({"id": "rules", "title": t("What counts as abnormal", "什麼算異常"), "widgets": [
            {"kind": "rules", "items": self._rule_items(cfg, L)}]})
        out["sections"] = sections
        return out

    @staticmethod
    def _rule_items(cfg, L):
        items = []
        for sid in ops_rules.IDS:
            s = ops_rules.spec(sid)
            items.append({"id": sid, "label": s["label"][L], "on": cfg[sid]["on"], "value": cfg[sid]["value"],
                          "unit": s["unit"][L], "level": s["level"], "min": s["min"], "max": s["max"], "step": s["step"]})
        return items

    def _sections(self, latest, recent, history, top, top_sort, cfg, rise, active, now, t):
        sections = []
        # CPU
        cpu = latest.get("cpu")
        cores = latest.get("cores") or []
        widgets = []
        if cpu is not None:
            widgets.append({"kind": "gauge", "label": t("All cores", "全部核心"), "value": cpu, "text": f"{cpu:.0f}%",
                            "tone": _pct_tone(cpu)})
        if cores:
            p, e = latest.get("cores_p") or 0, latest.get("cores_e") or 0
            if p and e and p + e == len(cores):      # Apple Silicon: the efficiency cores come first
                items = [{"label": f"P{i + 1}", "value": v, "group": "p"} for i, v in enumerate(cores[e:])]
                items += [{"label": f"E{i + 1}", "value": v, "group": "e"} for i, v in enumerate(cores[:e])]
            else:
                items = [{"label": str(i + 1), "value": v, "group": ""} for i, v in enumerate(cores)]
            widgets.append({"kind": "bars", "label": t("Each core", "每顆核心"), "items": items})
        widgets.append({"kind": "spark", "label": t("Last 60 seconds", "最近 60 秒"), "unit": "%", "max": 100,
                        "series": [{"label": "CPU", "values": self._series(recent, "cpu", now, 60, 2), "tone": "info"}]})
        sections.append({"id": "cpu", "title": "CPU", "widgets": widgets})
        # memory
        widgets = []
        total = latest.get("mem_total")
        if total and latest.get("mem_pct") is not None:
            parts = [(t("App", "App"), latest.get("mem_app"), "info"), (t("Wired", "固定"), latest.get("mem_wired"), "warn"),
                     (t("Compressed", "壓縮"), latest.get("mem_compressed"), "dim"), (t("Cached", "快取"), latest.get("mem_cached"), "")]
            widgets.append({"kind": "stack", "label": t(f"Used {latest['mem_pct']:.0f}%", f"已用 {latest['mem_pct']:.0f}%"),
                            "total": total, "parts": [{"label": a, "value": b, "tone": c} for a, b, c in parts if b]})
        pr = [(r["t"], r["pressure"]) for r in recent if r.get("pressure")]
        widgets.append({"kind": "strip", "label": t("Pressure (last 30 minutes)", "壓力（最近 30 分鐘）"),
                        "start": int(now - 1800), "step_s": 30, "cells": self._cells_pressure(now, pr, now - 1800, 30, 60)})
        if latest.get("swap_used") is not None:
            widgets.append({"kind": "stat", "label": t("Swap", "交換區"), "value": fmt_bytes(latest["swap_used"]),
                            "sub": t(f"30 min +{rise / 1024 ** 3:.1f} GB", f"30 分鐘 +{rise / 1024 ** 3:.1f} GB"),
                            "tone": "warn" if "swap_growth" in active else ""})
        sections.append({"id": "mem", "title": t("Memory", "記憶體"), "widgets": widgets})
        # network and disk
        widgets = []
        if latest.get("net_in") is not None:
            ins, outs = self._series(recent, "net_in", now, 240, 4), self._series(recent, "net_out", now, 240, 4)
            kb = lambda v: None if v is None else round(v / 1024, 1)
            widgets.append({"kind": "spark", "label": t("Last 4 minutes", "最近 4 分鐘"), "unit": "KB/s", "max": None,
                            "series": [{"label": t("Download", "下載"), "values": [kb(v) for v in ins], "tone": "info"},
                                       {"label": t("Upload", "上傳"), "values": [kb(v) for v in outs], "tone": "ok"}]})
        online = latest.get("online")
        widgets.append({"kind": "stat", "label": t("Connection", "連線"),
                        "value": t("Connected", "已連線") if online else t("No route out", "沒有連線") if online is False else t("Unknown", "未知"),
                        "sub": t(f"Download {fmt_rate(latest.get('net_in'))} · Upload {fmt_rate(latest.get('net_out'))}",
                                 f"下載 {fmt_rate(latest.get('net_in'))} · 上傳 {fmt_rate(latest.get('net_out'))}"),
                        "tone": "ok" if online else "warn" if online is False else "dim"})
        free = latest.get("disk_free_pct")
        if free is not None:
            line = cfg["disk_low"]["value"]
            widgets.append({"kind": "stat", "label": t("Free disk", "磁碟剩餘"), "value": f"{free:.0f}%",
                            "sub": fmt_bytes(latest.get("disk_free")),
                            "tone": "bad" if free < line else "warn" if free < line * 1.5 else "ok"})
        sections.append({"id": "net", "title": t("Network and disk", "網路與磁碟"), "widgets": widgets})
        # top five
        by, why = self.top_order(top_sort, latest)
        rows = [[(r["name"] + (f" ×{r['count']}" if r.get("count", 1) > 1 else "")), f"{r['cpu']:.0f}%", fmt_bytes(r["rss"])]
                for r in (top.get(by) or [])[:5]]
        names = {"auto": t("Auto", "自動"), "cpu": "CPU", "mem": t("Memory", "記憶體")}
        sections.append({"id": "top", "title": t("Top 5 by CPU", "吃資源前 5 名・照 CPU") if by == "cpu"
                         else t("Top 5 by memory", "吃資源前 5 名・照記憶體"), "widgets": [
            {"kind": "table", "columns": [t("Program", "程式"), "CPU", t("Memory", "記憶體")], "rows": rows,
             "switch": {"action": "top_sort", "value": top_sort, "options": [{"id": k, "label": names[k]} for k in TOP_SORTS],
                        "note": self._top_note(top_sort, by, why, latest, t)}}]})
        # the last 24 hours
        end = int(now // HISTORY_STEP_S)
        start = (end - HISTORY_KEEP + 1) * HISTORY_STEP_S
        cells = ["none"] * HISTORY_KEEP
        word = {0: "ok", 1: "warn", 2: "alert"}
        rank = {"none": 0, "ok": 1, "warn": 2, "alert": 3}
        for r in history:
            idx = int(r[0] // HISTORY_STEP_S) - (end - HISTORY_KEEP + 1)
            if 0 <= idx < HISTORY_KEEP:
                c = word.get(r[3], "none")
                if rank[c] > rank[cells[idx]]:
                    cells[idx] = c
        sections.append({"id": "day", "title": t("Last 24 hours", "最近 24 小時"), "widgets": [
            {"kind": "strip", "label": t("Memory pressure (every 5 minutes)", "記憶體壓力（每 5 分鐘）"),
             "start": start, "step_s": HISTORY_STEP_S, "cells": cells}]})
        return sections

    @staticmethod
    def top_order(top_sort, latest):
        """(order, why): "cpu" or "mem", and for "auto" which condition chose memory ("pressure", "mem", "swap"; "" when CPU)."""
        if top_sort in ("cpu", "mem"):
            return top_sort, ""
        if latest.get("pressure") in ("warn", "critical"):
            return "mem", "pressure"
        if (latest.get("mem_pct") or 0) >= TOP_AUTO_MEM_PCT:
            return "mem", "mem"
        if (latest.get("swap_used") or 0) >= TOP_AUTO_SWAP_BYTES:
            return "mem", "swap"
        return "cpu", ""

    @staticmethod
    def _top_note(top_sort, by, why, latest, t):
        if top_sort != "auto":
            return ""
        if by == "cpu":
            return t("Now ranked by CPU", "現在照 CPU 排")
        reason = {"pressure": t("memory pressure is up", "記憶體壓力偏高"),
                  "mem": t(f"memory {latest.get('mem_pct') or 0:.0f}% used", f"記憶體已用 {latest.get('mem_pct') or 0:.0f}%"),
                  "swap": t(f"swap {fmt_bytes(latest.get('swap_used'))}", f"交換區已用 {fmt_bytes(latest.get('swap_used'))}")}[why]
        return t(f"Now ranked by memory ({reason})", f"現在照記憶體排（{reason}）")

    @staticmethod
    def _series(recent, key, now, span, step):
        """`span` seconds in cells of `step` seconds, oldest first: the newest reading in each cell, else None."""
        n = int(span // step)
        out = [None] * n
        base = now - span
        for r in recent:
            v = r.get(key)
            if v is None:
                continue
            idx = int((r["t"] - base) // step)
            if 0 <= idx < n:
                out[idx] = v
        return out


# ── the built-in object ───────────────────────────────────────────────────────
class Ops(oo.OfficeObject):
    declaration = DECLARATION

    def visible(self) -> bool:
        return _enabled

    def summary(self, ctx) -> dict:
        return collector().summary(ctx.lang)

    def view(self, ctx) -> dict:
        return collector().view(ctx.lang)

    def action(self, ctx, item_id, action_id, args) -> dict:
        return collector().act(action_id, args, ctx.lang)


_enabled = False
_collector = None
_cl = threading.Lock()
_cfg_lock = threading.Lock()          # configure() only; collector() takes _cl


def collector() -> Collector:
    global _collector
    with _cl:
        if _collector is None:
            _collector = Collector()
        return _collector


def set_collector(c) -> None:
    """Replace the collector (a test's, built over fake readings and its own folder)."""
    global _collector
    with _cl:
        _collector = c


def enabled() -> bool:
    return _enabled


def configure(on, on_level=None, tell=True) -> None:
    """Make the collector match `settings.experimental_ops`: started while on, stopped (and the record written)
    when it goes off, and (`tell`) the page told the level is gone; the app's exit passes tell=False. Cheap to call again with the same value; two
    callers at once (the page's calls run on a thread each) cannot start two collectors."""
    global _enabled
    on = bool(on)
    with _cfg_lock:
        if on:
            c = collector()
            if on_level is not None:
                c.on_level = on_level
            _enabled = True
            c.start()
            return
        was = _enabled or (_collector is not None and _collector.running)
        _enabled = False
        c = _collector
        if c is not None and was:
            c.stop()
    if c is not None and was and tell and c.on_level:
        try:
            c.on_level({"level": "none", "reason": "", "since": None})      # the sidebar's red dot goes with the switch
        except Exception:
            _swallow("office_ops.configure")
        with c._lock:
            c._pushed = None
