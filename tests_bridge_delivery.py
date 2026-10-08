#!/usr/bin/env python3
"""送達驗證：訊息已經進去了，就不能再說「無法確認」、更不能再貼第二次。

回報：每次都說「無法確認訊息已送進」，實際上訊息有進去。對話檔裡看得到：第一則
在 11:54:35 進去，14 秒後同一段又以排隊訊息（queue-operation）進去一次——是 ShellFrame
自己重貼的。兩層原因：
  1. 殘留判斷拿「訊息尾巴」去比整個畫面，但送出的訊息會回顯在對話區，所以每一則
     成功的訊息都被當成「卡在輸入框」→ 補 Enter → 重貼。
  2. 完全沒看 hook。Claude Code 收下 prompt 的當下 hook 就回報了（0.5 秒內），
     那是最準的訊號，驗證卻在畫面上找另一個字串。

跑法：.venv/bin/python tests_bridge_delivery.py
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))

import bridge_delivery as bd  # noqa: E402
import bridge_telegram  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


RULE = "─" * 60
TAIL = "跟我軟體設計無關我會跟客戶討論批次或直接送的議題"[-24:]
MSG = "跟我軟體設計無關 我會跟客戶討論 批次 或直接送的議題"

# 送出之後：訊息回顯在對話區，輸入框是空的，agent 開始工作前的一瞬間
SENT = "\n".join([
    f"❯ [SF-TG wrapper] …", f"  User: 客戶問題", f"  {MSG}", "",
    "✻ Thinking…", RULE, "❯ ", RULE,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)"])
# 沒送出：整段還躺在輸入框裡
STUCK = "\n".join([
    "⏺ 上一則回覆", "", RULE, f"❯ [SF-TG wrapper] … User: 客戶問題 {MSG}", RULE,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)"])
CHIP_IN_BOX = "\n".join(["⏺ 上一則回覆", RULE, "❯ [Pasted text #1 +25 lines]", RULE, "  ⏵⏵ bypass"])
CHIP_ECHOED = "\n".join(["❯ [Pasted text #1 +25 lines]", "", "✻ Thinking…", RULE, "❯ ", RULE, "  ⏵⏵ bypass"])
NO_RULES = f"❯ {MSG}\n  ⏵⏵ bypass"

# ── 1. 只看輸入框 ──
import re  # noqa: E402
check("reproduction: the old whole-screen match flagged the successfully sent message as residue",
      TAIL in re.sub(r"\s+", "", SENT))
check("an echoed message above the input box is not residue",
      not bd.residue_in_composer(SENT, TAIL))
check("the same text still sitting in the input box is residue",
      bd.residue_in_composer(STUCK, TAIL))
check("a paste chip in the input box is residue", bd.residue_in_composer(CHIP_IN_BOX, TAIL))
check("a paste chip echoed in the conversation is not", not bd.residue_in_composer(CHIP_ECHOED, TAIL))
check("composer_text is exactly what sits between the two rules",
      bd.composer_text(SENT).strip() == "❯" and "Thinking" not in bd.composer_text(SENT))
check("fewer than two rules in view falls back to the whole screen (the old behaviour)",
      bd.residue_in_composer(NO_RULES, TAIL))
check("a titled top rule (named tab) still counts as a rule",
      not bd.residue_in_composer(SENT.replace(RULE, "─" * 40 + " 分頁名稱 ─", 1), TAIL))
check("an empty tail never matches", not bd.residue_in_composer(STUCK, ""))

# ── 2. hook：agent 自己說收到了 ──
T = 1_000.0
check("a prompt accepted after injection confirms delivery",
      bd.prompt_accepted(lambda sid: ({"prompt_at": T + 0.4}, 0.1), "s1", T))
check("the tuple (res, age) and a bare dict both work",
      bd.prompt_accepted(lambda sid: {"prompt_at": T + 1}, "s1", T))
check("a prompt accepted BEFORE the injection is someone else's, ignored",
      not bd.prompt_accepted(lambda sid: ({"prompt_at": T - 5}, 0.1), "s1", T))
check("no prompt recorded yet (0.0) never confirms",
      not bd.prompt_accepted(lambda sid: ({"prompt_at": 0.0}, 0.1), "s1", T))
check("no callback / a callback that fails / no result are all just 'not confirmed'",
      not bd.prompt_accepted(None, "s1", T)
      and not bd.prompt_accepted(lambda sid: (_ for _ in ()).throw(RuntimeError("x")), "s1", T)
      and not bd.prompt_accepted(lambda sid: None, "s1", T))


# ── 3. 接進 bridge：_verify_injection ──
def bridge(screen, status=None):
    b = object.__new__(bridge_telegram.TelegramBridge)
    b._live_tail = lambda slot, rows=10: screen
    b._on_agent_status = status
    slot = type("S", (), {"sid": "s1", "last_extraction_ts": 0.0})()
    return b, slot


payload = f"[SF-TG wrapper] …\nUser: 客戶問題\n{MSG}"
b, slot = bridge(SENT)
t0 = time.time()
check("success screen, no hook, no footer: not delivered but NOT residue (no retry, no alarm)",
      b._verify_injection(slot, payload, time.time(), window=0.6) == (False, False))
b, slot = bridge(STUCK)
check("payload still in the input box: residue (a retry is justified)",
      b._verify_injection(slot, payload, time.time(), window=0.6) == (False, True))
b, slot = bridge(SENT, status=lambda sid: ({"prompt_at": time.time() + 0.1, "state": "working"}, 0.1))
t0 = time.time()
check("the hook confirms delivery at once, without waiting out the 8s window",
      b._verify_injection(slot, payload, time.time() - 1.0, window=8.0) == (True, False)
      and time.time() - t0 < 1.5, f"{time.time() - t0:.1f}s")
b, slot = bridge(STUCK, status=lambda sid: ({"prompt_at": time.time() + 0.1}, 0.1))
check("the hook wins even when the screen looks stuck (the screen can be wrong, the hook is exact)",
      b._verify_injection(slot, payload, time.time() - 1.0, window=0.6) == (True, False))
b, slot = bridge(STUCK, status=lambda sid: ({"prompt_at": time.time() - 60}, 0.1))
check("a stale hook time does not mask a genuinely stuck message",
      b._verify_injection(slot, payload, time.time(), window=0.6) == (False, True))

# ── 4. 狀態端：prompt_at 的來源 ──
import main  # noqa: E402

api = object.__new__(main.Api)
api.sessions, api._hook_events, api._status_cache = {}, {}, {}
args = lambda ev: {"sid": "s1", "event": ev, "notification_type": "", "message": "", "tool_name": "Bash"}
api._on_agent_event(args("PreToolUse"))
check("a tool call before any prompt leaves prompt_at at 0", api._hook_events["s1"]["prompt_at"] == 0.0)
t_before = time.time()
api._on_agent_event(args("UserPromptSubmit"))
t_prompt = api._hook_events["s1"]["prompt_at"]
check("UserPromptSubmit stamps prompt_at", t_before <= t_prompt <= time.time())
api._on_agent_event(args("PreToolUse"))
api._on_agent_event(args("Stop"))
check("later tool calls and Stop overwrite the state but keep prompt_at",
      api._hook_events["s1"]["prompt_at"] == t_prompt and api._hook_events["s1"]["state"] != "working")
api._status_tracker = type("T", (), {"last_result": lambda self, sid: ({"state": "done"}, 0.2)})()
snap = api._agent_status_snapshot("s1")
check("the status snapshot carries prompt_at to the bridge",
      snap and snap[0]["prompt_at"] == t_prompt and snap[0]["state"] == "done" and snap[1] == 0.2)
api._status_tracker = type("T", (), {"last_result": lambda self, sid: (None, None)})()
check("no tracker result still means no snapshot (unchanged)", api._agent_status_snapshot("s1") is None)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
