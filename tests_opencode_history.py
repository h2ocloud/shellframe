"""opencode 分頁的上滑歷史來源（_opencode_history_response）回歸測試。

日常使用回報：opencode 分頁上滑「看不到歷史，滑上去還是同一屏」。根因是
opencode 走了 claude/codex 那條 sparse floor（transcript 少於 400 字就放棄、
落回終端管線）——但 opencode 的 TUI 是原地重繪，捲出視窗的內容既不進 terminal
scrollback 也不進 pyte history，alt-screen 下 tmux capture 拿到的就是使用者
眼前那一屏。落回去＝上滑等於沒滑。門檻因此改成「transcript 裡有沒有真的
對話」。

跑法：python3 tests_opencode_history.py
"""

import json
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_status  # noqa: E402
import api_history  # noqa: E402

API = object.__new__(api_history.HistoryApiMixin)
ANSI = re.compile(r"\x1b\[[0-9;]*m")

SCHEMA = """
CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT,
                      time_updated INTEGER);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                      time_created INTEGER, data TEXT);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                   data TEXT);
"""


def build_db(path, parts_by_message, cwd):
    """parts_by_message: [(role, [part_dict, ...]), ...]"""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO session VALUES (?,?,?,?)",
                ("ses_1", "測試 session", cwd, int(time.time() * 1000)))
    pid = 0
    for i, (role, parts) in enumerate(parts_by_message):
        mid = f"msg_{i:03d}"
        con.execute("INSERT INTO message VALUES (?,?,?,?)",
                    (mid, "ses_1", 1788532141000 + i, json.dumps({"role": role})))
        for p in parts:
            pid += 1
            con.execute("INSERT INTO part VALUES (?,?,?,?)",
                        (f"prt_{pid:03d}", mid, "ses_1", json.dumps(p)))
    con.commit()
    con.close()


def response_for(parts_by_message):
    tmp = tempfile.mkdtemp(prefix="sf_oc_test_")
    db = pathlib.Path(tmp) / "opencode.db"
    cwd = os.path.join(tmp, "work")
    os.makedirs(cwd, exist_ok=True)
    build_db(db, parts_by_message, cwd)
    old_db = api_history.HistoryApiMixin._OPENCODE_DB
    old_title = agent_status._tmux_pane_title
    api_history.HistoryApiMixin._OPENCODE_DB = db
    # 分頁 → session 只認 pane title（見 agent_status.opencode_session_id）
    agent_status._tmux_pane_title = lambda name: "OC | 測試 session"
    agent_status._OPENCODE_SES_CACHE.clear()
    try:
        worker = {"cmd": "opencode --model spark/spark-main", "cwd": cwd,
                  "tmux_name": "sf_test", "session_id": None}
        return API._opencode_history_response(worker, True)
    finally:
        api_history.HistoryApiMixin._OPENCODE_DB = old_db
        agent_status._tmux_pane_title = old_title
        agent_status._OPENCODE_SES_CACHE.clear()


# ── 1. 回歸本體：一來一往的短對話也要出 transcript ──
# 實測的原始案例（一句問句 + 一段天氣回覆）純文字只有 167 字、14 行，舊的
# 400 字 / 8 行門檻直接判死，overlay 落回 tmux alt-fallback＝原畫面。
def test_short_conversation_still_uses_transcript():
    resp = response_for([
        ("user", [{"type": "text", "text": "今天桃園天氣"}]),
        ("assistant", [
            {"type": "tool", "tool": "bash",
             "state": {"input": {"command": "curl -s wttr.in/Taoyuan"}}},
            {"type": "text", "text": "桃園天氣現在：\n\n- 溫度 25°C\n- 濕度 92%"},
            {"type": "step-finish"},
        ]),
    ])
    assert resp is not None, "短對話被 sparse floor 擋掉了（回歸）"
    data = json.loads(resp)
    assert data["success"] is True, data
    assert data["source"] == "transcript (opencode)", data["source"]
    plain = ANSI.sub("", data["text"])
    assert "今天桃園天氣" in plain, plain
    assert "溫度 25°C" in plain, plain
    assert len(plain.strip()) < 400, "這個 case 必須低於舊門檻才守得住回歸"


# ── 2. 只有工具行、沒有任何對話 → 回 None，讓 caller 落回終端管線 ──
# 換掉 400 字門檻不是「什麼都收」：沒有 user/assistant 訊息的 transcript
# 讀感不如活畫面，該讓路。
def test_tool_only_transcript_falls_through():
    resp = response_for([
        ("assistant", [
            {"type": "tool", "tool": "bash", "state": {"input": {"command": "ls"}}},
            {"type": "step-finish"},
        ]),
    ])
    assert resp is None, resp


# ── 3. 空 session → None ──
def test_empty_session_falls_through():
    assert response_for([]) is None


# ── 4. 長對話照舊（沒有把原本會過的 case 弄壞） ──
def test_long_conversation_still_works():
    body = "\n".join(f"- 第 {i} 點的說明文字，足夠長到超過舊門檻。" for i in range(20))
    resp = response_for([
        ("user", [{"type": "text", "text": "幫我整理"}]),
        ("assistant", [{"type": "text", "text": body}]),
    ])
    assert resp is not None
    assert json.loads(resp)["source"] == "transcript (opencode)"


# ── 5. cmd 辨識：帶路徑、帶 .exe、帶參數都要認得 ──
def test_is_opencode_cmd():
    ok = ("opencode", "opencode --model spark/spark-main",
          "/opt/homebrew/bin/opencode", "opencode.exe --continue")
    for cmd in ok:
        assert api_history.HistoryApiMixin._is_opencode_cmd(cmd), cmd
    for cmd in ("claude --model opus", "codex resume", "", "opencodex"):
        assert not api_history.HistoryApiMixin._is_opencode_cmd(cmd), cmd


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
