#!/usr/bin/env python3
"""Text written to agents follows the OS language: English everywhere except on a
Chinese system, so the same build works for anyone.

  - detection: zh-* (and Windows' "Chinese (...)") → zh, anything else → en;
    SHELLFRAME_PROMPT_LANG overrides
  - every prompt, brief and banner has an English version with no Chinese in it
  - the two versions say the same thing where it matters to an agent: the same
    sfctl commands and the same [[SF:…]] markers

Run: .venv/bin/python tests_prompt_lang.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHELLFRAME_BOTS_DIR"] = tempfile.mkdtemp(prefix="sf-lang-")
os.environ.pop("SHELLFRAME_PROMPT_LANG", None)

import agent_group  # noqa: E402
import agent_link  # noqa: E402
import agent_prompts  # noqa: E402
import bots  # noqa: E402
import sf_lang  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


CJK = re.compile(r"[　-〿㐀-鿿＀-￯]")

print("detection")
real = sf_lang._os_tag
for tag, want in (("zh-Hant-TW", "zh"), ("zh_CN.UTF-8", "zh"), ("Chinese (Traditional)_Taiwan", "zh"),
                  ("en-US", "en"), ("ja-JP", "en"), ("de_DE.UTF-8", "en"), ("", "en")):
    sf_lang._os_tag = lambda t=tag: t
    sf_lang._detected.cache_clear()
    check(f"{tag or '(nothing)'} → {want}", sf_lang.prompt_lang() == want)
sf_lang._os_tag = real
sf_lang._detected.cache_clear()
for forced in ("en", "zh"):
    os.environ["SHELLFRAME_PROMPT_LANG"] = forced
    check(f"SHELLFRAME_PROMPT_LANG={forced} wins", sf_lang.prompt_lang() == forced)
os.environ.pop("SHELLFRAME_PROMPT_LANG", None)

# Every piece of text ShellFrame writes to an agent, built in a given language.
ROSTER = {"coder": {"label": "coder", "responsibility": "code and tests", "bot": {}},
          "scout": {"label": "scout", "responsibility": "research", "bot": {}},
          "chief": {"label": "chief", "responsibility": "dispatch", "bot": {"external": {"url": "https://x/y"}}}}
TAGS = [{"label": "weekly", "sid": "s12"}]


def texts(lang):
    return {
        "brief": bots.compose_brief("coder", ROSTER["coder"], ROSTER, memory="m", last="l", last_name="f.md",
                                    skills=[("demo", "/p/SKILL.md")], lang=lang),
        "brief (primary)": bots.compose_brief("coder", ROSTER["coder"], ROSTER, is_primary=True, lang=lang),
        "brief (empty memory)": bots.compose_brief("coder", {}, {}, lang=lang),
        "summary prompt": bots.summary_prompt("coder", {}, "/m/s.md", "/m/MEMORY.md", lang=lang),
        "fallback summary": bots.fallback_summary("coder", "s1", "", "why", lang=lang),
        "reply command": bots.reply_command("chief", "abc", lang=lang),
        "routine": bots.routine_instruction("chief", "chief", lang=lang),
        "delegate": agent_prompts.delegate("coder", "", "do it", tagged=TAGS, lang=lang),
        "idle summary": agent_prompts.idle_summary("tab", 60, "/x/reflections.md", lang=lang),
        "a2a message": agent_link.format_delivery("scout", "hello", lang=lang),
        "a2a hand-off": agent_link.format_handoff("scout", "task", "h1", hint="use the fast model", lang=lang),
        "group message": agent_group.format_group_message("team", ["coder", "scout"], "coder", "go",
                                                          a2a=True, lang=lang),
    }


EN, ZH = texts("en"), texts("zh")
print("english versions")
for name, text in EN.items():
    hit = CJK.findall(text)
    check(f"{name}: no Chinese in the English version", not hit, "".join(hit[:12]))
print("chinese versions")
for name, text in ZH.items():
    if name == "reply command" or name == "fallback summary":
        continue
    check(f"{name}: the Chinese version is Chinese", bool(CJK.search(text)))

print("same commands and markers")
cmd = re.compile(r"sfctl [a-z0-9-]+(?: --[a-z]+)?")
mark = re.compile(r"\[\[SF:[A-Z]+")
for name in EN:
    for what, rx in (("sfctl commands", cmd), ("markers", mark)):
        a, b = sorted(set(rx.findall(EN[name]))), sorted(set(rx.findall(ZH[name])))
        check(f"{name}: same {what} in both", a == b, f"en={a} zh={b}")

print("messages")
check("name errors have an English version",
      not CJK.search(sf_lang.pick(bots.name_error(""), "x", "en") if sf_lang.prompt_lang() == "en" else "ok"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
check("validation messages follow the language", bots.name_error("") == "A name is required"
      and "A2A" in bots.name_error("a/b"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
check("…both ways", bots.name_error("") == "名稱必填")
os.environ.pop("SHELLFRAME_PROMPT_LANG", None)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
