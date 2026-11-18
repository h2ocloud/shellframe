#!/usr/bin/env python3
"""「待你決定」 (web/pending.js, pending.css): what waits on the person, always in reach on the right. In the real page with a
stand-in Python side.

Asked for: the questions an agent waits on the person for were buried in long conversations. Now:
  - chat view: a docked column on the right when the window is wide (the conversation keeps its column and is not covered), a thin
    strip with the red count when it is narrower (900, 1280), a click slides the list over the conversation as a drawer, and an
    outside click or Esc closes it; folding the docked column is remembered per view
  - terminal view: a small capsule at the pane's top-right; the terminal is never resized (same cols and rows, no resize call to
    the tab) whether the capsule shows, the drawer is open or closed, the count goes 0 -> 1 (one pulse), and the chat view docks or not
  - 分頁在等你: a tab whose light is the decision one, its question and options as buttons; a button answers through chat_answer
    (the chat view's route); the entry goes when the tab's light is no longer that; nothing reads a screen on a timer
  - 等你決定: tasks_overview's decisions, overdue first, then due soon, then the one that has waited longest; the red number is the
    sidebar button's badge (the same read); a card opens in place: next step, history (task_detail, once), an answer box (empty is
    refused, nothing sent), 也告訴 <asker> (task_user_act + one chat line / unchecked: only task_user_act), 跳到那個分頁,
    在白板上看 (the board opens on that card, also when the board is already open)
  - 隔壁：X 有 N 件等你: one line per paired computer that opens its read-only board
  - the window hidden: no tasks_overview, link_peer_tasks, task_detail, no question asked of a tab; interface size 1.3 breaks nothing
  - no page error
Demo data only. Headless Chromium, and WebKit when playwright has it. Screenshots (Chromium): qa-shots/pending-*.png.
Run: .venv/bin/python tests_pending_rail_ui.py
"""
import copy
import json
import pathlib
import subprocess
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

task, NOW = H.task, H.NOW
BASE_STATUS = copy.deepcopy(H.STATUS)
WAITING_STATUS = copy.deepcopy(H.STATUS["s702"])      # the tab that waits on the person (the harness's)
# four decisions for the person. asked: #34 first (the longest ago), then #31, #32, #33. #32 is overdue, #31 due soon.
D32 = task(32, "示範：趕不上期限的那件", "waiting", "user", "eng", "user", "回「加班」或「延後」", NOW - 3600, 800, "decision")
D31 = task(31, "示範方案 A 或 B", "waiting", "user", "res", "user", "等你選一個", NOW + 7200, 1200, "decision")
D34 = task(34, "示範週報要寄給誰", "waiting", "user", "chief", "user", "等你點頭", None, 90000, "decision")
D33 = task(33, "示範要不要多開一個分頁", "waiting", "user", "chief", "user", "等你說好或不要", None, 500, "decision")
DECS = [D33, D31, D34, D32]
ORDER = [32, 31, 34, 33]
OPEN = [x for x in H.T_OPEN if x["id"] != 31] + DECS
TASKS = {"success": True, "decisions": DECS, "stuck": [], "open": OPEN}
NONE = {"success": True, "decisions": [], "stuck": [], "open": [x for x in H.T_OPEN if x["id"] != 31]}
EVENTS = [{"id": 200 + i, "task_id": 32, "ts": NOW - i * 900, "actor": "eng" if i % 2 else "chief", "kind": "note", "text": f"示範第 {i} 筆歷程"} for i in range(7)]
QUESTION = {"id": "q1", "kind": "menu", "reason": "示範：要選方案 A 還是 B？", "options": [{"n": 1, "text": "方案 A"}, {"n": 2, "text": "方案 B"}]}
PEERS = {"peers": {
    "pid-a": {"name": "示範電腦甲", "addr": "示範電腦甲", "primary": "builder",
              "bots": [{"role": "builder", "label": "示範建置員", "state": "awake", "primary": True, "sid": "r10", "activity": "idle",
                        "avatar": {"shape": "ghost", "color": "green"}, "responsibility": "建置與測試"}], "tabs": {}},
    "pid-b": {"name": "示範電腦乙", "addr": "示範電腦乙", "primary": "", "bots": [], "tabs": {}}}}


def peer_rows(n):
    dec = [task(41 + i, f"示範隔壁的決定 {i}", "waiting", "user", "user", "user", "等那台的使用者", NOW + 7200, 1200, "decision") for i in range(n)]
    return {"decisions": dec, "stuck": [], "open": list(dec), "state": "ok", "at": NOW}


PEER_TASKS = {"peers": {"pid-a": peer_rows(2), "pid-b": peer_rows(1)}, "pending": 0}

ACT = """      case 'task_user_act': {
        const id = +args[0], r = window.__actReply || {success: true, message: 'ok', tell: ''};
        if (r.success && args[1] === 'done') for (const k of ['decisions', 'stuck', 'open']) window.__tasks[k] = window.__tasks[k].filter(t => t.id !== id);
        return R(r); }
      case 'chat_send': window.__sent.push([args[0], args[1]]); return R({success: true});
      case 'chat_view': { const sid = args[0], st = (window.__status[sid] || {}).state === 'decision';
        return R({success: true, readable: true, provider: 'claude', activity: st ? 'decision' : 'idle', decision: st ? window.__decision : null, turns: [], rev: 'r' + Math.random()}); }
      case 'chat_answer': { if (args[2] !== window.__decision.id) return R({success: false, code: 'stale'});
        window.__answers.push(args); window.__status.s702 = {state: 'idle', dot: '', summary: 'idle'}; return R({success: true}); }
      case 'task_detail': return R({success: true, task: {id: +args[0], title: 'x', events: window.__events}});
      case 'link_peer_tasks': return R(window.__peerTasks);
      case 'tasks_overview': return R(window.__tasks);
      default: return R('{}');"""


def stub():
    s = H.stub()
    assert s.count("      default: return R('{}');") == 1 and s.count("      case 'tasks_overview': return R(window.__tasks);\n") == 1
    assert s.count("      case 'task_detail': return R({success: true, task: {id: +args[0], title: 'x', events: window.__events}});\n") == 1
    s = s.replace("      case 'tasks_overview': return R(window.__tasks);\n", "")
    s = s.replace("      case 'task_detail': return R({success: true, task: {id: +args[0], title: 'x', events: window.__events}});\n", "")
    s = s.replace("      default: return R('{}');", ACT)
    return s + ("\nif (window.top === window) { window.__peerTasks = %s; window.__sent = []; window.__answers = []; window.__decision = %s; }\n"
                % (json.dumps(PEER_TASKS, ensure_ascii=False), json.dumps(QUESTION, ensure_ascii=False)))


def new_page(browser, errors, style="chat", peers=False, tasks=None, waiting=True, width=1280, height=800, clock=False):
    cfg = copy.deepcopy(H.CFG)
    cfg["settings"].update({"experimental_office": False, "ui_style": style})
    H.CFG = cfg
    H.PEERS = PEERS if peers else {"peers": {}}
    H.TASKS = copy.deepcopy(tasks if tasks is not None else TASKS)
    H.EVENTS = EVENTS
    st = copy.deepcopy(BASE_STATUS)
    if not waiting:
        st["s702"] = {"state": "idle", "dot": "", "summary": "idle"}
    H.STATUS = st
    page = browser.new_page(viewport={"width": width, "height": height})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    if clock:
        page.clock.install()
    page.add_init_script(stub())
    return page


def start(page, base, peers=False, count=None):
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFBoard && window.SFPending && window.SFBots && SFBots.enabled() && window.__sfHost && Object.keys(window.__sfHost.sessions()).length >= 3", timeout=20000)
    page.wait_for_selector("#pend-cap, #pend-panel", state="attached", timeout=10000)
    if peers:
        page.wait_for_function("Object.keys(SFBots.peerBots()).length === 2", timeout=20000)
    if count is not None:
        page.wait_for_function(f"SFBoard.count() === {count}", timeout=10000)
    page.wait_for_timeout(1500)


def geo(page, sel):
    return page.evaluate("""(sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
        return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height, vis: cs.visibility !== 'hidden' && cs.display !== 'none' && r.width > 0 && r.height > 0 && !e.hidden}; }""", sel)


def term_size(page):
    return page.evaluate("(() => { const t = window.__sfHost.sessions().s701.term; return [t.cols, t.rows]; })()")


def resizes(page):
    return H.calls(page, "resize")


def args_of(page, name):
    return page.evaluate("(n) => window.__args.filter(a => a[0] === n).map(a => a[1])", name)


def rail_ids(page):
    return page.evaluate("[...document.querySelectorAll('#pend-cards .pend-card')].map(e => +e.dataset.id)")


def badge(page):
    return page.evaluate("(() => { const b = document.querySelector('#btn-taskboard .sfb-badge'); return b && !b.hidden ? b.textContent : ''; })()")


def overflow_x(page):
    return page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")


def run(browser, base, engine):
    shoot = engine == "chromium"
    errors = []
    tag = f"[{engine}]"

    # ── the pure rules ────────────────────────────────────────────────────
    print(f"{tag} the order: overdue, due soon, then the longest waiting")
    page = new_page(browser, errors, style="chat", width=1920, height=900)
    start(page, base, count=4)
    got = page.evaluate("(rows) => SFPending.sortDecisions(rows, Date.now() / 1000).map(r => r.id)", DECS)
    check("sortDecisions: #32 (overdue), #31 (due soon), #34 (asked first), #33", got == ORDER, got)
    check("it does not change the list it is given", page.evaluate("(rows) => { const a = rows.map(r => r.id); SFPending.sortDecisions(rows, 1); return rows.map(r => r.id).join() === a.join(); }", DECS))

    # ── chat view, 1920: docked ───────────────────────────────────────────
    print(f"{tag} chat view at 1920: the docked column")
    page.wait_for_function("document.body.classList.contains('sf-chat-shown')", timeout=10000)
    page.wait_for_function("document.querySelectorAll('#pend-cards .pend-card').length === 4 && document.querySelectorAll('#pend-tabs .pend-opt').length === 2", timeout=15000)
    p, c = geo(page, "#pend-panel"), geo(page, "#chat-layer")
    check("docked: body.sf-pend-docked, the column shows, about 300 px wide", page.evaluate("document.body.classList.contains('sf-pend-docked')") and p["vis"] and 295 <= p["w"] <= 305, p)
    check("the conversation is not covered: the chat layer ends where the column begins", abs(c["r"] - p["l"]) <= 1 and geo(page, "#chat-col")["r"] <= p["l"] + 1, (c, p))
    check("no capsule or strip while docked", not (geo(page, "#pend-cap") or {}).get("vis"))
    check("the cards follow the order", rail_ids(page) == ORDER, rail_ids(page))
    check("the red number is the board button's badge (4)", page.evaluate("document.querySelector('#pend-panel .pend-n').dataset.n") == "4" == badge(page) == str(page.evaluate("SFBoard.count()")), (badge(page),))
    tabs = page.evaluate("[...document.querySelectorAll('#pend-tabs .pend-tab')].map(e => ({sid: e.dataset.sid, q: e.querySelector('.pend-q').textContent, opts: [...e.querySelectorAll('.pend-opt')].map(b => b.textContent)}))")
    check("分頁在等你 lists the waiting tab with its question and its options as buttons", len(tabs) == 1 and tabs[0]["sid"] == "s702" and "方案 A 還是 B" in tabs[0]["q"] and len(tabs[0]["opts"]) == 2, tabs)
    check("the tab waiting is its own mark, the red number is not changed by it", page.evaluate("SFPending.state().waiting.join()") == "s702" and badge(page) == "4")
    check("the sidebar button and the rail read once (tasks_overview is not asked twice in a row)", H.calls(page, "tasks_overview") <= 3, H.calls(page, "tasks_overview"))
    if shoot:
        page.screenshot(path=str(H.SHOTS / "pending-chat-docked-1920.png"))
    check("no horizontal scroll", not overflow_x(page))
    page.close()

    # ── chat view 1280 and 900: strip + drawer ────────────────────────────
    for width in (1280, 900):
        print(f"{tag} chat view at {width}: the strip and the drawer")
        page = new_page(browser, errors, style="chat", width=width)
        start(page, base, count=4)
        page.wait_for_function("document.body.classList.contains('sf-chat-shown')", timeout=10000)
        cap, p, c = geo(page, "#pend-cap"), geo(page, "#pend-panel"), geo(page, "#chat-layer")
        check(f"{width}: a thin strip at the right edge with the red count, no docked column", cap["vis"] and cap["w"] <= 40 and abs(cap["r"] - width) <= 1
              and page.evaluate("document.body.classList.contains('sf-pend-strip')") and not page.evaluate("document.body.classList.contains('sf-pend-docked')")
              and page.evaluate("document.querySelector('#pend-cap .pend-n').textContent") == "4", (cap, p))
        check(f"{width}: the conversation is not under the strip", c["r"] <= cap["l"] + 1, (c, cap))
        check(f"{width}: the panel is folded away (not visible, not focusable)", not p["vis"] and page.evaluate("document.getElementById('pend-panel').hasAttribute('inert')"))
        size0, rz0 = term_size(page), resizes(page)
        page.wait_for_function("SFPending.state().waiting.length === 1", timeout=15000)
        page.click("#pend-cap")
        page.wait_for_function("document.getElementById('pend-panel').classList.contains('open') && document.querySelectorAll('#pend-tabs .pend-tab').length === 1", timeout=15000)
        page.wait_for_timeout(350)
        p = geo(page, "#pend-panel")
        check(f"{width}: a click slides the list in over the conversation", p["vis"] and p["r"] <= width + 1 and p["l"] < geo(page, "#chat-layer")["r"] and p["w"] <= width, p)
        check(f"{width}: it lists the waiting tab and the cards", len(rail_ids(page)) == 4 and page.evaluate("document.querySelectorAll('#pend-tabs .pend-tab').length") == 1)
        if shoot and width == 1280:
            page.screenshot(path=str(H.SHOTS / "pending-chat-drawer-1280.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check(f"{width}: Esc closes the drawer", not page.evaluate("document.getElementById('pend-panel').classList.contains('open')"))
        page.click("#pend-cap")
        page.wait_for_timeout(300)
        page.mouse.click(p["l"] - 60, 400)
        page.wait_for_timeout(300)
        check(f"{width}: a click outside closes it", not page.evaluate("document.getElementById('pend-panel').classList.contains('open')"))
        check(f"{width}: the terminal under the chat view was not resized", term_size(page) == size0 and resizes(page) == rz0, (size0, term_size(page), rz0, resizes(page)))
        check(f"{width}: no horizontal scroll", not overflow_x(page))
        page.close()

    # ── a chat view too narrow for a strip ─────────────────────────────────
    print(f"{tag} chat view with no width to give: the capsule floats, the conversation keeps every pixel")
    page = new_page(browser, errors, style="chat", width=560, height=800)
    start(page, base, count=4)
    c, w = geo(page, "#chat-layer"), geo(page, "#terminal-wrap")
    cap = geo(page, "#pend-cap")
    check("a narrow chat layer (< 420 px): no strip, no docked column; the chat layer reaches the window's edge", c["w"] < 420 and not page.evaluate("document.body.classList.contains('sf-pend-strip') || document.body.classList.contains('sf-pend-docked')")
          and abs(c["r"] - w["r"]) <= 1, (c, w))
    check("…and the capsule floats under the header, inside the view", cap["vis"] and page.evaluate("document.getElementById('pend-cap').classList.contains('capsule')") and cap["r"] <= c["r"] and cap["t"] >= 40, cap)
    page.click("#pend-cap")
    page.wait_for_timeout(500)
    check("…and it opens the drawer, no wider than the view", geo(page, "#pend-panel")["vis"] and geo(page, "#pend-panel")["w"] <= w["w"] + 1 and not overflow_x(page), geo(page, "#pend-panel"))
    page.close()

    # ── fold, and remembered ───────────────────────────────────────────────
    print(f"{tag} folding the docked column is remembered per view")
    page = new_page(browser, errors, style="chat", width=1920, height=900)
    start(page, base, count=4)
    page.wait_for_function("window.__sfHost.sessions().s701 && window.__sfHost.sessions().s701.term", timeout=15000)
    size0, rz0 = term_size(page), resizes(page)
    page.click("#pend-panel .pend-x")
    page.wait_for_timeout(300)
    check("folded: the docked column becomes the strip", page.evaluate("document.body.classList.contains('sf-pend-strip')") and (geo(page, "#pend-cap") or {}).get("vis"))
    page.reload()
    page.wait_for_function("window.SFPending && document.getElementById('pend-cap')", timeout=20000)
    page.wait_for_timeout(1200)
    check("…and still folded after a reload; the strip docks the list again on a click", page.evaluate("document.body.classList.contains('sf-pend-strip')"))
    size1, rz1 = term_size(page), resizes(page)
    page.click("#pend-cap")
    page.wait_for_timeout(300)
    check("a click on the strip in a wide window docks it", page.evaluate("document.body.classList.contains('sf-pend-docked')"))
    page.wait_for_timeout(900)
    check("folding and docking again never resize the terminal (same cols and rows, no resize call to the tab)", term_size(page) == size0 == size1 and resizes(page) == rz0 == rz1,
          (size0, size1, term_size(page), rz0, rz1, resizes(page)))
    page.evaluate("SFChat.setMode('s701', 'cli')")           # the page's own mode switch re-fits the terminal (one call, the same size): not the list's doing
    page.wait_for_timeout(600)
    page.evaluate("SFChat.setMode('s701', 'chat')")
    page.wait_for_timeout(900)
    check("…and the chat/terminal switch while docked leaves the terminal's cols and rows as they were", term_size(page) == size1, (size1, term_size(page)))
    page.evaluate("document.documentElement.style.setProperty('--ui-scale', '1.3')")
    page.wait_for_timeout(700)
    p, c = geo(page, "#pend-panel"), geo(page, "#chat-layer")
    check("interface size 1.3 in the chat view: the docked column grows to ~390 px and the layer still ends where it begins",
          p["vis"] and 380 <= p["w"] <= 400 and abs(c["r"] - p["l"]) <= 1 and not overflow_x(page), (p, c))
    page.set_viewport_size({"width": 1280, "height": 900})
    page.wait_for_timeout(700)
    cap, c = geo(page, "#pend-cap"), geo(page, "#chat-layer")
    check("...and at 1280 it is the strip (grown with the size) and the layer ends where the strip begins", cap and cap["vis"] and abs(c["r"] - cap["l"]) <= 1 and 30 <= cap["w"] <= 40 and not overflow_x(page), (cap, c))
    page.close()

    # ── terminal view ─────────────────────────────────────────────────────
    print(f"{tag} terminal view: the capsule and the drawer never resize the terminal")
    page = new_page(browser, errors, style="cli", tasks=NONE, waiting=False)
    start(page, base, count=0)
    page.wait_for_function("window.__sfHost.sessions().s701 && window.__sfHost.sessions().s701.term", timeout=15000)
    page.wait_for_timeout(1000)
    size0, rz0, pane0 = term_size(page), resizes(page), geo(page, ".term-pane.active")
    cap = geo(page, "#pend-cap")
    check("the capsule is at the pane's top-right", cap["vis"] and pane0["r"] - cap["r"] < 60 and cap["t"] - pane0["t"] < 30 and cap["w"] < 220, (cap, pane0))
    check("with nothing waiting it says so quietly: 待你決定 0, no red", page.evaluate("document.querySelector('#pend-cap .pend-n').textContent") == "0" and not page.evaluate("document.querySelector('#pend-cap .pend-n').classList.contains('on')"))
    page.click("#pend-cap")
    page.wait_for_timeout(500)
    check("empty drawer: it says nothing is waiting", page.evaluate("document.getElementById('pend-panel').classList.contains('empty')") and "沒有等你決定" in page.inner_text("#pend-panel"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.wait_for_function("SFPending.state().armed", timeout=20000)
    check("what is there when the window opens does not pulse (no news)", not page.evaluate("document.getElementById('pend-cap').classList.contains('pulse')"))
    page.evaluate("window.__tasks = %s; SFBoard.poll()" % json.dumps(TASKS))
    page.wait_for_function("SFBoard.count() === 4", timeout=5000)
    page.wait_for_timeout(200)
    check("the count going 0 -> 4 pulses the capsule once, with no notification", page.evaluate("document.getElementById('pend-cap').classList.contains('pulse')")
          and not page.evaluate("window.__calls.some(c => /notif|sound|beep/i.test(c))"))
    page.evaluate("window.__status.s702 = %s" % json.dumps(WAITING_STATUS))
    page.wait_for_function("SFPending.state().waiting.length === 1", timeout=15000)
    check("a tab waiting adds its own mark beside the red number", page.evaluate("!!document.querySelector('#pend-cap .pend-bang')") and page.evaluate("document.querySelector('#pend-cap .pend-n').textContent") == "4")
    if shoot:
        page.screenshot(path=str(H.SHOTS / "pending-terminal-capsule-1280.png"))
    page.click("#pend-cap")
    page.wait_for_function("document.getElementById('pend-panel').classList.contains('open')", timeout=3000)
    page.wait_for_function("document.querySelectorAll('#pend-cards .pend-card').length === 4", timeout=5000)
    page.wait_for_timeout(400)
    p = geo(page, "#pend-panel")
    check("the drawer slides over the terminal at the right edge, about 300 px", p["vis"] and 295 <= p["w"] <= 305 and abs(p["r"] - pane0["r"]) <= 1, (p, pane0))
    if shoot:
        page.screenshot(path=str(H.SHOTS / "pending-terminal-drawer-1280.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    free = page.evaluate("(() => { let p = null; window.addEventListener('keydown', e => { p = e.defaultPrevented; }); window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true})); return p; })()")
    check("with the drawer shut, Esc is left to the terminal (not taken)", free is False, free)
    check("Esc closes it (and in the terminal view the capsule is back)", not page.evaluate("document.getElementById('pend-panel').classList.contains('open')") and geo(page, "#pend-cap")["vis"])
    page.wait_for_timeout(900)
    check("the terminal kept its cols and rows and its box; no resize reached the tab", term_size(page) == size0 and geo(page, ".term-pane.active") == pane0 and resizes(page) == rz0,
          (size0, term_size(page), pane0, geo(page, ".term-pane.active"), rz0, resizes(page)))
    page.evaluate("document.documentElement.style.setProperty('--ui-scale', '1.3')")
    page.wait_for_timeout(600)
    page.click("#pend-cap")
    page.wait_for_timeout(500)
    p = geo(page, "#pend-panel")
    check("interface size 1.3: the drawer grows with it (about 390 px), inside the window, no horizontal scroll", 380 <= p["w"] <= 400 and p["r"] <= 1281 and not overflow_x(page), p)
    page.keyboard.press("Escape")
    page.close()

    # ── the terminal view's drawer is remembered; the chat style's terminal has a header ──
    print(f"{tag} the terminal view's drawer: remembered across a start; chat style's terminal under its header")
    page = new_page(browser, errors, style="cli", tasks=NONE, waiting=False)
    page.add_init_script("try { localStorage.setItem('sf.pending.v1', JSON.stringify({chat: true, term: true})); } catch (e) {}")
    start(page, base, count=0)
    check("left open in the terminal view, the drawer is open again when the window starts there", page.evaluate("SFPending.state().drawer") and geo(page, "#pend-panel")["vis"])
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    check("closing it is remembered (term: false)", page.evaluate("JSON.parse(localStorage.getItem('sf.pending.v1')).term") is False)
    page.close()
    page = new_page(browser, errors, style="chat", width=1280)
    start(page, base, count=4)
    page.evaluate("SFChat.setMode('s701', 'cli')")
    page.wait_for_function("!document.body.classList.contains('sf-chat-shown')", timeout=8000)
    page.wait_for_timeout(1500)
    size0, rz0 = term_size(page), resizes(page)
    cap, th, sw = geo(page, "#pend-cap"), geo(page, "#term-head"), geo(page, "#chat-switch")
    check("chat style, a tab on its terminal: the capsule sits under the header, inside the view, clear of the Chat/Terminal switch",
          cap["vis"] and cap["t"] >= th["b"] - 1 and (sw["b"] <= cap["t"] or sw["r"] <= cap["l"] or sw["l"] >= cap["r"]) and cap["r"] <= 1281, (cap, th, sw))
    page.click("#pend-cap")
    page.wait_for_function("document.getElementById('pend-panel').classList.contains('open')", timeout=3000)
    page.wait_for_timeout(500)
    p = geo(page, "#pend-panel")
    check("...its drawer opens under the header too", p["vis"] and p["t"] >= th["b"] - 1 and p["w"] <= 305, (p, th))
    page.keyboard.press("Escape")
    page.wait_for_timeout(900)
    check("...and the terminal was not resized", term_size(page) == size0 and resizes(page) == rz0, (size0, term_size(page), rz0, resizes(page)))
    page.close()

    # ── a tab waiting: answer, and it goes ─────────────────────────────────
    print(f"{tag} a tab waiting: answered through chat_answer, gone when the menu is")
    page = new_page(browser, errors, style="chat", width=1920, height=900)
    start(page, base, count=4)
    page.wait_for_function("document.querySelectorAll('#pend-tabs .pend-opt').length === 2", timeout=15000)
    asked = [a for a in args_of(page, "chat_view") if a[0] == "s702"]
    check("its question was asked once when the tab began to wait, not on a timer", len(asked) == 1, asked)
    page.click('#pend-tabs .pend-opt[data-n="2"]')
    page.wait_for_function("window.__answers.length === 1", timeout=5000)
    ans = page.evaluate("window.__answers[0]")
    check("the button answers as the chat view's does: chat_answer(sid, n, the question's id)", ans == ["s702", 2, "q1"], ans)
    page.wait_for_function("document.querySelectorAll('#pend-tabs .pend-tab').length === 0", timeout=15000)
    check("the entry goes once the tab's light is no longer the decision one", page.evaluate("SFPending.state().waiting.length") == 0)
    check("the red number is unchanged by it", badge(page) == "4")
    page.close()
    page = new_page(browser, errors, style="chat", width=1920, height=900)
    start(page, base, count=4)
    page.wait_for_function("document.querySelectorAll('#pend-tabs .pend-opt').length === 2", timeout=15000)
    page.evaluate("window.__decision = Object.assign({}, window.__decision, {id: 'q2'})")      # the tab asks something else meanwhile
    page.click('#pend-tabs .pend-opt[data-n="1"]')
    page.wait_for_selector("#pend-tabs .pend-note.bad", timeout=5000)
    check("a question that changed is refused (stale): nothing is sent, and it says so", page.evaluate("window.__answers.length") == 0 and "沒有送出" in page.inner_text("#pend-tabs"))
    page.wait_for_function("document.querySelector('#pend-tabs .pend-opt') && document.querySelector('#pend-tabs .pend-opt').dataset.id === 'q2'", timeout=15000)
    check("…and the new question is read (its options now carry the new id)", page.evaluate("document.querySelector('#pend-tabs .pend-opt').dataset.id") == "q2")
    page.click('#pend-tabs [data-act="jump-tab"]')
    page.wait_for_timeout(500)
    check("跳到分頁 switches to that tab", page.evaluate("window.__sfHost.active()") == "s702", page.evaluate("window.__sfHost.active()"))
    page.close()

    # ── a card: open, answer ───────────────────────────────────────────────
    print(f"{tag} a decision card: opens in place, answers, tells who asked")
    page = new_page(browser, errors, style="chat", width=1920, height=900, waiting=False)
    start(page, base, count=4)
    page.wait_for_function("document.querySelectorAll('#pend-cards .pend-card').length === 4", timeout=15000)
    check("an overdue card says 逾期, one due soon 快到期, and who asked and when", "逾期" in page.inner_text('#pend-cards .pend-card[data-id="32"]') and "快到期" in page.inner_text('#pend-cards .pend-card[data-id="31"]')
          and "示範工程師" in page.inner_text('#pend-cards .pend-card[data-id="32"]') and "問的" in page.inner_text('#pend-cards .pend-card[data-id="32"]'), page.inner_text("#pend-cards"))
    page.click('#pend-cards .pend-card[data-id="32"] .pend-card-h')
    page.wait_for_selector("#pend-cards .pend-card.open textarea", timeout=5000)
    page.wait_for_function("document.querySelectorAll('#pend-cards .pend-hist li:not(.none)').length === 4", timeout=5000)
    txt = page.inner_text("#pend-cards .pend-card.open")
    check("it opens in place: the title, the next step, the last four lines of history", "趕不上期限" in txt and "回「加班」或「延後」" in txt and "示範第 0 筆歷程" in txt and "示範第 4 筆歷程" not in txt, txt)
    check("history was asked once, for that card", [a[0] for a in args_of(page, "task_detail")] == ["32"], args_of(page, "task_detail"))
    check("the answer box and 「也告訴 示範工程師」 (ticked), 跳到, 在白板上看", page.evaluate("document.querySelector('#pend-cards [data-tell]').checked") and "也告訴 示範工程師" in txt and "跳到 示範工程師 的分頁" in txt and "在白板上看" in txt, txt)
    if shoot:
        page.screenshot(path=str(H.SHOTS / "pending-card-expanded-1920.png"))
    n0 = H.calls(page, "task_user_act")
    page.click("#pend-cards .pend-go")
    page.wait_for_timeout(400)
    check("an empty answer is refused: nothing is sent, the box says why", H.calls(page, "task_user_act") == n0 and "要先寫" in page.inner_text("#pend-cards .pend-form") and not args_of(page, "chat_send"))
    page.fill("#pend-ans", "加班，週五前交")
    page.evaluate("document.getElementById('pend-ans').blur()")
    page.wait_for_timeout(2500)
    check("typing survives the status pushes and the list refresh (the box keeps its text)", page.input_value("#pend-ans") == "加班，週五前交" and page.evaluate("SFPending.state().openId") == 32)
    page.click("#pend-cards .pend-go")
    page.wait_for_function("window.__args.some(a => a[0] === 'chat_send')", timeout=5000)
    act = args_of(page, "task_user_act")[-1]
    sent = page.evaluate("window.__sent")
    check("task_user_act(32, 'done', the answer)", act == ["32", "done", "加班，週五前交"], act)
    check("one line to the bot that asked, in the board's own wording, into its tab (s701)", len(sent) == 1 and sent[0][0] == "s701" and sent[0][1] == "（看板）#32「示範：趕不上期限的那件」我的回覆：加班，週五前交", sent)
    page.wait_for_function("SFBoard.count() === 3", timeout=15000)
    check("the card is gone, and the red number and the badge are both 3", rail_ids(page) == [31, 34, 33] and badge(page) == "3" and page.evaluate("document.querySelector('#pend-panel .pend-n').dataset.n") == "3", (rail_ids(page), badge(page)))
    # unchecked: nothing to the asker
    page.click('#pend-cards .pend-card[data-id="34"] .pend-card-h')
    page.wait_for_selector("#pend-cards .pend-card.open textarea", timeout=5000)
    page.uncheck("#pend-cards [data-tell]")
    page.fill("#pend-ans", "寄給示範小組")
    page.click("#pend-cards .pend-go")
    page.wait_for_function("SFBoard.count() === 2", timeout=15000)
    check("unticked 「也告訴」: task_user_act only, no second line to the asker", [a[0] for a in args_of(page, "task_user_act")][-1] == "34" and len(page.evaluate("window.__sent")) == 1)
    # a card whose asker has no tab: no tick box, still answers
    page.click('#pend-cards .pend-card[data-id="31"] .pend-card-h')
    page.wait_for_selector("#pend-cards .pend-card.open textarea", timeout=5000)
    check("a card whose asker has no tab here has no 「也告訴」 and no 跳到 (only the board)", not page.evaluate("!!document.querySelector('#pend-cards [data-tell]')")
          and "跳到" not in page.inner_text("#pend-cards .pend-card.open") and "在白板上看" in page.inner_text("#pend-cards .pend-card.open"))
    page.fill("#pend-ans", "選 A")
    page.keyboard.press("Control+Enter")
    page.wait_for_function("SFBoard.count() === 1", timeout=15000)
    check("Ctrl+Enter sends too; a failing store keeps the card (below)", [a[0] for a in args_of(page, "task_user_act")][-1] == "31")
    page.evaluate("window.__actReply = {success: false, code: 'closed', message: '#33 已經結束了。'}")
    page.click('#pend-cards .pend-card[data-id="33"] .pend-card-h')
    page.wait_for_selector("#pend-cards .pend-card.open textarea", timeout=5000)
    page.fill("#pend-ans", "好")
    page.click("#pend-cards .pend-go")
    page.wait_for_selector("#pend-cards .pend-note.bad", timeout=5000)
    check("a refused answer stays in the box with the reason, the card stays, nothing is told", "已經結束" in page.inner_text("#pend-cards .pend-form") and 33 in rail_ids(page) and len(page.evaluate("window.__sent")) == 1)
    page.close()

    # ── jump to the asker's tab, and the board on a card ────────────────────
    print(f"{tag} 跳到那個分頁 and 在白板上看")
    page = new_page(browser, errors, style="chat", width=1280, waiting=False)
    start(page, base, count=4)
    page.click("#pend-cap")
    page.wait_for_function("document.querySelectorAll('#pend-cards .pend-card').length === 4", timeout=15000)
    page.click('#pend-cards .pend-card[data-id="32"] .pend-card-h')
    page.wait_for_selector('#pend-cards [data-act="jump-card"]', timeout=5000)
    page.click('#pend-cards [data-act="jump-card"]')
    page.wait_for_timeout(500)
    check("跳到 <asker> 的分頁: that tab (s701) is the active one and the drawer is shut", page.evaluate("window.__sfHost.active()") == "s701" and not page.evaluate("document.getElementById('pend-panel').classList.contains('open')"))
    page.evaluate("window.__sfHost.switchTab('s700')")
    page.wait_for_timeout(300)
    page.click("#pend-cap")
    page.wait_for_selector('#pend-cards .pend-card[data-id="32"]', timeout=5000)
    if page.evaluate("SFPending.state().openId") != 32:          # it was left open by the jump above
        page.click('#pend-cards .pend-card[data-id="32"] .pend-card-h')
    page.click('#pend-cards [data-act="board"]')
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f && f.contentWindow.__office && f.contentWindow.__office.view() === 'board' && !!f.contentWindow.document.querySelector('#vOver .wcard'); } catch (e) { return false; } }""", timeout=15000)
    fr = H.office(page)
    check("在白板上看: the board opens (no office floor) on card #32, open", page.evaluate("SFOffice.isBoard()") and fr.evaluate("document.querySelector('#vOver .wcard .nid').textContent") == "#32", fr.evaluate("document.querySelector('#vOver').innerText")[:80])
    page.evaluate("SFOffice.openBoard({task: 33})")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.document.querySelector('#vOver .wcard .nid').textContent === '#33'; } catch (e) { return false; } }""", timeout=15000)
    check("with the board already open it moves to the other card (and stays one overlay)", page.evaluate("document.querySelectorAll('#sfo-overlay').length") == 1)
    page.keyboard.press("Escape")
    page.close()

    # ── paired computers ──────────────────────────────────────────────────
    print(f"{tag} paired computers: one line each, opens the read-only board")
    page = new_page(browser, errors, style="chat", width=1920, height=900, peers=True, waiting=False)
    start(page, base, peers=True, count=4)
    page.wait_for_function("document.querySelectorAll('#pend-peers .pend-peer').length === 2", timeout=15000)
    lines = page.evaluate("[...document.querySelectorAll('#pend-peers .pend-peer')].map(b => b.textContent.trim())")
    check("隔壁：X 有 N 件等你, one line per computer", lines == ["隔壁：示範電腦甲 有 2 件等你 →", "隔壁：示範電腦乙 有 1 件等你 →"], lines)
    check("the peers' cards are not mixed into the list, the red number is this computer's only", rail_ids(page) == ORDER and badge(page) == "4")
    page.click('#pend-peers [data-pid="pid-a"]')
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.view() === 'board' && f.contentWindow.document.getElementById('vTitle').textContent === '隔壁：示範電腦甲 的白板'; } catch (e) { return false; } }""", timeout=15000)
    fr = H.office(page)
    check("its board opens, read only (no 回覆 on its cards)", fr.evaluate("document.querySelectorAll('#vBody .nb').length") >= 2 and not fr.evaluate("!!document.querySelector('[data-act=areply]')"))
    if shoot:
        page.screenshot(path=str(H.SHOTS / "pending-peer-board.png"))
    page.keyboard.press("Escape")
    page.close()

    # ── hidden: no polling ────────────────────────────────────────────────
    print(f"{tag} the window hidden: nothing is read")
    page = new_page(browser, errors, style="chat", width=1920, height=900, peers=True, clock=True)
    start(page, base, peers=True)
    page.clock.run_for(2000)
    n0 = {n: H.calls(page, n) for n in ("tasks_overview", "link_peer_tasks", "task_detail")}
    s0 = len([a for a in args_of(page, "chat_view") if a[0] == "s702"])
    page.clock.run_for(21000)
    page.wait_for_timeout(300)
    n1 = {n: H.calls(page, n) for n in n0}
    check("visible: the count is asked once per ~20 s (with the rail, as without)", n1["tasks_overview"] == n0["tasks_overview"] + 1 and n1["link_peer_tasks"] >= n0["link_peer_tasks"], (n0, n1))
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})")
    page.evaluate("window.__status.s700 = {state: 'decision', dot: 'sig-decision', summary: 'x'}")            # a tab starts waiting while hidden
    page.clock.run_for(65000)
    page.wait_for_timeout(400)
    n2 = {n: H.calls(page, n) for n in n0}
    s2 = len([a for a in args_of(page, "chat_view") if a[0] in ("s702", "s700")])
    check("hidden (65 s): no tasks_overview, no link_peer_tasks, no task_detail", n2 == n1, (n1, n2))
    check("hidden: no question asked of any tab", s2 == s0, (s0, s2))
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
    page.clock.run_for(1500)
    page.wait_for_timeout(500)
    check("visible again: the newly waiting tab is picked up (and its question asked once)", "s700" in page.evaluate("SFPending.state().waiting") and
          len([a for a in args_of(page, "chat_view") if a[0] == "s700"]) >= 1)
    page.close()

    check(f"{tag} no page error or console error", not errors, errors[:5])


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
