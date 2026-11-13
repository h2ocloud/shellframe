"""Can the programs in ShellBot's tabs read ~/Documents, ~/Desktop and ~/Downloads, and if not, why.

macOS (TCC) judges a program running in a tab as the app that started the tmux server, not as
the program itself and not as tmux. Measured with a byte-identical copy of the app starting a
private tmux server, then `/bin/ls ~/Documents` in a new pane of it:

  the app's file where it was                -> allowed (the app's own grant)
  the app renamed (same file, new name)      -> allowed (macOS follows the file)
  the app's file gone (replaced, then moved) -> denied; tccd logs "Failed to fetch responsible
                                                file descriptor: No such file or directory" and
                                                no responsible_path; Apple's own tools (ls, bash,
                                                git...) may not even ask, so they are refused
  a copy put back at the old path            -> allowed again

That is what an update did to a tmux server started before ShellFrame.app became ShellBot.app:
an earlier update had replaced the bundle (its first file was gone), and the rename took the
path away as well. Granting anything does not help then; a tmux server started by the app as it
is now does. probe() runs inside the tmux server, so it sees what the tabs see wherever sfctl
itself runs.
"""
import os
import shutil
import subprocess
import tempfile
import time

FOLDERS = (("Documents", "文件"), ("Desktop", "桌面"), ("Downloads", "下載項目"))
APP_PATHS = ("/Applications/ShellBot.app", os.path.expanduser("~/Applications/ShellBot.app"))


def _tmux(*args, socket=None, timeout=5):
    """tmux on the tabs' server, or on a private one (`socket`, tests)."""
    return subprocess.run(["tmux", *(["-L", socket] if socket else []), *args],
                          capture_output=True, text=True, timeout=timeout)


def server_started(socket=None):
    """When the tmux server holding the tabs started (epoch seconds), or None when none runs."""
    try:
        r = _tmux("display-message", "-p", "#{start_time}", socket=socket)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def app_placed(paths=APP_PATHS):
    """(path, when) of the installed app: the later of the bundle's birth and its last change of
    name or place (ctime), or (None, None)."""
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        return p, max(getattr(st, "st_birthtime", 0) or 0, st.st_ctime)
    return None, None


def probe(home=None, wait_s=8.0, socket=None):
    """{folder: "ok" | "denied" | "missing" | "unknown"} as a program in a tab sees it: a short
    detached tmux session on the tabs' server runs Apple's ls on each folder. {} without a server."""
    home = home or os.path.expanduser("~")
    fd, out = tempfile.mkstemp(prefix="sf-tcc-probe-", suffix=".txt")
    os.close(fd)
    lines = "; ".join(
        f"/bin/ls '{home}/{name}' >/dev/null 2>'{out}.{name}'; echo {name}=$? >> '{out}'" for name, _ in FOLDERS)
    name = f"_sf_tcc_probe_{os.getpid()}"
    try:
        # tmux runs this through sh itself: no second sh -c, or the outer one expands $? early
        r = _tmux("new-session", "-d", "-s", name, f"{lines}; echo end >> '{out}'", socket=socket)
        if r.returncode != 0:
            return {}
        end = time.time() + wait_s
        text = ""
        while time.time() < end:
            with open(out, encoding="utf-8", errors="replace") as f:
                text = f.read()
            if "end" in text.split():
                break
            time.sleep(0.2)
        result = {}
        for folder, _ in FOLDERS:
            code = next((ln.split("=", 1)[1] for ln in text.split() if ln.startswith(folder + "=")), None)
            try:
                err = open(f"{out}.{folder}", encoding="utf-8", errors="replace").read()
            except OSError:
                err = ""
            if "Operation not permitted" in err:
                result[folder] = "denied"
            elif "No such file" in err:
                result[folder] = "missing"
            elif code == "0":
                result[folder] = "ok"
            else:
                result[folder] = "unknown"
        return result
    except (OSError, subprocess.SubprocessError):
        return {}
    finally:
        try:
            _tmux("kill-session", "-t", name, socket=socket)
        except (OSError, subprocess.SubprocessError):
            pass
        for f in [out] + [f"{out}.{n}" for n, _ in FOLDERS]:
            try:
                os.unlink(f)
            except OSError:
                pass


def verdict(result, started, placed):
    """"ok" | "no-server" | "stale-server" (denied, and the server is older than the app where it
    is now: restart the server) | "not-granted" (denied by the app's own permissions)."""
    if not result:
        return "no-server"
    if "denied" not in result.values():
        return "ok"
    if started and placed and started < placed:
        return "stale-server"
    return "not-granted"


def _when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"


def report(result, started, app, placed):
    """Lines for `sfctl permissions --check`."""
    v = verdict(result, started, placed)
    if v == "no-server":
        return v, ["沒有正在跑的分頁（tmux server 沒開），略過資料夾檢查。"]
    out = ["分頁裡的程式能不能讀這些資料夾："]
    for folder, zh in FOLDERS:
        state = result.get(folder, "unknown")
        mark = {"ok": "✅ 可以", "denied": "❌ 被 macOS 擋下", "missing": "（這台沒有這個資料夾）"}.get(state, "？ 沒測出來")
        out.append(f"  {zh}（~/{folder}）：{mark}")
    if v == "ok":
        return v, out
    out += ["", f"tmux server 啟動時間：{_when(started)}", f"{app or 'ShellBot.app'} 放到現在位置的時間：{_when(placed)}", ""]
    if v == "stale-server":
        out += [
            "原因：放著所有分頁的 tmux server 比現在的 ShellBot.app 還舊。macOS 判斷分頁權限時，",
            "會去找「當初啟動 tmux 的那份 App」；它後來被更新換掉、又因為改名（ShellFrame → ShellBot）",
            "換了位置，系統找不到，就一律拒絕。這時在系統設定給權限也沒用，",
            "要讓現在的 ShellBot 重新起一個 tmux server：",
            "  1. 先確認沒有分頁正在工作；輸入框裡還沒送出的字會不見，先送出或複製起來。",
            "  2. 完全結束 ShellBot（在 ShellBot 按 ⌘Q）。",
            f"  3. 打開「終端機」，貼上這行後按 Enter：{shutil.which('tmux') or 'tmux'} kill-server",
            "  4. 重新打開 ShellBot。分頁會一個個自動接回原本的對話",
            "     （Claude Code、Codex、Grok、Cursor 的分頁接回原對話；其他種類的分頁會重新開始）。",
            "  （也可以直接重新開機，再打開 ShellBot。）",
            "  5. 如果跳出「ShellBot 想要取用…檔案夾」的視窗，按「允許」。",
        ]
    else:
        out += [
            "原因：ShellBot 本身沒有這些資料夾的權限。到「系統設定 → 隱私權與安全性 → 檔案與資料夾」",
            "找到 ShellBot，把文件、桌面、下載項目打開；或在「完整磁碟取用權限」加入 ShellBot。",
        ]
    return v, out
