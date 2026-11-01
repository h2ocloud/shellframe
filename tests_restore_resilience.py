#!/usr/bin/env python3
"""重開 app 時一個分頁接不回來，不能拖垮其餘分頁；接不回的要再試、要回報（不殺）。

實例（2026-10-29）：記憶體吃緊、tmux server 被換出，重開 ShellFrame 時 restore
迴圈在第 10 個 session 遇到 tmux 呼叫逾時 → 例外穿出迴圈、前端 catch 吞掉，
後面 15 個 claude／pi 還在跑卻沒有分頁（孤兒）。sid 計數器停在 137，下一個新分頁
還可能撞名 attach 到別人的 sf_s140。

這支驗：
  1. 中間一個 Session() 拋例外，前後的分頁照樣接回，失敗的交給重試
  2. 計數器推到所有 sf_sN 之上（新分頁不會撞名）
  3. 重試迴圈把晚到的分頁接回；還是接不回的只記 log、不殺
  4. tmux 單次逾時：清單不會整份變空、metadata 呼叫不會讓分頁失敗
  5. 孤兒報告與 sid 計算
  6. 螢幕醒來：叫 tmux 重畫目前分頁、叫頁面重畫、記下頁面回應時間

跑法：.venv/bin/python tests_restore_resilience.py
"""
import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))

import tempfile  # noqa: E402
import sf_log  # noqa: E402
# 這支會造出「attach sf_s137 failed」之類的假事件；別寫進正在跑的 app 的 debug log。
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp()) / "shellframe_debug.log")

import main  # noqa: E402
import tmux_sessions  # noqa: E402
import api_tmux  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class _FakeSession:
    def __init__(self, sid, cmd, cols, rows, on_data=None, tmux_name=None, account_refs=None, **_):
        if tmux_name in _FakeSession.broken:
            raise subprocess.TimeoutExpired(["tmux", "resize-window"], 3)
        self.sid, self.cmd, self._tmux_name = sid, cmd, tmux_name
        self.child_pid = 4242


_FakeSession.broken = set()


def _api():
    api = object.__new__(main.Api)
    api.sessions = {}
    api._counter = 0
    api._output_event = threading.Event()
    api._start_startup_trust_watcher = lambda sid, s: None
    api._restore_transcript_hint = lambda s, e: None
    api._persist_session_manifest = lambda order=None: None
    api._sync_bridge_sessions = lambda: None
    return api


def _patch_main(names):
    saved = {k: getattr(main, k) for k in ("load_config", "save_config", "ACCOUNT_MANAGER",
                                          "_has_tmux", "_list_tmux_sessions", "_tmux_get_env",
                                          "Session", "IS_WIN")}
    am = MagicMock()
    am.ensure.return_value = False
    am.session_refs.return_value = {}
    main.load_config = lambda: {"session_order": [], "session_manifest": []}
    main.save_config = lambda c: None
    main.ACCOUNT_MANAGER = am
    main._has_tmux = lambda: True
    main._list_tmux_sessions = lambda: [{"name": n, "cmd": "claude", "sid": n[3:]} for n in names]
    main._tmux_get_env = lambda name, key: ""
    main.Session = _FakeSession
    main.IS_WIN = False
    return saved


def _restore(saved):
    for k, v in saved.items():
        setattr(main, k, v)


# ── 1+2. 一個壞掉不拖垮其他，計數器推到最高 ───────────────────────────────
names = ["sf_s111", "sf_s130", "sf_s137", "sf_s140", "sf_s171", "sf_s172", "sf_s86"]
saved = _patch_main(names)
try:
    _FakeSession.broken = {"sf_s137"}
    api = _api()
    retried = []
    api._retry_failed_attach = lambda failed: retried.append(list(failed))
    out = json.loads(api.restore_tmux_sessions(80, 24))
    got = sorted(api.sessions)
    check("s137 失敗，其餘 6 個照樣接回（含 claude、pi 分頁）",
          got == ["s111", "s130", "s140", "s171", "s172", "s86"], str(got))
    check("回傳給前端的清單也有它們", len(out) == 6, str(out))
    check("失敗的交給重試", retried == [["sf_s137"]], str(retried))
    check("計數器 ≥ 所有 sf_sN 的最大值（新分頁不會撞 sf_s172）", api._counter >= 172, str(api._counter))
    check("下一個新分頁的 sid 不撞任何既有 session", api._next_sid() == "s173")

    # ── 3. 重試迴圈：晚到的接回，接不回的只回報 ───────────────────────────
    api = _api()
    api._ATTACH_RETRY_DELAYS = (0.0, 0.0)
    _FakeSession.broken = {"sf_s137", "sf_s171"}
    api._retry_failed_attach = api_tmux.TmuxApiMixin._retry_failed_attach.__get__(api)
    logs = []
    orig_dlog = api_tmux._dlog
    api_tmux._dlog = lambda cat, msg: logs.append((cat, msg))
    orig_details, orig_kill = tmux_sessions.session_details, None
    tmux_sessions.session_details = lambda tmux, prefix="sf_": [
        {"name": n, "sid": n[3:], "attached": 0, "created": 0, "activity": 0,
         "pane_pid": 1, "pane_cmd": "claude"} for n in names]
    killed = []
    orig_run = subprocess.run

    def _no_kill(args, *a, **k):
        if any(x in ("kill-session", "kill-server", "kill") for x in args):
            killed.append(args)
        return orig_run(["true"], capture_output=True)
    subprocess.run = _no_kill
    api._attach_retrying = True                     # 下面自己跑重試迴圈，不要背景那條
    try:
        api.restore_tmux_sessions(80, 24)          # s137、s171 失敗
        check("第一輪：兩個失敗、其餘接回", "s137" not in api.sessions and "s171" not in api.sessions
              and "s172" in api.sessions)
        _FakeSession.broken = {"sf_s171"}           # tmux 回神：s137 好了，s171 還是不行
        api._attach_retry_loop(["sf_s137", "sf_s171"])
        check("重試把 s137 接回", "s137" in api.sessions, str(sorted(api.sessions)))
        check("接不回的 s171 記成孤兒", any("sf_s171" in m and "orphan" in m for _, m in logs),
              str(logs[-2:]))
        check("全程沒有 kill 任何東西", not killed, str(killed))
        check("重試結束會放掉旗標", getattr(api, "_attach_retrying", True) is False)
    finally:
        subprocess.run = orig_run
        api_tmux._dlog = orig_dlog
        tmux_sessions.session_details = orig_details
finally:
    _FakeSession.broken = set()
    _restore(saved)


# ── 4. tmux 單次逾時 ──────────────────────────────────────────────────────
orig_run = subprocess.run


def _slow_env(args, *a, **k):
    if args[1] == "list-sessions":
        return subprocess.CompletedProcess(args, 0, "sf_s1\nsf_s2\nother\n", "")
    if args[1] == "show-environment" and args[3] == "sf_s1":
        raise subprocess.TimeoutExpired(args, 3)
    return subprocess.CompletedProcess(args, 0, f"{args[4]}=v-{args[3]}\n", "")


subprocess.run = _slow_env
try:
    rows = tmux_sessions.list_sf_sessions("/usr/bin/tmux")
finally:
    subprocess.run = orig_run
check("一個 session 的 env 讀不到，清單不會整份變空（舊版回 []，掉進重開機路徑）",
      [r["name"] for r in rows] == ["sf_s1", "sf_s2"], str(rows))
check("讀不到的欄位留空，讀得到的照填", rows[0]["sid"] == "" and rows[1]["sid"] == "v-sf_s2", str(rows))


def _timeout(*a, **k):
    raise subprocess.TimeoutExpired(a[0], 3)


subprocess.run = _timeout
try:
    main._tmux_quiet("set-environment", "-t", "sf_s1", "SF_SID", "s1")
    quiet_ok = True
except Exception as e:  # noqa: BLE001
    quiet_ok = repr(e)
finally:
    subprocess.run = orig_run
check("metadata 呼叫逾時不會讓分頁失敗", quiet_ok is True, str(quiet_ok))
src = Path(main.__file__).read_text(encoding="utf-8")
start = src.index("    def _start_tmux(")
body = src[start:src.index("\n    def _start_unix(", start)]
check("_start_tmux 的 resize／set-environment 都走容錯的 _tmux_quiet",
      '"resize-window"' in body and "_tmux_quiet(\"resize-window\"" in body
      and body.count("_tmux_quiet(\"set-environment\"") >= 3, "")


# ── 5. 孤兒報告與 sid ─────────────────────────────────────────────────────
check("highest_sid 只看 sf_sN", tmux_sessions.highest_sid(["sf_s86", "sf_s172", "sf_x", "other"]) == 172)
details = [{"name": "sf_s171", "sid": "s171", "attached": 0, "created": 0, "activity": 0,
            "pane_pid": 4242, "pane_cmd": "claude"},
           {"name": "sf_s111", "sid": "s111", "attached": 1, "created": 0, "activity": 0,
            "pane_pid": 1, "pane_cmd": "claude"}]
orph = tmux_sessions.find_orphans(details, {"sf_s111"}, {"s171": "lecture"})
check("沒有分頁擁有的 session 才算孤兒，帶上 label",
      [o["name"] for o in orph] == ["sf_s171"] and orph[0]["label"] == "lecture", str(orph))
check("沒有孤兒時的訊息", tmux_sessions.format_orphans([]) == "no orphan sf_* tmux sessions")
rsrc = Path(Path(main.__file__).parent / "api_remote.py").read_text(encoding="utf-8")
check("sfctl orphans 接到 orphan_sessions", 'cmd == "orphans"' in rsrc and "self.orphan_sessions()" in rsrc)


# ── 6. 螢幕醒來 ───────────────────────────────────────────────────────────
api = _api()
api._active_sid = "s1"
sess = MagicMock()
sess._tmux_name, sess.child_pid = "sf_s1", 999
api.sessions = {"s1": sess}
calls = []
win = MagicMock()
win.evaluate_js.side_effect = lambda js: (calls.append(js), api.js_debug("wake", "screens"))
api._window = win
api._WAKE_ACK_TIMEOUT = 1.0
orig_refresh, orig_pressure = tmux_sessions.refresh_client, api_tmux.wake_watch.memory_pressure
refreshed = []
tmux_sessions.refresh_client = lambda tmux, name, pid: refreshed.append((name, pid)) or True
api_tmux.wake_watch.memory_pressure = lambda: "warn"
logs = []
orig_dlog = api_tmux._dlog
api_tmux._dlog = lambda cat, msg: logs.append(msg)
try:
    api._on_display_wake("screens")
    check("叫 tmux 重畫目前分頁（用我們自己的 client pid）", refreshed == [("sf_s1", 999)], str(refreshed))
    check("叫頁面 _sfOnWake 重畫", calls and "_sfOnWake" in calls[0], str(calls))
    check("記下頁面多久回應", any("page repainted" in m for m in logs), str(logs))
    check("記下記憶體壓力", any("pressure=warn" in m for m in logs), str(logs))
    api._on_display_wake("system")
    check("screens + system 同時到只處理一次", len(calls) == 1, str(len(calls)))
    win2 = MagicMock()
    api._window, api._display_woke_at, logs[:] = win2, 0.0, []
    api._on_display_wake("screens")
    check("頁面沒回應要留下紀錄", any("did not answer" in m for m in logs), str(logs))
finally:
    tmux_sessions.refresh_client = orig_refresh
    api_tmux.wake_watch.memory_pressure = orig_pressure
    api_tmux._dlog = orig_dlog

html = (Path(main.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
check("前端有 _sfOnWake，visibilitychange 也會叫它",
      "window._sfOnWake = function" in html and "window._sfOnWake('visible')" in html)
check("_sfOnWake 會把被 fitTerm 藏起來的 .xterm-screen 放出來",
      "querySelectorAll('.xterm-screen').forEach(el => { el.style.visibility = ''; })" in html)
check("前端不再無聲吞掉 restore 的例外",
      "try { await pywebview.api.restore_tmux_sessions(80, 24); } catch(e) {}" not in html)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
