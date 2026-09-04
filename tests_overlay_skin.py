#!/usr/bin/env python3
"""上滑 overlay 的排版要跟活畫面對得上（v0.31.3）。

日常使用回報：opencode 分頁能上滑了，但「樣式或是定位不太一致」。原因是
overlay 用的是一套通用的 markdown→ANSI 近似值——表格原樣吐 `|` 管線、水平線
固定 40 欄、列點被改成 `•`、內容貼齊左緣——而 opencode 的 TUI 是縮排 5 欄、
表格畫成方框並撐滿寬度、列點保留 `-`。

排版規則是 2026-09-05 用 `tmux capture-pane -e` 從實機量的，不是估的。這份
測試把量到的數字釘住：

- 表格欄寬公式（撐滿模式）＝自然寬 + 平分剩餘，餘數由左往右各加 1。
  實測兩組：3 欄自然寬 6/6/6、可用 86 → 28/27/27；2 欄 14/18、可用 86 → 40/43。
- 斷行斷在標點之後，不是任意全形字之後（差 4 欄，且句子會被切在詞中間）。
- 水平線與表格同寬。

跑法：python3 tests_overlay_skin.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api_history  # noqa: E402

H = api_history.HistoryApiMixin
OC = H._SKIN_OPENCODE
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(s):
    return ANSI.sub("", s)


# ── 1. 顯示寬度：全形算 2 ──
def test_disp_width():
    assert H._disp_width("abc") == 3
    assert H._disp_width("中文") == 4
    assert H._disp_width("\x1b[1m中\x1b[0m") == 2


# ── 2. 表格欄寬：撐滿模式重現實機量到的兩組數字 ──
def test_table_widths_match_measured():
    # 3 欄，自然寬各 6（"欄位 A"），可用總寬 86
    rows = [["欄位 A", "欄位 B", "欄位 C"], ["資料 1", "資料 2", "資料 3"]]
    out = H._render_md_table(rows, OC, 86, True)
    top = plain(out[0])
    assert H._disp_width(top) == 86, H._disp_width(top)
    segs = [len(x) for x in top.strip("┌┐").split("┬")]
    assert segs == [28, 27, 27], segs
    # 2 欄，自然寬 14 / 18，可用總寬 86
    rows2 = [["模式", "說明"], ["Active-Passive", "主備模式"],
             ["x", "多區域同時處理流量"]]
    top2 = plain(H._render_md_table(rows2, OC, 86, True)[0])
    segs2 = [len(x) for x in top2.strip("┌┐").split("┬")]
    assert segs2 == [40, 43], segs2


# ── 3. 表格每一列之間都有分隔線（opencode 的畫法）──
def test_table_row_separators():
    rows = [["a", "b"], ["1", "2"], ["3", "4"]]
    out = [plain(x) for x in H._render_md_table(rows, OC, 40, True)]
    assert out[0].startswith("┌") and out[-1].startswith("└")
    assert sum(1 for x in out if x.startswith("├")) == 2, out


# ── 4. 每一列的顯示寬度都一致（CJK 補空格要用顯示寬度算，不是 len）──
def test_table_rows_same_display_width():
    rows = [["模式", "說明"], ["Active-Passive", "多區域同時處理流量"]]
    out = H._render_md_table(rows, OC, 86, True)
    widths = {H._disp_width(plain(x)) for x in out}
    assert widths == {86}, widths


# ── 5. 斷行：斷在標點之後（核心回歸——斷在任意全形字後會多塞 4 欄）──
def test_wrap_breaks_after_punctuation():
    line = "只回覆 ok 兩個字。這是一段刻意很長的中文訊息用來量測使用者訊息區塊的自動換行寬度，請不要做任何其他事情"
    out = H._wrap_ansi(line, 86)
    assert len(out) > 1
    assert out[0].endswith("，"), out[0]
    for ln in out:
        assert H._disp_width(ln) <= 86, (H._disp_width(ln), ln)


# ── 6. 沒有標點可斷時，退回全形字邊界，仍不可超寬 ──
def test_wrap_without_punctuation():
    out = H._wrap_ansi("中" * 60, 20)
    assert len(out) == 6, out
    assert all(H._disp_width(x) <= 20 for x in out)


# ── 7. 斷行後續行要沿用 SGR 狀態（不然下半段變沒有顏色）──
def test_wrap_carries_sgr():
    line = "\x1b[1m\x1b[38;2;1;2;3m" + "中" * 40
    out = H._wrap_ansi(line, 20)
    assert len(out) > 1
    assert out[1].startswith("\x1b[1m\x1b[38;2;1;2;3m"), repr(out[1][:24])
    # reset 之後就不該再帶著舊狀態
    out2 = H._wrap_ansi("\x1b[1m中中中\x1b[0m" + "文" * 30, 20)
    assert not out2[1].startswith("\x1b[1m"), repr(out2[1][:16])


# ── 8. 水平線跟表格同寬（舊行為寫死 40）──
def test_hr_width_follows_pane():
    out = H._md_ansi_lines("---", True, OC, 86)
    assert H._disp_width(plain(out[0])) == 86, out


# ── 9. 列點：opencode 保留 `-`，預設 skin 仍是 `•` ──
def test_bullet_marker_per_skin():
    assert plain(H._md_ansi_lines("- 項目", True, OC, 80)[0]) == "- 項目"
    assert plain(H._md_ansi_lines("- 項目", True, None, 0)[0]) == "• 項目"


# ── 10. 表格會被認出來並畫成方框，不是原樣吐管線 ──
def test_markdown_table_rendered_not_raw():
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    out = [plain(x) for x in H._md_ansi_lines(md, True, OC, 40)]
    assert out[0].startswith("┌"), out
    assert not any(x.strip().startswith("|") for x in out), out


# ── 10b. 儲存格裡的行內 markdown 要渲染掉，欄寬也要照渲染後的字算 ──
#        原字 `**Multi-AZ**` 比 `Multi-AZ` 寬 4 欄，照原字算整張表會歪掉。
def test_table_cell_inline_markdown():
    rows = [["**A**", "B"], ["**Multi-AZ**", "多可用性區域部署"]]
    out = H._render_md_table(rows, OC, 60, True)
    body = [plain(x) for x in out if "│" in plain(x)]
    assert all("**" not in x for x in body), body
    assert "Multi-AZ" in body[1], body
    assert {H._disp_width(plain(x)) for x in out} == {60}


# ── 11. 端到端：opencode skin 的縮排 5 欄與 ┃ 側槽 ──
def test_overlay_indent_and_gutter():
    evs = [{"kind": "user_msg", "ts": 1, "text": "你好"},
           {"kind": "assistant_text", "ts": 2, "text": "## 標題\n\n內文"}]
    lines = plain(H._render_transcript_overlay(evs, True, OC, 93)).splitlines()
    assert lines[0] == "  ┃", lines
    assert lines[1] == "  ┃  你好", lines
    assert lines[2] == "  ┃", lines
    body = [x for x in lines if x.strip() and not x.lstrip().startswith("┃")]
    assert all(x.startswith(" " * 5) for x in body), body
    # 空行不帶縮排（不然複製出來全是尾隨空白）
    assert "" in lines


# ── 12. 預設 skin（claude/codex）維持原本的 ❯ 與零縮排 ──
def test_default_skin_unchanged():
    evs = [{"kind": "user_msg", "ts": 1, "text": "hi"},
           {"kind": "assistant_text", "ts": 2, "text": "yo"}]
    lines = plain(H._render_transcript_overlay(evs, True)).splitlines()
    assert lines[0] == "❯ hi", lines
    assert lines[-1] == "yo", lines


# ── 13. cols=0（不知道寬度）不能炸，也不該斷行 ──
def test_unknown_width_is_safe():
    evs = [{"kind": "assistant_text", "ts": 1, "text": "x" * 300}]
    out = plain(H._render_transcript_overlay(evs, True, OC, 0))
    assert len(out.splitlines()) == 1, out.splitlines()[:3]


if __name__ == "__main__":
    import traceback
    fails = 0
    for name in sorted(list(globals())):
        if name.startswith("test_") and callable(globals()[name]):
            try:
                globals()[name]()
                print(f"PASS  {name}")
            except Exception:
                fails += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
