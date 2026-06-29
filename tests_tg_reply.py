#!/usr/bin/env python3
"""bridge_telegram.py TG 回覆抽取/分段回歸測試（v0.14.2 修截斷+TUI洩漏+「和」污染）。

跑法：~/.local/apps/shellframe/.venv/bin/python tests_tg_reply.py
"""
import importlib.util, pathlib, sys

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("bt", HERE / "bridge_telegram.py")
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)
TB = bt.TelegramBridge

START = "[[TG_REPLY_ab12cd34]]"; END = "[[/TG_REPLY_ab12cd34]]"
INSTR = (f"最終要回 Telegram 的文字請放在 {START} 和 {END} 之間。"
         "標記外可以思考或操作，但手機只會收到標記內文字。")

class _S: pass
def mkslot(raw):
    s = _S(); s.expect_marker = True
    s.reply_start_marker = START; s.reply_end_marker = END
    s.pending_raw = raw; s.peek_fn = None; s.marker_prompt = INSTR
    return s

bridge = TB.__new__(TB)
fails = []
def ok(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond: fails.append(name)

# 1) 「和」污染：buffer 含指示回顯 + 真實回應
r1 = bridge._extract_marked_mobile_reply(mkslot(
    INSTR + "\n" + START + "這是真正要回覆的內容\n第二段補充。" + END + "\n"))
ok("和-pollution excluded", r1 and "真正要回覆" in r1 and r1 != "和" and "標記外" not in r1, repr(r1))

# 2) TUI leak + 重繪重複（夾在 marker 間）→ 哨兵截斷
r2 = bridge._extract_marked_mobile_reply(mkslot(
    START + "先說重點：worker 都在跑。\n要我建 watchdog 嗎？\n"
    "● How is Claude doing this session? (optional)\n"
    "1: Bad    2: Fine   3: Good   0: Dismiss\n"
    "要我建 watchdog 嗎？" + END))
ok("TUI-leak cut + no dup",
   "How is Claude" not in r2 and "Dismiss" not in r2
   and "worker 都在跑" in r2 and r2.count("要我建 watchdog 嗎") == 1, repr(r2))

# 3) 串流中未閉合 → 正常等待 / force 取最後完整
raw3 = START + "舊的完整回應" + END + "\n" + START + "新的還在打字中…"
ok("streaming: normal waits", bridge._extract_marked_mobile_reply(mkslot(raw3)) == "")
ok("streaming: force last complete",
   bridge._extract_marked_mobile_reply_force(mkslot(raw3)) == "舊的完整回應")

# 4) 多次重繪（皆閉合）→ 取最後完整
r4 = bridge._extract_marked_mobile_reply(mkslot(
    INSTR + "\n" + START + "回應v1" + END + "\n" + START + "回應v1 完整版" + END))
ok("multi-repaint takes last", r4 == "回應v1 完整版", repr(r4))

# 5) 一般回應（含無害的 "1:" 但非評分選項）不被誤切
r5 = bt.clean_mobile_marker_response("正常回覆\n第二行\n第三行 1: 一點補充")
ok("normal reply intact", "第三行" in r5 and "正常回覆" in r5, repr(r5))

# 6) 長回應分段不截斷、可還原
long = "\n".join(f"段{i} " + "内" * 70 for i in range(150))
parts = bt.split_for_telegram(long)
ok("long reply split (no truncate)",
   len(parts) > 1 and all(len(p) <= 3900 for p in parts) and "\n".join(parts) == long,
   f"{len(parts)} parts")

# 7) 單行超長硬切
hp = bt.split_for_telegram("x" * 9000)
ok("oversize single line hard-split", all(len(p) <= 3900 for p in hp) and "".join(hp) == "x" * 9000)

# 8) Terminal control fragments without ESC (e.g. "[0 q") do not leak to TG.
r8 = bridge._extract_marked_mobile_reply(mkslot(
    START + "Hi Huang [0 q [0 q\n第二行" + END))
ok("strips bracketed control fragments", "[0 q" not in r8 and "Hi Huang" in r8, repr(r8))

# 9) Reply longer than the viewport: while streaming, the TUI scrolls and
#    re-emits overlapping windows between the single start/end, so each line
#    repeats. Output must collapse to the unique lines once, no footer leak.
L = [f"第{i}段：要回覆給 Telegram 的內容說明。" for i in range(1, 7)]
fr = lambda lo, hi: "\n".join(L[lo:hi])
r9 = bridge._extract_marked_mobile_reply_force(mkslot(
    START + "\n" + fr(0, 4) + "\n" + fr(1, 5) + "\n" + fr(2, 6) + "\n"
    + fr(2, 6) + "\n" + END + "\n› Explain this codebase\n"))
ok("scroll-repaint dup collapsed",
   all(r9.count(x) == 1 for x in L) and all(x in r9 for x in L)
   and "Explain this codebase" not in r9, repr(r9))

# 10) Repeated complete blocks + composer footer leaked BETWEEN markers
#     (nested fresh start inside an open block). Tightest pairing + dedup must
#     yield a single clean copy with no footer / marker-token leak.
ANS = "工作完成：worker 都在跑。\n要我建 watchdog 嗎？"
r10 = bridge._extract_marked_mobile_reply_force(mkslot(
    START + ANS + "\n› Explain this codebase\n" + START + ANS + "\n" + END
    + "\n› Explain this codebase\n" + START + ANS + "\n" + END))
ok("repeated blocks → one clean copy",
   r10.count("要我建 watchdog 嗎") == 1 and "worker 都在跑" in r10
   and "Explain this codebase" not in r10 and "TG_REPLY" not in r10, repr(r10))

print(f"\n=== {10 - len(fails)}/10 groups PASS ===")
sys.exit(1 if fails else 0)
