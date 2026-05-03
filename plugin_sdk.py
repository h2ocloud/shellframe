"""
ShellFrame plugin SDK — minimal v1.

Plugin layout:
    ~/.local/apps/shellframe/plugins/<name>/
        manifest.json           required: name, version, [description, repo_url, capabilities]
        plugin.py               optional: defines class Plugin(SFPlugin)
        settings.html           optional: injected as a tab in Settings
        sidebar.js              optional: per-session badge renderer
        styles.css              optional: appended to the page <style> stack

Hooks (override in subclass; all optional, all best-effort):
    on_load(api)                — called once after plugin import
    on_session_change(sid)      — called when active tab changes
    on_session_open(sid, label) — called when a new session is registered
    on_session_close(sid)       — called when a session ends
    sidebar_badge(sid) -> str   — return HTML snippet for session row badge
                                  (called once per render per session)
    settings_panel() -> str     — return HTML for Settings tab body (cached)

The host (main.py) calls these in best-effort try/except blocks so a buggy
plugin never crashes the app.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.0"
    description: str = ""
    repo_url: str = ""
    capabilities: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class SFPlugin:
    """Subclass me. All hooks are optional — override what you need."""

    manifest: PluginManifest

    def __init__(self, manifest: PluginManifest, root: Path):
        self.manifest = manifest
        self.root = root

    # ── lifecycle ──
    def on_load(self, api: "PluginHostAPI") -> None: ...

    # ── session ──
    def on_session_change(self, sid: str) -> None: ...
    def on_session_open(self, sid: str, label: str) -> None: ...
    def on_session_close(self, sid: str) -> None: ...

    # ── UI injection ──
    def sidebar_badge(self, sid: str) -> str:
        """Return a small HTML snippet rendered after the session label."""
        return ""

    def settings_panel(self) -> str:
        """Return HTML body for a tab in the Settings overlay. Falls back
        to the contents of settings.html in the plugin dir."""
        f = self.root / "settings.html"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    # ── ergonomic helpers ──
    def asset(self, name: str) -> str:
        f = self.root / name
        return f.read_text(encoding="utf-8") if f.exists() else ""


class PluginHostAPI:
    """Hand-back from the host so plugins can read state without poking
    into main.py internals."""

    def __init__(
        self,
        get_active_sid: Callable[[], str],
        list_sessions: Callable[[], list[dict]],
        send_to_session: Callable[[str, str], None],
        config_dir: Path,
    ):
        self.get_active_sid = get_active_sid
        self.list_sessions = list_sessions
        self.send_to_session = send_to_session
        self.config_dir = config_dir


class PluginRegistry:
    """Loads plugins, dispatches hooks. The host (main.py) holds one
    instance and calls dispatch_*() at the appropriate sites."""

    def __init__(self, plugin_dir: Path, api: PluginHostAPI):
        self.plugin_dir = plugin_dir
        self.api = api
        self.plugins: list[SFPlugin] = []

    def load_all(self) -> None:
        if not self.plugin_dir.exists():
            return
        for sub in sorted(self.plugin_dir.iterdir()):
            if not sub.is_dir():
                continue
            try:
                self._load_one(sub)
            except Exception as e:
                print(f"[plugins] {sub.name}: load failed: {e}")

    def _load_one(self, root: Path) -> None:
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            return
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = PluginManifest(
            name=raw.get("name", root.name),
            version=raw.get("version", "0.0.0"),
            description=raw.get("description", ""),
            repo_url=raw.get("repo_url", ""),
            capabilities=raw.get("capabilities", []),
            raw=raw,
        )

        plugin_py = root / "plugin.py"
        if plugin_py.exists():
            spec = importlib.util.spec_from_file_location(
                f"sfplugin_{manifest.name}", plugin_py
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                cls = getattr(mod, "Plugin", None)
                if cls and issubclass(cls, SFPlugin):
                    inst = cls(manifest, root)
                else:
                    inst = SFPlugin(manifest, root)
            else:
                inst = SFPlugin(manifest, root)
        else:
            # Asset-only plugin (settings.html / sidebar.js, no Python).
            inst = SFPlugin(manifest, root)

        try:
            inst.on_load(self.api)
        except Exception as e:
            print(f"[plugins] {manifest.name}: on_load failed: {e}")

        self.plugins.append(inst)
        print(f"[plugins] loaded {manifest.name} v{manifest.version}")

    # ── dispatch helpers (one-line try/except wrappers) ──

    def _safe(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            print(f"[plugins] {fn} failed: {e}")
            return None

    def dispatch_session_change(self, sid: str) -> None:
        for p in self.plugins:
            self._safe(p.on_session_change, sid)

    def dispatch_session_open(self, sid: str, label: str) -> None:
        for p in self.plugins:
            self._safe(p.on_session_open, sid, label)

    def dispatch_session_close(self, sid: str) -> None:
        for p in self.plugins:
            self._safe(p.on_session_close, sid)

    # ── UI aggregation (returned to JS via Api.list_plugin_panels) ──

    def collect_settings_panels(self) -> list[dict]:
        """Return list of {name, version, html, sidebar_js, styles} for
        the frontend to render as additional Settings tabs."""
        out = []
        for p in self.plugins:
            html = self._safe(p.settings_panel) or ""
            sidebar_js = p.asset("sidebar.js")
            styles = p.asset("styles.css")
            out.append({
                "name": p.manifest.name,
                "version": p.manifest.version,
                "description": p.manifest.description,
                "repo_url": p.manifest.repo_url,
                "html": html,
                "sidebar_js": sidebar_js,
                "styles": styles,
            })
        return out

    def collect_sidebar_badges(self, sid: str) -> str:
        """Return concatenated HTML of all plugin badges for a given session."""
        parts = []
        for p in self.plugins:
            snippet = self._safe(p.sidebar_badge, sid) or ""
            if snippet:
                parts.append(snippet)
        return "".join(parts)
