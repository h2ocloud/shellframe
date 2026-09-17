#!/usr/bin/env python3
"""Bridge 啟動與分頁還原是兩條獨立的路，順序不能決定成敗（v0.36.5）。

回報：Telegram 顯示「Sessions: none」、/list 空的、任何指令都是
「No active session」——而 app 裡有 21 個分頁。

根因：v0.36.1 起 bridge 由 Python 在視窗開起來**之前**自己啟動（UI 卡住不該讓
遠端失聯），而分頁是 UI 載入後才還原的。bridge 起來時 self.sessions 還是空的，
start_bridge 的「註冊現有分頁」那一圈因此一個都沒跑到；UI 之後看到 bridge 已經
在跑就不再呼叫 start_bridge，那些分頁於是永遠不在 bridge 裡。實測 app 21 個
分頁對 bridge 0 個 slot。

修法不去猜誰先誰後：註冊是冪等的，所以還原完就無條件同步一次，bridge 自己起來
之後也同步一次。

跑法：.venv/bin/python tests_bridge_session_sync.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import main as _main  # noqa: E402
_main._dlog = lambda *a, **k: None

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class _FakeBridge:
    bridge_id = "tg"

    def __init__(self):
        self.slots = {}
        self.refreshed = 0

    def register_session(self, sid, label, write_fn, **kw):
        self.slots[sid] = {"label": label, "cmd": kw.get("cmd", "")}

    def refresh_commands(self):
        self.refreshed += 1


class _FakeSession:
    def __init__(self, sid, cmd="claude", alive=True, bridged=True, label=None):
        self.sid = sid
        self.cmd = cmd
        self.alive = alive
        self._bridge_enabled = bridged
        self._recent = bytearray(b"")
        self.cols, self.rows = 200, 50
        if label:
            self._custom_label = label

    def write(self, text):
        pass


def api_with(sessions, bridge=None, line=None):
    api = object.__new__(_main.Api)
    api.sessions = {s.sid: s for s in sessions}
    api.bridge = bridge
    api.line_bridge = line
    api._prepare_pane_for_input = lambda s: None
    return api


# ── 1. bridge 先起來、分頁後還原（實測到的那個順序）────────────────────────
b = _FakeBridge()
api = api_with([_FakeSession("s1"), _FakeSession("s2", label="小N開發")], bridge=b)
check("同步前 bridge 是空的（重現回報的狀態）", b.slots == {})
api._sync_bridge_sessions()
check("同步後兩個分頁都在", sorted(b.slots) == ["s1", "s2"], str(sorted(b.slots)))
check("自訂名稱有帶上", b.slots["s2"]["label"] == "小N開發", str(b.slots["s2"]))
check("有補一次 refresh_commands（TG 的 /1 /2 選單才會更新）", b.refreshed == 1)

# ── 2. 冪等：已經註冊過的不重複、也不白白 refresh ──────────────────────────
api._sync_bridge_sessions()
check("第二次同步不再動任何東西", b.refreshed == 1, f"refreshed={b.refreshed}")
check("slot 數沒變", len(b.slots) == 2)

# ── 3. 不該進 bridge 的分頁不要進去 ───────────────────────────────────────
b2 = _FakeBridge()
api2 = api_with([_FakeSession("s1"),
                 _FakeSession("s2", alive=False),
                 _FakeSession("s3", bridged=False)], bridge=b2)
api2._sync_bridge_sessions()
check("死掉的分頁不註冊", "s2" not in b2.slots, str(sorted(b2.slots)))
check("使用者關掉 TG 的分頁不註冊", "s3" not in b2.slots, str(sorted(b2.slots)))
check("其餘照樣註冊", sorted(b2.slots) == ["s1"], str(sorted(b2.slots)))

# ── 4. 沒有 bridge 時不能炸 ───────────────────────────────────────────────
api3 = api_with([_FakeSession("s1")], bridge=None, line=None)
api3._sync_bridge_sessions()
check("沒有 bridge 時安靜跳過", True)

# ── 5. LINE bridge 也一起同步 ─────────────────────────────────────────────
tb, lb = _FakeBridge(), _FakeBridge()
api4 = api_with([_FakeSession("s1")], bridge=tb, line=lb)
api4._sync_bridge_sessions()
check("TG 與 LINE 兩邊都補", "s1" in tb.slots and "s1" in lb.slots)

# ── 6. 其中一邊壞掉不能拖垮另一邊 ─────────────────────────────────────────
class _BrokenBridge(_FakeBridge):
    def register_session(self, *a, **k):
        raise RuntimeError("boom")


tb2, lb2 = _BrokenBridge(), _FakeBridge()
api5 = api_with([_FakeSession("s1")], bridge=tb2, line=lb2)
api5._sync_bridge_sessions()
check("一邊註冊失敗，另一邊照樣補上", "s1" in lb2.slots, str(sorted(lb2.slots)))

# ── 7. 接線：還原完與 bridge 自啟後都要同步 ───────────────────────────────
src = (HERE / "main.py").read_text(encoding="utf-8")
restore = src.split("def restore_tmux_sessions")[1].split("\n    def ")[0]
check("分頁還原的兩個返回點都同步",
      restore.count("self._sync_bridge_sessions()") == 2,
      f"只有 {restore.count('self._sync_bridge_sessions()')} 處")
auto = src.split("def _autostart_bridge")[1].split("\n    def ")[0]
check("bridge 自啟成功後也同步（順序反過來時）",
      "self._sync_bridge_sessions()" in auto, auto[-300:])

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
