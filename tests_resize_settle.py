#!/usr/bin/env python3
"""A window being resized tells the tab's process its new size once, when it settles (web/index.html sendSizeSettled).

Reported on Windows: resizing the window redrew a Codex tab's whole conversation, again and again, and it was slow.
Codex reprints its transcript into the scrollback at every new size, and the page sent one size per animation
frame of the drag (fitTerm), with no tmux in between to soak them up. So:
  - a drag (ten sizes in quick succession) reaches the process as one resize, with the final size
  - the terminal on screen follows the window at once (its columns change during the drag)
  - a tab's first size still goes at once when it opens
  - one change, then another after the window has settled, is two resizes
Demo data only. Headless Chromium, and WebKit when playwright has it.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_resize_settle.py
"""
import functools
import http.server
import json
import pathlib
import sys
import threading

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
    print("SKIP  tests_resize_settle.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": True}, "presets": [{"name": "Codex", "cmd": "codex", "icon": "C"}],
       "agent_roster": {}, "guide": {"tour": "done", "seen": "9.9.9"}}
SESSIONS = [{"sid": "s900", "cmd": "codex", "alive": True, "label": "示範 Codex"}]


def stub():
    return """
if (window.top === window) {
window.__resizes = [];
window.__cfg = %s; window.__sessions = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's900'});
      case 'resize': window.__resizes.push({sid: args[0], cols: args[1], rows: args[2], t: performance.now()}); return R('');
      case 'read_output': case 'plugin_sidebar_badges': case 'get_changelog': return R('');
      case 'guide_data': return R({success: true, features: [], tours: {}, state: {tour: 'done', seen: '9.9.9'}});
      case 'preset_groups': return R([]);
      default: return R('{}');
    }
  };
}})};
}
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False))


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run(browser, base, engine):
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.add_init_script(stub())
    page.goto(base + "/index.html")
    page.wait_for_function("window.__sfHost && window.__sfHost.sessions().s900 && window.__sfHost.sessions().s900.term", timeout=20000)
    page.wait_for_timeout(800)
    first = page.evaluate("window.__resizes.filter(r => r.sid === 's900')")
    check(f"[{engine}] a tab's first size goes when it opens", len(first) >= 1 and first[0]["cols"] > 0, first)

    # When the terminal itself changed size, as the page saw it (term.onResize): the debounce is judged against
    # this, not against when the test moved the window. On a slow machine (CI) the page can see a drag late or in
    # fewer steps, and a step can last longer than the settle time; what must always hold is that nothing goes
    # out sooner than the settle time after the terminal last changed, and that the last one is the final size.
    page.evaluate("""(() => { window.__moves = []; const t = window.__sfHost.sessions().s900.term;
        t.onResize(() => window.__moves.push(performance.now())); window.__resizes.length = 0; })()""")
    cols_seen = set()
    for w in range(1180, 880, -30):                 # a drag: ten sizes, a frame or two apart
        page.set_viewport_size({"width": w, "height": 800})
        page.wait_for_timeout(35)
        cols_seen.add(page.evaluate("window.__sfHost.sessions().s900.term.cols"))
    during = page.evaluate("window.__resizes.length")
    final = lambda: page.evaluate("({cols: window.__sfHost.sessions().s900.term.cols, rows: window.__sfHost.sessions().s900.term.rows})")
    try:
        page.wait_for_function("""(() => { const t = window.__sfHost.sessions().s900.term, r = window.__resizes;
            return r.length && r[r.length - 1].cols === t.cols && r[r.length - 1].rows === t.rows; })()""", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(400)
    after, moves, fin = page.evaluate("window.__resizes"), page.evaluate("window.__moves"), final()
    fast = len(moves) >= 3 and max(b - a for a, b in zip(moves, moves[1:])) < 150
    early = [r for r in after if any(0 <= r["t"] - m < 180 for m in moves)]
    check(f"[{engine}] the terminal on screen follows the window during the drag", len(cols_seen) >= (3 if fast else 2), sorted(cols_seen))
    check(f"[{engine}] nothing goes out within the settle time of the terminal's last change", not early, (early, moves))
    if fast:
        check(f"[{engine}] nothing reaches the process while the window is still moving", during == 0, during)
    check(f"[{engine}] …and once it settles, the last resize has the final size" + (" (one resize)" if fast else ""),
          bool(after) and after[-1]["cols"] == fin["cols"] and after[-1]["rows"] == fin["rows"] and (not fast or len(after) == 1),
          (after, fin))

    page.evaluate("window.__resizes.length = 0")
    two = []
    for size in ({"width": 1100, "height": 800}, {"width": 1000, "height": 700}):
        before = page.evaluate("window.__sfHost.sessions().s900.term.cols")
        page.set_viewport_size(size)
        try:                                        # the terminal follows first (late on a slow machine)…
            page.wait_for_function(f"window.__sfHost.sessions().s900.term.cols !== {before}", timeout=8000)
        except Exception:
            pass
        try:                                        # …then, once the window is still, one resize
            page.wait_for_function("""(() => { const t = window.__sfHost.sessions().s900.term, r = window.__resizes;
                return r.length && r[r.length - 1].cols === t.cols && r[r.length - 1].rows === t.rows; })()""", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(300)
    two = page.evaluate("window.__resizes")
    check(f"[{engine}] two changes with a pause between are two resizes", len(two) == 2 and two[0]["cols"] != two[1]["cols"], two)
    check(f"[{engine}] no page error", not errors, errors[:3])
    page.close()


srv = serve()
base = f"http://127.0.0.1:{srv.server_port}"
with sync_playwright() as pw:
    try:
        for engine in ("chromium", "webkit"):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as e:
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
