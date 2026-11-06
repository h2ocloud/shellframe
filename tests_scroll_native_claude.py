#!/usr/bin/env python3
"""全螢幕的 Claude Code 分頁：往上滑交給 Claude Code 自己捲，不蓋重排版的 overlay。

回報：上滑看到的歷史「樣式差很多，不能跟原始終端機一樣嗎」，改了好幾版都沒對上。
根因不在樣式：Claude Code 的全螢幕模式（alt screen＋開 1003/1006 滑鼠追蹤）自己
就有完整的捲動歷史，滾輪的滑鼠回報本來就送進去了；ShellFrame 卻同時在上面蓋一份
從 transcript 重新排版的 overlay。兩套捲動同時動，overlay 再怎麼調也不是原畫面。

改法：這種分頁的滾輪交給 Claude Code（看到的就是它自己畫的畫面）；
按住 Option 或 Shift 再往上滑，才開重排版的 overlay（可選取複製）。
其他會開滑鼠追蹤的 TUI（opencode 等）照舊開 overlay——v0.31.2 為它們修的路不動。

真的 ScrollHistory／setupScrollHistory／fetchHistory 從 web/index.html 抽出來，
餵真的 xterm.js 5.5.0 與真的 wheel 事件。需要 playwright；沒有就 SKIP。

跑法：python3 tests_scroll_native_claude.py
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
    if os.environ.get("_SH_NATIVE_REEXEC") != "1":
        env = dict(os.environ, _SH_NATIVE_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_scroll_native_claude.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")


def grab(pattern):
    m = re.search(pattern, html, re.S)
    assert m, f"index.html 找不到：{pattern}"
    return m.group(0)


SCROLL = grab(r"  const ScrollHistory = \(function\(\) \{.*?\n    return \{ show, close, isOpen \};\n  \}\)\(\);\n")
FETCH = grab(r"  async function fetchHistory\(sid, cols\) \{.*?\n  \}\n")
SETUP = grab(r"  function setupScrollHistory\(sid, pane\) \{.*?\n  \}\n")
WHEEL = (grab(r"  function _appScrollsItself\(s\) \{.*?\n  \}\n")
         + grab(r"  function _xtermTakesWheel\(term, ev\) \{.*?\n  \}\n"))
ATTACH = "term.attachCustomWheelEventHandler(ev => _xtermTakesWheel(term, ev));"
THEME = grab(r"  const THEME = \{.*?\n  \};\n")

HIST = "\n".join(f"history line {i:03d}" for i in range(200))
FULLSCREEN = "\x1b[?1049h\x1b[?1003h\x1b[?1006h"   # Claude Code 全螢幕模式開的三個模式

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<style>body{margin:0;background:#16161e}#terminal-wrap{position:relative;width:900px;height:640px}
.term-pane{position:absolute;inset:0;overflow:hidden}</style>
<div id="terminal-wrap"></div>
<script>
const HIST = __HIST__;
window.__fetches = 0; window.__pty = [];
const isRemoteSid = () => false, parseRemoteSid = () => null;
let activeId = 's1'; let currentFontSize = 13;
__THEME__
const pywebview = { api: {
  js_debug: () => {},
  get_clean_history: () => { window.__fetches++; return Promise.resolve(JSON.stringify(
    { success: true, text: HIST, source: 'test', ansi: true })); },
  write_input: () => {},
}};
const copySelection = async () => true;
const sessions = {};
__SCROLL__
__FETCH__
__WHEEL__
__SETUP__
// 每個情境一個全新的 pane＋terminal（setupScrollHistory 會在 pane 上掛監聽）
window.mk = (cmd, seq) => new Promise(done => {
  try { ScrollHistory.close(); } catch (_) {}
  const wrap = document.getElementById('terminal-wrap');
  wrap.querySelectorAll('.term-pane').forEach(p => p.remove());
  const pane = document.createElement('div');
  pane.className = 'term-pane';
  wrap.appendChild(pane);
  const term = new Terminal({ cols: 97, rows: 40, fontSize: 13, theme: THEME });
  term.open(pane);
  __ATTACH__
  term.onData(d => window.__pty.push(d));
  term.onBinary(d => window.__pty.push(d));
  sessions.s1 = { term, pane, cmd };   // 先登記：_xtermTakesWheel 從 sessions 找分頁
  setupScrollHistory('s1', pane);
  window.__fetches = 0; window.__pty.length = 0;
  term.write(seq + 'live screen line\\r\\n'.repeat(30), done);
});
window.isOpen = () => !!document.querySelector('.scroll-history-overlay');
window.mode = () => sessions.s1.term.modes.mouseTrackingMode;
window.wheelReports = () => window.__pty.filter(d => d.startsWith('\\x1b[<6')).length;
window.ready = true;
</script>"""
SRC = (PAGE.replace("__HIST__", json.dumps(HIST)).replace("__THEME__", THEME)
       .replace("__SCROLL__", SCROLL).replace("__FETCH__", FETCH).replace("__SETUP__", SETUP)
       .replace("__WHEEL__", WHEEL).replace("__ATTACH__", ATTACH))

passed = failed = 0


def test_every_tab_uses_the_shared_wheel_rule():
    n = html.count("attachCustomWheelEventHandler(")
    check("every tab (new, restored, remote) asks _xtermTakesWheel who owns the wheel",
          n == 3 and html.count(ATTACH) == n, f"{html.count(ATTACH)}/{n}")


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def scenario(pg, cmd, seq, events=5, dy=-48, hold=None):
    pg.evaluate("([c, s]) => window.mk(c, s)", [cmd, seq])
    pg.mouse.move(450, 300)
    if hold:
        pg.keyboard.down(hold)
    for _ in range(events):
        pg.mouse.wheel(0, dy)
        pg.wait_for_timeout(16)
    if hold:
        pg.keyboard.up(hold)
    pg.wait_for_timeout(700)
    return {"open": pg.evaluate("window.isOpen()"), "fetches": pg.evaluate("window.__fetches"),
            "reports": pg.evaluate("window.wheelReports()")}


test_every_tab_uses_the_shared_wheel_rule()

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1000, "height": 700})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.set_content(SRC)
    pg.wait_for_function("window.ready")

    # ── 1. 全螢幕的 Claude Code：滾輪交給它 ──
    r = scenario(pg, "claude --resume 0b5e --model opus", FULLSCREEN)
    check("xterm 5.5.0 reports the mouse mode Claude Code turns on",
          pg.evaluate("window.mode()") == "any", pg.evaluate("window.mode()"))
    check("scrolling up in a full-screen Claude Code tab does not open the re-rendered history",
          not r["open"] and r["fetches"] == 0, str(r))
    check("the wheel reaches Claude Code as mouse reports, so it scrolls its own screen",
          r["reports"] >= 5, str(r))
    for cmd in ("/opt/homebrew/bin/claude --resume 0b5e",
                "env -u CLAUDE_CONFIG_DIR claude --resume 0b5e"):
        r = scenario(pg, cmd, FULLSCREEN)
        check(f"same for a launch command like `{cmd.split(' --')[0]}`",
              not r["open"] and r["fetches"] == 0, str(r))

    # ── 2. 要重排版的歷史（可選取複製）：Option 或 Shift ──
    r = scenario(pg, "claude", FULLSCREEN, hold="Alt")
    check("Option + scroll up still opens the re-rendered history", r["open"] and r["fetches"] == 1, str(r))
    check("...and Claude Code is not scrolled underneath it", r["reports"] == 0, str(r))
    pg.evaluate("window.mk('claude', %s)" % json.dumps(FULLSCREEN))
    pg.evaluate("""() => sessions.s1.pane.dispatchEvent(new WheelEvent('wheel',
        { deltaX: -48, deltaY: 0, shiftKey: true, bubbles: true, cancelable: true }))""")
    pg.wait_for_timeout(700)
    check("Shift + scroll up opens it too (macOS turns Shift + wheel into a sideways deltaX)",
          pg.evaluate("window.isOpen()"))

    # ── 3. 其他情況照舊 ──
    r = scenario(pg, "opencode", FULLSCREEN)
    check("opencode (mouse tracking, no history of its own we rely on) still gets the overlay",
          r["open"] and r["fetches"] == 1 and r["reports"] == 0, str(r))
    r = scenario(pg, "sf-codex --search", FULLSCREEN)
    check("other agents with mouse tracking still get the overlay", r["open"], str(r))
    r = scenario(pg, "claude", "\x1b[?1049h")
    check("Claude Code on the alternate screen without mouse tracking still gets the overlay",
          r["open"] and r["fetches"] == 1, str(r))
    r = scenario(pg, "claude", FULLSCREEN, dy=48)
    check("scrolling down never opens anything", not r["open"] and r["fetches"] == 0, str(r))
    check("no script errors", not errs, "; ".join(errs[:3]))
    b.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
