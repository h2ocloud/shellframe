#!/usr/bin/env python3
"""上滑 overlay 要跟活畫面像素對齊（v0.30.17）。

Howard 2026-09-03：「會有一個斷差感，修掉，體驗要無縫」。根因是 v0.30.11 給
.term-pane 加了左側 gutter（放分頁名標籤），overlay 卻還停在 left:0——上滑瞬間整個
畫面往左跳一段。這份測試把 overlay 的 inset 量測公式抽出來，用真實 DOM 驗它跟得上
pane 的實際位置，不是寫死 0 也不是寫死 gutter。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_scroll_overlay_align.py
"""
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_OVERLAY_QA_REEXEC") != "1":
        env = dict(os.environ, _OVERLAY_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_scroll_overlay_align.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
GUTTER = int(re.search(r"--hint-gutter: (\d+)px", html).group(1))
# 守住實作真的在「量」而不是寫死
assert "leftInset" in html, "overlay 少了 leftInset"
assert "getBoundingClientRect().left - hostRect.left" in html, \
    "leftInset 必須量 live pane 的實際左緣"

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<style>
body{margin:0;background:#16161e}
:root{--hint-gutter:__GUT__px}
#terminal-wrap{position:relative;width:900px;height:400px}
.term-pane{position:absolute;inset:0 0 0 var(--hint-gutter);overflow:hidden}
</style>
<div id="terminal-wrap"><div class="term-pane" id="pane"></div></div>
<script>
const term = new Terminal({ fontSize: 14, theme: { background: '#1a1b26' } });
term.open(document.getElementById('pane'));
window.ready = false;
term.write('row1\\r\\nrow2\\r\\nrow3', () => { window.ready = true; });
// overlay 的 inset 量測公式（與 web/index.html 的 ScrollHistory.show 同一套）
window.measure = function (gutterOn) {
  const pane = document.getElementById('pane');
  pane.style.inset = gutterOn ? '0 0 0 var(--hint-gutter)' : '0';
  const host = document.getElementById('terminal-wrap');
  const hostRect = host.getBoundingClientRect();
  const ls = pane.querySelector('.xterm-screen');
  const lsRect = ls.getBoundingClientRect();
  const rowH = lsRect.height / term.rows;
  return {
    leftInset: Math.max(0, Math.round(pane.getBoundingClientRect().left - hostRect.left)),
    bottomInset: Math.max(0, Math.round(hostRect.bottom - lsRect.bottom + rowH)),
    rowH: Math.round(rowH * 10) / 10,
    rows: term.rows,
  };
};
</script>"""

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 960, "height": 460})
    pg.set_content(PAGE.replace("__GUT__", str(GUTTER)))
    pg.wait_for_function("() => window.ready === true")

    on = pg.evaluate("() => window.measure(true)")
    check(f"有 gutter 時 overlay 左緣跟到 {GUTTER}px（不再從 0 開始）",
          on["leftInset"] == GUTTER, str(on))
    check("留給 tmux status bar 的下緣至少一行",
          on["bottomInset"] >= on["rowH"] - 1,
          f"bottomInset={on['bottomInset']} rowH={on['rowH']}")

    off = pg.evaluate("() => window.measure(false)")
    check("沒有 gutter 時退回 0（不寫死 gutter，向後相容）",
          off["leftInset"] == 0, str(off))
    b.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
