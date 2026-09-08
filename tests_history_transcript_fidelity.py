#!/usr/bin/env python3
"""上滑歷史（transcript 來源）要讀起來像活畫面（v0.35.11）。

Claude Code 2.1.261 起會進 alt-screen 並原地重繪，捲出去的內容留不住，所以
claude 分頁的上滑歷史是從 transcript JSONL 重繪的。回報的畫面差異有三處，都在
這個渲染器裡，而且都可以從實際的 transcript 量到：

  1. 工具行牆。渲染器本來就會把連續的 tool_call 收合成一行摘要，但 Claude 在
     每個 tool_use 旁邊會附一個**空的** text block（某分頁實測：13 個 tool_call
     之間夾了 9 個長度 0 的 assistant_text）。那些事件什麼都不畫，卻會沖掉待
     收合的工具行 → 每個工具各佔一行。活畫面在同一段只有一行摘要。
  2. 裸反引號。使用者訊息原本只做 harness 雜訊清理就直接輸出，沒有走 markdown
     渲染，於是 `sfctl reload` 帶著反引號出現；活畫面是上了色的 inline code。
  3. 詞中間被切斷。預設 skin 的 wrap 是關的，使用者訊息整行送進 overlay，由
     xterm 硬切（實測有 309 字元的一行）。活畫面是照詞斷行。

跑法：.venv/bin/python tests_history_transcript_fidelity.py
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
import agent_status  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def render(evs, ansi=True, cols=120):
    return Api._render_transcript_overlay(evs, ansi, cols=cols)


def plain(text):
    return ANSI.sub("", text)


def tool(name, target=""):
    return {"kind": "tool_call", "tool": name, "target": target}


RESULT = {"kind": "tool_result", "text": "ok"}
EMPTY_TEXT = {"kind": "assistant_text", "text": ""}

# ── 1. 空的 assistant_text 不能沖掉待收合的工具行 ──────────────────────────
# 事件形狀照實際量到的：tool_call / tool_result 之間夾空 text block
evs = [{"kind": "user_msg", "text": "排今天打卡"}]
for i in range(13):
    if i and i % 2:
        evs.append(dict(EMPTY_TEXT))
    evs += [tool("Bash", f"cmd-{i}"), dict(RESULT)]
evs.append({"kind": "assistant_text", "text": "打卡搞定"})

lines = [l for l in plain(render(evs)).split("\n") if l.strip()]
tool_lines = [l for l in lines if "⏺" in l]
check("13 個工具呼叫收成一行摘要（不是 13 行）",
      len(tool_lines) == 1, f"{len(tool_lines)} 行：{tool_lines}")
check("摘要有帶次數", "×13" in tool_lines[0], tool_lines[0])
check("真的有內容的 assistant_text 照樣畫出來",
      any("打卡搞定" in l for l in lines), str(lines))

# ── 2. 有內容的 assistant_text 仍然要把工具行沖出來（保序）──────────────
evs = [tool("Read", "a.py"), dict(RESULT),
       {"kind": "assistant_text", "text": "先看檔案"},
       tool("Edit", "a.py"), dict(RESULT),
       {"kind": "assistant_text", "text": "改好了"}]
lines = [l for l in plain(render(evs)).split("\n") if l.strip()]
check("敘述文字把前面的工具行沖出來，順序不亂",
      lines.index("先看檔案") > 0
      and any("Read" in l for l in lines[:lines.index("先看檔案")]),
      str(lines))
check("後面的工具行也有畫出來", any("Edit" in l for l in lines), str(lines))

# ── 3. 使用者訊息要照 pane 寬度斷行 ────────────────────────────────────────
check("預設 skin 開了 wrap", Api._SKIN_DEFAULT.get("wrap") is True)
long_user = ("You can self-modify shellframe at `~/.local/apps/shellframe/` when "
             "asked. Apply changes with reload or restart, and bump `version.json` "
             "plus CHANGELOG.md for anything user-visible, no tables because the "
             "mobile client cannot render them, and no ASCII-art dividers either.")
out = render([{"kind": "user_msg", "text": long_user}], cols=120)
widths = [len(l) for l in plain(out).split("\n")]
check("沒有任何一行超過 cols（否則 xterm 會在詞中間硬切）",
      max(widths) <= 120, f"最長 {max(widths)}")
check("是照詞斷行，不是照字元切",
      all(not l.rstrip().endswith(("wor", "aske", "restar"))
          for l in plain(out).split("\n")), plain(out))
check("cols=0（不知道寬度）時不強行斷行",
      max(len(l) for l in plain(
          render([{"kind": "user_msg", "text": long_user}], cols=0)).split("\n")) > 120)

# ── 4. 使用者訊息的 inline code 要跟活畫面一樣上色 ────────────────────────
out = render([{"kind": "user_msg", "text": "用 `sfctl reload` 熱載入"}], ansi=True)
check("使用者訊息沒有裸反引號", "`" not in plain(out), plain(out))
check("inline code 有上色（跟活畫面同一個顏色）",
      Api._SKIN_DEFAULT["code"] in out, repr(out))
out_plain = render([{"kind": "user_msg", "text": "用 `sfctl reload` 熱載入"}], ansi=False)
# 純文字模式刻意保留 markdown 來源——沒有樣式可以表達格式時，反引號本身就是
# 唯一的訊息。要驗的是「不要留下半截 escape」與「內容沒被吃掉」。
check("ansi=False 不留任何 escape", "\x1b[" not in out_plain, repr(out_plain))
check("ansi=False 保留 markdown 來源", "`sfctl reload`" in out_plain, repr(out_plain))

# ── 5. 使用者訊息的第一行仍然有 ❯ 標記、續行對齊 ──────────────────────────
out = render([{"kind": "user_msg", "text": long_user}], ansi=False, cols=100)
rows = [l for l in out.split("\n") if l.strip()]
check("第一行有使用者標記", rows[0].startswith("❯ "), repr(rows[0]))
check("續行縮排對齊（不是頂到最左）",
      len(rows) > 1 and rows[1].startswith("  "), repr(rows[1:2]))

# ── 6. codex 分支的 lsof 守門要用帳號感知的根目錄 ─────────────────────────
hist = (HERE / "api_history.py").read_text(encoding="utf-8")
guard = hist.split('if kind == "codex":')[1].split("return None")[0]
check("codex 守門用 codex_sessions_root（0.35.8 漏掉的呼叫點）",
      "codex_sessions_root(worker)" in guard, guard)
check("不再是寫死的 /.codex/sessions/ 子字串",
      '"/.codex/sessions/"' not in guard, guard)
check("transcript 來源走共用的 _worker_ctx",
      "self._worker_ctx(sid, s)" in hist)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
