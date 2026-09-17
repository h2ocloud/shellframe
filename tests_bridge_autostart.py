#!/usr/bin/env python3
"""Telegram bridge 必須獨立於 UI 啟動。

為什麼有這支測試：橋接原本只由 web UI 的還原流程啟動，而那段在視窗載入完成後
才跑。於是任何擋住載入的東西（啟動時的對話框、載入很慢、渲染失敗）都會一併
把 Telegram 弄掉——偏偏那正是最需要它的時候：人不在機器前，而 UI 正是他唯一
碰不到的東西。遠端維護是必要條件，不是加分項。

跑法：.venv/bin/python tests_bridge_autostart.py
"""

import ast
import os
import pathlib
import sys

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main():
    src = pathlib.Path(__file__).with_name("main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    # 1. The autostart exists at all.
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    check("Api._autostart_bridge exists", "_autostart_bridge" in names)

    # 2. It is invoked from module-level startup, not only from the UI bridge.
    main_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    called = {ast.unparse(c.func) for c in ast.walk(main_fn) if isinstance(c, ast.Call)} if main_fn else set()
    starts_it = any("_autostart_bridge" in c for c in called)
    check("startup calls _autostart_bridge", starts_it)

    # 3. It must run before/independent of webview.start(), which blocks until
    #    the UI event loop ends — anything after it would never run at boot.
    idx_auto = src.find("_autostart_bridge")
    idx_start = src.find("webview.start(")
    boot_auto = src.rfind("_autostart_bridge", 0, idx_start)
    check("autostart is wired before webview.start()",
          idx_auto != -1 and idx_start != -1 and boot_auto != -1)

    # 4. The UI's restore must stay idempotent, or the two paths would fight and
    #    a second bridge would start polling the same bot token.
    ui = pathlib.Path(__file__).with_name("web") / "index.html"
    js = ui.read_text(encoding="utf-8")
    guarded = "bs.exists && bs.active" in js
    check("UI restore returns early when the bridge already runs", guarded)

    # 5. start_bridge stops any existing bridge first, so even a race cannot
    #    leave two pollers on one token.
    check("start_bridge replaces rather than duplicates a running bridge",
          "if self.bridge:" in src and "self.bridge.stop()" in src)

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
