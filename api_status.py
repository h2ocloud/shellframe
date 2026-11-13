"""Api mixin — Agent 狀態域（God-class 分批拆解 第二批）.

hook 事件驅動的精確狀態、狀態 hook 安裝開關、定期狀態監看。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import agent_status
import bridge_home
import sf_log
import sf_notify
import tab_delivery
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
        if event == "Stop":     # a [[SF:RED]] question: kept for /link/bots, and buttons on Telegram when it is on
            threading.Thread(target=bridge_home.on_turn_end, args=(self, sid), daemon=True).start()
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

    # The one synchronous entry: Stop asks whether an unanswered hand-off should keep
    # the agent going (sf_agent_hook.py --stop-check, agent_link.stop_check).
    _STOP_CHECK = "--stop-check"

    # A tab started under a ShellFrame account profile reads that profile's
    # settings.json as its *user* settings; ~/.claude/settings.json reaches it only
    # as project settings when the tab's working directory happens to be ~. So the
    # hooks go into every profile too, or tabs elsewhere never report their state.
    _CLAUDE_PROFILES_ROOT = Path.home() / ".config" / "shellframe" / "account-profiles" / "claude"

    def _status_hook_targets(self) -> list:
        """[(label, settings.json path)] every Claude tab may take its hooks from."""
        targets = [("~/.claude", self._CLAUDE_SETTINGS_PATH)]
        try:
            for d in sorted(x for x in Path(self._CLAUDE_PROFILES_ROOT).iterdir() if x.is_dir()):
                targets.append((f"profile {d.name}", d / "settings.json"))
        except OSError:
            pass
        return targets

    def _hooks_installed_in(self, p: Path) -> bool:
        try:
            hooks = (json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}).get("hooks") or {}
        except Exception:
            return False
        return (all(any("sf_agent_hook.py" in json.dumps(g) for g in (hooks.get(ev) or []))
                    for ev in self._HOOK_EVENTS)
                and any(self._STOP_CHECK in json.dumps(g) for g in (hooks.get("Stop") or [])))

    def _write_status_hooks(self, p: Path, enabled: bool) -> str:
        """Install/remove the status hook entries in one settings.json. Merge is
        surgical: only groups whose command references sf_agent_hook.py are
        touched; every other key and hook survives. Returns an error or ''."""
        try:
            cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception as e:
            return f"{p}: unreadable ({e})"
        hooks = cfg.setdefault("hooks", {})
        if enabled:
            for ev in self._HOOK_EVENTS:
                groups = hooks.setdefault(ev, [])
                if any("sf_agent_hook.py" in json.dumps(g) and self._STOP_CHECK not in json.dumps(g)
                       for g in groups):
                    continue
                groups.append({"matcher": "", "hooks": [{
                    "type": "command", "command": self._sf_hook_command(),
                    "async": True, "timeout": 10}]})
            stop = hooks.setdefault("Stop", [])
            if not any(self._STOP_CHECK in json.dumps(g) for g in stop):
                stop.append({"matcher": "", "hooks": [{
                    "type": "command", "command": f"{self._sf_hook_command()} {self._STOP_CHECK}",
                    "timeout": 5}]})
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
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(p) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception as e:
            return f"{p}: write failed ({e})"
        return ""

    def get_status_hooks_info(self) -> str:
        """Settings UI: are the status hooks in every settings.json a Claude tab reads?"""
        rows = []
        try:
            for label, p in self._status_hook_targets():
                rows.append({"label": label, "path": str(p), "installed": self._hooks_installed_in(p)})
        except Exception:
            main._swallow("Api.get_status_hooks_info:2188")
        done = sum(1 for r in rows if r["installed"])
        return json.dumps({"installed": bool(rows) and done == len(rows),
                           "installed_count": done, "total": len(rows), "targets": rows,
                           "settings_path": str(self._CLAUDE_SETTINGS_PATH)})

    def set_status_hooks_enabled(self, enabled: bool) -> str:
        """Install/remove the status hooks in ~/.claude and in every account profile."""
        errors = [e for e in (self._write_status_hooks(p, bool(enabled))
                              for _label, p in self._status_hook_targets()) if e]
        if not enabled:
            self._hook_events.clear()
        info = json.loads(self.get_status_hooks_info())
        if errors:
            info["error"] = "; ".join(errors)[:300]
        return json.dumps(info)

    def _last_activity_at(self, sid: str, s, out_ts: float = 0.0) -> float:
        """When this tab's agent last did something real: the newest of its
        transcript's last write and its last hook event. Both survive a ShellFrame
        restart, unlike the PTY output time, which every reattach repaint resets
        (after a restart every tab looked about 45 s idle). Falls back to the PTY
        output time for a CLI with no transcript file."""
        best = 0.0
        try:
            res, _age = self._status_tracker.last_result(sid)
            best = float((res or {}).get("activity_at") or 0)
        except Exception:
            best = 0.0
        hk = (getattr(self, "_hook_events", None) or {}).get(sid) or {}
        best = max(best, float(hk.get("ts") or 0))
        return best or float(out_ts or 0)

    def _sync_status_hooks_to_profiles(self) -> int:
        """Hooks were switched on (they are in ~/.claude): make sure every account
        profile has them too, including one created since. Returns how many files
        were changed. Never removes anything."""
        try:   # switched on = the status hooks are in ~/.claude (older installs lack the stop check)
            main_hooks = json.loads(self._CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8")).get("hooks") or {}
        except Exception:
            return 0
        if not any("sf_agent_hook.py" in json.dumps(g) for g in (main_hooks.get("Stop") or [])):
            return 0
        changed = 0
        for _label, p in self._status_hook_targets():
            if not self._hooks_installed_in(p) and not self._write_status_hooks(p, True):
                changed += 1
        return changed

    HOOK_QUIET_S = 90.0

    def _quiet_hook_sids(self, now: float) -> list:
        """Tabs whose hooks still say working but which have shown no sign of life —
        no hook event and no screen output — for HOOK_QUIET_S. A working agent keeps
        painting (Claude Code's spinner and elapsed timer tick while it thinks and
        while a tool runs), so silence this long means the turn ended without a Stop
        (an interrupt). Only those verdicts are dropped; the screen decides again."""
        out = []
        for sid, hk in list(self._hook_events.items()):
            if hk.get("state") != "working":
                continue
            s = self.sessions.get(sid)
            if s is None:
                continue
            last = max(float(hk.get("ts") or 0), float(getattr(s, "_last_output_activity_time", 0) or 0))
            if now - last >= self.HOOK_QUIET_S:
                out.append(sid)
        return out

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

    def _notify_transitions(self, out: dict):
        """Feed one monitor pass to the optional webhook notifier (sf_notify).
        A no-op unless the user configured a URL and `auto`; never raises."""
        try:
            notifier = getattr(self, "_auto_notifier", None)
            if notifier is None:
                notifier = self._auto_notifier = sf_notify.AutoNotifier(
                    log=sf_log._dlog, redact=lambda t: agent_status.redact(t, 200))

            def label_of(sid):
                s = self.sessions.get(sid)
                return getattr(s, "_custom_label", None) or (
                    s.cmd.split()[0] if s is not None and s.cmd else sid)
            notifier.process(out, label_of)
        except Exception:
            sf_log._swallow("_notify_transitions")
        push = getattr(self, "_push_observe", None)   # the phones (api_push); returns at once without one
        if push:
            push(out)

    def _start_status_monitor(self):
        """Background thread: every ~600ms compute each tab's agent status from
        its transcript/rollout log (+ screen wording) and push to the webview.
        Fully isolated from the PTY/output path — any failure just yields
        'unknown' and the browser heuristic keeps working."""
        if self._status_started:
            return
        self._status_started = True
        try:   # profiles created since the hooks were switched on get them too
            n = self._sync_status_hooks_to_profiles()
            if n:
                sf_log._dlog("hookstat", f"status hooks added or completed in {n} Claude settings file(s)")
        except Exception:
            sf_log._swallow("status:sync_profile_hooks")

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
            # 中斷對話時 Claude Code 不一定發 Stop，hook 會一直停在 working
            # （2026-09-03 回報：「有些情況我會中斷對話，這時候燈號就不會變動了」）。
            # 以前每 5 分鐘把所有分頁的 hook 狀態一起清掉；那也清掉了明明還在跑的
            # 分頁（長思考、長指令），清完到下一個 hook 之前 `sfctl state` 都說 done
            # （2026-11-09 實測 13:00:45 清掉、13:01 連問四次都是 done）。現在只交還
            # 「真的安靜」的分頁：hook 停在 working，而 hook 與畫面都 HOOK_QUIET_S 沒動靜。
            cache = self._status_cache  # sid -> {out_ts, computed_at, since_ts, result}
            last_push = {"key": None, "at": 0.0}
            while True:
                try:
                    if not self._auto_status_enabled() or not self._window:
                        time.sleep(1.0)
                        continue
                    now = time.time()
                    getattr(self, "_connect_tick", lambda _n: None)(now)       # connections: the hourly health check (api_connect)
                    for stale in [k for k in list(cache) if k not in self.sessions]:
                        cache.pop(stale, None)
                    for stale in [k for k in list(self._hook_events) if k not in self.sessions]:
                        self._hook_events.pop(stale, None)
                    quiet = self._quiet_hook_sids(now)
                    for qsid in quiet:
                        self._hook_events.pop(qsid, None)
                        cache.pop(qsid, None)
                    if quiet:
                        main._dlog("status", f"hook 停在 working 但已安靜：{','.join(quiet)} 交回畫面判斷")
                    out = {}
                    for sid, s in list(self.sessions.items()):
                        try:
                            out_ts = getattr(s, "_last_output_activity_time", 0.0)
                            c = cache.get(sid)
                            # ShellBot is driving the tab's own menu (a model switch, tab_model): its last state
                            # stands, so the menu is not read as a question for the person
                            if c and (sid in tab_delivery.HOLD or (c["out_ts"] == out_ts
                                                                   and now - c["computed_at"] < FORCE_REFRESH)):
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
                                          "model": st.get("model"),
                                          "activity_at": st.get("activity_at") or 0}
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
                    self._notify_transitions(out)
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
