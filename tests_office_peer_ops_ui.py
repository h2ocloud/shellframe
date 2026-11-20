#!/usr/bin/env python3
"""A paired computer's ops board in the office: the small TV on each Next-door room's back wall, in the real page against a
stand-in Python side (ops_peer / ops_peer_view / ops_peer_live answer like api_ops.py). Demo data only (示範電腦 …).

  - this computer's ops board off (no `ops` object): no small TVs, nothing to hit, and ops_peer is never asked
  - on: every room has one; it is asked once at open, for each computer, and not again inside the minute
  - the faces: ok (green, the three readings and the disk line) and alert (red banner with the reason) are told apart by pixels;
    off / older / out of reach / no data are grey (no red, yellow or green) and say why in words: 「那台沒打開維運戰情」
    「那台版本較舊」「連不上」「還沒有資料」; a computer that has not answered yet says 「讀取中…」
  - a blip keeps the last good face (150 s), a lasting failure turns it grey
  - the place: hung above the desks' monitors (its frame's bottom above where a monitor's top lands on the wall, its top inside the
    wall), right of the whiteboard and its red count badge, left of the rack; it overlaps none of the whiteboard (with the badge), the
    rack, the door, a window, a desk or a monitor on screen (3D and flat) in a three-seat, a five-seat and a nine-seat (two-row-plus) room
  - hovering it: the pointer and the tooltip 「維運戰情 · <name>」; a click opens the same window titled 「維運戰情 · <name>」, read through
    ops_peer_view, kept live with ops_peer_live (on, a heartbeat, off at Esc / ✕ / a click outside / another window), and never
    through this computer's own office_object_view / office_object_live
  - the window's rules are shown and cannot be changed (switches and numbers disabled, the note 「要改請到那台電腦」, no reset, no
    靜音 button, no 調整規則 button); a click on one sends nothing; the top five's order switch works and is sent to that computer
  - Arrange positions: the small TV moves with its room
  - English: the window, the tooltip and every string on the small TVs have no Chinese in them
Headless Chromium and WebKit when playwright has it. Screenshots (Chromium, zh-TW): qa-shots/office-peer-ops-{ok,alert,old}.png (the small TV close up) and office-peer-ops-window.png.
Run: .venv/bin/python tests_office_peer_ops_ui.py
"""
import json
import pathlib
import re
import sys
import time
import types

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
        print(f"  [FAIL] {name}  {str(detail)[:600]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_office_peer_ops_ui.py（沒裝 playwright）")
    sys.exit(0)

_osrc = (HERE / "tests_office_ops_ui.py").read_text(encoding="utf-8")
O = types.ModuleType("office_ops_harness")
O.__file__ = str(HERE / "tests_office_ops_ui.py")
exec(compile(_osrc[:_osrc.index("\ndef run(")], O.__file__, "exec"), O.__dict__)   # the ops test's stand-in objects and views, not its run
H = O.H
SHOTS = H.SHOTS
SHOTS.mkdir(exist_ok=True)
NOW = int(time.time())
CJK = re.compile(r"[一-鿿　-〿＀-￯]")


def bot(role, label, shape, color, primary=False):
    return {"role": role, "label": label, "state": "awake", "primary": primary, "sid": "r" + role, "activity": "idle",
            "avatar": {"shape": shape, "color": color}, "responsibility": "示範工作"}


def peer(name, n=1):
    shapes = [("round", "teal"), ("egg", "pink"), ("ghost", "green"), ("hex", "amber"), ("star", "purple")]
    return {"name": name, "addr": name, "primary": "b0",
            "bots": [bot(f"b{i}", f"{name[-1]}{i + 1}號", *shapes[i % 5], primary=i == 0) for i in range(n)], "tabs": {}}


PIDS = ["pid-a", "pid-b", "pid-c", "pid-d", "pid-e", "pid-f"]
PEERS = {"peers": {"pid-a": peer("示範電腦甲", 3), "pid-b": peer("示範電腦乙", 5), "pid-c": peer("示範電腦丙"),
                   "pid-d": peer("示範電腦丁"), "pid-e": peer("示範電腦戊"), "pid-f": peer("示範電腦己", 9)}}
FACE = {"cpu": 23.5, "mem": 61.0, "pressure": "normal", "net_in": 1200000, "net_out": 30000, "online": True, "disk_free_pct": 42.3, "spark": [55, 57, 60, 61]}


def summ(level="ok", reason="", face=True, **kw):
    d = {"success": True, "level": level, "reason": reason, "since": NOW - 720 if reason else None, "muted_until": None, "updated": NOW - 20,
         "face": dict(FACE, mem=93.0, pressure="critical") if face and level == "alert" else (dict(FACE) if face else None)}
    d.update(kw)
    return d


ANSWERS = {"pid-a": summ(), "pid-b": summ("alert", "記憶體壓力高 12 分鐘"), "pid-c": summ("off", face=False), "pid-d": {"success": False, "code": "old"},
           "pid-e": {"success": False, "code": "offline"}, "pid-f": summ("none", face=False)}
PEER_VIEW = O.view("ok", "zh")
for _s in PEER_VIEW["sections"]:
    for _w in _s["widgets"]:
        if _w["kind"] == "rules":
            _w["readonly"] = True
PEER_VIEW["banner"] = {"level": "ok", "text": "一切正常", "since": None, "rule": ""}
PEER_VIEW_EN = O.view("ok", "en")
for _s in PEER_VIEW_EN["sections"]:
    for _w in _s["widgets"]:
        if _w["kind"] == "rules":
            _w["readonly"] = True

PEER_PATCH = """
if (window.top === window) {
  const base = window.pywebview.api;
  const P = window.__peer = { answers: %s, view: %s, viewEn: %s, ask: [], views: [], live: [], delay: 0 };
  const R = v => Promise.resolve(JSON.stringify(v));
  window.pywebview.api = new Proxy({}, { get: (t, prop) => {
    if (prop === 'then') return undefined;
    switch (prop) {
      case 'ops_peer': return (pid, lang) => { P.ask.push([pid, lang, Date.now()]); let a = P.answers[pid]; if (a && lang === 'en' && a.reason) a = Object.assign({}, a, {reason: 'Memory pressure high for 12 minutes'}); return a === 'throw' ? Promise.reject(new Error('x')) : R(a || {success: false, code: 'offline'}); };
      case 'ops_peer_view': return (pid, lang, sort) => { P.views.push([pid, lang, sort]); const an = P.answers[pid]; if (an && an.success === false) return R(an); const v = JSON.parse(JSON.stringify(lang === 'en' ? P.viewEn : P.view)); const t = (v.sections.find(s => s.id === 'top') || {widgets: []}).widgets[0]; if (t && t.switch && sort) t.switch.value = sort; return R(v); };
      case 'ops_peer_live': return (pid, on) => { P.live.push([pid, !!on]); return R({success: true}); };
    }
    return base[prop];
  }});
}
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (ANSWERS, PEER_VIEW, PEER_VIEW_EN))

PEER_PIX = O.TV_PIX.replace("__office.opsTv()", "__office.peerTvScreen(window.__pid)")
CAPTURE = """async (pid) => { const out = [], f = CanvasRenderingContext2D.prototype.fillText, ops = __office.ops(), draw = ops.drawPeer, y = __office.peerWall(pid).room.y;
  ops.drawPeer = function (x, g, o) { if (Math.abs(g.q[1] - y) > 1e-6) return draw.call(this, x, g, o);
    CanvasRenderingContext2D.prototype.fillText = function (s) { out.push(String(s)); return f.apply(this, arguments); };
    try { return draw.call(this, x, g, o); } finally { CanvasRenderingContext2D.prototype.fillText = f; } };
  await new Promise(r => setTimeout(r, 450)); ops.drawPeer = draw; return out; }"""


def hull(pts):
    pts = sorted({(round(p["x"], 3), round(p["y"], 3)) for p in pts})
    cr = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cr(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def convex_overlap(a, b):
    """How far two convex polygons (lists of (x, y)) overlap on screen, in px: 0 when they only touch or are apart."""
    best = 1e9
    for poly in (a, b):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = -(y2 - y1), (x2 - x1)
            n = (nx * nx + ny * ny) ** .5
            nx, ny = nx / n, ny / n
            pa, pb = [nx * p[0] + ny * p[1] for p in a], [nx * p[0] + ny * p[1] for p in b]
            ov = min(max(pa), max(pb)) - max(min(pa), min(pb))
            if ov <= 0:
                return 0.0
            best = min(best, ov)
    return best


def new_page(browser, errors, lang="zh", on=True, answers=None, dpr=1):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=dpr)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    H.PEERS = PEERS
    page.add_init_script(H.stub())
    if lang == "en":
        page.add_init_script("if (window.top === window) window.__cfg.settings.language = 'en';")
    page.add_init_script(O.PATCH)
    page.add_init_script(PEER_PATCH)
    page.add_init_script("""if (window.top === window) { window.__fake.objects = %s; window.__fake.view = %s; window.__fake.state = %s; window.__peer.answers = Object.assign(window.__peer.answers, %s); }""" % (
        json.dumps([O.BOOKSHELF, O.PERSONNEL] + ([O.decl("ok", "", lang)] if on else []), ensure_ascii=False),
        json.dumps({"zh": O.view("ok", "zh"), "en": O.view("ok", "en")}, ensure_ascii=False),
        json.dumps({"on": on, "level": "ok", "reason": "", "since": None}), json.dumps(answers or {}, ensure_ascii=False)))
    page.goto(base_url[0] + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    page.wait_for_function(f"Object.keys(SFBots.peerBots()).length === {len(PIDS)}", timeout=20000)
    return page


base_url = [""]


def open_office(page, pid="pid-a", wait_asked=True):
    if page.evaluate("SFOffice.isOpen()"):
        page.evaluate("SFOffice.close()")
    page.evaluate("SFOffice.open()")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && f.contentWindow.__office.rooms().filter(r => r.id.startsWith('peer:')).length === %d; } catch (e) { return false; } }""" % len(PIDS), timeout=20000)
    if wait_asked:
        page.wait_for_function("window.__peer.ask.length >= %d" % len(PIDS), timeout=15000)
        page.wait_for_timeout(500)
    fr = H.office(page)
    fr.evaluate("__office.pause(true); __office.setMode('iso', true)")
    goto_room(page, fr, pid)
    return fr


def goto_room(page, fr, pid):
    fr.evaluate("(pid) => __office.setArea('peer:' + pid, true)", pid)
    fr.wait_for_function("() => { const w = __office.busyWhy(); return !w.cam && w.rooms.length === 0; }", timeout=8000)
    page.wait_for_timeout(350)


def tv_pix(page, fr, pid):
    fr.evaluate("(p) => { window.__pid = p; }", pid)
    return fr.evaluate(PEER_PIX)


def win(fr):
    return O.win(fr)


def run(browser, base, engine):
    shoot = engine == "chromium"
    base_url[0] = base
    errors = []

    # ── 1. this computer's ops board is off ──
    print(f"[{engine}] this computer's ops board off: no small TVs, nobody asked")
    page = new_page(browser, errors, on=False)
    fr = open_office(page, wait_asked=False)
    page.wait_for_timeout(800)
    check("no small TV in any room, nothing to hit", all(fr.evaluate("(p) => __office.peerTvScreen(p)", p) is None for p in PIDS)
          and fr.evaluate("__office.wallPoint('tv:pid-a')") is None)
    check("ops_peer was never asked", page.evaluate("window.__peer.ask.length") == 0, page.evaluate("window.__peer.ask"))
    page.context.close()

    # ── 2. on: asked once each, the faces ──
    print(f"[{engine}] on: asked once a minute, the faces")
    page = new_page(browser, errors)
    fr = open_office(page)
    ask = page.evaluate("window.__peer.ask")
    check("every computer is asked once at open, in the page's language", sorted(a[0] for a in ask) == PIDS and all(a[1] == "zh" for a in ask), ask)
    page.wait_for_timeout(7000)
    check("…and not again within the minute (the 3 s tick does not ask)", page.evaluate("window.__peer.ask.length") == len(PIDS), page.evaluate("window.__peer.ask.length"))
    check("every room has a small TV", all(fr.evaluate("(p) => __office.peerTvScreen(p)", p) is not None for p in PIDS))

    pix, words = {}, {}
    for pid in PIDS:
        goto_room(page, fr, pid)
        pix[pid] = tv_pix(page, fr, pid)
        words[pid] = fr.evaluate(CAPTURE, pid)
    print("     pixels:", {k: (v["red"], v["yel"], v["grn"]) for k, v in pix.items()})
    print("     words:", {k: sorted(set(v)) for k, v in words.items()})
    check("ok: green is on it, no red banner", pix["pid-a"]["grn"] > 10 and pix["pid-a"]["red"] < 20, pix["pid-a"])
    check("alert: a red banner", pix["pid-b"]["red"] > 5 * max(3, pix["pid-a"]["red"]) and pix["pid-b"]["red"] > 80, (pix["pid-b"], pix["pid-a"]))
    for pid in ("pid-c", "pid-d", "pid-e", "pid-f"):
        check(f"{pid} is grey: no red, yellow or green", pix[pid]["red"] < 12 and pix[pid]["yel"] < 12 and pix[pid]["grn"] < 12, pix[pid])
    check("ok says its readings: CPU, memory, network and the disk line", {"CPU", "記憶體", "網路"} <= set(words["pid-a"]) and any("磁碟剩 42%" == w for w in words["pid-a"])
          and "一切正常" in words["pid-a"], words["pid-a"])
    check("alert says why", "記憶體壓力高 12 分鐘" in words["pid-b"], words["pid-b"])
    check("off: 「那台沒打開維運戰情」", "那台沒打開維運戰情" in words["pid-c"], words["pid-c"])
    check("older: 「那台版本較舊」", "那台版本較舊" in words["pid-d"], words["pid-d"])
    check("out of reach: 「連不上」", "連不上" in words["pid-e"], words["pid-e"])
    check("no data: 「還沒有資料」", "還沒有資料" in words["pid-f"], words["pid-f"])
    if shoot:                                  # the three small TVs close up (a 2x page, clipped around the TV)
        sp = new_page(browser, errors, dpr=2)
        sfr = open_office(sp, "pid-a")
        for pid, name in (("pid-a", "ok"), ("pid-b", "alert"), ("pid-d", "old")):
            goto_room(sp, sfr, pid)
            sp.mouse.move(5, 120)
            sp.wait_for_timeout(500)
            q = sfr.evaluate("(p) => __office.peerTvScreen(p)", pid)
            xs, ys = [v["x"] for v in q], [v["y"] for v in q]
            x0, y0 = max(0, min(xs) - 70), max(60, min(ys) - 50)
            sp.screenshot(path=str(SHOTS / f"office-peer-ops-{name}.png"), clip={"x": x0, "y": y0, "width": min(1280 - x0, max(xs) - min(xs) + 140), "height": min(800 - y0, max(ys) - min(ys) + 100)})
        sp.context.close()

    # a pending computer, then a blip that keeps the face, then a lasting failure
    print(f"[{engine}] pending, a blip, a lasting failure")
    pg2 = new_page(browser, errors)
    pg2.evaluate("window.__peer.delay = 1")
    pg2.evaluate("""() => { const api = window.pywebview.api; window.pywebview.api = new Proxy({}, { get: (t, p) => p === 'ops_peer' ? (pid, l) => new Promise(r => { window.__hold = r; window.__peer.ask.push([pid, l]); }) : api[p] }); }""")
    pg2.evaluate("SFOffice.open()")
    pg2.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.rooms().filter(r => r.id.startsWith('peer:')).length === %d; } catch (e) { return false; } }""" % len(PIDS), timeout=20000)
    pg2.wait_for_timeout(900)
    fr2 = H.office(pg2)
    fr2.evaluate("__office.pause(true); __office.setMode('iso', true); __office.setArea('peer:pid-a', true)")
    pg2.wait_for_timeout(1100)
    w = fr2.evaluate(CAPTURE, "pid-a")
    check("a computer that has not answered yet: 「讀取中…」 (grey, no data)", "讀取中…" in w, w)
    pg2.context.close()

    print(f"[{engine}] the flat view (a low, wide screen): the same faces, compact")
    fr.evaluate("__office.setMode('flat', true)")
    fw, fp = {}, {}
    for pid in ("pid-a", "pid-b", "pid-c", "pid-d"):
        goto_room(page, fr, pid)
        fw[pid] = set(fr.evaluate(CAPTURE, pid))
        fp[pid] = tv_pix(page, fr, pid)
    check("flat, ok: the three readings in a row (CPU 24%, 記憶體 61%, ↓ 1.2 MB/s), green bar", {"CPU 24%", "記憶體 61%", "↓ 1.2 MB/s"} <= fw["pid-a"] and fp["pid-a"]["red"] < 20, (fw["pid-a"], fp["pid-a"]))
    check("flat, alert: the banner with the reason, red", "記憶體壓力高 12 分鐘" in fw["pid-b"] and fp["pid-b"]["red"] > 60, (fw["pid-b"], fp["pid-b"]))
    check("flat, off and older: grey, the reason in words", "那台沒打開維運戰情" in fw["pid-c"] and "那台版本較舊" in fw["pid-d"] and fp["pid-c"]["red"] < 12 and fp["pid-d"]["grn"] < 12, (fw["pid-c"], fw["pid-d"]))
    fr.evaluate("__office.setMode('iso', true)")

    # ── 3. the place ──
    print(f"[{engine}] the place: above the monitors, clear of the whiteboard (and its badge), the rack, the door, the windows and the desks")
    SCENE = """({ p, n, iso }) => { const g = __office.peerWall(p), r = g.room, P = (x, y, z) => __office.proj(x, y, z), box = f => { const pts = []; for (const x of [f.x0, f.x1]) for (const y of [f.y0, f.y1]) for (const z of [f.z0, f.z1]) pts.push(P(x, y, z)); return pts; };
      const unit = Math.hypot(P(0, 0, 1).x - P(0, 0, 0).x, P(0, 0, 1).y - P(0, 0, 0).y) / (iso ? 1 : .5);     // the camera zoom: the page draws its badges 7.4 px in the world
      const wallQ = (q, dx, dz) => [[q[0] - dx, q[6] - dz], [q[2] + dx, q[6] - dz], [q[2] + dx, q[7] + dz], [q[0] - dx, q[7] + dz]].map(([x, z]) => P(x, r.y, z));
      const circle = (cx, cz, rad) => { const c = P(cx, r.y, cz); return [...Array(16).keys()].map(i => ({ x: c.x + rad * Math.cos(i * Math.PI / 8), y: c.y + rad * Math.sin(i * Math.PI / 8) })); };
      const seats = [...Array(n).keys()].flatMap(i => { const big = i === 0, lx = .9 + (i % 4) * 2.5, ly = .6 + Math.floor(i / 4) * 2.4, dh = big ? 22 : 20, mw = big ? 1.1 : .78, mx = r.x + lx + .75 - mw / 2;     // a desk and its monitor, as office.html draws them
        return [{ k: 'desk', x0: r.x + lx, x1: r.x + lx + 1.5, y0: r.y + ly, y1: r.y + ly + .8, z0: 0, z1: dh }, { k: 'mon', x0: mx, x1: mx + mw, y0: r.y + ly + .13, y1: r.y + ly + .21, z0: dh + 5, z1: dh + 23 }]; });
      return { tv: __office.peerTvScreen(p), tvFrame: wallQ(g.tv, .07, 1.6), badge: circle(g.tv[2], g.tv[7] + 2, 7.4 * unit),
        rack: box({ x0: g.rack[0].x, x1: g.rack[0].x + g.rack[0].w, y0: g.rack[0].y, y1: g.rack[0].y + g.rack[0].h, z0: 0, z1: 50 }),
        door: box({ x0: r.x + 1.2, x1: r.x + 2.3, y0: r.y, y1: r.y + .2, z0: 0, z1: r.wallH - 14 }),
        board: wallQ(g.board, 0, 0), boardBadge: circle(g.board[2], g.board[7] - 1, 7.4 * unit),
        wins: __office.peerWindows(p).map(([a, b]) => [[a, 14.4], [b, 14.4], [b, 32.8], [a, 32.8]].map(([x, z]) => P(x, r.y, z))),
        seats: seats.map(s => ({ k: s.k, pts: box(s) })) }; }"""
    for pid, label, n in (("pid-a", "three seats", 3), ("pid-b", "five seats", 5), ("pid-f", "nine seats", 9)):
        for mode in ("iso", "flat"):
            fr.evaluate("(m) => __office.setMode(m, true)", mode)
            goto_room(page, fr, pid)
            g = fr.evaluate("(p) => __office.peerWall(p)", pid)
            r, tv, bd = g["room"], g["tv"], g["board"]
            rack = g["rack"][0]
            if mode == "iso":
                check(f"{label}: right of the whiteboard's badge (>= 0.35), left of the rack's covered stretch, right of the door, inside the wall",
                      tv[0] >= bd[2] + .35 - 1e-9 and tv[2] <= r["x"] + r["w"] - 1.7 + 1e-9 and tv[0] >= r["x"] + 2.3 + .1 and 0 <= tv[6] < tv[7] <= r["wallH"], (tv, bd, rack, r))
                check(f"{label}: hung above the monitors (frame bottom z >= 22.5, frame top <= the wall's height; a monitor's top lands at z 19.6–21.6 on the wall)",
                      tv[6] - 1.6 >= 22.5 and tv[7] + 1.6 <= r["wallH"] + .2, tv)
            sc = fr.evaluate(SCENE, {"p": pid, "n": n, "iso": mode == "iso"})
            tvh = hull(sc["tv"] + sc["tvFrame"] + sc["badge"])
            bad = {"board": convex_overlap(tvh, hull(sc["board"] + sc["boardBadge"])), "rack": convex_overlap(tvh, hull(sc["rack"])), "door": convex_overlap(tvh, hull(sc["door"]))}
            for i, w in enumerate(sc["wins"]):
                bad[f"window{i}"] = convex_overlap(tvh, hull(w))
            for i, s in enumerate(sc["seats"]):
                bad[f"{s['k']}{i // 2}"] = convex_overlap(tvh, hull(s["pts"]))
            bad = {k: round(v, 2) for k, v in bad.items() if v > .5}
            check(f"{label}, {mode}: on screen the TV (frame and alert badge too) overlaps none of the whiteboard and its badge, the rack, the door, a window, a desk or a monitor (> 0.5 px)", not bad, bad)
            above = [i // 2 for i, s in enumerate(sc["seats"]) if s["k"] == "mon" and min(p["x"] for p in s["pts"]) < max(p["x"] for p in sc["tv"]) and max(p["x"] for p in s["pts"]) > min(p["x"] for p in sc["tv"])
                     and sum(p["y"] for p in sc["tv"]) / 4 >= sum(p["y"] for p in s["pts"]) / len(s["pts"])]
            check(f"{label}, {mode}: every monitor beside it on screen sits below it (the TV is above the monitors' tops)", not above, above)
    fr.evaluate("__office.setMode('iso', true)")
    goto_room(page, fr, "pid-a")

    # ── 4. hover and click ──
    print(f"[{engine}] the click opens the window")
    pt = fr.evaluate("__office.wallPoint('tv:pid-a')")
    check("the small TV is a hit target", pt is not None, pt)
    page.mouse.move(pt["x"], pt["y"])
    page.wait_for_timeout(300)
    check("hover: the pointer cursor and the tooltip 「維運戰情 · 示範電腦甲」", fr.evaluate("__office.hoverTip()") == "維運戰情 · 示範電腦甲"
          and fr.evaluate("document.getElementById('stage').classList.contains('pt')"), fr.evaluate("__office.hoverTip()"))
    page.evaluate("window.__fake.live = []; window.__fake.views = []; window.__fake.acts = []; window.__peer.live = []; window.__peer.views = []")
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.getElementById('opspop') && !document.querySelector('#sfo-overlay iframe').contentDocument.getElementById('opspop').hidden", timeout=6000)
    page.wait_for_timeout(700)
    w = win(fr)
    check("the window is titled 「維運戰情 · 示範電腦甲」 with all eight kinds of widget", w and w["title"] == "維運戰情 · 示範電腦甲" and all(k >= 1 for k in w["kinds"]), w and (w["title"], w["kinds"]))
    check("it was read through ops_peer_view (this computer's own view and live were not touched)", page.evaluate("window.__peer.views.length") >= 1
          and page.evaluate("window.__peer.views[0][0]") == "pid-a" and page.evaluate("window.__peer.views[0][1]") == "zh"
          and page.evaluate("window.__fake.views.length") == 0 and page.evaluate("window.__fake.live.length") == 0, (page.evaluate("window.__peer.views"), page.evaluate("window.__fake.views")))
    check("…and live on for that computer", page.evaluate("window.__peer.live[0]") == ["pid-a", True], page.evaluate("window.__peer.live"))
    check("the rules are shown but cannot be changed: every switch and number is disabled, the note says where",
          fr.evaluate("""() => { const v = document.getElementById('opspop'), sw = [...v.querySelectorAll('.op-sw')], nu = [...v.querySelectorAll('.op-num')];
            return sw.length > 0 && nu.length > 0 && sw.every(b => b.disabled) && nu.every(b => b.disabled) && v.innerText.includes('要改請到那台電腦'); }"""))
    check("…no 靜音 button, no 調整規則 button, no reset", fr.evaluate("""() => { const t = document.getElementById('opspop').innerText; return !t.includes('靜音') && !t.includes('調整規則') && !t.includes('回到預設規則') && !document.querySelector('#opspop [data-op=\"mute\"], #opspop [data-op=\"reset\"], #opspop [data-op=\"rules\"]'); }"""))
    fr.evaluate("""() => { const b = document.querySelector('#opspop .op-sw'); b.click(); const n = document.querySelector('#opspop .op-num'); n.value = '99'; n.dispatchEvent(new Event('change', {bubbles: true})); }""")
    page.wait_for_timeout(400)
    check("clicking a disabled switch and changing a number sends nothing anywhere", page.evaluate("window.__fake.acts.length") == 0)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-peer-ops-window.png"))
    # the sort switch
    before = page.evaluate("window.__peer.views.length")
    fr.evaluate("document.querySelector('#opspop [data-op=\"switch\"][data-val=\"cpu\"]').click()")
    page.wait_for_timeout(900)
    last = page.evaluate("window.__peer.views[window.__peer.views.length - 1]")
    check("the top five's order switch: the next read to that computer carries sort=cpu, and the button is on",
          page.evaluate("window.__peer.views.length") > before and last == ["pid-a", "zh", "cpu"]
          and fr.evaluate("document.querySelector('#opspop [data-op=\"switch\"][data-val=\"cpu\"]').getAttribute('aria-pressed')") == "true", last)
    check("…and nothing went to this computer's own actions", page.evaluate("window.__fake.acts.length") == 0)
    page.evaluate("window.__peer.live = []")
    page.wait_for_timeout(1500)
    check("the window keeps reading at its own pace (refresh_ms 600)", page.evaluate("window.__peer.views.length") >= before + 3, page.evaluate("window.__peer.views.length"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    check("Esc closes it and says live off for that computer", win(fr) is None and page.evaluate("window.__peer.live[window.__peer.live.length - 1]") == ["pid-a", False], page.evaluate("window.__peer.live"))
    fr.evaluate("(p) => __office.openView('tv:' + p)", "pid-a")
    page.wait_for_timeout(500)
    check("openView('tv:<pid>') opens it again; a second press on the same TV closes it", win(fr) is not None)
    fr.evaluate("(p) => __office.openView('tv:' + p)", "pid-a")
    page.wait_for_timeout(400)
    check("…closed", win(fr) is None and page.evaluate("window.__peer.live[window.__peer.live.length - 1]") == ["pid-a", False])
    fr.evaluate("(p) => __office.openView('tv:' + p)", "pid-a")
    page.wait_for_timeout(500)
    fr.evaluate("(p) => __office.openView('tv:' + p)", "pid-c")
    page.wait_for_timeout(500)
    ww = win(fr)
    check("another computer's TV takes over: titled with its name, the first one is told live off, the second live on",
          ww and ww["title"] == "維運戰情 · 示範電腦丙" and ["pid-a", False] in page.evaluate("window.__peer.live") and page.evaluate("window.__peer.live[window.__peer.live.length - 1]") == ["pid-c", True], (ww and ww["title"], page.evaluate("window.__peer.live")))
    fr.evaluate("document.getElementById('opspop').click()")
    page.wait_for_timeout(400)
    check("a click outside closes it (live off for that one)", win(fr) is None and page.evaluate("window.__peer.live[window.__peer.live.length - 1]") == ["pid-c", False])
    fr.evaluate("document.getElementById('opsBtn').click()")
    page.wait_for_timeout(500)
    wl = win(fr)
    check("this computer's own button still opens this computer's window (its title, its own calls)", wl and wl["title"] == "維運戰情" and page.evaluate("window.__fake.live.length") >= 1
          and "靜音" in fr.evaluate("document.getElementById('opspop').innerText"), wl and wl["title"])
    fr.evaluate("(p) => __office.openView('tv:' + p)", "pid-a")
    page.wait_for_timeout(500)
    check("…and a small TV replaces it (this computer's window is told live off)", win(fr)["title"] == "維運戰情 · 示範電腦甲" and page.evaluate("window.__fake.live[window.__fake.live.length - 1][1]") is False, page.evaluate("window.__fake.live"))
    fr.evaluate("__office.ops().close()")
    page.wait_for_timeout(300)

    # a failure inside the window
    print(f"[{engine}] a window for a computer that cannot be read")
    page.evaluate("window.__peer.answers['pid-e'] = {success: false, code: 'offline'}")
    fr.evaluate("(p) => __office.openView('tv:' + p)", "pid-e")
    page.wait_for_timeout(600)
    check("a computer that cannot be read: the window says it in words (not a blank)", "連不上" in fr.evaluate("document.getElementById('opspop').innerText"), fr.evaluate("document.getElementById('opspop').innerText"))
    fr.evaluate("__office.ops().close()")

    # ── 5. the TV follows its room in Arrange positions ──
    print(f"[{engine}] Arrange positions: the small TV moves with its room")
    fr.evaluate("__office.setArea('all', true)")
    page.wait_for_timeout(900)
    rel = lambda pid: fr.evaluate("(p) => { const g = __office.peerWall(p); return { dx: +(g.tv[0] - g.room.x).toFixed(3), dy: +(g.tv[1] - g.room.y).toFixed(3), x: g.room.x, y: g.room.y, w: g.room.w }; }", pid)
    a0 = rel("pid-b")
    fr.click("#arrange")
    fr.wait_for_function("__office.arr.state().on")
    page.wait_for_timeout(600)
    pts = fr.evaluate("() => ({ h: __office.arr.handlePoint('pid-b'), r: __office.arr.roomPoint('pid-b'), t: __office.arr.cellPoint(-1, 2) })")
    page.mouse.move(pts["h"]["x"], pts["h"]["y"])
    page.mouse.down()
    for i in range(1, 9):
        page.mouse.move(pts["h"]["x"] + (pts["t"]["x"] - pts["r"]["x"]) * i / 8, pts["h"]["y"] + (pts["t"]["y"] - pts["r"]["y"]) * i / 8)
    page.mouse.up()
    page.wait_for_timeout(900)
    a1 = rel("pid-b")
    check("the room moved, and the TV is at the same place in it (same offset from the room's corner)", (a1["x"], a1["y"]) != (a0["x"], a0["y"]) and a1["dx"] == a0["dx"] and a1["dy"] == a0["dy"], (a0, a1))
    others = {p: rel(p) for p in PIDS if p != "pid-b"}
    check("…every other room's TV is also right of its board's end by the same gap", all(abs(v["dx"] - (2.75 + min(3.6, max(2.6, v["w"] - 6.2)) + .35)) < 1e-6 and v["dy"] == 0 for v in others.values()), others)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # ── 6. a blip keeps the face, a lasting failure turns it grey ──
    print(f"[{engine}] a blip keeps the last face, a lasting failure turns it grey")
    page.close()
    page = new_page(browser, errors)
    page.evaluate("SFOffice.peerOpsTiming.every = 1200; SFOffice.peerOpsTiming.keep = 4")
    fr = open_office(page)
    page.evaluate("window.__peer.answers['pid-a'] = {success: false, code: 'offline'}; window.__peer.answers['pid-c'] = {success: true, level: 'ok', reason: '', face: null, updated: %d}" % NOW)
    page.wait_for_function("window.__peer.ask.length >= %d" % (2 * len(PIDS)), timeout=15000)
    page.wait_for_timeout(500)
    w = fr.evaluate(CAPTURE, "pid-a")
    check("one failed ask: the last good face stays (the readings, not 「連不上」)", "CPU" in w and "連不上" not in w, w)
    page.wait_for_timeout(6000)
    w = fr.evaluate(CAPTURE, "pid-a")
    check("still failing past the keep time: grey, 「連不上」", "連不上" in w and "CPU" not in w, w)
    page.evaluate("window.__peer.answers['pid-a'] = %s" % json.dumps(ANSWERS["pid-a"]))
    page.wait_for_timeout(2500)
    check("it answers again: the face is back", "CPU" in fr.evaluate(CAPTURE, "pid-a"))
    page.context.close()

    # ── 7. English ──
    print(f"[{engine}] English")
    page = new_page(browser, errors, lang="en")
    fr = open_office(page)
    en = []
    for pid in PIDS:
        goto_room(page, fr, pid)
        en += fr.evaluate(CAPTURE, pid)
    check("every string on the small TVs is English (no Chinese)", en and not [w for w in en if CJK.search(w)], [w for w in en if CJK.search(w)])
    goto_room(page, fr, "pid-a")
    pt = fr.evaluate("__office.wallPoint('tv:pid-a')")
    page.mouse.move(pt["x"], pt["y"])
    page.wait_for_timeout(300)
    tip = fr.evaluate("__office.hoverTip()")
    check("the tooltip is English", tip == "Ops board · 示範電腦甲".replace("示範電腦甲", tip.split("· ")[-1]) and tip.startswith("Ops board · "), tip)
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(900)
    w = win(fr)
    check("the window is English: title, the note, no Chinese but the computer's name",
          w and w["title"].startswith("Ops board · ") and "Change these on that computer." in w["text"] and not CJK.search(w["text"].replace(w["title"].split("· ")[-1], "")), w and w["text"][:300])
    check("…and it asked for English", page.evaluate("window.__peer.views[0][1]") == "en")
    page.context.close()
    check("no page error", not errors, errors[:5])


srv = H.serve()
base = f"http://127.0.0.1:{srv.server_port}"
with sync_playwright() as pw:
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
srv.shutdown()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
