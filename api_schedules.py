"""Api mixin — Loops 排程面板域（God-class 分批拆解 第一批）.

使用者自己的 LaunchAgents（cron-like jobs）列表/開關/編輯 prompt。
enabled 以 launchctl 實際 bootstrap 狀態為準（v0.22.7 起）。
"""

import json
import os
import plistlib
import subprocess
from pathlib import Path

from sf_log import _dlog, _swallow  # noqa: F401


class SchedulesApiMixin:
    # ── Scheduled jobs (LaunchAgents) for the Loops panel ──────────────
    # Surfaces the user's own recurring jobs so the Loops panel can show each
    # one's frequency and flip it on/off. Only the user's own label prefixes
    # are listed/togglable — never arbitrary system agents.
    _SCHED_PREFIXES = ("com.howard.", "com.neux.", "com.claude.", "com.h2ocloud.")

    _SCHED_TITLES = {
        "com.howard.scrum-morning": "Scrum 早排卡",
        "com.howard.scrum-daily": "Scrum 日報推送",
        "com.howard.scrum-evening": "Scrum 晚收尾",
        "com.howard.plaud-trigger": "Plaud 觸發轉錄",
        "com.howard.plaud-daily": "Plaud 每日摘要",
        "com.neux.tech-digest": "科技日報",
        "com.neux.tmux-groom": "tmux 整理",
        "com.neux.femas.clockin": "FEMAS 上班打卡",
        "com.neux.femas.clockout": "FEMAS 下班打卡",
    }

    @staticmethod
    def _launch_agents_dir() -> Path:
        return Path.home() / "Library" / "LaunchAgents"

    @staticmethod
    def _sched_freq(p: dict) -> str:
        """Human-readable cadence from a LaunchAgent plist."""
        if "StartInterval" in p:
            s = int(p["StartInterval"])
            if s >= 3600:
                return f"每 {s // 3600}h"
            if s >= 60:
                return f"每 {s // 60}m"
            return f"每 {s}s"
        sci = p.get("StartCalendarInterval")
        if sci:
            items = sci if isinstance(sci, list) else [sci]
            wd = ["日", "一", "二", "三", "四", "五", "六"]
            out = []
            for it in items:
                h, m = it.get("Hour"), it.get("Minute")
                t = (f"{h:02d}:{m:02d}" if h is not None and m is not None
                     else (f":{m:02d}" if m is not None else ""))
                pre = ""
                if it.get("Weekday") is not None:
                    pre = f"週{wd[it['Weekday'] % 7]} "
                out.append((pre + t).strip())
            return "每天 " + " · ".join(out)
        if p.get("RunAtLoad"):
            return "登入常駐"
        return "—"

    def _sched_loaded_map(self):
        """Jobs actually bootstrapped in launchd, from `launchctl list`:
        label -> (pid, last_exit). A plist that's on disk but not in here was
        never loaded (or was booted out) — it will NOT fire, regardless of
        what the plist says. This is the ground truth the panel shows."""
        out = {}
        try:
            r = subprocess.run(["launchctl", "list"],
                               capture_output=True, text=True, timeout=5)
            for ln in r.stdout.splitlines():
                parts = ln.split("\t")
                if len(parts) != 3 or not parts[2].startswith(self._SCHED_PREFIXES):
                    continue
                pid, status, lbl = parts
                try:
                    out[lbl] = (None if pid == "-" else int(pid), int(status))
                except ValueError:
                    out[lbl] = (None, 0)
        except Exception:
            _swallow("Api._sched_loaded_map:2466")
        return out

    def schedules_list(self) -> str:
        """List the user's own *timed* LaunchAgents: id, title, frequency,
        command, enabled, last_exit. Drives the Loops panel's schedule section.
        RunAtLoad/KeepAlive daemons with no timer (e.g. the Telegram channel
        processes ShellFrame already tracks itself) are not schedules and are
        excluded. `enabled` means the job is actually bootstrapped in launchd,
        not merely present on disk."""
        try:
            loaded = self._sched_loaded_map()
            items = []
            for fp in sorted(self._launch_agents_dir().glob("*.plist")):
                if not fp.stem.startswith(self._SCHED_PREFIXES):
                    continue
                try:
                    with open(fp, "rb") as f:
                        p = plistlib.load(f)
                except Exception:
                    continue
                if "StartInterval" not in p and not p.get("StartCalendarInterval"):
                    continue
                lbl = p.get("Label", fp.stem)
                prog = p.get("ProgramArguments") or (
                    [p["Program"]] if p.get("Program") else [])
                pid, last_exit = loaded.get(lbl, (None, 0))
                items.append({
                    "id": lbl,
                    "title": self._SCHED_TITLES.get(lbl, lbl.split(".")[-1]),
                    "freq": self._sched_freq(p),
                    "cmd": " ".join(str(x) for x in prog),
                    "enabled": lbl in loaded,
                    "running": pid is not None,
                    "last_exit": last_exit,
                })
            return json.dumps({"schedules": items})
        except Exception as e:
            return json.dumps({"schedules": [], "error": str(e)})

    def schedule_set_enabled(self, label: str, enabled) -> str:
        """Flip a user LaunchAgent on/off via launchctl. Guarded: the label
        must be one of the user's own agents that exists on disk."""
        try:
            if isinstance(enabled, str):
                enabled = enabled.lower() in ("1", "true", "yes", "on")
            fp = self._launch_agents_dir() / f"{label}.plist"
            if not label.startswith(self._SCHED_PREFIXES) or not fp.exists():
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
            actual = label in self._sched_loaded_map()
            return json.dumps({"success": actual == bool(enabled),
                               "enabled": actual})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    @staticmethod
    def _schedule_script_path(prog) -> str:
        exts = (".sh", ".py", ".js", ".mjs", ".ts", ".exp", ".rb", ".zsh", ".bash")
        for a in prog:
            if str(a).endswith(exts):
                return str(a)
        return ""

    def schedule_prompt(self, label: str, sid: str = "") -> str:
        """Collect a schedule's paths/command into a prompt and drop it into the
        given session (the user's current tab) — no new tab. Pasted unsent so
        the user can append their request and send it themselves."""
        try:
            fp = self._launch_agents_dir() / f"{label}.plist"
            if not label.startswith(self._SCHED_PREFIXES) or not fp.exists():
                return json.dumps({"success": False, "message": "unknown schedule"})
            session = self.sessions.get(sid)
            if not session:
                return json.dumps({"success": False, "message": "no active session"})
            with open(fp, "rb") as f:
                p = plistlib.load(f)
            prog = p.get("ProgramArguments") or ([p["Program"]] if p.get("Program") else [])
            script = self._schedule_script_path(prog)
            title = self._SCHED_TITLES.get(label, label.split(".")[-1])
            freq = self._sched_freq(p)
            prompt = (
                f"我想調整這個排程：{title}（{label}），目前頻率：{freq}。\n"
                f"- LaunchAgent plist：{fp}\n"
                + (f"- 執行腳本：{script}\n" if script else "")
                + f"- 完整指令：{' '.join(str(x) for x in prog)}\n\n"
                "請先讀上面的檔案，再依我接下來的要求調整；改完 plist 記得提醒我重新載入"
                f"（launchctl bootout/bootstrap gui/{os.getuid()}）才生效。要調整的是："
            )
            session._startup_trust_pending = False
            self._send_text_to_session(session, prompt, submit=False)
            return json.dumps({"success": True, "sid": sid})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})
