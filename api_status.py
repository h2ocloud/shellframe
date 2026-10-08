"""Api mixin — Agent 狀態域（God-class 分批拆解 第二批）.

hook 事件驅動的精確狀態、狀態 hook 安裝開關、定期狀態監看。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import json
import os
import subprocess
import threading
import time

from api_host import main


class StatusApiMixin:
    @staticmethod
    def _auto_status_enabled() -> bool:
        # Feature flag — default ON; set settings.auto_status_detect=false to
        # disable and fall back to the browser-side heuristic + [[SF:]] markers.
        try:
            settings = main.load_config().get("settings", {}) or {}
            return settings.get("auto_status_detect", True) is not False
        except Exception:
            return True

    @staticmethod
    def _hook_state_for(event: str, notification_type: str = "", message: str = "") -> str | None:
        """Map a Claude Code hook event to a feed state; None = no transition."""
        if event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"):
            return "working"
        if event == "Stop":
            return "done"
        if event == "StopFailure":
            return "stuck"
        if event == "SessionEnd":
            return "done"
        if event == "Notification":
            blob = f"{notification_type} {message}".lower()
            if "permission" in blob:
                return "decision"
            if "idle" in blob or "waiting for your input" in blob:
                return "done"
        return None

    def _on_agent_event(self, args: dict) -> dict:
        """sfctl cmd `agent_event` — fired by sf_agent_hook.py (fire-and-forget)."""
        sid = str(args.get("sid") or "").strip()
        if not sid:
            return {"success": False, "message": "sid required"}
        event = str(args.get("event") or "")
        # hook 事件帶的 session_id / transcript_path 是「這個分頁現在寫哪個
        # transcript」的唯一即時真相——/clear 會在同一個 claude process 裡
        # 輪替 uuid，spawn 時的 --session-id 與 nearest-birth 都會釘在舊檔
        # （2026-08-06 tab13 badge 顯示 Opus 4.6、實際 Opus 5 的根因）。
        # 存在 state gate 之前：被 ignore 的事件同樣帶有效路徑。
        s = self.sessions.get(sid)
        if s is not None:
            tp = str(args.get("transcript_path") or "").strip()
            csid = str(args.get("session_id") or "").strip()
            changed = False
            if tp and getattr(s, "_hook_transcript_path", None) != tp:
                s._hook_transcript_path = tp
                changed = True
            if csid and getattr(s, "session_id", None) != csid:
                s.session_id = csid
                changed = True
            # 只有真的換檔才落地——hook 每個 PreToolUse 都會進來，無條件
            # persist 等於把整份 config 重寫成高頻寫入。
            if changed:
                self._persist_session_manifest()
        state = self._hook_state_for(
            event,
            str(args.get("notification_type") or ""),
            str(args.get("message") or ""))
        if not state:
            return {"success": True, "message": f"ignored {event}"}
        now = time.time()
        prev = self._hook_events.get(sid)
        since = prev["since"] if (prev and prev["state"] == state) else now
        tool = str(args.get("tool_name") or "")
        self._hook_events[sid] = {
            "state": state, "ts": now, "since": since,
            "tool": tool if state == "working" else "",
            "event": event,
            # 送達驗證要的是「prompt 被收下」的時間，不是最新狀態——後面的
            # PreToolUse／Stop 會蓋掉 state，這個值要往後帶。
            "prompt_at": now if event == "UserPromptSubmit" else (prev or {}).get("prompt_at", 0.0),
        }
        # Invalidate the gated cache so the next monitor pass (≤0.6s) refreshes
        # transcript-side details alongside the new exact state.
        self._status_cache.pop(sid, None)
        main._dlog("hookstat", f"sid={sid} {event} -> {state}")
        return {"success": True, "message": f"{sid} -> {state}"}

    @staticmethod
    def _apply_hook_state(result: dict, hk: dict, now: float) -> dict:
        """Overlay the hook-derived state on a heuristic result. Detail fields
        (task/narration from the transcript) are kept — only the state verdict
        and its dependents are replaced when they disagree."""
        state = hk["state"]
        if result.get("state") == state:
            return result
        out = dict(result)
        out["state"] = state
        out["dot"] = main.agent_status.DOT.get(state, "")
        out["elapsed"] = int(now - hk.get("since", now))
        if state == "working":
            if hk.get("tool"):
                out["summary"] = f"Running {hk['tool']}"
        elif state == "decision":
            out["summary"] = "等待權限決策"
        elif state == "stuck":
            out["summary"] = "回合異常結束"
        return out

    def _sf_hook_command(self) -> str:
        return f'python3 "{main.APP_DIR / "sf_agent_hook.py"}"'

    def get_status_hooks_info(self) -> str:
        """Settings UI: are the ShellFrame status hooks installed in ~/.claude/settings.json?"""
        installed = False
        try:
            p = self._CLAUDE_SETTINGS_PATH
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            hooks = cfg.get("hooks") or {}
            installed = all(
                any("sf_agent_hook.py" in json.dumps(g) for g in (hooks.get(ev) or []))
                for ev in self._HOOK_EVENTS)
        except Exception:
            main._swallow("Api.get_status_hooks_info:2188")
        return json.dumps({"installed": installed,
                           "settings_path": str(self._CLAUDE_SETTINGS_PATH)})

    def set_status_hooks_enabled(self, enabled: bool) -> str:
        """Install/remove the status hook entries. Merge is surgical: only
        groups whose command references sf_agent_hook.py are touched, every
        other hook in the user's settings.json survives byte-for-byte."""
        p = self._CLAUDE_SETTINGS_PATH
        try:
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception as e:
            return json.dumps({"installed": False, "error": f"settings.json unreadable: {e}"})
        hooks = cfg.setdefault("hooks", {})
        if enabled:
            for ev in self._HOOK_EVENTS:
                groups = hooks.setdefault(ev, [])
                if any("sf_agent_hook.py" in json.dumps(g) for g in groups):
                    continue
                groups.append({"matcher": "", "hooks": [{
                    "type": "command", "command": self._sf_hook_command(),
                    "async": True, "timeout": 10}]})
        else:
            for ev in list(hooks.keys()):
                kept = []
                for g in hooks.get(ev) or []:
                    inner = [h for h in (g.get("hooks") or [])
                             if "sf_agent_hook.py" not in str(h.get("command", ""))]
                    if inner or not g.get("hooks"):
                        if g.get("hooks"):
                            g = dict(g)
                            g["hooks"] = inner
                        kept.append(g)
                if kept:
                    hooks[ev] = kept
                else:
                    hooks.pop(ev, None)
            if not hooks:
                cfg.pop("hooks", None)
            self._hook_events.clear()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(p) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception as e:
            return json.dumps({"installed": False, "error": f"write failed: {e}"})
        return self.get_status_hooks_info()

    def refresh_agent_status(self, sid: str = "") -> str:
        """把狀態快取與 hook 狀態清掉，強制下一輪從畫面／transcript 重算。

        中斷對話（Ctrl+C／Esc）時 Claude Code 不一定會發 Stop hook，
        `_hook_events` 就卡在 working；而 status monitor 的 idle gating 又因為
        PTY 不再輸出而跳過重算——燈號於是一直停在「執行中」（日常使用中回報
        回報）。清掉之後 heuristic 會重新判斷：真的還在跑就會再標回 working，
        所以清掉是安全的。

        sid 空字串＝全部分頁。"""
        try:
            targets = [sid] if sid else list(self.sessions.keys())
            for t in targets:
                self._status_cache.pop(t, None)
                self._hook_events.pop(t, None)
            main._dlog("status", f"強制重算狀態 targets={len(targets)}"
                            f"{' sid=' + sid if sid else ' (全部)'}")
            return json.dumps({"refreshed": len(targets)})
        except Exception as e:
            main._swallow(f"refresh_agent_status:{sid}")
            return json.dumps({"refreshed": 0, "error": str(e)})

    def _start_status_monitor(self):
        """Background thread: every ~600ms compute each tab's agent status from
        its transcript/rollout log (+ screen wording) and push to the webview.
        Fully isolated from the PTY/output path — any failure just yields
        'unknown' and the browser heuristic keeps working."""
        if self._status_started:
            return
        self._status_started = True

        def monitor():
            # (hook override below) Industry survey 2026-06: every OSS agent
            # manager (claude-squad SHA256 pane diff, agentapi 2s screen
            # stability, ccmanager UI-string regex) scrapes the screen and is
            # fragile by design. Claude Code's own hooks emit the exact
            # transitions instead — see _on_agent_event / sf_agent_hook.py.
            # Heuristic detection below stays as the fallback (Codex, plain
            # shells, hooks not installed, tabs opened before install).
            # Idle gating: a tab whose PTY printed nothing since the last pass
            # cannot have changed state — transcript and screen only move when
            # the program outputs, and working tabs always stream spinner/timer
            # frames. Reuse the cached result and just tick `elapsed`. With 10
            # idle tabs this removes ~17 tmux capture-pane forks plus ~17
            # 256KB transcript tail-reads PER SECOND from the steady state.
            # Wall-clock-dependent transitions (pending-tool age guard,
            # debounce) still land within FORCE_REFRESH.
            FORCE_REFRESH = 15.0   # full recompute at least this often per tab
            PUSH_HEARTBEAT = 5.0   # elapsed-only changes push at most this often
            # FORCE_REFRESH 只在「有輸出過」的分頁上重算，救不了中斷後就完全
            # 安靜的分頁——hook 沒發 Stop、PTY 也不再動，燈號會一直停在
            # working。每 5 分鐘連 hook 狀態一起清掉重判一次（日常使用中
            # 2026-09-03：「有些情況我會中斷對話，這時候燈號就不會變動了」）。
            HOOK_RESET_INTERVAL = 300.0
            cache = self._status_cache  # sid -> {out_ts, computed_at, since_ts, result}
            last_push = {"key": None, "at": 0.0}
            last_hook_reset = time.time()
            while True:
                try:
                    if not self._auto_status_enabled() or not self._window:
                        time.sleep(1.0)
                        continue
                    now = time.time()
                    for stale in [k for k in list(cache) if k not in self.sessions]:
                        cache.pop(stale, None)
                    for stale in [k for k in list(self._hook_events) if k not in self.sessions]:
                        self._hook_events.pop(stale, None)
                    if now - last_hook_reset >= HOOK_RESET_INTERVAL:
                        last_hook_reset = now
                        cache.clear()
                        self._hook_events.clear()
                        main._dlog("status", "五分鐘定期重算：清掉 hook 與狀態快取")
                    out = {}
                    for sid, s in list(self.sessions.items()):
                        try:
                            out_ts = getattr(s, "_last_output_activity_time", 0.0)
                            c = cache.get(sid)
                            if (c and c["out_ts"] == out_ts
                                    and now - c["computed_at"] < FORCE_REFRESH):
                                result = dict(c["result"])
                                result["elapsed"] = int(now - c["since_ts"])
                            else:
                                worker = self._worker_ctx(sid, s)
                                # Screen wording must come from the CURRENT rendered
                                # screen. The _recent ring buffer is a byte-stream
                                # history — a /model or feedback menu that scrolled
                                # away stays in it and false-triggers "decision".
                                screen_tail = ""
                                tn = getattr(s, "_tmux_name", None)
                                if tn:
                                    try:
                                        r = subprocess.run(
                                            ["tmux", "capture-pane", "-t", tn, "-p"],
                                            capture_output=True, text=True, timeout=2)
                                        if r.returncode == 0:
                                            screen_tail = "\n".join(
                                                r.stdout.rstrip().splitlines()[-20:])
                                    except Exception:
                                        main._swallow("_start_status_monitor.monitor:2308")
                                if not screen_tail:
                                    screen_tail = bytes(getattr(s, "_recent", b"")).decode(
                                        "utf-8", errors="replace")[-4000:]
                                st = self._status_tracker.status_for(
                                    sid, worker, screen_tail=screen_tail)
                                result = {"state": st.get("state"),
                                          "dot": st.get("dot"),
                                          "summary": st.get("summary"),
                                          "task": st.get("task", ""),
                                          "elapsed": st.get("elapsed", 0),
                                          "activity": st.get("activity") or {},
                                          "loop": st.get("loop"),
                                          "model": st.get("model")}
                                cache[sid] = {
                                    "out_ts": out_ts,
                                    "computed_at": now,
                                    "since_ts": now - result["elapsed"],
                                    "result": result,
                                }
                            # Hook events (Claude Code hooks → sf_agent_hook.py)
                            # are exact turn/permission transitions — while
                            # fresh they override the screen/transcript guess.
                            hk = self._hook_events.get(sid)
                            if hk and now - hk["ts"] <= self._HOOK_TTL:
                                result = self._apply_hook_state(result, hk, now)
                            # 排程面板用：標出被 scheduler/auto 啟動的頁籤
                            result["lifecycle_source"] = getattr(s, "_lifecycle_source", "")
                            # A tab stopped on a dialog is not idle and not
                            # working: it is waiting for a person, and from the
                            # outside its silence is indistinguishable from
                            # being done. Checked here, in the thread that is
                            # already reading this tab's screen, so the tab list
                            # — pulled by every paired phone every few seconds —
                            # stays free of capture-pane forks. Only tabs that
                            # look quiet are checked; one mid-turn cannot be
                            # sitting on a startup menu.
                            if result.get("state") in ("", "idle", "done", "unknown"):
                                result["blocked"] = self._blocked_reason_cached(
                                    sid, out_ts, now)
                            else:
                                self._blocked_cache.pop(sid, None)
                                result["blocked"] = ""
                            out[sid] = result
                        except Exception:
                            out[sid] = {"state": "unknown", "dot": "",
                                        "summary": "", "activity": {}}
                            cache.pop(sid, None)
                    if out and self._window:
                        # Push only when something besides `elapsed` changed,
                        # or on a slow heartbeat so elapsed keeps ticking —
                        # idle fleets stop waking the webview 1.7×/s for
                        # identical payloads.
                        key = json.dumps(
                            {k: {kk: vv for kk, vv in v.items() if kk != "elapsed"}
                             for k, v in out.items()}, sort_keys=True)
                        if key != last_push["key"] or now - last_push["at"] >= PUSH_HEARTBEAT:
                            payload = json.dumps(out)
                            try:
                                self._window.evaluate_js(
                                    f'window.__sfAgentStatus && window.__sfAgentStatus({payload})')
                                last_push["key"] = key
                                last_push["at"] = now
                            except Exception:
                                main._swallow("_start_status_monitor.monitor:2357")
                except Exception:
                    main._swallow("_start_status_monitor.monitor:2359")
                time.sleep(0.6)

        threading.Thread(target=monitor, daemon=True).start()
