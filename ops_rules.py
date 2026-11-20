"""The ops TV's rules: what counts as abnormal, and the state machine that turns a stream of samples into
events. Pure logic: no clock of its own (the caller passes the time of each sample), no thread, no file,
no import of anything but the standard library. Leaf module.

The default rules (the office design's table; each can be switched off and its number changed within
min..max, which is also all a saved value can say):

  mem_pressure_high   memory pressure "critical" for N minutes            alert   10 min
  mem_pressure_warn   memory pressure "warn" or "critical" for N minutes  warn    30 min
  cpu_high            average of all cores over 90 % for N minutes        alert   5 min
  swap_growth         swap used grew by more than N GB within 30 minutes  warn    2 GB
  disk_low            free disk under N percent                           alert   10 %
  net_down            no route out for N minutes (off by default: a laptop is often offline)  warn  2 min

One event per thing: an event starts when a rule's condition has held for its duration, and its `since` is
the time of the FIRST sample over the line (not the moment the duration was reached). A sample that is
under the line does not end it: it ends after RECOVER_SAMPLES under-the-line samples in a row (the
hysteresis that stops a value hovering at the line from flapping). The same count bridges a short dip while an
event is still building up. A sample whose field is None (could not be read) changes nothing. A gap longer
than `max_gap` seconds between two samples (the computer slept, the app was away) restarts a building
event, so sleeping does not count as "10 minutes of high pressure"; a running event stays until it recovers.
"""
from collections import deque

RECOVER_SAMPLES = 3
SWAP_WINDOW_S = 1800
SWAP_KEEP_EVERY_S = 10.0
CPU_LINE = 90.0
GB = 1024 ** 3

# id, level, on by default, default, min, max, step, unit (en, zh), label (en, zh)
SPECS = (
    ("mem_pressure_high", "alert", True, 10, 1, 120, 1, ("min", "分鐘"),
     ("Memory pressure is high for", "記憶體壓力「高」連續")),
    ("mem_pressure_warn", "warn", True, 30, 1, 240, 1, ("min", "分鐘"),
     ("Memory pressure is a warning or worse for", "記憶體壓力「警告」以上連續")),
    ("cpu_high", "alert", True, 5, 1, 120, 1, ("min", "分鐘"),
     ("All cores are over 90% for", "CPU 全部核心超過 90% 連續")),
    ("swap_growth", "warn", True, 2, 1, 64, 1, ("GB in 30 min", "GB（30 分鐘內）"),
     ("Swap used grows by more than", "交換區增加超過")),
    ("disk_low", "alert", True, 10, 1, 50, 1, ("% free", "% 剩餘"),
     ("Free disk is under", "磁碟剩餘低於")),
    ("net_down", "warn", False, 2, 1, 120, 1, ("min", "分鐘"),
     ("There is no route out for", "網路中斷（沒有對外路由）連續")),
)
IDS = tuple(s[0] for s in SPECS)
_SPEC = {s[0]: s for s in SPECS}


def spec(rule_id):
    s = _SPEC.get(rule_id)
    if not s:
        return None
    return {"id": s[0], "level": s[1], "on": s[2], "value": s[3], "min": s[4], "max": s[5], "step": s[6],
            "unit": {"en": s[7][0], "zh": s[7][1]}, "label": {"en": s[8][0], "zh": s[8][1]}}


def _t(lang, en, zh):
    return zh if str(lang or "").lower().startswith("zh") else en


def clamp_value(rule_id, value):
    s = _SPEC[rule_id]
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        v = s[3]
    return max(s[4], min(s[5], v))


class Event:
    """A rule's event that has just started (what `Engine.feed` returns)."""
    __slots__ = ("rule", "level", "since", "notify")

    def __init__(self, rule, level, since, notify):
        self.rule, self.level, self.since, self.notify = rule, level, since, notify


class Engine:
    def __init__(self, saved=None, max_gap=900.0):
        self.max_gap = max_gap
        self.cfg = {s[0]: {"on": s[2], "value": s[3]} for s in SPECS}
        self.muted_until = None
        self._st = {i: self._blank() for i in IDS}
        self._swap = deque()                 # (t, swap used) over the last SWAP_WINDOW_S
        self._last_t = None
        self.last = {}                       # the newest sample's fields a reason text quotes
        self._swap_rise = 0
        if saved:
            self.load(saved)

    @staticmethod
    def _blank():
        return {"over": None, "ok": 0, "active": False, "since": None, "told": None}

    # ── settings ──
    def load(self, saved) -> None:
        """Rules and mute from what was saved; anything unknown or out of range is dropped or clamped."""
        if not isinstance(saved, dict):
            return
        rules = saved.get("rules")
        for rid, got in (rules.items() if isinstance(rules, dict) else ()):
            if rid in self.cfg and isinstance(got, dict):
                if isinstance(got.get("on"), bool):
                    self.cfg[rid]["on"] = got["on"]
                if "value" in got:
                    self.cfg[rid]["value"] = clamp_value(rid, got["value"])
        mu = saved.get("muted_until")
        self.muted_until = float(mu) if isinstance(mu, (int, float)) and not isinstance(mu, bool) else None

    def export(self) -> dict:
        return {"v": 1, "rules": {k: dict(v) for k, v in self.cfg.items()}, "muted_until": self.muted_until}

    def set_rule(self, rule_id, on=None, value=None) -> bool:
        """Switch a rule or change its number (clamped to its range). False for an id that is not a rule."""
        if rule_id not in self.cfg:
            return False
        c = self.cfg[rule_id]
        if isinstance(on, bool):
            c["on"] = on
        if value is not None:
            c["value"] = clamp_value(rule_id, value)
        st = self._st[rule_id]
        if not c["on"]:
            self._st[rule_id] = self._blank()
        else:                                # judge again under the new number; the first over-the-line time stays
            st["active"], st["ok"] = False, 0
        return True

    def reset_rules(self) -> None:
        for s in SPECS:
            self.cfg[s[0]] = {"on": s[2], "value": s[3]}
        self._st = {i: self._blank() for i in IDS}

    def mute(self, minutes, now) -> float:
        self.muted_until = now + max(1, min(24 * 60, int(minutes))) * 60
        return self.muted_until

    def unmute(self) -> None:
        self.muted_until = None

    def muted(self, now) -> bool:
        return self.muted_until is not None and now < self.muted_until

    # ── what a sample says about each rule: True over the line, False under it, None unknown ──
    def _judge(self, rid, sample, now):
        v = self.cfg[rid]["value"]
        if rid == "mem_pressure_high":
            p = sample.get("pressure")
            return None if p is None else p == "critical"
        if rid == "mem_pressure_warn":
            p = sample.get("pressure")
            return None if p is None else p in ("warn", "critical")
        if rid == "cpu_high":
            c = sample.get("cpu")
            return None if c is None else c > CPU_LINE
        if rid == "swap_growth":
            return None if sample.get("swap_used") is None else self._swap_rise > v * GB
        if rid == "disk_low":
            d = sample.get("disk_free_pct")
            return None if d is None else d < v
        if rid == "net_down":
            o = sample.get("online")
            return None if o is None else o is False
        return None

    def _track_swap(self, sample, now):
        used = sample.get("swap_used")
        if used is None:
            return
        if not self._swap or now - self._swap[-1][0] >= SWAP_KEEP_EVERY_S:
            self._swap.append((now, used))
        else:
            self._swap[-1] = (self._swap[-1][0], used)
        while self._swap and self._swap[0][0] < now - SWAP_WINDOW_S:
            self._swap.popleft()
        low = min([u for _, u in self._swap] + [used])
        self._swap_rise = max(0, used - low)

    def feed(self, sample, now) -> list:
        """Take one sample (a dict with the fields of ops_sampler.SAMPLE_KEYS that were read) taken at `now`;
        return the events that started with it."""
        if self._last_t is not None and now - self._last_t > self.max_gap:
            for rid, st in self._st.items():             # a gap: what was building up starts over
                if not st["active"]:
                    self._st[rid] = self._blank()
            self._swap.clear()
        self._last_t = now
        self.last = {k: sample.get(k) for k in ("cpu", "pressure", "swap_used", "disk_free_pct", "online")}
        self._track_swap(sample, now)
        started = []
        for rid in IDS:
            if not self.cfg[rid]["on"]:
                continue
            over = self._judge(rid, sample, now)
            if over is None:
                continue
            st = self._st[rid]
            if over:
                st["ok"] = 0
                if st["over"] is None:
                    st["over"] = now
                need = 0 if rid in ("swap_growth", "disk_low") else self.cfg[rid]["value"] * 60
                if not st["active"] and now - st["over"] >= need:
                    st["active"], st["since"] = True, st["over"]
                    if st["told"] != st["since"]:
                        st["told"] = st["since"]
                        level = _SPEC[rid][1]
                        started.append(Event(rid, level, st["since"], level == "alert" and not self.muted(now)))
            elif st["over"] is not None or st["active"]:
                st["ok"] += 1
                if st["ok"] >= RECOVER_SAMPLES:
                    old_told = st["told"]
                    self._st[rid] = self._blank()
                    self._st[rid]["told"] = old_told
        return started

    # ── what is going on ──
    @property
    def swap_rise(self) -> int:
        """Bytes the swap used has grown within the last 30 minutes (over its lowest point in that time)."""
        return self._swap_rise

    def pending(self) -> bool:
        """Some rule is over its line (building up or running): the collector looks every 30 s then."""
        return any(st["over"] is not None or st["active"] for st in self._st.values())

    def active(self) -> list:
        """Running events, alerts first, then the earliest."""
        rows = [{"rule": rid, "level": _SPEC[rid][1], "since": st["since"]}
                for rid, st in self._st.items() if st["active"]]
        return sorted(rows, key=lambda r: (r["level"] != "alert", r["since"] or 0))

    def reason(self, rule_id, now, lang="en") -> str:
        st = self._st.get(rule_id) or {}
        mins = max(0, int(((now - st["since"]) if st.get("since") else 0) // 60))
        last = self.last
        if rule_id == "mem_pressure_high":
            return _t(lang, f"Memory pressure high for {mins} min", f"記憶體壓力高 {mins} 分鐘")
        if rule_id == "mem_pressure_warn":
            return _t(lang, f"Memory pressure warning for {mins} min", f"記憶體壓力警告 {mins} 分鐘")
        if rule_id == "cpu_high":
            return _t(lang, f"CPU over 90% for {mins} min", f"CPU 全核心超過 90% 已 {mins} 分鐘")
        if rule_id == "swap_growth":
            gb = f"{self._swap_rise / GB:.1f}"
            return _t(lang, f"Swap grew {gb} GB in 30 min", f"交換區 30 分鐘增加 {gb} GB")
        if rule_id == "disk_low":
            pct = last.get("disk_free_pct")
            pct = f"{pct:.0f}" if isinstance(pct, (int, float)) else "?"
            return _t(lang, f"Only {pct}% of the disk is free", f"磁碟只剩 {pct}%")
        if rule_id == "net_down":
            return _t(lang, f"No network route for {mins} min", f"網路中斷 {mins} 分鐘")
        return ""

    def status(self, now, lang="en") -> dict:
        """{"level": ok|warn|alert|muted, "reason", "since", "rule", "muted_until", "active": [...]}. `level` is
        "muted" while a mute runs (the events are still tracked underneath: `under` is the level they have)."""
        act = self.active()
        under = "ok"
        if any(a["level"] == "alert" for a in act):
            under = "alert"
        elif act:
            under = "warn"
        top = act[0] if act else None
        muted = self.muted(now)
        return {"level": "muted" if muted else under, "under": under,
                "reason": self.reason(top["rule"], now, lang) if top else "",
                "since": top["since"] if top else None, "rule": top["rule"] if top else "",
                "muted_until": self.muted_until if muted else None,
                "active": [dict(a, reason=self.reason(a["rule"], now, lang)) for a in act]}
