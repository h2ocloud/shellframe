#!/usr/bin/env python3
"""write_input 的 IME 雙送保底去重（v0.30.6）。

前端 `_makeImeDedup` 擋的是 xterm.onData 那一條，但 2026-09-02 實測重複仍然
穿過來——10:05:51.665 / .758 兩筆一模一樣的 8 字、中間 93ms，而前端一筆
ime-dup 足跡都沒留，表示那條路徑根本沒經過它（write_input 在前端有 28 個
呼叫點）。write_input 是所有輸入的唯一出口，這份測試守的是那道保底。

紅線（前一版會吃掉使用者剛打完的最後一個字）：**不能吃掉使用者真的
想輸入的東西**——單字不管、ASCII 不管、超過窗口不管。

跑法：.venv/bin/python tests_ime_backstop.py
"""
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))

from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _api_with_session():
    """最小可用的 Api + fake session：只記錄真正寫進 PTY 的內容。"""
    api = Api()
    written = []
    s = types.SimpleNamespace(
        sid="s1", cmd="claude", cwd="~", lock=MagicMock(),
        write=lambda d: written.append(d),
        _recent=bytearray(), _startup_trust_pending=False,
        _last_activity_time=0.0, _last_user_activity_time=0.0,
        _idle_reap_state="", _slug_pending=False, _tmux_name=None,
        _init_pending=False, _master_turn=False,
    )
    api.sessions = {"s1": s}
    api.bridge = None
    api._arm_awaiting_response = lambda *a, **k: None
    return api, s, written


# 1. 就是這次的 bug：同一個詞組在窗口內送兩次
api, s, written = _api_with_session()
api.write_input("s1", "這些都是我打字後")
api.write_input("s1", "這些都是我打字後")
check("窗口內重複的詞組 → 只寫一次", written == ["這些都是我打字後"], f"written={written}")

# 2. 連三送也要一路擋掉（每次擋掉都往後推窗口）
api, s, written = _api_with_session()
for _ in range(3):
    api.write_input("s1", "確認")
check("連三送 → 仍只寫一次", written == ["確認"], f"written={written}")

# 3. 紅線：單字不去重（「哈哈」這種連字要留住）
api, s, written = _api_with_session()
api.write_input("s1", "哈")
api.write_input("s1", "哈")
check("單字重複 → 兩個都留（不吃字）", written == ["哈", "哈"], f"written={written}")

# 4. 紅線：純 ASCII 完全不經手
api, s, written = _api_with_session()
for _ in range(3):
    api.write_input("s1", "ls")
check("ASCII 重複 → 全留", written == ["ls", "ls", "ls"], f"written={written}")

# 5. 紅線：超過窗口就是使用者真的又打了一次
api, s, written = _api_with_session()
api.write_input("s1", "確認")
time.sleep(0.25)
api.write_input("s1", "確認")
check("超過 200ms 窗口 → 兩個都留", written == ["確認", "確認"], f"written={written}")

# 6. 內容不同不算重複
api, s, written = _api_with_session()
api.write_input("s1", "你好")
api.write_input("s1", "早安")
check("不同詞組 → 全留", written == ["你好", "早安"], f"written={written}")

# 7. 中間夾了別的輸入，之後的重複不該被誤判成同一次
api, s, written = _api_with_session()
api.write_input("s1", "測試")
api.write_input("s1", "\r")
api.write_input("s1", "測試")
check("中間夾 Enter → 後面那次仍留（Enter 不更新 IME 狀態）",
      written == ["測試", "\r", "測試"], f"written={written}")

# 8. 每個 session 各自獨立
api, s, written = _api_with_session()
s2 = types.SimpleNamespace(**vars(s))
w2 = []
s2.write = lambda d: w2.append(d)
api.sessions["s2"] = s2
api.write_input("s1", "同時")
api.write_input("s2", "同時")
check("不同 session 互不影響", written == ["同時"] and w2 == ["同時"],
      f"w1={written} w2={w2}")

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
