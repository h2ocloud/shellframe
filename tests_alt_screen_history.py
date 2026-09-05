#!/usr/bin/env python3
"""alt-screen 分頁的上滾歷史（v0.30.29）。

回報「某個分頁無法上滑看歷史」。根因鏈有三段，缺一段都修不好：

1. Claude Code 2.1.261 起會用 alt-screen（2.1.260 不會）。alt-screen 的內容 tmux
   不記進 scrollback，所以終端來源只剩 pyte 的「目前這一屏」。
2. 判斷 pyte 夠不夠用的門檻是「> 64 字元」——只擋得掉全空，擋不掉「只有一屏」。
   實測該分頁 29 行／1525 字元，輕鬆通過，於是回傳一屏就當成歷史。
3. 退到 transcript 時，組 worker 少了 `transcript_hint`，而那是唯一指得到
   account-profile 目錄的線索（切過帳號的分頁 transcript 不在 ~/.claude/projects）。
   resolve_transcript 因此找不到檔案、回 None，最後落到 tmux capture——alt-screen
   下那是錯的 buffer。

跑法：.venv/bin/python tests_alt_screen_history.py
"""
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


hist = (HERE / "api_history.py").read_text(encoding="utf-8")
main_src = (HERE / "main.py").read_text(encoding="utf-8")

# ── 1. transcript 來源要拿得到 hook 回報的路徑 ──
m = re.search(r"def _transcript_history_response.*?kind = agent_status\._worker_kind",
              hist, re.S)
check("_transcript_history_response 有組 worker", bool(m))
if m:
    check("worker 帶 transcript_hint（唯一指得到 account-profile 的線索）",
          '"transcript_hint": getattr(s, "_hook_transcript_path", None)' in m.group(0),
          "少了它，切過帳號的分頁永遠找不到 transcript")

# ── 2. pyte 夠不夠用要看行數，不是字元數 ──
check("pyte 門檻改用行數判斷（只有一屏不算歷史）",
      "_has_history" in hist and "_rows * 1.5" in hist,
      "舊門檻只有 > 64 字元，擋不掉「只剩一屏」")
check("門檻仍保留字元下限（全空要擋掉）", "len(plain) > 64" in hist)

# ── 3. 存在判斷要涵蓋帳號切換的目錄 ──
check("_claude_transcript_exists 也找 account-profiles",
      "account-profiles" in main_src and '"projects"' in main_src)

E = Api._claude_transcript_exists
check("不存在的 uuid 仍回 False", E("00000000-0000-0000-0000-000000000000") is False)
check("空 uuid 回 False", E("") is False)

# 用真實環境驗：兩個目錄底下任一個 uuid 都要找得到
import glob  # noqa: E402
import os  # noqa: E402
found = []
for root in (os.path.expanduser("~/.claude/projects"),
             os.path.expanduser("~/.config/shellframe/account-profiles/*/*/projects")):
    hits = glob.glob(os.path.join(root, "*", "*.jsonl"))
    if hits:
        found.append((root, os.path.basename(hits[0])[:-6]))
check(f"兩個 transcript 根目錄都掃得到（實測 {len(found)} 個）",
      len(found) >= 1 and all(E(u) for _, u in found),
      str([r for r, _ in found]))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
