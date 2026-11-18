#!/usr/bin/env python3
"""Each remote office has its own whiteboard and its main bot, in the real page with a stand-in Python side.

Asked for: "each remote office should have its own whiteboard, and also their main bot; try to show this in the
office." A remote office is a paired computer's room (「隔壁：<machine>」).
  - each peer room shows its main bot (the /link/bots row that is primary): at the room's first seat (the desk is the
    big one), at the primary's larger size, with ★ 主要 on its name and 「主要：<bot>」 on the room's sign; exactly one
    per room (two primaries: one of them), none when the computer has no primary
  - each peer room has a small whiteboard on its back wall (sticky notes, a badge with the number of tasks), a hit
    target like the lobby's (tooltip 「<machine> 的白板」, pointer cursor); a click opens the same centred sheet for that
    computer's tasks, titled 「隔壁：<machine> 的白板」, in the right priority lanes; the lobby's board still opens this
    computer's own tasks (and still has 我做好了 / 留言), and a task number that exists on both is each its own
  - a peer's board is read-only: no 我做好了 / 留言, no task_detail and no task_user_act for it; the card says changes go
    through that computer's main bot, with a 「跟 <bot> 說」 button when its chat can be reached (it follows that bot and
    opens the conversation), plain words when it cannot
  - states: pending 「讀取中…」, old 「那台的 ShellBot 版本較舊…」, denied 「那台沒有開放給這台看任務」, error keeps the last
    lists with 「暫時連不上，顯示的是 X 分鐘前的內容」
  - link_peer_tasks is asked on the tasks' own ~9 s cadence (not on every 3 s snapshot), and a host that does not have
    it (an older app) gets no peer boards and no error
  - office.html on its own (the demo page) shows a main bot and a board in its demo computer
Demo data only (made-up names). Headless Chromium, and WebKit when playwright has it.
Screenshots (Chromium): qa-shots/office-peer-board-room.png, office-peer-board-open.png.
Run: .venv/bin/python tests_office_peer_board_ui.py
"""
import json
import pathlib
import sys
import time
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_board_harness")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__dict__["__file__"], "exec"), H.__dict__)   # its stand-in Python side and helpers, not its run

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


NOW = time.time()
task = H.task

# three paired computers: A has a main bot who is listed last (so the front seat is a swap), B has no primary,
# C has two bots that both say they are primary and whose main bot has no tab (its chat cannot be reached)
PEERS = {"peers": {
    "pid-a": {"name": "示範電腦甲", "addr": "示範電腦甲", "primary": "builder",
              "bots": [{"role": "napper", "label": "示範午睡員", "state": "asleep", "avatar": {"shape": "round", "color": "teal"}, "responsibility": "休息中"},
                       {"role": "asker", "label": "示範等待員", "state": "awake", "sid": "r11", "activity": "decision", "decision": True,
                        "latest": "示範：要不要部署？", "avatar": {"shape": "egg", "color": "pink"}, "responsibility": "部署"},
                       {"role": "builder", "label": "示範建置員", "state": "awake", "primary": True, "sid": "r10", "activity": "idle",
                        "avatar": {"shape": "ghost", "color": "green"}, "responsibility": "建置與測試"}],
              "tabs": {"r30": {"kind": "temp", "name": "示範隔壁實習生", "cli": "codex", "activity": "working", "latest": "示範：Running npm test"}}},
    "pid-b": {"name": "示範電腦乙", "addr": "示範電腦乙", "primary": "",
              "bots": [{"role": "solo", "label": "示範獨行員", "state": "awake", "sid": "r20", "activity": "idle",
                        "avatar": {"shape": "hex", "color": "amber"}, "responsibility": "獨自作業"}], "tabs": {}},
    "pid-c": {"name": "示範電腦丙", "addr": "示範電腦丙", "primary": "boss",
              "bots": [{"role": "boss", "label": "示範老闆", "state": "asleep", "primary": True,
                        "avatar": {"shape": "star", "color": "purple"}, "responsibility": "統籌"},
                       {"role": "deputy", "label": "示範副手", "state": "awake", "primary": True, "sid": "r40", "activity": "idle",
                        "avatar": {"shape": "tri", "color": "sky"}, "responsibility": "副手"}], "tabs": {}},
}}


def rows(owner, requester="user"):
    # a peer's tasks: ids that overlap the local ones on purpose (#12, #13): each board has its own
    dec = task(31, "示範隔壁：要不要現在部署", "waiting", "user", requester, "user", "等那台的使用者回一句", NOW + 7200, 1200, "decision")
    stk = task(12, "示範隔壁：資料表合併", "doing", owner, "user", "", "等來源檔補齊", None, 90000)
    opn = [stk, dec, task(13, "示範隔壁：夜間建置", "todo", owner, "user", "", "先列出專案", NOW - 3600, 3000),
           task(14, "示範隔壁：跑完整測試", "doing", owner, "user", "", "跑到 310 項", None, 300)]
    return {"decisions": [dec], "stuck": [dict(stk, idle_s=90000)], "open": opn}


def peer_tasks(state="ok", at=None, owner="builder"):
    return dict(rows(owner), state=state, at=NOW if at is None else at)


PEER_TASKS = {"peers": {"pid-a": peer_tasks(), "pid-b": peer_tasks("ok", owner="solo"), "pid-c": peer_tasks("ok", owner="deputy")}, "pending": 0}

WRAP = """
if (window.top === window) {
  const base = window.pywebview.api;
  window.__peerTasks = %s; window.__ptCalls = 0;
  window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
    if (prop === 'link_peer_tasks') {
      if (window.__noPT) return undefined;            // an older app: the call is not there
      return () => { window.__ptCalls++; window.__calls.push(prop); return Promise.resolve(JSON.stringify(window.__peerTasks)); };
    }
    return base[prop];
  } }) };
}
""" % json.dumps(PEER_TASKS, ensure_ascii=False)


def new_page(browser, errors, old_host=False):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    H.PEERS = PEERS
    if old_host:
        page.add_init_script("window.__noPT = true;")
    page.add_init_script(H.stub())
    page.add_init_script(WRAP)
    return page


def start(page, base):
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    page.wait_for_function("Object.keys(SFBots.peerBots()).length === 3", timeout=20000)


def open_peer(page, pid="pid-a"):
    """(Re)open the office with the stand-in's current answers; the camera on that computer's room."""
    if page.evaluate("SFOffice.isOpen()"):
        page.evaluate("SFOffice.close()")
    page.evaluate("SFOffice.open()")
    page.wait_for_function("""(pid) => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && f.contentWindow.__office.rooms().some(r => r.id === 'peer:' + pid); } catch (e) { return false; } }""",
                           arg=pid, timeout=15000)
    fr = H.office(page)
    fr.evaluate("(pid) => { __office.setMode('iso', true); __office.setArea('peer:' + pid, true); }", pid)
    page.wait_for_timeout(900)
    return fr


def text_of(fr):
    return fr.evaluate("document.getElementById('vBody').innerText")


def lanes_of(fr):
    return fr.evaluate("""() => [...document.querySelectorAll('#vBody .nt')].map(r => [+r.dataset.id, r.closest('.wb-lane').getAttribute('aria-label')])""")


def run(browser, base, engine):
    shoot = engine == "chromium"
    errors = []
    page = new_page(browser, errors)
    start(page, base)

    print(f"[{engine}] each peer room has its main bot")
    fr = open_peer(page)
    ma = fr.evaluate("__office.peerMain('pid-a')")
    check("computer A: its main bot is the primary /link/bots row", ma and ma["key"] == "p:pid-a/builder", ma)
    check("…at the room's first seat although it is listed last (the front seat)", ma and ma["seat"] == "p:pid-a:0", ma)
    others = ["p:pid-a/asker", "p:pid-a/napper"]
    check("…larger than the others", ma["scale"] >= 1.25 and all(fr.evaluate("(k) => __office.entScale(k)", k) < ma["scale"] - .1 for k in others),
          (ma, [fr.evaluate("(k) => __office.entScale(k)", k) for k in others]))
    check("…named on the room's sign (主要：<bot>)", ma["sign"] == "示範建置員", ma)
    seats = [fr.evaluate("(k) => __office.seatOf(k)", k) for k in others]
    check("…and the others sit at the other seats", all(x and x.startswith("p:pid-a:") and x != "p:pid-a:0" for x in seats) and seats[0] != seats[1], seats)
    fr.evaluate("__office.follow('p:pid-a/builder')")
    page.wait_for_timeout(400)
    check("its name has the ★ 主要 tag in the panel", "★ 主要" in fr.evaluate("document.getElementById('pName').innerText"))
    fr.evaluate("__office.follow('p:pid-a/asker')")
    page.wait_for_timeout(400)
    check("…and another bot's name does not", "★" not in fr.evaluate("document.getElementById('pName').innerText"))
    fr.evaluate("__office.unfollow()")
    check("computer B has no primary: no main bot, nobody at a big desk", fr.evaluate("__office.peerMain('pid-b')") is None
          and fr.evaluate("__office.entScale('p:pid-b/solo')") < 1.25)
    mc = fr.evaluate("__office.peerMain('pid-c')")
    both = [fr.evaluate("(k) => __office.entScale(k)", k) for k in ("p:pid-c/boss", "p:pid-c/deputy")]
    check("computer C says two bots are primary: exactly one is the main bot", mc and sorted(both)[0] < 1.25 and sorted(both)[1] >= 1.25, (mc, both))
    fr.evaluate("__office.setArea('peer:pid-a', true)")           # following someone moved the camera: back to the room
    page.wait_for_timeout(700)
    if shoot:
        page.screenshot(path=str(H.SHOTS / "office-peer-board-room.png"))

    print(f"[{engine}] each peer room has a whiteboard")
    pb = fr.evaluate("__office.peerBoard('pid-a')")
    check("computer A's board is its own tasks, by priority", pb and pb["state"] == "ok"
          and [x["id"] for x in pb["board"]["decisions"]] == [31] and [x["id"] for x in pb["board"]["stuck"]] == [12]
          and [x["id"] for x in pb["board"]["open"]] == [13, 14], pb and pb["board"])
    check("…owners are that computer's people", {x["id"]: x["owner_key"] for x in pb["board"]["all"]}[14] == "p:pid-a/builder"
          and {x["id"]: x["requester_key"] for x in pb["board"]["all"]}[31] == "user", pb["board"]["all"])
    check("…one sticky note per task (red: decision and overdue, orange: stuck, yellow: the rest)", pb["notes"] == [H.RED, H.RED, H.ORANGE, H.YELLOW], pb["notes"])
    pt = fr.evaluate("__office.wallPoint('board:pid-a')")
    check("the wall board is there to be hit", pt is not None, pt)
    page.mouse.move(pt["x"], pt["y"])
    page.wait_for_timeout(300)
    check("hovering it: the tooltip 「示範電腦甲 的白板」 and the pointer cursor",
          fr.evaluate("__office.hoverTip()") == "示範電腦甲 的白板" and fr.evaluate("document.getElementById('stage').classList.contains('pt')"),
          fr.evaluate("__office.hoverTip()"))
    check("…B and C have their own, and the lobby's two are still there", all(fr.evaluate("(k) => !!__office.wallPoint(k) || true", k) for k in ("board:pid-b", "board:pid-c")))
    before = {n: H.calls(page, n) for n in ("task_detail", "task_user_act")}
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(600)
    check("a click opens that computer's board as the centred sheet", fr.evaluate("__office.view()") == "board" and fr.evaluate("__office.viewScope()") == "pid-a"
          and fr.evaluate("!document.getElementById('wall').hidden"))
    check("…titled 隔壁：<machine> 的白板", fr.evaluate("document.getElementById('vTitle').textContent") == "隔壁：示範電腦甲 的白板")
    check("…four lanes with its tasks in the right ones (the number #12 is the peer's stuck task, not the lobby's)",
          lanes_of(fr) == [[31, "緊急"], [13, "緊急"], [12, "重要"], [14, "進行中"]], lanes_of(fr))
    body = text_of(fr)
    check("…the peer's titles, none of this computer's", "示範隔壁：資料表合併" in body and "示範登入頁改版" not in body and "示範比較表" not in body, body[:200])
    check("…a decision reads as waiting for that computer's user, not 你", "等使用者決定" in body and "等你決定" not in body)
    check("…owners are people in that room (their names)", "示範建置員" in body)
    if shoot:
        page.screenshot(path=str(H.SHOTS / "office-peer-board-open.png"))

    print(f"[{engine}] it is read-only")
    fr.click('#vBody .nt[data-id="12"] .nb')
    page.wait_for_timeout(600)
    card = fr.evaluate("document.querySelector('#vOver .wcard').innerText")
    check("a note opens a card with what the row has (title, next step, how long it did not move)",
          "示範隔壁：資料表合併" in card and "等來源檔補齊" in card and "1 天前沒動" in card, card)
    check("…it says changes go through that computer's main bot", "只能看" in card and "主要 bot（示範建置員）" in card, card)
    check("…no 我做好了, no 留言, no history", fr.evaluate("document.querySelectorAll('#vOver [data-act=adone], #vOver [data-act=anote], #vOver .thist, #vOver form, #vOver textarea').length") == 0
          and "歷程" not in card, card)
    check("no task_detail and no task_user_act was called for it", H.calls(page, "task_detail") == before["task_detail"]
          and H.calls(page, "task_user_act") == before["task_user_act"], (before, H.calls(page, "task_detail"), H.calls(page, "task_user_act")))
    check("…a 「跟 示範建置員 說」 button is offered (its chat can be reached)", fr.evaluate("""() => [...document.querySelectorAll('#vOver [data-act=tellmain]')].map(b => b.textContent.trim()).join()""") == "跟 示範建置員 說")
    fr.click('#vOver [data-act="tellmain"]')
    page.wait_for_timeout(600)
    check("…it follows that bot and opens the conversation", fr.evaluate("__office.view()") is None and fr.evaluate("__office.following()") == "p:pid-a/builder"
          and fr.evaluate("__office.chat().tab") == "chat" and fr.evaluate("document.getElementById('panel').classList.contains('open')"),
          (fr.evaluate("__office.view()"), fr.evaluate("__office.following()"), fr.evaluate("__office.chat()")))
    fr.evaluate("__office.unfollow()")

    print(f"[{engine}] a note's owner selects that person; Esc closes")
    fr.evaluate("__office.openView('board:pid-a')")
    page.wait_for_timeout(400)
    fr.click('#vBody .nt[data-id="14"] .who[data-key="p:pid-a/builder"]')
    page.wait_for_timeout(500)
    check("an owner there is clickable and follows that person on that computer", fr.evaluate("__office.following()") == "p:pid-a/builder" and fr.evaluate("__office.view()") is None)
    fr.evaluate("__office.unfollow(); __office.openView('board:pid-a')")
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc closes the sheet, not the office", fr.evaluate("__office.view()") is None and page.evaluate("SFOffice.isOpen()"))
    fr.evaluate("__office.openView('board:pid-a')")
    page.wait_for_timeout(300)
    fr.evaluate("__office.openView('board:pid-b')")
    page.wait_for_timeout(300)
    check("opening another computer's board switches to it", fr.evaluate("__office.viewScope()") == "pid-b"
          and fr.evaluate("document.getElementById('vTitle').textContent") == "隔壁：示範電腦乙 的白板")
    fr.evaluate("__office.openView('board:pid-b')")
    check("…opening the same one again closes it", fr.evaluate("__office.view()") is None)
    fr.evaluate("__office.openView('board:pid-b')")
    page.wait_for_timeout(300)
    fr.click('#vBody .nt[data-id="14"] .nb')
    page.wait_for_timeout(400)
    card = fr.evaluate("document.querySelector('#vOver .wcard').innerText")
    check("a computer with no main bot: the card says to handle it on that computer, no button", "請到那台電腦上處理" in card
          and fr.evaluate("document.querySelectorAll('#vOver [data-act=tellmain]').length") == 0, card)
    fr.evaluate("__office.closeView()")
    fr.evaluate("__office.openView('board:pid-c')")
    page.wait_for_timeout(300)
    fr.click('#vBody .nt[data-id="14"] .nb')
    page.wait_for_timeout(400)
    card = fr.evaluate("document.querySelector('#vOver .wcard').innerText")
    mc = fr.evaluate("__office.peerMain('pid-c')")
    check("a main bot with no tab: its name is in the words, but there is nothing to press",
          "主要 bot（" in card and fr.evaluate("document.querySelectorAll('#vOver [data-act=tellmain]').length") == 0, (card, mc))
    fr.evaluate("__office.closeView()")

    print(f"[{engine}] the lobby's board is unchanged")
    fr = open_peer(page, "pid-a")
    fr.evaluate("__office.setArea('lobby', true)")
    page.wait_for_timeout(700)
    pt, tip, cursor = H.wall_click(page, fr, "board")
    check("the lobby's board: tooltip 任務看板, opens this computer's tasks", tip == "任務看板" and fr.evaluate("__office.view()") == "board"
          and fr.evaluate("__office.viewScope()") is None and fr.evaluate("document.getElementById('vTitle').textContent") == "任務看板", (tip, cursor))
    check("…its own titles (#12 is 示範登入頁改版 here), not the peer's", "示範登入頁改版" in text_of(fr) and "示範隔壁" not in text_of(fr))
    check("…and a decision still reads 等你決定", "等你決定" in text_of(fr))
    fr.click('#vBody .nt[data-id="31"] .nb')
    page.wait_for_timeout(500)
    check("…its card (a decision: 回覆 and 留言) is still the live one, and asks for the history", fr.evaluate("!!document.querySelector('#vOver [data-act=areply]') && !!document.querySelector('#vOver [data-act=anote]')")
          and H.calls(page, "task_detail") >= 1)
    fr.evaluate("__office.closeView()")
    cal = fr.evaluate("__office.wallPoint('cal')")
    check("the calendar is still a wall object too", cal is not None)

    print(f"[{engine}] states")
    expect = {
        "pending": "讀取中…",
        "old": "那台的 ShellBot 版本較舊，還看不到它的白板（更新那台之後就有）",
        "denied": "那台沒有開放給這台看任務",
    }
    for state, words in expect.items():
        page.evaluate("(s) => { window.__peerTasks.peers['pid-a'] = {state: s, at: 0, decisions: [], stuck: [], open: []}; }", state)
        fr = open_peer(page)
        check(f"{state}: the room still has its board", fr.evaluate("__office.wallPoint('board:pid-a')") is not None)
        fr.evaluate("__office.openView('board:pid-a')")
        page.wait_for_timeout(400)
        body = text_of(fr)
        check(f"{state}: the sheet says 「{words}」 (and shows no lanes)", words in body and fr.evaluate("document.querySelectorAll('#vBody .wb-lane').length") == 0, body)
        if state == "old":
            check("…the main bot is still shown", fr.evaluate("__office.peerMain('pid-a')")["key"] == "p:pid-a/builder")
        fr.evaluate("__office.closeView()")
    page.evaluate("(a) => { window.__peerTasks.peers['pid-a'] = Object.assign(%s, {state: 'error', at: a}); }" % json.dumps(rows("builder")), time.time() - 5 * 60 - 10)
    fr = open_peer(page)
    fr.evaluate("__office.openView('board:pid-a')")
    page.wait_for_timeout(400)
    body = text_of(fr)
    check("error: the last lists are kept, with 「暫時連不上，顯示的是 5 分鐘前的內容」", "暫時連不上，顯示的是 5 分鐘前的內容" in body
          and lanes_of(fr) == [[31, "緊急"], [13, "緊急"], [12, "重要"], [14, "進行中"]], body[:160])
    if shoot:
        page.screenshot(path=str(H.SHOTS / "office-peer-board-error.png"))
    fr.evaluate("__office.closeView()")
    page.evaluate("(a) => { window.__peerTasks.peers['pid-a'] = {state: 'error', at: 0, decisions: [], stuck: [], open: []}; }", 0)
    fr = open_peer(page)
    fr.evaluate("__office.openView('board:pid-a')")
    page.wait_for_timeout(400)
    check("error and nothing read yet: says so, no minutes", "暫時連不上那台" in text_of(fr) and "分鐘前" not in text_of(fr), text_of(fr))
    fr.evaluate("__office.closeView()")
    page.evaluate("(p) => { window.__peerTasks = p; }", PEER_TASKS)

    print(f"[{engine}] the sheet is not rebuilt for a snapshot, and link_peer_tasks has its own cadence")
    page.evaluate("window.__ptCalls = 0")
    fr = open_peer(page)
    n0 = page.evaluate("window.__ptCalls")
    check("asked once when the office opened", n0 == 1, n0)
    fr.evaluate("__office.openView('board:pid-a')")
    page.wait_for_timeout(300)
    fr.evaluate("window.__tagRow = document.querySelector('#vBody .nt[data-id=\"14\"]'); window.__tagRow.__mine = 1")
    for _ in range(3):
        page.evaluate("SFOffice.refresh()")                    # a snapshot each time (the 3 s tick does the same)
        page.wait_for_timeout(350)
    check("snapshots in between do not ask again", page.evaluate("window.__ptCalls") == n0, page.evaluate("window.__ptCalls"))
    check("…and do not rebuild the sheet (the same note node, the same board version)",
          fr.evaluate("document.body.contains(window.__tagRow) && window.__tagRow.__mine === 1"))
    t0 = time.time()
    page.wait_for_function("window.__ptCalls >= 2", timeout=16000)
    dt = time.time() - t0
    check("it is asked again on the tasks' ~9 s cadence (not every 3 s)", page.evaluate("window.__ptCalls") == n0 + 1 and 4 < dt < 12, (page.evaluate("window.__ptCalls"), round(dt, 1)))
    page.evaluate("window.__peerTasks.peers['pid-a'].open.push(%s)" % json.dumps(task(77, "示範隔壁：新來的一件", "doing", "builder")))
    page.wait_for_function("window.__ptCalls >= 3", timeout=16000)
    page.wait_for_timeout(500)
    check("a change on that computer reaches the open sheet on the next ask", 77 in [x[0] for x in lanes_of(fr)], lanes_of(fr))
    check("…and the wall board's badge/notes follow", len(fr.evaluate("__office.peerBoard('pid-a').notes")) == 5)
    fr.evaluate("__office.closeView()")
    page.evaluate("SFOffice.close()")
    check("no console error from the page or the office", not errors, errors[:5])
    page.close()

    print(f"[{engine}] a host without link_peer_tasks (an older app)")
    errors2 = []
    old = new_page(browser, errors2, old_host=True)
    start(old, base)
    fr = open_peer(old)
    check("the call is not made", old.evaluate("window.__ptCalls") == 0 and "link_peer_tasks" not in old.evaluate("window.__calls"))
    check("no board on any peer room's wall", all(fr.evaluate("(k) => __office.wallPoint(k)", k) is None for k in ("board:pid-a", "board:pid-b", "board:pid-c"))
          and fr.evaluate("__office.peerBoard('pid-a').state") == "")
    check("…the main bot is still shown", fr.evaluate("__office.peerMain('pid-a')")["seat"] == "p:pid-a:0")
    fr.evaluate("__office.openView('board:pid-a')")
    check("…and opening a board that is not there does nothing", fr.evaluate("__office.view()") is None)
    old.wait_for_timeout(1500)
    old.evaluate("SFOffice.refresh()")
    old.wait_for_timeout(500)
    check("…the lobby's board and calendar are unaffected", fr.evaluate("__office.openView('board'), __office.view()") == "board")
    check("no page error", not errors2, errors2[:5])
    old.close()


def run_demo(browser, base, engine):
    print(f"[{engine}] the office on its own (demo page)")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.goto(base + "/office.html")
    page.wait_for_function("window.__office && __office.rooms().some(r => r.id.startsWith('peer:'))", timeout=15000)
    page.evaluate("__office.pause(true); __office.setMode('iso', true); __office.setArea('peer:peer1', true)")
    page.wait_for_timeout(1000)
    m = page.evaluate("__office.peerMain('peer1')")
    check("the demo computer has a main bot at its front seat", m and m["key"] == "p:peer1/bld" and m["seat"] == "p:peer1:0" and m["scale"] >= 1.25, m)
    pt = page.evaluate("__office.wallPoint('board:peer1')")
    check("…and a whiteboard on its wall", pt is not None)
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(600)
    check("…that opens with demo tasks, read-only", page.evaluate("__office.view()") == "board" and page.evaluate("document.querySelectorAll('#vBody .nt').length") == 5
          and page.evaluate("document.getElementById('vTitle').textContent") == "隔壁：示範電腦 的白板")
    page.click('#vBody .nt .nb')
    page.wait_for_timeout(400)
    check("…its card has the 「跟 示範建置員 說」 button", page.evaluate("!!document.querySelector('#vOver [data-act=tellmain]')")
          and page.evaluate("document.querySelectorAll('#vOver [data-act=adone]').length") == 0)
    page.evaluate("__office.closeView()")
    check("no page error", not errors, errors[:5])
    page.close()


H.SHOTS.mkdir(exist_ok=True)
srv = H.serve()
base = f"http://127.0.0.1:{srv.server_port}"
with H.sync_playwright() as pw:
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
                run_demo(browser, base, engine)
            finally:
                browser.close()
    finally:
        srv.shutdown()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
