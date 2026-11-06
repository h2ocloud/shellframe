#!/usr/bin/env python3
"""往上滑：程式要了滑鼠，滾輪就是它的——看到的就是它自己畫的畫面。

回報：上滑看到的歷史「樣式差很多，不能跟原始終端機一樣嗎」，改了好幾版都沒對上；
接著「codex、grok build、agy、pi、opencode 都要好好支援，從本質上修復」。

實測各 agent（tmux 裡跑、看 pane 的 alternate_on／mouse_any_flag）：
  - Claude Code 全螢幕模式、grok、opencode：alt screen＋開 1003/1006 滑鼠追蹤，
    自己就有捲動歷史（opencode 收到滾輪回報會捲，grok 文件寫明支援滾輪/觸控板）。
  - codex、pi、agy、cursor-agent、Claude Code 一般模式：inline，不開滑鼠；捲出去的
    內容留在 tmux 的 scrollback，overlay 拿的就是那份原樣輸出（tmux -e）。
ShellFrame 以前在 alt screen 一律擋下滾輪、蓋上 transcript 重新排版的 overlay——
全螢幕那一類因此永遠看不到原畫面，opencode 甚至捲不動自己。

注意：tmux attach 自己會把外層 xterm 切進 alt buffer，所以每個 tmux 分頁的 xterm
都在 alt buffer；要不要滑鼠則是 tmux 照 pane 裡的程式轉過來的。下面用
`\x1b[?1049h` 模擬 tmux 的切換、再加上程式自己的滑鼠模式。

真的 ScrollHistory／setupScrollHistory／fetchHistory／_xtermTakesWheel 從
web/index.html 抽出來，餵真的 xterm.js 5.5.0 與真的 wheel 事件。需要 playwright。

跑法：python3 tests_scroll_native_tui.py
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
    print("SKIP  tests_scroll_native_tui.py（沒裝 playwright）\nALL PASS")
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
TMUX_ATTACH = "\x1b[?1049h"                       # tmux attach 自己切的 alt buffer
FULLSCREEN = TMUX_ATTACH + "\x1b[?1003h\x1b[?1006h"  # 加上程式要的滑鼠（實測三者皆同）

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
window.arrows = () => window.__pty.filter(d => /\\x1b[\\[O][AB]/.test(d)).length;
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
            "reports": pg.evaluate("window.wheelReports()"), "arrows": pg.evaluate("window.arrows()")}


test_every_tab_uses_the_shared_wheel_rule()

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1000, "height": 700})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.set_content(SRC)
    pg.wait_for_function("window.ready")

    # ── 1. 要了滑鼠的全螢幕 TUI：滾輪交給它 ──
    for cmd in ("claude --resume 0b5e --model opus", "env -u CLAUDE_CONFIG_DIR claude --resume 0b5e",
                "grok --permission-mode bypassPermissions", "opencode", "vim"):
        r = scenario(pg, cmd, FULLSCREEN)
        check(f"`{cmd.split(' --')[0]}` with the mouse on: scrolling up is the app's own scroll, no overlay",
              not r["open"] and r["fetches"] == 0 and r["reports"] >= 5, str(r))
    check("xterm 5.5.0 sees the mouse mode the app asked for", pg.evaluate("window.mode()") == "any",
          pg.evaluate("window.mode()"))

    # ── 2. 要重排版的歷史（可選取複製）：Option 或 Shift ──
    r = scenario(pg, "claude", FULLSCREEN, hold="Alt")
    check("Option + scroll up opens the re-rendered history", r["open"] and r["fetches"] == 1, str(r))
    check("...and the app is not scrolled underneath it", r["reports"] == 0, str(r))
    pg.evaluate("window.mk('opencode', %s)" % json.dumps(FULLSCREEN))
    pg.evaluate("""() => sessions.s1.pane.dispatchEvent(new WheelEvent('wheel',
        { deltaX: -48, deltaY: 0, shiftKey: true, bubbles: true, cancelable: true }))""")
    pg.wait_for_timeout(700)
    check("Shift + scroll up opens it too (macOS turns Shift + wheel into a sideways deltaX)",
          pg.evaluate("window.isOpen()"))

    # ── 3. inline 的 agent（不要滑鼠）：overlay＝tmux 原樣輸出，方向鍵不能漏進去 ──
    for cmd in ("sf-codex --search", "pi", "agy", "cursor-agent", "claude --model opus"):
        r = scenario(pg, cmd, TMUX_ATTACH)
        check(f"`{cmd.split(' ')[0]}` (inline, no mouse): scrolling up opens the history from tmux",
              r["open"] and r["fetches"] == 1, str(r))
        check(f"`{cmd.split(' ')[0]}`: the wheel never reaches it as arrow keys (that recalls old prompts)",
              r["arrows"] == 0 and r["reports"] == 0, str(r))
    r = scenario(pg, "claude", FULLSCREEN, dy=48)
    check("scrolling down never opens anything", not r["open"] and r["fetches"] == 0, str(r))
    check("no script errors", not errs, "; ".join(errs[:3]))
    b.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
