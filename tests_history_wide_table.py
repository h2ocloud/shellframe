#!/usr/bin/env python3
"""歷史紀錄的寬表格：畫不下就改成直式區塊，不要硬畫方框再讓斷行切碎。

回報：歷史紀錄的表格樣式都跑掉。歷史畫面是從 transcript 重新排版的，方框表的
欄寬只看內容、不管 pane 有多寬；表格比 pane 寬時，後面的斷行把每條框線、每個
儲存格都切成好幾段，整張表碎成一團。同一張表在活畫面是「欄名: 值」的直式區塊
（Claude Code 自己遇到太寬的表格就這樣畫，2.1.284 實測）。

跑法：.venv/bin/python tests_history_wide_table.py
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules.setdefault("webview", MagicMock())
sys.modules.setdefault("bridge_telegram", MagicMock())
sys.path.insert(0, str(HERE))

import api_history  # noqa: E402

H = next(c for c in vars(api_history).values()
         if isinstance(c, type) and hasattr(c, "_render_md_table_vertical"))
SKIN = H._SKIN_DEFAULT
OPENCODE = H._SKIN_OPENCODE
SGR = re.compile(r"\x1b\[[0-9;]*m")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def md_table(rows):
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def render(rows, width, skin=SKIN, ansi=True):
    out = H._md_ansi_lines(md_table(rows), ansi, skin, width)
    return [SGR.sub("", ln) for ln in out]


WIDE = [
    ["#", "日期", "題目", "線索", "錄音"],
    ["1", "5/8 第 2 題", "知識庫：MCP + Codex + Notion 自動產生模組文件",
     "標記「甲」，整段沒人點名", "https://example.test/file/42824e47dbb07c1002e65fc8c810c498"],
    ["2", "5/8 第 3 題", "專案：AI 導入設計流程，客戶直接改 HTML 回寫 Figma",
     "前半段乙，介紹成員時提到老闆；45:27 之後換人講設計", "同上"],
]
NARROW = [["id", "name", "note"], ["1", "alpha", "short"], ["2", "beta", "also short"]]

# ── 1. 畫不下 → 直式區塊 ──
out = render(WIDE, 97)
check("a table wider than the pane is not drawn as a box",
      not any(ch in "".join(out) for ch in "┌┐└┘├┤┬┴┼│"), "\n".join(out[:4]))
check("no line is wider than the pane (nothing left for the wrapper to shred)",
      max(H._disp_width(ln) for ln in out) <= 97)
check("each row becomes a 'header: value' block",
      out[:5] == ["#: 1", "日期: 5/8 第 2 題",
                  "題目: 知識庫：MCP + Codex + Notion 自動產生模組文件",
                  "線索: 標記「甲」，整段沒人點名",
                  "錄音: https://example.test/file/42824e47dbb07c1002e65fc8c810c498"], out[:5])
check("blocks are separated by a 40-column rule, none after the last",
      out.count("─" * 40) == len(WIDE) - 2 and out[-1] != "─" * 40, repr(out))
check("the second block starts after the rule", out[out.index("─" * 40) + 1] == "#: 2")

# ── 2. 畫得下 → 照舊畫方框 ──
fit = render(NARROW, 97)
check("a table that fits is still a box", fit[0].startswith("┌") and fit[-1].startswith("└"))
check("a table that fits is untouched by width 0 (unknown width)",
      render(WIDE, 0)[0].startswith("┌"))
box_w = H._disp_width(render(WIDE, 0)[0])
check("exactly as wide as the pane still fits as a box",
      render(WIDE, box_w)[0].startswith("┌") and not render(WIDE, box_w - 1)[0].startswith("┌"))

# ── 3. 樣式 ──
raw = H._md_ansi_lines(md_table(WIDE), True, SKIN, 97)
check("labels are bold", raw[0] == SKIN["text"] + SKIN["bold"] + "#:\x1b[0m" + SKIN["text"] + " 1\x1b[0m", repr(raw[0]))
bold_cell = [["項目", "說明", "備註"], ["**重點**", "`code` 與一長串" + "字" * 80, "x" * 40]]
bo = H._md_ansi_lines(md_table(bold_cell), True, SKIN, 60)
check("inline markdown inside cells is still rendered in blocks",
      any("\x1b[1m重點" in ln for ln in bo) and not any("**" in ln for ln in bo))
check("a long value wraps instead of overflowing",
      max(H._disp_width(ln) for ln in bo) <= 60)

# ── 3b. 整條鏈路：transcript 事件 → 歷史文字（上滑看到的就是這個） ──
evs = [{"kind": "assistant_text", "text": "結果如下：\n\n" + md_table(WIDE) + "\n\n請確認。"}]
txt = SGR.sub("", H._render_transcript_overlay(evs, True, cols=97))
lines = txt.split("\n")
check("transcript overlay: wide table arrives as blocks, text around it intact",
      "結果如下：" in lines and "請確認。" in lines and "#: 1" in lines and "#: 2" in lines, txt[:200])
check("transcript overlay: no line exceeds the pane width",
      max(H._disp_width(ln) for ln in lines) <= 97)
check("transcript overlay: no stray box-drawing fragments",
      not any(ch in txt for ch in "┌┐└┘├┤┬┴┼│"))
narrow_txt = SGR.sub("", H._render_transcript_overlay(
    [{"kind": "assistant_text", "text": md_table(NARROW)}], True, cols=97))
check("transcript overlay: a table that fits is still a box",
      narrow_txt.startswith("┌") and "└" in narrow_txt)

# ── 4. 邊界 ──
empty = [["a", "b", "c"], ["1", "", "3" * 80]]
eo = render(empty, 60)
check("an empty cell keeps its label", "b:" in eo and "a: 1" in eo)
blank_head = [["", "名稱", "說明" * 30], ["1", "甲", "乙" * 30]]
check("a blank header gets a placeholder label, not ':'",
      render(blank_head, 40)[0].startswith("(1):"))
check("a single-row table has no rule at all",
      "─" * 40 not in render([WIDE[0], WIDE[1]], 97))
check("plain-text mode is untouched (source markdown comes back as is)",
      H._md_ansi_lines(md_table(WIDE), False, SKIN, 97) == md_table(WIDE).splitlines())

# ── 5. opencode 的撐滿表格同樣不再碎掉 ──
oc = render(WIDE, 97, OPENCODE)
check("opencode skin: a too-wide table also falls back (it was wrapped the same way)",
      not any(ch in "".join(oc) for ch in "┌┐└┘├┤┬┴┼│"))
oc_fit = render(NARROW, 97, OPENCODE)
check("opencode skin: a table that fits still stretches to the full width",
      H._disp_width(oc_fit[0]) == 97 - 0 or H._disp_width(oc_fit[0]) >= 90, oc_fit[0])

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
