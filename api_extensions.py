"""Api mixin — 外掛與 AI skill 域（God-class 分批拆解 第二批）.

plugin_sdk 外掛載入/事件分派/側欄面板、ShellFrame AI skill 安裝。外掛市集
（設定頁的 Plugins 分頁、安裝／啟停／移除）已在設定整理時移除；
shellframe_plugins/ 裡、config 的 plugins.enabled 有列的外掛照常載入。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import importlib
import json
from pathlib import Path

from api_host import main
import sf_lang


def _lang(en, zh):
    return sf_lang.pick(en, zh)


def _skill_message(changed, where, remove):
    """Localize the small status vocabulary returned by ai_skill; keep paths intact."""
    text = str(where)
    if text.startswith("寫入失敗："):
        return _lang(f"Write failed: {text[len('寫入失敗：'):]}", text)
    if text.startswith("找不到 "):
        return _lang(text.replace("找不到 ", "Not found ", 1), text)
    if changed:
        verb_en = "Removed" if remove else "Wrote"
        verb_zh = "移除" if remove else "寫入"
        return _lang(f"{verb_en}: {text}", f"已{verb_zh}：{text}")
    return _lang(f"Already up to date: {text}", f"已經是最新的：{text}")


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
            else:
                changed, where = ai_skill.install_claude_skill()
            ok = not str(where).startswith("寫入失敗") and "找不到" not in str(where)
            return json.dumps({"success": ok,
                               "message": _skill_message(changed, where, remove) if ok
                                          else _skill_message(False, where, remove),
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
