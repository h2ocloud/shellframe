#!/usr/bin/env python3
"""A page nobody is looking at does no work: web/index.html in a browser with a stand-in Python side,
35 tabs, bots on, the sidebar open.

Reported: the laptop would not charge with the app idle; the web process ~50% of a core and its GPU
process ~45%, ever since a restart. Measured with this setup (WebKit, the Mac app's engine): every
face ran three or four infinite animations on parts inside its SVG (244 in all), and one such
animation alone makes the engine redo style, layout and paint of the page every frame; the page
also asked Python ~20 times a second (one is_alive loop per tab).
  - nothing happening: no face moves (an idle or done face keeps still), and the page asks Python
    a few times a second, never is_alive
  - something happening: only the faces of tabs that work, ask or are stuck move, and only as a
    whole (the svg itself, which the compositor moves without repainting the page)
  - the window in the background, hidden, or untouched for a while: nothing moves and the polling
    drops to about one call in three seconds
  - back in front (or a touch): the faces move again and the chip and tab list are asked at once
Run in Chromium, and in WebKit when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_idle_power.py
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
    print("SKIP  tests_idle_power.py（沒裝 playwright）")
    sys.exit(0)

N = 35
DOT = {"working": "sig-working", "decision": "sig-decision", "done": "sig-done", "stuck": "sig-stuck", "idle": ""}
QUIET = {f"s{400 + i}": ("done" if i % 3 == 0 else "idle") for i in range(N)}
BUSY = dict(QUIET, s400="working", s401="working", s402="decision", s403="stuck")
ROSTER = {f"助手{i}": {"label": f"助手{i}", "cmd": "claude", "bot": {"avatar": {"shape": "pill", "color": "blue"}, "memory": True}}
          for i in range(0, N, 4)}
BOTS = {"enabled": True, "roster": ROSTER, "primary": "助手0", "memory_root": "~/.config/shellframe/bots",
        "sessions": {f"s{400 + i}": {"bot": f"助手{i}"} for i in range(0, N, 4)}, "kinds": {}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True, "ui_style": "cli"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": f"s{400 + i}", "cmd": "claude", "alive": True, "label": f"分頁 {i}", "bridge_enabled": True}
            for i in range(N)]


def status(states):
    return {sid: {"state": st, "dot": DOT[st], "summary": st, "activity": {"narration": "示範"}} for sid, st in states.items()}


STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, Date.now()]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's400'});
      case 'is_alive': return Promise.resolve(true);
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'get_bridge_status': return R({exists: true, active: true, paused: false, sessions: %d, active_sid: 's400'});
      case 'link_peer_bots': return R({peers: {}});
      default: return R('{}');
    }
  };
}})};
// the status monitor: a push now and then, as the Python side does on change and on a heartbeat
setInterval(() => { try { window.__sfAgentStatus && window.__sfAgentStatus(window.__status); } catch (e) {} }, 1000);
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False),
       json.dumps(BOTS, ensure_ascii=False), json.dumps(status(QUIET), ensure_ascii=False), N)

RUNNING = """() => document.getAnimations().filter(a => a.playState === 'running').map(a => {
  const t = a.effect && a.effect.target;
  return {name: a.animationName || '', root: !!(t && t.matches && t.matches('svg.sfb')),
          inSvg: !!(t && t.closest && t.closest('svg') && !(t.matches && t.matches('svg')))}; })"""
CALLS = "([t0, t1]) => { const n = {}; window.__calls.filter(c => c[1] >= t0 && c[1] < t1).forEach(c => n[c[0]] = (n[c[0]] || 0) + 1); return n; }"
BLUR = "() => { Object.defineProperty(document, 'hasFocus', {configurable: true, value: () => false}); window.dispatchEvent(new Event('blur')); }"
FOCUS = "() => { Object.defineProperty(document, 'hasFocus', {configurable: true, value: () => true}); window.dispatchEvent(new Event('focus')); }"
STILL = "document.body.classList.contains('sf-still')"


def rate(page, secs):
    t0 = page.evaluate("Date.now()")
    page.wait_for_timeout(secs * 1000)
    t1 = page.evaluate("Date.now()")
    n = page.evaluate(CALLS, [t0, t1])
    return n, sum(n.values()) / ((t1 - t0) / 1000)


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(f"document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] svg.sfb').length >= {N}",
                           timeout=30000)
    page.mouse.move(700, 450)
    page.wait_for_timeout(2500)

    print(f"[{engine}] 1. nothing happening")
    run0 = page.evaluate(RUNNING)
    check("no face moves: an idle or done face keeps still", not run0, run0[:5])
    n, per_s = rate(page, 4)
    check("no is_alive loop per tab (was one call per tab every 2 s)", "is_alive" not in n, n)
    check("a few calls to Python a second (was ~20 with 35 tabs)", per_s < 5, f"{per_s:.1f}/s {n}")

    print(f"[{engine}] 2. something happening")
    page.evaluate(f"window.__status = {json.dumps(status(BUSY), ensure_ascii=False)}")
    page.wait_for_function("document.querySelector('#sidebar-sessions [data-sid=\"s400\"] .busy-dot.sig-working')", timeout=8000)
    page.wait_for_timeout(600)
    run1 = page.evaluate(RUNNING)
    names = sorted({a["name"] for a in run1})
    check("the faces of the tabs at work move: working bobs, a question tilts, stuck shakes",
          {"sfb-bob", "sfb-tilt", "sfb-shake"} <= set(names), names)
    check("…only as a whole: nothing inside a face is animated", run1 and all(a["root"] and not a["inSvg"] for a in run1),
          [a for a in run1 if not a["root"]][:5])
    check("…and only those four tabs' faces (tab bar, sidebar, the gutter)", len(run1) <= 4 * 3, len(run1))

    print(f"[{engine}] 3. the window in the background")
    page.evaluate(BLUR)
    page.wait_for_function(STILL, timeout=4000)
    run2 = page.evaluate(RUNNING)
    check("nothing moves", not run2, run2[:5])
    n, per_s = rate(page, 6)
    check("about one call to Python in three seconds", per_s < 1.0, f"{per_s:.2f}/s {n}")

    print(f"[{engine}] 4. back in front")
    t0 = page.evaluate("Date.now()")
    page.evaluate(FOCUS)
    page.wait_for_function(f"!{STILL}", timeout=3000)
    page.wait_for_timeout(1700)
    n = page.evaluate(CALLS, [t0, page.evaluate("Date.now()")])
    check("the faces move again", len(page.evaluate(RUNNING)) > 0)
    check("the model chip and the tab list are asked at once", n.get("tab_model_info") and n.get("list_sessions"), n)

    print(f"[{engine}] 5. untouched for a while, then a touch")
    page.evaluate("window.SFQuiet.setIdleMs(800)")
    page.wait_for_function(STILL, timeout=5000)
    check("untouched: nothing moves", not page.evaluate(RUNNING))
    page.mouse.move(650, 400)
    check("the pointer moves: at once back to normal", page.evaluate(f"!{STILL}") and len(page.evaluate(RUNNING)) > 0)
    page.evaluate("window.SFQuiet.setIdleMs(60000)")

    print(f"[{engine}] 6. hidden")
    page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                             document.dispatchEvent(new Event('visibilitychange')); }""")
    check("hidden: nothing moves", page.evaluate(STILL) and not page.evaluate(RUNNING))
    check("no page errors", not errors, errors[:3])
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
