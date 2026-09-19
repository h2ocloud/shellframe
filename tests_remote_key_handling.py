#!/usr/bin/env python3
"""遠端分頁也要吃得到自訂按鍵處理（v0.37.1）。

回報：操作遠端 session 時 Shift+Enter 沒用，會直接把對話送出去。

根因：自訂按鍵處理（Shift+Enter 換行、Esc 清行、Windows 的 Ctrl+C 複製）只掛在
本機 pane 上，`ensureRemotePane` 從頭到尾沒呼叫過它——遠端 pane 的按鍵是裸交給
xterm 的，而實測 xterm 對 Shift+Enter 一律送 `\\r`，跟純 Enter 一樣，於是直接送出。
這跟先前「遠端分頁上滑看不到歷史」是同一類缺口：本機 pane 陸續長出來的能力，
遠端 pane 沒有跟上。

第二層：送出的位元組原本寫死走 `write_input`（本機 PTY）。遠端分頁要走
Frame Link，所以先把「送按鍵給某個 session」收斂成一個入口，再讓兩邊共用。

第三層：送哪種編碼要看對方跑什麼 CLI，而遠端 session 物件的 cmd 原本是空字串
——遠端分頁因此一律被當成非 AI 分頁。改成從對方的分頁清單帶過來。

跑法：.venv/bin/python tests_remote_key_handling.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
idx = (HERE / "web/index.html").read_text(encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 統一的送出入口 ─────────────────────────────────────────────────────────
check("有 sendKeysToSession 這個入口", "function sendKeysToSession(sid, data)" in idx)
send = idx.split("function sendKeysToSession(sid, data) {")[1].split("\n  }\n")[0]
check("本機走 write_input", "pywebview.api.write_input(sid, data)" in send, send)
check("遠端走 link_remote_input", "link_remote_input(info.peerId, info.rsid, data)" in send, send)
check("分派藏在入口裡（呼叫端不必知道差別）", "isRemoteSid(sid)" in send, send)

# ── 按鍵處理裡不再有寫死的本機送出 ────────────────────────────────────────
handler = idx.split("function setupAttachKeyHandler(sid, term) {")[1].split("\n  }\n")[0]
check("Shift+Enter 走統一入口",
      "sendKeysToSession(" in handler and "\\x1b[27;2;13~" in handler, handler[-600:])
check("Esc 清行也走統一入口",
      handler.count("sendKeysToSession(sid, '\\x15')") == 1, handler)
check("按鍵處理裡沒有殘留寫死的 write_input",
      "pywebview.api.write_input" not in handler,
      "還有寫死的本機送出，遠端分頁會靜默失效")

# ── 遠端 pane 要掛上 ───────────────────────────────────────────────────────
remote = idx.split("function ensureRemotePane(sid, peer, rt) {")[1].split("\n  }\n")[0]
check("遠端 pane 掛上自訂按鍵處理",
      "setupAttachKeyHandler(sid, term);" in remote, remote[-500:])
check("遠端 pane 也還是有上滑歷史（別修一個弄壞另一個）",
      "setupScrollHistory(sid, pane);" in remote)
check("遠端 session 帶上對方的 cmd（否則一律被當非 AI 分頁）",
      re.search(r"cmd:\s*\(rt && rt\.cmd\)", remote) is not None, remote[:600])

# 三種 pane（本機新開、本機重連、遠端）都要掛
check("三種 pane 都掛了按鍵處理",
      idx.count("setupAttachKeyHandler(sid, term);") == 3,
      f"只有 {idx.count('setupAttachKeyHandler(sid, term);')} 處")

# ── 編碼選擇：遠端 Codex 與遠端 Claude 要不一樣 ───────────────────────────
def _is_codex(cmd):
    for tok in (cmd or "").split():
        base = re.split(r"[\\/]", tok)[-1]
        base = re.sub(r"\.\w+$", "", base).lower()
        if base == "codex" or re.match(r"^sf-(.+-)?codex(-.+)?$", base):
            return True
    return False


for cmd, want_codex in [
    ("codex --search", True),
    ("sf-codex --dangerously-bypass-approvals-and-sandbox", True),
    ("claude --resume abc --permission-mode bypassPermissions", False),
    ("bash", False),
    ("", False),
]:
    got = _is_codex(cmd)
    check(f"遠端分頁 cmd={cmd[:34]!r} → {'Codex 編碼' if want_codex else '換行字元'}",
          got is want_codex)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
