#!/usr/bin/env python3
"""遠端分頁的上滑歷史、附檔與狀態燈（v0.35.2）。

回報三件事：遠端分頁無法上滑看歷史、沒有工作狀態燈、不能貼圖。這支三件都守。

上滑歷史壞在兩層：後端沒有 history 端點，前端連滾輪監聽都沒裝在遠端 pane 上。
只修一層，使用者看到的還是「上滑沒反應」。

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

# ── 上滑歷史 ──
check("frame_link 有 remote_history", hasattr(frame_link.FrameLink, "remote_history"))
check("Api 有 link_remote_history", hasattr(Api, "link_remote_history"))
fl_src = (HERE / "frame_link.py").read_text(encoding="utf-8")
check("有 /link/history 端點", 'path == "/link/history"' in fl_src)
check("端點跟 peek 一樣要過 _peer_may_control",
      '/link/history' in fl_src.split('def do_GET')[-1].split('path == "/link/stream"')[0]
      and "_peer_may_control" in fl_src.split('path == "/link/history"')[1].split("return")[0])
hist_src = inspect.getsource(frame_link.FrameLink.remote_history)
check("timeout 給得比 peek 寬（對方要先重建 transcript）", "timeout=15" in hist_src)
check("cols 有帶過去（表格／橫線要照對方寬度排）", "cols=" in hist_src)

check("後端有獨立的 history 指令（不併進 peek）", 'elif cmd == "history":' in main_src)
hist_cmd = main_src.split('elif cmd == "history":')[1].split('elif cmd == "peek":')[0]
check("history 帶 ansi 出去（peek 會剝掉，貼回 xterm 會破圖）", "ansi=True" in hist_cmd)
check("history 回傳 source（overlay 要標歷史來源）", '"source"' in hist_cmd)
check("遠端行數有上限（帶 ANSI 的一萬行不該走簽章連線）", "min(int(args.get(\"lines\"" in hist_cmd)

check("前端有統一的 fetchHistory", "async function fetchHistory(sid, cols)" in idx)
check("fetchHistory 內部才分本機／遠端",
      "if (isRemoteSid(sid))" in idx.split("async function fetchHistory")[1].split("function setupScrollHistory")[0])
check("overlay 只認一種回傳形狀",
      idx.count("ScrollHistory.show(result.text, sid, { source: result.source, ansi: result.ansi })") == 1)
check("遠端 pane 有掛滾輪監聽（原本完全沒裝）",
      "setupScrollHistory(sid, pane);\n    return sessions[sid];" in idx)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
