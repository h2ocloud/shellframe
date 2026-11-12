#!/usr/bin/env python3
"""A path in a chat message: a picture shows as a thumbnail, a click shows it full size; a file opens in Finder.

Asked for: when a message gives a path, show the picture in the view and open it big on a click; a file
that is not a picture opens in Finder (Explorer on Windows).
  - an inline `code` that is a path is a link; a picture's path gets a thumbnail under it
  - a click on the thumbnail shows the picture over the page; Esc or a click closes it
  - a click on another file's path asks the app to show it in Finder; a missing one says so
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_paths.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
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
    print("SKIP  tests_chat_paths.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
import agent_link  # noqa: E402
TURNS = [{"kind": "user_msg", "ts": 1794190841, "text": "幫我整理這週的筆記"},
         {"kind": "assistant_text", "ts": 1794190843, "text": "截圖在 `/tmp/demo/shot.png`，計畫在 `~/notes/plan.md`，舊的 `/tmp/demo/gone.md` 已經刪了。"},
         {"kind": "user_msg", "ts": 1794190850, "text": agent_link.format_delivery("研究", "比較表整理好了", lang="zh"),
          "from": {"kind": "a2a", "name": "研究", "handoff": False}}]
STUB = """
window.__calls = []; window.__draft = true; window.__queue = {queued: [], given_up: []};
window.__cfg = %s; window.__sessions = %s; window.__turns = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R({success: true, readable: true, provider: 'claude', activity: 'working',
                                  decision: null, rev: 'r' + JSON.stringify(window.__queue), turns: window.__turns,
                                  queue: window.__queue});
      case 'chat_file_preview': window.__asked = (window.__asked || 0) + 1;
        return R(args[0].endsWith('.png') ? {success: true, kind: 'image', name: 'shot.png', data: window.__img}
                 : args[0].includes('gone') ? {success: false, code: 'missing'} : {success: true, kind: 'file', name: 'plan.md'});
      case 'chat_reveal': (window.__revealed = window.__revealed || []).push(args[0]);
        return R(args[0].includes('gone') ? {success: false, code: 'missing'} : {success: true});
      case 'chat_send': {
        window.__calls.push({text: args[1], clear: !!args[2]});
        if (!args[2] && window.__draft)               // the terminal still holds it
          return R({success: false, code: 'draft', draft: '終端機裡的草稿（右鍵誤貼上來的）'});
        if (args[2]) { window.__draft = false; window.__queue = {queued: ['q1'], given_up: []};
                       return R({success: true, queued: true, id: 'q1', reason: 'working', position: 1}); }
        return R({success: true, queued: false});
      }
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, TURNS))
SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"



import base64  # noqa: E402
IMG = "data:image/png;base64," + base64.b64encode((HERE / "qa-shots" / "chat-reply.png").read_bytes()).decode()


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.add_init_script(f"window.__img = {IMG!r};")
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1500)

    print(f"[{engine}] 1. the paths in a message")
    check("each path is a link", page.locator("#chat-list .cm-path").count() == 3, page.locator("#chat-list .cm-path").count())
    page.wait_for_selector("#chat-list .cm-thumb", timeout=5000)
    check("the picture's path has a thumbnail under it, the others none", page.locator("#chat-list .cm-thumb").count() == 1)
    if engine == "webkit":
        page.locator("#chat-layer").screenshot(path=str(HERE / "qa-shots" / "chat-path-thumb.png"))

    print(f"[{engine}] 2. the picture, full size")
    page.locator("#chat-list .cm-thumb").click()
    check("a click shows it over the page", page.locator(".cm-lightbox img").count() == 1)
    if engine == "webkit":
        page.screenshot(path=str(HERE / "qa-shots" / "chat-path-full.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    check("…Esc closes it", page.locator(".cm-lightbox").count() == 0)

    print(f"[{engine}] 3. a file")
    page.locator("#chat-list .cm-path", has_text="plan.md").click()
    page.wait_for_timeout(300)
    check("a click asks the app to show it in Finder", page.evaluate("window.__revealed") == ["~/notes/plan.md"],
          page.evaluate("window.__revealed"))
    page.locator("#chat-list .cm-path", has_text="gone.md").click()
    page.wait_for_timeout(300)
    check("…a file that is not there says so", "找不到這個檔案" in page.inner_text("#chat-layer"))
    check("each path was asked about once, however often the view was drawn", page.evaluate("window.__asked") == 1,
          page.evaluate("window.__asked"))
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:
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
