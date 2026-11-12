#!/usr/bin/env python3
"""The chat view in a narrow window: web/index.html in a browser with a stand-in Python side.

Measured on Windows: the window stops at 800px, and with the sidebar at its widest and the
interface size at Large the chat view has ~210px. Its header's title (the tab's name) was
down to its first letter beside the Terminal button, and the input box to a ~25px box
beside Send and the mic.
  - 800x800, the sidebar open at its widest, a Claude tab in the chat view: the title shows
    several characters (>= 60px) and does not run under the Terminal button; the input box
    is >= 120px wide and neither Send nor the mic covers it; nothing is wider than the view
  - …a window 16px narrower still
  - 1280 wide, the same tab: the layout it always had (Terminal with its word, Send on the
    input box's line) and the same holds
The sidebar's own limit (web/layout.js) keeps the view from being this narrow beside it now; the layout rules for
a narrow view still hold (the right panel open, a small window), so this measures them with that limit off.
Demo data only. Screenshots: qa-shots/chat-narrow-800.png and qa-shots/chat-narrow-1280.png
(qa-shots/chat-narrow-800-before.png is the 800 one before the fix). Needs playwright; SKIP without it.
Run: .venv/bin/python tests_chat_narrow.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
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
    print("SKIP  tests_chat_narrow.py（沒裝 playwright）")
    sys.exit(0)

TITLE = "demo 分頁｜窄視窗與中文標題"
CFG = {"settings": {"language": "en", "sidebar_open": True, "sidebar_width": 480, "uiScale": 1.18,
                    "ui_style": "chat", "stt_mic_button": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s301", "cmd": "claude", "alive": True, "label": TITLE, "bridge_enabled": True},
            {"sid": "s302", "cmd": "claude", "alive": True, "label": "查資料", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841,
                   "text": "中文字型測試：繁體中文、全形標點「引號」，數字１２３，這是一段比較長的句子，用來看窄視窗時會不會好好換行。"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "收到，週報在 `reports/weekly.md`，測試全部通過。"}]}
STUB = """
window.__cfg = %s; window.__sessions = %s; window.__view = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's301'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(VIEW, ensure_ascii=False))

# Boxes of the parts that collided, and whatever in the view reaches past its edges.
MEASURE = """() => {
  const box = s => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect();
    return {left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width}; };
  const layer = document.getElementById('chat-layer');
  const lb = layer.getBoundingClientRect();
  const out = [...layer.querySelectorAll('*')].filter(e => { const b = e.getBoundingClientRect();
    return b.width > 0 && (b.left < lb.left - .5 || b.right > lb.right + .5); }).map(e => e.id || e.getAttribute('class') || e.tagName);
  const wide = ['#chat-layer', '#chat-head', '#chat-scroll', '#chat-compose']
    .filter(s => { const e = document.querySelector(s); return e.scrollWidth > e.clientWidth + 1; });
  const label = document.querySelector('.cm-to-term span');
  return {layer: box('#chat-layer'), title: box('.cm-who b'), titleText: document.querySelector('.cm-who b').textContent,
          btn: box('.cm-to-term'), btnName: document.querySelector('.cm-to-term').title, labelShown: !!(label && label.offsetWidth),
          input: box('#chat-input'), send: box('#chat-send'), mic: box('#chat-mic'), out, wide};
}"""


def overlap(a, b):
    return a["left"] < b["right"] and b["left"] < a["right"] and a["top"] < b["bottom"] and b["top"] < a["bottom"]


def common(m):
    check(f"the title shows several characters: {m['title']['width']:.0f}px >= 60px", m["title"]["width"] >= 60, m["title"])
    check("…the tab's name, not under the Terminal button", m["titleText"] == TITLE and m["title"]["right"] <= m["btn"]["left"]
          and not overlap(m["title"], m["btn"]), (m["titleText"], m["title"], m["btn"]))
    check(f"the input box: {m['input']['width']:.0f}px >= 120px", m["input"]["width"] >= 120, m["input"])
    check("…neither Send nor the mic covers it, nor each other", not overlap(m["input"], m["send"])
          and not overlap(m["input"], m["mic"]) and not overlap(m["send"], m["mic"]), (m["input"], m["send"], m["mic"]))
    check("nothing is wider than the view", not m["out"] and not m["wide"], (m["out"][:8], m["wide"]))


SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 800, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    # The sidebar now shows no wider than the window allows (web/layout.js, tests_sidebar_limit.py), which keeps the
    # chat view from being this narrow on its own. The rules for a narrow view are still there (the right panel
    # open, a small window), so they are measured with that limit off: the sidebar at the width it was saved.
    page.add_style_tag(content="#sidebar { width: var(--sidebar-w, 220px) !important; }")
    page.wait_for_function("(() => { const l = document.getElementById('chat-layer'), b = document.querySelector('.cm-who b');"
                           " return l && !l.hidden && !document.getElementById('chat-mic').hidden && b && b.textContent"
                           " && document.querySelectorAll('#chat-list .cm-row').length >= 2; })()", timeout=15000)

    def measure(width, shot=None):
        page.set_viewport_size({"width": width, "height": 800})
        page.wait_for_timeout(150)
        if shot:                                   # before the checks, so a failing run still shows what it saw
            page.screenshot(path=str(SHOTS / shot))
        return page.evaluate(MEASURE)

    print("1. 800px, the sidebar at its widest")
    m = measure(800, "chat-narrow-800.png")
    check(f"the chat view is the narrow one: {m['layer']['width']:.0f}px beside the sidebar, the mic shown",
          190 <= m["layer"]["width"] <= 240 and m["mic"]["width"] > 0, (m["layer"], m["mic"]))
    common(m)
    check("the Terminal button still says what it does", bool(m["btnName"]), m["btnName"])

    print("2. 16px narrower still")
    m = measure(784)
    common(m)

    print("3. 1280px: as it always was")
    m = measure(1280, "chat-narrow-1280.png")
    common(m)
    check("Terminal keeps its word", m["labelShown"])
    check("Send stays on the input box's line, after it", m["send"]["left"] >= m["input"]["right"]
          and m["send"]["top"] < m["input"]["bottom"] and m["input"]["top"] < m["send"]["bottom"], (m["input"], m["send"]))
    check("no page errors", not errors, errors)
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
