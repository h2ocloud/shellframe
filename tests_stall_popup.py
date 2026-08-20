"""阻擋性彈窗偵測（_detect_blocking_popup）回歸測試（v0.29.4）。

回報 2026-07-06：常收到「popup detected (UserNotificationCenter)」誤報。
UserNotificationCenter 擁有所有通知橫幅（非 TCC modal），且橫幅不擋前景——
移出阻擋清單；並要求命中的視窗有實際尺寸＋非透明，過濾 0x0 / 幽靈系統視窗。

跑法：
    .venv/bin/python tests_stall_popup.py
"""

import importlib.util
import os
import sys
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)

# 注入假 Quartz，讓偵測讀我們給的視窗清單
_WINS = []
_fake = types.ModuleType("Quartz")
_fake.kCGWindowListOptionOnScreenOnly = 1
_fake.kCGNullWindowID = 0
_fake.CGWindowListCopyWindowInfo = lambda opt, wid: _WINS
sys.modules["Quartz"] = _fake
_bt._sys.platform = "darwin"

_BR = object.__new__(_bt.TelegramBridge)


def _w(owner, width=400, height=200, alpha=1.0):
    return {"kCGWindowOwnerName": owner, "kCGWindowAlpha": alpha,
            "kCGWindowBounds": {"Width": width, "Height": height, "X": 0, "Y": 0}}


def _detect(wins):
    _WINS[:] = wins
    return _BR._detect_blocking_popup()


def test_notification_banner_ignored():
    assert _detect([_w("UserNotificationCenter")]) is None


def test_empty_screen():
    assert _detect([]) is None


def test_loginwindow_zero_size_ignored():
    assert _detect([_w("loginwindow", 0, 0)]) is None


def test_ghost_zero_size_owner_ignored():
    assert _detect([_w("SecurityAgent", 0, 0)]) is None


def test_transparent_owner_ignored():
    assert _detect([_w("SecurityAgent", 400, 200, alpha=0.0)]) is None


def test_real_security_dialog_detected():
    assert _detect([_w("SecurityAgent", 480, 260)]) == "SecurityAgent"


def test_quarantine_dialog_detected():
    assert _detect([_w("CoreServicesUIAgent", 500, 300)]) == "CoreServicesUIAgent"


def test_banner_plus_real_dialog_returns_dialog():
    assert _detect([_w("UserNotificationCenter"),
                    _w("SecurityAgent", 480, 260)]) == "SecurityAgent"


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
