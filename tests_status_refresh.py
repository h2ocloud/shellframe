#!/usr/bin/env python3
"""手動／定期重判狀態燈號（v0.30.15）。

中斷對話（Ctrl+C／Esc）時 Claude Code 不一定會發 Stop hook，`_hook_events` 就卡在
working；而 status monitor 的 idle gating 又因為 PTY 不再輸出而跳過重算——燈號於是
一直停在「執行中」（Howard 2026-09-03 回報）。`refresh_agent_status()` 把 hook 與
狀態快取一起清掉，讓 heuristic 從畫面重新判斷。

跑法：.venv/bin/python tests_status_refresh.py
"""
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

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


def _api():
    api = Api()
    api.sessions = {"s1": object(), "s2": object()}
    api._status_cache = {"s1": {"result": "working"}, "s2": {"result": "done"}}
    api._hook_events = {"s1": {"state": "working"}, "s2": {"state": "done"}}
    return api


# 全部清
api = _api()
r = json.loads(api.refresh_agent_status(""))
check("不給 sid → 清掉全部分頁", r["refreshed"] == 2 and not api._status_cache
      and not api._hook_events, f"{r} cache={api._status_cache} hook={api._hook_events}")

# 只清一個
api = _api()
r = json.loads(api.refresh_agent_status("s1"))
check("給 sid → 只清那一個", r["refreshed"] == 1
      and "s1" not in api._status_cache and "s2" in api._status_cache
      and "s1" not in api._hook_events and "s2" in api._hook_events,
      f"{r} cache={list(api._status_cache)} hook={list(api._hook_events)}")

# 卡住的 working 一定要被清掉——這就是這支功能的存在理由
api = _api()
api.refresh_agent_status("")
check("卡在 working 的 hook 狀態被清掉", "s1" not in api._hook_events)

# 不存在的 sid 不能炸
api = _api()
r = json.loads(api.refresh_agent_status("nope"))
check("未知 sid 不炸、回報 1 筆", r["refreshed"] == 1 and "error" not in r, str(r))

# 沒有任何分頁時也不炸
api = Api()
api.sessions = {}
api._status_cache = {}
api._hook_events = {}
r = json.loads(api.refresh_agent_status(""))
check("零分頁不炸", r["refreshed"] == 0, str(r))

# 定期重算的常數要真的存在（前端按鈕只能救手動，安靜的分頁靠這個）
mono = (HERE / "main.py").read_text(encoding="utf-8")
m = re.search(r"HOOK_RESET_INTERVAL = (\d+(?:\.\d+)?)", mono)
check("status monitor 有五分鐘定期重算", bool(m) and float(m.group(1)) == 300.0,
      f"HOOK_RESET_INTERVAL={m.group(1) if m else '找不到'}")
check("定期重算會連 hook 快取一起清",
      "self._hook_events.clear()" in mono and "五分鐘定期重算" in mono)

# 前端按鈕要接到這支 API
idx = (HERE / "web/index.html").read_text(encoding="utf-8")
check("側欄有刷新鈕且接到 refresh_agent_status",
      'id="btn-refresh-status"' in idx and "refresh_agent_status(''" in idx)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
