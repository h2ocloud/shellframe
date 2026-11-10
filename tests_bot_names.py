#!/usr/bin/env python3
"""One name per bot, one way to find it (bot_names.py): a tab id, a bot's name, a tab's
title, an alias or an old label, the start of exactly one name; the binding before the
title; a "not a bot" tab is no bot's; two matches are an error, never a guess.

The roster and tabs are demo data. Run: .venv/bin/python tests_bot_names.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bot_names as bn  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


ROSTER = {
    "研究": {"label": "研究-CLD", "bot": {"memory": True}},
    "研究助理": {"label": "研究助理"},
    "Coding": {"label": "Coding-CDX", "bot": {"memory": True}},
    "時程信件": {"label": "時程信件-CLD"},
    "總管": {"label": "幕僚｜總管", "bot": {"external": {"kind": "webhook"}}},
}
TABS = [{"sid": "s1", "title": "研究-CLD"}, {"sid": "s2", "title": "Coding-CLD"},
        {"sid": "s3", "title": "查資料"}, {"sid": "s4", "title": "查資料"}, {"sid": "s5", "title": "zsh"}]
BIND = {"s2": {"bot": "Coding"}, "s9": {"bot": "研究"}}           # s9 is closed
ALIASES = {"coder": "Coding"}


def r(name, tabs=TABS, bind=BIND):
    return bn.resolve(name, ROSTER, tabs, bind, ALIASES)


def got(res):
    return (res.get("role"), res.get("sid"), res.get("via")) if res.get("ok") else (res.get("error"), res.get("candidates"))


print("1. the steps, in order")
check("a tab id is that tab (a temporary bot: no role)", got(r("s3")) == ("", "s3", "sid"), got(r("s3")))
check("a bot's name, its tab found by its label", got(r("研究")) == ("研究", "s1", "name"), got(r("研究")))
check("any case", got(r("coding")) == ("Coding", "s2", "name"), got(r("coding")))
check("its tab by the binding first, though the title says another engine", got(r("Coding"))[1] == "s2")
check("a tab's title", got(r("zsh")) == ("", "s5", "title"), got(r("zsh")))
check("a bot's tab by its title: the bot", got(r("研究-CLD")) == ("研究", "s1", "title"), got(r("研究-CLD")))
check("an old label", got(r("Coding-CDX")) == ("Coding", "s2", "alias"), got(r("Coding-CDX")))
check("an alias", got(r("coder")) == ("Coding", "s2", "alias"), got(r("coder")))
check("a bot asleep: its name, no tab", got(r("時程信件")) == ("時程信件", "", "name"), got(r("時程信件")))

print("2. the start of a name: only when exactly one")
check("the start of one name", got(r("時程")) == ("時程信件", "", "prefix"), got(r("時程")))
check("the start of two names: an error listing both, no guess",
      got(r("研")) == ("ambiguous", ["研究", "研究助理"]), got(r("研")))
check("a longer start that only one has", got(r("研究助")) == ("研究助理", "", "prefix"), got(r("研究助")))
check("a bot's name, its label and its tab's title are one target", got(r("Cod")) == ("Coding", "s2", "prefix"), got(r("Cod")))
check("nothing in the middle of a name is matched", got(r("資料")) == ("not_found", []), got(r("資料")))

print("3. two tabs with one title: an error with both, by id")
check("two temporary bots called the same", got(r("查資料")) == ("ambiguous", ["查資料 (s3)", "查資料 (s4)"]), got(r("查資料")))

print("4. renamed tabs, plain tabs, closed tabs")
renamed = [dict(t, title="工程師") if t["sid"] == "s2" else t for t in TABS]
check("the bot's tab renamed: still found by the bot's name (the binding)",
      got(r("Coding", tabs=renamed)) == ("Coding", "s2", "name"), got(r("Coding", tabs=renamed)))
plain = dict(BIND, s1={"plain": True})
check("a tab marked not a bot is not the bot's, whatever its title",
      got(r("研究", bind=plain)) == ("研究", "", "name") and bn.role_of_tab("s1", ROSTER, TABS, plain) == "",
      got(r("研究", bind=plain)))
check("a binding to a closed tab is not a tab", bn.bot_tab("研究", ROSTER, TABS[1:], BIND) == "")
check("a title that another bot's binding holds is not taken by a second bot",
      bn.role_of_tab("s1", ROSTER, TABS + [{"sid": "s7", "title": "另一個"}], {"s7": {"bot": "研究"}}) == "")
check("only: a role without memory does not count where only bots with memory do",
      bn.role_of_tab("s1", {"研究": {"label": "研究-CLD"}}, TABS, {}, only=lambda r_, e: "bot" in e) == "")

print("5. what is said when it fails")
check("not found, Chinese", "找不到「xyz」" in bn.explain(r("xyz"), "xyz", True))
check("ambiguous, English, with the candidates",
      '"研" matches more than one: 研究, 研究助理' in bn.explain(r("研"), "研", False))
check("nothing given: not found", got(r("  ")) == ("not_found", []))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
