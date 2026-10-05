"""Api mixin — Telegram / LINE 橋接控制域（God-class 分批拆解 第二批）.

啟停、分頁註冊/切換、session 同步。hot_reload_bridge 會用 `global`
重新綁定 main 的橋接類別，所以留在 main.py。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import json
import threading
import time

from api_host import main


class BridgesApiMixin:
    def _sync_bridge_sessions(self):
        """把目前所有存活的分頁補進 bridge，缺哪個補哪個。

        Bridge 的啟動與分頁的還原是兩條獨立的路。v0.36.1 起 bridge 由 Python 在
        視窗開起來**之前**自己啟動（UI 卡住不該讓遠端失聯），而分頁是 UI 載入後
        才還原的——於是 bridge 起來時 self.sessions 還是空的，一個註冊都沒發生；
        UI 之後看到 bridge 已經在跑就不再呼叫 start_bridge，那些分頁因此永遠不在
        bridge 裡。實測：app 有 21 個分頁、bridge 0 個 slot，Telegram 回報
        「Sessions: none」、/list 空的、任何指令都是「No active session」。

        註冊本身是冪等的（同一個 sid 重複註冊只是覆蓋），所以還原完就無條件同步
        一次，不去猜 bridge 是「剛啟動」還是「早就在跑」。
        """
        for bridge in (getattr(self, "bridge", None),
                       getattr(self, "line_bridge", None)):
            if not bridge:
                continue
            try:
                have = set(getattr(bridge, "slots", {}) or {})
                added = 0
                for sid, s in list(self.sessions.items()):
                    if sid in have or not getattr(s, "alive", False):
                        continue
                    if not getattr(s, "_bridge_enabled", True):
                        continue
                    label = (getattr(s, "_custom_label", None)
                             or (s.cmd.split()[0] if s.cmd else sid))
                    bridge.register_session(
                        sid, label,
                        lambda text, _s=s: _s.write(text),
                        peek_fn=lambda _s=s: bytes(_s._recent).decode(
                            "utf-8", errors="replace"),
                        prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                        cmd=getattr(s, "cmd", "") or "",
                        cols=getattr(s, "cols", 0), rows=getattr(s, "rows", 0),
                    )
                    added += 1
                if added:
                    main._dlog("bridge", f"同步補上 {added} 個分頁到 "
                                    f"{getattr(bridge, 'bridge_id', '?')}")
                    try:
                        bridge.refresh_commands()
                    except Exception:
                        main._swallow("_sync_bridge_sessions:refresh")
            except Exception as e:
                main._dlog("bridge", f"sync sessions failed: {e}")

    def _autostart_bridge(self):
        """Bring the Telegram bridge up from Python, without waiting for the UI.

        The bridge used to be started only by the web UI's restore path, which
        runs after the window finishes loading. Anything that blocks that load —
        a modal dialog on launch, a slow or failed render — therefore took
        Telegram down with it, exactly when remote access matters most: the user
        is away from the machine and the UI is the one thing they cannot reach.

        Startup now does not depend on the UI at all. The UI's own restore is
        idempotent (it returns early when the bridge is already active), so the
        two cannot fight."""
        self._autoinstall_ai_skill()
        try:
            saved = (main.load_config() or {}).get("bridge") or {}
            token = saved.get("bot_token") or ""
            if not token or getattr(self, "bridge", None):
                return
            res = self.start_bridge(token,
                                    json.dumps(saved.get("allowed_users") or []),
                                    saved.get("prefix_enabled") is not False,
                                    "")          # no initial prompt on restore
            ok = json.loads(res).get("success") if isinstance(res, str) else False
            main._dlog("bridge", f"autostart {'ok' if ok else 'failed'} (UI-independent)")
            # 兩條路的順序不保證：分頁若已經還原完才輪到這裡，start_bridge 已經
            # 註冊過；反過來就由這一次補上。兩邊都同步才不必賭誰先。
            if ok:
                self._sync_bridge_sessions()
        except Exception as e:
            main._dlog("bridge", f"autostart failed: {e}")

    def start_bridge(self, bot_token: str, allowed_users_json: str,
                     prefix_enabled: bool, initial_prompt: str) -> str:
        """Start the global TG bridge. Registers all current sessions."""
        if self.bridge:
            self.bridge.stop()

        allowed = json.loads(allowed_users_json) if allowed_users_json else []
        # Pull STT settings from config so they survive across restarts
        cfg_now = main.load_config()
        bridge_cfg = cfg_now.get("bridge", {})
        config = main.TelegramBridgeConfig(
            bot_token=bot_token,
            allowed_users=[int(u) for u in allowed],
            prefix_enabled=prefix_enabled,
            initial_prompt=initial_prompt,
            stt_backend=bridge_cfg.get("stt_backend", "auto"),
        )

        self.bridge = main.TelegramBridge(
            bridge_id="tg",
            config=config,
            on_reload=self.hot_reload_bridge,
            on_close_session=self.close_session,
            on_restart=self.restart_app,
            on_check_update=self.check_update,
            on_new_session=lambda c: self.new_session(c, 200, 50),
            on_consume_init=self.consume_init_prompt_if_ready,
            on_model_info=self.get_session_model_info,
            on_agent_status=self._agent_status_snapshot,
            on_input_blocked=self.startup_dialog_blocking,
            on_answer_dialog=self.answer_startup_trust,
        )

        # Register existing sessions (skip bridge-disabled ones)
        for sid, s in self.sessions.items():
            if not getattr(s, 'alive', False):
                continue
            if not getattr(s, '_bridge_enabled', True):
                continue
            label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
            self.bridge.register_session(
                sid, label,
                lambda text, _s=s: _s.write(text),
                peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                cmd=getattr(s, 'cmd', '') or '',
                cols=getattr(s, 'cols', 0), rows=getattr(s, 'rows', 0),
            )

        self.bridge.start()

        # Send initial prompt to first session (delayed to let CLI load)
        if initial_prompt and self.sessions:
            first_sid = list(self.sessions.keys())[0]
            # Track in sent_texts so echo gets filtered
            slot = self.bridge.slots.get(first_sid)
            if slot:
                slot.sent_texts.append(initial_prompt)
            def _send_prompt(sid=first_sid, text=initial_prompt):
                time.sleep(3)
                s = self.sessions.get(sid)
                if s:
                    s.write(text)
                    time.sleep(0.3)
                    s.write("\r")
            threading.Thread(target=_send_prompt, daemon=True).start()

        # Persist bridge config (preserve existing STT settings)
        cfg = main.load_config()
        prev_bridge = cfg.get("bridge", {})
        persisted_initial_prompt = initial_prompt
        if not persisted_initial_prompt and prev_bridge.get("initial_prompt"):
            persisted_initial_prompt = prev_bridge.get("initial_prompt", "")
        cfg["bridge"] = {
            "bot_token": bot_token,
            "allowed_users": [int(u) for u in allowed],
            "prefix_enabled": prefix_enabled,
            "initial_prompt": persisted_initial_prompt,
            "stt_backend": prev_bridge.get("stt_backend", "auto"),
            "stt_providers": prev_bridge.get("stt_providers", []),
        }
        main.save_config(cfg)
        self._persist_session_manifest()

        return json.dumps({"success": self.bridge.connected, **self.bridge.get_status()})

    def stop_bridge(self) -> str:
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
            # Remove from config
            cfg = main.load_config()
            cfg.pop("bridge", None)
            main.save_config(cfg)
            return json.dumps({"success": True})
        return json.dumps({"success": False, "message": "No bridge running"})

    # ── LINE bridge plugin ──
    def start_line_bridge(self, channel_access_token: str, channel_secret: str,
                          allowed_users_json: str, prefix_enabled: bool,
                          webhook_port: int, webhook_path: str,
                          public_webhook_url: str, delivery_mode: str = "push",
                          poll_path: str = "/line/poll",
                          forward_secret: str = "") -> str:
        """Start the LINE bridge plugin and persist its config."""
        if self.line_bridge:
            self.line_bridge.stop()
            self.line_bridge = None
        try:
            allowed = json.loads(allowed_users_json) if allowed_users_json else []
            allowed = [str(u).strip() for u in allowed if str(u).strip()]
            config = main.LineBridgeConfig(
                channel_access_token=channel_access_token,
                channel_secret=channel_secret,
                allowed_users=allowed,
                prefix_enabled=bool(prefix_enabled),
                webhook_port=int(webhook_port or 8787),
                webhook_path=webhook_path or "/line/webhook",
                public_webhook_url=public_webhook_url or "",
                delivery_mode=delivery_mode or "push",
                poll_path=poll_path or "/line/poll",
                forward_secret=forward_secret or "",
            )
            self.line_bridge = main.LineBridge(
                bridge_id="line",
                config=config,
                on_new_session=lambda c: self.new_session(c, 200, 50),
                on_close_session=self.close_session,
                on_consume_init=self.consume_init_prompt_if_ready,
                on_rename_session=self.rename_session,
                on_session_ready=self.is_session_ready_for_bridge,
                gateway_worker_cmd=main.SHELLFRAME_CODEX_CMD,
            )
            for sid, s in self.sessions.items():
                if not getattr(s, '_bridge_enabled', True):
                    continue
                label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                self.line_bridge.register_session(
                    sid, label,
                    lambda text, _s=s: _s.write(text),
                    peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                )
            self.line_bridge.start()
            status = self.line_bridge.get_status()
            if not self.line_bridge.connected:
                message = status.get("message") or "LINE bridge failed to start"
                self.line_bridge = None
                return json.dumps({"success": False, "message": message, **status})

            cfg = main.load_config()
            cfg["line_bridge"] = {
                "channel_access_token": channel_access_token,
                "channel_secret": channel_secret,
                "allowed_users": allowed,
                "prefix_enabled": bool(prefix_enabled),
                "webhook_port": config.webhook_port,
                "webhook_path": config.webhook_path,
                "public_webhook_url": public_webhook_url or "",
                "delivery_mode": config.delivery_mode,
                "poll_path": config.poll_path,
                "forward_secret": forward_secret or "",
            }
            main.save_config(cfg)
            return json.dumps({"success": True, "exists": True, **status})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "message": str(e)})

    def stop_line_bridge(self) -> str:
        if self.line_bridge:
            self.line_bridge.stop()
            self.line_bridge = None
        cfg = main.load_config()
        cfg.pop("line_bridge", None)
        main.save_config(cfg)
        return json.dumps({"success": True})

    def get_line_bridge_status(self) -> str:
        if not self.line_bridge:
            return json.dumps({"exists": False})
        return json.dumps({"exists": True, **self.line_bridge.get_status()})

    def toggle_bridge(self) -> str:
        """Toggle pause/resume."""
        if not self.bridge:
            return json.dumps({"active": False, "exists": False})
        is_active = self.bridge.toggle_pause()
        return json.dumps({"active": is_active, "exists": True, **self.bridge.get_status()})

    def get_bridge_status(self) -> str:
        if not self.bridge:
            return json.dumps({"exists": False})
        return json.dumps({"exists": True, **self.bridge.get_status()})

    def set_session_bridge(self, sid: str, enabled: bool) -> str:
        """Enable/disable TG bridge for a specific session. Persists to config."""
        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False})
        s._bridge_enabled = bool(enabled)
        if self.bridge:
            if enabled:
                label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                self.bridge.register_session(
                    sid, label,
                    lambda text, _s=s: _s.write(text),
                    peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                    prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                    cmd=getattr(s, 'cmd', '') or '',
                    cols=getattr(s, 'cols', 0), rows=getattr(s, 'rows', 0),
                )
            else:
                self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        if self.line_bridge:
            if enabled:
                label = getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid)
                self.line_bridge.register_session(
                    sid, label,
                    lambda text, _s=s: _s.write(text),
                    peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                )
            else:
                self.line_bridge.unregister_session(sid)
        # Persist bridge-disabled sessions so they survive restart
        cfg = main.load_config()
        disabled = set(cfg.get("bridge_disabled_sessions", []))
        if enabled:
            disabled.discard(sid)
        else:
            disabled.add(sid)
        cfg["bridge_disabled_sessions"] = sorted(disabled)
        main.save_config(cfg)
        self._persist_session_manifest()
        return json.dumps({"success": True, "enabled": enabled})

    def switch_bridge_session(self, sid: str) -> str:
        """Switch TG bridge active session and notify TG users."""
        if not self.bridge:
            return json.dumps({"success": False, "message": "No bridge"})
        try:
            self.bridge.switch_active_session(sid)
            return json.dumps({"success": True, "active_sid": sid})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def switch_line_bridge_session(self, sid: str) -> str:
        """Switch LINE bridge active session for forwarded / polled chats."""
        if not self.line_bridge:
            return json.dumps({"success": False, "message": "No LINE bridge"})
        try:
            self.line_bridge.switch_active_session(sid)
            return json.dumps({"success": True, "active_sid": sid})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def debug_bridge_info(self) -> str:
        """Debug: return bridge internals for troubleshooting."""
        if not self.bridge:
            return json.dumps({"bridge": False})
        b = self.bridge
        return json.dumps({
            "bridge": True,
            "slot_order": list(b._slot_order),
            "slots": list(b.slots.keys()),
            "user_active": {str(k): v for k, v in b._user_active.items()},
            "user_chat": {str(k): v for k, v in b._user_chat.items()},
            "active_sid": b.get_primary_active_sid(),
        })

    def bridge_register_session(self, sid: str, label: str):
        """Register a new session with the running bridge."""
        if not self.bridge:
            return
        s = self.sessions.get(sid)
        if s and getattr(s, 'alive', False):
            self.bridge.register_session(
                sid, label,
                lambda text, _s=s: _s.write(text),
                peek_fn=lambda _s=s: bytes(_s._recent).decode('utf-8', errors='replace'),
                prepare_fn=lambda _s=s: self._prepare_pane_for_input(_s),
                cmd=getattr(s, 'cmd', '') or '',
                cols=getattr(s, 'cols', 0), rows=getattr(s, 'rows', 0),
            )
            self.bridge.refresh_commands()

    def bridge_unregister_session(self, sid: str):
        """Remove a session from the bridge."""
        if self.bridge:
            self.bridge.unregister_session(sid)
            self.bridge.refresh_commands()
        if self.line_bridge:
            self.line_bridge.unregister_session(sid)
