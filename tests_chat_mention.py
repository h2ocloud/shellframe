#!/usr/bin/env python3
"""# in the chat view's input box names a bot or a tab, as the terminal's # does: web/index.html
in a browser with a stand-in Python side.

Asked for in daily use: in the terminal, # picks which conversation to call (Claude Code took @);
the chat view should have it too.
  - # opens the other tabs, then the bots asleep, then the external ones, with faces and states
  - letters filter (a bot's other names count), ↑↓ move, Enter or Tab put "#name " in, Esc closes;
    Enter on the list picks, it does not send; the next Enter sends the text as written
  - no list after a letter, a digit or a URL ("C#", "/#a"), in `code`, or for "# " (a heading);
    ＃ works as # and goes in as #
  - while the input method is choosing characters, its Enter is its own
  - bots off: the open tabs only
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_mention.py
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
    print("SKIP  tests_chat_mention.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {
    "助理": {"label": "助理", "cmd": "claude", "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}},
    "寫程式": {"label": "寫程式", "cmd": "codex", "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}},
    "研究": {"label": "研究", "cmd": "claude", "former_names": ["查資料"],
             "bot": {"avatar": {"shape": "hex", "color": "blue"}, "memory": True}},
    "總管": {"label": "總管", "cmd": "zsh", "bot": {"avatar": {"shape": "round", "color": "ink"}, "memory": True,
                                                 "external": {"kind": "webhook", "url": "", "auth": ""}}},
}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "助理", "bridge_enabled": True},
            {"sid": "s502", "cmd": "codex", "alive": True, "label": "寫程式", "bridge_enabled": True},
            {"sid": "s503", "cmd": "claude", "alive": True, "label": "週報草稿", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}


def stub(bots_on):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat", "experimental_bots": bots_on},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": ROSTER}
    bots = {"enabled": True, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
            "sessions": {"s501": {"bot": "助理"}, "s502": {"bot": "寫程式"}},
            "kinds": {"s501": "bot", "s502": "bot", "s503": "temp"}, "roster": ROSTER} if bots_on else {"enabled": False}
    return """
window.__sent = [];
window.__cfg = %s; window.__sessions = %s; window.__view = %s; window.__bots = %s;
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
      case 'bots_state': return R(window.__bots);
      case 'chat_send': window.__sent.push(args[1]); return R({success: true, queued: false});
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, SESSIONS, VIEW, bots))


SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
NAMES = "() => document.getElementById('chat-pick').hidden ? null : [...document.querySelectorAll('#chat-pick .cm-pick-item b')].map(b => b.textContent)"
SUBS = "() => [...document.querySelectorAll('#chat-pick .cm-pick-sub')].map(b => b.textContent)"


def open_page(browser, bots_on=True):
    page = browser.new_page(viewport={"width": 1000, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(bots_on))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1200)
    page.click("#chat-input")
    return page, errors


def typed(page, text):
    page.fill("#chat-input", "")
    page.keyboard.type(text)
    page.wait_for_timeout(150)
    return page.evaluate(NAMES)


def run(browser, engine):
    print(f"[{engine}] 1. the list")
    page, errors = open_page(browser)
    names = typed(page, "#")
    check("# lists the other tabs, then the bots asleep, then the external ones",
          names == ["寫程式", "週報草稿", "研究", "總管"], names)
    check("…with what each is", page.evaluate(SUBS) == ["醒著", "分頁", "休息中，會叫醒", "外部"], page.evaluate(SUBS))
    check("…and faces", page.evaluate("[...document.querySelectorAll('#chat-pick .cm-face')].every(f => f.querySelector('svg'))"))
    check("…over the input box, on its left edge", page.evaluate(
        "Math.abs(document.getElementById('chat-pick').getBoundingClientRect().left - document.getElementById('chat-input').getBoundingClientRect().left) < 1"
        " && document.getElementById('chat-pick').getBoundingClientRect().bottom <= document.getElementById('chat-input').getBoundingClientRect().top"))
    if engine == "webkit":
        page.screenshot(path=str(HERE / "qa-shots" / "chat-mention-picker.png"),
                        clip={"x": 0, "y": 360, "width": 1000, "height": 340})
    page.keyboard.type("研")
    page.wait_for_timeout(150)
    check("letters filter it", page.evaluate(NAMES) == ["研究"], page.evaluate(NAMES))
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    check("Enter puts \"#name \" in", page.input_value("#chat-input") == "#研究 ", page.input_value("#chat-input"))
    check("…closes the list, and sends nothing", page.evaluate(NAMES) is None and not page.evaluate("window.__sent"))
    page.keyboard.type("幫我查一下")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    check("the next Enter sends the text as written", page.evaluate("window.__sent") == ["#研究 幫我查一下"],
          page.evaluate("window.__sent"))
    check("a former name finds the bot", typed(page, "#查資") == ["研究"], page.evaluate(NAMES))

    print(f"[{engine}] 2. keys")
    typed(page, "#")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Tab")
    page.wait_for_timeout(150)
    check("↑↓ move, Tab puts it in", page.input_value("#chat-input") == "#週報草稿 ", page.input_value("#chat-input"))
    typed(page, "#")
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    check("Esc closes, the # stays, nothing sent", page.evaluate(NAMES) is None and page.input_value("#chat-input") == "#"
          and len(page.evaluate("window.__sent")) == 1)
    typed(page, "請 ")
    page.keyboard.insert_text("＃寫")
    page.wait_for_timeout(150)
    check("＃ opens it too", page.evaluate(NAMES) == ["寫程式"], page.evaluate(NAMES))
    page.keyboard.press("Enter")
    check("…and goes in as #", page.input_value("#chat-input") == "請 #寫程式 ", page.input_value("#chat-input"))
    typed(page, "#")
    page.locator("#chat-pick .cm-pick-item").nth(2).click()
    check("a click puts it in", page.input_value("#chat-input") == "#研究 ", page.input_value("#chat-input"))

    print(f"[{engine}] 3. a # that names no one")
    for text, why in (("C#", "after a letter"), ("http://example.com/#a", "in a URL"), ("看 `a #b", "in `code`"),
                      ("# ", "a heading"), ("第3#", "after a digit")):
        check(f"{why}: no list ({text!r})", typed(page, text) is None, page.evaluate(NAMES))

    print(f"[{engine}] 4. the input method")
    typed(page, "#")
    page.evaluate("""() => { const i = document.getElementById('chat-input');
      i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true, isComposing: true})); }""")
    page.wait_for_timeout(150)
    check("its Enter while choosing characters is its own: nothing picked, nothing sent",
          page.input_value("#chat-input") == "#" and page.evaluate(NAMES) is not None and len(page.evaluate("window.__sent")) == 1)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 5. bots off")
    page, errors = open_page(browser, bots_on=False)
    names = typed(page, "#")
    check("the open tabs only", names == ["寫程式", "週報草稿"], names)
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
