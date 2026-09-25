#!/usr/bin/env python3
"""給外部調度者的低成本狀態查詢（v0.37.5）。

`sfctl state <sid>` / `--all` / `sfctl link-state <peer> <sid>`：一行或一個小
JSON，讓外部調度者分得出「在忙」「在等人」「撞到錯誤」「停滯了」，而不必把對話
或畫面搬出來。

守住的三件事：
  1. 不回傳畫面內容。欄位全是後端算好的結構化值。
  2. 不印秘密。活動、阻塞、錯誤都先過遮蔽——實測第一版把一行含
     `https://user:pass@host` 的文字當成錯誤回傳。
  3. 錯誤偵測不能誤判散文。第一版的 regex 會被「回應 timeout 的設定值」「quota
     這個欄位」命中，22 個正常分頁有 3 個被報成有問題。

跑法：.venv/bin/python tests_state_query.py
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import agent_status as A  # noqa: E402
import main as _main      # noqa: E402
_main._dlog = lambda *a, **k: None

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 遮蔽 ───────────────────────────────────────────────────────────────────
for raw, must_not in [
    ("API Error 401 invalid x-api-key sk-ant-abcdefghijklmnop", "sk-ant-abcdefghij"),
    ("**入口** https://neux:neux1234@www.toolhubmaster.com/neux/", "neux1234"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payloadpayloadpayload", "eyJhbGci"),
    ("clone https://oauth2:ghp_abcdefghijklmnopqrst@github.com/x", "ghp_abcdefghij"),
]:
    out = A.redact(raw)
    check(f"遮掉憑證：{raw[:34]!r}", must_not not in out, out)
check("遮蔽會壓成單行", "\n" not in A.redact("a\nb\nc"))
check("遮蔽會截短", len(A.redact("x" * 500)) <= 160)

# ── 錯誤偵測：要抓到真錯誤 ────────────────────────────────────────────────
for text in [
    "API Error: 401 invalid x-api-key",
    "You have hit your usage limit · resets 10:40pm",
    "Error: 429 Too Many Requests",
    "HTTP 503 upstream unavailable",
    "authentication failed",
    "connection refused",
    "Your credit balance is too low to run this request",
]:
    check(f"認得錯誤：{text[:38]!r}", bool(A._ERROR_RE.search(text)))

# ── 錯誤偵測：不能誤判一般敘述 ────────────────────────────────────────────
for text in [
    "官網前端至後端之間的負載平衡、WAF 或反向代理是否放行串流回應，以及回應 timeout 的設定值",
    "我把 quota 這個欄位改成可選",
    "**入口** https://example.com/neux/ （13 個工具）",
    "改完之後 401 那條路徑就不會再走到了",
    "把 timeout 調成 30 秒",
]:
    hit = bool(A._ERROR_RE.search(text)) and len(text.strip()) <= A._ERROR_MAX_LINE
    check(f"不誤判敘述：{text[:30]!r}", not hit)
check("超長的段落不當成錯誤（錯誤訊息是短的）",
      A._ERROR_MAX_LINE <= 300 and A._ERROR_MAX_LINE >= 80)


# ── 一列狀態的組法 ─────────────────────────────────────────────────────────
class _S:
    def __init__(self, cmd="claude", label=None, out_ago=5):
        self.cmd = cmd
        self.alive = True
        self._last_output_activity_time = time.time() - out_ago
        if label:
            self._custom_label = label


def api_with(snapshot=None, error=""):
    api = object.__new__(_main.Api)
    api.sessions = {}
    api._agent_status_snapshot = lambda sid: (snapshot or {})
    api._worker_ctx = lambda sid, s: {"cmd": s.cmd}
    orig = A.last_error
    A.last_error = lambda worker, **k: error
    api.__dict__["_restore_last_error"] = orig
    return api


api = api_with({"state": "working", "task": "Editing main.py",
                "model": {"name": "Opus 5", "effort": "xhigh"}})
row = api._session_state_row("s1", _S(label="sf dev"))
A.last_error = api.__dict__["_restore_last_error"]
check("欄位齊全",
      set(row) == {"sid", "label", "agent_state", "agent_activity", "agent_blocked",
                   "last_error", "last_output_at", "idle_for_s", "runs_on"},
      str(sorted(row)))
check("runs_on 是實際模型，不是只有 CLI 名稱", row["runs_on"] == "Opus 5 xhigh", row["runs_on"])
check("沒有任何畫面內容的欄位",
      not any(k in row for k in ("screen", "text", "output", "peek", "cmd")),
      str(sorted(row)))
check("idle_for_s 算得出來", 0 <= row["idle_for_s"] <= 30, str(row["idle_for_s"]))

api2 = api_with({"state": "", "model": None})
row2 = api2._session_state_row("s2", _S(cmd="bash"))
A.last_error = api2.__dict__["_restore_last_error"]
check("沒有狀態時回 idle（不是空字串）", row2["agent_state"] == "idle", row2["agent_state"])
check("沒有模型資訊時退回指令描述", bool(row2["runs_on"]), row2["runs_on"])

# 活動行也要遮蔽（它會帶正在跑的指令）
api3 = api_with({"state": "working",
                 "task": "Running curl -H 'Bearer eyJhbGciOiJIUzI1NiJ9.aaaaaaaaaaaaaaaaaaaa'"})
row3 = api3._session_state_row("s3", _S())
A.last_error = api3.__dict__["_restore_last_error"]
check("活動行裡的憑證有被遮掉", "eyJhbGci" not in row3["agent_activity"], row3["agent_activity"])

# ── 「需要注意」的判斷 ─────────────────────────────────────────────────────
api4 = object.__new__(_main.Api)
mk = lambda **kw: {"agent_blocked": "", "last_error": "", "agent_state": "done",
                   "idle_for_s": 10, **kw}
check("在等人 → 需要注意", api4._state_row_is_problem(mk(agent_blocked="要覆蓋嗎？")))
check("有錯誤 → 需要注意", api4._state_row_is_problem(mk(last_error="API Error 401")))
check("太久沒輸出 → 需要注意",
      api4._state_row_is_problem(mk(idle_for_s=3600), stale_min=15))
check("正在忙就不算停滯（長推理很正常）",
      not api4._state_row_is_problem(mk(agent_state="working", idle_for_s=3600),
                                     stale_min=15))
check("剛有輸出 → 正常", not api4._state_row_is_problem(mk(idle_for_s=10)))
check("沒輸出過（-1）不算停滯", not api4._state_row_is_problem(mk(idle_for_s=-1)))
check("門檻可調", api4._state_row_is_problem(mk(idle_for_s=120), stale_min=1)
      and not api4._state_row_is_problem(mk(idle_for_s=120), stale_min=30))

# ── CLI 輸出與退出碼 ───────────────────────────────────────────────────────
import sfctl  # noqa: E402

check("state 走專用的印法", hasattr(sfctl, "_print_state"))
check("退出碼有被送出去",
      "sys.exit(main() or 0)" in (HERE / "sfctl.py").read_text(encoding="utf-8"),
      "main() 的回傳值沒用到，退出碼永遠是 0")
for secs, want in [(5, "5s"), (90, "1m"), (7325, "2h02m"), (90000, "1d"), (-1, "沒輸出過")]:
    check(f"時間格式 {secs} → {want}", sfctl._fmt_age(secs) == want, sfctl._fmt_age(secs))

# ── 接線 ───────────────────────────────────────────────────────────────────
main_src = (HERE / "main.py").read_text(encoding="utf-8")
sfctl_src = (HERE / "sfctl.py").read_text(encoding="utf-8")
fl_src = (HERE / "frame_link.py").read_text(encoding="utf-8")
check("後端有 state 指令", 'elif cmd == "state":' in main_src)
check("state 與 list 分開（list 會被週期性拉，不該變貴）",
      "_session_state_row" in main_src and 'elif cmd == "list":' in main_src)
check("status 也回每個分頁的狀態（文件說它有）",
      '"states": [self._session_state_row(' in main_src)
check("status 不解析錯誤（它可能被輪詢）",
      "with_error=False" in main_src)
check("有 /link/state 端點", 'path == "/link/state"' in fl_src)
check("跨機端點過同一道權限閘",
      "_peer_may_control(peer_id)" in fl_src.split('path == "/link/state"')[1][:400])
check("link-state 共用既有的 peer 解析（不另寫一份）",
      '"link_state")' in main_src and "找不到 peer" in main_src)
check("CLI 有 state 與 link-state",
      'sub.add_parser(\n        "state"' in sfctl_src or '"state",' in sfctl_src)
check("CLI 有 --all 與 --stale-min", "--stale-min" in sfctl_src and '"--all"' in sfctl_src)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
