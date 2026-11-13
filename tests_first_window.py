#!/usr/bin/env python3
"""The first window after a new install offers a tab, even where the page has no localStorage.

Measured in a clean Ubuntu 24.04 container after install.sh: the window opened but stayed empty.
pywebview's private mode on WebKitGTK leaves `localStorage` undefined; init() read it for the
sidebar state (a fresh config has none), threw, and never restored tabs or opened the launcher.
web/storage_shim.js gives such a page an in-memory localStorage. Also measured there: a first
launch shows "What's new" instead of the launcher, and closing it left an empty window; with no
tab open, closing it now opens the launcher.

web/index.html in a browser with a stand-in Python side holding a fresh config and no tabs,
`localStorage` taken away as WebKitGTK does. Chromium, and WebKit when playwright has it.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_first_window.py
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
    print("SKIP  tests_first_window.py（沒裝 playwright）")
    sys.exit(0)

# A fresh config: no sidebar state, no release seen yet. Nothing else is needed to start.
CFG = {"settings": {"language": "en"}, "presets": [{"name": "Bash", "cmd": "bash", "icon": "$"}]}
STUB = """
// As in WebKitGTK private mode: no localStorage at all.
try { Object.defineProperty(window, 'localStorage', { value: undefined, configurable: true, writable: true }); } catch (e) {}
window.__calls = [];
window.__cfg = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push(prop);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R([]);
      case 'get_version': return R({version: '9.9.9'});
      case 'check_update': return R({update_available: false, local: '9.9.9'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      default: return R('{}');
    }
  };
}})};
"""


def run(browser, engine):
    tag = f"[{engine}]"
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.set_default_timeout(15000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB % json.dumps(CFG))
    page.goto((HERE / "web" / "index.html").as_uri())
    try:
        page.wait_for_function("window.__calls.includes('restore_tmux_sessions')", timeout=15000)
        started = True
    except Exception:
        started = False
    check(f"{tag} no localStorage: start-up goes on to restore tabs (init() does not stop)", started, errors[:3])
    if not started:
        page.close()
        return
    page.wait_for_selector("#modal-release.open")
    check(f"{tag} a first launch shows What's new", page.is_visible("#modal-release"))
    page.click("#btn-release-ok")
    page.wait_for_timeout(300)
    check(f"{tag} closing it with no tab open opens the tab launcher, not an empty window",
          page.evaluate("document.getElementById('modal-new').classList.contains('open')"))
    ls_errors = [e for e in errors if "localStorage" in e]
    check(f"{tag} no page error from a missing localStorage", not ls_errors, ls_errors[:3])
    page.close()


with sync_playwright() as p:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:
            if engine == "chromium":
                print(f"SKIP  tests_first_window.py（playwright 沒有 chromium：{str(e).splitlines()[0]}）")
                sys.exit(0)
            print(f"  [info] {engine} not installed for playwright; not run")
            continue
        try:
            run(browser, engine)
        finally:
            browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
