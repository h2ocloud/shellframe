#!/usr/bin/env python3
"""「請他離開」 in the office's follow panel, pressed the way a hand presses it: mousedown, a moment, mouseup, with
the office animating and its 3 s snapshots arriving (the real page: index.html + office.js host, a stand-in Python side).

The report: in the Mac app the first press did nothing at all, the label never changed. Cause (measured): the office
redraws the panel every frame and renderBye() wrote the button's text on every frame, even the same words. That swaps the
button's text node, and a press that straddles a frame (mousedown ... mouseup, 50-150 ms) has its click dropped: WebKit
(and Chromium, from about 150 ms) fires click on the common ancestor of the two press targets, and the first is gone.
The existing tests press with an instant click on a paused office, so they never saw it.

  - a press held 120 ms, the office running: the first one asks (label changes), nothing is closed
  - a snapshot arriving while it asks: still asking (the armed label is not reset), and a held second press closes the tab
  - a press held 3.4 s (a snapshot and some 200 frames inside it) still asks, for a paired computer's intern too
  - the label is written only when it differs (no frame replaces the button's text)
Demo data only. Headless Chromium, and WebKit (the macOS app's engine) when playwright has it.
Run: .venv/bin/python tests_office_dismiss_press.py
"""
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_src = (HERE / "tests_office_chat_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_chat_harness")
H.__file__ = str(HERE / "tests_office_chat_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)   # its stand-in Python side and helpers, not its run

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
    print("SKIP  tests_office_dismiss_press.py（沒裝 playwright）")
    sys.exit(0)


def run(browser, base, engine):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.add_init_script(H.stub())
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row #btn-office", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled() && window.SFChat", timeout=20000)
    page.wait_for_function("""['s701:sig-working','s702:sig-decision'].every(p => { const [sid, c] = p.split(':');
        const s = window.__sfHost.sessions()[sid]; return s && s._dotClass === c; })""", timeout=20000)
    page.click("#btn-office")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && Object.keys(f.contentWindow.__office.assignments()).length >= 3; } catch (e) { return false; } }""",
                           timeout=15000)
    fr = H.office(page)                                  # NOT paused: the office animates and renders the panel every frame
    fr.evaluate("""() => { window.__upd = 0; const o = Office.update; Office.update = function () { window.__upd++; return o.apply(this, arguments); };
        const b = document.getElementById('dismiss'); window.__bye = 0; new MutationObserver(m => { window.__bye += m.length; })
          .observe(b, {childList: true, characterData: true, subtree: true}); }""")
    label = lambda: fr.evaluate("document.getElementById('dismiss').innerText")  # noqa: E731

    def press(hold):
        box = fr.locator("#dismiss").bounding_box()
        fb = page.locator("#sfo-overlay iframe").bounding_box()
        page.mouse.move(fb["x"] + box["x"] + box["width"] / 2, fb["y"] + box["y"] + box["height"] / 2)
        page.mouse.down()
        page.wait_for_timeout(hold)
        page.mouse.up()
        page.wait_for_timeout(150)

    print(f"[{engine}] pressed with a hand's timing, the office running")
    fr.evaluate("__office.follow('t:s702')")
    page.wait_for_timeout(1200)
    check("an intern has 請他離開, and nothing has replaced its text so far", fr.evaluate("!document.getElementById('dismiss').hidden")
          and label() == "請他離開" and fr.evaluate("window.__bye") == 0, (label(), fr.evaluate("window.__bye")))
    press(120)
    check("the first press (held 120 ms) asks: the label changes, nothing is closed",
          "確定" in label() and page.evaluate("window.__closed") == [], (label(), page.evaluate("window.__closed")))
    n = fr.evaluate("window.__upd")
    fr.wait_for_function(f"window.__upd > {n}", timeout=6000)                      # a snapshot arrives while it asks
    check("a snapshot arriving meanwhile does not reset the question", "確定" in label(), label())
    press(120)
    page.wait_for_function("window.__closed.length === 1", timeout=5000)
    check("the second press (held) closes this computer's intern tab", page.evaluate("window.__closed") == ["s702"], page.evaluate("window.__closed"))

    print(f"[{engine}] a press that lasts across a snapshot")
    fr.evaluate("__office.follow('q:pid-demo-1/r30')")
    page.wait_for_timeout(800)
    fr.evaluate("window.__upd = 0")
    press(3400)
    check("held 3.4 s (a snapshot inside it), the first press still asks, for a paired computer's intern too",
          "確定" in label() and fr.evaluate("window.__upd") >= 1 and page.evaluate("window.__rclosed") == [],
          (label(), fr.evaluate("window.__upd"), page.evaluate("window.__rclosed")))
    fr.wait_for_function("document.getElementById('dismiss').innerText === '請他離開'", timeout=8000)
    check("left alone it is forgotten after about 4 s", True)
    check("no page error", not errors, errors[:3])
    page.close()


H.SHOTS.mkdir(exist_ok=True)
srv = H.serve()
base = f"http://127.0.0.1:{srv.server_port}"
with sync_playwright() as pw:
    try:
        for engine in ("chromium", "webkit"):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as e:                       # webkit is optional: `playwright install webkit`
                if engine == "chromium":
                    raise
                print(f"[{engine}] not installed, skipped: {str(e).splitlines()[0][:80]}")
                continue
            try:
                run(browser, base, engine)
            finally:
                browser.close()
    finally:
        srv.shutdown()

print(f"\nResults: {passed + H.passed} passed, {failed + H.failed} failed")
sys.exit(1 if failed or H.failed else 0)
