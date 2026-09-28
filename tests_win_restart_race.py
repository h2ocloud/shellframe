#!/usr/bin/env python3
"""Windows self-restart 不會被單實例鎖擋下來自己（v0.37.7）。

`restart_app()` 在 Windows 上先 spawn 新行程，再讓舊行程睡 0.8 秒（cleanup_all +
os._exit）才真正結束，為的是讓 RPC 回應先送回前端。新行程開機夠快的話，走到
`_ensure_single_instance_windows()` 時舊行程的 mutex 還沒放掉——於是新行程判定
「已經有一個在跑」，把舊視窗拉到前景、自己結束。使用者看到的是：點了重啟，視窗
閃一下，畫面卻還是更新前的那個——新行程（帶著修正的那個）才是死掉的那個。

`_acquire_mutex_with_retry` 把重試/backoff 抽出來單獨測，不必碰真的 Windows handle。

跑法：.venv/bin/python tests_win_restart_race.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))

from main import _acquire_mutex_with_retry  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# 1. 第一次就拿到 → 不重試、不 sleep
sleeps = []
calls = {"n": 0}


def _always_free():
    calls["n"] += 1
    return ("handle", True)


h, ok = _acquire_mutex_with_retry(_always_free, sleep=sleeps.append)
check("第一次就拿到：只呼叫一次、不 sleep", ok is True and calls["n"] == 1 and sleeps == [],
      f"calls={calls['n']} sleeps={sleeps}")

# 2. 模擬 restart 情境：前 3 次舊行程還沒放（模擬 0.8s 的 exit 延遲），第 4 次拿到
calls = {"n": 0}
sleeps = []


def _busy_then_free():
    calls["n"] += 1
    return ("handle", calls["n"] >= 4)


h, ok = _acquire_mutex_with_retry(_busy_then_free, sleep=sleeps.append)
check("舊行程晚點才放掉 mutex：重試後仍然拿得到", ok is True and calls["n"] == 4,
      f"calls={calls['n']}")
check("重試之間真的有 backoff（sleep 次數 = 重試次數）", len(sleeps) == 3, str(sleeps))

# 3. 真的有另一個在跑（一直拿不到）→ 用完重試預算後老實回報失敗
calls = {"n": 0}
sleeps = []


def _always_busy():
    calls["n"] += 1
    return (None, False)


h, ok = _acquire_mutex_with_retry(_always_busy, attempts=10, delay=0.2, sleep=sleeps.append)
check("真的有另一個實例在跑：重試預算用完後回報未取得", ok is False, str(ok))
check("重試次數符合 attempts 設定（10 次嘗試＝9 次 sleep）", calls["n"] == 10 and len(sleeps) == 9,
      f"calls={calls['n']} sleeps={len(sleeps)}")

# 4. 重試預算涵蓋 restart_app() 舊行程的 0.8 秒退出延遲，留有餘裕
total_budget = 0.2 * 9  # attempts=10 → 9 次 sleep，每次 delay 秒
check("重試總時長 > 舊行程 0.8 秒退出延遲（否則沒補到這條 race）",
      total_budget > 0.8, f"budget={total_budget}s")

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
