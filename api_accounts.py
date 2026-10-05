"""Api mixin — 帳號 / Provider 域（God-class 分批拆解 第二批）.

Claude/Codex 帳號 profile 切換（搬 transcript + resume 重開）、登入擷取、
provider 安裝與就緒檢查、用量探針。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import concurrent.futures
import json
import os
import subprocess
import threading

from api_host import main


class AccountsApiMixin:
    def _account_config(self):
        cfg = main.load_config()
        if main.ACCOUNT_MANAGER.ensure(cfg):
            main.save_config(cfg)
        return cfg

    @staticmethod
    def _single_account_state(provider: str):
        """Panel entry for a provider that has quota but no switchable profiles.

        Some CLIs sign in as exactly one account and manage that themselves
        (agy → one Google account), so there is nothing to switch between. They
        still belong in the panel: the point is seeing every account's
        water-level. `single_account` tells the UI to drop the switch buttons.
        Derived from the registry, so a future usage-only provider needs no
        change here.
        """
        spec = main.usage_probe.PROVIDER_SPECS.get(provider) or {}
        label = ""
        try:
            label = spec["account"](None, {}) if spec.get("account") else ""
        except Exception:
            label = ""
        if not label:
            return {"current": None, "global": None, "accounts": [],
                    "logged_in": False, "single_account": True}
        item = {"id": f"{provider}-current", "email": label, "label": label,
                "plan": "", "organization": ""}
        return {"current": item, "global": item, "accounts": [item],
                "logged_in": True, "single_account": True}

    def _account_state(self, sid: str = ""):
        cfg = self._account_config()
        session = self.sessions.get(sid)
        if session:
            cfg.setdefault("accounts", main.account_manager._empty_accounts()) \
                .setdefault("sessions", {})[sid] = dict(session.account_refs)
        state = main.ACCOUNT_MANAGER.safe_state(cfg, sid or None)
        for provider in main.usage_probe.PROVIDERS:
            if provider not in main.account_manager.PROVIDERS:
                state["providers"][provider] = self._single_account_state(provider)
            entry = state["providers"].setdefault(provider, {})
            # "not installed" and "installed but not signed in" need different
            # advice, so the panel gets both facts instead of one blank row.
            entry["installed"] = main.usage_probe.provider_installed(provider)
            entry["install"] = (
                main.usage_probe.PROVIDER_SPECS.get(provider, {}).get("install") or {}
            )
        return cfg, state

    def account_state(self, sid: str = "") -> str:
        """Safe account panel data: refs and labels, never credential contents."""
        try:
            return json.dumps(self._account_state(sid)[1], ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def account_switch_global(self, provider: str, ref: str) -> str:
        """Change only the account inherited by future sessions."""
        try:
            cfg = self._account_config()
            main.ACCOUNT_MANAGER.set_global(cfg, provider, ref)
            main.save_config(cfg)
            return json.dumps({"success": True, "scope": "global",
                               "state": self._account_state()[1]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    @staticmethod
    def _claude_config_dir_for(refs: dict) -> str:
        """這組 account refs 對應的 claude 設定家目錄（含 projects/transcripts）。
        沒釘 profile → 預設 ~/.claude。"""
        ref = (refs or {}).get("claude")
        env = main.ACCOUNT_MANAGER.env_for("claude", ref) if ref else {}
        return env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")

    def _pane_pids(self, s) -> list:
        """這個分頁 pane 的行程 pid，加上它的直接子行程（有些指令是殼包 claude）。"""
        tmux_name = getattr(s, "_tmux_name", None)
        if not tmux_name or main.IS_WIN:
            return []
        try:
            r = subprocess.run(["tmux", "display-message", "-p", "-t", tmux_name,
                                "#{pane_pid}"], capture_output=True, text=True, timeout=3)
            pid = r.stdout.strip()
            if not pid.isdigit():
                return []
            kids = subprocess.run(["pgrep", "-P", pid], capture_output=True,
                                  text=True, timeout=3).stdout.split()
            return [pid] + [k for k in kids if k.isdigit()]
        except Exception:
            main._swallow("_pane_pids")
            return []

    def _actual_claude_home(self, s, csid: str) -> str:
        """這個分頁的對話**實際**住在哪個 claude 設定家目錄。

        依序看：跑著的行程的 sessions/<pid>.json（最準，行程吃的就是它）→
        hook 回報的 transcript 路徑 → 硬碟上這個 uuid 最新的那份。
        `account_refs` 不在名單裡：它只記得 ShellFrame 自己 pin 過的帳號，
        從環境繼承來的它不知道（實例：refs 是 null，對話卻在 profile 目錄）。"""
        home = main._claude_dir_for_live_pids(self._pane_pids(s), csid)
        if home:
            return home
        tp = getattr(s, "_hook_transcript_path", "") or ""
        if tp and os.path.isfile(tp) and (not csid or os.path.basename(tp) == f"{csid}.jsonl"):
            return main._claude_home_of_transcript(tp)
        return main._claude_home_of_transcript(main._claude_newest_transcript(csid))

    def _claude_home_to_keep(self, refs: dict, home: str) -> str:
        """帳號不變的重開，要不要照原樣帶 `CLAUDE_CONFIG_DIR=<home>`。

        只在「沒 pin claude 帳號、對話卻住在預設以外的家目錄」時回 home——那就是
        從環境繼承來、ShellFrame 沒記到的那種。有 pin 就照 pin 走（transcript 由
        呼叫端搬進 pin 的目錄）；home 是預設目錄就什麼都不必帶。"""
        if not home or (refs or {}).get("claude"):
            return ""
        if main._same_dir(home, self._claude_config_dir_for(refs)):
            return ""
        return home

    def _carry_claude_transcript(self, old_session, csid: str, new_refs: dict,
                                 src_home: str = "", dst_home: str = ""):
        """切帳號＝換 CLAUDE_CONFIG_DIR，新帳號的 projects 裡沒有這段對話的
        transcript，`--resume` 會找不到、歷史就消失（日常使用中回報）。
        把當前對話的 uuid.jsonl 複製進新帳號的 projects/<同一個 cwd slug>/，
        resume 才接得回同一段歷史。同帳號（config dir 沒變）則不必搬。

        `src_home` 是對話實際所在的家目錄（`_actual_claude_home`）；沒給才用
        舊 refs 推。`dst_home` 是新行程會用的家目錄（有 override 時）；沒給就用
        新 refs 推。來源一律取最新的那份，目標若殘留舊副本會被換掉（留底不刪）。"""
        if not csid:
            return
        old_dir = src_home or self._claude_config_dir_for(
            getattr(old_session, "account_refs", {}))
        new_dir = dst_home or self._claude_config_dir_for(new_refs)
        if main._same_dir(old_dir, new_dir):
            return
        src = getattr(old_session, "_hook_transcript_path", "") or ""
        if not (src and os.path.isfile(src) and os.path.basename(src) == f"{csid}.jsonl"):
            src = (main._claude_newest_transcript(csid, roots=[old_dir])
                   or main._claude_newest_transcript(csid))
        if not src:
            main._dlog("account", f"switch: 找不到 {csid} 的 transcript，歷史無法搬移")
            return
        main._claude_ensure_transcript_in(csid, new_dir, src=src)

    def _restart_session_for_account(self, sid: str, account_refs: dict):
        old = self.sessions.get(sid)
        if not old:
            raise ValueError("此 tab 不存在或已關閉")
        cmd = old.cmd
        # 保留對話：把行程砍掉重開時（換帳號、或「套用 CLI 更新」重啟），用
        # --resume <uuid> 接回原對話。claude 換帳號會換 config dir，先把 uuid 的
        # transcript 搬進新 config dir 再 resume；codex 只在帳號不變（CODEX_HOME
        # 不變、rollout 找得到）時 resume，換帳號則照舊重新開始。
        csid = getattr(old, "session_id", "") or ""
        claude_home = ""
        try:
            _is_claude = main.usage_probe.detect_ai(cmd) == "claude"
        except Exception:
            _is_claude = False
        if _is_claude and not csid:
            # ShellFrame 重開後、還沒收到 hook 的閒置分頁，session_id 是空的。
            # 空著就會跳過下面整段家目錄判斷，直接用舊指令重開（實測：分頁消失）。
            csid = main._claude_session_hint(self._pane_pids(old), cmd)
        same_account = (dict(account_refs or {})
                        == dict(getattr(old, "account_refs", {}) or {}))
        try:
            provider = main.usage_probe.detect_ai(cmd)
        except Exception:
            provider = None
        if provider == "claude" and csid:
            # 先量對話實際住哪。帳號不變的重啟（套用 CLI 更新）要接回行程原本
            # 的家目錄——只看 refs 的話，隱性跑在 profile 裡的分頁會拿預設目錄
            # 去 --resume：找不到就當場結束（分頁消失），預設目錄剛好有舊副本
            # 就悄悄接回舊對話（實例：599 行 vs 2952 行，退回 23 天前）。
            actual = self._actual_claude_home(old, csid)
            if same_account:
                claude_home = self._claude_home_to_keep(account_refs, actual)
                if claude_home:
                    main._dlog("account", f"relaunch {sid}: 對話在 {claude_home}，照原樣帶 "
                                     f"CLAUDE_CONFIG_DIR 重開")
            self._carry_claude_transcript(old, csid, account_refs, src_home=actual,
                                          dst_home=claude_home)
            cmd = self._cmd_with_resume(cmd, csid)
        elif provider == "codex" and same_account:
            try:
                ccsid = self._codex_session_id(sid, old) or ""
            except Exception:
                ccsid = ""
            if ccsid:
                cmd = self._cmd_with_resume(cmd, ccsid)
        cols, rows = old.cols, old.rows
        tmux_name = old._tmux_name
        label = getattr(old, "_custom_label", None)
        bridge_enabled = getattr(old, "_bridge_enabled", True)
        glasses_enabled = getattr(old, "_glasses_enabled", False)
        lifecycle_source = getattr(old, "_lifecycle_source", "")
        lifecycle_handoff = getattr(old, "_lifecycle_handoff", False)
        if self.bridge:
            self.bridge.unregister_session(sid)
        if self.line_bridge:
            self.line_bridge.unregister_session(sid)
        old.kill()
        session = main.Session(sid, cmd, cols, rows, on_data=self._output_event.set,
                          tmux_name=tmux_name, account_refs=account_refs,
                          account_refs_authoritative=True, claude_home=claude_home)
        session._bridge_enabled = bridge_enabled
        session._glasses_enabled = glasses_enabled
        session._init_pending = False
        # 換帳號＝把行程砍掉重開，等同全新啟動，信任對話框會再問一次。
        session._slug_pending = False
        session._lifecycle_source = lifecycle_source
        session._lifecycle_handoff = lifecycle_handoff
        if label:
            session._custom_label = label
        self.sessions[sid] = session
        # 註冊進 self.sessions 之後才掛 watcher——answer_startup_trust 是用
        # sid 回查 session 的，先掛會空轉幾輪。
        self._start_startup_trust_watcher(sid, session)
        if self.bridge:
            self.bridge.register_session(
                sid, label or (cmd.split()[0] if cmd else sid),
                lambda text, _s=session: _s.write(text),
                peek_fn=lambda _s=session: bytes(_s._recent).decode("utf-8", errors="replace"),
                prepare_fn=lambda _s=session: self._prepare_pane_for_input(_s),
                cmd=cmd,
                cols=session.cols, rows=session.rows,
            )
            self.bridge.refresh_commands()
        if self.line_bridge:
            self.line_bridge.register_session(
                sid, label or (cmd.split()[0] if cmd else sid),
                lambda text, _s=session: _s.write(text),
                peek_fn=lambda _s=session: bytes(_s._recent).decode("utf-8", errors="replace"),
            )
        self._persist_session_manifest()
        self._notify_ui_sessions_changed()
        return session

    def account_switch_session(self, sid: str, provider: str, ref: str) -> str:
        """Switch/relaunch one tab; all other running tabs stay untouched."""
        try:
            cfg = self._account_config()
            if provider not in main.account_manager.PROVIDERS:
                raise ValueError("unknown provider")
            main.ACCOUNT_MANAGER.set_session_ref(cfg, sid, provider, ref)
            session = self.sessions.get(sid)
            if not session:
                raise ValueError("此 tab 不存在或已關閉")
            refs = dict(session.account_refs)
            refs[provider] = ref
            cfg.setdefault("accounts", main.account_manager._empty_accounts()) \
                .setdefault("sessions", {})[sid] = refs
            main.save_config(cfg)
            self._restart_session_for_account(sid, refs)
            return json.dumps({"success": True, "scope": "session", "sid": sid,
                               "state": self._account_state(sid)[1]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def relaunch_session(self, sid: str) -> str:
        """重啟這個 tab 的 CLI 行程，套用 CLI（claude/codex）的更新並盡量續接對話。

        為什麼需要：AI CLI 是常駐在 tmux 裡的長命行程，claude/codex 自我更新只換
        了磁碟上的執行檔，**正在跑的行程仍是舊版**；而 `sfctl restart` 只重開 GUI、
        tmux 分頁照留，等於行程沒動 → 使用者以為「更新失敗」。這裡把該分頁的行程
        砍掉重開（帳號不變），claude 用 --resume、codex 用 `codex resume` 接回原
        對話，新版執行檔就生效了。非 AI 分頁（bash 等）則單純重開。"""
        try:
            old = self.sessions.get(sid)
            if not old:
                return json.dumps({"success": False, "message": "此 tab 不存在或已關閉"},
                                  ensure_ascii=False)
            provider = None
            try:
                provider = main.usage_probe.detect_ai(old.cmd)
            except Exception:
                provider = None
            self._restart_session_for_account(sid, dict(getattr(old, "account_refs", {})))
            label = getattr(self.sessions.get(sid), "_custom_label", None) or sid
            msg = (f"已重啟「{label}」的 CLI 並套用更新"
                   + ("（已 --resume 接回對話）" if provider in ("claude", "codex")
                      else ""))
            return json.dumps({"success": True, "sid": sid, "provider": provider or "",
                               "message": msg}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def account_login_start(self, provider: str) -> str:
        """Open an explicit provider login tab; nothing starts automatically."""
        try:
            if provider == "codex":
                cmd = f"{main.CODEX_LAUNCHER} login"
            elif provider == "claude":
                cmd = "claude"
            else:
                raise ValueError("unknown provider")
            # Login must use the provider's canonical auth location. If it
            # inherited the current profile, /login would overwrite that
            # profile instead of creating a new account.
            sid = self.new_session(cmd, 120, 30, source="account-login",
                                   inherit_accounts=False)
            if provider == "claude":
                # The login command is deliberately sent only after the user
                # explicitly pressed the panel's Login button.
                threading.Timer(
                    2.0, lambda: self._send_text_to_session(
                        self.sessions.get(sid), "/login", submit=True
                    ) if self.sessions.get(sid) else None
                ).start()
            return json.dumps({"success": True, "sid": sid, "provider": provider,
                               "message": "登入頁籤已開啟；完成瀏覽器登入後回到面板按重新整理。"},
                              ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def account_capture_login(self, provider: str) -> str:
        """Capture credentials after an explicit login flow into a new profile.

        防呆（日常使用中回報）：切帳號殘留會讓 ~/.claude.json 的帳號資料與
        keychain 的 token 對不上（帳號顯示 team、token 其實是個人），抓下來的
        profile 就用錯 token → 「兩個帳號都顯示同一個人的用量」。抓取前先驗證
        兩邊一致，對不上就擋下、不寫入，並告訴使用者去重新登入選對帳號。"""
        try:
            cfg = self._account_config()
            discovered = main.ACCOUNT_MANAGER.discover(provider)
            if not discovered:
                raise ValueError("尚未偵測到已登入帳號")
            mm = discovered.get("mismatch") if isinstance(discovered, dict) else None
            if mm:
                who = mm.get("email") or mm.get("organization") or "?"
                tiers = "／".join(mm.get("account_tiers") or []) or "?"
                plan = mm.get("token_plan") or mm.get("token_tier") or "?"
                return json.dumps({
                    "success": False,
                    "mismatch": True,
                    "message": (f"擋下：憑證與帳號資料對不上，沒有存下。\n"
                                f"帳號顯示 {who}（{tiers}），但目前的登入憑證是"
                                f"「{plan}」方案——不是同一個帳號。\n"
                                f"這通常是切帳號殘留造成的：請在該分頁重新 /login、"
                                f"確認登入到正確帳號後，再按一次「重新整理已登入」。"),
                }, ensure_ascii=False)
            ref = main.ACCOUNT_MANAGER.sync_current(cfg, provider, discovered)
            if not ref:
                raise ValueError("尚未偵測到已登入帳號")
            main.save_config(cfg)
            captured = main.ACCOUNT_MANAGER.profile(cfg, provider, ref) or {}
            who = captured.get("email") or captured.get("label") or ref
            return json.dumps({"success": True, "ref": ref,
                               "message": f"已存下帳號：{who}",
                               "state": self._account_state()[1]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def ai_providers(self) -> str:
        """The AI-CLI registry, for the web UI.

        The front end derives "is this an AI tab / which provider" from this
        instead of keeping its own literals, so supporting another CLI needs no
        change in index.html. `extra` are CLIs recognised without quota support.
        """
        try:
            return json.dumps({
                "providers": {
                    name: {"label": spec["label"],
                           "binaries": list(spec["binaries"]),
                           "installed": main.usage_probe.provider_installed(name),
                           "install": spec.get("install") or {}}
                    for name, spec in main.usage_probe.PROVIDER_SPECS.items()
                },
                "extra": sorted(main.OTHER_AI_CLI_TOOLS),
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"providers": {}, "extra": [], "error": str(e)})

    def provider_ready(self, cmd: str) -> str:
        """Is the AI CLI this command launches actually installed?

        Called before opening an AI tab. Without this check a missing binary
        makes the tab die instantly with "command not found", which reads as
        ShellFrame being broken rather than as a CLI that needs installing —
        exactly what a stock preset for a not-yet-installed CLI would do.
        Errors resolve to ready=True: a broken check must not block a tab.
        """
        try:
            provider = main.usage_probe.detect_ai(cmd or "")
            if not provider:
                return json.dumps({"ready": True})
            spec = main.usage_probe.PROVIDER_SPECS.get(provider) or {}
            return json.dumps({
                "ready": main.usage_probe.provider_installed(provider),
                "provider": provider,
                "label": spec.get("label", provider),
                "install": spec.get("install") or {},
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ready": True, "error": str(e)}, ensure_ascii=False)

    def install_provider(self, provider: str) -> str:
        """Open a shell tab and run that CLI's documented install command.

        Visible on purpose: the command runs in a real tab the user can read,
        interrupt and re-run, instead of a silent background install.
        """
        try:
            spec = main.usage_probe.PROVIDER_SPECS.get(provider)
            if not spec:
                raise ValueError("unknown provider")
            command = ((spec.get("install") or {}).get("command") or "").strip()
            if not command:
                raise ValueError(f"{spec.get('label', provider)} 沒有內建安裝指令，請參考官方文件")
            shell = "powershell" if main.IS_WIN else "bash"
            sid = self.new_session(shell, 120, 30, source="provider-install",
                                   inherit_accounts=False)
            session = self.sessions.get(sid)
            if session:
                threading.Timer(
                    1.5, lambda: self._send_text_to_session(session, command, submit=True)
                ).start()
            return json.dumps({
                "success": True, "sid": sid, "command": command,
                "message": f"已在新分頁執行 {spec.get('label', provider)} 安裝指令，"
                           f"裝完再開分頁即可。",
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def account_usage_all(self, refresh: bool = False) -> str:
        """Every logged-in account's water-level, for the AI accounts panel.

        One reading per account so the panel can show all of them at once
        instead of only the active tab's. Accounts are queried in parallel —
        they use different tokens / CODEX_HOMEs, so there is no shared
        rate-limit budget between them — while usage_probe keeps a per-account
        cache so re-opening the panel does not re-hit the APIs.
        """
        try:
            # Normalised explicitly: a JS-side "false" arriving as a string
            # would make bool() force a refresh on every open — the fastest way
            # to get every account rate-limited.
            force = refresh is True or str(refresh).strip().lower() in ("true", "1")
            cfg = self._account_config()
            accounts = cfg.get("accounts") or {}
            jobs = []
            for provider in main.usage_probe.PROVIDERS:
                if provider not in main.account_manager.PROVIDERS:
                    # Quota but no switchable profiles: one implicit account,
                    # read with no credential override (see _single_account_state).
                    entry = self._single_account_state(provider)
                    if entry["logged_in"]:
                        jobs.append((provider, entry["current"]["id"],
                                     entry["current"], True))
                    continue
                # The account the provider is really signed in as right now:
                # it may read from the canonical location instead of a snapshot.
                current = (main.ACCOUNT_MANAGER.discover(provider) or {}).get("id")
                for item in (accounts.get("profiles") or {}).get(provider, []) or []:
                    ref = item.get("id")
                    if ref:
                        jobs.append((provider, ref, item, ref == current))

            def _one(job):
                provider, ref, item, is_current = job
                profiled = provider in main.account_manager.PROVIDERS
                data = main.usage_probe.account_usage(
                    provider,
                    env=main.ACCOUNT_MANAGER.env_for(provider, ref) if profiled else {},
                    ref=ref,
                    account=main.usage_probe.profile_account(item) if profiled
                    else (item.get("email") or ""),
                    force=force,
                    is_current=is_current,
                )
                data["is_current_login"] = is_current
                return provider, ref, data

            out = {provider: {} for provider in main.usage_probe.PROVIDERS}
            if jobs:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(4, len(jobs))
                ) as pool:
                    for provider, ref, data in pool.map(_one, jobs):
                        out[provider][ref] = data
            return json.dumps({"success": True, "providers": out}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)

    def _probe_session_data(self, session: main.Session):
        provider = main.usage_probe.detect_ai(session.cmd)
        ref = session.account_refs.get(provider) if provider else None
        # A reattached tab from before per-account profiles has no ref (see
        # Session._account_refs_authoritative); env_for needs a real ref, so
        # fall back to the provider's global credentials instead of erroring.
        env = main.ACCOUNT_MANAGER.env_for(provider, ref) if ref else {}
        result = main.usage_probe.probe_data(session.cmd, env=env)
        profile = main.ACCOUNT_MANAGER.profile(
            self._account_config(), provider, ref
        ) if ref else None
        if profile:
            result["account"] = main.usage_probe.profile_account(profile)
        # Existing Codex tmux processes predate per-account CODEX_HOME and
        # therefore left their rollout JSONL in ~/.codex. Reuse that snapshot
        # only when this tab is the currently discovered canonical account;
        # a genuinely different profile must not display another account's
        # quota.
        if provider == "codex" and result.get("error") == "no_data" and ref:
            discovered = main.ACCOUNT_MANAGER.discover(provider) or {}
            if discovered.get("id") == ref:
                result = main.usage_probe.probe_data(session.cmd)
        return result

    def tab_usage(self, sid: str) -> str:
        """Web /usage slash command: return this tab's AI usage water-level.

        Detects claude/codex from the session's launch command and queries the
        matching local usage script. Result is shown in the web UI, never sent
        into the agent's conversation. Can take a few seconds (network/JSONRPC).
        """
        s = self.sessions.get(sid)
        if not s:
            return "此 tab 不存在或已關閉。"
        try:
            data = self._probe_session_data(s)
            return main.usage_probe.probe_text(data)
        except Exception as e:
            return f"用量查詢失敗：{e}"

    def tab_usage_brief(self, sid: str) -> str:
        """Structured usage for the inline top-bar pill (polled ~every 5 min).

        Follows the active tab: if it runs claude/codex, probe that provider;
        otherwise fall back to claude (account-global, no tab needed) so the
        indicator still shows something on non-AI tabs. Returns JSON.
        """
        s = self.sessions.get(sid)
        cmd = (s.cmd if s else "") or ""
        if main.usage_probe.detect_ai(cmd) is None:
            cmd = "claude"
        try:
            provider = main.usage_probe.detect_ai(cmd)
            result = self._probe_session_data(s) if s else main.usage_probe.probe_data(cmd)
            if s and provider:
                profile = main.ACCOUNT_MANAGER.profile(
                    self._account_config(), provider, s.account_refs.get(provider)
                )
                if profile:
                    result["account"] = " · ".join(
                        x for x in (profile.get("email"), profile.get("label")) if x
                    )
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"ai": None, "error": str(e)})
