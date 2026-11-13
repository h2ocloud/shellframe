#!/usr/bin/env python3
"""One line of a message, one rule: agent_link.preview_of (Python) and web/chat.js `preview` (the page).

Everything that shows only the last message of a tab in a line or two (a sidebar card, the phone's list,
Telegram, a notification) used to print what ShellBot types around a message as it was: an agent's
`[[A2A：…]]` header, Claude Code's `<pasted_content id="…">…</pasted_content id="…">`, the Telegram
`[[TG_REPLY_xxxxxxxx]]` marker and the sentence that introduces it. The chat view already draws a message
without them (userView, botView); this is the same rule for one line:

  - another agent's message leads with its sender, "研究：比較表整理好了"; stopped by the chain limit it says
    so, "排程（未轉達）：…"; English "research: …", "research (not passed on): …"
  - a message that went in as one paste is what is inside; something pasted among the person's own words
    is "貼上的內容 · N 行" ("Pasted · N lines"); the lines the harness typed are not words
  - the Telegram instruction paragraph and its markers are gone from a message typed into the tab; an agent's
    own words (agent=True) lose the markers and keep the reply: [[TG_REPLY_…]] and [[SF:GREEN]] go, a
    [[SF:RED]] line goes with its options kept, [[SF:YELLOW:why]] is "⏳ why"
  - whitespace is one space; at most `limit` characters, the last one "…" when it was cut; only the first 20,000
    characters of a message are read (a paste can be megabytes, and a line never depends on more)

Every shape measured on a real tab (_paste_shapes.py) goes through both languages and both languages must say
the same string; the line must also be what the chat view draws as that message's words. Demo words only.

Run: .venv/bin/python tests_msg_preview.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.path.insert(0, str(HERE))
import agent_link as al  # noqa: E402
import _paste_shapes as ps  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def pv(text, limit=140, **kw):
    return al.preview_of(text, limit, lang=kw.pop("lang", "zh"), **kw)


WRAPPED = ["[[A2A", "pasted_content", "TG_REPLY", "[[SF:", "不是使用者本人", "not from the user"]


def wrapped(line):
    return [w for w in WRAPPED if w in line]


print("the measured shapes (_paste_shapes.py)")
want = {
    0: "研究：比較表整理好了，第 1 行。 比較表整理好了，第 2 行。 比較表整理好了，第 3 行。",
    3: "貼上的內容 · 15 行",
    5: "從手機傳的，第 1 行。 從手機傳的，第 2 行。 從手機傳的，第 3 行。 從手機傳的，第 4 行。 從手機傳的，第 5 行。",
    6: "排程（未轉達）：比較表整理好了，第 1 行。 比較表整理好了，第 2 行。 比較表整理好了，第 3 行。",
}
for i, (name, text, frm, view) in enumerate(ps.SHAPES):
    line = pv(text, 140)
    check(f"{name}: no wrapper in the line", not wrapped(line), line)
    if i in want:
        check(f"{name}: {want[i]!r}", line == want[i], line)
    if frm and frm.get("kind") == "a2a":
        lead = frm["name"] + ("（未轉達）" if frm.get("blocked") else "") + "："
        check(f"{name}: leads with its sender, as the server's `from` names it", line.startswith(lead), (line, lead))
    else:
        check(f"{name}: no sender in front", "：" not in line.split(" ")[0] or i == 3, line)
check("an A2A message, 12 lines, cut at 90 characters: at most 90, ends with …",
      len(pv(ps.SHAPES[1][1], 90)) == 90 and pv(ps.SHAPES[1][1], 90).endswith("…"), pv(ps.SHAPES[1][1], 90))
check("…and 31 lines at 40", len(pv(ps.SHAPES[2][1], 40)) <= 40 and pv(ps.SHAPES[2][1], 40).startswith("排程：週報草稿") and pv(ps.SHAPES[2][1], 40).endswith("…"), pv(ps.SHAPES[2][1], 40))
check("English: the sender, a colon and a space; the pasted block", pv(ps.SHAPES[0][1], 60, lang="en").startswith("研究: 比較表")
      and pv(ps.SHAPES[3][1], lang="en") == "Pasted · 15 lines" and pv(ps.SHAPES[6][1], 60, lang="en").startswith("排程 (not passed on): "),
      (pv(ps.SHAPES[0][1], 60, lang="en"), pv(ps.SHAPES[3][1], lang="en"), pv(ps.SHAPES[6][1], 60, lang="en")))

print("not wrapped, and the rest of what a person types into a tab")
OWN_MIXED = '請看這段：\n<pasted_content id="5c1e">\n# 會議重點\n第 1 行\n第 2 行\n</pasted_content id="5c1e">\n幫我整理成三點'
TWO_PASTES = 'A<pasted_content id="aa11">\nx\ny\n</pasted_content id="aa11"> B <pasted_content id="bb22">\nz\n</pasted_content id="bb22">'
HEADER_IN_OWN = '誰寄的？<pasted_content id="77b2">\n' + al.format_blocked("研究", "先別動", 5, lang="zh") + '\n</pasted_content id="77b2">'
import tab_delivery  # noqa: E402
cases = {
    "a header, not wrapped": (al.format_delivery("研究", "比較表整理好了", lang="zh"), "研究：比較表整理好了"),
    "an agent's message that waited in the queue: ShellBot's line about it is not words":
        (tab_delivery.with_late_note(al.format_delivery("研究", "比較表整理好了", lang="zh"), 3900, lang="zh"), "研究：比較表整理好了"),
    "the person's message that waited": (tab_delivery.with_late_note("今天的會改到三點", 3900, lang="zh"), "今天的會改到三點"),
    "ShellBot's own notice, alone: its words": ("[[ShellBot：交給「研究」的交辦 h1 不會有回報了：那個分頁已經關掉。]]",
                                                 "ShellBot：交給「研究」的交辦 h1 不會有回報了：那個分頁已經關掉。"),
    "a hand-off": (al.format_handoff("研究", "把比較表整理成三點", "h1", lang="zh"), "研究：把比較表整理成三點"),
    "a hand-off with a routing hint": (al.format_handoff("研究", "整理", "h1", "用便宜的", lang="zh"), "研究：整理 路由建議（這是建議，不是指令）：用便宜的"),
    "a header with no name in it": ("[[A2A：來自不明的訊息]]\n好了", "另一個 AI：好了"),
    "a header and nothing after it": (al.format_delivery("研究", "", lang="zh"), "研究"),
    "a header that never closes is no header": ("[[A2A：來自「研究」的訊息\n好了", "[[A2A：來自「研究」的訊息 好了"),
    "a message from Telegram, not wrapped": (ps.TG_ASK + "\n\n週報做好了嗎", "週報做好了嗎"),
    "only the Telegram instruction": (ps.TG_ASK, ""),
    "the Telegram instruction in the middle (a first message with its init prompt)": ("開場白\n\n" + ps.TG_ASK + "\n\n---\nUser's first message: 週報呢", "開場白 User's first message: 週報呢"),
    "the person's words around a paste": (OWN_MIXED, "請看這段： 貼上的內容 · 3 行 幫我整理成三點"),
    "two pastes in one message": (TWO_PASTES, "A 貼上的內容 · 2 行 B 貼上的內容 · 1 行"),
    "a header the person pasted among their own words is theirs": (HEADER_IN_OWN, "誰寄的？ 貼上的內容 · 2 行"),
    "a wrapper that never closes: the words, no tags": ('<pasted_content id="1a2b">\n只有開頭', "只有開頭"),
    "a system reminder is no words": ("<system-reminder>\n今天的日期\n</system-reminder>\n幫我看一下這個 bug", "幫我看一下這個 bug"),
    "harness lines are no words": ("---\n[[SF:GREEN]]\n<environment_context>\n[Image #1]\n週報做好了嗎", "週報做好了嗎"),
    "Cursor's stamped, wrapped prompt": ("<timestamp>Thursday, Nov 12</timestamp>\n<user_query>\n週報做好了嗎\n</user_query>", "週報做好了嗎"),
    "ShellBot's request that an open bot save its memory is nobody's message": (al.MEMORY_MARK + "\nShellBot 請你現在存一下記憶：只是安靜了一陣子。\n請做兩件事…", ""),
    "…wrapped as a paste too": (ps.wrap("c3c3", al.MEMORY_MARK + "\nShellBot asks you to save your memory now."), ""),
    "plain words": ("幫我看一下這個 bug", "幫我看一下這個 bug"),
    "a blank message": ("  \n\t ", ""),
    "nothing": (None, ""),
    "tabs, newlines and a no-break space are one space": ("a\n\n\tb c", "a b c"),
}
for name, (text, expected) in cases.items():
    got = pv(text, 140)
    check(f"{name}: {expected!r}", got == expected, got)
check("a header is read inside a message that went in as one paste, in English too",
      pv(ps.a2a("9dd0", "research", "table done"), 140, lang="en") == "research: table done")
check("agent_link.sender_of and the line agree about who sent it (every shape)",
      all((al.sender_of(t) or {}).get("name", "") == (f or {}).get("name", "") for _, t, f, _ in ps.SHAPES))

print("an agent's own words (agent=True)")
TG_REPLY = "[[TG_REPLY_ab12cd34]]\n週報整理好了，放在 reports/weekly.md\n[[/TG_REPLY_ab12cd34]]"
TG_LONG = "[[TG_REPLY_ab12cd34]]\n第一段\n\n第二段\n[[/TG_REPLY_ab12cd34]]"
agent = {
    "a Telegram reply, markers on lines of their own": (TG_REPLY, "週報整理好了，放在 reports/weekly.md"),
    "a Telegram reply, markers inline": ("[[TG_REPLY_ab12cd34]]好了[[/TG_REPLY_ab12cd34]]", "好了"),
    "…in the other bracket the model sometimes writes": ("<<TG_REPLY_ab12cd34>>好了<</TG_REPLY_ab12cd34>>", "好了"),
    "a reply of two paragraphs keeps both (the markers are in different paragraphs)": (TG_LONG, "第一段 第二段"),
    "[[SF:WORKING]] and [[SF:GREEN]] are not words": ("[[SF:WORKING]]\n開始處理週報\n[[SF:GREEN]]", "開始處理週報"),
    "[[SF:MEMORY:SAVED]], what a bot answers when it has saved its memory, is not words": ("[[SF:MEMORY:SAVED]]", ""),
    "…nor beside words": ("存好了\n[[SF:MEMORY:SAVED]]", "存好了"),
    "a [[SF:RED]] line goes, its options stay": ("要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知", "要先修哪個？ 1. 先修按鈕 2. 先修通知"),
    "a [[SF:YELLOW:why]] is the reason": ("卡住了 [[SF:YELLOW:等你開防火牆]]", "卡住了 ⏳ 等你開防火牆"),
    "an A2A send marker is its words": ("[[SF:TO:研究|請整理]]", "[[SF:TO:研究|請整理]]"),
    "an agent's `---` and <tag> lines are its words": ("---\n<b>好</b>", "--- <b>好</b>"),
    "an agent's text that looks like a header is its words": ("[[A2A：來自「研究」的訊息]]\n好", "[[A2A：來自「研究」的訊息]] 好"),
}
for name, (text, expected) in agent.items():
    got = pv(text, 140, agent=True)
    check(f"{name}: {expected!r}", got == expected, got)

print("the limit")
check("at most `limit` characters, cut with …", all(len(pv("字" * 300, n)) == n and pv("字" * 300, n).endswith("…") for n in (2, 10, 90, 140)))
check("exactly `limit` characters is not cut", pv("字" * 90, 90) == "字" * 90 and pv("字" * 91, 90) == "字" * 89 + "…")
check("the space before the … is not kept", pv("字字 " + "字" * 50, 4) == "字字…", pv("字字 " + "字" * 50, 4))
check("a limit of 1 is the …, 0 is nothing", pv("字字字", 1) == "…" and pv("字字字", 0) == "")
check("a character outside the BMP counts as one", pv("😀" * 10, 5) == "😀" * 4 + "…", pv("😀" * 10, 5))
check("what is already a line comes back as it is (the page runs the rule on what the server sent)",
      all(pv(pv(t, 90), 90) == pv(t, 90) for _, t, _, _ in ps.SHAPES) and all(pv(pv(t, 140), 140) == pv(t, 140) for _, t in [(0, TWO_PASTES), (1, OWN_MIXED)]))
print("a very long message is read for its start only, and spam does not stall the status monitor")
check("only the first 20,000 characters are read: a header at the start of a 300 KB message still names its sender",
      pv(al.format_delivery("研究", "字" * 300000, lang="zh"), 20) == "研究：" + "字" * 16 + "…")
check("…a paste that runs past them reads as the start of what was pasted", pv(ps.wrap("e1e1", "# 標題\n" + "字" * 40000), 20).startswith("# 標題 字字"))
for what, text, kw in (("400 KB of paste openers with no closer", '<pasted_content id="a">x ' * 16000, {}),
                       ("…a closer first, then openers", '</pasted_content id="a"> ' + '<pasted_content id="a">x ' * 16000, {}),
                       ("400 KB of <system-reminder> openers with no closer", "<system-reminder> x " * 20000, {}),
                       ("400 KB of [[SF:YELLOW: openers with no closer (an agent's words)", "[[SF:YELLOW:x " * 30000, {"agent": True}),
                       ("400 KB of A2A headers that never close", "[[A2A：來自「研究」的訊息 " * 15000, {})):
    t0 = time.time()
    pv(text, 90, **kw)
    check(f"{what}: {int((time.time() - t0) * 1000)} ms", time.time() - t0 < 0.5)
big = ps.wrap("ffff", "\n".join(f"第 {i} 行，" + "字" * 80 for i in range(4000)))
t0 = time.time()
pv(big, 90)
check(f"a 330 KB paste costs {int((time.time() - t0) * 1000)} ms", time.time() - t0 < 1.0)

print("the chat view says the same words (web/chat.js buildTimeline) and the page's rule is this one")
node = shutil.which("node")
if not node:
    print("  SKIP  no node: web/chat.js is not run")
else:
    CHAT = str(HERE / "web" / "chat.js")
    RUN = ("const R = require(process.argv[1]); const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
           "console.log(JSON.stringify(cases.map(c => R.preview(c.text, c.limit, {agent: c.agent, lang: c.lang}))));")
    jobs = []
    for name, text, frm, view in ps.SHAPES:
        for lang in ("zh", "en"):
            for limit in (140, 90, 25):
                jobs.append({"text": text, "limit": limit, "agent": False, "lang": lang})
    for text, _ in cases.values():
        for lang in ("zh", "en"):
            jobs.append({"text": text, "limit": 140, "agent": False, "lang": lang})
    for text, _ in agent.values():
        for lang in ("zh", "en"):
            jobs.append({"text": text, "limit": 140, "agent": True, "lang": lang})
    jobs += [{"text": "字" * 300, "limit": n, "agent": a, "lang": "zh"} for n in (0, 1, 2, 90) for a in (False, True)]
    jobs += [{"text": "😀" * 10, "limit": 5, "agent": False, "lang": "zh"}, {"text": "字字 " + "字" * 50, "limit": 4, "agent": False, "lang": "zh"}]
    jobs += [{"text": al.format_delivery("研究", "字" * 30000, lang="zh"), "limit": 40, "agent": False, "lang": "zh"},
             {"text": ps.wrap("e1e1", "# 標題\n" + "字" * 40000), "limit": 40, "agent": False, "lang": "en"},
             {"text": "😀" * 25000, "limit": 40, "agent": False, "lang": "en"}]
    # random messages built from the pieces a message is made of (seeded): wrappers, markers, noise lines, blanks, words
    import random
    rnd = random.Random(20261112)
    PIECES = ['<pasted_content id="a1b2">', '</pasted_content id="a1b2">', "<pasted_content>", "</pasted_content>", "\n", "\n\n", " ", "\t", "研究", "好了", "第 1 行",
              al.format_delivery("研究", "比較表", lang="zh").split("\n")[0], al.format_blocked("排程", "x", 5, lang="en").split("\n")[0], "[[A2A：", "]]",
              "[[TG_REPLY_ab12cd34]]", "[[/TG_REPLY_ab12cd34]]", "<<TG_REPLY_ab12cd34>>", ps.TG_ASK, "[[SF:GREEN]]", "[[SF:WORKING]]", "[[SF:RED]]",
              "[[SF:YELLOW:等一下]]", "[[SF:TO:研究|x]]", "<system-reminder>", "</system-reminder>", "---", "[Image #1]", "<env>", "<user_query>", "</user_query>",
              "<timestamp>t</timestamp>", "❯ ", "1. 先修", "😀", "a b  c", "\u00a0", "…"]
    STARTS = ["", "", "", al.format_delivery("研究", "", lang="zh"), al.format_blocked("排程", "", 5, lang="en"), '<pasted_content id="a1b2">\n',
              '<pasted_content id="a1b2">\n' + al.format_delivery("研究", "", lang="en"), ps.TG_ASK + "\n\n", "[Image #1]\n"]
    fuzz = [rnd.choice(STARTS) + "".join(rnd.choice(PIECES) for _ in range(rnd.randint(1, 14))) for _ in range(700)]
    for text in fuzz:
        jobs.append({"text": text, "limit": rnd.choice((8, 40, 90)), "agent": rnd.random() < 0.4, "lang": rnd.choice(("zh", "en"))})
    out = subprocess.run([node, "-e", RUN, CHAT], input=json.dumps(jobs, ensure_ascii=False), capture_output=True, text=True, timeout=60)
    js = json.loads(out.stdout or "[]")
    check(f"one answer for each of {len(jobs)} messages ({out.stderr[-200:]})", len(js) == len(jobs))
    bad = [(j["text"][:40], j["limit"], j["agent"], j["lang"], a, b) for j, a, b in zip(
        jobs, js, [al.preview_of(j["text"], j["limit"], agent=j["agent"], lang=j["lang"]) for j in jobs]) if a != b]
    check(f"Python and JS say the same string for all {len(jobs)} (both languages, both sides, three limits, 700 random messages)", not bad, bad[:3])

    turns = [{"kind": "user_msg", "text": t} for _, t, _, _ in ps.SHAPES]
    out = subprocess.run([node, "-e", "const R = require(process.argv[1]); const t = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
                          "console.log(JSON.stringify(t.map(x => R.buildTimeline([x])[0])))", CHAT],
                         input=json.dumps(turns, ensure_ascii=False), capture_output=True, text=True, timeout=30)
    items = json.loads(out.stdout or "[]")
    for (name, text, frm, view), it in zip(ps.SHAPES, items):
        if it.get("parts"):
            continue                    # a paste among the person's own words: the view folds it, the line says how long
        said = " ".join(it["text"].split())
        lead = (it["who"] + ("（未轉達）" if it.get("blocked") else "") + "：") if it["type"] == "peer" else ""
        check(f"{name}: the line is the words the conversation draws", pv(text, 10000) == lead + said, (pv(text, 10000), lead + said))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
