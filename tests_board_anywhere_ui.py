#!/usr/bin/env python3
"""The task board from anywhere: a sidebar button with the count of decisions waiting on the person, a shortcut, the
whiteboard on its own (no office floor), and answering a decision from its card. In the real page with a stand-in
Python side, with the office switch OFF unless a case says otherwise.

Asked for: the questions an agent waits on the person for should be on a board they can call up in the terminal view and
the chat view alike, instead of digging through long conversations.
  - 看板 (#btn-taskboard) in the entry row at the top of the sidebar (web/entry.js) in the terminal view and in the chat view, office switch off; a red badge =
    tasks_overview's decisions, none at 0; the button goes only when the tasks store is unavailable
  - the count is asked about every 20 s while the window is visible (not while hidden), and again when it comes back
  - the button and the shortcut (Cmd+Shift+B / Ctrl+Shift+B, in the tooltip) open the board sheet over the window with no
    floor: decisions in the 緊急 lane; Esc, ✕, a click outside and the shortcut close the whole overlay
  - 回覆 on a decision card: needs text, task_user_act(id, 'done', text), the board and the badge follow, one line to the bot
    that asked: 「（看板）#N「標題」我的回覆：…」 (unchecked 「也告訴」: nothing); any other card keeps 我做好了 / 留言
  - paired computers: 這台 / 隔壁：<電腦> at the top of the sheet shows that computer's board, read only
  - with the office switch on, the office and its lobby whiteboard behave as before; the button toggles the board
Demo data only. Headless Chromium, and WebKit when playwright has it. Screenshots (Chromium): qa-shots/board-anywhere-*.png.
Run: .venv/bin/python tests_board_anywhere_ui.py
"""
import copy
import json
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_board_harness")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)   # its stand-in Python side and helpers, not its run

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
    print("SKIP: playwright not installed")
    sys.exit(0)

task = H.task
NOW = H.NOW
# three decisions for the person: #31's requester has no tab, #32's is the engineer (tab s701), #33's the chief (tab s700)
D31 = H.T_DECISION
D32 = task(32, "示範要不要加班趕工", "waiting", "user", "eng", "user", "等你回一句", None, 800, "decision")
D33 = task(33, "示範週報要寄給誰", "waiting", "user", "chief", "user", "等你點頭", None, 500, "decision")
OPEN = [x for x in H.T_OPEN if x["id"] != 31] + [D31, D32, D33]
TASKS = {"success": True, "decisions": [D31, D32, D33], "stuck": [dict(H.T_STUCK, idle_s=90000)], "open": OPEN}

# the paired computers (shapes: tests_office_peer_board_ui.py)
PEERS = {"peers": {
    "pid-a": {"name": "示範電腦甲", "addr": "示範電腦甲", "primary": "builder",
              "bots": [{"role": "builder", "label": "示範建置員", "state": "awake", "primary": True, "sid": "r10", "activity": "idle",
                        "avatar": {"shape": "ghost", "color": "green"}, "responsibility": "建置與測試"}], "tabs": {}},
    "pid-b": {"name": "示範電腦乙", "addr": "示範電腦乙", "primary": "",
              "bots": [{"role": "solo", "label": "示範獨行員", "state": "awake", "sid": "r20", "activity": "idle",
                        "avatar": {"shape": "hex", "color": "amber"}, "responsibility": "獨自作業"}], "tabs": {}}}}


def peer_rows(owner):
    dec = task(41, "示範隔壁：要不要現在部署", "waiting", "user", "user", "user", "等那台的使用者回一句", NOW + 7200, 1200, "decision")
    opn = [dec, task(42, "示範隔壁：夜間建置", "todo", owner, "user", "", "先列出專案", None, 3000)]
    return {"decisions": [dec], "stuck": [], "open": opn, "state": "ok", "at": NOW}


PEER_TASKS = {"peers": {"pid-a": peer_rows("builder"), "pid-b": peer_rows("solo")}, "pending": 0}

ACT = """      case 'task_user_act': {
        const id = +args[0], r = window.__actReply || {success: true, message: 'ok', tell: ''};
        if (r.success && args[1] === 'done') for (const k of ['decisions', 'stuck', 'open']) window.__tasks[k] = window.__tasks[k].filter(t => t.id !== id);
        return R(r); }
      case 'chat_send': return R({success: true});
      case 'task_detail': return R({success: true, task: {id: +args[0], title: 'x', events: []}});
      case 'link_peer_tasks': return R(window.__peerTasks);
      case 'tasks_overview': return R(window.__tasks);
      default: return R('{}');"""


def stub():
    s = H.stub()
    assert s.count("      default: return R('{}');") == 1 and s.count("      case 'tasks_overview': return R(window.__tasks);\n") == 1
    s = s.replace("      case 'tasks_overview': return R(window.__tasks);\n", "").replace("      default: return R('{}');", ACT)
    return s + "\nif (window.top === window) window.__peerTasks = %s;\n" % json.dumps(PEER_TASKS, ensure_ascii=False)


def args_of(page, name):
    return page.evaluate("(n) => window.__args.filter(a => a[0] === n).map(a => a[1])", name)


def new_page(browser, errors, office=False, style="cli", peers=False, width=1280, height=800, clock=False):
    cfg = copy.deepcopy(H.CFG)
    cfg["settings"].update({"experimental_office": office, "ui_style": style})
    H.CFG = cfg
    H.PEERS = PEERS if peers else {"peers": {}}
    H.TASKS = copy.deepcopy(TASKS)
    page = browser.new_page(viewport={"width": width, "height": height})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    if clock:
        page.clock.install()
    page.add_init_script(stub())
    return page


def start(page, base, peers=False):
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFBoard && window.SFBots && SFBots.enabled() && window.__sfHost && Object.keys(window.__sfHost.sessions()).length >= 3", timeout=20000)
    page.wait_for_selector("#btn-taskboard", timeout=10000)
    if peers:
        page.wait_for_function("Object.keys(SFBots.peerBots()).length === 2", timeout=20000)
    page.wait_for_function("SFBoard.count() >= 0 && !document.querySelector('#btn-taskboard').hidden", timeout=10000)


def mod_of(page):
    return page.evaluate("/Mac|iPhone|iPad/i.test(navigator.platform) ? 'Meta' : 'Control'")


def board_frame(page):
    """The board-only overlay, once its sheet is showing; the frame."""
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f && f.contentWindow.__office && f.contentWindow.__office.view() === 'board'; } catch (e) { return false; } }""", timeout=15000)
    page.wait_for_timeout(500)
    return H.office(page)


def overlay_gone(page):
    page.wait_for_function("!document.getElementById('sfo-overlay')", timeout=4000)
    return True


def lane_of(fr):
    return {r[0]: r[1] for r in fr.evaluate("""() => [...document.querySelectorAll('#vBody .nt')].map(r => [+r.dataset.id, r.closest('.wb-lane').getAttribute('aria-label')])""")}


def badge(page):
    return page.evaluate("""() => { const b = document.querySelector('#btn-taskboard .sfb-badge'), r = b.getBoundingClientRect();
        return {text: b.textContent, shown: !b.hidden && r.width > 0 && r.height > 0}; }""")


def button_box(page):
    return page.evaluate("""() => { const b = document.getElementById('btn-taskboard'), r = b.getBoundingClientRect(), row = document.querySelector('#sb-entry .sfe-row').getBoundingClientRect();
        const cs = getComputedStyle(b); return {w: r.width, h: r.height, visible: cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0,
        inRow: r.left >= row.left - 1 && r.right <= row.right + 1 && r.top >= row.top - 1 && r.bottom <= row.bottom + 1,
        tip: b.title, label: b.getAttribute('aria-label'), onTop: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2).closest('#btn-taskboard') !== null}; }""")


def run(browser, base, engine):
    shoot = engine == "chromium"
    errors = []

    print(f"[{engine}] terminal view, office switch off: the button and its badge")
    page = new_page(browser, errors, office=False, style="cli")
    start(page, base)
    page.wait_for_function("SFBoard.count() === 3", timeout=8000)
    bb = button_box(page)
    check("看板 is in the entry row at the top of the sidebar (not the foot), visible, and nothing covers it", bb["visible"] and bb["inRow"] and bb["onTop"], bb)
    check("the office button is there but off (dashed; switch off) and the office is not open", page.evaluate("document.getElementById('btn-office').classList.contains('off')") and not page.evaluate("SFOffice.isOpen()"))
    check("the board button is not at the foot", page.evaluate("!document.querySelector('#sb-footer-row #btn-taskboard')"))
    check("the badge is the number of decisions (3), as a red count", badge(page) == {"text": "3", "shown": True}, badge(page))
    mod = mod_of(page)
    key = "⌘⇧B" if mod == "Meta" else "Ctrl+Shift+B"
    check("the tooltip says what it is, how many wait, and the shortcut", "看板" in bb["tip"] and "3" in bb["tip"] and key in bb["tip"] and bb["label"] == bb["tip"], bb)
    check("the guide mark is on it", page.evaluate("document.getElementById('btn-taskboard').getAttribute('data-guide')") == "taskboard")
    if shoot:
        H.SHOTS.mkdir(exist_ok=True)
        page.locator("#sidebar").screenshot(path=str(H.SHOTS / "board-anywhere-button-terminal.png"))

    print(f"[{engine}] the button opens the board on its own")
    calls0 = H.calls(page, "office_helpers")
    page.click("#btn-taskboard")
    fr = board_frame(page)
    ov = page.evaluate("""() => { const o = document.getElementById('sfo-overlay'); return {cls: o.className, close: !!o.querySelector('.sfo-close'), label: o.getAttribute('aria-label')}; }""")
    check("the overlay is the board alone: no office close button, labelled 看板", "sfo-board" in ov["cls"] and not ov["close"] and ov["label"] == "看板", ov)
    check("the sheet is open on the person's board, titled 任務看板", fr.evaluate("document.getElementById('vTitle').textContent") == "任務看板"
          and fr.evaluate("!document.getElementById('wall').hidden"))
    check("no floor: the canvas and the office's bars are not shown", fr.evaluate("""() => ['stage', 'top', 'legend', 'tools'].every(id => { const e = document.getElementById(id); return !e || getComputedStyle(e).display === 'none'; })"""))
    check("the office's helper call is not made (the switch is off)", H.calls(page, "office_helpers") == calls0)
    lanes = lane_of(fr)
    check("the three decisions are in the red 緊急 lane", [lanes.get(i) for i in (31, 32, 33)] == ["緊急"] * 3, lanes)
    check("…no switcher without a paired computer", fr.evaluate("!document.querySelector('.wscope')"))
    if shoot:
        page.screenshot(path=str(H.SHOTS / "board-anywhere-open.png"))

    print(f"[{engine}] closing: Esc, ✕, outside, the shortcut")
    page.keyboard.press("Escape")
    check("Esc closes the whole overlay", overlay_gone(page))
    page.click("#btn-taskboard")
    fr = board_frame(page)
    fr.click("#vClose")
    check("✕ closes the whole overlay", overlay_gone(page))
    page.click("#btn-taskboard")
    fr = board_frame(page)
    page.mouse.click(6, 6)
    check("a click outside the sheet closes the whole overlay", overlay_gone(page))
    page.click("#btn-taskboard")
    board_frame(page)
    page.keyboard.press(f"{mod}+Shift+B")
    check(f"the shortcut ({key}) closes it with the focus inside the sheet", overlay_gone(page))
    page.evaluate("document.body.focus()")
    page.keyboard.press(f"{mod}+Shift+B")
    fr = board_frame(page)
    check("…and opens it from the page", fr.evaluate("__office.view()") == "board")
    page.keyboard.press("Escape")
    overlay_gone(page)
    page.keyboard.press(f"{mod}+Shift+B")
    board_frame(page)
    page.keyboard.press(f"{mod}+Shift+B")
    overlay_gone(page)
    page.keyboard.press("Shift+B")
    page.wait_for_timeout(300)
    check("…Shift+B alone opens nothing", not page.evaluate("!!document.getElementById('sfo-overlay')"))

    print(f"[{engine}] 回覆 on a decision card")
    page.click("#btn-taskboard")
    fr = board_frame(page)
    fr.click('#vBody .nb[data-id="32"]')
    fr.wait_for_selector("#vOver .wcard", timeout=5000)
    btns = fr.evaluate("[...document.querySelectorAll('#vOver .wb-actions > button')].map(b => b.textContent)")
    check("a decision card: 回覆 first, then 留言 (no 我做好了)", btns == ["回覆", "留言"], btns)
    fr.click('#vOver [data-act="areply"]')
    ph = fr.evaluate("document.querySelector('#vOver textarea').getAttribute('placeholder')")
    tell = fr.evaluate("(() => { const l = document.querySelector('#vOver .wtell'); return l ? {t: l.innerText.trim(), on: l.querySelector('input').checked} : null; })()")
    check("the form: a text box 「你的決定或回答」, 送出回覆, and 「也告訴 示範工程師」 ticked", ph == "你的決定或回答" and tell == {"t": "也告訴 示範工程師", "on": True}
          and fr.evaluate("document.querySelector('#vOver .wform .ok').textContent") == "送出回覆", (ph, tell))
    if shoot:
        fr.fill("#vOver textarea", "示範：加班，週五前交")
        page.wait_for_timeout(600)
        page.screenshot(path=str(H.SHOTS / "board-anywhere-reply.png"))
        fr.fill("#vOver textarea", "")
    page.evaluate("window.__args.length = 0")
    fr.click("#vOver .wform .ok")
    page.wait_for_timeout(500)
    msg = fr.evaluate("(document.querySelector('#vOver .wmsg') || {}).innerText || ''")
    check("an empty reply is refused in words, and nothing is called", "要先寫" in msg and args_of(page, "task_user_act") == [] and args_of(page, "chat_send") == [], (msg, args_of(page, "task_user_act")))
    fr.fill("#vOver textarea", "示範：加班，週五前交")
    fr.click("#vOver .wform .ok")
    page.wait_for_function("SFBoard.count() === 2", timeout=6000)
    check("task_user_act(32, done, the text) was called", args_of(page, "task_user_act") == [["32", "done", "示範：加班，週五前交"]], args_of(page, "task_user_act"))
    try:   # the line to the bot goes out after the store's answer: wait for it rather than look at once
        page.wait_for_function("window.__args.some(a => a[0] === 'chat_send')", timeout=4000)
    except Exception:
        pass
    sends = args_of(page, "chat_send")
    check("one line went to the bot that asked (the engineer's tab, s701), not as a draft-clear",
          sends == [["s701", "（看板）#32「示範要不要加班趕工」我的回覆：示範：加班，週五前交", False]], sends)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe').contentWindow; return !f.document.querySelector('#vBody .nt[data-id="32"]'); }""", timeout=8000)
    check("the board no longer has #32 and its card closed", fr.evaluate("document.getElementById('vOver').hidden") and 32 not in lane_of(fr), lane_of(fr))
    check("the badge is 2 (the same refresh fed it)", badge(page) == {"text": "2", "shown": True}, badge(page))

    print(f"[{engine}] 回覆 with 也告訴 unchecked, and for a requester with no tab")
    fr.click('#vBody .nb[data-id="33"]')
    fr.wait_for_selector("#vOver .wcard", timeout=5000)
    fr.click('#vOver [data-act="areply"]')
    fr.fill("#vOver textarea", "示範：寄給全部人")
    fr.uncheck("#vOver .wtell input")
    page.evaluate("window.__args.length = 0")
    fr.click("#vOver .wform .ok")
    page.wait_for_function("SFBoard.count() === 1", timeout=6000)
    check("task_user_act(33, done, the text) and nothing is sent", args_of(page, "task_user_act") == [["33", "done", "示範：寄給全部人"]] and args_of(page, "chat_send") == [],
          (args_of(page, "task_user_act"), args_of(page, "chat_send")))
    fr.click('#vBody .nb[data-id="31"]')
    fr.wait_for_selector("#vOver .wcard", timeout=5000)
    fr.click('#vOver [data-act="areply"]')
    check("#31's requester has no tab: no 「也告訴」 box (nobody to send to)", fr.evaluate("!document.querySelector('#vOver .wtell input')"))
    miss = fr.evaluate("(document.querySelector('#vOver .wmiss') || {}).innerText || ''")
    check("…and the card says so instead of skipping it: 沒辦法通知 示範研究員 and why", "沒辦法通知 示範研究員：他現在沒有開著的分頁（在休息）" in miss, miss)
    if shoot:
        page.screenshot(path=str(H.SHOTS / "board-anywhere-cannot-tell.png"))
    page.evaluate("window.__actReply = {success: false, code: 'closed', message: '#31 已經結束了。'}")
    fr.fill("#vOver textarea", "示範：好")
    page.evaluate("window.__args.length = 0")
    fr.click("#vOver .wform .ok")
    page.wait_for_timeout(700)
    check("a refusal from the store is shown in the card, nothing is sent, the badge stays",
          "已經結束" in fr.evaluate("(document.querySelector('#vOver .wmsg') || {}).innerText || ''") and args_of(page, "chat_send") == [] and SFB(page) == 1)
    page.evaluate("window.__actReply = null")
    fr.click('#vOver [data-act="acancel"]')
    fr.click('#vOver [data-act="dclose"]')

    print(f"[{engine}] the other cards keep their buttons")
    fr.click('#vBody .nb[data-id="15"]')
    fr.wait_for_selector("#vOver .wcard", timeout=5000)
    b15 = fr.evaluate("[...document.querySelectorAll('#vOver .wb-actions > button')].map(b => b.textContent)")
    fr.click('#vOver [data-act="dclose"]')
    fr.click('#vBody .nb[data-id="14"]')
    fr.wait_for_selector("#vOver .wcard", timeout=5000)
    b14 = fr.evaluate("[...document.querySelectorAll('#vOver .wb-actions > button')].map(b => b.textContent)")
    check("a task waiting on you: 我做好了 and 留言; someone else's task: only 留言", b15 == ["我做好了", "留言"] and b14 == ["留言"], (b15, b14))
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    overlay_gone(page)

    print(f"[{engine}] the badge follows the store: gone at 0, the button gone only without a store")
    page.evaluate("window.__tasks.decisions = []; window.__tasks.open = window.__tasks.open.filter(t => t.kind !== 'decision')")
    page.evaluate("SFBoard.poll()")
    page.wait_for_function("SFBoard.count() === 0", timeout=4000)
    check("no decisions: no badge, the button is still there", badge(page)["shown"] is False and button_box(page)["visible"], (badge(page), button_box(page)))
    check("…and the tooltip has no count", "等你" not in button_box(page)["tip"], button_box(page)["tip"])
    page.evaluate("window.__tasks = {success: false, code: 'unavailable', message: 'no store'}")
    page.evaluate("SFBoard.poll()")
    page.wait_for_function("document.getElementById('btn-taskboard').hidden", timeout=4000)
    check("the tasks store is unavailable: the button is hidden", not button_box(page)["visible"])
    page.evaluate("window.__tasks = {success: true, decisions: [], stuck: [], open: []}")
    page.evaluate("SFBoard.poll()")
    page.wait_for_function("!document.getElementById('btn-taskboard').hidden", timeout=4000)
    check("…and back when it answers again", button_box(page)["visible"])
    check("no page error so far", not errors, errors[:3])
    page.close()

    print(f"[{engine}] the count is polled about every 20 s, only while the window is visible")
    page = new_page(browser, errors, office=False, style="cli", clock=True)
    start(page, base)
    n0 = H.calls(page, "tasks_overview")
    page.clock.run_for(21000)
    page.wait_for_timeout(300)
    n1 = H.calls(page, "tasks_overview")
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})")
    page.clock.run_for(65000)
    page.wait_for_timeout(300)
    n2 = H.calls(page, "tasks_overview")
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_timeout(400)
    n3 = H.calls(page, "tasks_overview")
    page.clock.run_for(10000)
    page.wait_for_timeout(300)
    n4 = H.calls(page, "tasks_overview")
    check("one more call after 21 s", n1 == n0 + 1, (n0, n1))
    check("none while the window is hidden (65 s)", n2 == n1, (n1, n2))
    check("one at once when it is visible again, and not another within 10 s", n3 == n2 + 1 and n4 == n3, (n2, n3, n4))
    page.close()

    print(f"[{engine}] chat view, office switch off")
    page = new_page(browser, errors, office=False, style="chat")
    start(page, base)
    page.wait_for_function("document.getElementById('sb-dock') !== null && SFBoard.count() === 3", timeout=10000)
    bb = button_box(page)
    check("the chat view's sidebar has the button, visible, in the entry row", bb["visible"] and bb["inRow"] and bb["onTop"], bb)
    check("…with the same badge (3)", badge(page) == {"text": "3", "shown": True}, badge(page))
    if shoot:
        page.locator("#sidebar").screenshot(path=str(H.SHOTS / "board-anywhere-button-chat.png"))
        page.screenshot(path=str(H.SHOTS / "board-anywhere-chat-view.png"))
    page.keyboard.press(f"{mod}+Shift+B")
    fr = board_frame(page)
    check("the shortcut opens the board from the chat view too", lane_of(fr).get(32) == "緊急")
    page.keyboard.press("Escape")
    overlay_gone(page)
    page.click("#btn-taskboard")
    board_frame(page)
    page.keyboard.press("Escape")
    check("the button opens it, Esc closes it", overlay_gone(page))
    page.close()

    print(f"[{engine}] a paired computer's board: 這台 / 隔壁")
    page = new_page(browser, errors, office=False, style="cli", peers=True)
    start(page, base, peers=True)
    page.click("#btn-taskboard")
    fr = board_frame(page)
    fr.wait_for_selector(".wscope", timeout=8000)
    sw = fr.evaluate("[...document.querySelectorAll('.wscope .wsc')].map(b => [b.textContent, b.getAttribute('aria-pressed')])")
    check("the switcher: 這台 (pressed) and 隔壁：<電腦> for each paired computer with a board", sw == [["這台", "true"], ["隔壁：示範電腦甲", "false"], ["隔壁：示範電腦乙", "false"]], sw)
    if shoot:
        page.screenshot(path=str(H.SHOTS / "board-anywhere-switcher.png"))
    before = {n: H.calls(page, n) for n in ("task_detail", "task_user_act")}
    fr.click('.wscope [data-scope="pid-a"]')
    page.wait_for_timeout(500)
    check("隔壁：示範電腦甲 shows that computer's board", fr.evaluate("document.getElementById('vTitle').textContent") == "隔壁：示範電腦甲 的白板"
          and 41 in lane_of(fr) and 32 not in lane_of(fr), (fr.evaluate("document.getElementById('vTitle').textContent"), lane_of(fr)))
    check("…it is the pressed one now, and the overlay is still open", fr.evaluate("document.querySelector('.wscope .wsc[aria-pressed=true]').textContent") == "隔壁：示範電腦甲"
          and page.evaluate("!!document.getElementById('sfo-overlay')"))
    fr.click('#vBody .nb[data-id="41"]')
    fr.wait_for_selector("#vOver .wcard", timeout=5000)
    check("its card is read only: no 回覆 / 我做好了 / 留言, and it says so", fr.evaluate("!document.querySelector('#vOver .wb-actions [data-act=areply], #vOver [data-act=adone], #vOver [data-act=anote]')")
          and "只能看" in fr.evaluate("document.querySelector('#vOver .wpeer').innerText"))
    check("…and the card has no button that leaves for the floor (the names are plain text)", fr.evaluate("!document.querySelector('#vOver [data-act=person], #vOver [data-act=tellmain]')"))
    check("no task_detail and no task_user_act for a paired computer's task", H.calls(page, "task_detail") == before["task_detail"] and H.calls(page, "task_user_act") == before["task_user_act"])
    fr.click('#vOver [data-act="dclose"]')
    fr.click('.wscope [data-scope="pid-b"]')
    page.wait_for_timeout(400)
    check("the other computer's board next", fr.evaluate("document.getElementById('vTitle').textContent") == "隔壁：示範電腦乙 的白板")
    fr.click('.wscope [data-scope=""]')
    page.wait_for_timeout(400)
    check("這台 comes back to the person's own board", fr.evaluate("document.getElementById('vTitle').textContent") == "任務看板" and 32 in lane_of(fr)
          and fr.evaluate("document.querySelector('.wscope .wsc[aria-pressed=true]').textContent") == "這台")
    fr.click('.wscope [data-scope=""]')
    page.wait_for_timeout(300)
    check("pressing the pressed one again changes nothing (it does not close)", page.evaluate("!!document.getElementById('sfo-overlay')") and fr.evaluate("__office.view()") == "board")
    page.keyboard.press("Escape")
    check("Esc closes it", overlay_gone(page))
    page.close()

    print(f"[{engine}] office switch on: both buttons; the office and its lobby whiteboard as before")
    page = new_page(browser, errors, office=True, style="cli")
    start(page, base)
    page.wait_for_selector("#btn-office", timeout=10000)
    order = page.evaluate("[...document.querySelectorAll('#sb-entry button')].map(b => b.id)")
    check("the board button sits right after the office's in the entry row", order.index("btn-office") + 1 == order.index("btn-taskboard"), order)
    page.click("#btn-taskboard")
    board_frame(page)
    check("with the switch on the board button still opens the board alone (not the floor)", page.evaluate("document.getElementById('sfo-overlay').classList.contains('sfo-board')"))
    page.keyboard.press("Escape")
    overlay_gone(page)
    fr = H.open_office(page, copy.deepcopy(TASKS))
    check("the office opens from its own button path: the floor is drawn, the overlay is the office's", page.evaluate("!document.getElementById('sfo-overlay').classList.contains('sfo-board')")
          and bool(page.evaluate("document.querySelector('#sfo-overlay .sfo-close') !== null")) and fr.evaluate("getComputedStyle(document.getElementById('stage')).display") != "none")
    pt, tip, cursor = H.wall_click(page, fr, "board")
    check("its lobby whiteboard is a hit target and opens the sheet, as before", tip == "任務看板" and fr.evaluate("__office.view()") == "board")
    check("…the sheet has no 這台 / 隔壁 switcher (the floor has its rooms)", fr.evaluate("!document.querySelector('.wscope')"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc closes the sheet only; the office stays open", fr.evaluate("__office.view()") is None and page.evaluate("SFOffice.isOpen()"))
    page.click("#sfo-overlay .sfo-close")
    page.wait_for_timeout(300)
    page.keyboard.press(f"{mod}+Shift+B")
    board_frame(page)
    page.keyboard.press("Escape")
    overlay_gone(page)
    page.evaluate("SFOffice.open()")
    page.wait_for_function("SFOffice.isOpen() && !SFOffice.isBoard()", timeout=8000)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.board() !== null; } catch (e) { return false; } }""", timeout=10000)
    page.evaluate("SFBoard.toggle()")
    page.wait_for_timeout(500)
    fr = H.office(page)
    check("with the office open, the button / shortcut opens its lobby whiteboard (toggle), not a second overlay",
          fr.evaluate("__office.view()") == "board" and page.evaluate("document.querySelectorAll('#sfo-overlay').length") == 1 and not page.evaluate("SFOffice.isBoard()"))
    page.evaluate("SFBoard.toggle()")
    page.wait_for_timeout(400)
    check("…and again closes the sheet", fr.evaluate("__office.view()") is None and page.evaluate("SFOffice.isOpen()"))
    page.evaluate("SFOffice.close()")
    page.close()
    check("no page error or console error", not errors, errors[:5])


def SFB(page):
    return page.evaluate("SFBoard.count()")


H.SHOTS.mkdir(exist_ok=True)
srv = H.serve()
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
