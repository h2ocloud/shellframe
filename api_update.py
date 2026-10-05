"""Api mixin — 版本與更新域（God-class 分批拆解 第二批）.

版本/CHANGELOG、檢查更新、git pull + 依賴安裝的自我更新、App 自我重啟。

行為與搬家前相同（僅搬家）；main.py 的全域經 api_host 的 late-bound `main` 取得。
"""

import json
import os
import platform
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from api_host import main


class UpdateApiMixin:
    def get_version(self) -> str:
        """Return current local version info."""
        try:
            return main.VERSION_FILE.read_text(encoding='utf-8')
        except:
            return json.dumps({"version": "unknown", "channel": "main"})

    def get_changelog(self) -> str:
        """Return changelog content."""
        changelog = main.APP_DIR / "CHANGELOG.md"
        try:
            return changelog.read_text(encoding='utf-8')
        except:
            return ""

    def get_latest_release_notes(self) -> str:
        """Return the current version + the top (latest) CHANGELOG section as
        markdown, for the startup 'what's new' popup. Body is just the first
        '## ...' block so we don't ship the whole 240KB changelog to JS."""
        try:
            version = json.loads(main.VERSION_FILE.read_text(encoding='utf-8')).get("version", "")
        except Exception:
            version = ""
        body, heading = "", ""
        try:
            text = (main.APP_DIR / "CHANGELOG.md").read_text(encoding='utf-8')
            lines = text.splitlines()
            start = None
            for i, ln in enumerate(lines):
                if ln.startswith("## "):
                    start = i
                    break
            if start is not None:
                heading = lines[start][3:].strip()
                section = []
                for ln in lines[start + 1:]:
                    if ln.startswith("## "):
                        break
                    section.append(ln)
                body = "\n".join(section).strip()
        except Exception:
            main._swallow("Api.get_latest_release_notes:4601")
        return json.dumps({"version": version, "heading": heading, "body": body})

    def check_update(self) -> str:
        """Check GitHub for latest version. Returns JSON with local, remote, update_available."""
        try:
            local = json.loads(main.VERSION_FILE.read_text(encoding='utf-8')) if main.VERSION_FILE.exists() else {"version": "0.0.0"}
        except:
            local = {"version": "0.0.0"}

        # Remote version STRING — for display only (banner「vX 可更新」). Cache-
        # bust: raw.githubusercontent is Fastly-cached (~5 min) and serves a
        # STALE version.json right after a push.
        remote_ver = None
        try:
            bust = int(time.time())
            req = urllib.request.Request(
                f"{main.REPO_URL}?t={bust}",
                headers={"User-Agent": "shellframe",
                         "Cache-Control": "no-cache", "Pragma": "no-cache"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote_ver = json.loads(resp.read().decode()).get("version")
        except Exception:
            pass

        # AUTHORITATIVE update signal: git commit SHA, NOT the version.json
        # semver (使用者 2026-08-03:「版號不能衝突，會讓其他機器檢測不到
        # update」）。並行 session 撞版號時，舊機器看到 remote_v == local_v →
        # 誤判沒更新、永遠不更新。改比對 remote main 的 commit SHA vs 本機
        # HEAD：版號變純顯示，撞號再也不影響偵測。git 不可用時退回 semver。
        def _git(*args, timeout=8):
            try:
                r = subprocess.run(["git", "-C", str(main.APP_DIR), *args],
                                   capture_output=True, text=True, timeout=timeout)
                return r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                return ""
        local_sha = _git("rev-parse", "HEAD")
        ls = _git("ls-remote", "origin", "-h", "refs/heads/main")
        remote_sha = ls.split()[0] if ls else ""
        if local_sha and remote_sha:
            has_update = remote_sha != local_sha
            if has_update:
                # 避免「本機領先遠端」誤報：remote_sha 是本機 HEAD 的祖先
                # ＝本機在前面（remote 物件在本機才判得出；不在＝遠端有新東西
                # → 維持 has_update）。免 fetch。
                anc = subprocess.run(
                    ["git", "-C", str(main.APP_DIR), "merge-base",
                     "--is-ancestor", remote_sha, "HEAD"],
                    capture_output=True, timeout=8)
                if anc.returncode == 0:
                    has_update = False
            return json.dumps({
                "local": local["version"],
                "remote": remote_ver or local["version"],
                "update_available": has_update,
                "remote_sha": remote_sha[:7],
            })

        # FALLBACK：git 不可用 → 回到 version.json semver 比對（舊行為）。
        if remote_ver is None:
            return json.dumps({"local": local["version"], "remote": None,
                               "update_available": False,
                               "error": "Could not reach GitHub"})
        def _vtuple(s):
            out = []
            for x in str(s).split("."):
                m = re.match(r"\d+", x)
                out.append(int(m.group()) if m else 0)
            return tuple(out)
        has_update = _vtuple(remote_ver) > _vtuple(local.get("version", "0"))
        return json.dumps({
            "local": local["version"],
            "remote": remote_ver,
            "update_available": has_update,
        })

    def do_update(self) -> str:
        """Full upgrade with defensive fallbacks so a half-bad state doesn't brick the install.

        Steps (each with its own recovery):
          1. Auto-stash dirty working tree (so local edits never block pull).
          2. `git pull --ff-only` → on failure, `git fetch && git reset --hard origin/main`
             (force-sync to remote; the stash in step 1 preserves user work).
          3. `python -m pip install -r requirements.txt` → on failure, recreate
             `.venv` from scratch and retry once.
          4. Refresh `.app` bundle (macOS). Never touches the source .app in
             APP_DIR, so if copy fails the user can still launch via CLI.

        Recovery hint (always returned on total failure):
          curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash
        """
        post_steps = []
        RECOVERY_CMD = ("curl -fsSL "
                        "https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh "
                        "| bash")
        try:
            # Pre-check: APP_DIR must be a git repo for `git pull` to work.
            # Users who installed via zip/download have no .git — auto-fallback
            # to install.sh (which converts a non-git dir into a git clone).
            if not (main.APP_DIR / ".git").exists():
                post_steps.append(".git missing — running install.sh to re-initialize")
                ok, msg = main._run_install_sh()
                if ok:
                    try:
                        new_ver = json.loads(main.VERSION_FILE.read_text(encoding='utf-8'))["version"]
                    except Exception:
                        new_ver = "unknown"
                    post_steps.append(f"install.sh: {msg}")
                    return json.dumps({
                        "success": True,
                        "message": "Reinitialized via install.sh",
                        "version": new_ver,
                        "can_hot_reload": False,
                        "needs_restart": True,
                        "changed_files": [],
                        "post_steps": post_steps,
                    })
                else:
                    return json.dumps({
                        "success": False,
                        "message": f"install.sh failed: {msg}",
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })

            old_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(main.APP_DIR),
                capture_output=True, text=True, timeout=10
            ).stdout.strip()

            # ── Step 1: auto-stash dirty tree ────────────────────────
            try:
                status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(main.APP_DIR),
                    capture_output=True, text=True, timeout=10
                )
                if status.stdout.strip():
                    stash_tag = f"shellframe-auto-{int(time.time())}"
                    stash = subprocess.run(
                        ["git", "stash", "push", "-u", "-m", stash_tag],
                        cwd=str(main.APP_DIR),
                        capture_output=True, text=True, timeout=15
                    )
                    if stash.returncode == 0:
                        post_steps.append(f"stashed local changes ({stash_tag})")
                    else:
                        post_steps.append(f"stash skipped: {stash.stderr.strip()[:80]}")
            except Exception as e:
                post_steps.append(f"stash check failed: {e}")

            # ── Step 2: pull with fallback to force-sync ─────────────
            pull_out = ""
            pull = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(main.APP_DIR),
                capture_output=True, text=True, timeout=45
            )
            if pull.returncode == 0:
                pull_out = pull.stdout.strip()
            else:
                post_steps.append(f"ff-only pull failed: {pull.stderr.strip()[:100]} — falling back to force-sync")
                fetch = subprocess.run(
                    ["git", "fetch", "origin", "main"],
                    cwd=str(main.APP_DIR),
                    capture_output=True, text=True, timeout=45
                )
                if fetch.returncode != 0:
                    return json.dumps({
                        "success": False,
                        "message": f"git fetch failed: {fetch.stderr.strip()[-200:]}",
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })
                reset = subprocess.run(
                    ["git", "reset", "--hard", "origin/main"],
                    cwd=str(main.APP_DIR),
                    capture_output=True, text=True, timeout=15
                )
                if reset.returncode != 0:
                    return json.dumps({
                        "success": False,
                        "message": f"git reset failed: {reset.stderr.strip()[-200:]}",
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })
                post_steps.append("force-synced to origin/main")
                pull_out = reset.stdout.strip()

            try:
                new_ver = json.loads(main.VERSION_FILE.read_text(encoding='utf-8'))["version"]
            except Exception:
                new_ver = "unknown"

            # Determine what changed
            changed_files = []
            needs_restart = False
            if old_head:
                diff = subprocess.run(
                    ["git", "diff", "--name-only", old_head, "HEAD"],
                    cwd=str(main.APP_DIR),
                    capture_output=True, text=True, timeout=10
                )
                changed_files = [f for f in diff.stdout.strip().split('\n') if f]
                needs_restart = any(
                    f.endswith('.py') or f == 'requirements.txt' or f == 'filters.json'
                    for f in changed_files
                )

            # ── Step 3: pip install with venv-recreate fallback ─────
            req_changed = 'requirements.txt' in changed_files
            venv_dir = main.APP_DIR / ".venv"
            req_file = str(main.APP_DIR / "requirements.txt")
            if req_changed or not main._venv_has_pip(venv_dir):
                pip_ok, pip_msg = main._pip_install_robust(venv_dir, req_file)
                post_steps.append(f"pip install: {pip_msg}")
                if not pip_ok:
                    post_steps.append("venv may be broken — try recovery command")
                    return json.dumps({
                        "success": False,
                        "message": f"pip install failed: {pip_msg}",
                        "version": new_ver,
                        "post_steps": post_steps,
                        "recovery": RECOVERY_CMD,
                    })

            # ── Step 4: refresh .app bundle (macOS only) ────────────
            if not main.IS_WIN:
                src_app = main.APP_DIR / "ShellFrame.app"
                if src_app.exists():
                    for dest_dir in [Path("/Applications"), Path.home() / "Applications"]:
                        dest = dest_dir / "ShellFrame.app"
                        try:
                            if dest.exists() or dest_dir.exists():
                                subprocess.run(
                                    ["rm", "-rf", str(dest)],
                                    capture_output=True, timeout=10
                                )
                                subprocess.run(
                                    ["cp", "-R", str(src_app), str(dest)],
                                    capture_output=True, timeout=10
                                )
                                ok, launcher_msg = main._refresh_macos_app_launcher(dest)
                                post_steps.append(f".app launcher: {launcher_msg}")
                                post_steps.append(f".app copied to {dest}")
                                if dest_dir == Path("/Applications"):
                                    user_app = Path.home() / "Applications" / "ShellFrame.app"
                                    subprocess.run(
                                        ["rm", "-rf", str(user_app)],
                                        capture_output=True, timeout=10
                                    )
                                    post_steps.append(f"removed stale app copy: {user_app}")
                                break
                        except Exception as e:
                            post_steps.append(f".app copy to {dest} failed: {e}")
                            # Non-fatal — src .app in APP_DIR is still usable

            has_sessions = len(self.sessions) > 0
            return json.dumps({
                "success": True,
                "message": pull_out,
                "version": new_ver,
                "can_hot_reload": has_sessions and not needs_restart,
                "needs_restart": needs_restart,
                "changed_files": changed_files,
                "post_steps": post_steps,
                "platform": platform.system(),
            })
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": str(e),
                "post_steps": post_steps,
                "recovery": RECOVERY_CMD,
            })

    def restart_app(self, confirm: bool = False) -> str:
        """Restart the app — spawns a new instance and exits the current one.
        tmux-backed sessions persist; the new instance reattaches on startup.

        Strategies (tried in order):
          macOS:   `open -n -a ShellFrame.app` → launcher script → python relaunch
          Windows: shellframe.bat in install dir → pythonw.exe main.py
          Linux:   launcher script → python relaunch
        """
        try:
            spawned = False
            err_msgs = []
            in_place_restart = False

            def _find_macos_app_path():
                candidates = [
                    Path("/Applications/ShellFrame.app"),
                    Path.home() / "Applications" / "ShellFrame.app",
                    main.APP_DIR / "ShellFrame.app",
                ]
                for c in candidates:
                    try:
                        if c.exists():
                            return c.resolve()
                    except Exception:
                        main._swallow("restart_app._find_macos_app_path:4854")
                return None

            def _schedule_macos_app_relaunch(app_path: Path):
                """Launch via the .app bundle after this PID exits.

                Opening the bundle before the old process has quit can be a
                no-op on some LaunchServices states. Replacing the process via
                execv is reliable but loses the .app identity and shows up as
                Python in Dock, so keep the handoff outside this process and
                retry `open -n` after the old PID is gone.
                """
                pid = os.getpid()
                app = shlex.quote(str(app_path))
                log = shlex.quote(main.DEBUG_LOG)
                script = (
                    f"pid={pid}; app={app}; log={log}; "
                    "i=0; "
                    "while kill -0 \"$pid\" >/dev/null 2>&1 && [ $i -lt 80 ]; do "
                    "  i=$((i+1)); sleep 0.1; "
                    "done; "
                    "for j in 1 2 3; do "
                    "  if /usr/bin/open -n \"$app\" >/dev/null 2>&1; then "
                    "    echo \"$(date +%H:%M:%S.%3N) [restart] relaunched app=$app\" >> \"$log\"; "
                    "    exit 0; "
                    "  fi; "
                    "  sleep 1; "
                    "done; "
                    "echo \"$(date +%H:%M:%S.%3N) [restart] failed to relaunch app=$app\" >> \"$log\""
                )
                subprocess.Popen(
                    ["/bin/sh", "-c", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )

            if main.IS_WIN:
                # Windows has no tmux, so a restart recreates each tab from the
                # persisted manifest (same command, same label, same position)
                # rather than reattaching — scrollback is lost, the tabs are not.
                # This used to refuse outright whenever any session existed,
                # which on Windows is always: an update could be downloaded but
                # never applied, and the dialog's only button said "blocked".
                # The caller now asks first (confirm=True) instead.
                if self.sessions and not confirm:
                    n = len(self.sessions)
                    return json.dumps({
                        "success": False,
                        "needs_confirm": True,
                        "session_count": n,
                        "message": (
                            f"Windows has no tmux, so restarting recreates your {n} "
                            "tab(s) from their saved commands and labels — scrollback "
                            "and any in-progress AI conversation are lost. Anything "
                            "running in a tab is stopped."
                        ),
                        "preserves_sessions": False,
                    })
                # Strategy W1: shellframe.bat from install dir / user's local bin
                bat_candidates = [
                    main.APP_DIR / "ShellFrame.bat",
                    Path.home() / ".local" / "bin" / "shellframe.bat",
                ]
                bat_path = None
                for c in bat_candidates:
                    try:
                        if c.exists():
                            bat_path = c
                            break
                    except Exception:
                        main._swallow("Api.restart_app:4914")
                if bat_path:
                    try:
                        DETACHED_PROCESS = 0x00000008
                        CREATE_NEW_PROCESS_GROUP = 0x00000200
                        subprocess.Popen(
                            ["cmd", "/c", "start", "", str(bat_path)],
                            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            close_fds=True,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"shellframe.bat failed: {e}")

                # Strategy W2: pythonw.exe main.py (windowless Python)
                if not spawned:
                    try:
                        # Try pythonw.exe (no console) first, fall back to python.exe
                        py_exe = sys.executable
                        if py_exe.endswith("python.exe"):
                            pyw = py_exe[:-10] + "pythonw.exe"
                            if Path(pyw).exists():
                                py_exe = pyw
                        DETACHED_PROCESS = 0x00000008
                        CREATE_NEW_PROCESS_GROUP = 0x00000200
                        subprocess.Popen(
                            [py_exe, str(main.APP_DIR / "main.py")],
                            cwd=str(main.APP_DIR),
                            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            close_fds=True,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"pythonw relaunch failed: {e}")
            else:
                if platform.system() == "Darwin":
                    app_path = _find_macos_app_path()
                    if app_path:
                        try:
                            _schedule_macos_app_relaunch(app_path)
                            spawned = True
                        except Exception as e:
                            err_msgs.append(f".app relaunch schedule failed: {e}")
                    else:
                        err_msgs.append("ShellFrame.app not found")
                else:
                    # Linux/no-.app fallback: replace the current Python
                    # process in-place after the RPC response has been written.
                    try:
                        def _exec_soon():
                            time.sleep(0.8)
                            try:
                                self.cleanup_all()
                            except Exception as e:
                                main._dlog("restart", f"cleanup before exec failed: {e}")
                            try:
                                os.chdir(str(main.APP_DIR))
                            except Exception:
                                main._swallow("restart_app._exec_soon:4976")
                            main._dlog("restart", f"exec in-place python={sys.executable!r}")
                            os.execv(sys.executable, [sys.executable, str(main.APP_DIR / "main.py")])

                        threading.Thread(target=_exec_soon, daemon=True).start()
                        spawned = True
                        in_place_restart = True
                    except Exception as e:
                        err_msgs.append(f"in-place exec schedule failed: {e}")

                # Strategy 1: `open -n <absolute .app path>` — no `-a`, so
                # LaunchServices doesn't route by bundle ID. Passing the
                # path directly gives the spawned process full .app bundle
                # context (Info.plist / CFBundleName / icon), so Dock +
                # Cmd-Tab show "ShellFrame" with the right icon. The old
                # "exec launcher directly" strategy worked around a stale
                # bundle-id registration but lost the bundle wrapping, so
                # the new process showed up as a generic "Python" icon —
                # the user saw two Dock entries during restart and couldn't
                # tell which was shellframe. With `open -n <path>` the new
                # instance inherits the clicked app's identity properly.
                app_path = _find_macos_app_path()
                if not spawned and app_path:
                    try:
                        _schedule_macos_app_relaunch(app_path)
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"open -n <path> failed: {e}")

                # Strategy 2: `open -n -a` (resolves by bundle id via
                # LaunchServices). Fallback if the direct-path form above
                # isn't supported on this macOS build.
                if not spawned and app_path and platform.system() == "Darwin":
                    try:
                        subprocess.Popen(
                            ["/usr/bin/open", "-n", "-a", str(app_path)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"open -n -a failed: {e}")

                # Strategy 3: relaunch via current Python
                if not spawned and platform.system() != "Darwin":
                    try:
                        subprocess.Popen(
                            [sys.executable, str(main.APP_DIR / "main.py")],
                            cwd=str(main.APP_DIR),
                            start_new_session=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        spawned = True
                    except Exception as e:
                        err_msgs.append(f"python relaunch failed: {e}")

            if not spawned:
                return json.dumps({"success": False, "message": "; ".join(err_msgs) or "no spawn method worked"})

            if not in_place_restart:
                # Schedule exit so the response can return cleanly first
                def _exit_soon():
                    time.sleep(0.8)
                    try:
                        self.cleanup_all()  # detaches from tmux without killing
                    except Exception:
                        main._swallow("restart_app._exit_soon:5043")
                    os._exit(0)
                threading.Thread(target=_exit_soon, daemon=True).start()
            return json.dumps({"success": True})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "message": str(e)})
