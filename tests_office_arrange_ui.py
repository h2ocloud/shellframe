#!/usr/bin/env python3
"""The office's 「整理位置」 (Arrange positions): where the paired computers' rooms stand around this computer's building.

The page is web/office.html with web/office_arrange.js. Two ways of running it:
  - on its own (the demo page, localStorage as the store): the layout, the drag, the keys, the camera, both languages, narrow windows
  - in the real window with a stand-in Python side (index.html, the office in its iframe): what is saved goes through office_prefs_save
    together with the other keys, and a reload reads it back
Pairs in the stand-in data are 示範電腦 甲 / 乙 / 丙 / 丁 (made-up names). Headless WebKit and Chromium.

  - without `arrange` the rooms stand in the old column, the numbers exactly as before (positions and "whole floor" zoom)
  - the mode: a bar on top, a handle on each Next-door room only; this computer's building and the avatars cannot be dragged
  - dragging a handle snaps to the cell nearest the carried room (blue) and drops there; Done saves every computer's cell, a reload keeps it
  - 3D and flat share the cell codes: 3D's upper right is flat's top
  - Esc goes back to how it was before the mode (also when a saved arrangement existed); Reset removes the key and the column is back
  - keys: Tab to a handle, an arrow picks up and moves one cell, Enter drops, Esc puts it back
  - a new pair takes the next empty cell; a saved cell that is taken is not used and not overwritten; an unpaired computer's record goes at the next save
  - "whole floor" keeps the building at the horizontal centre of the window (1, 2, 4, 6 computers, 3D and flat); the zoom is printed next to the design note's
  - both languages, no stray Chinese in English; a 900 and a 390 wide window; a touch drag
Screenshots (Chromium, zh-TW): qa-shots/office-arrange-*.png.
Run: .venv/bin/python tests_office_arrange_ui.py
"""
import functools
import http.server
import json
import pathlib
import re
import sys
import threading
import types

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
sys.path.insert(0, str(HERE))

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
    print("SKIP  tests_office_arrange_ui.py（沒裝 playwright）")
    sys.exit(0)

NAMES = ["甲", "乙", "丙", "丁", "戊", "己"]
COUNTS = [3, 2, 5, 1, 3, 2]                    # bots per computer: the third one has two rows (11.8 x 5.9)
PIDS = ["pid" + c for c in "abcdef"]
KEY = "sbOffice.v2.arrange"
# the column as it was before this feature (measured on the unchanged page: 1280 x 800, WebKit), first room at y 11, then every height + 2.6
COL_Y = [11, 17.1, 23.2, 31.7, 37.8, 43.9]
COL_SIZE = [(9.3, 3.5), (9.3, 3.5), (11.8, 5.9), (9.3, 3.5), (9.3, 3.5), (9.3, 3.5)]
COL_ZOOM = {"iso": {1: .704, 2: .683, 3: .593, 4: .542, 6: .462}, "flat": {1: .637, 2: .589, 3: .429, 4: .358, 6: .27}}
DESIGN_ZOOM = {"iso": {1: .62, 2: .52, 4: .49, 6: .47}, "flat": {1: .49, 2: .49, 4: .45, 6: .43}}     # the design note's table, the new arrangement
AFTER = {"pida": [0, -1], "pidb": [-1, 0], "pidc": [-1, 1], "pidd": [1, -1]}                       # the design note's example (甲、丁 above; 乙、丙 left)

SNAP = """(ix) => { const s = __office.db(), NM = %s, C = %s;
  s.peers = ix.map(k => { const bots = []; for (let i = 0; i < C[k]; i++) bots.push({ id: 'p' + k + 'b' + i, name: '示範員' + k + '-' + i, avatar: { shape: 'round', color: 'mint' }, state: i ? 'idle' : 'working', duty: 'x' });
    return { id: 'pid' + 'abcdef'[k], name: '示範電腦 ' + NM[k], bots, temps: [] }; });
  window.Office.update(s); }""" % (json.dumps(NAMES, ensure_ascii=False), json.dumps(COUNTS))


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


def settle(page):
    page.wait_for_function("() => { const w = __office.busyWhy(); return w.rooms.length === 0 && !w.cam; }", timeout=8000)
    page.wait_for_timeout(120)


def new_office(browser, base, n=4, mode="iso", seed=None, w=1280, h=800, locale="zh-TW", errors=None, peers=None):
    """The demo page with `n` paired computers (or the list `peers` of indexes), pausing the demo; `seed` is the saved arrangement."""
    ctx = browser.new_context(viewport={"width": w, "height": h}, locale=locale)
    ctx.add_init_script("""if (!sessionStorage.getItem('__seeded')) { sessionStorage.setItem('__seeded', '1');
        localStorage.setItem('sbOffice.v2.legendMin', 'true'); %s }""" % (f"localStorage.setItem('{KEY}', {json.dumps(json.dumps(seed))});" if seed else ""))
    page = ctx.new_page()
    if errors is not None:
        page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
        page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.goto(base + "/office.html")
    page.wait_for_function("window.__office && window.Office", timeout=15000)
    page.wait_for_timeout(500)
    page.evaluate("__office.pause(true)")
    put_peers(page, list(range(n)) if peers is None else peers)
    page.evaluate(f"__office.setMode('{mode}', true)")
    page.evaluate("__office.setArea('all', true)")
    settle(page)
    return page


def put_peers(page, ix):
    page.evaluate(SNAP, ix)
    page.wait_for_function("() => __office.walking() === 0", timeout=15000)
    settle(page)


def peers_rooms(page):
    return {r["id"][5:]: r for r in page.evaluate("__office.rooms()") if r["id"].startswith("peer:")}


def saved(page):
    raw = page.evaluate(f"localStorage.getItem('{KEY}')")
    return json.loads(raw) if raw else None


def st(page):
    return page.evaluate("__office.arr.state()")


def cells(page):
    return page.evaluate("__office.arr.cells()")


def enter(page):
    page.click("#arrange")
    page.wait_for_function("__office.arr.state().on", timeout=5000)
    settle(page)


def room_pt(page, pid):
    return page.evaluate("(p) => __office.arr.roomPoint(p)", pid)


def drag_to(page, pid, cell, up=True, jitter=(0, 0)):
    """Press the handle of `pid`, carry the room to the cell's centre on the screen, release (or not)."""
    h = page.evaluate("(p) => __office.arr.handlePoint(p)", pid)
    r0 = room_pt(page, pid)
    t = page.evaluate("(c) => __office.arr.cellPoint(c[0], c[1], c[2] || 1)", cell)
    dx, dy = t["x"] - r0["x"] + jitter[0], t["y"] - r0["y"] + jitter[1]
    page.mouse.move(h["x"], h["y"])
    page.mouse.down()
    for i in range(1, 9):
        page.mouse.move(h["x"] + dx * i / 8, h["y"] + dy * i / 8)
    page.wait_for_timeout(120)
    if up:
        page.mouse.up()
        settle(page)


def done(page):
    page.click('#arrBar [data-act="done"]')
    settle(page)


def legacy_ok(page, n):
    rooms = peers_rooms(page)
    return all(rooms[PIDS[i]]["x"] == .8 and rooms[PIDS[i]]["y"] == COL_Y[i] and (rooms[PIDS[i]]["w"], rooms[PIDS[i]]["h"]) == COL_SIZE[i] for i in range(n)) and len(rooms) == n


def building_x(page):
    return page.evaluate("() => { const c = __office.cam(); return c.x; }")


def bldg_center_dx(page):
    """How far the building's horizontal centre is from the window's: screen x of the building's middle minus W/2."""
    return page.evaluate("() => { const p = __office.arr.buildingPoint(); return p.x - innerWidth / 2; }")


def run(browser, base, engine):
    shoot = engine == "chromium"
    errors = []

    # ── 1. no `arrange`: exactly the old column ───────────────────────────────────────
    print(f"[{engine}] without `arrange` the rooms stand in the old column, the numbers unchanged")
    for mode in ("iso", "flat"):
        bad, zs = [], {}
        for n in (1, 2, 4, 6):
            pg = new_office(browser, base, n, mode, errors=errors)
            if not legacy_ok(pg, n):
                bad.append((mode, n, "rooms", peers_rooms(pg)))
            z = pg.evaluate("__office.cam().z")
            zs[n] = z
            if abs(z - COL_ZOOM[mode][n]) > .003:
                bad.append((mode, n, "zoom", z, COL_ZOOM[mode][n]))
            if n == 4 and (cells(pg) is not None or st(pg)["on"] or pg.evaluate("__office.arr.handles().length") != 0):
                bad.append((mode, n, "arrange state"))
            pg.context.close()
        check(f"{mode}: 1, 2, 4 and 6 computers: every room at x .8 and the old y, same size, the same whole-floor zoom (±.003)", not bad, bad)
    pg = new_office(browser, base, 4, errors=errors)
    check("the 整理位置 button is there, marked for the guide, and the mode is off", pg.evaluate("""() => { const b = document.getElementById('arrange');
        return !!b && b.getAttribute('data-guide') === 'office-arrange' && b.getAttribute('aria-pressed') === 'false' && document.getElementById('arrBar').hidden; }"""))
    pg.context.close()

    # ── 2. entering the mode ──────────────────────────────────────────────────────────
    print(f"[{engine}] entering the mode")
    pg = new_office(browser, base, 4, errors=errors)
    enter(pg)
    c = cells(pg)
    check("a bar on top (Reset, Cancel, Done) and a pressed button", pg.evaluate("""() => { const b = document.getElementById('arrBar');
        return !b.hidden && [...b.querySelectorAll('button')].map(x => x.dataset.act).join() === 'reset,cancel,done' && document.getElementById('arrange').getAttribute('aria-pressed') === 'true'; }"""))
    check("the rooms take the 'next empty cell' order: above, left, the corner, above again", {k: [v["c"], v["r"]] for k, v in c["at"].items()} ==
          {"pida": [0, -1], "pidb": [-1, 0], "pidc": [-1, -1], "pidd": [1, -1]}, c["at"])
    check("the building takes 2 x 3 cells and the ring has 14 cells; 4 are taken, 10 are open", (c["bc"], c["br"]) == (2, 3) and len(c["open"]) == 10, (c["bc"], c["br"], len(c["open"])))
    hs = pg.evaluate("__office.arr.handles()")
    check("one handle per Next-door room and none for this computer", [h["pid"] for h in hs] == PIDS[:4] and all("示範電腦" in h["text"] for h in hs), hs)
    check("nothing is saved by entering", saved(pg) is None)
    pg.screenshot(path=str(SHOTS / "office-arrange-mode.png")) if shoot else None
    pg.context.close()

    # ── 3. drag, snap, drop, Done, reload ─────────────────────────────────────────────
    print(f"[{engine}] dragging a room to a cell")
    pg = new_office(browser, base, 4, errors=errors)
    enter(pg)
    drag_to(pg, "pidb", [-1, 2], up=False)
    s = st(pg)
    check("carried near an open cell, that cell lights up (the target), the room stays until it is dropped", s["dragging"] and s["dragging"]["pid"] == "pidb"
          and s["dragging"]["cell"] == [-1, 2] and s["dragging"]["from"] == [-1, 0], s)
    check("…the dragged handle is the blue one", pg.evaluate("document.querySelector('.arr-h.lifted') && document.querySelector('.arr-h.lifted').dataset.pid") == "pidb")
    if shoot:
        pg.screenshot(path=str(SHOTS / "office-arrange-drag.png"))
    pg.mouse.up()
    settle(pg)
    c = cells(pg)
    r = peers_rooms(pg)["pidb"]
    check("dropped: the room is at the new cell, the old one is empty again, nobody else moved", c["at"]["pidb"]["c"] == -1 and c["at"]["pidb"]["r"] == 2
          and [-1, 0] in c["open"] and (c["at"]["pida"]["c"], c["at"]["pida"]["r"]) == (0, -1) and (c["at"]["pidc"]["c"], c["at"]["pidc"]["r"]) == (-1, -1)
          and (r["x"], r["y"]) == (c["at"]["pidb"]["x"], c["at"]["pidb"]["y"]), (c["at"], r))
    check("…nothing is saved until Done", saved(pg) is None)
    done(pg)
    sv = saved(pg)
    check("Done saves {v: 1, slots}: the room you moved and the cell each other computer stands on", sv == {"v": 1, "slots": {"pida": [0, -1], "pidb": [-1, 2], "pidc": [-1, -1], "pidd": [1, -1]}}, sv)
    check("…the mode is closed (no bar, no handles) and the rooms stay where they are",
          not st(pg)["on"] and pg.evaluate("document.getElementById('arrBar').hidden && __office.arr.handles().length === 0") and peers_rooms(pg)["pidb"]["y"] == r["y"])
    before = peers_rooms(pg)
    pg.reload()
    pg.wait_for_function("window.__office && window.Office", timeout=15000)
    pg.wait_for_timeout(500)
    pg.evaluate("__office.pause(true)")
    put_peers(pg, [0, 1, 2, 3])
    pg.evaluate("__office.setArea('all', true)")
    settle(pg)
    check("after a reload the same arrangement is there without opening the mode", peers_rooms(pg) == before and not st(pg)["on"], (peers_rooms(pg), before))
    pg.context.close()

    # ── 4. 3D and flat share the cell codes ───────────────────────────────────────────
    print(f"[{engine}] 3D and flat read the same cells")
    pg = new_office(browser, base, 4, "iso", seed={"v": 1, "slots": AFTER}, errors=errors)
    iso_rooms = peers_rooms(pg)
    p_iso = {k: pg.evaluate("(c) => __office.arr.cellPoint(c[0], c[1])", v) for k, v in {"above": [0, -1], "left": [-1, 0], "home": [0, 0]}.items()}
    pg.evaluate("__office.setMode('flat', true)")
    pg.evaluate("__office.setArea('all', true)")
    settle(pg)
    p_flat = {k: pg.evaluate("(c) => __office.arr.cellPoint(c[0], c[1])", v) for k, v in {"above": [0, -1], "left": [-1, 0], "home": [0, 0]}.items()}
    check("the world positions are the same in both views", peers_rooms(pg) == iso_rooms)
    check("3D: the cell 'above' the building is on its upper right, the one on the left is its upper left",
          p_iso["above"]["x"] > p_iso["home"]["x"] and p_iso["above"]["y"] < p_iso["home"]["y"] and p_iso["left"]["x"] < p_iso["home"]["x"] and p_iso["left"]["y"] < p_iso["home"]["y"], p_iso)
    check("flat: the same cell is straight above, and the left one straight to the left",
          abs(p_flat["above"]["x"] - p_flat["home"]["x"]) < 1 and p_flat["above"]["y"] < p_flat["home"]["y"] - 20
          and abs(p_flat["left"]["y"] - p_flat["home"]["y"]) < 1 and p_flat["left"]["x"] < p_flat["home"]["x"] - 20, p_flat)
    enter(pg)
    drag_to(pg, "pidd", [2, 1])
    check("the mode works flat too: a room dragged to the east side lands there", cells(pg)["at"]["pidd"]["c"] == 2 and cells(pg)["at"]["pidd"]["r"] == 1, cells(pg)["at"]["pidd"])
    pg.context.close()

    # ── 5. snapping ───────────────────────────────────────────────────────────────────
    print(f"[{engine}] snapping")
    pg = new_office(browser, base, 4, errors=errors)
    enter(pg)
    drag_to(pg, "pida", [0, 3], up=False, jitter=(26, 9))            # a little off the centre of the cell
    check("a little off the centre of a cell still snaps to it", st(pg)["dragging"]["cell"] == [0, 3], st(pg)["dragging"])
    pg.mouse.move(640, 400)                              # over the middle of this computer's building: no cell
    pg.wait_for_timeout(100)
    check("over this computer's own building nothing is lit, and releasing there changes nothing", st(pg)["dragging"]["cell"] is None)
    pg.mouse.up()
    settle(pg)
    check("…the room is still on its own cell and no slot was written", cells(pg)["at"]["pida"]["c"] == 0 and cells(pg)["at"]["pida"]["r"] == -1 and st(pg)["slots"].get("pida") == [0, -1])
    pg.context.close()

    # ── 6. Esc, Cancel, Reset ─────────────────────────────────────────────────────────
    print(f"[{engine}] Esc cancels, Reset goes back to the column")
    pg = new_office(browser, base, 4, errors=errors)
    enter(pg)
    drag_to(pg, "pidb", [-1, 2], up=False)
    pg.keyboard.press("Escape")
    s = st(pg)
    check("Esc while carrying only puts the room down (still arranging)", s["on"] and s["dragging"] is None and cells(pg)["at"]["pidb"]["r"] == 0, s)
    pg.mouse.up()
    drag_to(pg, "pidb", [-1, 2])
    check("…a drop is made", cells(pg)["at"]["pidb"]["r"] == 2)
    pg.keyboard.press("Escape")
    settle(pg)
    check("Esc again leaves the mode and everything is as before it: the old column (there was no arrangement), nothing saved",
          not st(pg)["on"] and legacy_ok(pg, 4) and saved(pg) is None and pg.evaluate("document.getElementById('arrBar').hidden && __office.arr.handles().length === 0"))
    pg.context.close()
    pg = new_office(browser, base, 4, seed={"v": 1, "slots": AFTER}, errors=errors)
    r0 = peers_rooms(pg)
    enter(pg)
    drag_to(pg, "pidd", [2, 1])
    pg.click('#arrBar [data-act="cancel"]')
    settle(pg)
    check("Cancel with a saved arrangement: back to exactly that one, the saved value untouched", peers_rooms(pg) == r0 and saved(pg) == {"v": 1, "slots": AFTER} and not st(pg)["on"])
    enter(pg)
    pg.keyboard.press("Escape")
    settle(pg)
    check("Esc without any change leaves the saved arrangement alone", peers_rooms(pg) == r0 and saved(pg) == {"v": 1, "slots": AFTER})
    enter(pg)
    pg.click('#arrBar [data-act="reset"]')
    settle(pg)
    check("Reset: the key is gone, the mode is closed, the rooms are in the old column again", saved(pg) is None and not st(pg)["on"] and legacy_ok(pg, 4), peers_rooms(pg))
    check("…and the toolbar button is not pressed", pg.evaluate("document.getElementById('arrange').getAttribute('aria-pressed')") == "false")
    pg.context.close()

    # ── 7. keys ───────────────────────────────────────────────────────────────────────
    print(f"[{engine}] the keyboard")
    pg = new_office(browser, base, 4, errors=errors)
    enter(pg)
    pg.focus('#arrBar [data-act="done"]')
    pg.keyboard.press("Tab")
    check("Tab from Done reaches the first room's handle", pg.evaluate("document.activeElement.dataset.pid") == "pida", pg.evaluate("document.activeElement.outerHTML.slice(0, 80)"))
    pg.keyboard.press("Tab")
    check("…and the next Tab the second", pg.evaluate("document.activeElement.dataset.pid") == "pidb")
    open_before = cells(pg)["open"]
    pg.keyboard.press("ArrowDown")
    s = st(pg)
    check("an arrow key picks the room up and aims it at an open cell in that direction on the screen",
          s["dragging"] and s["dragging"]["how"] == "key" and s["dragging"]["cell"] in open_before, s)
    first = s["dragging"]["cell"]
    moved = []
    for key in ("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"):
        pg.keyboard.press(key)
        moved.append(st(pg)["dragging"]["cell"])
    check("…more arrows move it on from there to other open cells (or back to its own), one step at a time",
          any(m != first for m in moved) and all(m is None or m in open_before for m in moved), (first, moved))
    pg.keyboard.press("Escape")
    s = st(pg)
    check("Esc puts a picked-up room back: still arranging, no change, no slot rewritten", s["on"] and s["dragging"] is None and cells(pg)["at"]["pidb"]["c"] == -1 and cells(pg)["at"]["pidb"]["r"] == 0, s)
    pg.focus('.arr-h[data-pid="pidb"]')
    pg.keyboard.press("ArrowDown")
    pg.keyboard.press("ArrowDown")
    target = st(pg)["dragging"]["cell"]
    pg.keyboard.press("Enter")
    settle(pg)
    at = cells(pg)["at"]["pidb"]
    check("Enter drops it on the aimed cell", target is not None and [at["c"], at["r"]] == target and st(pg)["dragging"] is None, (target, at))
    pg.focus('.arr-h[data-pid="pidc"]')
    pg.keyboard.press("Enter")
    check("Enter on a handle also picks it up (then the arrows aim, Enter drops)", st(pg)["dragging"] and st(pg)["dragging"]["pid"] == "pidc")
    live = pg.evaluate("document.querySelector('#arrBar .ab-live').textContent")
    check("a screen reader is told what to do (the picked-up line is in the live region)", "示範電腦 丙" in live and "Enter" in live, live)
    pg.keyboard.press("Escape")
    pg.context.close()

    # ── 8. new pair, a taken cell, an unpaired computer ──────────────────────────────
    print(f"[{engine}] a new pair, a taken cell, an unpaired computer")
    seed4 = {"v": 1, "slots": {"pida": [0, -1], "pidb": [-1, 0], "pidc": [-1, -1], "pidd": [1, -1]}}
    pg = new_office(browser, base, 4, seed=seed4, errors=errors)
    raw0 = pg.evaluate(f"localStorage.getItem('{KEY}')")
    put_peers(pg, [0, 1, 2, 3, 4])
    c = cells(pg)
    check("a fifth computer takes the next empty cell (above, left, corner, above: then the second left one)", (c["at"]["pide"]["c"], c["at"]["pide"]["r"], c["at"]["pide"]["how"]) == (-1, 1, "auto"), c["at"]["pide"])
    check("…the others did not move, and the saved record was not touched by that", all((c["at"][k]["c"], c["at"][k]["r"]) == tuple(v) for k, v in seed4["slots"].items()) and pg.evaluate(f"localStorage.getItem('{KEY}')") == raw0)
    put_peers(pg, [0, 1, 2, 3, 4, 5])
    c = cells(pg)
    check("a sixth takes the one after it", (c["at"]["pidf"]["c"], c["at"]["pidf"]["r"]) == (-1, 2), c["at"]["pidf"])
    pg.context.close()
    # two computers claim the same cell; a third claims a cell inside the building
    dup = {"v": 1, "slots": {"pida": [0, -1], "pidb": [0, -1], "pidc": [0, 0]}}
    pg = new_office(browser, base, 3, seed=dup, errors=errors)
    c = cells(pg)
    check("a saved cell that is taken is not used: the second claimant and the one inside the building stand on the next empty cells",
          (c["at"]["pida"]["c"], c["at"]["pida"]["r"], c["at"]["pida"]["how"]) == (0, -1, "saved") and c["at"]["pidb"]["how"] == "auto" and c["at"]["pidc"]["how"] == "auto"
          and len({(v["c"], v["r"]) for v in c["at"].values()}) == 3, c["at"])
    check("…and the saved record is not overwritten by that", saved(pg) == dup)
    enter(pg)
    done(pg)
    check("Done keeps what was saved for the displaced ones (it only writes the cell of those that had none)", saved(pg)["slots"]["pidb"] == [0, -1] and saved(pg)["slots"]["pidc"] == [0, 0] and saved(pg)["slots"]["pida"] == [0, -1], saved(pg))
    pg.context.close()
    # unpaired
    pg = new_office(browser, base, 4, seed=seed4, errors=errors)
    put_peers(pg, [0, 1, 2])
    check("a computer that is unpaired frees its cell…", "pidd" not in cells(pg)["at"] and [1, -1] in cells(pg)["open"])
    check("…its record is still in the file until the next save", saved(pg) == seed4)
    enter(pg)
    done(pg)
    check("…the next Done drops it, and the others stay", saved(pg) == {"v": 1, "slots": {k: v for k, v in seed4["slots"].items() if k != "pidd"}}, saved(pg))
    pg.context.close()
    # the first snapshot is late: no computer known yet
    pg = new_office(browser, base, 0, seed=seed4, errors=errors)
    enter(pg)
    done(pg)
    check("Done before any computer is known drops nothing", saved(pg) == seed4, saved(pg))
    pg.context.close()

    # ── 9. the building and the avatars do not move ──────────────────────────────────
    print(f"[{engine}] this computer's building and the avatars stay put")
    pg = new_office(browser, base, 4, errors=errors)
    pg.wait_for_function("() => __office.walking() === 0", timeout=15000)
    asg = pg.evaluate("__office.assignments()")
    zbots = [k for k, v in asg.items() if v.startswith("z:")][:2]
    a, b = zbots
    sa = pg.evaluate("(k) => __office.entScreen('b:' + k)", a)
    sb = pg.evaluate("(s) => __office.seatScreen(s)", asg[b])
    pg.mouse.move(sa["x"], sa["y"]); pg.mouse.down()
    for i in range(1, 9):
        pg.mouse.move(sa["x"] + (sb["x"] - sa["x"]) * i / 8, sa["y"] + (sb["y"] - sa["y"]) * i / 8)
    pg.mouse.up()
    pg.wait_for_timeout(400)
    swapped = pg.evaluate("__office.assignments()")
    check("(control) outside the mode, dragging an avatar onto another seat swaps them", swapped[a] == asg[b] and swapped[b] == asg[a], (asg, swapped))
    pg.context.close()
    pg = new_office(browser, base, 4, errors=errors)
    pg.wait_for_function("() => __office.walking() === 0", timeout=15000)
    asg = pg.evaluate("__office.assignments()")
    enter(pg)
    sa = pg.evaluate("(k) => __office.entScreen('b:' + k)", a)
    sb = pg.evaluate("(s) => __office.seatScreen(s)", asg[b])
    pg.mouse.move(sa["x"], sa["y"]); pg.mouse.down()
    for i in range(1, 9):
        pg.mouse.move(sa["x"] + (sb["x"] - sa["x"]) * i / 8, sa["y"] + (sb["y"] - sa["y"]) * i / 8)
    pg.mouse.up()
    pg.wait_for_timeout(400)
    check("while arranging, the same drag moves no avatar (it pans the view instead)", pg.evaluate("__office.assignments()") == asg and pg.evaluate("document.getElementById('resetSeats').hidden"))
    r_before = peers_rooms(pg)
    cam0 = pg.evaluate("__office.cam()")
    pg.mouse.move(560, 440); pg.mouse.down(); pg.mouse.move(600, 470, steps=4); pg.mouse.move(640, 500, steps=4)
    check("…pressing on the building's floor carries nothing: no room is lifted, no slot written", st(pg)["dragging"] is None and st(pg)["slots"] == {k: [v["c"], v["r"]] for k, v in cells(pg)["at"].items()})
    pg.mouse.up()
    check("…and the rooms did not move", peers_rooms(pg) == r_before)
    pg.context.close()

    # ── 10. "whole floor" keeps the building in the middle ───────────────────────────
    print(f"[{engine}] the whole-floor camera keeps the building at the horizontal centre")
    rows = []
    worst = 0
    for mode in ("iso", "flat"):
        for n in (1, 2, 4, 6):
            pg = new_office(browser, base, n, mode, errors=errors)
            enter(pg)
            done(pg)
            pg.evaluate("__office.setArea('all', true)")
            settle(pg)
            dx = bldg_center_dx(pg)
            z = pg.evaluate("__office.cam().z")
            worst = max(worst, abs(dx))
            rows.append((mode, n, round(z, 3), round(dx, 2)))
            pg.context.close()
    print("     whole-floor zoom of the new arrangement (this build) vs the design note's table, and the building's offset from the window's centre in px:")
    for mode in ("iso", "flat"):
        print("       " + mode + ": " + ", ".join(f"{n} computers {z} (design {DESIGN_ZOOM[mode][n]}, was {COL_ZOOM[mode][n]}; off-centre {dx}px)" for m, n, z, dx in rows if m == mode))
    check("the building is at the window's horizontal centre in 3D and flat with 1, 2, 4 and 6 computers (tolerance 1.5 px)", worst <= 1.5, rows)
    check("the whole-floor zoom is within ±.04 of the design note's table", all(abs(z - DESIGN_ZOOM[m][n]) <= .04 for m, n, z, dx in rows if n in DESIGN_ZOOM[m]), rows)

    # ── 11. pictures: the arranged floor in 3D and flat ──────────────────────────────
    if shoot:
        pg = new_office(browser, base, 4, "iso", seed={"v": 1, "slots": AFTER}, errors=errors)
        pg.screenshot(path=str(SHOTS / "office-arrange-after-iso.png"))
        pg.evaluate("__office.setMode('flat', true)")
        pg.evaluate("__office.setArea('all', true)")
        settle(pg)
        pg.screenshot(path=str(SHOTS / "office-arrange-after-flat.png"))
        pg.context.close()

    # ── 12. the two languages ────────────────────────────────────────────────────────
    print(f"[{engine}] both languages")
    cjk = re.compile(r"[一-鿿　-〿＀-￯]")
    en = new_office(browser, base, 4, locale="en-US", errors=errors)
    enter(en)
    txt = en.evaluate("() => [document.getElementById('arrange').textContent, document.getElementById('arrange').title, document.getElementById('arrBar').innerText, ...[...document.querySelectorAll('.arr-h')].map(h => h.textContent + h.title + h.getAttribute('aria-label'))].join(' | ')")
    # the demo's computers are named in Chinese (made-up names): take those out before looking for stray Chinese
    stripped = re.sub(r"示範電腦 [甲乙丙丁]", "", txt)
    check("English: the button, the bar and the handles have no Chinese in them", not cjk.search(stripped) and "Arrange" in txt and "Done" in txt and "Next door:" in txt, stripped)
    en.context.close()
    zh = new_office(browser, base, 4, locale="zh-TW", errors=errors)
    enter(zh)
    txt = zh.evaluate("() => [document.getElementById('arrange').textContent, document.getElementById('arrBar').innerText, ...[...document.querySelectorAll('.arr-h')].map(h => h.textContent)].join(' | ')")
    check("Chinese: 整理位置, 還原預設, 完成, 隔壁：", all(w in txt for w in ("整理位置", "還原預設", "完成", "隔壁：")) and "Arrange" not in txt, txt)
    zh.context.close()

    # ── 13. narrow windows ───────────────────────────────────────────────────────────
    print(f"[{engine}] narrow windows")
    for w, h in ((900, 700), (390, 800)):
        pg = new_office(browser, base, 4, "iso" if w == 900 else "flat", w=w, h=h, errors=errors)      # a phone is flat (portrait)
        enter(pg)
        m = pg.evaluate("""() => { const r = document.getElementById('arrBar').getBoundingClientRect(), t = document.getElementById('top').getBoundingClientRect();
            return { l: r.left, r: r.right, t: r.top, b: r.bottom, topBottom: t.bottom, sw: document.scrollingElement.scrollWidth, W: innerWidth, bh: [...document.querySelectorAll('#arrBar button')].map(b => { const q = b.getBoundingClientRect(); return [q.left, q.right]; }) }; }""")
        check(f"{w} wide: the bar is inside the window, under the top bar, and the page does not scroll sideways",
              m["l"] >= 0 and m["r"] <= m["W"] and m["t"] >= m["topBottom"] - 1 and m["sw"] <= m["W"] and all(a >= 0 and b <= m["W"] for a, b in m["bh"]), m)
        if shoot and w == 900:
            pg.screenshot(path=str(SHOTS / "office-arrange-900.png"))
        if w == 390:
            if shoot:
                pg.screenshot(path=str(SHOTS / "office-arrange-390.png"))
            hh = pg.evaluate("__office.arr.handlePoint('pidb')")
            check("390 wide: a handle is at least 36 px high (a finger)", hh and hh["h"] >= 36, hh)
            # a touch drag: pointer events of type touch on the handle (the drivers cannot press a real finger)
            tgt = pg.evaluate("(c) => __office.arr.cellPoint(c[0], c[1])", [-1, 2])
            ro = room_pt(pg, "pidb")
            res = pg.evaluate("""([hx, hy, dx, dy]) => { const el = document.querySelector('.arr-h[data-pid="pidb"]');
                const mk = (type, x, y) => new PointerEvent(type, { pointerId: 7, pointerType: 'touch', isPrimary: true, clientX: x, clientY: y, bubbles: true, cancelable: true, button: 0 });
                el.dispatchEvent(mk('pointerdown', hx, hy));
                for (let i = 1; i <= 8; i++) window.dispatchEvent(mk('pointermove', hx + dx * i / 8, hy + dy * i / 8));
                const mid = __office.arr.state().dragging;
                window.dispatchEvent(mk('pointerup', hx + dx, hy + dy));
                return { mid, after: __office.arr.state() }; }""", [hh["x"], hh["y"], tgt["x"] - ro["x"], tgt["y"] - ro["y"]])
            check("390 wide: a touch drag (pointer events of type touch) lifts the room, aims at the cell and drops it",
                  res["mid"] and res["mid"]["cell"] == [-1, 2] and res["after"]["slots"].get("pidb") == [-1, 2] and res["after"]["dragging"] is None, res)
        pg.context.close()

    check("no page error", not errors, errors[:5])


# ── the real window: what is saved goes through office_prefs_save with the other keys ─────────
H = types.ModuleType("office_board_harness")
_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)

REAL_PEERS = {"peers": {("pid-" + c): {"name": "示範電腦 " + n, "addr": "示範電腦 " + n, "primary": "",
                                       "bots": [{"role": "b1", "label": "示範員 " + n, "state": "awake", "sid": "r1" + c, "activity": "idle", "avatar": {"shape": "hex", "color": "amber"}, "responsibility": "示範"}],
                                       "tabs": {}} for c, n in zip("abcd", "甲乙丙丁")}}
OTHER_KEYS = {"mode": "iso", "seats": {"eng": {"zone": "營運組#1", "slot": 2}}, "zoning": "custom", "groups": {"eng": "示範小組"}, "legendMin": True, "calMode": "week"}
WRAP = """
if (window.top === window) {
  const base = window.pywebview.api;
  window.__prefs0 = %s; window.__saved = [];
  window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
    if (prop === 'then') return undefined;
    if (prop === 'office_prefs') return () => Promise.resolve(JSON.stringify({ success: true, prefs: window.__prefs0 }));
    if (prop === 'office_prefs_save') return (j) => { window.__saved.push(JSON.parse(j)); return Promise.resolve(JSON.stringify({ success: true })); };
    return base[prop];
  } }) };
}
"""


def run_real(browser, base, engine):
    print(f"[{engine}] in the real window: saved with the other keys, read back after a reload")
    errors = []

    def open_page(prefs):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
        page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
        H.PEERS = REAL_PEERS
        page.add_init_script(H.stub())
        page.add_init_script(WRAP % json.dumps(prefs))
        page.goto(base + "/index.html")
        page.wait_for_selector("#sb-footer-row", timeout=20000)
        page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
        page.wait_for_function("Object.keys(SFBots.peerBots()).length === 4", timeout=20000)
        page.evaluate("SFOffice.open()")
        page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
            try { return f.contentWindow.__office && f.contentWindow.__office.rooms().filter(r => r.id.startsWith('peer:')).length === 4; } catch (e) { return false; } }""", timeout=20000)
        fr = H.office(page)
        fr.evaluate("__office.pause(true); __office.setMode('iso', true); __office.setArea('all', true)")
        fr.wait_for_function("() => { const w = __office.busyWhy(); return w.rooms.length === 0; }", timeout=8000)
        page.wait_for_timeout(300)
        return page, fr

    page, fr = open_page(dict(OTHER_KEYS))
    check("no `arrange` in the saved prefs: the real window shows the old column", all(r["x"] == .8 for r in fr.evaluate("__office.rooms()") if r["id"].startswith("peer:")))
    fr.click("#arrange")
    fr.wait_for_function("__office.arr.state().on")
    page.wait_for_timeout(600)
    pts = fr.evaluate("() => ({ h: __office.arr.handlePoint('pid-b'), r: __office.arr.roomPoint('pid-b'), t: __office.arr.cellPoint(-1, 2) })")
    page.mouse.move(pts["h"]["x"], pts["h"]["y"]); page.mouse.down()
    for i in range(1, 9):
        page.mouse.move(pts["h"]["x"] + (pts["t"]["x"] - pts["r"]["x"]) * i / 8, pts["h"]["y"] + (pts["t"]["y"] - pts["r"]["y"]) * i / 8)
    page.mouse.up()
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc in the real window cancels the mode, not the whole office: it is still open and nothing was saved", page.evaluate("SFOffice.isOpen()") and fr.evaluate("!__office.arr.state().on") and page.evaluate("window.__saved.every(p => !('arrange' in p))"))
    fr.click("#arrange")
    fr.wait_for_function("__office.arr.state().on")
    page.wait_for_timeout(600)
    pts = fr.evaluate("() => ({ h: __office.arr.handlePoint('pid-b'), r: __office.arr.roomPoint('pid-b'), t: __office.arr.cellPoint(-1, 2) })")
    page.mouse.move(pts["h"]["x"], pts["h"]["y"]); page.mouse.down()
    for i in range(1, 9):
        page.mouse.move(pts["h"]["x"] + (pts["t"]["x"] - pts["r"]["x"]) * i / 8, pts["h"]["y"] + (pts["t"]["y"] - pts["r"]["y"]) * i / 8)
    page.mouse.up()
    page.wait_for_timeout(500)
    fr.click('#arrBar [data-act="done"]')
    page.wait_for_function("window.__saved.some(p => 'arrange' in p)", timeout=8000)
    last = page.evaluate("window.__saved[window.__saved.length - 1]")
    check("Done: the saved prefs hold `arrange` together with every other key that was there, none washed out",
          last.get("arrange") and all(last.get(k) == v for k, v in OTHER_KEYS.items()), last)
    check("…the slots: the room that was moved, and the cell each other computer stands on",
          last["arrange"] == {"v": 1, "slots": {"pid-a": [0, -1], "pid-b": [-1, 2], "pid-c": [-1, -1], "pid-d": [1, -1]}}, last.get("arrange"))
    rooms1 = {r["id"]: (r["x"], r["y"]) for r in fr.evaluate("__office.rooms()")}
    page.close()
    page2, fr2 = open_page(last)                       # the JSON the page itself wrote, not a hand-made one
    rooms2 = {r["id"]: (r["x"], r["y"]) for r in fr2.evaluate("__office.rooms()")}
    check("a new window that reads that JSON puts every room at the same place", rooms2 == rooms1 and not fr2.evaluate("__office.arr.state().on"), (rooms1, rooms2))
    fr2.click("#arrange")
    fr2.wait_for_function("__office.arr.state().on")
    page2.wait_for_timeout(400)
    fr2.click('#arrBar [data-act="reset"]')
    page2.wait_for_function("window.__saved.some(p => !('arrange' in p))", timeout=8000)
    last2 = page2.evaluate("window.__saved[window.__saved.length - 1]")
    check("Reset saves the prefs without `arrange`, every other key kept", "arrange" not in last2 and all(last2.get(k) == v for k, v in OTHER_KEYS.items()), last2)
    check("no page error", not errors, errors[:5])
    page2.close()


def static_checks():
    print("the file itself")
    src = (HERE / "web" / "office_arrange.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    cjk = re.compile(r"[一-鿿]")
    stray = [ln.strip()[:100] for ln in code.splitlines() if cjk.search(ln) and not re.search(r"\bT\(", ln)]
    check("every Chinese string in office_arrange.js is the second argument of T(en, zh) (a line with Chinese and no T( call is a leak)", not stray, stray[:3])
    html = (HERE / "web" / "office.html").read_text(encoding="utf-8")
    check("office.html only calls into it (the new logic is in its own file)", "<script src=\"office_arrange.js\"></script>" in html and "ARRG.place(" in html)
    api = (HERE / "api_office.py").read_text(encoding="utf-8")
    check("api_office.PREF_KEYS lists `arrange` (a key not listed is silently dropped)", '"arrange"' in api.split("PREFS_MAX")[0])


H.SHOTS.mkdir(exist_ok=True)
static_checks()
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
                run_real(browser, base, engine)
            finally:
                browser.close()
    finally:
        srv.shutdown()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
