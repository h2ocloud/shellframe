"""Api mixin — 遠端控制域（God-class 分批拆解 第二批）.

sfctl 指令檔監看與 _execute_sfctl 指令分派、本機 HTTP API 伺服器。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import agent_grok
from sf_config import CONFIG_LOCK
from api_host import main


class RemoteApiMixin:
    def sfctl_call(self, cmd: str, args_json: str = "{}") -> str:
        """Let the web UI reach a dispatch command directly.

        The UI has a bespoke Api method per feature, which is fine for the big
        ones but heavy for a small settings panel. Groups are configured through
        the same commands the CLI, Telegram and paired phones use, so there is
        exactly one implementation to keep correct.

        Deliberately narrow: only the group commands are reachable, so this does
        not quietly become a way to run anything from the page."""
        allowed = {"group_list", "group_save", "group_delete"}
        if cmd not in allowed:
            return json.dumps({"success": False,
                               "message": f"{cmd} is not callable from the UI"})
        try:
            args = json.loads(args_json or "{}")
        except Exception:
            args = {}
        try:
            return json.dumps(self._execute_sfctl(cmd, args), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)})

    def _start_command_watcher(self):
        """Watch for commands from sfctl CLI (file-based IPC)."""
        def _command_paths() -> list[Path]:
            paths = []
            legacy = Path(self._CMD_FILE)
            if legacy.exists():
                paths.append(legacy)
            cmd_dir = Path(self._CMD_DIR)
            try:
                cmd_dir.mkdir(parents=True, exist_ok=True)
                paths.extend(sorted(cmd_dir.glob("*.json")))
            except OSError as e:
                main._dlog("sfctl", f"cmd dir unavailable: {e}")
            return paths

        def watcher():
            main._dlog("sfctl", f"watcher started cmd_file={self._CMD_FILE} cmd_dir={self._CMD_DIR}")
            while True:
                try:
                    time.sleep(0.5)
                    paths = _command_paths()
                    if not paths:
                        continue
                    for path in paths:
                        try:
                            with open(path, encoding='utf-8') as f:
                                cmd_data = json.load(f)
                            path.unlink(missing_ok=True)
                        except (json.JSONDecodeError, IOError, OSError) as e:
                            main._dlog("sfctl", f"failed reading cmd path={path}: {e}")
                            try:
                                path.unlink(missing_ok=True)
                            except OSError:
                                main._swallow("_start_command_watcher.watcher:5638")
                            continue

                        # Ignore stale commands (older than 30s)
                        if time.time() - cmd_data.get("ts", 0) > 30:
                            main._dlog("sfctl", f"ignored stale cmd={cmd_data.get('cmd')!r}")
                            continue

                        cmd = cmd_data.get("cmd", "")
                        args = cmd_data.get("args", {})
                        main._dlog("sfctl", f"exec cmd={cmd!r}")
                        try:
                            result = self._execute_sfctl(cmd, args)
                        except Exception as e:
                            import traceback
                            main._dlog("sfctl", f"execute crashed cmd={cmd!r}: {e}\n{traceback.format_exc()}")
                            result = {"success": False, "message": f"sfctl crashed: {e}"}

                        try:
                            result_file = str(cmd_data.get("result_file") or self._RESULT_FILE)
                            tmp = result_file + ".tmp"
                            with open(tmp, "w", encoding='utf-8') as f:
                                json.dump(result, f, ensure_ascii=False)
                                f.flush()
                                try:
                                    os.fsync(f.fileno())
                                except OSError:
                                    main._swallow("_start_command_watcher.watcher:5665")
                            os.replace(tmp, result_file)
                        except IOError as e:
                            main._dlog("sfctl", f"failed writing result: {e}")
                except Exception as e:
                    # Never let the remote-control thread die; TG recovery
                    # depends on sfctl staying alive after bad edge cases.
                    import traceback
                    main._dlog("sfctl", f"watcher loop crashed: {e}\n{traceback.format_exc()}")
        threading.Thread(target=watcher, daemon=True).start()

    def _start_api_server(self):
        """Start the optional local HTTP API (opt-in via config api_server.enabled).

        Loopback + token + IP whitelist. Wraps _execute_sfctl. A blank token is
        auto-generated and persisted on first enable so the surface is never
        unauthenticated. Failures (missing module, bind error) are non-fatal."""
        if getattr(self, "_api_httpd", None):
            return  # already running (hot re-enable from Settings)
        try:
            cfg = (main.load_config().get("api_server") or {})
        except Exception:
            cfg = {}
        if not cfg.get("enabled"):
            return
        token = (cfg.get("token") or "").strip()
        if not token:
            import secrets
            token = secrets.token_urlsafe(24)
            try:
                with CONFIG_LOCK:
                    full = main.load_config()
                    full.setdefault("api_server", {})["token"] = token
                    main.save_config(full)
            except Exception as e:
                main._dlog("api", f"failed persisting generated token: {e}")
        try:
            import api_server
        except Exception as e:
            main._dlog("api", f"api_server import failed: {e}")
            return
        # Stamp the live event bus onto the bridge so signal transitions
        # (RED/YELLOW/GREEN) surface to API clients via GET /events.
        self.api_event_bus = api_server.EVENT_BUS
        try:
            ver = json.loads((Path(__file__).parent / "version.json").read_text()).get("version", "0")
        except Exception:
            ver = "0"
        httpd, _thread = api_server.start(
            self._execute_sfctl,
            host=cfg.get("host", "127.0.0.1"),
            port=cfg.get("port", 8765),
            token=token,
            allowed_ips=cfg.get("allowed_ips") or ["127.0.0.1", "::1"],
            version=ver,
            log=lambda m: main._dlog("api", m),
        )
        self._api_httpd = httpd  # None on bind failure → info shows not running

    def get_api_server_info(self) -> str:
        """Settings UI: current Local HTTP API state."""
        try:
            cfg = (main.load_config().get("api_server") or {})
        except Exception:
            cfg = {}
        host = cfg.get("host", "127.0.0.1")
        port = cfg.get("port", 8765)
        return json.dumps({
            "enabled": bool(cfg.get("enabled")),
            "host": host,
            "port": port,
            "token": cfg.get("token") or "",
            "running": bool(getattr(self, "_api_httpd", None)),
            "docs_url": f"http://{host}:{port}/docs",
        })

    def set_api_server_enabled(self, enabled: bool) -> str:
        """Settings UI toggle — hot start/stop, no restart needed.
        Enabling auto-generates and persists a token if blank (fail-closed
        stays intact: _start_api_server never serves without a token)."""
        try:
            with CONFIG_LOCK:
                full = main.load_config()
                full.setdefault("api_server", {})["enabled"] = bool(enabled)
                main.save_config(full)
        except Exception as e:
            main._dlog("api", f"failed saving api_server.enabled: {e}")
        if enabled:
            self._start_api_server()
        else:
            httpd = getattr(self, "_api_httpd", None)
            self._api_httpd = None
            if httpd:
                try:
                    httpd.shutdown()
                    httpd.server_close()
                except Exception as e:
                    main._dlog("api", f"api_server shutdown failed: {e}")
        return self.get_api_server_info()

    def _execute_sfctl(self, cmd: str, args: dict = None) -> dict:
        """Execute a sfctl command and return result dict."""
        args = args or {}
        if cmd == "new_session":
            try:
                preset_cmd = args.get("cmd", "claude")
                cols = args.get("cols", 200)
                rows = args.get("rows", 50)
                source = args.get("source", "sfctl")
                handoff = bool(args.get("handoff", False))
                sid = self.new_session(preset_cmd, cols, rows, source=source, handoff=handoff)
                return {
                    "success": True,
                    "message": f"Created session {sid}",
                    "details": {"sid": sid, "cmd": preset_cmd},
                }
            except Exception as e:
                return {"success": False, "message": f"Failed: {e}"}

        elif cmd == "close_session":
            try:
                sid = args.get("sid", "")
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                self.close_session(
                    sid,
                    reason=args.get("reason", "sfctl"),
                    handoff=bool(args.get("handoff", False)),
                    summary_path=args.get("summary_path", "") or "",
                )
                return {"success": True, "message": f"Closed {sid}"}
            except Exception as e:
                return {"success": False, "message": f"Failed: {e}"}

        elif cmd == "relaunch":
            try:
                sid = args.get("sid", "")
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                raw = self.relaunch_session(sid)
                res = json.loads(raw) if isinstance(raw, str) else raw
                return {"success": res.get("success", False),
                        "message": res.get("message", ""),
                        "details": {k: v for k, v in res.items()
                                    if k not in ("success", "message")}}
            except Exception as e:
                return {"success": False, "message": f"relaunch failed: {e}"}

        elif cmd == "restart":
            try:
                # `sfctl restart` and TG /restart are already explicit asks, and
                # on Windows they are often the only way to reach the machine —
                # so they carry the confirmation rather than bouncing back a
                # prompt no one is there to answer. Pass confirm=false to get
                # the warning instead.
                result_json = self.restart_app(confirm=bool(args.get("confirm", True)))
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return {
                    "success": result.get("success", False),
                    "message": result.get("message", "Restart triggered"),
                    "details": {k: v for k, v in result.items() if k not in ("success", "message")},
                }
            except Exception as e:
                return {"success": False, "message": f"Restart failed: {e}"}

        elif cmd == "check_update":
            try:
                result = json.loads(self.check_update())
                return {
                    "success": True,
                    "message": ("有新版 v{}".format(result.get("remote"))
                                if result.get("update_available")
                                else "已是最新版 v{}".format(result.get("local"))),
                    "details": result,
                }
            except Exception as e:
                return {"success": False, "message": f"Check update failed: {e}"}

        elif cmd == "update":
            # do_update 是 git pull ＋ 依賴安裝，會跑幾十秒；它自己每一步都有
            # 復原路徑，失敗時會回 recovery 指令而不是把安裝弄壞。更新完不會自動
            # 重啟——跟本機的流程一樣，重啟是另一個明確的動作。
            try:
                result = json.loads(self.do_update())
                return {
                    "success": bool(result.get("success")),
                    "message": result.get("message", "Update finished"),
                    "details": {k: v for k, v in result.items()
                                if k not in ("success", "message")},
                }
            except Exception as e:
                return {"success": False, "message": f"Update failed: {e}"}

        elif cmd == "reload":
            try:
                result_json = self.hot_reload_bridge()
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return {
                    "success": result.get("success", False),
                    "message": result.get("message", "Reload completed"),
                    "details": {
                        "state": result.get("state", "unknown"),
                        "bot": result.get("bot", ""),
                        "sessions": result.get("sessions", 0),
                    }
                }
            except Exception as e:
                return {"success": False, "message": f"Reload failed: {e}"}

        elif cmd == "status":
            if not self.bridge:
                return {
                    "success": True,
                    "message": "Bridge not running",
                    "details": {"state": "stopped", "sessions": len(self.sessions)}
                }
            status = self.bridge.get_status()
            return {
                "success": True,
                "message": f"Bridge {status.get('state', 'unknown')} — @{status.get('bot', '?')}",
                "details": {
                    "state": status.get("state"),
                    "bot": status.get("bot"),
                    "sessions": status.get("sessions", 0),
                    "paused": status.get("paused", False),
                    # docs/ai-skill.md 說 status 是「roster ＋ live per-tab state」，
                    # 但這裡一直只回 bridge 自己的狀態——照著文件用的人看到的是
                    # 一行 bridge 狀態，然後以為分頁狀態要另外找。補上每個分頁的
                    # 那一列（讀 monitor 已經算好的快照，零額外成本）。
                    # with_error=False：status 可能被輪詢，而解析錯誤要讀
                    # transcript，逐頁做等於每一輪 N 次檔案讀取。要錯誤就用
                    # `sfctl state`——那是問單一分頁、或只列有問題的那幾個。
                    "states": [self._session_state_row(sid, s, with_error=False)
                               for sid, s in ((i, self.sessions.get(i))
                                              for i in self._ordered_sids())
                               if s is not None],
                }
            }

        elif cmd == "state":
            # 給外部調度者的低成本狀態查詢。刻意跟 `list` 分開：list 會被遠端
            # peer 週期性拉，欄位多一個就是每台每輪都多付；而 state 是「問一次
            # 某個分頁現在怎麼了」，可以負擔解析 transcript 的成本。
            #
            # 輸出只有結構化欄位，沒有任何畫面內容——調度者要判斷的是「能不能
            # 派工給它」，不是讀它的對話。錯誤訊息會先遮憑證再截短。
            try:
                want = str(args.get("sid") or "").strip()
                want_all = bool(args.get("all"))
                stale_min = float(args.get("stale_min") or 0)
                if not want and not want_all:
                    return {"success": False, "message": "sid required (or --all)"}
                sids = self._ordered_sids() if want_all else [want]
                now = time.time()
                rows, problems = [], []
                for sid in sids:
                    s = self.sessions.get(sid)
                    if not s:
                        if want_all:
                            continue
                        return {"success": False, "message": f"No such session: {sid}"}
                    row = self._state_row(sid, s, now, stale_min=stale_min)
                    rows.append(row)
                    if row["needs_attention"]:
                        problems.append(row)
                if want_all:
                    # --all 只列有問題的：調度者要的是「誰需要我介入」，把 20 個
                    # 正常分頁一起回去只是讓它多讀 20 行。數量仍然回報，這樣
                    # 「沒有問題」跟「沒查到分頁」分得開。
                    return {
                        "success": True,
                        "message": (f"{len(problems)}/{len(rows)} 需要注意"
                                    if problems else f"{len(rows)} 個分頁都正常"),
                        "details": {"states": problems, "checked": len(rows)},
                    }
                return {"success": True, "message": rows[0].get("label") or want,
                        "details": {"states": rows}}
            except Exception as e:
                return {"success": False, "message": f"State failed: {e}"}

        elif cmd == "list":
            # List all sessions with sid + label + alive state, in the same
            # durable order the desktop tab bar uses (drag-reorder persists to
            # config session_order via _ordered_sids). A remote viewer should
            # see the tabs in the user's own order, not raw dict order.
            sessions_info = []
            for sid in self._ordered_sids():
                s = self.sessions.get(sid)
                if not s:
                    continue
                sessions_info.append({
                    "sid": sid,
                    "label": getattr(s, '_custom_label', None) or (s.cmd.split()[0] if s.cmd else sid),
                    "cmd": s.cmd,
                    "alive": s.alive,
                    "bridge_enabled": getattr(s, '_bridge_enabled', True),
                    "glasses_enabled": getattr(s, '_glasses_enabled', False),
                    "provider": main._session_provider(s.cmd),
                    # 遠端檢視端的狀態燈。用 status monitor 算好的快照
                    #（唯讀、零額外成本）。同步頻率就是 peer 拉 /link/info
                    # 的頻率，刻意不另外開高頻通道。
                    "agent_state": self._agent_state_for_list(sid),
                    "agent_activity": self._agent_activity_for_list(sid),
                    # Non-empty when the tab is stopped on a dialog. Carries the
                    # dialog's own wording, so a phone can show what is being
                    # asked instead of only that something is.
                    "agent_blocked": self._agent_blocked_for_list(sid),
                    # Which CLI on which model answered you.
                    "runs_on": main.agent_model.describe(s.cmd),
                    # Frame Link 無縫遠端分頁：對齊對方 PTY 尺寸，alt-screen TUI
                    # （claude/codex）才不會因 cols/rows 不同而畫面錯位。
                    "cols": getattr(s, 'cols', 0),
                    "rows": getattr(s, 'rows', 0),
                    # the glasses bridge needs this to find a codex rollout:
                    # codex has no --session-id, so the only reliable link from
                    # tab to transcript is the fd the process holds open.
                    # Resolved here (cached) rather than in the bridge so the
                    # lsof logic lives in exactly one place, and only for tabs
                    # that are actually open to the glasses — normally zero.
                    "tmux_name": getattr(s, '_tmux_name', None) or "",
                    "transcript": (self._glasses_transcript(sid)
                                   if getattr(s, '_glasses_enabled', False) else ""),
                })
            return {
                "success": True,
                "message": f"{len(sessions_info)} sessions",
                "details": {"sessions": sessions_info},
            }

        elif cmd == "glasses_status":
            st = json.loads(self.get_glasses_status())
            return {"success": True,
                    "message": f"{len(st.get('allowed') or [])} allowed",
                    "details": st}

        elif cmd == "glasses":
            action = str(args.get("action") or "status").lower()
            sids = [x for x in (args.get("sids") or []) if x]
            if action in ("allow", "deny"):
                if not sids:
                    return {"success": False, "message": f"glasses {action} 需要至少一個 sid"}
                changed, missing = [], []
                source = str(args.get("source") or "sfctl")
                for sid in sids:
                    r = json.loads(self.set_session_glasses(sid, action == "allow", source))
                    (changed if r.get("success") else missing).append(sid)
                if not changed:
                    return {"success": False,
                            "message": f"沒有這些分頁：{', '.join(missing)}"}
                verb = "開放" if action == "allow" else "收回"
                msg = f"{verb} {', '.join(changed)}"
                if missing:
                    msg += f"（略過不存在的 {', '.join(missing)}）"
            elif action == "status":
                msg = "glasses status"
            else:
                return {"success": False, "message": f"unknown action {action!r}"}
            return {"success": True, "message": msg,
                    "details": {"text": self._glasses_report()}}

        elif cmd == "roster":
            roster = self._agent_roster_config(main.load_config())
            roles = []
            for role, entry in roster.items():
                roles.append({
                    "role": role,
                    "label": entry.get("label", role),
                    "agent_code": entry.get("agent_code", ""),
                    "responsibility": entry.get("responsibility", ""),
                    "cmd": entry.get("cmd", ""),
                    # Which CLI on which model. Worth showing next to the role:
                    # the whole point of pinning is that you can see at a glance
                    # that the dispatcher is strong and the workers are not.
                    "model": entry.get("model", ""),
                    "runs_on": main.agent_model.describe(entry.get("cmd", "")),
                })
            return {
                "success": True,
                "message": f"{len(roles)} roles",
                "details": {"roles": roles},
            }

        elif cmd == "delegate":
            try:
                return self.delegate_task(args.get("role", ""), args.get("task", ""))
            except Exception as e:
                return {"success": False, "message": f"Delegate failed: {e}"}

        elif cmd == "agent_event":
            try:
                return self._on_agent_event(args)
            except Exception as e:
                return {"success": False, "message": f"agent_event failed: {e}"}

        elif cmd == "send":
            try:
                sid = args.get("sid", "")
                text = args.get("text", "")
                submit = args.get("submit", True)
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                s = self.sessions.get(sid)
                if not s:
                    return {"success": False, "message": f"No such session: {sid}"}
                s._startup_trust_pending = False
                self._send_text_to_session(s, text, submit=submit)
                return {"success": True, "message": f"Sent {len(text)} chars to {sid}"}
            except Exception as e:
                return {"success": False, "message": f"Send failed: {e}"}

        elif cmd == "raw_input":
            # Frame Link 無縫遠端分頁：把遠端使用者的原始鍵盤位元組直接寫進 PTY
            # （方向鍵、Ctrl-C、Enter 都照原樣），不走 send 的 paste-buffer/submit。
            try:
                sid = args.get("sid", "")
                data = args.get("data", "")
                s = self.sessions.get(sid)
                if not s:
                    return {"success": False, "message": f"No such session: {sid}"}
                s.write(data)
                return {"success": True}
            except Exception as e:
                return {"success": False, "message": f"raw_input failed: {e}"}

        elif cmd == "save_paste":
            # Frame Link：把遠端檢視端貼上的圖片位元組（base64）落地成檔案，
            # 回傳路徑，讓對方的 CLI（Claude/Codex）能像本機貼圖那樣讀到。
            try:
                import base64 as _b64
                filename = os.path.basename(args.get("filename", "") or "paste.png")
                data_b64 = args.get("data_b64", "") or ""
                if "," in data_b64 and data_b64[:5].lower() == "data:":
                    data_b64 = data_b64.split(",", 1)[1]
                raw = _b64.b64decode(data_b64)
                if not raw or len(raw) > 64 * 1024 * 1024:
                    return {"success": False, "message": "bad/too-large paste"}
                dest_dir = main.CLAUDE_TMP / "framelink-paste"
                dest_dir.mkdir(parents=True, exist_ok=True)
                stem = re.sub(r"[^\w.\-]", "_", filename) or "paste.png"
                dest = dest_dir / f"{int(time.time()*1000)}_{stem}"
                dest.write_bytes(raw)
                return {"success": True, "details": {"path": str(dest)}}
            except Exception as e:
                return {"success": False, "message": f"save_paste failed: {e}"}

        elif cmd == "raw_screen":
            # Frame Link 無縫遠端分頁的初次上畫：用 tmux capture-pane -e 取「當前
            # 可視畫面」含 ANSI 色碼，前置 clear+home 直接重現對方畫面。這對
            # alt-screen TUI（claude/codex/opencode）才畫得對——cleaned peek 拿到
            # 的是 scrollback 重建版，貼進 xterm 會破圖。
            try:
                sid = args.get("sid", "")
                s = self.sessions.get(sid)
                if not s:
                    return {"success": False, "message": f"No such session: {sid}"}
                tn = getattr(s, "_tmux_name", None)
                if tn:
                    tmux = main._tmux_bin() or "tmux"
                    out = subprocess.run(
                        [tmux, "capture-pane", "-e", "-p", "-t", tn],
                        capture_output=True, text=True, timeout=3)
                    if out.returncode == 0:
                        screen = out.stdout.rstrip("\n").replace("\n", "\r\n")
                        return {"success": True,
                                "details": {"screen": "\x1b[2J\x1b[H" + screen}}
                # 非 tmux session：退回 cleaned history（至少有內容）
                raw = self.get_clean_history(sid, max_lines=200)
                res = json.loads(raw) if isinstance(raw, str) else raw
                txt = (res.get("text") or "").replace("\n", "\r\n")
                return {"success": True,
                        "details": {"screen": "\x1b[2J\x1b[H" + txt}}
            except Exception as e:
                return {"success": False, "message": f"raw_screen failed: {e}"}

        elif cmd == "resize_pty":
            # Frame Link：遠端檢視端把「它的可視尺寸」推過來，讓這台的 PTY
            # reflow 成一樣大——遠端 pane 才撐得滿、alt-screen 不破版。
            try:
                sid = args.get("sid", "")
                cols = int(args.get("cols") or 0)
                rows = int(args.get("rows") or 0)
                if not sid or cols <= 0 or rows <= 0:
                    return {"success": False, "message": "bad sid/cols/rows"}
                if sid not in self.sessions:
                    return {"success": False, "message": f"No such session: {sid}"}
                self.resize(sid, cols, rows)
                return {"success": True}
            except Exception as e:
                return {"success": False, "message": f"resize_pty failed: {e}"}

        elif cmd == "snapshot":
            # Frame Link 手機端：目前可視畫面的彩色快照（tmux capture-pane -e，含 ANSI
            # 與游標位置）。attach 時先貼這張再接增量串流，開起來就是電腦上看到的樣子。
            try:
                sid = args.get("sid", "")
                s = self.sessions.get(sid)
                if not s:
                    return {"success": False, "message": f"No such session: {sid}"}
                tmux_name = getattr(s, "_tmux_name", None)
                if main.IS_WIN or not tmux_name or not shutil.which("tmux"):
                    return {"success": False, "message": "snapshot needs tmux"}
                r = subprocess.run(["tmux", "capture-pane", "-e", "-p", "-t", tmux_name],
                                   capture_output=True, timeout=3)
                if r.returncode != 0:
                    return {"success": False,
                            "message": r.stderr.decode("utf-8", errors="replace").strip()
                            or "capture-pane failed"}
                lines = r.stdout.decode("utf-8", errors="replace").split("\n")
                if lines and lines[-1] == "":
                    lines.pop()
                ansi = "\x1b[0m" + "\r\n".join(lines)
                try:
                    cur = subprocess.run(["tmux", "display-message", "-p", "-t", tmux_name,
                                          "#{cursor_x},#{cursor_y}"],
                                         capture_output=True, timeout=2)
                    cx, cy = cur.stdout.decode().strip().split(",")
                    ansi += f"\x1b[{int(cy) + 1};{int(cx) + 1}H"
                except Exception:
                    pass
                return {"success": True, "message": f"{len(lines)} rows",
                        "details": {"ansi": ansi, "cols": getattr(s, "cols", 0),
                                    "rows": getattr(s, "rows", 0)}}
            except Exception as e:
                return {"success": False, "message": f"snapshot failed: {e}"}

        elif cmd == "voice_inject":
            # Frame Link 手機／手錶語音：走與 TG 語音、桌面麥克風相同的
            # STT → 精煉 → 注入鏈（AI 分頁帶語音 tag 送出；shell 分頁只貼不送）。
            try:
                path = args.get("path", "")
                sid = args.get("sid", "")
                if not path or not os.path.exists(path):
                    return {"success": False, "message": "no audio file"}
                raw = self._mic_transcribe_inject(path, sid)
                res = json.loads(raw) if isinstance(raw, str) else (raw or {})
                if not res.get("ok"):
                    reason = res.get("reason", "stt_failed")
                    msg = {"no_backend": "電腦沒有可用的語音辨識後端（設定 → 語音輸入）",
                           "stt_failed": "語音辨識失敗（沒聽出文字）",
                           "no_audio": "沒有音檔"}.get(reason, reason)
                    return {"success": False, "message": msg, "details": res}
                injected = bool(res.get("injected"))
                return {"success": True,
                        "message": "transcribed" + (" + injected" if injected else " (session gone)"),
                        "details": {"text": res.get("text", ""), "injected": injected,
                                    "ai": bool(res.get("ai"))}}
            except Exception as e:
                return {"success": False, "message": f"voice_inject failed: {e}"}

        elif cmd == "history":
            # Frame Link 遠端分頁的上滑歷史。刻意不併進 peek：peek 是給手機端／
            # master 編排用的，它剝掉空行、丟掉 ANSI 與 source，貼回 xterm 會破圖。
            # 這裡原封不動把 overlay 要的三個欄位（text / source / ansi）帶回去。
            try:
                sid = args.get("sid", "")
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                if sid not in self.sessions:
                    return {"success": False, "message": f"No such session: {sid}"}
                # 上限比本機的 10000 低：帶 ANSI 的一萬行要走簽章連線，是好幾百 KB，
                # 而 overlay 本來就是拿來回看最近的內容。
                max_lines = max(1, min(int(args.get("lines", 3000)), 3000))
                cols = int(args.get("cols", 0) or 0)
                raw = self.get_clean_history(sid, max_lines=max_lines,
                                             ansi=True, cols=cols)
                result = json.loads(raw) if isinstance(raw, str) else raw
                if not result.get("success"):
                    return {"success": False,
                            "message": result.get("reason", "history failed")}
                return {
                    "success": True,
                    "message": f"{len(result.get('text', '').splitlines())} lines",
                    "details": {
                        "text": result.get("text", ""),
                        "source": result.get("source", ""),
                        "ansi": bool(result.get("ansi")),
                    },
                }
            except Exception as e:
                return {"success": False, "message": f"History failed: {e}"}

        elif cmd == "peek":
            try:
                sid = args.get("sid", "")
                max_lines = int(args.get("lines", 200))
                if not sid:
                    return {"success": False, "message": "No sid provided"}
                if sid not in self.sessions:
                    return {"success": False, "message": f"No such session: {sid}"}
                raw = self.get_clean_history(sid, max_lines=max_lines)
                result = json.loads(raw) if isinstance(raw, str) else raw
                if not result.get("success"):
                    return {"success": False, "message": result.get("reason", "peek failed")}
                text = result.get("text", "")
                # Keep only the last max_lines non-empty lines for master orchestration use
                lines = [l for l in text.split("\n") if l.strip()]
                tail = "\n".join(lines[-max_lines:])
                return {
                    "success": True,
                    "message": f"{len(lines)} lines",
                    "details": {"text": tail},
                }
            except Exception as e:
                return {"success": False, "message": f"Peek failed: {e}"}

        elif cmd == "rename":
            try:
                sid = args.get("sid", "")
                name = args.get("name", "")
                if not sid or not name:
                    return {"success": False, "message": "sid and name required"}
                result_json = self.rename_session(sid, name)
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                if result.get("success"):
                    return {"success": True, "message": f"Renamed {sid} to {name}"}
                return {"success": False, "message": "Rename failed"}
            except Exception as e:
                return {"success": False, "message": f"Rename failed: {e}"}

        elif cmd in ("group_list", "group_save", "group_delete",
                     "group_send", "group_conversation"):
            try:
                import agent_group
            except Exception as e:
                return {"success": False, "message": f"agent_group unavailable: {e}"}
            cfg = main.load_config()
            if not (cfg.get("settings") or {}).get("experimental_groups", False):
                return {"success": False,
                        "message": "群組是實驗性功能，請先在設定裡打開（實驗性 → 角色群組）"}
            roster = self._agent_roster_config(cfg)
            groups = agent_group.normalize(cfg.get("groups"))

            if cmd == "group_list":
                out = []
                for name, g in groups.items():
                    out.append({"name": name, "roles": g["roles"],
                                "created": g.get("created"),
                                "runs_on": {r: main.agent_model.describe(
                                    (roster.get(r) or {}).get("cmd", ""))
                                    for r in g["roles"]}})
                return {"success": True, "message": f"{len(out)} groups",
                        "details": {"groups": out, "roles": list(roster.keys())}}

            if cmd == "group_save":
                ok, name, roles, err = agent_group.validate(
                    args.get("name", ""), args.get("roles") or [], roster)
                if not ok:
                    return {"success": False, "message": err}
                def _mut(c):
                    gs = agent_group.normalize(c.get("groups"))
                    gs[name] = {"roles": roles,
                                "created": (gs.get(name) or {}).get("created") or time.time()}
                    c["groups"] = gs
                main.update_config(_mut)
                return {"success": True, "message": f"已儲存群組「{name}」",
                        "details": {"name": name, "roles": roles}}

            if cmd == "group_delete":
                name = str(args.get("name") or "").strip()
                if name not in groups:
                    return {"success": False, "message": f"找不到群組「{name}」"}
                def _mut(c):
                    gs = agent_group.normalize(c.get("groups"))
                    gs.pop(name, None)
                    c["groups"] = gs
                main.update_config(_mut)
                return {"success": True, "message": f"已刪除群組「{name}」"}

            name = str(args.get("name") or "").strip()
            g = groups.get(name)
            if not g:
                names = "、".join(groups.keys()) or "(無)"
                return {"success": False,
                        "message": f"找不到群組「{name}」。已有：{names}"}
            members = g["roles"]

            if cmd == "group_send":
                text = str(args.get("text") or "").strip()
                if not text:
                    return {"success": False, "message": "訊息必填"}
                a2a_on = bool((cfg.get("settings") or {}).get("experimental_a2a", False))

                def _one(role):
                    body = agent_group.format_group_message(name, members, role, text,
                                                            a2a=a2a_on)
                    try:
                        # delegate opens the role's tab when it is not running,
                        # which is what makes a group usable after a restart.
                        r = self.delegate_task(role, body) or {}
                        return role, bool(r.get("success")), str(r.get("message") or "")
                    except Exception as e:
                        return role, False, str(e)

                # In parallel, because each cold member costs the time its CLI
                # takes to boot. Serially, a five-member group with nothing open
                # holds the command loop for minutes, and every other sfctl call
                # — including the ones used to find out what is wrong — queues
                # behind it. Parallel makes the fan-out cost one member's wait.
                results = []
                if len(members) == 1:
                    results.append(_one(members[0]))
                else:
                    with concurrent.futures.ThreadPoolExecutor(
                            max_workers=min(len(members), agent_group.MAX_MEMBERS)) as pool:
                        results = list(pool.map(_one, members))

                sent = [r for r, ok, _ in results if ok]
                failed = [(r, why) for r, ok, why in results if not ok]
                # The reason travels with the failure. "失敗：知庫" tells you
                # nothing you can act on; "知庫：等你選：Switch to Sonnet 5…"
                # tells you exactly which tab to open and what it is asking.
                tail = ("（" + "；".join(f"{r}：{why}" for r, why in failed) + "）") if failed else ""
                return {"success": bool(sent),
                        "message": f"已送給 {len(sent)}/{len(members)} 個角色{tail}",
                        "details": {"sent": sent,
                                    "failed": [r for r, _ in failed],
                                    "failures": [{"role": r, "reason": w} for r, w in failed],
                                    "members": members}}

            # group_conversation — one thread, every reply attributed.
            limit = max(1, min(int(args.get("limit") or 120), 400))
            # Two different silences, kept apart on purpose. `offline` is "this
            # role has no tab": nothing was ever going to answer. `quiet` is "the
            # tab is open but has written no transcript yet", which is the normal
            # state for the seconds after a fan-out opens one — reporting that as
            # offline made a working send look like a failed one.
            per_member, offline, quiet = {}, [], []
            for role in members:
                entry = roster.get(role) or {}
                label = entry.get("label") or role
                sid, _sess = self._find_session_by_label(label)
                if not sid:
                    offline.append(role)
                    continue
                res = self._execute_sfctl("conversation",
                                          {"sid": sid, "limit": limit}) or {}
                if res.get("success"):
                    per_member[role] = (res.get("details") or {}).get("turns") or []
                else:
                    quiet.append(role)
            note = ""
            if quiet:
                note = f"（{'、'.join(quiet)} 分頁剛開，還沒有對話記錄）"
            return {"success": True,
                    "message": f"{len(per_member)}/{len(members)} 個角色有對話{note}",
                    "details": {"name": name, "members": members,
                                "offline": offline, "quiet": quiet,
                                "turns": agent_group.merge_turns(per_member, limit)}}

        elif cmd == "conversation":
            # Structured turns for a chat-style view, instead of terminal bytes.
            # Reuses the transcript reader the scroll-up history already relies
            # on, so Claude and Codex both normalise to the same event shape.
            try:
                sid = args.get("sid", "")
                limit = max(1, min(int(args.get("limit") or 80), 400))
                s_obj = self.sessions.get(sid)
                if not s_obj:
                    return {"success": False, "message": f"No such session: {sid}"}
                # `_worker_ctx` rather than a hand-rolled dict. The local one was
                # missing `config_dir`, and a tab pinned to an account profile
                # keeps its transcript under that profile — so the lookup went to
                # the global path, found nothing, and every such tab reported
                # "no conversation" while it was visibly answering on screen.
                # That is the chat view on the phone, not a corner case.
                worker = self._worker_ctx(sid, s_obj)
                # opencode keeps no transcript file — its history is in the
                # shared session SQLite — so it needs its own reader rather than
                # a path. Without this every opencode tab reported having no
                # conversation and the phone drew an empty chat for a tab that
                # was plainly holding one.
                if main._session_provider(s_obj.cmd) == "opencode":
                    turns = main.agent_status.opencode_turns(worker, limit)
                    if not turns:
                        return {"success": False,
                                "message": "這個 opencode 分頁還沒有可讀的對話"
                                           "（分頁標題還沒被 opencode 標上，或還沒送出過訊息）"}
                    return {"success": True, "message": f"{len(turns)} turns",
                            "details": {"format": "opencode", "turns": turns,
                                        "label": getattr(s_obj, "_custom_label", None)
                                                 or (s_obj.cmd.split()[0] if s_obj.cmd else sid)}}
                path = main.agent_status.resolve_transcript(worker)
                if not path:
                    return {"success": False,
                            "message": "這個分頁沒有可讀的對話記錄（可能還沒送出過訊息）"}
                fmt, evs, err = main.agent_status._read_tail_events(str(path))
                if err and not evs:
                    return {"success": False, "message": f"transcript unreadable: {err}"}
                turns = [e for e in evs
                         if e.get("kind") in ("user_msg", "assistant_text",
                                              "tool_call", "error")]
                return {"success": True, "message": f"{len(turns)} turns",
                        "details": {"format": fmt, "turns": turns[-limit:],
                                    "label": getattr(s_obj, "_custom_label", None)
                                             or (s_obj.cmd.split()[0] if s_obj.cmd else sid)}}
            except Exception as e:
                return {"success": False, "message": f"conversation failed: {e}"}

        elif cmd == "a2a_history":
            try:
                import agent_link
                return {"success": True,
                        "details": {"entries": agent_link.history(int(args.get("limit") or 100)),
                                    "enabled": bool((main.load_config().get("settings") or {})
                                                    .get("experimental_a2a", False))}}
            except Exception as e:
                return {"success": False, "message": f"a2a_history failed: {e}"}

        elif cmd == "reorder":
            # Remote drag-to-reorder (the phone app). Deliberately the *same*
            # entry point the desktop tab drag uses, so session_order lands on
            # disk and the Telegram /1 /2 numbering follows — reorder on either
            # surface and the other one agrees on the next refresh.
            try:
                order = args.get("order") or []
                if not isinstance(order, list) or not order:
                    return {"success": False, "message": "order (non-empty list) required"}
                result_json = self.reorder_sessions(json.dumps(order))
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return result if isinstance(result, dict) else {"success": True}
            except Exception as e:
                return {"success": False, "message": f"Reorder failed: {e}"}

        elif cmd == "ui_sessions":
            # 診斷用：回傳 webview「眼中」的 tabs/labels。專治「後端說有、
            # 畫面沒有」各說各話——直接問 UI 而不是用後端狀態推論。
            # 注意：evaluate_js 的「回傳值」在背景 thread 會卡死（WKWebView
            # round-trip 不回來），所以走 fire-and-forget＋JS 回呼
            # report_ui_state 存值、這裡輪詢取件。
            try:
                if not self._window:
                    return {"success": False, "message": "no window"}
                self._ui_state_report = None
                self._window.evaluate_js(
                    'try { pywebview.api.report_ui_state('
                    'window.__sfUiState ? __sfUiState() : "{}"); } catch(e) {}')
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    if self._ui_state_report is not None:
                        return {"success": True, "message": self._ui_state_report}
                    time.sleep(0.1)
                return {"success": False, "message": "UI 未回報（webview 無回應）"}
            except Exception as e:
                return {"success": False, "message": f"ui_sessions failed: {e}"}

        elif cmd == "history_audit":
            try:
                sid = args.get("sid", "")
                if not sid:
                    if self.bridge and self.bridge.slots:
                        sid = next(iter(self.bridge.slots))
                    elif self.sessions:
                        sid = next(iter(self.sessions))
                if not sid:
                    return {"success": False, "message": "no sessions"}
                raw = self.history_audit(sid)
                return json.loads(raw) if isinstance(raw, str) else raw
            except Exception as e:
                return {"success": False, "message": f"history_audit failed: {e}"}

        elif cmd == "do_update":
            try:
                result_json = self.do_update()
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return {
                    "success": result.get("success", False),
                    "message": result.get("message", ""),
                    "details": {
                        "version": result.get("version", "?"),
                        "needs_restart": result.get("needs_restart", False),
                        "can_hot_reload": result.get("can_hot_reload", False),
                    }
                }
            except Exception as e:
                return {"success": False, "message": f"Update failed: {e}"}

        elif cmd == "board_list":
            tasks = main.board.list_tasks()
            return {
                "success": True,
                "message": f"{len(tasks)} tasks",
                "details": {"enabled": self._board_enabled(), "tasks": tasks},
            }

        elif cmd == "board_add":
            try:
                task = main.board.add_task(
                    args.get("title", ""),
                    assignee=args.get("assignee", "unassigned"),
                    status=args.get("status", "todo"),
                    difficulty=args.get("difficulty", "medium"),
                    notes=args.get("notes", ""),
                )
                return {"success": True, "message": f"Added {task['id']}", "details": {"task": task}}
            except Exception as e:
                return {"success": False, "message": f"board_add failed: {e}"}

        elif cmd == "board_update":
            try:
                task_id = args.get("id", "")
                fields = {k: args[k] for k in ("title", "assignee", "status", "difficulty", "notes") if k in args}
                task = main.board.update_task(task_id, **fields)
                if task is None:
                    return {"success": False, "message": f"No such task: {task_id}"}
                return {"success": True, "message": f"Updated {task_id}", "details": {"task": task}}
            except Exception as e:
                return {"success": False, "message": f"board_update failed: {e}"}

        elif cmd == "board_remove":
            ok = main.board.remove_task(args.get("id", ""))
            return {"success": ok, "message": "Removed" if ok else "No such task"}

        elif cmd == "link_status":
            # Frame Link（跨機配對）狀態：listener + peers 可達性。
            try:
                st = self._link().status()
                lines = [f"🔗 Frame Link — {'on' if st.get('running') else 'off'}"
                         f" · {st.get('frame_name')}"]
                if st.get("running"):
                    addrs = ", ".join(st.get("addresses") or []) or "?"
                    lines.append(f"   {addrs} :{st.get('listen_port')}")
                for p in st.get("peers") or []:
                    dot = "🟢" if p.get("reachable") else "⚫"
                    lines.append(f" {dot} {p['name']} — {p.get('host') or '(無位址)'}"
                                 f":{p.get('port') or ''}")
                if not st.get("peers"):
                    lines.append(" (尚未配對任何 ShellFrame)")
                return {"success": True, "message": "\n".join(lines),
                        "details": st}
            except Exception as e:
                return {"success": False, "message": f"link status failed: {e}"}

        elif cmd == "link_pair":
            # 產生短效一次性配對碼（TG 遠端也能觸發，人在外面即可配對）。
            try:
                res = self._link().pairing_begin()
                if not res.get("success"):
                    return res
                addrs = ", ".join(res.get("addresses") or []) or "?"
                msg = (f"🔗 配對碼：{res['code']}\n"
                       f"位址：{addrs}  port {res['port']}\n"
                       f"{res['expires_in']} 秒內、限一次，"
                       f"在另一台 ShellFrame 選「加入配對」輸入\n"
                       f"📱 手機 App：點連結直接配對 → {res.get('pair_url', '')}")
                if not res.get("relay_url"):
                    msg += "\n（尚未設定 relay：手機需與電腦同區網，或電腦有 port-forward）"
                return {"success": True, "message": msg, "details": res}
            except Exception as e:
                return {"success": False, "message": f"link pair failed: {e}"}

        elif cmd == "link_join":
            try:
                if args.get("url"):
                    res = self._link().join_url(args.get("url", ""))
                else:
                    res = self._link().join(args.get("host", ""),
                                            int(args.get("port") or 8767),
                                            args.get("code", ""))
                if res.get("success"):
                    self._notify_ui_sessions_changed()
                    return {"success": True,
                            "message": f"✅ 已配對：{res.get('peer_name')}",
                            "details": res}
                return res
            except Exception as e:
                return {"success": False, "message": f"link join failed: {e}"}

        elif cmd == "delay_schedule":
            try:
                res = self.delay_add(
                    args.get("sid", ""), args.get("text", ""),
                    int(args.get("delay_sec") or 0),
                    chat_id=int(args.get("chat_id") or 0),
                    label=args.get("label", ""))
                if not res.get("success"):
                    return res
                mins = int(args.get("delay_sec") or 0) // 60
                secs = int(args.get("delay_sec") or 0) % 60
                when = time.strftime("%H:%M", time.localtime(res["due_ts"]))
                dur = (f"{mins}m" + (f"{secs}s" if secs else "")) if mins else f"{secs}s"
                return {"success": True,
                        "message": f"⏳ 已排程（{res['id']}）：{dur} 後（約 {when}）送出\n"
                                   f"未送出前可 /delay cancel {res['id']} 收回",
                        "details": res}
            except Exception as e:
                return {"success": False, "message": f"delay schedule failed: {e}"}

        elif cmd == "delay_list":
            try:
                items = self.delay_list()
                if not items:
                    return {"success": True, "message": "沒有排程中的 /delay"}
                now = time.time()
                lines = ["⏳ 排程中："]
                for x in items:
                    left = int(x.get("due_ts", 0) - now)
                    when = time.strftime("%H:%M", time.localtime(x.get("due_ts", 0)))
                    left_s = (f"{left // 60}m{left % 60}s" if left >= 60
                              else f"{max(0, left)}s")
                    preview = (x.get("text", "") or "").replace("\n", " ")[:40]
                    lines.append(f" [{x.get('id')}] {x.get('label','')} · {when}"
                                 f"（剩 {left_s}）\n   {preview}")
                return {"success": True, "message": "\n".join(lines), "details": {"items": items}}
            except Exception as e:
                return {"success": False, "message": f"delay list failed: {e}"}

        elif cmd == "delay_cancel":
            try:
                res = self.delay_cancel(args.get("id", ""))
                return {"success": res.get("success", False),
                        "message": ("🗑 已收回排程 " + args.get("id", ""))
                                   if res.get("success") else res.get("message", "取消失敗")}
            except Exception as e:
                return {"success": False, "message": f"delay cancel failed: {e}"}

        elif cmd == "version":
            # An agent should ask what it is driving before assuming a feature
            # exists, rather than hard-coding a version it was told once.
            try:
                v = json.loads(main.VERSION_FILE.read_text()).get("version", "0")
            except Exception:
                v = "0"
            st = (main.load_config().get("settings") or {})
            # Enumerated from the settings themselves, not a hard-coded list: a
            # new experimental flag must show up here the day it is added, or an
            # agent reading this has no way to learn the feature exists.
            keys = sorted(set(k for k in main.DEFAULT_CONFIG.get("settings", {})
                              if k.startswith("experimental_"))
                          | set(k for k in st if k.startswith("experimental_")))
            return {"success": True, "message": f"ShellFrame v{v}",
                    "details": {"version": v,
                                "experimental": {k: bool(st.get(k)) for k in keys}}}

        elif cmd == "skill_doc":
            # `sfctl skill` — the agent-facing reference, served from disk with a
            # live header, so one call answers both "what am I driving" and
            # "how". Anything else would drift from the installed build.
            try:
                doc = (Path(__file__).resolve().parent / "docs" / "ai-skill.md"
                       ).read_text(encoding="utf-8")
            except Exception as e:
                return {"success": False, "message": f"讀不到 docs/ai-skill.md: {e}"}
            ver = self._execute_sfctl("version", {}) or {}
            det = ver.get("details") or {}
            on = [k for k, v in (det.get("experimental") or {}).items() if v]
            header = (f"<!-- 這台機器實際跑的是 ShellFrame v{det.get('version', '?')}；"
                      f"已開啟的實驗性功能：{'、'.join(on) or '（無）'}。"
                      f"底下提到但沒開的功能，就是不能用。 -->\n\n")
            return {"success": True, "message": "ai-skill.md",
                    "details": {"version": det.get("version", ""),
                                "experimental": det.get("experimental") or {},
                                "text": header + doc}}

        elif cmd in ("link_list", "link_peek", "link_send", "link_new",
                     "link_close", "link_rename", "link_conversation",
                     "link_state", "link_maintenance"):
            # Cross-machine session control. Same verbs as the local ones, with a
            # peer in front; the peer may be named or given by frame_id.
            try:
                target = (args.get("peer") or "").strip()
                peers = self._link().peers()
                pid = target if target in peers else ""
                if not pid:
                    for k, v in peers.items():
                        if v.get("name") == target:
                            pid = k
                            break
                if not pid:
                    names = ", ".join(v.get("name", k[:8]) for k, v in peers.items()) or "(none)"
                    return {"success": False,
                            "message": f"找不到 peer「{target}」。已配對：{names}"}
                link, sid = self._link(), args.get("sid", "")
                if cmd == "link_list":
                    return link.remote_info(pid)
                if cmd == "link_peek":
                    return link.remote_peek(pid, sid, int(args.get("lines") or 120))
                if cmd == "link_conversation":
                    return link.remote_conversation(pid, sid, int(args.get("limit") or 80))
                if cmd == "link_send":
                    return link.remote_send(pid, sid, args.get("text", ""),
                                            bool(args.get("submit", True)))
                if cmd == "link_new":
                    return link.remote_new(pid, args.get("cmd") or "claude")
                if cmd == "link_close":
                    return link.remote_close(pid, sid)
                if cmd == "link_rename":
                    return link.remote_rename(pid, sid, args.get("name", ""))
                if cmd == "link_state":
                    return link.remote_state(pid, sid, bool(args.get("all")),
                                             float(args.get("stale_min") or 0))
                if cmd == "link_maintenance":
                    # 更新／重啟另一台。動作是白名單（見 FrameLink），對方那端
                    # 還會再過一次權限閘。原本只有側欄的 ⟳ 維運進得去，所以
                    # 跨機更新沒辦法寫進腳本。
                    return link.remote_maintenance(pid, args.get("action") or "")
            except Exception as e:
                return {"success": False, "message": f"{cmd} failed: {e}"}

        elif cmd == "link_unpair":
            try:
                target = (args.get("peer") or "").strip()
                peers = self._link().peers()
                pid = target if target in peers else ""
                if not pid:
                    for k, v in peers.items():
                        if v.get("name") == target:
                            pid = k
                            break
                if not pid:
                    return {"success": False,
                            "message": f"找不到 peer「{target}」（用 link_status 看名單）"}
                self._link().unpair(pid)
                return {"success": True, "message": f"已斷開 {target}"}
            except Exception as e:
                return {"success": False, "message": f"link unpair failed: {e}"}

        elif cmd == "usage":
            # Per-tab AI usage water-level. Detects claude/codex from the
            # session's launch command and queries the matching local script.
            sid = args.get("sid", "")
            s = self.sessions.get(sid)
            if not s:
                return {"success": False, "message": "此 tab 不存在或已關閉。"}
            try:
                # grok 沒有配額端點，走它自己的說明；probe() 那句「請確認已登入」對它不準確
                if agent_grok.is_grok_cmd(s.cmd):
                    text = agent_grok.usage_report(self._worker_ctx(sid, s))
                else:
                    text = main.usage_probe.probe(s.cmd)
                return {"success": True, "message": text}
            except Exception as e:
                return {"success": False, "message": f"用量查詢失敗：{e}"}

        else:
            return {"success": False, "message": f"Unknown command: {cmd}"}
