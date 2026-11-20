#!/usr/bin/env python3
"""Closing the follow panel keeps the camera where it is (v0.62.0 follow-up, reported with a screenshot).

Click a person in the office: the camera zooms in and follows them, the panel opens on the right (Chat / Info). Before this change the
only ways out were Esc and the "Stop following" button, and both flew the camera back to the whole floor, so picking a neighbour
meant zooming in again. Now the panel has its own close button (x), and every way of closing it only closes the panel and stops
following: the camera stays at the zoom and place it had. "Whole floor" (bottom right) is still the way back to the full view.
The page is web/office.html on its own (demo host), demo data, Chromium and WebKit, zh-TW and en-US, 1280 x 800.

  a. click a person: following, panel open, camera zoomed in (zoom well above the whole-floor zoom); the close button is in the panel
     header, with the title "收起（Esc）" / "Close (Esc)" and aria-label "收起" / "Close"; the footer button still says "停止追蹤 Esc" /
     "Stop following Esc" (wording unchanged)
  b. close with the x, with "Stop following", and with Esc (one run each): the panel closes, nobody is followed, the camera is at the
     same place and zoom as before closing (tolerance: 1.5 world px on x / y, 0.01 on zoom, measured after 0.8 s), and it still is
     2 s later (nothing drifts back to the whole floor)
  c. following A, click B (a different person beside them): the panel is B's, the camera pans over to B and the zoom never drops
     below 90 % of the zoom it had (sampled every 40 ms while it moves; it never went through the whole floor)
  d. a click on bare floor (no person, no furniture) does nothing: the panel stays open, still following (behaviour kept as it was)
  e. "Whole floor" (the toolbar is hidden under the panel while following, so: close the panel, then click it) still returns to the
     whole floor: zoom back below half of the kept zoom
Screenshots (Chromium, zh-TW): qa-shots/office-follow-panel.png (panel with the x), qa-shots/office-follow-closed-kept-zoom.png (after closing).
Run: .venv/bin/python tests_office_follow_ui.py
"""
import functools
import http.server
import pathlib
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
passed = failed = 0
W, H = 1280, 800


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:500]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_office_follow_ui.py（沒裝 playwright）")
    sys.exit(0)


def settle(page, ms=8000):
    """Wait until the camera has stopped: two readings 200 ms apart are the same (while following, busyWhy().cam alone can be false early)."""
    page.wait_for_function("() => { const w = __office.busyWhy(); return !w.cam; }", timeout=ms)
    last = None
    for _ in range(int(ms / 200)):
        c = page.evaluate("__office.cam()")
        if last and abs(c["x"] - last["x"]) < 0.05 and abs(c["y"] - last["y"]) < 0.05 and abs(c["z"] - last["z"]) < 0.0005:
            return
        last = c
        page.wait_for_timeout(200)


def open_office(browser, base, locale, errors):
    ctx = browser.new_context(viewport={"width": W, "height": H}, locale=locale)
    ctx.add_init_script("localStorage.setItem('sbOffice.v2.legendMin', 'true');")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.goto(base + "/office.html")
    page.wait_for_function("window.__office && window.Office", timeout=15000)
    page.wait_for_timeout(800)
    page.evaluate("__office.pause(true)")
    page.wait_for_function("() => __office.walking() === 0", timeout=15000)
    page.evaluate("__office.setArea('all', true)")
    settle(page)
    page.mouse.move(2, 790)
    return ctx, page


def bots(page):
    """Visible bots as [{key, name, x, y}], on screen and clear of the panel (right 420 px) and the top bar."""
    return page.evaluate("""() => __office.people().filter(p => p.kind === 'bot' && !p.gone && !p.leaving).map(p => ({ key: p.key, name: p.name, ...(__office.entScreen(p.key) || { x: -1, y: -1 }) }))
        .filter(p => p.x > 60 && p.x < 1280 - 440 && p.y > 110 && p.y < 800 - 90)""")


def cam(page):
    return page.evaluate("__office.cam()")


def same_cam(a, b, tol_xy=1.5, tol_z=0.01):
    return abs(a["x"] - b["x"]) <= tol_xy and abs(a["y"] - b["y"]) <= tol_xy and abs(a["z"] - b["z"]) <= tol_z


def follow_by_click(page, key):
    s = page.evaluate("k => __office.entScreen(k)", key)
    page.mouse.click(s["x"], s["y"])
    page.wait_for_function("k => __office.following() === k", arg=key, timeout=5000)
    settle(page)


def pick_first(page):
    """A person who is easy to click at the whole-floor zoom: on screen, and the nearest to the middle of the screen."""
    bs = sorted(bots(page), key=lambda b: (b["x"] - 500) ** 2 + (b["y"] - 400) ** 2)
    return bs[0]


def run(browser, base, engine, errors):
    shoot = engine == "chromium"
    for locale, zh in (("zh-TW", True), ("en-US", False)):
        tag = f"{engine} {locale}"
        ctx, page = open_office(browser, base, locale, errors)
        print(f"[{tag}] follow, then close three ways")
        whole = cam(page)
        a = pick_first(page)
        follow_by_click(page, a["key"])
        zoomed = cam(page)
        check(f"{tag}: click a person: following, panel open", page.evaluate("__office.following()") == a["key"] and page.evaluate("__office.panelOpen()"))
        check(f"{tag}: camera zoomed in (zoom {zoomed['z']:.2f} vs whole floor {whole['z']:.2f})", zoomed["z"] > whole["z"] * 1.5, (whole, zoomed))
        btn = page.locator("#pClose")
        want_title, want_label = ("收起（Esc）", "收起") if zh else ("Close (Esc)", "Close")
        check(f"{tag}: close button in the panel header, title / aria-label", btn.count() == 1 and btn.is_visible()
              and btn.get_attribute("title") == want_title and btn.get_attribute("aria-label") == want_label
              and page.evaluate("() => !!document.querySelector('#pHead #pClose [data-sfi], #pHead #pClose svg')"),
              (btn.get_attribute("title"), btn.get_attribute("aria-label")))
        stop_txt = page.locator("#stopFollow").inner_text().replace("\n", " ")
        check(f"{tag}: footer button wording unchanged ({stop_txt!r})", stop_txt.startswith("停止追蹤" if zh else "Stop following") and stop_txt.endswith("Esc"), stop_txt)
        if shoot and zh:
            page.screenshot(path=str(SHOTS / "office-follow-panel.png"))

        for how in ("x", "stop", "esc"):
            if page.evaluate("__office.following()") != a["key"]:
                follow_by_click(page, a["key"])
                settle(page)
            before = cam(page)
            if how == "x":
                btn.click()
            elif how == "stop":
                page.locator("#stopFollow").click()
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            settle(page)
            after, closed = cam(page), not page.evaluate("__office.panelOpen()") and page.evaluate("__office.following()") is None
            check(f"{tag}: close with {how}: panel closed, not following", closed)
            check(f"{tag}: close with {how}: camera stays (zoom {before['z']:.3f} -> {after['z']:.3f})", same_cam(before, after), (before, after))
            page.wait_for_timeout(2000)
            later = cam(page)
            check(f"{tag}: close with {how}: still there 2 s later", same_cam(before, later), (before, later))
            if how == "x" and shoot and zh:
                page.screenshot(path=str(SHOTS / "office-follow-closed-kept-zoom.png"))

        print(f"[{tag}] switch to another person, bare floor, whole floor")
        follow_by_click(page, a["key"])
        z0 = cam(page)
        others = [b for b in bots(page) if b["key"] != a["key"] and (b["x"] - a_now(page, a)["x"]) ** 2 + (b["y"] - a_now(page, a)["y"]) ** 2 > 70 ** 2]
        others.sort(key=lambda b: (b["x"] - a_now(page, a)["x"]) ** 2 + (b["y"] - a_now(page, a)["y"]) ** 2)
        if not others:
            check(f"{tag}: someone else on screen beside the followed one", False, "no other bot visible")
        else:
            b = others[0]
            zs = []
            page.mouse.click(b["x"], b["y"])
            for _ in range(60):
                zs.append(cam(page)["z"])
                page.wait_for_timeout(40)
            now_follow = page.evaluate("__office.following()")
            name = page.locator("#pName").inner_text()
            check(f"{tag}: click {b['name']}: following them, panel is theirs", now_follow == b["key"] and b["name"] in name and page.evaluate("__office.panelOpen()"), (now_follow, name))
            check(f"{tag}: zoom never below 90 % while switching (min {min(zs):.2f}, before {z0['z']:.2f})", min(zs) >= z0["z"] * 0.9, (min(zs), z0["z"]))
            settle(page)
            z1 = cam(page)
            check(f"{tag}: camera panned over to them (moved {abs(z1['x'] - z0['x']) + abs(z1['y'] - z0['y']):.0f} px)", abs(z1["x"] - z0["x"]) + abs(z1["y"] - z0["y"]) > 5, (z0, z1))

            # bare floor: a point 140 px or more from every person
            spots = [(x, y) for x in range(80, W - 440, 60) for y in range(120, H - 100, 60)]
            ppl = [page.evaluate("k => __office.entScreen(k)", p["key"]) for p in bots(page)]
            spots = [(x, y) for x, y in spots if all(not q or (x - q["x"]) ** 2 + (y - q["y"]) ** 2 > 140 ** 2 for q in ppl)]
            done = False
            for x, y in spots[:12]:
                page.mouse.click(x, y)
                page.wait_for_timeout(200)
                if page.evaluate("() => !document.querySelector('#wall').hidden"):      # that was a piece of furniture: close it, try the next spot
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
                    follow_by_click(page, b["key"])
                    continue
                done = True
                break
            check(f"{tag}: click on bare floor ({x},{y}) leaves the panel open and the following as it was",
                  done and page.evaluate("__office.following()") == b["key"] and page.evaluate("__office.panelOpen()"), (done, page.evaluate("__office.following()")))

        page.locator("#pClose").click()      # the toolbar is hidden under the panel while following: close it first, then "Whole floor"
        page.wait_for_timeout(500)
        settle(page)
        before = cam(page)
        page.locator("#fitAll").click()
        page.wait_for_timeout(300)
        settle(page, 10000)
        end = cam(page)
        check(f"{tag}: Whole floor: back to the whole floor and not following (zoom {before['z']:.2f} -> {end['z']:.2f})",
              end["z"] < before["z"] * 0.5 and page.evaluate("__office.following()") is None, (before, end))
        ctx.close()


def a_now(page, a):
    return page.evaluate("k => __office.entScreen(k)", a["key"])


def main():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "web"))
    handler.log_message = lambda *args: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    SHOTS.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as p:
        for engine in ("chromium", "webkit"):
            try:
                browser = getattr(p, engine).launch()
            except Exception as e:      # noqa: BLE001
                print(f"SKIP  {engine}: {e}")
                continue
            try:
                run(browser, base, engine, errors)
            finally:
                browser.close()
    srv.shutdown()
    check("no page errors", not errors, errors[:3])
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
