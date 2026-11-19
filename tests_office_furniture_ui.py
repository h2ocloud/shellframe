#!/usr/bin/env python3
"""The lobby's furniture does not overlap: every piece has its own floor, and nothing stands in front of the wall's boards.

Reported with a screenshot: the bookshelf stood inside the small counter (the one with the black machine) and the personnel
cabinet was pressed against it, next to the secretary's desk. The objects were laid one after another along the back
wall from a fixed point, blind to the room's own furniture. Now the office page names the stretches of wall that are free
(office.html OBJ_SLOTS) and each piece goes into the first one with room. This test reads the lobby's layout
(__office.lobbyLayout: the room's furniture, the secretary's desk, the interns' tables, the objects; the boards on the
back wall) and checks, so that the next piece of furniture (the ops TV, …) is held to it too:
  - no two floor rectangles overlap; an object keeps at least 0.15 from everything else
  - an object stands against the back wall, inside the room, off the walkways (x≈1.0 along the left, the front strip)
  - an object does not stand in front of the whiteboard or the calendar
  - a third piece that fits nowhere gets no furniture (its toolbar button stays) instead of overlapping
office.html on its own (demo host), Chromium and WebKit. Screenshots (Chromium): qa-shots/office-lobby-furniture-{flat,iso}-1280.png.
Run: .venv/bin/python tests_office_furniture_ui.py
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
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


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


EPS = 1e-6   # touching is not overlapping: the interns' long table is pieces of 1.2 side by side (1.6 + 3 * 1.2 = 5.1999…)


def overlap(a, b, pad=0.0):
    return (a["x0"] < b["x1"] + pad - EPS and b["x0"] < a["x1"] + pad - EPS
            and a["y0"] < b["y1"] + pad - EPS and b["y0"] < a["y1"] + pad - EPS)


def layout_checks(L, tag):
    floor = L["floor"]
    objs = [f for f in floor if f["id"].startswith("obj:")]
    bad = [(a["id"], b["id"]) for i, a in enumerate(floor) for b in floor[i + 1:] if overlap(a, b)]
    check(f"{tag}: no two pieces of the lobby's furniture share floor", not bad, bad)
    tight = [(o["id"], f["id"]) for o in objs for f in floor if f is not o and overlap(o, f, 0.15)]
    check(f"{tag}: an object keeps at least 0.15 from everything else", not tight, tight)
    check(f"{tag}: objects stand against the back wall, inside the room",
          all(o["y0"] == 0 and o["x0"] >= 0 and o["x1"] <= L["w"] and o["y1"] <= 1.0 for o in objs), objs)
    walk = [o["id"] for o in objs if o["x0"] < 1.25 or o["y1"] > L["h"] - 0.9]
    check(f"{tag}: no object on a walkway (the left aisle, the front strip)", not walk, walk)
    covers = [(o["id"], w["id"]) for o in objs for w in L["wall"] if o["x0"] < w["x1"] and w["x0"] < o["x1"] and o["z1"] > w["z0"]]
    check(f"{tag}: no object stands in front of the whiteboard or the calendar", not covers, covers)
    return objs


DEMO_OBJS = """(extra) => {
  const db = __office.db();
  db.objects = [
    { id: 'bookshelf', title: '書櫃', furniture: 'bookshelf', place: 'lobby:wall', button: true, badge: 2, filters: [], sorts: [], actions: [] },
    { id: 'personnel', title: '人事檔案櫃', short: '人事', furniture: 'cabinet', place: 'lobby:wall', button: true, badge: 0, filters: [], sorts: [], actions: [] },
    { id: 'deliverables', title: '交付物', furniture: 'generic', place: 'button', button: true, badge: 3, filters: [], sorts: [], actions: [] }
  ].concat(extra ? [{ id: 'opstv', title: '示範第三件', furniture: 'generic', place: 'lobby:wall', button: true, badge: 0, filters: [], sorts: [], actions: [] }] : []);
  Office.update(db);
  return __office.objects().objects.map(o => [o.id, o.onWall]);
}"""

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
                print(f"[{engine}] the lobby")
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
                page.goto(base + "/office.html")
                page.wait_for_timeout(1200)
                page.evaluate("__office.pause(true)")
                on = page.evaluate(DEMO_OBJS, False)
                check("the bookshelf and the personnel cabinet both stand in the lobby; the deliverables are a button only",
                      dict(on) == {"bookshelf": True, "personnel": True, "deliverables": False}, on)
                L = page.evaluate("__office.lobbyLayout()")
                objs = layout_checks(L, "two objects")
                ids = {o["id"]: o for o in objs}
                ctr = next((f for f in L["floor"] if f["id"].startswith("decor:counter")), None)
                check("the bookshelf is right of the counter (the reported overlap), the cabinet behind the secretary's desk",
                      ctr and ids["obj:bookshelf"]["x0"] >= ctr["x1"] + 0.15 and ids["obj:personnel"]["x0"] > L["rug"]["x0"], ids)
                if engine == "chromium":
                    SHOTS.mkdir(exist_ok=True)
                    page.evaluate("__office.setArea && __office.setArea('lobby')")
                    page.evaluate("__office.setMode('iso', true)")
                    page.wait_for_timeout(500)
                    page.screenshot(path=str(SHOTS / "office-lobby-furniture-iso-1280.png"))
                    page.evaluate("__office.setMode('flat', true)")
                    page.wait_for_timeout(500)
                    page.screenshot(path=str(SHOTS / "office-lobby-furniture-flat-1280.png"))
                on = page.evaluate(DEMO_OBJS, True)
                L = page.evaluate("__office.lobbyLayout()")
                layout_checks(L, "with a third piece")
                check("a third piece that fits nowhere gets no furniture (its toolbar button stays), it does not overlap",
                      dict(on).get("opstv") is False and page.evaluate("!!document.querySelector('#tools button[data-obj=\"opstv\"]')"), on)
                check("no page errors", not errors, errors)
                page.close()
            finally:
                browser.close()
    finally:
        srv.shutdown()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
