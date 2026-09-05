#!/usr/bin/env python3
"""遠端分頁的附檔與狀態燈（v0.35.2）。

回報三件事：遠端分頁無法上滑看歷史、沒有工作狀態燈、不能貼圖。這支守後兩件
——附檔與狀態燈；上滑歷史另計。

附檔：拖放拿到的是**本機路徑**，而遠端分頁不能用 write_input（那是本機 PTY），
drop handler 原本完全沒判斷遠端，檔案就這樣靜默消失。讀檔轉 base64 刻意放後端：
大圖走 pywebview 的 IPC 會把字串參數塞爆，前端也沒有讀本機檔案的能力。

狀態燈：走既有的 list／`/link/info`，不另外開高頻通道。

跑法：.venv/bin/python tests_remote_tab_features.py
"""
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

from main import Api  # noqa: E402
import frame_link  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


idx = (HERE / "web/index.html").read_text(encoding="utf-8")
main_src = (HERE / "main.py").read_text(encoding="utf-8")

# ── 附檔封裝 ──
check("frame_link 有 remote_attach_file", hasattr(frame_link.FrameLink, "remote_attach_file"))
check("Api 有 link_remote_attach_file", hasattr(Api, "link_remote_attach_file"))
sig = inspect.signature(frame_link.FrameLink.remote_attach_file)
check("它收的是路徑（讀檔在後端，不是前端塞 base64）",
      list(sig.parameters)[1:] == ["peer_id", "sid", "path"], str(sig))

src = inspect.getsource(frame_link.FrameLink.remote_attach_file)
check("有檔案大小上限（不讓一張大圖打爆連線）", "MAX_FILE_BYTES" in src)
check("檔案不存在會回錯誤而不是丟例外", "檔案不存在" in src)
check("最後走既有的 remote_paste（對方落地＋注入路徑）", "self.remote_paste(" in src)

api = Api()
api._link = lambda: MagicMock(**{
    "remote_attach_file.return_value": {"success": False, "message": "no such peer"}})
import json  # noqa: E402
check("未知 peer 回 success=False 不炸",
      json.loads(api.link_remote_attach_file("nope", "s1", "/tmp/x")).get("success") is False)

# ── 前端：drop 要分辨本機／遠端 ──
check("前端有統一入口 attachPathToSession", "async function attachPathToSession" in idx)
check("入口內部才分本機／遠端（呼叫端不必知道）",
      "if (!isRemoteSid(sid)) { attachFile(path, null); return true; }" in idx)
check("drop 的預讀分支會走遠端路徑",
      idx.count("await attachPathToSession(activeId, p)") >= 2,
      f"只找到 {idx.count('await attachPathToSession(activeId, p)')} 處")

# ── 狀態燈 ──
check("list 回傳 agent_state", '"agent_state": self._agent_state_for_list(sid)' in main_src)
check("狀態取自算好的快照，不在列表時解 transcript",
      "_agent_status_snapshot" in inspect.getsource(Api._agent_state_for_list))
check("取不到狀態回空字串（寧可沒燈也不拖慢列表）",
      Api._agent_state_for_list(Api(), "no-such-sid") == "")
check("遠端側欄會畫狀態燈", "const rState = rt.agent_state" in idx and "busy-dot" in idx)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
