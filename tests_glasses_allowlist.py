#!/usr/bin/env python3
"""眼鏡（Agent Relay）白名單回歸（v0.30.0）。

這個開關不是偏好設定，是授權：ShellFrame 的每個分頁都跑
`--dangerously-skip-permissions`，把某個分頁開給眼鏡，等於「在外面講的每
一句話都會在這台機器上執行」。所以這裡釘住的全是**安全語意**：

  - allow list 不是 deny list（缺資料時必須是「關」，不是「開」）
  - 新分頁預設關
  - 開一個 sid 不會順手開到別的
  - 不存在的 sid 一律失敗，而且不能污染設定檔
  - 升級路徑：舊 manifest 沒有 glasses_enabled 欄位 → 一律關

跑法：.venv/bin/python tests_glasses_allowlist.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


class FakeSession:
    def __init__(self, cmd="claude", label=None, alive=True):
        self.cmd = cmd
        self.alive = alive
        self._custom_label = label
        self._tmux_name = "sf_x"
        self._bridge_enabled = True


def make_api(sessions, cfg):
    api = object.__new__(main.Api)
    api.sessions = sessions
    api._ordered_sids = lambda c, preferred=None: list(sessions.keys())
    api._persist_session_manifest = lambda preferred_order=None: None
    main.load_config = lambda: cfg
    main.save_config = lambda c: cfg.update(c)
    return api


# ── 1. provider 判定（眼鏡端要靠它分辨這則對話是誰講的） ──────────────
print("provider detection")
check("claude", main._session_provider("claude --model opus") == "claude")
check("codex", main._session_provider("codex --yolo") == "codex")
check("wrapped codex", main._session_provider('bash -lc "codex resume"') == "codex")
check("sf-codex launcher", main._session_provider("sf-codex") == "codex")
check("non-agent tab", main._session_provider("bash") == "other")
check("empty cmd never crashes", main._session_provider("") in ("other", ""))

# ── 2. allow / deny 只動指定的 sid ────────────────────────────────────
print("allow / deny")
cfg = {}
sessions = {"s1": FakeSession(), "s2": FakeSession("codex")}
api = make_api(sessions, cfg)

check("預設是關的", not getattr(sessions["s1"], "_glasses_enabled", False))
check("allowlist 一開始是空的", not cfg.get("glasses_allowed_sessions"))

api.set_session_glasses("s1", True)
check("s1 開了", sessions["s1"]._glasses_enabled is True)
check("s1 進了設定檔", cfg["glasses_allowed_sessions"] == ["s1"])
check("s2 沒被順手開到", not getattr(sessions["s2"], "_glasses_enabled", False))

api.set_session_glasses("s2", True)
check("兩個都在（有排序）", cfg["glasses_allowed_sessions"] == ["s1", "s2"])

api.set_session_glasses("s1", False)
check("s1 收回了", sessions["s1"]._glasses_enabled is False)
check("設定檔只剩 s2", cfg["glasses_allowed_sessions"] == ["s2"])

# ── 3. 不存在的 sid：失敗，且不能污染設定檔 ──────────────────────────
print("unknown sid is fail-closed")
before = list(cfg["glasses_allowed_sessions"])
import json  # noqa: E402
r = json.loads(api.set_session_glasses("s99", True))
check("回報失敗", r.get("success") is False)
check("設定檔沒被動到", cfg["glasses_allowed_sessions"] == before)

# ── 4. manifest 往返：欄位缺席時必須解讀成「關」 ─────────────────────
print("manifest round trip")
cfg2 = {}
sessions2 = {"s1": FakeSession(label="日常"), "s2": FakeSession("codex", label="Pi")}
api2 = make_api(sessions2, cfg2)
api2._persist_session_manifest = main.Api._persist_session_manifest.__get__(api2)
sessions2["s1"]._glasses_enabled = True
api2._persist_session_manifest()
entries = {e["sid"]: e for e in cfg2["session_manifest"]}
check("開著的寫進 manifest", entries["s1"]["glasses_enabled"] is True)
check("關著的也明寫 False", entries["s2"]["glasses_enabled"] is False)
check("allow list 同步", cfg2["glasses_allowed_sessions"] == ["s1"])

sessions2["s1"]._glasses_enabled = False
api2._persist_session_manifest()
check("收回後 allow list 清空", cfg2["glasses_allowed_sessions"] == [])

# ⚠️ 這一組釘的是 2026-08-31 差點釀成的事故類型：授權被「靜靜」清空。
# 一個沒設過 _glasses_enabled 的 Session 物件（任何未來新增的重建路徑都可能
# 忘了設），以前會被 getattr(..., False) 讀成「使用者關掉了」然後 discard。
print("silent-clear guard")
cfg5 = {"glasses_allowed_sessions": ["s1", "s2"]}
sessions5 = {"s1": FakeSession(label="日常"), "s2": FakeSession(label="研究")}
api5 = make_api(sessions5, cfg5)
api5._persist_session_manifest = main.Api._persist_session_manifest.__get__(api5)
# 兩個 session 物件都「沒有意見」（模擬重建後忘了設旗標）
api5._persist_session_manifest()
check("沒有意見的 session 不得推翻設定檔", cfg5["glasses_allowed_sessions"] == ["s1", "s2"])
check("manifest 沿用設定檔的值",
      all(e["glasses_enabled"] for e in cfg5["session_manifest"]))

# 明確關掉才會真的關掉
sessions5["s1"]._glasses_enabled = False
api5._persist_session_manifest()
check("明確 False 才會收回", cfg5["glasses_allowed_sessions"] == ["s2"])
sessions5["s2"]._glasses_enabled = False
api5._persist_session_manifest()
check("全部明確關掉就是空的（deny 仍要能清空）", cfg5["glasses_allowed_sessions"] == [])

# 升級路徑：舊版 manifest 沒有這個欄位
legacy = {"sid": "s7"}
allowed = set()
check("舊 manifest + 空 allow list = 關",
      bool(legacy.get("glasses_enabled", "s7" in allowed)) is False)

# ── 5. 授權變更一定要留痕（擋不住的就要看得見） ─────────────────────
print("audit trail")
cfg4 = {}
sessions4 = {"s1": FakeSession(label="日常"), "s2": FakeSession("codex", label="Pi")}
api4 = make_api(sessions4, cfg4)
api4.set_session_glasses("s1", True, "sfctl")
api4.set_session_glasses("s2", True, "api")
api4.set_session_glasses("s1", False, "ui")
trail = cfg4.get("glasses_audit") or []
check("每次變更都留一筆", len(trail) == 3)
check("記得是誰改的", [e["source"] for e in trail] == ["sfctl", "api", "ui"])
check("記得開還是關", [e["enabled"] for e in trail] == [True, True, False])
check("記得哪個 sid", [e["sid"] for e in trail] == ["s1", "s2", "s1"])
# 一個 shell 迴圈五秒就能把每個分頁各開一次——擋不住，但每一筆都要看得見
for i in range(60):
    api4.set_session_glasses("s1", i % 2 == 0, "sfctl")
check("留痕有上限，不會把設定檔養大", len(cfg4["glasses_audit"]) == 40)
check("留的是最新的那些", cfg4["glasses_audit"][-1]["sid"] == "s1")
# 失敗的變更不該留痕（不存在的 sid）
before = len(cfg4["glasses_audit"])
api4.set_session_glasses("s404", True, "sfctl")
check("失敗的變更不留痕", len(cfg4["glasses_audit"]) == before)

# ── 6. 狀態報告：沒有 bridge 時要說得出「送不進來」 ──────────────────
print("status report")
main.GLASSES_STATE_PATH = "/nonexistent/evenclaude/state.json"
cfg3 = {}
sessions3 = {"s1": FakeSession(label="日常")}
api3 = make_api(sessions3, cfg3)
report = api3._glasses_report()
check("報出 bridge 未執行", "未執行" in report)
check("報出 fail-closed", "fail-closed" in report)
check("教使用者怎麼開", "sfctl glasses allow" in report)

sessions3["s1"]._glasses_enabled = True
report2 = api3._glasses_report()
check("開放中列出 sid 與 provider", "s1" in report2 and "claude" in report2)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    sys.exit(1)
print("all green")
