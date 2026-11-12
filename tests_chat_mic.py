#!/usr/bin/env python3
"""The chat view's microphone, in the input row: web/index.html in a browser with a stand-in
Python side.

Asked for in daily use: the floating microphone was apart from the input box, and what it heard
was typed straight into the CLI. In the chat view it sits beside Send; the words go into the input
box to be read before sending; Esc throws a recording away. Same recording and speech-to-text as
the floating one (mic_record_start/stop), asked without a tab so nothing is typed into the CLI.
  - beside Send; the floating one hides while the view shows, and is back for the terminal
  - press, press again: the words land in the box at the cursor, nothing is sent
  - Esc while recording: discarded, the box untouched
  - the setting that hides the floating one hides this one too; a failure goes to the page's guide
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_mic.py
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
    print("SKIP  tests_chat_mic.py（沒裝 playwright）")
    sys.exit(0)

SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}
HEARD = "明天早上十點開會"


def stub(settings_extra):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat", **settings_extra},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    return """
window.__calls = []; window.__startReply = {ok: true};
window.__cfg = %s; window.__sessions = %s; window.__view = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      case 'mic_record_start': return R(window.__startReply);
      case 'mic_record_stop': return R(args[1] ? {ok: true, cancelled: true} : {ok: true, text: %s, injected: false});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(cfg, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(VIEW, ensure_ascii=False),
       json.dumps(HEARD, ensure_ascii=False))


SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
CALLS = "name => window.__calls.filter(c => c[0] === name).map(c => c[1])"
FAB = "getComputedStyle(document.getElementById('mic-fab')).display"


def open_page(browser, extra=None):
    page = browser.new_page(viewport={"width": 1100, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(extra or {}))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1000)
    return page, errors


def run(browser, engine):
    print(f"[{engine}] 1. where it is")
    page, errors = open_page(browser)
    mic, send = page.locator("#chat-mic"), page.locator("#chat-send")
    check("a microphone in the input row", mic.is_visible() and (mic.text_content() or "").strip() == "🎙")
    m, s = mic.bounding_box(), send.bounding_box()
    check("…right beside Send", m["x"] + m["width"] <= s["x"] + 1 and abs(m["y"] + m["height"] / 2 - (s["y"] + s["height"] / 2)) < 4, (m, s))
    check("the floating one hides while the view shows", page.evaluate(FAB) == "none", page.evaluate(FAB))
    if engine == "webkit":
        box = page.locator("#chat-compose").bounding_box()
        page.screenshot(path=str(HERE / "qa-shots" / "chat-mic-idle.png"), clip=box)

    print(f"[{engine}] 2. press, press again")
    page.fill("#chat-input", "請記下：")
    mic.click()
    page.wait_for_timeout(300)
    check("recording starts", len(page.evaluate(CALLS, "mic_record_start")) == 1)
    check("…and shows: red, a timer, how to stop", "rec" in (mic.get_attribute("class") or "")
          and (mic.text_content() or "").startswith("⏹") and "Esc" in page.inner_text("#chat-note"))
    if engine == "webkit":
        page.screenshot(path=str(HERE / "qa-shots" / "chat-mic-rec.png"), clip=page.locator("#chat-compose").bounding_box())
    mic.click()
    page.wait_for_timeout(400)
    stops = page.evaluate(CALLS, "mic_record_stop")
    check("stopped without a tab, so nothing is typed into the CLI", stops and stops[-1][0] == "", stops)
    check("the words are in the box, after what was there", page.input_value("#chat-input") == "請記下：" + HEARD,
          page.input_value("#chat-input"))
    check("…not sent", not page.evaluate(CALLS, "chat_send"))
    check("…and the box has the cursor", page.evaluate("document.activeElement && document.activeElement.id") == "chat-input")
    check("back to idle", (mic.text_content() or "").strip() == "🎙")

    print(f"[{engine}] 3. Esc while recording")
    page.fill("#chat-input", "")
    mic.click()
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    stops = page.evaluate(CALLS, "mic_record_stop")
    check("the recording is thrown away", stops and stops[-1][1:] == [True], stops[-1:])
    check("…the box untouched, the mic idle", page.input_value("#chat-input") == ""
          and (mic.text_content() or "").strip() == "🎙")

    print(f"[{engine}] 4. the terminal")
    page.evaluate("window.SFChat.setMode('s501', 'cli')")
    page.wait_for_timeout(300)
    check("the floating one is back for the terminal", page.evaluate(FAB) != "none", page.evaluate(FAB))

    print(f"[{engine}] 5. it cannot record")
    page.evaluate("window.SFChat.setMode('s501', 'chat')")
    page.wait_for_function(SHOWN)
    page.evaluate("window.__startReply = {ok: false, reason: 'no_mic_device'}")
    said = []
    page.on("dialog", lambda d: (said.append(d.message), d.dismiss()))
    mic.click()
    page.wait_for_timeout(500)
    check("the page's own guide says why", len(said) == 1, said)
    check("…and the mic is idle again", (mic.text_content() or "").strip() == "🎙")
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 6. the floating mic turned off in settings")
    page, errors = open_page(browser, {"stt_mic_button": False})
    check("this one is off too", not page.locator("#chat-mic").is_visible())
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
