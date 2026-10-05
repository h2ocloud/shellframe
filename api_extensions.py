"""Api mixin — 外掛與 AI skill 域（God-class 分批拆解 第二批）.

plugin_sdk 外掛載入/事件分派/側欄面板、外掛市集安裝與啟停、
ShellFrame AI skill 安裝。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import importlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from api_host import main


class ExtensionsApiMixin:
    def _plugin_api(self):
        import plugin_sdk
        return plugin_sdk.PluginHostAPI(
            get_active_sid=lambda: main.load_config().get("last_active_tab", "") or "",
            list_sessions=lambda: [
                {
                    "sid": sid,
                    "label": getattr(s, "_custom_label", None) or sid,
                    "cmd": s.cmd,
                    "alive": bool(s.alive),
                }
                for sid, s in self.sessions.items()
                if s.alive
            ],
            send_to_session=lambda sid, text: (
                self.sessions[sid].write(text) if sid in self.sessions else None
            ),
            config_dir=main.CONFIG_DIR,
        )

    def _plugins_reload(self):
        """Re-scan shellframe_plugins without restarting the app."""
        try:
            import plugin_sdk
            importlib.reload(plugin_sdk)
            cfg = main.load_config()
            enabled = main._plugins_config(cfg).get("enabled") or []
            self._plugins = plugin_sdk.PluginRegistry(
                main.APP_DIR / "shellframe_plugins",
                self._plugin_api(),
                enabled_plugins=[str(name) for name in enabled],
            )
            self._plugins.load_all()
            main._dlog("plugins", f"reloaded; {len(self._plugins.plugins)} plugin(s)")
        except Exception as e:
            self._plugins = None
            main._dlog("plugins", f"reload failed: {e!r}")

    def _plugin_dispatch_session_open(self, sid: str, label: str):
        try:
            if self._plugins:
                self._plugins.dispatch_session_open(sid, label)
        except Exception as e:
            main._dlog("plugins", f"session_open failed: {e!r}")

    def _plugin_dispatch_session_close(self, sid: str):
        try:
            if self._plugins:
                self._plugins.dispatch_session_close(sid)
        except Exception as e:
            main._dlog("plugins", f"session_close failed: {e!r}")

    def _plugin_dispatch_session_change(self, sid: str):
        try:
            if self._plugins:
                self._plugins.dispatch_session_change(sid)
        except Exception as e:
            main._dlog("plugins", f"session_change failed: {e!r}")

    def list_plugin_panels(self) -> str:
        """Return plugin metadata + injected HTML/CSS/JS for settings tabs."""
        if not self._plugins:
            return "[]"
        try:
            return json.dumps(self._plugins.collect_settings_panels(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def plugin_sidebar_badges(self, sid: str) -> str:
        """Return concatenated HTML snippets rendered after a session label."""
        if not self._plugins:
            return ""
        try:
            return self._plugins.collect_sidebar_badges(sid)
        except Exception:
            return ""

    def plugin_action(self, plugin_name: str, action: str, args_json: str = "{}") -> str:
        if not self._plugins:
            return json.dumps({"ok": False, "message": "plugin registry not loaded"})
        try:
            args = json.loads(args_json) if args_json else {}
        except Exception:
            args = {}
        try:
            result = self._plugins.dispatch_action(plugin_name, action, args)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False)

    def marketplace_list(self) -> str:
        """List curated plugins and mark installed versions."""
        try:
            mk_path = main.APP_DIR / "shellframe_plugins" / "_marketplace.json"
            data = json.loads(mk_path.read_text(encoding="utf-8")) if mk_path.exists() else {"plugins": []}
            cfg = main.load_config()
            plugin_cfg = main._plugins_config(cfg)
            installed_cfg = set(plugin_cfg.get("installed") or [])
            enabled_cfg = set(plugin_cfg.get("enabled") or [])
            installed = {
                p.manifest.name: p.manifest.version
                for p in (self._plugins.plugins if self._plugins else [])
            }
            for p in data.get("plugins", []):
                name = p.get("name")
                target = main.APP_DIR / "shellframe_plugins" / re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
                local_manifest = target / "manifest.json"
                local_version = ""
                if local_manifest.exists():
                    try:
                        local_version = json.loads(local_manifest.read_text(encoding="utf-8")).get("version", "")
                    except Exception:
                        local_version = ""
                bundled = bool(p.get("bundled"))
                p["installed"] = name in installed_cfg or (not bundled and local_manifest.exists())
                p["enabled"] = name in enabled_cfg
                p["installed_version"] = installed.get(name) or local_version
            return json.dumps(data, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e), "plugins": []}, ensure_ascii=False)

    def _marketplace_plugin_entry(self, name: str) -> dict:
        mk_path = main.APP_DIR / "shellframe_plugins" / "_marketplace.json"
        data = json.loads(mk_path.read_text(encoding="utf-8")) if mk_path.exists() else {"plugins": []}
        for p in data.get("plugins", []):
            if p.get("name") == name:
                return p
        return {}

    def _set_plugin_installed_enabled(self, name: str, installed: bool | None = None, enabled: bool | None = None) -> dict:
        cfg = main.load_config()
        main._ensure_plugins_defaults(cfg)
        plugin_cfg = main._plugins_config(cfg)
        installed_set = {str(v) for v in (plugin_cfg.get("installed") or [])}
        enabled_set = {str(v) for v in (plugin_cfg.get("enabled") or [])}
        if installed is not None:
            (installed_set.add if installed else installed_set.discard)(name)
        if enabled is not None:
            (enabled_set.add if enabled else enabled_set.discard)(name)
        if enabled is True:
            installed_set.add(name)
        plugin_cfg["installed"] = sorted(installed_set)
        plugin_cfg["enabled"] = sorted(enabled_set)
        cfg["plugins"] = plugin_cfg
        main.save_config(cfg)
        return cfg

    def marketplace_install(self, name: str, repo_url: str) -> str:
        """Install a plugin by cloning it into shellframe_plugins/<name>."""
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
        if not safe_name or safe_name != name:
            return json.dumps({"ok": False, "message": "invalid plugin name"})
        target = main.APP_DIR / "shellframe_plugins" / safe_name
        if target.exists():
            if not (target / "manifest.json").exists():
                return json.dumps({"ok": False, "message": f"{safe_name} already exists but is not a plugin"})
            self._set_plugin_installed_enabled(safe_name, installed=True, enabled=True)
            self._plugins_reload()
            return json.dumps({"ok": True, "message": f"enabled {safe_name}"})
        try:
            subprocess.check_output(
                ["git", "clone", "--depth", "1", repo_url, str(target)],
                stderr=subprocess.STDOUT,
                timeout=60,
            )
            self._set_plugin_installed_enabled(safe_name, installed=True, enabled=True)
            self._plugins_reload()
            return json.dumps({"ok": True, "message": f"installed {safe_name}"})
        except subprocess.CalledProcessError as e:
            return json.dumps({"ok": False, "message": e.output.decode("utf-8", errors="replace")[-400:]})
        except Exception as e:
            return json.dumps({"ok": False, "message": str(e)})

    def marketplace_enable(self, name: str, enabled: bool) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
        if not safe_name or safe_name != name:
            return json.dumps({"ok": False, "message": "invalid plugin name"})
        target = main.APP_DIR / "shellframe_plugins" / safe_name
        if not target.exists() or not (target / "manifest.json").exists():
            return json.dumps({"ok": False, "message": f"{safe_name} not installed"})
        self._set_plugin_installed_enabled(safe_name, installed=True, enabled=bool(enabled))
        self._plugins_reload()
        return json.dumps({"ok": True, "message": f"{'enabled' if enabled else 'disabled'} {safe_name}"})

    def marketplace_uninstall(self, name: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "", name or "")
        if not safe_name or safe_name != name:
            return json.dumps({"ok": False, "message": "invalid plugin name"})
        target = main.APP_DIR / "shellframe_plugins" / safe_name
        entry = self._marketplace_plugin_entry(safe_name)
        bundled = bool(entry.get("bundled"))
        if not bundled and (not target.exists() or not target.is_dir()):
            return json.dumps({"ok": False, "message": f"{safe_name} not installed"})
        try:
            self._set_plugin_installed_enabled(safe_name, installed=False, enabled=False)
            if target.exists() and target.is_dir() and not bundled:
                shutil.rmtree(target)
            self._plugins_reload()
            return json.dumps({"ok": True, "message": f"removed {safe_name}"})
        except Exception as e:
            return json.dumps({"ok": False, "message": str(e)})

    def _autoinstall_ai_skill(self):
        """Keep ~/.claude/skills/shellframe/SKILL.md in step with this install.

        Deliberately fire-and-forget: an agent not finding the pointer is a
        missed convenience, never a reason to hold up startup or the bridge."""
        try:
            if not (main.load_config().get("settings") or {}).get("ai_skill_autoinstall", True):
                return
            import ai_skill
            changed, where = ai_skill.install_claude_skill()
            if changed:
                print(f"[ai-skill] installed pointer skill at {where}")
        except Exception as e:
            print(f"[ai-skill] skipped: {e}")

    def ai_skill_status(self) -> str:
        """Where the pointer is installed, for the About panel."""
        try:
            import ai_skill
            return json.dumps({"success": True, "details": ai_skill.status()},
                              ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def ai_skill_install(self, target: str = "claude", remove: bool = False) -> str:
        """Install (or, for Codex, remove) the pointer. Codex shares AGENTS.md
        with the user, so it is only ever touched from this button."""
        try:
            import ai_skill
            if target == "codex":
                changed, where = ai_skill.install_codex_agents(remove=bool(remove))
                verb = "移除" if remove else "寫入"
            else:
                changed, where = ai_skill.install_claude_skill()
                verb = "寫入"
            ok = not str(where).startswith("寫入失敗") and "找不到" not in str(where)
            return json.dumps({"success": ok,
                               "message": (f"已{verb}：{where}" if changed and ok
                                           else where if not ok else f"已經是最新的：{where}"),
                               "details": ai_skill.status()}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def ai_skill_doc(self) -> str:
        """The agent-facing skill sheet (docs/ai-skill.md), for the About panel's
        copy button. Served from disk so it tracks the installed build rather
        than a copy pasted into the UI that would drift out of date."""
        try:
            path = Path(__file__).resolve().parent / "docs" / "ai-skill.md"
            return json.dumps({"success": True, "text": path.read_text(encoding="utf-8")})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})
