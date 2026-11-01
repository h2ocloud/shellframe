"""Tab <-> tmux session bookkeeping around the restore loop: which tab a tmux
session belongs to, reattaching tabs that failed at startup, the orphan report
(`sfctl orphans`), and repainting the visible tab after the display wakes.

中文摘要：app 重開時 tmux 若被換出記憶體，一次呼叫可能卡好幾秒，接回分頁會逾時。
接失敗的分頁過一陣子再接（分頁會自己出現在 UI 上）；最後還是接不回來的，記進
debug log，`sfctl orphans` 查得到。只回報，不殺任何行程。螢幕醒來時叫 tmux 整面
重畫目前分頁、叫頁面重排，並記下頁面多久才回應——下次「醒來看不到畫面」才有數據。
"""
import json
import re
import threading
import time

import tmux_sessions
import wake_watch
from api_host import main
from sf_log import _dlog


class TmuxApiMixin:
    # tmux pages back in within seconds to a minute; three spaced tries cover it
    # without hammering a server that is already slow.
    _ATTACH_RETRY_DELAYS = (5.0, 20.0, 60.0)

    @staticmethod
    def _manifest_entries(cfg: dict) -> list[dict]:
        manifest = cfg.get("session_manifest") or []
        if manifest:
            return [e for e in manifest if e.get("sid") and e.get("cmd")]
        # Backward compatibility with the old Windows/no-tmux soft list.
        return [e for e in (cfg.get("session_list") or []) if e.get("sid") and e.get("cmd")]

    @staticmethod
    def _resolve_tmux_sid(info: dict, cfg: dict) -> str:
        """Resolve stable sid for a tmux session whose display name may change."""
        if info.get("sid"):
            return str(info["sid"])
        prefix = main.TMUX_PREFIX
        name = info.get("name", "")
        suffix = name[len(prefix):] if name.startswith(prefix) else name
        if re.fullmatch(r"s\d+", suffix or ""):
            return suffix
        for entry in TmuxApiMixin._manifest_entries(cfg):
            if entry.get("tmux_name") == name:
                return str(entry.get("sid"))
        saved_labels = cfg.get("session_labels", {}) or {}
        for sid, label in saved_labels.items():
            if main._slugify(str(label)) == suffix:
                return str(sid)
        return suffix or name

    def _owned_tmux_names(self) -> set:
        return {getattr(s, "_tmux_name", "") or "" for s in list(self.sessions.values())}

    def _retry_failed_attach(self, failed: list[str]):
        """Reattach, in the background, the tmux sessions restore could not.

        Before this, one timed-out tmux call aborted the restore loop and every
        session after it stayed running with no tab (2026-10-29: stopped at the
        10th of 24, fifteen agents orphaned). The page picks the new tabs up on
        its own sync poll.
        """
        if not failed or getattr(self, "_attach_retrying", False):
            return
        self._attach_retrying = True
        threading.Thread(target=self._attach_retry_loop, args=(list(failed),),
                         daemon=True, name="reattach-retry").start()

    def _attach_retry_loop(self, failed: list[str]):
        try:
            for delay in self._ATTACH_RETRY_DELAYS:
                time.sleep(delay)
                left = [n for n in failed if n not in self._owned_tmux_names()]
                if not left:
                    break
                _dlog("lifecycle", f"  reattach retry: {left}")
                self.restore_tmux_sessions(80, 24)
            orphans = self.orphan_sessions()
            if orphans:
                _dlog("lifecycle", "  " + tmux_sessions.format_orphans(orphans).replace("\n", " | "))
            else:
                _dlog("lifecycle", f"  reattach retry: all {len(failed)} back, no orphans")
        except Exception as e:
            _dlog("lifecycle", f"  reattach retry failed: {e!r}")
        finally:
            self._attach_retrying = False

    def orphan_sessions(self) -> list[dict]:
        """sf_* tmux sessions no tab of this window owns (report only)."""
        details = tmux_sessions.session_details(main._tmux_bin(), main.TMUX_PREFIX)
        labels = main.load_config().get("session_labels") or {}
        return tmux_sessions.find_orphans(details, self._owned_tmux_names(), labels)

    # ── display sleep / wake ────────────────────────────────────────────
    _WAKE_ACK_TIMEOUT = 30.0

    def _on_display_sleep(self, reason: str = "screens"):
        self._display_slept_at = time.time()
        _dlog("wake", f"{reason} did sleep; tabs={len(self.sessions)} "
                      f"pressure={wake_watch.memory_pressure()}")

    def _on_display_wake(self, reason: str = "screens"):
        """Repaint the visible tab after the display (or the Mac) wakes.

        Nothing used to happen on wake: whether WebKit repainted was luck, and
        under swap pressure the window often stayed blank. tmux redraws the
        whole screen for our client, the page re-fits and repaints the
        terminal, and the log records how long the page took to answer.
        """
        woke = time.time()
        if woke - getattr(self, "_display_woke_at", 0.0) < 2.0:
            return  # screens + system wake arrive together
        self._display_woke_at = woke
        slept = getattr(self, "_display_slept_at", 0.0)
        _dlog("wake", f"{reason} did wake after {int(woke - slept) if slept else '?'}s; "
                      f"tabs={len(self.sessions)} pressure={wake_watch.memory_pressure()}")
        s = self.sessions.get(getattr(self, "_active_sid", "") or "")
        if s is not None and getattr(s, "_tmux_name", None):
            tmux_sessions.refresh_client(main._tmux_bin(), s._tmux_name, s.child_pid)
        self._output_event.set()
        win = getattr(self, "_window", None)
        if win is None:
            return
        try:
            win.evaluate_js(f"window._sfOnWake && window._sfOnWake({json.dumps(reason)})")
        except Exception as e:
            _dlog("wake", f"repaint call failed: {e!r}")
        while time.time() - woke < self._WAKE_ACK_TIMEOUT:
            if getattr(self, "_wake_ack_at", 0.0) >= woke:
                _dlog("wake", f"page repainted {int((self._wake_ack_at - woke) * 1000)}ms after wake")
                return
            time.sleep(0.25)
        _dlog("wake", f"page did not answer within {int(self._WAKE_ACK_TIMEOUT)}s of wake "
                      f"(pressure={wake_watch.memory_pressure()}); the renderer is stalled")
