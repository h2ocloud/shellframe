#!/usr/bin/env python3
"""Links out of the app open in the system browser (open_url), not through window.open.

Reported: About → 「Report Issue」 did nothing. The app's web view (WKWebView on macOS, WebView2 on Windows) does
nothing with window.open or target=_blank; terminal links already went through open_url for that reason, the About
window did not. Now Report Issue and every http(s) link in the page call open_url. The new-issue page also opens with
a reminder that it is public (no passwords, keys, internal addresses or customer names).
  - Report Issue → open_url(github issues/new?…) with the version and the public-page reminder; no window.open
  - the About window's links → open_url, and the page does not navigate
  - open_url itself (api_desktop): macOS `open`, Windows os.startfile, Linux xdg-open; anything not http(s) refused
web/index.html with a stand-in Python side (tests_settings_ui.open_page). Chromium and WebKit.
Run: .venv/bin/python tests_external_links_ui.py
"""
import json
import pathlib
import sys
import urllib.parse
from unittest import mock

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
        print(f"  [FAIL] {name}  {str(detail)[:300]}")


# ── open_url (the Python side) on each system, without opening anything ──────
import os  # noqa: E402
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import main  # noqa: E402
import api_desktop  # noqa: E402


class D(api_desktop.DesktopApiMixin):
    pass


d = D()
print("open_url")
with mock.patch.object(main, "IS_WIN", True), mock.patch.object(api_desktop.os, "startfile", create=True) as sf:
    r = json.loads(d.open_url("https://example.test/a"))
    check("Windows: os.startfile(url)", r["success"] and sf.call_args == mock.call("https://example.test/a"), (r, sf.call_args))
with mock.patch.object(main, "IS_WIN", False), mock.patch.object(api_desktop.platform, "system", return_value="Darwin"), \
        mock.patch.object(api_desktop.subprocess, "Popen") as po:
    r = json.loads(d.open_url("https://example.test/b"))
    check("macOS: /usr/bin/open url", r["success"] and po.call_args[0][0] == ["/usr/bin/open", "https://example.test/b"], po.call_args)
with mock.patch.object(main, "IS_WIN", False), mock.patch.object(api_desktop.platform, "system", return_value="Linux"), \
        mock.patch.object(api_desktop.subprocess, "Popen") as po:
    r = json.loads(d.open_url("https://example.test/c"))
    check("Linux: xdg-open url", r["success"] and po.call_args[0][0] == ["xdg-open", "https://example.test/c"], po.call_args)
with mock.patch.object(api_desktop.subprocess, "Popen") as po:
    r = json.loads(d.open_url("file:///etc/passwd"))
    check("not http(s): refused, nothing started", not r["success"] and not po.called, r)

# ── the page ─────────────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  the page part (no playwright)")
    sync_playwright = None

SPY = "window.__opened = []; window.open = (...a) => { window.__opened.push(a); return null; }; 0"   # the trailing 0: evaluate would call a function it is handed back
URLS = "window.__calls.filter(c => c[0] === 'open_url').map(c => c[1][0])"

if sync_playwright:
    import tests_settings_ui as S
    with sync_playwright() as pw:
        for engine in ("chromium", "webkit"):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as e:
                print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
                continue
            try:
                print(f"[{engine}] the About window")
                page, errors = S.open_page(browser, "zh-TW", {})
                page.wait_for_timeout(800)
                page.evaluate(SPY)
                page.click("#btn-about")
                page.wait_for_selector("#modal-about.open", timeout=5000)
                page.click("#btn-report-issue")
                page.wait_for_timeout(300)
                urls = page.evaluate(URLS)
                u = urls[-1] if urls else ""
                body = urllib.parse.unquote(u.split("body=", 1)[-1]) if "body=" in u else ""
                check("Report Issue → open_url(github issues/new…), not window.open",
                      u.startswith("https://github.com/h2ocloud/shellbot/issues/new?") and not page.evaluate("window.__opened.length"), (urls, page.evaluate("window.__opened")))
                check("…the issue text says the page is public: no passwords, keys, internal addresses, customer names (both languages)",
                      "This page is public" in body and "customer names" in body and "這是公開頁面" in body and "客戶名" in body and "**Version:**" in body, body[:300])
                n0 = len(urls)
                href = page.evaluate("document.querySelector('#modal-about a[href^=\"https://github.com/h2ocloud/shellbot\"]').href")
                page.click('#modal-about a[href^="https://github.com/h2ocloud/shellbot"]')
                page.wait_for_timeout(300)
                urls = page.evaluate(URLS)
                check("an About link → open_url(its address); no window.open, the page stays",
                      len(urls) == n0 + 1 and urls[-1] == href and not page.evaluate("window.__opened.length") and page.url.startswith("file:"), (urls, page.url))
                check("no page errors", not errors, errors)
                page.close()
            finally:
                browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
