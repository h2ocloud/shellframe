#!/usr/bin/env python3
"""Name plates and name tags stay off the furniture you can click (task #79).

Reported with a screenshot: after 「整理位置」 put a paired computer's room behind the lobby, that room's name plate
("隔壁：<電腦> 配對・主要：<bot>") hung across the lobby's back wall, over the whiteboard and the calendar, and people's name
tags often sat on furniture. The page is web/office.html on its own (demo host), two views (3D, flat), two languages, Chromium and WebKit.
The scene is the reported one: three paired computers (示範電腦 甲 / 乙 / 丙), 甲 and 乙 arranged behind the lobby, 丙 beside it, this
computer's ops board on (so there are small TVs too).

  a. the lobby's whiteboard, calendar and TV, a paired room's whiteboard and TV, the bookshelf and the cabinet: the pointer on the middle of
     one gets that piece's tooltip, and a click opens it (never a name plate)
  b. every room's name plate (the lobby, the zones, each paired room) overlaps no clickable wall piece of ANY room, and no other plate,
     in 3D and flat; the place the plate used to hang does overlap something (the check has teeth)
  c. a paired room's plate and the lobby's say only the name at rest ("隔壁：<電腦>", "大廳"); the second line (paired · main bot,
     secretary · interns) shows while the pointer is on the plate; a zone's count stays
  d. the pointer on a piece of furniture: the name tags and plates over it are drawn at 25 %, back to full when it leaves
  e. nothing in the way: the plates are exactly where they were before (positions measured on the unchanged page: lobby and zones, and the
     old column of paired rooms)
  f. no room fits in a very narrow window: the plate shrinks to a dot and a short name (10 characters at most, then …), still clear of the furniture
Screenshots (Chromium, zh-TW, 1280 x 800): qa-shots/office-labels-{before,after,after-flat,hover}.png (before = the unchanged page, taken from base 2ee954b).
Run: .venv/bin/python tests_office_labels_ui.py
"""
import functools
import http.server
import json
import pathlib
import re
import sys
import threading

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
        print(f"  [FAIL] {name}  {str(detail)[:500]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_office_labels_ui.py（沒裝 playwright）")
    sys.exit(0)

KEY = "sbOffice.v2.arrange"
ARRANGED = {"v": 1, "slots": {"pid甲": [0, -1], "pid乙": [1, -1], "pid丙": [-1, 0]}}      # 甲 right behind the lobby, 乙 behind the zones, 丙 on the left
SNAP = """(n) => { const s = __office.db(), mk = (k, c) => ({ id: 'pid' + k, name: '示範電腦 ' + k, board: { decisions: [], stuck: [], open: [] }, boardState: 'ok',
  bots: [...Array(c).keys()].map(i => ({ id: 'p' + k + i, name: '示範員' + k + i, primary: i == 0, avatar: { shape: 'round', color: 'mint' }, state: i ? 'idle' : 'working', duty: 'x' })), temps: [] });
  s.peers = [mk('甲', 3), mk('乙', 2), mk('丙', 1)].slice(0, n);
  s.objects = [{ id: 'bookshelf', title: '書櫃', furniture: 'bookshelf', place: 'lobby:wall', button: true, badge: 2, filters: [], sorts: [], actions: [] },
               { id: 'personnel', title: '人事檔案櫃', short: '人事', furniture: 'cabinet', place: 'lobby:wall', button: true, badge: 0, filters: [], sorts: [], actions: [] }];
  Office.update(s); }"""
# measured on the unchanged page (2ee954b), 1280 x 800, zh-TW, whole floor, demo data, no paired computer: the centre of each plate
BASE0 = {"iso": {"lobby": [782.2, 214.3], "z:營運組#1": [919.2, 436.4], "z:工程組#1": [777.2, 507.4], "z:研究組#1": [635.3, 578.4]},
         "flat": {"lobby": [547.9, 101.5], "z:營運組#1": [889.2, 241.8], "z:工程組#1": [889.2, 426.5], "z:研究組#1": [889.2, 611.2]}}
# the same with the old column of three paired computers
BASE3 = {"iso": {"lobby": [841.3, 198.8], "z:營運組#1": [961.1, 394.7], "z:工程組#1": [836.9, 456.8], "z:研究組#1": [712.7, 518.9], "peer:pid丙": [179.5, 465.2]},
         "flat": {"lobby": [572.9, 98.4], "z:營運組#1": [821.6, 203.9], "z:工程組#1": [821.6, 338.5], "z:研究組#1": [821.6, 473.0],
                  "peer:pid甲": [468.0, 443.1], "peer:pid乙": [468.0, 573.4], "peer:pid丙": [468.0, 703.7]}}
CJK = re.compile(r"[一-鿿]")


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    class Server(http.server.ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True
    srv = Server(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def poly_overlap(a, b):
    """How far two convex polygons (lists of (x, y)) overlap on screen, in px: 0 when they only touch or are apart (separating axis)."""
    best = 1e9
    for poly in (a, b):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = -(y2 - y1), (x2 - x1)
            n = (nx * nx + ny * ny) ** .5
            if not n:
                continue
            nx, ny = nx / n, ny / n
            pa, pb = [nx * p[0] + ny * p[1] for p in a], [nx * p[0] + ny * p[1] for p in b]
            ov = min(max(pa), max(pb)) - max(min(pa), min(pb))
            if ov <= 0:
                return 0.0
            best = min(best, ov)
    return best


rect_pts = lambda g: [(g["x"], g["y"]), (g["x"] + g["w"], g["y"]), (g["x"] + g["w"], g["y"] + g["h"]), (g["x"], g["y"] + g["h"])]
quad_pts = lambda q: [(p["x"], p["y"]) for p in q]
centre = lambda q: (sum(p["x"] for p in q) / len(q), sum(p["y"] for p in q) / len(q))


def office(browser, base, mode, locale="zh-TW", n=3, seed=ARRANGED, ops=True, size=(1280, 800), errors=None):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, locale=locale)
    ctx.add_init_script("localStorage.setItem('sbOffice.v2.legendMin', 'true'); " + (f"localStorage.setItem('{KEY}', {json.dumps(json.dumps(seed))});" if seed else ""))
    page = ctx.new_page()
    if errors is not None:
        page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.goto(base + "/office.html")
    page.wait_for_function("window.__office && window.Office", timeout=15000)
    page.wait_for_timeout(500)
    page.evaluate("__office.pause(true)")
    page.evaluate(SNAP, n)
    page.wait_for_function("() => __office.walking() === 0", timeout=15000)
    if ops:
        page.evaluate("__office.opsSet(OfficeOps.demoObject((en, zh) => zh, 'ok'))")
    page.evaluate(f"__office.setMode('{mode}', true)")
    page.evaluate("__office.setArea('all', true)")
    page.wait_for_function("() => { const w = __office.busyWhy(); return w.rooms.length === 0 && !w.cam; }", timeout=8000)
    page.mouse.move(2, 790)
    page.wait_for_timeout(500)
    return page


def run(browser, base, engine, errors):
    shoot = engine == "chromium"
    for locale, zh in (("zh-TW", True), ("en-US", False)):
        for mode in ("iso", "flat"):
            tag = f"{engine} {locale} {mode}"
            print(f"[{tag}] the reported scene: rooms behind the lobby")
            pg = office(browser, base, mode, locale, errors=errors)
            quads = pg.evaluate("__office.allWallQuads()")
            signs = {s["id"]: s for s in pg.evaluate("__office.signs()")}
            ids = pg.evaluate("__office.signRooms()")
            check("the scene has the lobby's board, calendar and TV, the room's board and TV, the bookshelf and the cabinet on the walls",
                  {"board", "cal", "tv", "board:pid甲", "tv:pid甲", "obj:bookshelf", "obj:personnel"} <= set(quads) and not pg.evaluate("__office.lodFar()"), sorted(quads))
            # b. nothing under a plate
            bad = {i: {k: round(poly_overlap(rect_pts(s), quad_pts(q)), 1) for k, q in quads.items() if poly_overlap(rect_pts(s), quad_pts(q)) > 0} for i, s in signs.items()}
            bad = {i: v for i, v in bad.items() if v}
            check("b. no name plate overlaps any clickable wall piece of any room", len(signs) >= 7 and not bad, (len(signs), bad))
            ov = [(a, b) for a in signs for b in signs if a < b and poly_overlap(rect_pts(signs[a]), rect_pts(signs[b])) > 0]
            check("b. …and no two plates overlap", not ov, ov)
            raw = {i: pg.evaluate("(i) => __office.signRect(i, true)", i) for i in ids}
            hit = {i: [k for k, q in quads.items() if poly_overlap(rect_pts(g), quad_pts(q)) > 0] for i, g in raw.items()}
            hit = {i: v for i, v in hit.items() if v}
            if mode == "iso":
                check("b. the places the plates used to hang do overlap furniture (the check has teeth): the room behind the lobby's among them", "peer:pid甲" in hit and len(hit) >= 2, hit)
            else:
                check("b. (flat) the places the plates used to hang: whichever overlapped furniture has moved off it", all(i not in hit or signs.get(i) for i in hit), hit)
            inside = all(s["x"] >= 0 and s["x"] + s["w"] <= 1280 and s["y"] >= 0 for i, s in signs.items() if raw[i]["x"] >= 0 and raw[i]["x"] + raw[i]["w"] <= 1280)
            check("b. a plate that was on the screen is still on it, and none became a dot where it fit", inside and not any(s["dot"] for s in signs.values()), [s for s in signs.values() if s["dot"]])
            # c. second line only under the pointer
            pid = "peer:pid甲"
            words = ("配對", "主要") if zh else ("Paired", "Main")
            rest = signs[pid]["text"], signs["lobby"]["text"]
            check("c. at rest a paired room's plate says only the name, and so does the lobby's",
                  not any(w in rest[0] for w in words) and ("秘書長" not in rest[1] and "Secretary" not in rest[1]) and rest[0].endswith("示範電腦 甲") and len(rest[1]) <= 6, rest)
            check("c. a zone's plate keeps its count (the n/12 is not hidden)", re.search(r"\d+/12", " ".join(s["text"] for i, s in signs.items() if i.startswith("z:"))) is not None, [s["text"] for s in signs.values()])
            for room, needs in ((pid, words), ("lobby", ("秘書長",) if zh else ("Secretary",))):
                on, s2 = None, {}
                for fx, fy in ((.5, .5), (.3, .5), (.7, .5), (.2, .3), (.8, .7)):     # a person's catch zone may cover one spot of the plate: any spot where nothing else is under the pointer
                    pg.mouse.move(signs[room]["x"] + signs[room]["w"] * fx, signs[room]["y"] + signs[room]["h"] * fy)
                    pg.wait_for_timeout(250)
                    on = pg.evaluate("__office.hoverSign()")
                    if on == room:
                        break
                s2 = {s["id"]: s for s in pg.evaluate("__office.signs()")}
                check(f"c. pointer on {room}'s plate: its second line shows ({' + '.join(needs)})", on == room and all(w in s2[room]["text"] for w in needs) and s2[room]["open"], (on, s2.get(room)))
                pg.mouse.move(2, 790)
                pg.wait_for_timeout(250)
                s3 = {s["id"]: s for s in pg.evaluate("__office.signs()")}
                check(f"c. pointer away: {room}'s plate is back to the name alone", not s3[room]["open"] and not any(w in s3[room]["text"] for w in needs), s3.get(room))
            # a. the furniture first
            wall_tip = {}
            fails = []
            for key, q in quads.items():
                cx, cy = centre(q)
                pg.mouse.move(cx, cy)
                pg.wait_for_timeout(200)
                at = pg.evaluate("([x, y]) => __office.wallAt(x, y)", [cx, cy])
                tip = pg.evaluate("__office.hoverTip()")
                if at != key or not tip:
                    fails.append((key, at, tip, "hover"))
                    continue
                pg.mouse.click(cx, cy)
                pg.wait_for_timeout(300)
                opened = pg.evaluate("() => ({ view: __office.view(), scope: __office.viewScope(), obj: __office.objects().objects.filter(o => o.open).map(o => o.id), ops: !!(window.OfficeOps && document.querySelector('.ops-win, .ops-panel, [data-ops-open]')) })")
                if key in ("board", "cal") and opened["view"] != key:
                    fails.append((key, "click opened", opened))
                if key.startswith("board:") and not (opened["view"] == "board" and opened["scope"] == key[6:]):
                    fails.append((key, "click opened", opened))
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(200)
                pg.evaluate("() => { __office.closeView(); __office.objClose(); if (__office.ops().close) __office.ops().close(); }")
                pg.mouse.move(2, 790)
                pg.wait_for_timeout(120)
            check(f"a. the middle of each of the {len(quads)} wall pieces: its tooltip on hover, and a click on the lobby's board / calendar and a room's board opens that one", not fails, fails)
            # d. over the piece under the pointer, labels turn faint
            pills = pg.evaluate("__office.pills()")
            cases = [(k, p["text"]) for k, q in quads.items() for p in pills if poly_overlap(rect_pts(p), quad_pts(q)) > 0]
            bad = []
            for key, text in cases:
                cx, cy = centre(quads[key])
                pg.mouse.move(cx, cy)
                pg.wait_for_timeout(250)
                now = [p for p in pg.evaluate("__office.pills()") if p["text"] == text]
                pg.mouse.move(2, 790)
                pg.wait_for_timeout(250)
                back = [p for p in pg.evaluate("__office.pills()") if p["text"] == text]
                if not (now and all(p["alpha"] < .5 for p in now) and back and all(p["alpha"] == 1 for p in back)):
                    bad.append((key, text, now, back))
            if mode == "iso":
                check("d. the secretary's name tag stands over the cabinet: that case exists", any(t == "示範秘書長" and k == "obj:personnel" for k, t in cases), cases)
            check(f"d. pointer on a piece of furniture: every name tag over it is at 25 % ({len(cases)} case(s)), and back to full when the pointer leaves", not bad, bad)
            pg.context.close()
    return


def unmoved(browser, base, engine, errors):
    print(f"[{engine}] e. nothing in the way: the plates are where they were")
    for mode in ("iso", "flat"):
        pg = office(browser, base, mode, n=0, seed=None, ops=False, errors=errors)
        got = {s["id"]: [s["x"] + s["w"] / 2, s["y"] + s["h"] / 2] for s in pg.evaluate("__office.signs()")}
        bad = {k: (got.get(k), v) for k, v in BASE0[mode].items() if not got.get(k) or abs(got[k][0] - v[0]) > 1.5 or abs(got[k][1] - v[1]) > 1.5}
        check(f"e. {mode}: the lobby's and the zones' plates (no paired computer, no TV) are at the centres measured on the unchanged page", not bad and len(got) == 4, (bad, got))
        pg.context.close()
        pg = office(browser, base, mode, n=3, seed=None, ops=False, errors=errors)
        got = {s["id"]: [s["x"] + s["w"] / 2, s["y"] + s["h"] / 2] for s in pg.evaluate("__office.signs()")}
        quads = pg.evaluate("__office.allWallQuads()")
        raw = {i: pg.evaluate("(i) => __office.signRect(i, true)", i) for i in got}
        blocked = {i for i, g in raw.items() if any(poly_overlap(rect_pts(g), quad_pts(q)) > 0 for q in quads.values())}
        moved = {k for k, v in got.items() if k in BASE3[mode] and (abs(v[0] - BASE3[mode][k][0]) > 1.5 or abs(v[1] - BASE3[mode][k][1]) > 1.5)}
        check(f"e. {mode}: the old column of three paired computers: every plate that was clear is at its old centre; a plate that moved had furniture under it",
              set(got) >= set(BASE3[mode]) and moved <= blocked, (moved, blocked))
        # and a plate that is not in the way does not move when something unrelated changes (the pointer on another thing)
        pg.mouse.move(300, 300)
        pg.wait_for_timeout(200)
        again = {s["id"]: [s["x"] + s["w"] / 2, s["y"] + s["h"] / 2] for s in pg.evaluate("__office.signs()")}
        check(f"e. {mode}: the pointer moving about does not move any plate", all(abs(again[k][0] - v[0]) < .5 and abs(again[k][1] - v[1]) < .5 for k, v in got.items() if k in again), (got, again))
        pg.context.close()


def shrinks(browser, base, engine, errors):
    print(f"[{engine}] f. no room to hang: a dot and a short name")
    for mode in ("iso", "flat"):
        pg = office(browser, base, mode, errors=errors)
        pg.evaluate("""() => { const s = __office.db(); s.peers = [{ id: 'pid甲', name: 'Demo-MacBook-Pro-Long-Name', board: { decisions: [], stuck: [], open: [] }, boardState: 'ok',
            bots: [{ id: 'pa0', name: '示範員甲0', primary: true, avatar: { shape: 'round', color: 'mint' }, state: 'working', duty: 'x' }], temps: [] }]; Office.update(s); }""")
        pg.wait_for_timeout(800)
        pg.mouse.move(2, 790)
        pg.wait_for_timeout(300)
        raw = pg.evaluate("__office.signRect('peer:pid甲', true)")
        full = next(s for s in pg.evaluate("__office.signs()") if s["id"] == "peer:pid甲")
        gap = raw["w"] * .62
        cx, cy = raw["x"] + raw["w"] / 2, raw["y"] + raw["h"] / 2
        pg.evaluate("(r) => __office.blockSigns(r)", [{"x": cx - gap / 2 - 900, "y": cy - 200, "w": 900, "h": 400}, {"x": cx + gap / 2, "y": cy - 200, "w": 900, "h": 400}])
        pg.wait_for_timeout(400)
        s = next(s for s in pg.evaluate("__office.signs()") if s["id"] == "peer:pid甲")
        check(f"f. {mode}: with no spot wide enough, the plate becomes a dot and a short name (10 characters at most, then …)",
              s["dot"] and s["text"].endswith("…") and len(s["text"]) <= 11 and s["w"] < raw["w"] * .62 and full["w"] > s["w"], (s, raw))
        quads = pg.evaluate("__office.allWallQuads()")
        check(f"f. {mode}: …and it is still clear of the furniture", all(poly_overlap(rect_pts(s), quad_pts(q)) == 0 for q in quads.values()), s)
        pg.evaluate("__office.blockSigns([])")
        pg.wait_for_timeout(300)
        s2 = next(s for s in pg.evaluate("__office.signs()") if s["id"] == "peer:pid甲")
        check(f"f. {mode}: the block lifted, the plate is full size again", not s2["dot"] and s2["text"].startswith("隔壁：Demo-MacBook"), s2)
        pg.context.close()


srv = serve()
base = f"http://127.0.0.1:{srv.server_port}"
with sync_playwright() as pw:
    errors = []
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
                run(browser, base, engine, errors)
                unmoved(browser, base, engine, errors)
                shrinks(browser, base, engine, errors)
            finally:
                browser.close()
    finally:
        srv.shutdown()
check("no page errors", not errors, errors)
print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
