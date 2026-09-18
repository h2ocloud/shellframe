#!/usr/bin/env python3
"""新開分頁的「就緒／被選單卡住」判讀。

這兩條正則決定一件事：**要不要把一整段文字加 Enter 貼進這個分頁**。判斷錯的
代價不對稱：

  - 該就緒卻判成沒就緒 → 訊息不送，使用者看得到、可以重試（可接受）
  - 沒就緒卻判成就緒   → 文字打進選單＝幫使用者選了一個選項。Claude Code 的
    信任對話框第 2 項是 No, exit，分頁會被自己收到的訊息關掉

所以這裡守的是：空的 ❯ 輸入列算就緒；`❯ 某個選項` 這種**選單列不算**，而且要
被 startup_dialog_blocking 認出來、把選項字樣帶回去給使用者看。

選單的偵測刻意認「形狀」不認字串：每次 Claude Code 改版都可能冒出新對話框
（用量額度那個就是後來才有的），認形狀才不會每次都要追著改。

跑法：.venv/bin/python tests_agent_ready_gate.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def patterns():
    """Pull the live regexes out of main.py rather than restating them here —
    a copy would keep passing after the real ones changed."""
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    out = {}
    for name in ("_AI_READY_RE", "_MENU_RE", "_STARTUP_EXIT_OPTION_RE"):
        m = re.search(name + r"\s*=\s*_re\.compile\(", src)
        assert m, f"{name} not found in main.py"
        # Balance the parens rather than stopping at the first ")\n": the
        # patterns carry trailing comments, and a comment ending in ")" cut the
        # extraction short — the test then passed against half a pattern.
        i, depth = m.end() - 1, 0
        for j in range(i, len(src)):
            depth += (src[j] == "(") - (src[j] == ")")
            if depth == 0:
                break
        body = src[i + 1:j]
        flags = 0
        if "MULTILINE" in body or "_re.M" in body:
            flags |= re.MULTILINE
        if "IGNORECASE" in body:
            flags |= re.IGNORECASE
        parts = re.findall(r"r'((?:[^'\\]|\\.)*)'", body)
        out[name] = re.compile("".join(parts), flags)
    return out


IDLE_PROMPT = """──────────────────────────────────────
❯ 
──────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent
"""

# The usage-credits chooser, as captured from a real tab a fan-out had just
# opened. It blocked the delegated message until it was answered.
CREDITS_MENU = """Fable 5.1 now uses usage credits

Fable 5.1 runs on usage credits, purchased separately from your plan.

You don't have usage credits yet.

❯ Switch to Sonnet 5 and continue
  Set up usage credits on claude.ai
"""

TRUST_MENU = """Do you trust the files in this folder?

❯ 1. Yes, I trust this folder
  2. No, exit
"""

ANSWERED = """⏺ Switched to Sonnet 5 for this session · /model to change

⏺ 收到，群組功能正常

──────────────────────────────────────
❯ 
──────────────────────────────────────
"""


def main():
    p = patterns()
    ready, menu, exit_opt = p["_AI_READY_RE"], p["_MENU_RE"], p["_STARTUP_EXIT_OPTION_RE"]

    # ── 空輸入列＝可以送 ──
    check("an empty ❯ input line reads as ready", bool(ready.search(IDLE_PROMPT)))
    check("an idle prompt is not a menu", not menu.search(IDLE_PROMPT))
    check("a finished turn is ready again", bool(ready.search(ANSWERED))
          and not menu.search(ANSWERED))

    # ── 選單＝不能送 ──
    check("the usage-credits chooser is detected as a menu", bool(menu.search(CREDITS_MENU)))
    m = menu.search(CREDITS_MENU)
    check("the menu's own wording comes back",
          m and "Switch to Sonnet 5" in m.group(1) and "usage credits" in m.group(2))
    check("the trust dialog is still detected", bool(exit_opt.search(TRUST_MENU)))

    # ── 最重要的一條：選單列不可以被當成輸入列 ──
    # `❯ Switch to…` 若配到「輸入列」那條，貼上就等於選了那個選項。
    placeholder = re.compile(r"^\s*\[>›\]\s+\\S")
    check("the ready pattern has no ❯ in its placeholder alternative",
          r"[>›]\s+\S" in ready.pattern and r"[>›❯]\s+\S" not in ready.pattern)

    # ── 形狀而非字串：沒見過的對話框也要擋下來 ──
    unseen = "Something new in this release\n\n❯ Enable it now\n  Ask me later\n"
    check("an unseen menu is caught by shape", bool(menu.search(unseen)))
    check("ordinary prose is not mistaken for a menu",
          not menu.search("這是一段普通輸出\n  只是縮排了而已\n"))

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
