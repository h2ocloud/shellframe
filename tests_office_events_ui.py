"""The office's 「最近事件」 on a person's panel: an event that came again within the minute is one line with ×N.

Reported: a bot woken several times in a minute by its own background work showed three identical pairs of
「做完了／閒下來」 lines, which read as duplicated data. web/office.js track() now counts the same event within a
minute (tests_office_snapshot.js covers that); here office.html (demo host) is fed a snapshot whose events carry
`n`, and the panel must draw ×N on those lines and nothing on the others.
Demo data only. Headless Chromium, and WebKit when playwright has it.
Screenshot (Chromium): qa-shots/office-events-count.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_events_ui.py
"""
import functools
import http.server
import pathlib
import sys
import threading

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: playwright not installed")
    sys.exit(0)

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
        print(f"  [FAIL] {name}  {str(detail)[:300]}")


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    class Server(http.server.ThreadingHTTPServer):
        request_queue_size = 64   # the page loads many files at once; the default backlog of 5 reset connections under load
        daemon_threads = True
    srv = Server(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


FEED = """(() => {
  const db = __office.db(), b = db.bots[0], t0 = Date.now() - 50000;
  b.events = [{ t: t0, kind: 'arrive', text: '在辦公室看到他：在忙' },
              { t: t0 + 30000, kind: 'done', text: '做完了：示範：全套測試通過', n: 3 },
              { t: t0 + 40000, kind: 'idle', text: '閒下來', n: 3 }];
  Office.update(db);
  return 'b:' + b.id;
})()"""


def run(browser, base, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="zh-TW")
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.goto(base + "/office.html")
    page.wait_for_timeout(1000)
    page.evaluate("__office.pause(true)")
    key = page.evaluate(FEED)
    page.evaluate(f"__office.follow({key!r})")
    page.wait_for_function("__office.panelOpen()", timeout=5000)
    page.evaluate("(() => { const b = document.getElementById('tabInfo'); if (b && b.offsetParent) b.click(); })()")   # no conversation in the demo: the panel is its info already
    page.wait_for_selector("#pEvents li", timeout=5000)
    page.wait_for_timeout(300)
    rows = page.evaluate("[...document.querySelectorAll('#pEvents li')].map(li => ({ text: li.querySelector('span').innerText, n: (li.querySelector('.evn') || {}).textContent || '' }))")
    print(f"[{engine}] the panel's 最近事件")
    check("newest first: 閒下來 ×3, 做完了 ×3, then the first sight with no count",
          [r["n"] for r in rows[:3]] == ["×3", "×3", ""] and rows[0]["text"].startswith("閒下來") and rows[1]["text"].startswith("做完了：示範：全套測試通過"), rows)
    title = page.evaluate("(document.querySelector('#pEvents .evn') || {}).title || ''")
    check("the count says what it counts", title == "這一分鐘內 3 次", title)
    if shoot:
        SHOTS.mkdir(exist_ok=True)
        page.locator("#pEvents").scroll_into_view_if_needed()
        page.locator("#panel .events").screenshot(path=str(SHOTS / "office-events-count.png"))
    check("no console error from the page", not errors, errors[:5])
    page.close()


srv = serve()
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

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
