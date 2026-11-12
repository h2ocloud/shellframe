"""Api mixin — Loops 排程面板域（God-class 分批拆解 第一批）.

使用者自己的 LaunchAgents（cron-like jobs）列表/開關/編輯 prompt。
enabled 以 launchctl 實際 bootstrap 狀態為準（v0.22.7 起）。

這裡只做 I/O：讀 plist 資料夾、`launchctl list`、平台判斷、label 檢查。
一個排程「長什麼樣子」（頻率、單次排程、分組）全在 sched_view.py，是純函式。
哪些 label 算使用者自己的、標題、單次排程的分組，是使用者的資料，放在
config 資料夾的 schedules.json（見 sched_view.read_config），不寫在程式裡。
"""

import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import sched_view
import sf_config
import sf_lang
from sf_log import _dlog, _swallow  # noqa: F401

_ = sf_lang.pick      # _(english, chinese): the prompt an agent reads follows the OS language


class SchedulesApiMixin:
    # ── Scheduled jobs (LaunchAgents) for the Loops panel ──────────────
    # Surfaces the user's own recurring jobs so the Loops panel can show each
    # one's frequency and flip it on/off. Only labels under the user's own
    # prefixes (schedules.json) are listed or togglable — never arbitrary
    # system agents, and with no prefix configured nothing is.

    @staticmethod
    def _launch_agents_dir() -> Path:
        return Path.home() / "Library" / "LaunchAgents"

    @staticmethod
    def _sched_supported() -> bool:
        """launchd is the only scheduler the panel reads so far."""
        return sys.platform == "darwin"

    @staticmethod
    def _sched_now() -> datetime:
        return datetime.now()

    @staticmethod
    def _sched_config() -> tuple:
        """(config, error) from schedules.json in the app's config folder."""
        return sched_view.read_config(sf_config.config_dir() / sched_view.CONFIG_NAME)

    def _sched_loaded_map(self, prefixes):
        """Jobs actually bootstrapped in launchd, from `launchctl list`:
        label -> (pid, last_exit). A plist that's on disk but not in here was
        never loaded (or was booted out) — it will NOT fire, regardless of
        what the plist says. This is the ground truth the panel shows.
        None when launchd could not be asked (the panel then says so rather
        than showing every job as switched off)."""
        out = {}
        try:
            r = subprocess.run(["launchctl", "list"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return None
            for ln in r.stdout.splitlines():
                parts = ln.split("\t")
                if len(parts) != 3 or not parts[2].startswith(tuple(prefixes)):
                    continue
                pid, status, lbl = parts
                try:
                    out[lbl] = (None if pid == "-" else int(pid), int(status))
                except ValueError:
                    out[lbl] = (None, 0)
        except Exception:
            _swallow("Api._sched_loaded_map:2466")
            return None
        return out

    def _sched_plists(self, prefixes) -> list:
        """[(label, plist dict, path)] for the plists under the user's prefixes, in
        file-name order. A plist that cannot be read is skipped."""
        items = []
        if not prefixes:
            return items
        for fp in sorted(self._launch_agents_dir().glob("*.plist")):
            if not fp.stem.startswith(tuple(prefixes)):
                continue
            try:
                with open(fp, "rb") as f:
                    p = plistlib.load(f)
            except Exception:
                continue
            items.append((str(p.get("Label", fp.stem)), p, fp))
        return items

    def schedules_list(self) -> str:
        """The Loops panel's schedule section: one row per timed LaunchAgent (and per
        KeepAlive daemon) under the user's prefixes, with a one-off's group shown as
        one row. `enabled` means the job is actually bootstrapped in launchd, not
        merely present on disk. `unsupported`: not macOS. `configured`: false until
        schedules.json names at least one prefix."""
        if not self._sched_supported():
            return json.dumps({"schedules": [], "unsupported": True, "platform": sys.platform})
        try:
            cfg, cfg_error = self._sched_config()
            prefixes = cfg["prefixes"]
            out = {"schedules": [], "unsupported": False, "configured": bool(prefixes),
                   "config_error": cfg_error, "launchd_ok": True}
            if not prefixes:
                return json.dumps(out)
            loaded = self._sched_loaded_map(prefixes)
            out["launchd_ok"] = loaded is not None
            items = [(lbl, p) for lbl, p, _fp in self._sched_plists(prefixes)]
            out["schedules"] = sched_view.build_rows(items, loaded or {}, cfg, self._sched_now())
            return json.dumps(out)
        except Exception as e:
            return json.dumps({"schedules": [], "error": str(e)})

    def schedule_set_enabled(self, label: str, enabled) -> str:
        """Flip a user LaunchAgent on/off via launchctl. Guarded: the label
        must be one of the user's own agents that exists on disk."""
        try:
            if isinstance(enabled, str):
                enabled = enabled.lower() in ("1", "true", "yes", "on")
            prefixes = tuple(self._sched_config()[0]["prefixes"])
            if not self._sched_supported() or not sched_view.valid_label(label) \
                    or not prefixes or not label.startswith(prefixes):
                return json.dumps({"success": False, "message": "unknown schedule"})
            fp = self._launch_agents_dir() / f"{label}.plist"
            if not fp.exists():
                return json.dumps({"success": False, "message": "unknown schedule"})
            dom = f"gui/{os.getuid()}"
            if enabled:
                subprocess.run(["launchctl", "enable", f"{dom}/{label}"],
                               capture_output=True, text=True, timeout=10)
                subprocess.run(["launchctl", "bootstrap", dom, str(fp)],
                               capture_output=True, text=True, timeout=10)
            else:
                subprocess.run(["launchctl", "bootout", f"{dom}/{label}"],
                               capture_output=True, text=True, timeout=10)
                subprocess.run(["launchctl", "disable", f"{dom}/{label}"],
                               capture_output=True, text=True, timeout=10)
            # Report what launchd actually did, not what we asked for — a
            # failed bootstrap (bad plist, path gone) must show as still-off.
            actual = label in (self._sched_loaded_map(prefixes) or {})
            return json.dumps({"success": actual == bool(enabled),
                               "enabled": actual})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def _sched_prompt_text(self, row: dict, plists: dict) -> str:
        """The paragraph that asks an agent to adjust one schedule (or a group of
        one-offs). `plists`: label -> (plist dict, path)."""
        members = row.get("members") or [row]
        lines = []
        for m in members:
            p, fp = plists[m["id"]]
            prog = sched_view.program_args(p)
            lines.append(_(f"- {m['id']}: plist {fp}; command {' '.join(prog)}",
                           f"- {m['id']}：plist {fp}；指令 {' '.join(prog)}"))
        script = row.get("script") or ""
        when = row["when"]
        head = (_(f"I want to adjust this group of one-off schedules: {row['title']} "
                  f"({len(members)} jobs), now: {when['en']}.\n",
                  f"我想調整這一組單次排程：{row['title']}（{len(members)} 個），目前狀態：{when['zh']}。\n")
                if row.get("group") else
                _(f"I want to adjust this schedule: {row['title']} ({row['id']}), now: {when['en']}.\n",
                  f"我想調整這個排程：{row['title']}（{row['id']}），目前頻率：{when['zh']}。\n"))
        once = ""
        if row["kind"] == "once":
            once = _("This is a one-off schedule: launchd repeats a month-and-day every year, so unload it "
                     "(bootout) after it has run, or it runs again on the same day next year.\n",
                     "這是單次排程：launchd 的 Month／Day 每年都會觸發，跑完要解除（bootout），"
                     "不然隔年同一天會再跑一次。\n")
        return (head + "\n".join(lines) + "\n"
                + (_(f"- Script: {script}\n", f"- 執行腳本：{script}\n") if script else "")
                + once + "\n"
                + _(f"Read the files above first, then make the change I ask for next. After editing a plist, "
                    f"remind me to reload it (launchctl bootout/bootstrap gui/{os.getuid()}) for it to take "
                    f"effect. What to change: ",
                    f"請先讀上面的檔案，再依我接下來的要求調整；改完 plist 記得提醒我重新載入"
                    f"（launchctl bootout/bootstrap gui/{os.getuid()}）才生效。要調整的是："))

    def schedule_prompt(self, label: str, sid: str = "") -> str:
        """Collect a schedule's paths/command into a prompt and drop it into the
        given session (the user's current tab) — no new tab. Pasted unsent so
        the user can append their request and send it themselves. `label` is a
        schedule's label, or a one-off group's id (`group:…`) from the panel."""
        try:
            if not self._sched_supported():
                return json.dumps({"success": False, "message": "unknown schedule"})
            cfg, _err = self._sched_config()
            prefixes = tuple(cfg["prefixes"])
            if not prefixes:
                return json.dumps({"success": False, "message": "unknown schedule"})
            plists = {lbl: (p, fp) for lbl, p, fp in self._sched_plists(prefixes)}
            rows = sched_view.build_rows([(lbl, p) for lbl, (p, _fp) in plists.items()],
                                         (self._sched_loaded_map(prefixes) or {}), cfg, self._sched_now())
            row = next((r for r in rows if r["id"] == label), None)
            if row is None:
                return json.dumps({"success": False, "message": "unknown schedule"})
            session = self.sessions.get(sid)
            if not session:
                return json.dumps({"success": False, "message": "no active session"})
            session._startup_trust_pending = False
            self._send_text_to_session(session, self._sched_prompt_text(row, plists), submit=False)
            return json.dumps({"success": True, "sid": sid})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})
