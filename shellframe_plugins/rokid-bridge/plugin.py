"""
Rokid Bridge plugin — adds a 👁 badge to whichever session row Rokid
Glasses is currently injecting into, plus a settings tab summarising
state and exposing common actions.

Reads (does not write) ShellFrame state and the bridge state files:
    ~/.config/shellframe/config.json     (last_active_tab — same source
                                           the bridge listener uses)
    ~/.claude/channels/rokid-bridge/target.txt   (manual override)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make the SDK importable regardless of how main.py launched us.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plugin_sdk import SFPlugin, PluginHostAPI


CHANNEL_DIR = Path.home() / ".claude" / "channels" / "rokid-bridge"
TARGET_FILE = CHANNEL_DIR / "target.txt"
SF_CONFIG = Path.home() / ".config" / "shellframe" / "config.json"
PICKER_TTL_S = 5 * 60


def _current_target_sid() -> str:
    """Mirror listen.ts currentTargetSid() so the badge points at exactly
    the same session the inject pipeline will hit."""
    # Picker override within TTL.
    try:
        st = TARGET_FILE.stat()
        if st.st_size > 0 and (st.st_mtime + PICKER_TTL_S) > __import__("time").time():
            sid = TARGET_FILE.read_text(encoding="utf-8").strip()
            if sid:
                return sid
    except FileNotFoundError:
        pass
    except Exception:
        pass
    # ShellFrame UI tab.
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

    def sidebar_badge(self, sid: str) -> str:
        if sid == _current_target_sid():
            return '<span class="sb-rokid active" title="Rokid Glasses → this session">👁</span>'
        return '<span class="sb-rokid" title="not the active Rokid target">👁</span>'

    def settings_panel(self) -> str:
        # Render dynamic state into the static HTML template at request time.
        tmpl = (Path(__file__).parent / "settings.html").read_text(encoding="utf-8")
        target = _current_target_sid() or "(無)"
        listener_running = _proc_alive("bun") and (CHANNEL_DIR / "listen.ts").exists()
        bt_running = _proc_alive("bt_bridge")
        return (
            tmpl.replace("{{TARGET_SID}}", target)
                .replace("{{LISTENER_STATUS}}", "running" if listener_running else "stopped")
                .replace("{{BT_STATUS}}", "running" if bt_running else "stopped")
        )


def _proc_alive(name: str) -> bool:
    try:
        out = os.popen(f"pgrep -f {name}").read()
        return bool(out.strip())
    except Exception:
        return False
