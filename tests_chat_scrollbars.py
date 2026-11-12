#!/usr/bin/env python3
"""The chat view's scrollbars are thin and dark, and only where there is more to see:
web/index.html in a browser with a stand-in Python side.

Reported in daily use: the input box had a thick light system scrollbar on the dark view. The
view now draws its own (::-webkit-scrollbar, which WebKit and WebView2 both honour) on the
input box, the message list and code blocks. Measured as the room a bar takes beside the
content: before, 15px in Chromium; now 8px in both engines, and none while nothing overflows.
Run in Chromium (bars shown: playwright hides them by default) and in WebKit when installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_scrollbars.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_chat_scrollbars.py（沒裝 playwright）")
    sys.exit(0)

TURNS = []
for i in range(14):
    TURNS += [{"kind": "user_msg", "ts": 1794190800 + i * 60, "text": f"第 {i + 1} 個問題"},
              {"kind": "assistant_text", "ts": 1794190830 + i * 60, "text": f"第 {i + 1} 個回答：好的，已經處理完了。"}]
CFG = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1", "turns": TURNS}
STUB = """
window.__cfg = %s; window.__sessions = %s; window.__view = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, VIEW))
# the room a vertical bar takes beside the content
BAR = """sel => { const el = document.querySelector(sel); const cs = getComputedStyle(el);
  return el.offsetWidth - el.clientWidth - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth); }"""


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1100, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("document.querySelectorAll('#chat-list .cm-row').length >= 20", timeout=15000)
    page.wait_for_timeout(800)
    print(f"[{engine}] 1. the input box")
    check("a short message: no bar", page.evaluate(BAR, "#chat-input") == 0, page.evaluate(BAR, "#chat-input"))
    page.fill("#chat-input", "\n".join(f"第 {i} 行草稿" for i in range(1, 16)))
    page.wait_for_timeout(200)
    w = page.evaluate(BAR, "#chat-input")
    check(f"a long one: a thin bar ({w}px, the system's was 15px)", 0 < w <= 8, w)
    if engine == "webkit":
        box = page.locator("#chat-input").bounding_box()
        page.screenshot(path=str(HERE / "qa-shots" / "chat-scrollbar-input.png"),
                        clip={"x": box["x"] - 12, "y": box["y"] - 12, "width": box["width"] + 140, "height": box["height"] + 24})
    print(f"[{engine}] 2. the message list")
    w = page.evaluate(BAR, "#chat-scroll")
    check(f"a long conversation: the same thin bar ({w}px)", 0 < w <= 8, w)
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = (pw.chromium.launch(ignore_default_args=["--hide-scrollbars"]) if engine == "chromium"
                       else pw.webkit.launch())
        except Exception as e:                       # webkit is optional: `playwright install webkit`
            print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
            continue
        try:
            run(browser, engine)
        finally:
            browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
