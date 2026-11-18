#!/usr/bin/env python3
"""The office's task board as a whiteboard: sticky notes by priority (輕重緩急) on a centred sheet.

Drives web/office.html standalone (demo data only, made-up names), headless Chromium and WebKit when
playwright has it. The demo clock is paused so the people on the floor do not change under the checks.

  - notePriority(t, now) is pure and derived: P1 red = a decision or overdue, P2 orange = stuck or due
    within 24 h, P3 yellow = doing / waiting, P4 blue = todo; no due date is not overdue; boundaries
  - four lanes 緊急 / 重要 / 進行中 / 不急 with counts; the header subtitle sums them up; every task once
  - a note's tilt comes from its id: the same on every re-render; the DOM is not rebuilt by a snapshot
    (a note node survives three snapshots, so does an open card), but is when the data changes or when a
    task crosses into another lane by the clock alone
  - 0 / 1 / 45 notes; a very long title is clamped (the full title is in the title attribute)
  - a note opens a card (title in full, 負責／交辦自／在等／下一步／期限／動靜, history) centred in the sheet
    even when the board is scrolled; ✕, a click outside the card, and Esc close it (Esc: card, then sheet);
    the card's action slot is there and empty; an owner's name follows that person
  - the sticky notes on the wall use the lane colours in the lane order
  - a narrow window stacks the lanes, nothing spills sideways; no console error
Screenshots (Chromium, --ui 1 and 1.18): qa-shots/office-whiteboard-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_whiteboard_ui.py
"""
import functools
import http.server
import pathlib
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
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_office_whiteboard_ui.py（沒裝 playwright）")
    sys.exit(0)

HEX = ["#ff8a8a", "#ffb067", "#ffe066", "#9fd6f2"]


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# builds a board in the page from specs: {id, title, status, due_in (s from now or None), stuck, decision, owner, ago (min)}
MK = """(specs) => { const now = Date.now() / 1000, B = { decisions: [], stuck: [], open: [] };
  for (const s of specs) { const k = v => (v === 'user' ? 'user' : v ? 'b:' + v : '');
    const t = { id: s.id, title: s.title || ('示範任務 ' + s.id), kind: s.decision ? 'decision' : 'work', status: s.status || 'doing', owner: s.owner || 'eng', requester: s.requester || 'sec',
      waiting_on: s.waiting_on || '', next_step: s.next_step || '', due_at: s.due_in == null ? 0 : now + s.due_in, updated_at: now - (s.ago || 10) * 60, idle_s: s.stuck ? 90000 : 0 };
    Object.assign(t, { owner_key: k(t.owner), requester_key: k(t.requester), waiting_key: k(t.waiting_on) });
    if (s.decision) { t.decision = true; B.decisions.push(t); } else if (s.stuck) { t.stuck = true; B.stuck.push(t); } else B.open.push(t); }
  __office.setBoard(B); return specs.length; }"""


def put(page, specs):
    page.evaluate(MK, specs)
    page.wait_for_timeout(450)


def many():
    out = []
    n = 100
    for i in range(5):
        out.append(dict(id=n + i, decision=True, owner="user", title=f"示範要你決定的事 {i}"))
    for i in range(5):
        out.append(dict(id=n + 10 + i, due_in=-3600 * (i + 1), title="示範逾期的任務 " + "很長的標題" * (i * 8)))
    for i in range(5):
        out.append(dict(id=n + 20 + i, stuck=True, title=f"示範卡住的任務 {i}"))
    for i in range(5):
        out.append(dict(id=n + 30 + i, due_in=3600 * (i + 1), title=f"示範快到期的任務 {i}"))
    for i in range(15):
        out.append(dict(id=n + 40 + i, status="doing" if i % 3 else "waiting", title=f"示範進行中的任務 {i}"))
    for i in range(10):
        out.append(dict(id=n + 60 + i, status="todo", title=f"示範待辦的任務 {i}", owner=["eng", "res", "doc", "ops"][i % 4]))
    return out


PRIO_CASES = """(() => { const D = new Date(), noon = new Date(D.getFullYear(), D.getMonth(), D.getDate(), 12, 0, 0).getTime() / 1000, np = __office.notePriority, H = 3600;
  const r = (o, due) => { const x = np(Object.assign({ status: 'doing', due_at: due }, o), noon); return x.p + ':' + x.why; };
  return {
    decision: r({ decision: true }, 0), decisionFuture: r({ decision: true }, noon + 5 * H), decisionOverdue: r({ decision: true }, noon - H),
    overdue: r({}, noon - 1), overdueTodo: r({ status: 'todo' }, noon - 86400 * 3), atNow: r({}, noon), nullDue: r({ status: 'todo' }, null), zeroDue: r({ status: 'todo' }, 0), noDueKey: (() => { const x = np({ status: 'doing' }, noon); return x.p + ':' + x.why; })(),
    stuck: r({ stuck: true }, 0), stuckOverdue: r({ stuck: true }, noon - H), stuckFuture: r({ stuck: true }, noon + 40 * H),
    today: r({}, noon + 3 * H), todayTodo: r({ status: 'todo' }, noon + 11 * H), tomorrow: r({}, noon + 13 * H), exact24: r({}, noon + 24 * H), over24: r({ status: 'todo' }, noon + 24 * H + 1), over24doing: r({}, noon + 24 * H + 1), far: r({ status: 'todo' }, noon + 9 * 86400),
    doing: r({}, 0), waiting: r({ status: 'waiting' }, 0), todo: r({ status: 'todo' }, 0), unknown: r({ status: 'weird' }, 0) }; })()"""


def run(browser, base, engine):
    shoot = engine == "chromium"
    errors = []

    def new_page(w=1280, h=800, q=""):
        page = browser.new_page(viewport={"width": w, "height": h})
        page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
        page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
        page.goto(base + "/office.html" + q)
        page.wait_for_function("window.__office && __office.board() !== null", timeout=20000)
        page.evaluate("__office.pause(true)")
        page.wait_for_timeout(500)
        return page

    def open_board(page):
        page.evaluate("__office.openView('board')")
        page.wait_for_selector("#vBody .wb", timeout=5000)
        page.wait_for_timeout(500)

    print(f"[{engine}] notePriority: derived from the task, the clock decides")
    page = new_page()
    c = page.evaluate(PRIO_CASES)
    check("a decision is P1 等你決定 (whatever its due date)", c["decision"] == c["decisionFuture"] == c["decisionOverdue"] == "1:等你決定", c)
    check("overdue (due < now) is P1 逾期; one second late counts, exactly now does not",
          c["overdue"] == "1:逾期" and c["overdueTodo"] == "1:逾期" and c["atNow"] == "2:今天到期", c)
    check("no due date (0, null, missing) is never overdue", c["nullDue"] == "4:待辦" and c["zeroDue"] == "4:待辦" and c["noDueKey"] == "3:進行中", c)
    check("stuck is P2 卡住, unless it is overdue (then P1); a stuck task due in 40 h is still P2 卡住",
          c["stuck"] == "2:卡住" and c["stuckOverdue"] == "1:逾期" and c["stuckFuture"] == "2:卡住", c)
    check("due within 24 h is P2: today vs tomorrow by the calendar day (a fixed local noon)",
          c["today"] == "2:今天到期" and c["todayTodo"] == "2:今天到期" and c["tomorrow"] == "2:明天到期", c)
    check("exactly 24 h counts as due soon; one second more does not (back to its status)",
          c["exact24"] == "2:明天到期" and c["over24"] == "4:待辦" and c["over24doing"] == "3:進行中" and c["far"] == "4:待辦", c)
    check("doing and waiting are P3, todo is P4 (an unknown status reads as doing)",
          c["doing"] == "3:進行中" and c["waiting"] == "3:等待中" and c["todo"] == "4:待辦" and c["unknown"] == "3:進行中", c)
    check("the priority colours are four different, fixed ones", page.evaluate("__office.prio()") == HEX, page.evaluate("__office.prio()"))

    print(f"[{engine}] the demo board: four lanes")
    open_board(page)
    lanes = page.evaluate("__office.lanes()")
    ids = [l["ids"] for l in lanes]
    check("緊急: the decision, then the overdue one", ids[0] == [24, 22] and lanes[0]["why"] == ["等你決定", "逾期"], lanes[0])
    check("重要: stuck first, then the two due within 24 h (soonest first)", ids[1] == [20, 17, 25] and lanes[1]["why"][0] == "卡住"
          and all(w in ("今天到期", "明天到期") for w in lanes[1]["why"][1:]), lanes[1])
    check("進行中: doing (longest idle first), then waiting", ids[2] == [18, 21, 19] and lanes[2]["why"] == ["進行中", "進行中", "等待中"], lanes[2])
    check("不急: dated first, then the longest idle", ids[3] == [23, 15, 26], lanes[3])
    dom = page.evaluate("""() => [...document.querySelectorAll('#vBody .wb-lane')].map(l => ({name: l.getAttribute('aria-label'), cnt: l.querySelector('h3 .cnt').textContent,
        ids: [...l.querySelectorAll('.nt')].map(n => +n.dataset.id)}))""")
    check("the DOM has the four lanes left to right with their counts, each task once",
          [d["name"] for d in dom] == ["緊急", "重要", "進行中", "不急"] and [d["cnt"] for d in dom] == ["2", "3", "3", "3"]
          and [d["ids"] for d in dom] == ids, dom)
    check("the header sums the lanes up", page.evaluate("document.getElementById('vSub').textContent") == "緊急 2 · 重要 3 · 進行中 3 · 不急 3")
    check("the sheet is a whiteboard: the legend says how the colours come about, a marker tray sits at the bottom",
          page.evaluate("""() => { const k = document.querySelector('#vBody .wb-key').innerText, t = document.getElementById('wTray');
              return k.includes('等你決定或逾期') && k.includes('卡住或 24 小時內到期') && k.includes('進行中') && k.includes('待辦') && getComputedStyle(t).display !== 'none' && t.querySelectorAll('svg').length >= 2; }"""))
    fonts = page.evaluate("""() => { const n = document.querySelector('#vBody .nt .tt'), m = document.querySelector('#vBody .nt .nm'), h = document.querySelector('#vBody .wb-lane h3');
        return {title: parseFloat(getComputedStyle(n).fontSize), meta: parseFloat(getComputedStyle(m).fontSize), head: getComputedStyle(h).fontFamily}; }""")
    check("text is readable at --ui 1: title >= 15 px, meta >= 13 px; lane headings use a handwriting stack",
          fonts["title"] >= 15 and fonts["meta"] >= 13 and "cursive" in fonts["head"], fonts)
    w = page.evaluate("""() => { const n = document.querySelector('#vBody .nt').getBoundingClientRect(); return [n.width, n.height]; }""")
    check("a note is square-ish paper (about 170 px wide)", 150 <= w[0] <= 190 and 130 <= w[1] <= 230, w)
    lay = page.evaluate("""() => { const r = [...document.querySelectorAll('#vBody .wb-lane')].map(l => l.getBoundingClientRect()); return {tops: r.map(x => Math.round(x.top)), lefts: r.map(x => Math.round(x.left))}; }""")
    check("wide window: four lanes side by side", len(set(lay["tops"])) == 1 and lay["lefts"] == sorted(lay["lefts"]) and len(set(lay["lefts"])) == 4, lay)
    # priority colours: the wall and the lanes agree
    cols = page.evaluate("""() => [...document.querySelectorAll('#vBody .wb-lane')].map(l => [...l.querySelectorAll('.nt')].map(n => getComputedStyle(n).backgroundColor))""")
    rgb = [tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in HEX]
    check("every note in a lane has that lane's colour", all(all(c == f"rgb{rgb[i]}" for c in lane) for i, lane in enumerate(cols)), cols)
    wall = page.evaluate("__office.notes()")
    flat = [HEX[i] for i, l in enumerate(lanes) for _ in l["ids"]][:7]
    check("the wall's sticky notes: lane colours, in lane order (red first), at most 7", wall == flat and len(wall) == 7 and wall[0] == HEX[0], (wall, flat))
    if shoot:
        page.screenshot(path=str(SHOTS / "office-whiteboard-wide.png"))

    print(f"[{engine}] stable: tilt, no rebuild")
    tilts = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#vBody .nt')].map(n => [n.dataset.id, parseFloat(n.style.getPropertyValue('--tilt'))]))""")
    vals = list(tilts.values())
    check("each note has its own tilt within ±2.5°", all(-2.5 <= v <= 2.5 for v in vals) and len(set(vals)) >= 8, tilts)
    page.evaluate("window.__n1 = document.querySelector('#vBody .nt[data-id=\"18\"]'); window.__n1.__mine = 1; window.__body = document.querySelector('#vBody .wb')")
    for _ in range(4):
        page.evaluate("__office.snap()")                      # a snapshot, as the host sends one every 3 s
        page.wait_for_timeout(400)
    check("snapshots do not rebuild the board (the same node, a selection survives)", page.evaluate("document.body.contains(window.__n1) && window.__n1.__mine === 1 && document.querySelector('#vBody .wb') === window.__body"))
    page.evaluate("document.querySelector('#vBody .nb[data-id=\"20\"]').click()")
    page.wait_for_selector("#vOver .wcard", timeout=3000)
    page.wait_for_function("document.querySelectorAll('#vOver .thist li time').length > 0", timeout=3000)   # the history has arrived (it redraws the card once)
    page.evaluate("window.__card = document.querySelector('#vOver .wcard')")
    for _ in range(3):
        page.evaluate("__office.snap()")
        page.wait_for_timeout(450)
    check("…nor an open card (opening it did not rebuild the board either)",
          page.evaluate("document.querySelector('#vOver .wcard') === window.__card && document.body.contains(window.__n1) && window.__n1.__mine === 1"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    # data change rebuilds, tilt stays
    d = page.evaluate("__office.board()")
    d["open"][0]["title"] = "示範登入頁改版（改過）"
    page.evaluate("(b) => __office.setBoard(b)", d)
    page.wait_for_timeout(500)
    tilts2 = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#vBody .nt')].map(n => [n.dataset.id, parseFloat(n.style.getPropertyValue('--tilt'))]))""")
    check("a data change rebuilds the board (new nodes) and every note keeps its tilt",
          page.evaluate("!document.body.contains(window.__n1)") and tilts2 == tilts, (tilts, tilts2))
    page.evaluate("window.__n2 = document.querySelector('#vBody .nt[data-id=\"23\"]')")
    put(page, [dict(id=1, due_in=2.5, title="示範快到期的事"), dict(id=2, status="todo", title="示範待辦")])
    before = page.evaluate("__office.lanes()")
    page.evaluate("window.__n3 = document.querySelector('#vBody .nt[data-id=\"2\"]')")
    page.wait_for_timeout(3600)
    after = page.evaluate("__office.lanes()")
    check("a task crosses its due time by the clock alone: it moves from 重要 to 緊急 (逾期) without any data change",
          before[1]["ids"] == [1] and before[0]["ids"] == [] and after[0]["ids"] == [1] and after[0]["why"] == ["逾期"] and after[1]["ids"] == [], (before, after))
    check("…and the board redrew for it", page.evaluate("document.querySelector('#vBody .wb-lane.p1 .nt[data-id=\"1\"]') !== null && !document.body.contains(window.__n3)"))
    page.evaluate("__office.closeView()")

    print(f"[{engine}] 0, 1 and 45 notes")
    put(page, [])
    open_board(page)
    e = page.evaluate("""() => ({lanes: document.querySelectorAll('#vBody .wb-lane').length, notes: document.querySelectorAll('#vBody .nt').length, t: document.getElementById('vBody').innerText, sub: document.getElementById('vSub').textContent, wall: __office.notes()})""")
    check("no task: a blank whiteboard that says so and how a task gets there (sfctl task add)",
          e["lanes"] == 0 and e["notes"] == 0 and "還沒有任務" in e["t"] and "sfctl task add" in e["t"] and e["sub"] == "還沒有任務" and e["wall"] == [], e)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-whiteboard-empty.png"))
    put(page, [dict(id=7, title="示範唯一的一件", status="todo")])
    e = page.evaluate("""() => ({lanes: [...document.querySelectorAll('#vBody .wb-lane')].map(l => l.querySelectorAll('.nt').length), sub: document.getElementById('vSub').textContent, wall: __office.notes()})""")
    check("one task: one note in 不急, the other lanes say (沒有)", e["lanes"] == [0, 0, 0, 1] and e["sub"] == "緊急 0 · 重要 0 · 進行中 0 · 不急 1" and e["wall"] == [HEX[3]], e)
    put(page, many())
    e = page.evaluate("""() => { const b = document.getElementById('vBody'); return {notes: document.querySelectorAll('#vBody .nt').length, lanes: [...document.querySelectorAll('#vBody .wb-lane')].map(l => l.querySelectorAll('.nt').length),
        sh: b.scrollHeight, ch: b.clientHeight, sw: b.scrollWidth, cw: b.clientWidth, wall: __office.notes().length}; }""")
    check("45 notes: all there, spread over the lanes (10 / 10 / 15 / 10)", e["notes"] == 45 and e["lanes"] == [10, 10, 15, 10], e)
    check("…the board scrolls inside the sheet, nothing sideways; the wall shows 7", e["sh"] > e["ch"] and e["sw"] <= e["cw"] + 1 and e["wall"] == 7, e)
    lt = page.evaluate("""() => { const n = [...document.querySelectorAll('#vBody .nt')].find(x => x.querySelector('.tt').textContent.length > 30), t = n.querySelector('.tt'), cs = getComputedStyle(t), lh = parseFloat(cs.lineHeight);
        return {lines: Math.round(t.clientHeight / lh), clipped: t.scrollHeight > t.clientHeight + 1, attr: n.querySelector('.nb').title === t.textContent, nh: n.getBoundingClientRect().height, text: t.textContent.length}; }""")
    check("a very long title is clamped to 4 lines, the full text in the title attribute, the note stays a note",
          lt["lines"] <= 4 and lt["clipped"] and lt["attr"] and lt["nh"] < 300 and lt["text"] >= 40, lt)
    # the card is centred in the sheet even when the board is scrolled
    page.evaluate("document.getElementById('vBody').scrollTop = 1e6")
    page.wait_for_timeout(200)
    page.evaluate("[...document.querySelectorAll('#vBody .nb')].pop().click()")
    page.wait_for_selector("#vOver .wcard", timeout=3000)
    page.wait_for_timeout(500)
    g = page.evaluate("""() => { const c = document.querySelector('#vOver .wcard').getBoundingClientRect(), s = document.getElementById('wBox').getBoundingClientRect();
        return {dx: Math.abs((c.left + c.width / 2) - (s.left + s.width / 2)), dy: Math.abs((c.top + c.height / 2) - (s.top + s.height / 2)), inside: c.left >= s.left && c.right <= s.right && c.top >= s.top && c.bottom <= s.bottom}; }""")
    check("with the board scrolled to the bottom, the card is still centred in the sheet and inside it", g["dx"] <= 3 and g["dy"] <= 3 and g["inside"], g)
    page.keyboard.press("Escape")
    page.evaluate("__office.closeView()")

    print(f"[{engine}] a note opens its card")
    page.reload()
    page.wait_for_function("window.__office && __office.board() !== null", timeout=20000)
    page.evaluate("__office.pause(true)")
    page.wait_for_timeout(400)
    open_board(page)
    check("the card is closed at first", page.evaluate("document.getElementById('vOver').hidden"))
    page.focus('#vBody .nb[data-id="22"]')
    page.keyboard.press("Enter")
    page.wait_for_selector("#vOver .wcard", timeout=3000)
    page.wait_for_timeout(500)
    card = page.evaluate("""() => { const c = document.querySelector('#vOver .wcard');
        return {text: c.innerText, title: c.querySelector('.wt').textContent, labels: [...c.querySelectorAll('dt')].map(d => d.textContent), hist: [...c.querySelectorAll('.thist li')].map(l => l.innerText.replace(/\\s+/g, ' ')),
          slot: !!c.querySelector('.wb-actions[data-slot="actions"]'), slotKids: c.querySelector('.wb-actions') ? c.querySelector('.wb-actions').children.length : -1, buttonsInSlot: c.querySelectorAll('.wb-actions button').length,
          slotText: c.querySelector('.wb-actions').textContent, focus: document.activeElement.className}; }""")
    check("Enter on a focused note opens it (a real button); the card has the title in full and the six facts",
          card["title"] == "示範環境例行檢查" and card["labels"] == ["負責", "交辦自", "在等", "下一步", "期限", "動靜"] and "檢查磁碟與憑證到期日" in card["text"] and "已逾期" in card["text"], card)
    check("…its history is listed (3 demo events, newest first, kinds in Chinese)", len(card["hist"]) == 3 and "記錄" in card["hist"][0] and "建立" in card["hist"][2], card["hist"])
    check("…the action slot is there: a task that is neither the person's nor waiting on them offers 留言 only",
          card["slot"] and card["buttonsInSlot"] == 1 and card["slotText"] == "留言", card)
    check("…focus moved into the card (the ✕)", card["focus"] == "wx", card["focus"])
    if shoot:
        page.screenshot(path=str(SHOTS / "office-whiteboard-detail.png"))
    page.click('#vOver [data-act="dclose"]')
    check("✕ closes the card, the sheet stays, focus goes back to the note",
          page.evaluate("document.getElementById('vOver').hidden && __office.view() === 'board' && document.activeElement.classList.contains('nb') && document.activeElement.dataset.id === '22'"))
    page.click('#vBody .nb[data-id="19"]')
    page.wait_for_selector("#vOver .wcard", timeout=3000)
    page.mouse.click(40, 400)                      # the dimmed board next to the card
    page.wait_for_timeout(300)
    check("a click outside the card closes only the card", page.evaluate("document.getElementById('vOver').hidden && __office.view() === 'board'"))
    page.click('#vBody .nb[data-id="19"]')
    page.wait_for_selector("#vOver .wcard", timeout=3000)
    page.click('#vOver .wcard .wt')
    check("a click inside the card keeps it open", page.evaluate("!document.getElementById('vOver').hidden"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc closes the card first", page.evaluate("document.getElementById('vOver').hidden && __office.view() === 'board'"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("…a second Esc closes the sheet", page.evaluate("__office.view() === null && document.getElementById('wall').hidden && document.getElementById('vOver').hidden"))

    print(f"[{engine}] the card's actions: 我做好了 and 留言")
    page.reload()
    page.wait_for_function("window.__office && __office.board() !== null", timeout=20000)
    page.evaluate("__office.pause(true)")
    page.wait_for_timeout(400)
    open_board(page)

    def open_card(tid):
        page.evaluate("(i) => document.querySelector(`#vBody .nb[data-id=\"${i}\"]`).click()", tid)
        page.wait_for_function("(i) => { const c = document.querySelector('#vOver .wcard'); return c && c.querySelector('.nid').textContent === '#' + i && c.querySelectorAll('.thist li time').length > 0; }", arg=tid, timeout=4000)

    def buttons():
        return page.evaluate("[...document.querySelectorAll('#vOver .wb-actions > button')].map(b => b.textContent)")
    open_card(24)
    b24 = buttons()
    page.keyboard.press("Escape")
    open_card(19)
    b19 = buttons()
    page.keyboard.press("Escape")
    open_card(21)
    b21 = buttons()
    check("the buttons follow the rule: a decision for the person has 回覆 and 留言; their own task or one waiting on them 我做好了 and 留言; any other task only 留言",
          b24 == ["回覆", "留言"] and b19 == ["我做好了", "留言"] and b21 == ["留言"], (b24, b19, b21))
    page.click('#vOver [data-act="anote"]')
    check("留言 opens a form in the card: a focused textarea, 確定 and 取消, and a tick box 「也告訴 …」 (the owner), ticked",
          page.evaluate("""() => ({ta: document.activeElement.tagName, ok: !!document.querySelector('#vOver .wform .ok'), cancel: !!document.querySelector('#vOver [data-act=acancel]'),
              tell: document.querySelector('#vOver .wtell').innerText.trim(), on: document.querySelector('#vOver .wtell input').checked})""")
          == {"ta": "TEXTAREA", "ok": True, "cancel": True, "tell": "也告訴 示範秘書長", "on": True})
    page.click('#vOver .wform .ok')
    check("a note without a text is blocked: nothing sent, the reason shown in the card, the form stays",
          page.evaluate("__office.acted()") == [] and "要先寫點什麼" in page.evaluate("document.querySelector('#vOver .wmsg').textContent") and page.evaluate("!!document.querySelector('#vOver textarea')"))
    page.fill("#vOver textarea", "請先把草稿給我看")
    page.evaluate("__office.snap()")                      # a snapshot while typing: the text and the focus survive
    page.wait_for_timeout(500)
    check("a snapshot while typing keeps the text", page.evaluate("document.querySelector('#vOver textarea').value") == "請先把草稿給我看")
    page.click('#vOver .wform .ok')
    page.wait_for_function("__office.acted().length === 1", timeout=3000)
    page.wait_for_timeout(500)
    check("確定 sends the note: act(21, note, text), the form closes, the card stays",
          page.evaluate("__office.acted()") == [{"id": 21, "action": "note", "text": "請先把草稿給我看"}]
          and page.evaluate("!document.querySelector('#vOver .wform') && !document.getElementById('vOver').hidden"))
    check("…the toast says 已留言, and one line went to the owner through the chat path",
          page.evaluate("document.getElementById('toast').innerText").count("已留言") >= 1
          and page.evaluate("__office.said()") == [{"key": "b:sec", "text": "（看板）#21「整理本週示範週報」留言：請先把草稿給我看"}], page.evaluate("__office.said()"))
    page.wait_for_function("[...document.querySelectorAll('#vOver .thist li')].some(l => l.innerText.includes('請先把草稿給我看'))", timeout=4000)
    page.keyboard.press("Escape")
    open_card(18)
    page.click('#vOver [data-act="anote"]')
    page.uncheck("#vOver .wtell input")
    page.fill("#vOver textarea", "只記著，不用通知")
    page.keyboard.press("Control+Enter")
    page.wait_for_function("__office.acted().length === 2", timeout=3000)
    page.wait_for_timeout(400)
    check("Ctrl+Enter submits; with the box unticked nobody is told", len(page.evaluate("__office.said()")) == 1 and page.evaluate("__office.acted()")[1]["id"] == 18)
    page.keyboard.press("Escape")

    # failure: the message is shown, the text is kept, nothing else happens
    open_card(21)
    page.evaluate("__office.actOpts({fail: '資料庫暫時打不開，稍後再試。'})")
    page.click('#vOver [data-act="anote"]')
    page.fill("#vOver textarea", "這則會失敗")
    page.click('#vOver .wform .ok')
    page.wait_for_function("document.querySelector('#vOver .wmsg.bad')", timeout=3000)
    f = page.evaluate("""() => ({msg: document.querySelector('#vOver .wmsg').textContent, text: document.querySelector('#vOver textarea').value, dis: document.querySelector('#vOver textarea').disabled, said: __office.said().length})""")
    check("on failure the store's message is shown in the card, the text is kept, the form is usable again, nobody was told",
          "資料庫暫時打不開" in f["msg"] and f["text"] == "這則會失敗" and not f["dis"] and f["said"] == 1, f)
    page.evaluate("__office.actOpts({fail: ''})")
    # Esc order: form, card, sheet
    page.focus("#vOver textarea")
    page.keyboard.press("Escape")
    check("Esc in the form closes only the form (the card and the sheet stay), focus back on 留言",
          page.evaluate("!document.querySelector('#vOver .wform') && !document.getElementById('vOver').hidden && __office.view() === 'board' && document.activeElement.dataset.act === 'anote'"))
    page.keyboard.press("Escape")
    check("…the next Esc closes the card…", page.evaluate("document.getElementById('vOver').hidden && __office.view() === 'board'"))
    page.keyboard.press("Escape")
    check("…and the next the sheet", page.evaluate("__office.view() === null"))
    open_board(page)

    # sending is not optimistic: the form is locked while it waits
    open_card(19)
    page.evaluate("__office.actOpts({delay: 900})")
    page.click('#vOver [data-act="adone"]')
    check("我做好了 on a task waiting on you: the form asks for an optional result and offers to tell the owner",
          page.evaluate("document.querySelector('#vOver .wl').textContent").startswith("標成做好了") and "也告訴 示範研究員" in page.evaluate("document.querySelector('#vOver .wtell').innerText"))
    page.fill("#vOver textarea", "資料我補好了")
    page.click('#vOver .wform .ok')
    page.wait_for_timeout(250)
    w = page.evaluate("""() => ({ta: document.querySelector('#vOver textarea').disabled, ok: document.querySelector('#vOver .wform .ok').disabled, msg: document.querySelector('#vOver .wmsg').textContent,
        lane: [...document.querySelectorAll('#vBody .nt[data-id="19"] .why')].map(x => x.textContent)})""")
    check("…while it is sending the form is locked and the board has not changed (no optimistic edit)", w["ta"] and w["ok"] and "送出中" in w["msg"] and w["lane"] == ["等待中"], w)
    page.wait_for_function("__office.acted().length === 4", timeout=3000)
    page.wait_for_timeout(1500)
    page.evaluate("__office.actOpts({delay: 0})")
    ln = page.evaluate("__office.lanes()")
    check("…then the owner carries on: waiting cleared, the task is 進行中 and stays on the board; the card stays open",
          page.evaluate("__office.acted()")[3] == {"id": 19, "action": "done", "text": "資料我補好了"} and ln[2]["ids"].count(19) == 1
          and "等待中" not in ln[2]["why"] and page.evaluate("!document.getElementById('vOver').hidden"), ln[2])
    check("…the owner was told: 「（看板）#19「…」我做好了：資料我補好了」", page.evaluate("__office.said()")[-1] == {"key": "b:res", "text": "（看板）#19「示範工具選型比較」我做好了：資料我補好了"}, page.evaluate("__office.said()"))
    page.wait_for_function("[...document.querySelectorAll('#vOver .thist li')].some(l => l.innerText.includes('資料我補好了'))", timeout=4000)
    page.keyboard.press("Escape")
    # a reply on the person's own decision closes it (the card's 回覆: tests_board_anywhere_ui.py has its rules)
    open_card(24)
    page.click('#vOver [data-act="areply"]')
    if shoot:
        page.fill("#vOver textarea", "選方案 A，下週一開始")
        page.wait_for_timeout(700)
        page.screenshot(path=str(SHOTS / "office-whiteboard-actions.png"))
    page.fill("#vOver textarea", "選方案 A")
    page.click('#vOver .wform .ok')
    page.wait_for_function("document.getElementById('vOver').hidden", timeout=4000)
    check("a reply to your own decision closes it: it leaves the board, the card closes, the requester is told",
          page.evaluate("__office.acted()")[4] == {"id": 24, "action": "done", "text": "選方案 A"} and 24 not in sum(([*l["ids"]] for l in page.evaluate("__office.lanes()")), [])
          and page.evaluate("__office.said()")[-1] == {"key": "b:res", "text": "（看板）#24「示範工具選型：方案 A 還是 B？」我的回覆：選方案 A"}, page.evaluate("__office.said()")[-1])
    page.evaluate("__office.closeView()")

    print(f"[{engine}] a name follows the person")
    open_board(page)
    page.click('#vBody .nt[data-id="18"] .who[data-key="b:eng"]')
    page.wait_for_timeout(500)
    check("clicking the owner on a note closes the sheet and follows that person",
          page.evaluate("__office.view() === null && __office.following() === 'b:eng' && document.getElementById('wall').hidden"))
    page.evaluate("__office.unfollow()")
    open_board(page)
    page.click('#vBody .nb[data-id="18"]')
    page.wait_for_selector("#vOver .wcard")
    page.click('#vOver .wcard .who[data-key="b:eng"]')
    page.wait_for_timeout(500)
    check("…and so does the name inside the card", page.evaluate("__office.view() === null && __office.following() === 'b:eng' && document.getElementById('vOver').hidden"))
    page.evaluate("__office.unfollow()")
    page.close()

    print(f"[{engine}] a narrow window and the bigger interface size")
    page = new_page(600, 800)
    open_board(page)
    n = page.evaluate("""() => { const b = document.getElementById('vBody'), s = document.getElementById('wBox').getBoundingClientRect(), r = [...document.querySelectorAll('#vBody .wb-lane')].map(l => l.getBoundingClientRect());
        return {over: b.scrollWidth > b.clientWidth + 1, page: document.documentElement.scrollWidth > innerWidth, fit: s.left >= 0 && s.right <= innerWidth,
          stacked: new Set(r.map(x => Math.round(x.left))).size === 1 && r.every((x, i) => !i || x.top > r[i - 1].top), notes: [...document.querySelectorAll('#vBody .nt')].every(x => { const q = x.getBoundingClientRect(); return q.left >= s.left && q.right <= s.right; })}; }""")
    check("600 px: the sheet fits, nothing spills sideways, no horizontal page scroll", n["fit"] and not n["over"] and not n["page"], n)
    check("…the lanes are stacked, every note inside the sheet", n["stacked"] and n["notes"], n)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-whiteboard-narrow.png"))
    put(page, many())
    n = page.evaluate("() => { const b = document.getElementById('vBody'); return b.scrollWidth > b.clientWidth + 1; }")
    check("600 px with 45 notes: still nothing sideways", not n)
    page.close()
    if shoot:
        page = new_page(1280, 800, "?ui=1.18")
        open_board(page)
        z = page.evaluate("""() => ({ui: getComputedStyle(document.documentElement).getPropertyValue('--ui'), tt: document.querySelector('#vBody .nt .tt').getBoundingClientRect().height > 0,
            over: document.getElementById('vBody').scrollWidth > document.getElementById('vBody').clientWidth + 1})""")
        check("--ui 1.18: the board is zoomed with the interface size, still no sideways scroll", z["ui"].strip() == "1.18" and z["tt"] and not z["over"], z)
        page.screenshot(path=str(SHOTS / "office-whiteboard-wide-ui118.png"))
        page.evaluate("document.querySelector('#vBody .nb[data-id=\"24\"]').click()")
        page.wait_for_selector("#vOver .wcard")
        page.wait_for_timeout(500)
        g = page.evaluate("""() => { const c = document.querySelector('#vOver .wcard').getBoundingClientRect(), s = document.getElementById('wBox').getBoundingClientRect();
            return c.left >= s.left && c.right <= s.right && c.top >= s.top && c.bottom <= s.bottom; }""")
        check("--ui 1.18: the card stays inside the sheet", g)
        page.close()
    check("no console error from the page", not errors, errors[:5])


SHOTS.mkdir(exist_ok=True)
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
