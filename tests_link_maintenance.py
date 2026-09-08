#!/usr/bin/env python3
"""Frame Link 的遠端維運：更新／重啟對方那台（v0.35.15）。

需求：對有權操作的電腦，可以從這台觸發對方 ShellFrame 的更新與重啟。

這是**會改變另一台機器狀態**的操作，所以三道限制都要守住：
  1. 白名單。不能把 action 字串轉手丟給 _execute——那等於把整個 sfctl 指令面
     開放給遠端。
  2. 對方那端的 _peer_may_control 閘。單向配對（對方是主控端）時要 403。
  3. 呼叫端先跟使用者確認，而且確認框要寫出是哪一台。

另外 restart 的連線中斷不是失敗：對方 0.8 秒後才 exec，回應來得及送出，但之後
連線本來就會斷。

跑法：.venv/bin/python tests_link_maintenance.py
"""
import inspect
import json
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


FL = frame_link.FrameLink
fl_src = (HERE / "frame_link.py").read_text(encoding="utf-8")
main_src = (HERE / "main.py").read_text(encoding="utf-8")
idx = (HERE / "web/index.html").read_text(encoding="utf-8")

# ── 白名單 ────────────────────────────────────────────────────────────────
check("有 remote_maintenance", hasattr(FL, "remote_maintenance"))
check("動作是白名單而不是任意字串",
      FL.MAINTENANCE_ACTIONS == frozenset(
          {"check_update", "update", "restart", "reload"}),
      str(sorted(FL.MAINTENANCE_ACTIONS)))
check("白名單裡沒有任何會執行任意指令的東西",
      not (FL.MAINTENANCE_ACTIONS & {"new_session", "send", "raw_input",
                                     "delegate", "exec", "peek"}),
      str(sorted(FL.MAINTENANCE_ACTIONS)))

link = object.__new__(FL)
res = FL.remote_maintenance(link, "p1", "rm -rf /")
check("非白名單的 action 直接拒絕（連 peer 都不查）",
      res.get("success") is False and "action must be one of" in res.get("message", ""),
      str(res))
res = FL.remote_maintenance(link, "p1", "")
check("空 action 也拒絕", res.get("success") is False, str(res))

# ── 端點的權限閘 ──────────────────────────────────────────────────────────
endpoint = fl_src.split('if path == "/link/maintenance":')[1] \
                 .split('if path == "/link/reorder":')[0]
check("端點有過 _peer_may_control", "_peer_may_control(peer_id)" in endpoint, endpoint[:200])
check("單向配對回 403", '403' in endpoint, endpoint[:400])
check("端點自己也擋非白名單（不只靠呼叫端）",
      "link.MAINTENANCE_ACTIONS" in endpoint, endpoint)
check("端點把 action 當指令名而不是拼字串",
      "link._execute(action, {})" in endpoint, endpoint)

# ── 逾時：update 要夠久、restart 的斷線不是失敗 ────────────────────────────
check("update 的逾時足夠跑 git pull ＋ 依賴安裝",
      FL.MAINTENANCE_TIMEOUTS["update"] >= 120,
      str(FL.MAINTENANCE_TIMEOUTS))
src = inspect.getsource(FL.remote_maintenance)
check("restart 的連線中斷被當成預期，不報失敗",
      'action == "restart"' in src and '"restarting": True' in src, src)


class _FakeLink:
    MAINTENANCE_ACTIONS = FL.MAINTENANCE_ACTIONS
    MAINTENANCE_TIMEOUTS = FL.MAINTENANCE_TIMEOUTS
    remote_maintenance = FL.remote_maintenance

    def __init__(self, raise_on=None):
        self.sent = []
        self._raise_on = raise_on

    def _peer_or_err(self, pid):
        return ({"host": "h", "port": 1}, None)

    def _mark_status(self, *a, **k):
        pass

    def _signed_request(self, peer, method, path, body=b"", **kw):
        self.sent.append((method, path, json.loads(body or b"{}"), kw.get("timeout")))
        if self._raise_on and self._raise_on in path:
            raise OSError("connection reset")
        return {"success": True, "message": "ok"}


fake = _FakeLink()
res = fake.remote_maintenance("p1", "update")
check("update 走 POST /link/maintenance 並帶 action",
      fake.sent == [("POST", "/link/maintenance", {"action": "update"},
                     FL.MAINTENANCE_TIMEOUTS["update"])], str(fake.sent))
check("成功時原樣回傳對方的結果", res.get("success") is True, str(res))

broken = _FakeLink(raise_on="/link/maintenance")
res = broken.remote_maintenance("p1", "restart")
check("restart 時連線斷掉仍回 success（對方正在重啟）",
      res.get("success") is True and res.get("restarting") is True, str(res))
res = broken.remote_maintenance("p1", "update")
check("update 時連線斷掉是真的失敗",
      res.get("success") is False, str(res))


# 「對方明確拒絕」不能被「重啟時斷線屬預期」那個分支吃掉——那會把權限被擋
# 報成「已送出，正在重啟」。
class _DenyingLink(_FakeLink):
    def _signed_request(self, peer, method, path, body=b"", **kw):
        raise frame_link.LinkHTTPError(403, "單向配對：對方無權操作這台")


res = _DenyingLink().remote_maintenance("p1", "restart")
check("restart 被對方 403 拒絕時要回失敗（不是「正在重啟」）",
      res.get("success") is False and res.get("status") == 403
      and not res.get("restarting"), str(res))
check("拒絕原因原樣帶回來", "無權" in (res.get("message") or ""), str(res))
check("LinkHTTPError 是 RuntimeError 的子類別（既有 except 照舊）",
      issubclass(frame_link.LinkHTTPError, RuntimeError))

# ── 後端指令 ──────────────────────────────────────────────────────────────
check("_execute_sfctl 認得 update", 'elif cmd == "update":' in main_src)
check("_execute_sfctl 認得 check_update", 'elif cmd == "check_update":' in main_src)
check("_execute_sfctl 早就有 restart／reload",
      'elif cmd == "restart":' in main_src and 'elif cmd == "reload":' in main_src)
upd = main_src.split('elif cmd == "update":')[1].split("elif cmd ==")[0]
check("update 走既有的 do_update（有復原路徑），不自己拼 git 指令",
      "self.do_update()" in upd and "subprocess" not in upd, upd[:300])
check("update 完不自動重啟（跟本機流程一致）",
      "restart_app" not in upd, upd[:300])
check("Api 有 link_remote_maintenance", hasattr(Api, "link_remote_maintenance"))

# ── 前端：入口與確認 ──────────────────────────────────────────────────────
check("動作列有維運入口", 'data-pact="maint"' in idx)
check("入口只在有權操作且線上時出現",
      "p.can_control && p.reachable" in idx, "少了條件，會給一個必定被拒的按鈕")
check("有維運面板", "function showPeerMaintenance(peer)" in idx)
maint = idx.split("const PEER_MAINT_ACTIONS = [")[1].split("];")[0]
check("四個動作都在面板上",
      all(a in maint for a in ("check_update", "update", "restart", "reload")), maint)
check("檢查更新是唯讀的，不用確認", "confirm: null" in maint, maint)
for act in ("update", "restart", "reload"):
    seg = maint.split(f"action: '{act}'")[1].split("},")[0]
    check(f"{act} 有確認文案", "confirm:" in seg and "confirm: null" not in seg, seg)
panel = idx.split("function showPeerMaintenance(peer)")[1].split("\n  }\n")[0]
check("確認框裡有寫出是哪一台",
      "peer.name" in panel and "window.confirm" in panel, panel[:400])
check("執行中會把按鈕停用（不讓連點兩次更新）",
      "b.disabled = true" in panel, panel[:800])
check("失敗時會把對方的復原指令顯示出來", "recovery" in panel, panel[:1200])

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
