"""Api mixin — Frame Link（跨機配對）域（God-class 分批拆解 第二批）.

js_api 對 frame_link.FrameLink 的薄包裝：配對、遠端分頁瀏覽/輸入/串流、
檔案與訊息。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import json

from api_host import main


class LinkApiMixin:
    # ── Frame Link（跨機配對）────────────────────────────────────────────
    def _start_frame_link(self):
        """Create the FrameLink instance (always) and start its listener when
        config frame_link.enabled is true. Safe to call again after a settings
        toggle — start() is idempotent."""
        if getattr(self, "frame_link", None) is None:
            try:
                ver = json.loads(main.VERSION_FILE.read_text()).get("version", "0")
            except Exception:
                ver = "0"
            self.frame_link = main.frame_link_mod.FrameLink(
                get_config=main.load_config,
                update_config=main.update_config,
                execute_fn=self._execute_sfctl,
                notify=self._on_link_event,
                log=lambda m: main._dlog("link", m),
                version=ver,
            )
        try:
            if main.load_config().get("frame_link", {}).get("enabled"):
                self.frame_link.start()
        except Exception as e:
            main._dlog("link", f"start failed: {e}")

    def _on_link_event(self, ev: dict):
        """Push a Frame Link event (message / file / paired / peer_status)
        into the webview so the panel updates live."""
        try:
            if self._window:
                payload = json.dumps(ev, ensure_ascii=False)
                self._window.evaluate_js(
                    f"window._sfLinkEvent && _sfLinkEvent({payload})")
        except Exception:
            main._swallow("_on_link_event")

    def _link(self):
        if getattr(self, "frame_link", None) is None:
            self._start_frame_link()
        return self.frame_link

    def link_status(self) -> str:
        try:
            return json.dumps(self._link().status(), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"enabled": False, "running": False,
                               "peers": [], "error": str(e)})

    def link_set_enabled(self, enabled: bool) -> str:
        def fn(cfg):
            cfg.setdefault("frame_link", {})["enabled"] = bool(enabled)
        main.update_config(fn)
        fl = self._link()
        if enabled:
            fl.start()
        else:
            fl.stop()
        return self.link_status()

    def link_set_name(self, name: str) -> str:
        def fn(cfg):
            cfg.setdefault("frame_link", {})["frame_name"] = str(name or "").strip()[:60]
        main.update_config(fn)
        return self.link_status()

    def link_pair_begin(self, mode: str = "duplex") -> str:
        return json.dumps(self._link().pairing_begin(mode), ensure_ascii=False)

    def link_pair_cancel(self) -> str:
        return json.dumps(self._link().pairing_cancel())

    def link_join(self, host: str, port: int, code: str) -> str:
        return json.dumps(self._link().join(host, port, code), ensure_ascii=False)

    def link_join_url(self, url: str) -> str:
        """Join from a pairing QR / shellframe:// deep link (tries hosts, then relay)."""
        return json.dumps(self._link().join_url(url), ensure_ascii=False)

    def link_unpair(self, peer_id: str) -> str:
        return json.dumps(self._link().unpair(peer_id))

    def link_update_peer(self, peer_id: str, host: str, port: int,
                         relay_url: str = None, relay_token: str = "") -> str:
        relay = None
        if relay_url is not None:
            relay = {"url": relay_url, "token": relay_token} if relay_url else {}
        return json.dumps(self._link().update_peer(peer_id, host, port, relay),
                          ensure_ascii=False)

    def link_set_relay(self, url: str, token: str) -> str:
        """Relay for phones / peers behind NAT (TG-style outbound long-poll)."""
        try:
            return json.dumps(self._link().set_relay(url, token), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def link_set_public_host(self, host: str) -> str:
        return json.dumps(self._link().set_public_host(host), ensure_ascii=False)

    def link_ping(self, peer_id: str) -> str:
        return json.dumps(self._link().ping_peer(peer_id), ensure_ascii=False)

    def link_remote_tabs(self, peer_id: str) -> str:
        return json.dumps(self._link().remote_info(peer_id), ensure_ascii=False)

    def link_remote_peek(self, peer_id: str, sid: str, lines: int = 120) -> str:
        return json.dumps(self._link().remote_peek(peer_id, sid, lines),
                          ensure_ascii=False)

    def link_remote_maintenance(self, peer_id: str, action: str) -> str:
        return json.dumps(self._link().remote_maintenance(peer_id, action),
                          ensure_ascii=False)

    def link_remote_state(self, peer_id: str, sid: str = "",
                          all_tabs: bool = False, stale_min: float = 0) -> str:
        return json.dumps(
            self._link().remote_state(peer_id, sid, all_tabs, stale_min),
            ensure_ascii=False)

    def link_remote_history(self, peer_id: str, sid: str, cols: int = 0) -> str:
        return json.dumps(self._link().remote_history(peer_id, sid, cols),
                          ensure_ascii=False)

    def link_remote_stream(self, peer_id: str, sid: str, since: int = -1) -> str:
        return json.dumps(self._link().remote_stream(peer_id, sid, since),
                          ensure_ascii=False)

    def link_remote_send(self, peer_id: str, sid: str, text: str,
                         submit: bool = True) -> str:
        return json.dumps(self._link().remote_send(peer_id, sid, text, submit),
                          ensure_ascii=False)

    def link_remote_input(self, peer_id: str, sid: str, data: str) -> str:
        return json.dumps(self._link().remote_input(peer_id, sid, data),
                          ensure_ascii=False)

    def link_remote_resize(self, peer_id: str, sid: str, cols: int, rows: int) -> str:
        return json.dumps(self._link().remote_resize(peer_id, sid, cols, rows),
                          ensure_ascii=False)

    def link_remote_paste(self, peer_id: str, sid: str, data_url: str,
                          filename: str = "paste.png") -> str:
        return json.dumps(self._link().remote_paste(peer_id, sid, data_url, filename),
                          ensure_ascii=False)

    def link_remote_attach_file(self, peer_id: str, sid: str, path: str) -> str:
        """拖放／選檔附到遠端分頁。前端只給本機路徑，讀檔與傳輸都在後端。"""
        return json.dumps(self._link().remote_attach_file(peer_id, sid, path),
                          ensure_ascii=False)

    def link_remote_new(self, peer_id: str, cmd: str = "claude") -> str:
        return json.dumps(self._link().remote_new(peer_id, cmd),
                          ensure_ascii=False)

    def link_remote_close(self, peer_id: str, sid: str) -> str:
        return json.dumps(self._link().remote_close(peer_id, sid),
                          ensure_ascii=False)

    def link_message(self, peer_id: str, text: str) -> str:
        return json.dumps(self._link().send_message(peer_id, text),
                          ensure_ascii=False)

    def link_send_file(self, peer_id: str, path: str) -> str:
        return json.dumps(self._link().send_file(peer_id, path),
                          ensure_ascii=False)

    def link_recent_events(self, limit: int = 100) -> str:
        try:
            return json.dumps(self._link().recent_events(int(limit)),
                              ensure_ascii=False)
        except Exception:
            return "[]"

    def link_pick_file(self) -> str:
        """Native open-file dialog for「傳檔案給 peer」."""
        try:
            dialog_type = getattr(main.webview, "OPEN_DIALOG", None)
            if dialog_type is None:
                dialog_type = main.webview.FileDialog.OPEN
            result = self._window.create_file_dialog(dialog_type,
                                                     allow_multiple=False)
            if result:
                path = result[0] if isinstance(result, (list, tuple)) else result
                return json.dumps({"success": True, "path": str(path)})
            return json.dumps({"success": False, "message": "cancelled"})
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})
