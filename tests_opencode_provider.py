#!/usr/bin/env python3
"""opencode 支援回歸測試：provider registry + 狀態燈 + 模型徽章。

opencode 原本只是「能在分頁裡跑」的一般指令：不算 AI 分頁、沒有狀態燈、
沒有模型徽章。這份測試守住三件事：

1. registry 認得 opencode（含絕對路徑與 .exe），而且不誤判 codex。
2. 狀態直接讀它的 session SQLite——它沒有 JSONL transcript，事件狀態機無從判起。
   關鍵是 `finish` 的語意：`tool-calls` 只是停下來跑工具，turn 還沒結束。
3. 模型徽章逐訊息記錄，所以 session 中途換模型會跟著換。

跑法：python3 tests_opencode_provider.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_status as A  # noqa: E402
import usage_probe as U  # noqa: E402

NOW = 1788532200.0
SCHEMA = """
CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT,
                      time_updated INTEGER);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                      time_created INTEGER, data TEXT);
"""


def make_db(messages, title="桃園天氣", directory="/tmp/work", extra_sessions=()):
    """messages: [(role, finish, created_s, completed_s, model), ...]"""
    path = os.path.join(tempfile.mkdtemp(prefix="sf_oc_prov_"), "opencode.db")
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO session VALUES (?,?,?,?)",
                ("ses_1", title, directory, int(NOW * 1000)))
    for i, (sid, stitle, sdir, updated) in enumerate(extra_sessions):
        con.execute("INSERT INTO session VALUES (?,?,?,?)",
                    (sid, stitle, sdir, updated))
    for i, (role, finish, created, completed, model) in enumerate(messages):
        data = {"role": role, "modelID": model, "providerID": "spark",
                "time": {"created": int(created * 1000)}}
        if completed:
            data["time"]["completed"] = int(completed * 1000)
        if finish:
            data["finish"] = finish
        con.execute("INSERT INTO message VALUES (?,?,?,?)",
                    (f"msg_{i:03d}", "ses_1", int(created * 1000), json.dumps(data)))
    con.commit()
    con.close()
    return path


def _with_db(db, title="桃園天氣"):
    """把 OPENCODE_DB 與 pane title 換掉，回 restore()。"""
    saved = (A.OPENCODE_DB, A._tmux_pane_title)
    A.OPENCODE_DB = db
    A._tmux_pane_title = lambda name: ("OC | " + title) if title else "OpenCode"
    A._OPENCODE_SES_CACHE.clear()

    def restore():
        A.OPENCODE_DB, A._tmux_pane_title = saved
        A._OPENCODE_SES_CACHE.clear()
    return restore


def status(messages, now=NOW, sid="s1", title="桃園天氣"):
    restore = _with_db(make_db(messages), title)
    try:
        tr = A.StatusTracker()
        worker = {"cmd": "opencode --model spark/spark-main",
                  "cwd": "/tmp/work", "tmux_name": "sf_test"}
        return tr._opencode_status(sid, worker, now, "", None)
    finally:
        restore()


# ── 1. registry 認得 opencode，且不跟 codex 混淆 ──
def test_detect():
    for cmd in ("opencode", "opencode --model spark/spark-main",
                "/opt/homebrew/bin/opencode", "opencode.exe --continue"):
        assert U.detect_ai(cmd) == "opencode", cmd
    assert U.detect_ai("codex resume") == "codex"
    assert A._worker_kind("opencode --model spark/spark-main") == "opencode"


# ── 2. 沒有配額可報時回 None，不是編一個數字 ──
def test_no_quota_reading():
    assert U.PROVIDER_SPECS["opencode"]["probe"]({}) is None
    assert U.PROVIDER_SPECS["opencode"]["account"](None, {}) == ""
    ins = U.PROVIDER_SPECS["opencode"]["install"]
    assert ins.get("command") and ins.get("docs")


# ── 3. assistant 還沒寫 completed＝正在生成 ──
def test_generating_is_working():
    st = status([("user", "", NOW - 3, None, "spark-main"),
                 ("assistant", "", NOW - 2, None, "spark-main")])
    assert st["state"] == "working", st


# ── 4. 核心：finish=tool-calls 是「停下來跑工具」，不是 turn 結束 ──
#      工具可能跑很久，這段期間最新訊息就是那則已 completed 的 tool-calls。
def test_tool_calls_is_still_working():
    st = status([("assistant", "tool-calls", NOW - 30, NOW - 25, "spark-main")])
    assert st["state"] == "working", st
    assert "tool" in st["why"], st["why"]


# ── 5. finish=stop 且剛結束 → done；過了窗口 → idle ──
def test_stop_done_then_idle():
    st = status([("assistant", "stop", NOW - 10, NOW - 5, "spark-main")])
    assert st["state"] == "done", st
    old = A.OPENCODE_DONE_WINDOW_S
    st2 = status([("assistant", "stop", NOW - 600, NOW - 500, "spark-main")])
    assert st2["state"] == "idle", st2
    assert old == A.OPENCODE_DONE_WINDOW_S


# ── 6. 使用者送出後久久沒有 assistant → stuck，不是永遠 working ──
def test_stalled_turn_is_stuck():
    st = status([("user", "", NOW - A.STUCK_IDLE_S - 60, None, "spark-main")])
    assert st["state"] == "stuck", st


# ── 7. 沒有 session（全新分頁）→ 落回螢幕判讀，不炸 ──
def test_no_session_falls_back_to_screen():
    st = status([])
    assert st["state"] in ("idle", "unknown", "working", "done"), st
    assert "screen-only" in st["why"], st["why"]


# ── 7b. 核心回歸：pane title 還沒被 opencode 蓋上時，絕不能撈到隔壁分頁 ──
# 舊行為有一條「同 cwd 取最近 session」的 fallback：同一個目錄開第二個
# opencode 分頁，新分頁的上滑歷史與狀態燈直接顯示第一個分頁的對話。
def test_untitled_tab_does_not_borrow_neighbour_session():
    restore = _with_db(
        make_db([("assistant", "stop", NOW - 10, NOW - 5, "spark-main")]),
        title="")                      # pane title 仍是 ShellFrame 的分頁名
    try:
        got = A.opencode_session_id({"cwd": "/tmp/work", "tmux_name": "sf_new"})
    finally:
        restore()
    assert got is None, f"撈到了隔壁分頁的 session：{got}"


def test_untitled_tab_status_is_screen_only():
    st = status([("assistant", "stop", NOW - 10, NOW - 5, "spark-main")],
                title="")
    assert "screen-only" in st["why"], st["why"]


# ── 8. 模型徽章跟著最新訊息走（session 中途換模型會反映）──
def test_model_badge_follows_latest_message():
    restore = _with_db(
        make_db([("assistant", "stop", NOW - 60, NOW - 55, "spark-main"),
                 ("assistant", "stop", NOW - 10, NOW - 5, "qwen3-coder")]))
    try:
        info = A.detect_model_info({"cmd": "opencode", "cwd": "/tmp/work",
                                    "tmux_name": "sf_test"})
    finally:
        restore()
    assert info and info["name"] == "qwen3-coder", info
    assert info["provider"] == "opencode", info


# ── 9. session 對應：pane title 被 tmux 截斷仍要對得上（前綴比對）──
def test_session_resolution_matches_truncated_title():
    db = make_db([("assistant", "stop", NOW - 10, NOW - 5, "spark-main")],
                 title="桃園天氣預報與未來一週趨勢",
                 extra_sessions=[("ses_old", "別的對話", "/tmp/work",
                                  int((NOW - 9999) * 1000))])
    restore = _with_db(db, title="桃園天氣預報")     # 截斷後的樣子
    try:
        got = A.opencode_session_id({"cwd": "/tmp/work", "tmux_name": "sf_test"})
    finally:
        restore()
    assert got == "ses_1", got


# ── 10. 訊息時間戳當時鐘，不用檔案 mtime（DB 是所有 session 共用一個檔）──
def test_freshness_uses_message_stamp_not_file_mtime():
    db = make_db([("assistant", "stop", NOW - 600, NOW - 500, "spark-main")])
    os.utime(db, None)          # 隔壁分頁剛寫過的效果
    restore = _with_db(db)
    try:
        tr = A.StatusTracker()
        st = tr._opencode_status("s1", {"cmd": "opencode", "cwd": "/tmp/work",
                                        "tmux_name": "sf_test"}, NOW, "", None)
    finally:
        restore()
    assert st["state"] == "idle", f"檔案 mtime 汙染了新鮮度判斷：{st}"


if __name__ == "__main__":
    import traceback
    fails = 0
    for name in sorted(list(globals())):
        if name.startswith("test_") and callable(globals()[name]):
            try:
                globals()[name]()
                print(f"PASS  {name}")
            except Exception:
                fails += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
