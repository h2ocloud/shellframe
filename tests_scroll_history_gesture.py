#!/usr/bin/env python3
"""上滑歷史：取歷史的那段時間，使用者已經拉上去的量不能被吃掉。

回報：最後一個分頁拉上來又自己跳回去。歷史要向後端取（約半秒），那段時間的
滾輪事件被吞掉但沒累積；歷史蓋上去時一律從最底部開始——一次短手勢整個被打回
原形，持續拉的也會少掉開頭那一段。同時活畫面（Claude Code 的 alt screen，開著滑鼠
追蹤）自己已經因為滑鼠回報往上捲了，蓋上去的歷史卻在最底部，視覺上就是「跳回去」。

真的 ScrollHistory／setupScrollHistory／fetchHistory 從 web/index.html 抽出來，
餵真的 xterm.js 與真的 wheel 事件，不複製一份會走樣的副本。

需要 playwright（以及能連 cdn.jsdelivr.net）；沒有就 SKIP。

跑法：.venv/bin/python tests_scroll_history_gesture.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_SH_GESTURE_REEXEC") != "1":
        env = dict(os.environ, _SH_GESTURE_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_scroll_history_gesture.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")


def grab(pattern):
    m = re.search(pattern, html, re.S)
    assert m, f"index.html 找不到：{pattern}"
    return m.group(0)


SCROLL = grab(r"  const ScrollHistory = \(function\(\) \{.*?\n    return \{ show, close, isOpen \};\n  \}\)\(\);\n")
FETCH = grab(r"  async function fetchHistory\(sid, cols\) \{.*?\n  \}\n")
SETUP = grab(r"  function setupScrollHistory\(sid, pane\) \{.*?\n  \}\n")
THEME = grab(r"  const THEME = \{.*?\n  \};\n")

HIST = "\n".join(f"history line {i:03d}" for i in range(400))

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@xterm/addon-unicode11@0.8.0/lib/addon-unicode11.min.js"></script>
<style>body{margin:0;background:#16161e}#terminal-wrap{position:relative;width:900px;height:640px}
.term-pane{position:absolute;inset:0;overflow:hidden}</style>
<div id="terminal-wrap"><div class="term-pane" id="pane"></div></div>
<script>
const HIST = __HIST__;
window.__log = []; window.__FETCH_MS = 600;
const isRemoteSid = () => false, parseRemoteSid = () => null;
let activeId = 's1'; let currentFontSize = 13;
__THEME__
const pywebview = { api: {
  js_debug: (t, m) => window.__log.push([t, JSON.parse(m)]),
  get_clean_history: () => new Promise(r => setTimeout(() => r(JSON.stringify(
    { success: true, text: HIST, source: 'test', ansi: true })), window.__FETCH_MS)),
  write_input: () => {},
}};
const copySelection = async () => true;
const sessions = {};
const term = new Terminal({ cols: 97, rows: 40, fontSize: 13, theme: THEME, allowProposedApi: true });
term.open(document.getElementById('pane'));
term.write('\\x1b[?1049h' + 'live screen line\\r\\n'.repeat(30));   // alt screen：viewportY 永遠 0
sessions.s1 = { term, pane: document.getElementById('pane') };
__SCROLL__
__FETCH__
__SETUP__
setupScrollHistory('s1', document.getElementById('pane'));
window.up = () => {                       // overlay 離最底部幾行
  const vp = document.querySelector('.scroll-history-overlay .xterm-viewport');
  return vp ? Math.round((vp.scrollHeight - vp.clientHeight - vp.scrollTop) / 18) : null;
};
window.isOpen = () => !!document.querySelector('.scroll-history-overlay');
window.reset = () => { try { ScrollHistory.close(); } catch (_) {} window.__log.length = 0; };
window.ready = true;
</script>"""
SRC = (PAGE.replace("__HIST__", json.dumps(HIST)).replace("__THEME__", THEME)
       .replace("__SCROLL__", SCROLL).replace("__FETCH__", FETCH).replace("__SETUP__", SETUP))

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def gesture(pg, events, fetch_ms, gap=16, dy=-48):
    pg.evaluate("window.reset()")
    pg.evaluate(f"window.__FETCH_MS = {fetch_ms}")
    pg.mouse.move(450, 300)
    for _ in range(events):
        pg.mouse.wheel(0, dy)
        pg.wait_for_timeout(gap)
    pg.wait_for_timeout(fetch_ms + 600)
    return pg.evaluate("window.up()")


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1000, "height": 700})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.set_content(SRC)
    pg.wait_for_function("window.ready")

    # ── 1. 短手勢＋慢取回：不能被打回最底部 ──
    flick = gesture(pg, 3, 600)
    check("a short flick while the history loads is not thrown away (it used to land on the bottom)",
          flick is not None and flick >= 6, f"{flick} lines above the bottom")
    check("the pull is recorded in the debug log, never any text",
          any(t == "hist-open" and d.get("up", 0) >= 6 and d.get("ok") for t, d in pg.evaluate("window.__log")))

    # ── 2. 落點不取決於取回要多久 ──
    slow = gesture(pg, 75, 600)
    fast = gesture(pg, 75, 50)
    check("a sustained pull ends at the same place whether the history takes 600ms or 50ms",
          slow is not None and fast is not None and abs(slow - fast) <= 2, f"slow={slow} fast={fast}")

    # ── 3. 寫入期間（內容還沒就緒）的滾輪也要保留 ──
    pg.evaluate("window.reset()")
    pg.evaluate("""() => {
      ScrollHistory.show(HIST, 's1', { source: 'test', ansi: true });
      const host = document.querySelector('.scroll-history-overlay .xterm').parentElement;
      for (let i = 0; i < 5; i++) host.dispatchEvent(new WheelEvent('wheel', { deltaY: -48, bubbles: true, cancelable: true }));
    }""")
    pg.wait_for_timeout(800)
    early = pg.evaluate("window.up()")
    check("wheel events that arrive before the content is written are applied once it is",
          early is not None and early >= 12, f"{early} lines above the bottom")
    pg.evaluate("window.reset()")
    pg.evaluate("""() => {
      ScrollHistory.show(HIST, 's1', { source: 'test', ansi: true });
      const host = document.querySelector('.scroll-history-overlay .xterm').parentElement;
      for (let i = 0; i < 4; i++) host.dispatchEvent(new WheelEvent('wheel', { deltaY: 48, bubbles: true, cancelable: true }));
    }""")
    pg.wait_for_timeout(800)
    check("a downward wheel before the content is ready neither closes the overlay nor scrolls past the bottom",
          pg.evaluate("window.isOpen()") and pg.evaluate("window.up()") == 0)

    # ── 4. 原本的行為不變 ──
    pg.evaluate("window.reset()")
    pg.evaluate("ScrollHistory.show(HIST, 's1', { source: 'test', ansi: true })")
    pg.wait_for_timeout(800)
    check("an overlay opened without a pull starts at the bottom", pg.evaluate("window.up()") == 0)
    pg.mouse.move(450, 300)
    for _ in range(20):
        pg.mouse.wheel(0, -48)
        pg.wait_for_timeout(16)
    scrolled = pg.evaluate("window.up()")
    check("scrolling up inside the overlay still works", scrolled and scrolled > 30, str(scrolled))
    for _ in range(80):
        pg.mouse.wheel(0, 48)
        pg.wait_for_timeout(16)
    pg.wait_for_timeout(300)
    check("scrolling back down to the bottom still hands control back to the live view",
          not pg.evaluate("window.isOpen()"))
    check("no script errors", not errs, "; ".join(errs[:3]))
    b.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
