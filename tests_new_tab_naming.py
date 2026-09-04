#!/usr/bin/env python3
"""新分頁建立時直接問名字，且手動命名不會被 auto-slug 蓋掉（v0.30.25）。

實際使用流程是「新增分頁 → 馬上改名」，每次都要自己去雙擊很卡，所以新分頁一
建立就跳出命名 popup，按跳過就維持原本的 auto-slug 行為。

搭配的後端修正同樣關鍵：auto-slug 原本會在第一次送出訊息時依內容重新命名，
不管使用者有沒有自己取過名字——不修的話剛取的名字會在第一句話之後消失，這個
功能等於白做。

跑法：.venv/bin/python tests_new_tab_naming.py
"""
import sys
import types
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
    s = types.SimpleNamespace(sid="s1", cmd="claude", _slug_pending=True,
                              _custom_label=None, _tmux_name="sf_s1")
    api.sessions = {"s1": s}
    api.bridge = None
    api.line_bridge = None
    api._window = None
    api._persist_session_manifest = lambda *a, **k: None
    return api, s


# 手動命名要關掉 auto-slug，否則第一句話之後名字就被覆蓋
api, s = _api()
api.rename_session("s1", "遠銀提案")
check("手動命名後 auto-slug 被關掉", s._slug_pending is False)
check("名字有寫進 session", s._custom_label == "遠銀提案")

# 已經關掉的不會被打開
api, s = _api()
s._slug_pending = False
api.rename_session("s1", "x")
check("原本就關著的維持關著", s._slug_pending is False)

# 不存在的 sid 不炸
api, _ = _api()
import json  # noqa: E402
check("未知 sid 回 success=False 不炸",
      json.loads(api.rename_session("nope", "x")).get("success") is False)

# ── 前端：新分頁才問名字，preset（已帶名字）不問 ──
idx = (HERE / "web/index.html").read_text(encoding="utf-8")
check("openSession 接受 askName 選項", "const askName = !!(opts && opts.askName)" in idx)
check("只有沒帶 label 的新分頁會跳命名", "if (askName && !label) {" in idx)
check("New Session 的 Run 會帶 askName",
      "openSession(cmd, null, { askName: true })" in idx)
check("preset 那條沒帶 askName（已經有名字了）",
      "openSession(p.cmd, p.name);" in idx)
check("命名 popup 有 isNew 模式（標題與按鈕文字不同）",
      "renameSession(sid, { isNew: true })" in idx and "opts.isNew" in idx)
check("跳過鍵有翻譯（中英都有）",
      "skip: 'Skip'" in idx and "skip: '跳過'" in idx
      and "nameNewSession: 'Name this session'" in idx
      and "nameNewSession: '為這個分頁命名'" in idx)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
