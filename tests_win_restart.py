#!/usr/bin/env python3
"""Windows 更新後必須有辦法重啟。

Windows 沒有 tmux，重啟是「照存檔的指令與名稱重建分頁」而不是 reattach，
所以會失去 scrollback。原本的處理是「只要還有 session 就拒絕重啟」——但
Windows 上永遠都有 session，等於更新下載得下來卻永遠套用不了，而對話框上
唯一的按鈕寫著「無法安全重啟」。

正確做法是**問過再做**：講清楚會失去什麼，讓使用者自己決定。

跑法：.venv/bin/python tests_win_restart.py
"""

import ast
import pathlib
import re
import sys

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main():
    here = pathlib.Path(__file__).parent
    src = (here / "main.py").read_text(encoding="utf-8")
    js = (here / "web" / "index.html").read_text(encoding="utf-8")

    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "restart_app"), None)
    check("restart_app takes a confirm flag",
          fn is not None and any(a.arg == "confirm" for a in fn.args.args))

    win = src[src.find("if IS_WIN:", src.find("def restart_app")):][:1200]
    check("Windows only warns when unconfirmed",
          "not confirm" in win and "needs_confirm" in win)
    check("the warning says what is lost",
          "scrollback" in win.lower())
    check("it reports how many tabs are affected", "session_count" in win)

    # The UI must offer a way forward, not a dead end.
    check("the dialog offers 'restart anyway'", "Restart anyway" in js or "仍要重啟" in js)
    check("confirming calls back with confirm=true", "restart_app(true)" in js)
    check("only one restart dialog at a time", "data-restart-modal" in js)

    # An explicit CLI/Telegram restart should not bounce back a prompt nobody
    # is present to answer -- that is the remote-maintenance path.
    check("sfctl/TG restart carries the confirmation",
          re.search(r'cmd == "restart".*?restart_app\(confirm=', src, re.S) is not None)

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
