#!/usr/bin/env python3
"""守住「改功能要順手改 skill」這件事。

docs/ai-skill.md 是 AI 唯一的操作說明書。它一旦落後於實際的 build，AI 會去用
不存在的指令，或根本不知道新功能存在 —— 而這種落後不會有任何錯誤訊息。所以這裡
把它變成會失敗的測試，而不是一條要人記得的規矩：

  - 版本表要涵蓋目前安裝的版本（bump 了卻沒寫 → 紅燈）
  - DEFAULT_CONFIG 裡每一個 experimental_* 旗標都要在文件裡出現過
  - sfctl 的每個 group-* / skill 動詞都要文件化
  - 裝進 ~/.claude/skills 的那份是「指標」不是「副本」，不能自己列指令清單

跑法：.venv/bin/python tests_ai_skill.py
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(ROOT))
import ai_skill  # noqa: E402

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def default_settings() -> dict:
    """Read DEFAULT_CONFIG out of main.py without importing it (main.py pulls in
    webview and the whole app)."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    m = re.search(r"^DEFAULT_CONFIG\s*=\s*\{", src, re.M)
    assert m, "DEFAULT_CONFIG not found in main.py"
    i, depth = m.end() - 1, 0
    for j in range(i, len(src)):
        depth += (src[j] == "{") - (src[j] == "}")
        if depth == 0:
            break
    block = src[i:j + 1]
    return {k: True for k in re.findall(r'"(experimental_[a-z0-9_]+)"\s*:', block)}


def main():
    doc = (ROOT / "docs" / "ai-skill.md").read_text(encoding="utf-8")
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
    minor = ".".join(version.split(".")[:2])

    # ── 版本表要跟得上 ──
    check(f"the version table covers the installed {minor}.x",
          re.search(r"\|\s*" + re.escape(minor) + r"\.", doc) is not None)
    check("the §0 example shows this build's version", minor in doc.split("## 1.")[0])

    # ── 每個實驗性旗標都要被提到 ──
    for flag in default_settings():
        check(f"{flag} is documented", flag in doc)

    # ── sfctl 動詞要文件化 ──
    sfctl = (ROOT / "sfctl.py").read_text(encoding="utf-8")
    verbs = set(re.findall(r'add_parser\(\s*"(group-[a-z]+|skill)"', sfctl))
    check("sfctl grew the verbs this doc promises",
          verbs >= {"skill", "group-list", "group-send", "group-conversation"})
    for verb in sorted(verbs):
        check(f"sfctl {verb} appears in the doc", f"sfctl {verb}" in doc)

    # ── 裝出去的那份必須是指標 ──
    text = ai_skill.claude_skill_text()
    check("installed skill has name + description frontmatter",
          text.startswith("---\nname: shellframe\ndescription: ") and text.count("---\n") >= 2)
    check("installed skill points at the live reference", "sfctl skill" in text)
    check("installed skill hard-codes no version", not re.search(r"\b0\.\d+\.\d+\b", text))
    check("installed skill is a pointer, not a command list",
          sum(text.count(v) for v in ("sfctl list", "sfctl send", "sfctl peek")) == 0)
    check("installed skill uses a path that resolves",
          Path(ai_skill.sfctl_path()).exists() or ai_skill.sfctl_path() == "sfctl")

    # ── 共用 AGENTS.md 只能動自己的區塊 ──
    base = "# 我的筆記\n\n不要動我\n"
    once = ai_skill.apply_marked_block(base, ai_skill._marked_block())
    twice = ai_skill.apply_marked_block(once, ai_skill._marked_block())
    check("installing twice leaves one block", once == twice and once.count(ai_skill.MARK_START) == 1)
    check("the user's own text survives", "不要動我" in once)
    check("removal puts the file back", ai_skill.remove_marked_block(once).rstrip() == base.rstrip())

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
