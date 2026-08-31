#!/usr/bin/env python3
"""line-oriented agent 跳過送達驗證（v0.29.46）。

2026-08-29 回報：TG 送訊息給 pi 分頁會「跳針」——pi 多回一次，接著再收到一則
「無回應」通知。根因不在 pi：`_verify_injection` 的 delivered 只認兩個
**Claude Code TUI 專屬**訊號（`esc to interrupt` footer、bridge 抽到新回覆），
line-oriented REPL 兩者皆無 → 永遠 delivered=False → 補一個裸 Enter（agent
多收一次空輸入）＋45 秒後 deferred「無法確認送達」。訊息其實每次都送到了。

跑法：.venv/bin/python tests_tg_line_oriented.py
"""

import importlib.util
import os
import re

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
_bt._blog = lambda msg: None


# ── 1. 認得 line-oriented agent，含 preset 用的 wrapper ──
def test_matches_agents_and_wrappers():
    for cmd in ("pi", "pi --provider spark --model spark-vision",
                "/opt/homebrew/bin/pi", "sf-pi-spark",
                "sparkagent", "sf-sparkagent"):
        assert _bt.is_line_oriented(cmd) is True, cmd


# ── 2. 核心：精確 basename 比對，不得誤判含 "pi" 的其他指令 ──
#      （substring 比對會把這些一起吃掉 → 那些分頁失去送達驗證）
def test_no_substring_false_positives():
    for cmd in ("pip install requests", "pipenv shell", "api-server",
                "raspi-config", "python -m pip", "mpirun -n 4"):
        assert _bt.is_line_oriented(cmd) is False, cmd


# ── 3. TUI agent 不受影響（原本的驗證/重試行為要保留）──
def test_tui_agents_still_verified():
    for cmd in ("claude --model opus", "codex --search", "agy", "bash", ""):
        assert _bt.is_line_oriented(cmd) is False, cmd


# ── 4. 可由 settings 覆寫（比照 system_directive_agents）──
def test_settings_override():
    import time
    _bt._SETTINGS_CACHE["ts"] = time.monotonic()
    _bt._SETTINGS_CACHE["val"] = {"line_oriented_agents": ["myrepl"]}
    try:
        assert _bt.is_line_oriented("myrepl") is True
        assert _bt.is_line_oriented("pi") is False, "覆寫後不該再吃預設值"
    finally:
        _bt._SETTINGS_CACHE["ts"] = 0.0
        _bt._SETTINGS_CACHE["val"] = {}


# ── 5. 與 system_directive 清單刻意分離（語意不同，不可共用）──
def test_separate_from_directive_list():
    assert _bt._DEFAULT_LINE_ORIENTED_AGENTS is not _bt._DEFAULT_DIRECTIVE_AGENTS
    assert "pi" in _bt._DEFAULT_LINE_ORIENTED_AGENTS
    assert "pi" not in _bt._DEFAULT_DIRECTIVE_AGENTS, \
        "pi 不需要 system-directive 加框，只是不適用送達驗證"


# ── 6. 送出路徑真的接上閘門，且 TUI 分支仍在 ──
def test_gate_wired_in_send_path():
    src = __import__("inspect").getsource(_bt.TelegramBridge._handle_update)
    assert re.search(r"if injected and is_line_oriented\(", src), "閘門沒接上"
    assert re.search(r"elif injected and _detect_ai\(", src), \
        "TUI 驗證分支不見了——會讓 claude/codex 失去送達驗證"
    assert "skip delivery verification" in src, "缺 log 軌跡"


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

# ── 7. v0.29.51：line-oriented agent 的 marker 指示要多一句「只包最終回覆」。
#      pi 會逐階段輸出（先回「正在檢查…」再做事），每個 marker 區塊都會被
#      follow-up 機制各轉發一次 → 使用者端就是跳針（2026-08-31 s93 實案）。
def test_line_oriented_marker_prompt_has_final_only_rule():
    import inspect
    src = inspect.getsource(_bt.TelegramBridge._handle_update)
    assert "is_line_oriented(slot.cmd" in src, "marker 指示沒有依 agent 型別分流"
    assert "最終回覆" in src and "不要包進標記" in src, \
        "缺「進度不要包進 marker」的指示"
    # TUI agent 不該拿到這段（它們本來就只在收尾包一次）
    i_rule = src.index("不要包進標記")
    i_gate = src.index("is_line_oriented(slot.cmd")
    assert i_gate < i_rule, "指示必須在 line-oriented 閘門之內"

