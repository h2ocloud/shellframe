#!/usr/bin/env python3
"""Copying in the chat view, the way a chat app does: web/index.html in a browser with a stand-in
Python side.

Asked for in daily use: copy without the right-click menu, get clean text (no bullet boxes, code
not cut to pieces), a copy button on each message and code block; the highlight of a selection was
in pieces. Now:
  - ⌘C / Ctrl+C on a selection: a list item as "• text", inline code as its text, a code block as
    its lines, messages a blank line apart; no buttons, faces or labels
  - each message's button copies all of it (a long one too, while it shows only its start), through
    the app's copy_text; a code block's button copies its code
  - the bullets are not selectable; the input box keeps its own copy
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_copy.py
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
    print("SKIP  tests_chat_copy.py（沒裝 playwright）")
    sys.exit(0)

REPLY = ("接下來還在等你的：\n\n- 看完第一章和第二章，回我一行；\n- 新的設定名稱：`Demo Profile 2`；\n"
         "- 測試機的 `/login`；\n\n```bash\necho 一\necho 二\n```")
LONG = "長訊息：" + "這一段很長的說明，" * 200 + "最後一句。"
TURNS = [{"kind": "user_msg", "text": "還有哪些在等我？"}, {"kind": "assistant_text", "text": REPLY},
         {"kind": "user_msg", "text": "好"}, {"kind": "assistant_text", "text": LONG}]
for i, t in enumerate(TURNS):
    t["ts"] = 1794190800 + i * 30
CFG = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1", "turns": TURNS}
STUB = """
window.__copied = [];
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
      // as the real one: nothing new since `rev` is {unchanged} (a face's --t differs at each draw, so
      // a fresh copy each poll redrew the list, and the button's 「已複製」 with it)
      case 'chat_view': return R(args[2] === window.__view.rev ? {success: true, unchanged: true, rev: args[2]} : window.__view);
      case 'copy_text': window.__copied.push(args[0]); return Promise.resolve('ok');
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, VIEW))
# copy what is selected, the way ⌘C does, and read what went to the clipboard
COPY_SELECTION = """sel => {
  const s = getSelection(); s.removeAllRanges();
  const r = document.createRange();
  if (Array.isArray(sel)) { r.setStartBefore(document.querySelector(sel[0])); r.setEndAfter(document.querySelector(sel[1])); }
  else r.selectNodeContents(document.querySelector(sel));
  s.addRange(r);
  const dt = new DataTransfer();
  const ev = new ClipboardEvent('copy', {clipboardData: dt, bubbles: true, cancelable: true});
  document.querySelector('#chat-list').dispatchEvent(ev);
  return {text: dt.getData('text/plain'), handled: ev.defaultPrevented}; }"""
BOT = "#chat-list .cm-bot .cm-bubble"


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("document.querySelectorAll('#chat-list .cm-row').length >= 4", timeout=15000)
    page.wait_for_timeout(800)

    print(f"[{engine}] 1. ⌘C on a selection")
    got = page.evaluate(COPY_SELECTION, BOT)
    want = ("接下來還在等你的：\n• 看完第一章和第二章，回我一行；\n• 新的設定名稱：Demo Profile 2；\n"
            "• 測試機的 /login；\necho 一\necho 二")
    check("a message: list items as \"• text\", inline code as text, the code block's lines", got["text"] == want, got)
    check("…no button, no label in it", "複製" not in got["text"] and "bash" not in got["text"])
    got = page.evaluate(COPY_SELECTION, [["#chat-list .cm-user", "#chat-list .cm-bot"][0], "#chat-list .cm-bot"])
    check("two messages: a blank line between them, no face", got["text"].startswith("還有哪些在等我？\n\n接下來還在等你的：")
          and "<svg" not in got["text"], got["text"][:60])
    page.fill("#chat-input", "輸入框裡的字")
    got = page.evaluate("""() => { const i = document.getElementById('chat-input'); i.focus(); i.select();
      const dt = new DataTransfer(); const ev = new ClipboardEvent('copy', {clipboardData: dt, bubbles: true, cancelable: true});
      i.dispatchEvent(ev); return ev.defaultPrevented; }""")
    check("the input box keeps its own copy", got is False)
    check("the bullets are not selectable", page.evaluate(
        "getComputedStyle(document.querySelector('#chat-list .cm-mark')).webkitUserSelect || getComputedStyle(document.querySelector('#chat-list .cm-mark')).userSelect") == "none")

    print(f"[{engine}] 2. the buttons")
    bubble = page.locator(BOT).first
    btn = bubble.locator(":scope > .cm-copy")
    check("a message's copy button is out of sight", float(btn.evaluate("b => getComputedStyle(b).opacity")) == 0)
    bubble.hover()
    page.wait_for_timeout(250)
    check("…and shows on hover", float(btn.evaluate("b => getComputedStyle(b).opacity")) == 1)
    if engine == "webkit":
        page.screenshot(path=str(HERE / "qa-shots" / "chat-copy-buttons.png"), clip=bubble.bounding_box())
    btn.click()
    page.wait_for_timeout(200)
    check("it copies the whole message, as written", page.evaluate("window.__copied.slice(-1)[0]") == REPLY)
    check("…and says so", "已複製" in (btn.get_attribute("data-label") or ""))
    page.wait_for_timeout(1700)
    check("…then goes back to 「複製」", btn.get_attribute("data-label") == "複製")
    check("the button's word is not in the bubble's text", "複製" not in bubble.inner_text())
    code = page.locator(f"{BOT} .cm-code").first
    code.hover()
    page.wait_for_timeout(200)
    code.locator(".cm-copy").click()
    page.wait_for_timeout(200)
    check("a code block's button copies its code", page.evaluate("window.__copied.slice(-1)[0]") == "echo 一\necho 二")
    longb = page.locator(BOT).nth(1)
    check("a long message shows only its start", "最後一句" not in (longb.inner_text() or ""))
    longb.hover()
    longb.locator(":scope > .cm-copy").click()
    page.wait_for_timeout(200)
    check("…and its button copies all of it", page.evaluate("window.__copied.slice(-1)[0]") == LONG)
    check("the first line keeps its place (the button is not in the flow)",
          page.evaluate(f"document.querySelector('{BOT} > .cm-copy + *').getBoundingClientRect().top"
                        f" - document.querySelector('{BOT}').getBoundingClientRect().top") < 14)
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
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
