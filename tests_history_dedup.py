"""上滾歷史去重管線（_dedupe_history_lines）回歸測試。

這條戰線從 v0.11.x 修到 v0.22.x 十多輪，每輪都踩到前一輪的反例——每個
test case 對映 CHANGELOG 上一個真實踩過的坑。之後任何 heuristic 調整
（gate 閾值、collapse 視窗、keep-first/last）先跑這份，全綠才算沒回歸。

跑法：
    .venv/bin/python tests_history_dedup.py
    .venv/bin/python -m pytest tests_history_dedup.py
（需要 .venv：main.py import webview）
"""

import importlib.util
import os
import re
import sys

sys.argv = ["tests_history_dedup"]
_spec = importlib.util.spec_from_file_location(
    "sfmain", os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"))
_m = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_m)
except SystemExit:
    pass

API = object.__new__(_m.Api)
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def dedupe(lines, ansi=False):
    return API._dedupe_history_lines(list(lines), ansi)


# ── 1. CJK 串流重繪：同一寬中文行連續 N 次 → 收 1（v0.11.8 起的主戰場） ──
def test_cjk_stream_redraw_collapsed():
    out = dedupe(["交付成果與敘事結構的完整段落內容重繪測試"] * 6 + ["尾行"])
    assert out.count("敘事結構") == 1, out


# ── 1b. 段落之間的空行要留住（v0.30.16「上滑樣式會跑掉」根因） ──
# 空字串是任何字串的前綴，strict-prefix 那兩條判斷會兩邊都中：current 是空 →
# 當成「prev 的前綴」skip；prev 是空 → 當成「current 的前綴」拿 current 蓋掉。
# 結果每個內部空行都消失，上滾的歷史整段黏成一團、跟活畫面排版完全不同。
def test_paragraph_blank_lines_preserved():
    lines = ["第一段的內容在這裡", "", "第二段的內容在這裡", "", "第三段"]
    out = dedupe(lines).split("\n")
    assert out == lines, out


def test_blank_line_not_eaten_by_neighbours():
    # 前後都是「會互相成為前綴」的行，空行仍要活著
    lines = ["abc", "", "abcdef"]
    out = dedupe(lines).split("\n")
    assert out == ["abc", "", "abcdef"], out


def test_multiple_blank_lines_kept():
    lines = ["前文", "", "", "後文"]
    out = dedupe(lines).split("\n")
    assert out == lines, out


def test_prefix_collapse_still_works_across_nonblank():
    # 修空行不能把真正的 strict-prefix 摺疊弄壞（串流重繪的主要形狀）
    lines = ["這是一段還沒寫完的回", "這是一段還沒寫完的回覆內容"]
    out = dedupe(lines).split("\n")
    assert out == ["這是一段還沒寫完的回覆內容"], out


# ── 1c. 表格框線要全部留住（v0.30.28「上滑後表格黏成一團」根因） ──
# 一張 13 列的表有 12 條一模一樣的 `├───┼───┼───┤`，Gate B（同一行出現 ≥3 次
# 只留最後）會把它們全部吃掉，只剩頭尾兩條線。這跟空行同類：合法的重複。
def test_table_rules_all_preserved():
    top = "┌────────┬──────────────┬──────────┐"
    mid = "├────────┼──────────────┼──────────┤"
    bot = "└────────┴──────────────┴──────────┘"
    rows = []
    for i in range(6):
        rows += [f"│ 06-0{i}  │ 場次標題 {i}       │ 連結     │", mid]
    lines = [top] + rows[:-1] + [bot]
    out = dedupe(lines).split("\n")
    assert out.count(mid) == 5, f"分隔線只剩 {out.count(mid)} 條\n" + "\n".join(out)
    assert out == lines, out


def test_ascii_table_rules_preserved():
    mid = "+--------+----------+"
    lines = [mid, "| a      | b        |", mid, "| c      | d        |", mid,
             "| e      | f        |", mid]
    out = dedupe(lines).split("\n")
    assert out.count(mid) == 4, out


def test_box_rule_detection_does_not_eat_content():
    # 內容行裡有框線字元（表格的資料列）不能被當成框線而豁免摺疊——
    # 那些若真的重複三次以上，仍該收成一份
    row = "│ 同一列內容重複出現很多次的長字串 │"
    out = dedupe([row] * 4).split("\n")
    assert out.count(row) == 1, out


# ── 2. 程式碼合法重複 ×2 保留（v0.11.9「code 被吃掉」反例） ──
def test_code_repeats_preserved():
    lines = ["def foo():", "    return null;", "def bar():", "    return null;"]
    out = dedupe(lines)
    assert out.count("return null;") == 2, out


# ── 3. 短編號副標 ×3 保留（v0.21.x「outline 的 1./2. 消失」反例） ──
def test_short_numbered_headings_survive():
    lines = []
    for section in ("A", "B", "C"):
        lines += [f"Section {section} 的完整說明段落，內容彼此都不相同以避免其他 gate",
                  "3. Year One"]
    out = dedupe(lines)
    assert out.count("3. Year One") == 3, out


# ── 4. resize wrap-variant frame 收合（v0.22 前的「同段落重複 N 次」根因） ──
def test_resize_wrap_variants_collapsed():
    # 同一字元流、不同斷行點的兩個 frame（逐行 dedup 看不見，需 collapse pass）。
    # 每句帶序號使內容不自我重複，總長足以超過 min_drop=15 行。
    para = "".join(f"第{i:02d}句：模擬視窗改寬後整段串流被重繪殘留的寬度變體副本內容"
                   for i in range(24))
    frame_a = [para[i:i + 30] for i in range(0, len(para), 30)]   # 寬 30 斷行
    frame_b = [para[i:i + 42] for i in range(0, len(para), 42)]   # 寬 42 斷行
    assert len(frame_a) >= 15, "測資自檢：frame 行數需 ≥ min_drop"
    out = dedupe(frame_a + frame_b)
    joined = re.sub(r"\s+", "", out)
    # 內容只留一份（最後一個 frame），不是兩份拼在一起
    assert joined.count("第05句") == 1, joined.count("第05句")
    assert len(joined) <= len(para) + 60, (len(joined), len(para))


# ── 5. 相距很遠的常見 UI 元素不觸發整段砍除（v0.2x「上滑只剩 banner」反例） ──
def test_far_apart_ui_elements_not_nuked():
    sep = "─" * 60
    convo1 = [f"第一段對話第 {i} 行：各自獨特的內容避免其他 gate 誤收" for i in range(10)]
    convo2 = [f"第二段對話第 {i} 行：same-same but different 的獨特內容" for i in range(10)]
    lines = [sep] + convo1 + [sep] + convo2 + [sep]
    out = dedupe(lines)
    assert "第一段對話第 3 行" in out, "分隔線誤判成 redraw frame，整段對話被砍"
    assert "第二段對話第 7 行" in out, out


# ── 6. keep-LAST：user 訊息在 splash frame(T1) 與真回覆 frame(T2) 各出現一次，
#      保留 T2 那份（v0.21.x「上滾銜接不上、只看到 banner 版本」反例） ──
def test_keep_last_context():
    user_msg = "❯ 我有傳訊息了你可以去看一下紀錄然後回報結果給我"
    lines = ([user_msg]
             + ["Claude Code v2.1.199 啟動畫面橫幅內容顯示中"]
             + [f"啟動畫面雜項第 {i} 行的獨特內容" for i in range(5)]
             + [user_msg]
             + ["⏺ 真實回覆的第一行：查了紀錄，訊息有進來"])
    out = dedupe(lines).split("\n")
    idx = [i for i, l in enumerate(out) if user_msg in l]
    assert len(idx) == 1, f"user 訊息應只剩一份，got {len(idx)}"
    after = "\n".join(out[idx[0]:])
    assert "真實回覆的第一行" in after, "保留的是 T1(splash) 版本而非 T2(真回覆) 版本"


# ── 7. 裸 CR 清除（xterm.js convertEol 下 CR 會蓋掉整行只剩尾巴） ──
def test_bare_cr_stripped():
    out = dedupe(["前半段被蓋掉\r只剩尾巴"])
    assert "\r" not in out


# ── 8. ansi=True 逐行補 SGR reset（未閉合色碼滲染下一行） ──
def test_ansi_reset_per_line():
    out = dedupe(["\x1b[41m紅底未閉合的一行文字內容", "下一行不該被染色"], ansi=True)
    for line in out.split("\n"):
        if line:
            assert line.endswith("\x1b[0m"), repr(line)


# ── 9. 混合中英內容 ×4 收 1（v0.11.25「Warren 寄 V1.5.1 部版資訊 ×4」反例） ──
def test_mixed_content_4x_collapsed():
    lines = (["4/2 | Warren 寄 V1.5.1 部版資訊"] * 4
             + ["其他獨特內容一", "其他獨特內容二"])
    out = dedupe(lines)
    assert out.count("V1.5.1 部版資訊") == 1, out


# ── 10. 真實 capture 冒煙：拿最近一份 history-audit dump 的 RAW 段跑管線
#       （dump 不在就跳過——固定資產化靠 audit 持續產出） ──
def test_real_capture_smoke():
    import glob
    dumps = sorted(glob.glob(os.path.expanduser(
        "~/.config/shellframe/diag/history-audit_*.txt")), key=os.path.getmtime)
    if not dumps:
        print("  (skip: 無 diag dump)")
        return
    text = open(dumps[-1], encoding="utf-8", errors="replace").read()
    mraw = re.search(r"===== TMUX_RAW \(pre-dedup\) =====\n(.*?)\n=====", text, re.S)
    if not mraw:
        print("  (skip: dump 無 RAW 段)")
        return
    raw_lines = mraw.group(1).split("\n")
    out = dedupe(raw_lines)
    assert out is not None
    # 去重後不可掉超過一半的獨特內容（防「整段被砍」級回歸）
    uniq_in = {re.sub(r"\s+", " ", l).strip() for l in raw_lines if l.strip()}
    uniq_out = {re.sub(r"\s+", " ", l).strip() for l in out.split("\n") if l.strip()}
    kept = len(uniq_in & uniq_out) / max(1, len(uniq_in))
    assert kept >= 0.5, f"真實 capture 去重後僅存留 {kept:.0%} 獨特行"


# ── 11. transcript overlay 渲染保真度（v0.23.1「scroll 樣式不同」回歸）──
def test_transcript_render_fidelity():
    evs = [
        {"kind": "user_msg", "text":
            "<task-notification>\n<summary>Agent X finished</summary>\n"
            "<result>很長的內容</result>\n<usage><subagent_tokens>99</subagent_tokens></usage>\n"
            "</task-notification>\n請繼續"},
        {"kind": "assistant_text", "text":
            "## 標題\n**重點**結論，行內 `code` 如下：\n- 第一點\n1. 第二點\n---"},
        {"kind": "tool_call", "tool": "Edit", "target": "main.py"},
    ]
    text = API._render_transcript_overlay(evs, ansi=True)
    plain = ANSI.sub('', text)
    # 連續工具呼叫收合成摘要（工具行牆是 v0.23.1 的主要噪音）
    evs2 = ([{"kind": "tool_call", "tool": "Bash", "target": f"cmd {i}"} for i in range(6)]
            + [{"kind": "tool_result"}] * 3
            + [{"kind": "tool_call", "tool": "Edit", "target": "a.py"}] * 2
            + [{"kind": "assistant_text", "text": "完成"}])
    t2 = ANSI.sub('', API._render_transcript_overlay(evs2, ansi=True))
    tool_lines = [l for l in t2.split("\n") if "⏺" in l]
    assert len(tool_lines) == 1 and "Bash ×6" in t2 and "Edit ×2" in t2, t2
    # harness 雜訊不得直出，摺疊成摘要
    for bad in ("<task-notification>", "<usage>", "subagent_tokens", "<result>"):
        assert bad not in plain, plain
    assert "Agent X finished（內容略）" in plain
    assert "請繼續" in plain
    # markdown：** 與 ` 不得原樣露出；有 bold / 行內 code SGR；HR 轉線
    assert "**" not in plain and "`" not in plain, plain
    assert "\x1b[1m" in text and "\x1b[38;5;180m" in text
    assert "─" * 10 in plain
    # 樣式行皆閉合
    for l in text.split("\n"):
        if "\x1b[" in l:
            assert l.endswith("\x1b[0m"), repr(l)


def test_tmux_status_bar_filtered():
    """tmux 綠條（status bar）不是對話內容，兩個來源的管線都要濾掉——
    history-audit 對 s69(opencode) 抓到 '[sf_s69] 0:opencode.exe* …' 直出
    overlay（v0.29.0）。行首錨定：對話中「提到」sf_ 標籤的行不受影響。"""
    out = dedupe([
        "AI 的回覆第一行",
        '[sf_s69] 0:opencode.exe* [0,0] "OC | 原子習慣前兩頁內" 10:44 05-Jul',
        '  [sf_s43] 1:claude* [178,45] "移民議題" 09:00 05-Jul',
        "我在 [sf_s69] 這個 tab 看到 0:錯誤 記錄 [1,2] 座標",  # 內文提及 → 保留
        "AI 的回覆最後一行",
    ], ansi=False)
    assert "opencode.exe" not in out, out
    assert "1:claude*" not in out, out
    assert "我在 [sf_s69] 這個 tab" in out, out
    assert "AI 的回覆第一行" in out and "AI 的回覆最後一行" in out, out


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
