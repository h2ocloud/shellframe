#!/usr/bin/env python3
"""pi coding agent 支援回歸測試（v0.29.41）。

兩件事：
1. 燈號——pi 狀態列固定有「↑79k ↓1.5k 14.5%/128k」，那個 ↑ 命中共用
   SPINNER_RE 的 "↑" → pi 分頁永遠是 working、跑完不變燈（2026-08-24 回報的
   蒸餾任務實案）。改用 pi 專屬狀態機：braille spinner ＝工作中，token 數
   停止變動＝完成。
2. 安裝引導——registry 要有 pi 的 npm 安裝資訊。

跑法：.venv/bin/python tests_pi_provider.py
"""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_status as A
import usage_probe as U

IDLE = "↑79k ↓1.5k 14.5%/128k (auto)   spark-vision"
BUSY = "⠧ Working...\n" + IDLE
GROWN = "↑82k ↓1.7k 15.1%/128k (auto)   spark-vision"


def _tracker():
    return A.StatusTracker() if hasattr(A, "StatusTracker") else None


def _st(tr, screen, now):
    return tr.status_for("s1", {"cmd": "sf-pi-spark", "cwd": "~"},
                         screen_tail=screen, now=now)


# ── 1. registry 認得 pi，且不誤判 pip/pipenv ──
def test_detect():
    assert U.detect_ai("sf-pi-spark") == "pi"
    assert U.detect_ai("pi --provider spark --model spark-vision") == "pi"
    for bad in ("pip install x", "pipenv shell", "python -m pip"):
        assert U.detect_ai(bad) != "pi", bad


# ── 2. 安裝引導齊全（npm 指令 + Node 版本 + computer-use + allowScripts 提醒）──
def test_install_hint():
    ins = U.PROVIDER_SPECS["pi"]["install"]
    assert "npm i -g @earendil-works/pi-coding-agent" == ins["command"]
    note = ins.get("note", "")
    assert "22.19" in note, "缺 Node 版本需求"
    assert "pi-computer-use" in note, "缺 computer-use 擴充指令"
    assert "allowScripts" in note, "缺 postinstall 被擋的提醒"
    assert ins.get("docs")


# ── 3. braille spinner → working ──
def test_spinner_is_working():
    tr = _tracker()
    if not tr:
        return
    assert _st(tr, BUSY, 1000.0)["state"] == "working"


# ── 4. 核心回歸：token 停住不動 → 不可以永遠 working ──
#      （舊行為：SPINNER_RE 的 "↑" 命中 → 永遠 working）
def test_settled_tokens_not_working_forever():
    tr = _tracker()
    if not tr:
        return
    _st(tr, BUSY, 1000.0)                      # 工作中
    st = _st(tr, IDLE, 1000.0 + 30)            # spinner 消失、token 不動
    assert st["state"] != "working", \
        f"token 停住仍判 working（↑ 誤判回歸）：{st['state']} / {st['why']}"
    assert st["state"] in ("done", "idle"), st


# ── 5. token 還在長 → working ──
def test_growing_tokens_is_working():
    tr = _tracker()
    if not tr:
        return
    _st(tr, IDLE, 2000.0)
    st = _st(tr, GROWN, 2000.5)
    assert st["state"] == "working", st


# ── 6. 共用 compute_state 對 pi 狀態列會誤判（記錄為何需要專屬狀態機）──
def test_shared_path_would_misjudge():
    state, _act, _why = A.compute_state([], now=1000.0, screen_tail=IDLE)
    assert state == "working", \
        "若共用路徑不再誤判，_pi_status 的必要性需重新評估"


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}"); fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
