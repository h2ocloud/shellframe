#!/usr/bin/env python3
"""The app's own confirm and alert (web/dialog.js): web/index.html in a browser with a stand-in Python
side and the real xterm.

Reported on Windows (WebView2): the native confirm() and alert() are titled with the page's address,
"127.0.0.1:47342 says" (the 4060 smoke test, the rename question). The page now asks through its own
dialog, which returns a Promise.
  - confirm: Enter says yes, Esc no, the buttons and a click on the dim area do what they say; the OK
    button has the keyboard when it opens; Tab and Shift+Tab stay inside it; Enter on Cancel is no
  - alert: one button; Enter, Esc and the button close it; the Promise resolves
  - one at a time, in the order asked (a native one blocked, so this was free); a second waits
  - the message is text (not HTML), keeps its line breaks and wraps; a title and the buttons' words can be given;
    the words follow the page's language (OK / Cancel, 確定 / 取消)
  - the keyboard: back to where it was when it closes (the terminal, or a dialog it was asked from); the key
    that closed it does not reach the CLI; something taking the keyboard while it is open (the window
    regaining focus, a focus on the terminal) gives it back to the dialog
  - it is above every other dialog (asked from inside the rename box)
  - the call sites: deleting a bot from its editor asks through it (and cancelled, does nothing); a native
    dialog event is never raised
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Screenshots (WebKit): qa-shots/app-dialog-confirm-webkit.png, qa-shots/app-dialog-alert-webkit.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_app_dialogs.py
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
    print("SKIP  tests_app_dialogs.py（沒裝 playwright）")
    sys.exit(0)

SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "demo tab", "bridge_enabled": True}]
STUB = """
window.__calls = []; window.__r = [];
window.__cfg = %s; window.__sessions = %s;
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
      default: return R('{}');
    }
  };
}})};
"""
DIALOG = ".sf-dialog"
ACTIVE_IN_DIALOG = "!!(document.activeElement && document.activeElement.closest('.sf-dialog'))"
ACTIVE_LABEL = "(document.activeElement && document.activeElement.textContent) || ''"


def open_page(browser, lang="en"):
    cfg = {"settings": {"language": lang, "sidebar_open": True, "ui_style": "cli"},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.set_default_timeout(8000)                  # a dialog that never comes should fail the step, not stall the run
    errors, native = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))      # a native one: "127.0.0.1:port says"
    page.add_init_script(STUB % (json.dumps(cfg, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False)))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("window.__sfHost && Object.keys(window.__sfHost.sessions()).length > 0 && window.SFDialog", timeout=15000)
    page.wait_for_timeout(1200)
    return page, errors, native


def ask(page, kind, text, opts="undefined", tag="r"):
    """Ask the page's own dialog; the answer lands in window.__r as [tag, answer]."""
    # the braces matter: evaluate waits for a Promise it is handed, and this one is the dialog's answer
    page.evaluate("() => { SFDialog.%s(%s, %s).then(v => window.__r.push([%s, v === undefined ? 'done' : v])); }"
                  % (kind, json.dumps(text), opts, json.dumps(tag)))
    page.wait_for_selector(DIALOG)
    page.wait_for_timeout(80)


def answers(page):
    return page.evaluate("window.__r")


def writes(page):
    return page.evaluate("window.__calls.filter(c => c[0] === 'write_input').map(c => c[1])")


def gone(page):
    page.wait_for_function("document.querySelectorAll('.sf-dialog').length === 0", timeout=5000)
    page.wait_for_timeout(60)


def term_focused(page):
    return page.evaluate("!!(document.activeElement && document.activeElement.classList.contains('xterm-helper-textarea'))")


def run(browser, engine):
    tag = f"[{engine}]"

    print(f"{tag} 1. confirm")
    page, errors, native = open_page(browser)
    page.evaluate("window.__sfHost.sessions()['s501'].term.focus()")
    check("the terminal had the keyboard", term_focused(page))
    ask(page, "confirm", "Delete the demo bot?\nIts memory folder is kept.", "{title: 'Delete'}")
    check("it shows the title, the words and two buttons", "Delete" in page.inner_text(f"{DIALOG} h2")
          and "memory folder" in page.inner_text(f"{DIALOG} .sf-dialog-msg")
          and page.locator(f"{DIALOG} button").count() == 2, page.inner_text(DIALOG))
    check("the buttons say Cancel and OK", page.locator(f"{DIALOG} button").all_inner_texts() == ["Cancel", "OK"],
          page.locator(f"{DIALOG} button").all_inner_texts())
    check("the OK button has the keyboard", page.evaluate(ACTIVE_LABEL) == "OK", page.evaluate(ACTIVE_LABEL))
    if engine == "webkit":
        SHOTS.mkdir(exist_ok=True)
        page.locator(f"{DIALOG} .modal").screenshot(path=str(SHOTS / "app-dialog-confirm-webkit.png"))
    before = len(writes(page))
    page.keyboard.press("Enter")
    gone(page)
    check("Enter says yes", answers(page) == [["r", True]], answers(page))
    check("the key did not reach the CLI, and the terminal has the keyboard again",
          len(writes(page)) == before and term_focused(page), (writes(page)[before:], term_focused(page)))

    ask(page, "confirm", "Sure?", tag="esc")
    page.keyboard.press("Escape")
    gone(page)
    check("Esc says no, and does not reach the CLI", answers(page)[-1] == ["esc", False] and len(writes(page)) == before, answers(page))

    ask(page, "confirm", "Sure?", tag="cancel")
    page.click(f"{DIALOG} .btn-ghost")
    gone(page)
    ask(page, "confirm", "Sure?", tag="ok")
    page.click(f"{DIALOG} .btn-primary")
    gone(page)
    ask(page, "confirm", "Sure?", tag="dim")
    page.mouse.click(10, 10)                       # the dim area
    gone(page)
    check("Cancel is no, OK is yes, a click on the dim area is no",
          answers(page)[-3:] == [["cancel", False], ["ok", True], ["dim", False]], answers(page))

    print(f"{tag} 2. the keyboard inside it")
    ask(page, "confirm", "Sure?", tag="tab")
    page.keyboard.press("Tab")
    check("Tab goes to the other button", page.evaluate(ACTIVE_LABEL) == "Cancel", page.evaluate(ACTIVE_LABEL))
    page.keyboard.press("Tab")
    check("…and round again", page.evaluate(ACTIVE_LABEL) == "OK", page.evaluate(ACTIVE_LABEL))
    page.keyboard.press("Shift+Tab")
    check("Shift+Tab goes back", page.evaluate(ACTIVE_LABEL) == "Cancel", page.evaluate(ACTIVE_LABEL))
    page.keyboard.press("Enter")
    gone(page)
    check("Enter on Cancel is no", answers(page)[-1] == ["tab", False], answers(page))

    print(f"{tag} 3. alert")
    ask(page, "alert", "Could not send the file.", tag="al")
    check("one button, OK, with the keyboard", page.locator(f"{DIALOG} button").all_inner_texts() == ["OK"]
          and page.evaluate(ACTIVE_LABEL) == "OK", page.locator(f"{DIALOG} button").all_inner_texts())
    if engine == "webkit":
        page.locator(f"{DIALOG} .modal").screenshot(path=str(SHOTS / "app-dialog-alert-webkit.png"))
    page.keyboard.press("Enter")
    gone(page)
    check("Enter closes it and the Promise resolves", answers(page)[-1] == ["al", "done"], answers(page))
    ask(page, "alert", "Again", tag="al2")
    page.keyboard.press("Escape")
    gone(page)
    ask(page, "alert", "And again", tag="al3")
    page.click(f"{DIALOG} .btn-primary")
    gone(page)
    check("Esc and the button close it too", [a[0] for a in answers(page)[-2:]] == ["al2", "al3"], answers(page))

    print(f"{tag} 4. one at a time, in the order asked")
    page.evaluate("window.__r = []")
    page.evaluate("""() => {
      SFDialog.confirm('first?').then(v => window.__r.push(['first', v]));
      SFDialog.alert('second').then(() => window.__r.push(['second', 'done']));
      SFDialog.confirm('third?').then(v => window.__r.push(['third', v]));
    }""")
    page.wait_for_selector(DIALOG)
    page.wait_for_timeout(100)
    check("one is up, the first asked", page.locator(DIALOG).count() == 1 and "first?" in page.inner_text(DIALOG), page.inner_text(DIALOG))
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.sf-dialog .sf-dialog-msg') && document.querySelector('.sf-dialog .sf-dialog-msg').textContent === 'second'")
    check("then the second", page.locator(DIALOG).count() == 1)
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.sf-dialog .sf-dialog-msg') && document.querySelector('.sf-dialog .sf-dialog-msg').textContent === 'third?'")
    page.keyboard.press("Escape")
    gone(page)
    check("answered in the order asked", answers(page) == [["first", True], ["second", "done"], ["third", False]], answers(page))

    print(f"{tag} 5. the words are text, and keep their lines")
    ask(page, "alert", "<b>not bold</b> & <i>plain</i>\nsecond line\n" + "a-very-long-word-" * 12, tag="txt")
    msg = page.locator(f"{DIALOG} .sf-dialog-msg")
    check("tags are shown as they are, not drawn", "<b>not bold</b>" in msg.inner_text() and page.locator(f"{DIALOG} b").count() == 0)
    check("a line break stays one", msg.inner_text().split("\n")[1] == "second line", msg.inner_text())
    box = page.locator(f"{DIALOG} .modal").bounding_box()
    check("a long word wraps inside the box, which stays inside the window",
          msg.evaluate("e => e.scrollWidth <= e.clientWidth + 1") and box["x"] >= 0 and box["x"] + box["width"] <= 1100, box)
    page.keyboard.press("Enter")
    gone(page)
    ask(page, "confirm", "Sure?", "{ok: 'Yes, do it', cancel: 'Not now'}", tag="words")
    check("the caller's words for the buttons", page.locator(f"{DIALOG} button").all_inner_texts() == ["Not now", "Yes, do it"],
          page.locator(f"{DIALOG} button").all_inner_texts())
    page.keyboard.press("Enter")
    gone(page)

    print(f"{tag} 6. something takes the keyboard while it is up")
    ask(page, "confirm", "Sure?", tag="steal")
    page.evaluate("window.dispatchEvent(new Event('blur')); window.dispatchEvent(new Event('focus'))")
    page.wait_for_timeout(400)
    check("the window regaining focus leaves it in the dialog", page.evaluate(ACTIVE_IN_DIALOG), page.evaluate("document.activeElement.className"))
    page.evaluate("window.__sfHost.sessions()['s501'].term.focus()")
    page.wait_for_timeout(100)
    check("a focus on the terminal is given back to the dialog", page.evaluate(ACTIVE_IN_DIALOG))
    before = len(writes(page))
    page.keyboard.type("zz")
    page.keyboard.press("Escape")
    gone(page)
    check("what was typed went nowhere near the CLI", len(writes(page)) == before, writes(page)[before:])
    check("no page errors", not errors, errors)
    page.close()

    print(f"{tag} 7. above another dialog, and back to it")
    page, errors, native = open_page(browser)
    page.evaluate("window.__sfHost.renameSession('s501')")
    page.wait_for_selector("#rename-input")
    ask(page, "confirm", "Rename it?", tag="over")
    top = page.evaluate("""() => { const b = document.querySelector('.sf-dialog .modal').getBoundingClientRect();
      const e = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return !!(e && e.closest('.sf-dialog')); }""")
    check("it is over the rename box", top)
    page.keyboard.press("Escape")
    gone(page)
    check("answered no, and the rename box has its keyboard back",
          answers(page) == [["over", False]] and page.evaluate("document.activeElement && document.activeElement.id") == "rename-input",
          (answers(page), page.evaluate("document.activeElement && document.activeElement.id")))
    page.keyboard.press("Escape")
    check("no page errors", not errors, errors)
    page.close()

    print(f"{tag} 8. in Traditional Chinese")
    page, errors, native = open_page(browser, lang="zh-TW")
    ask(page, "confirm", "刪除這個 bot？", tag="zh")
    check("the buttons say 取消 and 確定", page.locator(f"{DIALOG} button").all_inner_texts() == ["取消", "確定"],
          page.locator(f"{DIALOG} button").all_inner_texts())
    page.keyboard.press("Enter")
    gone(page)
    check("no native dialog, no page errors", native == [] and not errors, (native, errors))
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
