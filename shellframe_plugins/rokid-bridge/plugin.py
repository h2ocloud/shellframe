"""
Rokid Bridge plugin — adds a 👓 badge to whichever session row Rokid
Glasses is currently injecting into, plus a settings tab summarising
state and exposing connect / restart actions.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Make the SDK importable regardless of how main.py launched us.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plugin_sdk import SFPlugin, PluginHostAPI


CHANNEL_DIR = Path.home() / ".claude" / "channels" / "rokid-bridge"
TARGET_FILE = CHANNEL_DIR / "target.txt"
SF_CONFIG = Path.home() / ".config" / "shellframe" / "config.json"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.h2ocloud.rokid-bridge-listener.plist"
PICKER_TTL_S = 5 * 60


def _proc_alive(name: str) -> bool:
    try:
        out = subprocess.check_output(["pgrep", "-f", name], stderr=subprocess.DEVNULL)
        return bool(out.strip())
    except Exception:
        return False


def _current_target_sid() -> str:
    """Mirror listen.ts currentTargetSid()."""
    try:
        st = TARGET_FILE.stat()
        if st.st_size > 0 and (st.st_mtime + PICKER_TTL_S) > time.time():
            sid = TARGET_FILE.read_text(encoding="utf-8").strip()
            if sid:
                return sid
    except Exception:
        pass
    try:
        cfg = json.loads(SF_CONFIG.read_text(encoding="utf-8"))
        sid = cfg.get("last_active_tab") or ""
        if isinstance(sid, str) and sid.startswith("s"):
            return sid
    except Exception:
        pass
    return ""


class Plugin(SFPlugin):
    def on_load(self, api: PluginHostAPI) -> None:
        self.api = api

    # ── sidebar ──

    def sidebar_badge(self, sid: str) -> str:
        if sid == _current_target_sid():
            return '<span class="sb-rokid active" title="Rokid Glasses → this session">👓</span>'
        return '<span class="sb-rokid" title="not the active Rokid target">👓</span>'

    # ── settings panel ──

    def settings_panel(self) -> str:
        tmpl = (Path(__file__).parent / "settings.html").read_text(encoding="utf-8")
        target = _current_target_sid() or "(無)"
        listener = "running" if self._listener_alive() else "stopped"
        bt = "running" if _proc_alive("bt_bridge") else "stopped"
        manual = ""
        try:
            if TARGET_FILE.exists():
                manual = TARGET_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return (
            tmpl.replace("{{TARGET_SID}}", target)
                .replace("{{LISTENER_STATUS}}", listener)
                .replace("{{BT_STATUS}}", bt)
                .replace("{{MANUAL_TARGET}}", manual)
        )

    def _listener_alive(self) -> bool:
        try:
            out = subprocess.check_output(
                ["launchctl", "list"], stderr=subprocess.DEVNULL, text=True
            )
            for line in out.split("\n"):
                if "com.h2ocloud.rokid-bridge-listener" in line:
                    cols = line.split()
                    return cols[0].isdigit() and int(cols[0]) > 0
        except Exception:
            pass
        return False

    # ── actions (called from settings.html buttons) ──

    def action(self, name: str, args: dict) -> dict:
        try:
            return getattr(self, f"_act_{name}")(args)
        except AttributeError:
            return {"ok": False, "message": f"unknown action: {name}"}

    def _act_restart_listener(self, _args) -> dict:
        try:
            if not LAUNCH_AGENT.exists():
                return {"ok": False, "message": "LaunchAgent not installed — run mac/install.sh"}
            uid = os.getuid()
            subprocess.check_call(
                ["launchctl", "kickstart", "-k", f"gui/{uid}/com.h2ocloud.rokid-bridge-listener"]
            )
            return {"ok": True, "message": "listener restarted"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _act_start_bt(self, args) -> dict:
        device = (args.get("device") or "Glasses_0352").strip()
        port = str(args.get("port") or "9877").strip()
        bin_path = Path(__file__).parent / "mac" / "bt-bridge" / "bt_bridge"
        if not bin_path.exists():
            return {"ok": False, "message": f"bt_bridge binary missing at {bin_path}; run mac/bt-bridge/build.sh"}
        if _proc_alive("bt_bridge"):
            return {"ok": False, "message": "bt_bridge already running"}
        try:
            log = open(Path.home() / ".local" / "logs" / "rokid-bt-bridge.log", "a")
            subprocess.Popen(
                [str(bin_path), device, port],
                stdout=log, stderr=log, start_new_session=True,
            )
            return {"ok": True, "message": f"bt_bridge spawned ({device} → :{port})"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _act_stop_bt(self, _args) -> dict:
        try:
            subprocess.call(["pkill", "-f", "bt_bridge"])
            return {"ok": True, "message": "bt_bridge killed"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _act_set_target(self, args) -> dict:
        sid = (args.get("sid") or "").strip()
        if not sid.startswith("s"):
            return {"ok": False, "message": "sid must look like s12"}
        try:
            CHANNEL_DIR.mkdir(parents=True, exist_ok=True)
            TARGET_FILE.write_text(sid + "\n", encoding="utf-8")
            return {"ok": True, "message": f"manual target set: {sid} (5 min TTL)"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _act_clear_target(self, _args) -> dict:
        try:
            if TARGET_FILE.exists():
                TARGET_FILE.unlink()
            return {"ok": True, "message": "manual target cleared — falls back to UI tab"}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _act_run_install(self, _args) -> dict:
        script = Path(__file__).parent / "mac" / "install.sh"
        if not script.exists():
            return {"ok": False, "message": "mac/install.sh not found"}
        try:
            out = subprocess.check_output(
                ["bash", str(script)], stderr=subprocess.STDOUT, text=True, timeout=60
            )
            return {"ok": True, "message": out[-400:]}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "message": e.output[-400:]}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def _act_send_test(self, args) -> dict:
        text = (args.get("text") or "Mac → glasses test ✓")[:160]
        try:
            outbox = CHANNEL_DIR / "outbox"
            outbox.mkdir(parents=True, exist_ok=True)
            (outbox / f"{int(time.time()*1000)}.txt").write_text(text, encoding="utf-8")
            return {"ok": True, "message": "queued to glasses HUD"}
        except Exception as e:
            return {"ok": False, "message": str(e)}
