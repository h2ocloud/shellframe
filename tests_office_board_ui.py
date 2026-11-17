#!/usr/bin/env python3
"""The office's two wall objects, in the real page with a stand-in Python side: the task board
(the lobby's whiteboard) and the schedule calendar next to it.

  - the whiteboard is a hit target (pointer, tooltip 任務看板); a click opens the panel's board view:
    等你決定 / 卡住了 / 進行中 (by owner), one row per task (#id, title, status in Chinese, next step,
    waiting on whom, due, how long since it moved), each task once; a row opens its history (newest
    first, at most 20, asked for only when opened); an owner in the office selects that person
  - an empty board says so in one line, and the sticky notes follow the data (one per task, at most 7,
    red for a decision or a stuck task, none on an empty board)
  - the calendar is a hit target too (tooltip 排程); its view lists the tabs that run a /loop or a cron
    (who, kind, interval, a countdown that ticks, a one-line summary) and this computer's schedules
    (title, when, on or off); schedules_list is asked only while that view is open, once at the start
    and then at most every 60 s; the badge on the calendar is the number of tabs with a loop
  - a person whose tab runs a loop has the calendar on the desk (an intern: on the table) and a line
    「排程：每 20 分鐘，下一次 HH:MM」 in the info tab; when the loop is over both are gone
  - the loop is in the snapshot with `experimental_loops` OFF (the status carries it regardless)
  - no console error from either page
Demo data only (made-up names). Headless Chromium, and WebKit when playwright has it.
Screenshots (Chromium): qa-shots/office-board-*.png, qa-shots/office-calendar-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_board_ui.py
"""
import functools
import http.server
import json
import pathlib
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
sys.path.insert(0, str(HERE))
import guide  # noqa: E402

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
    print("SKIP  tests_office_board_ui.py（沒裝 playwright）")
    sys.exit(0)

DATA = guide.load()
NOW = time.time()
ROSTER = {
    "chief": {"label": "示範秘書長", "responsibility": "統籌、分派工作", "cmd": "claude",
              "bot": {"avatar": {"shape": "star", "color": "purple"}, "primary": True, "memory": True},
              "cli": {"key": "claude", "label": "Claude Code"}, "pin": {"ok": True}},
    "eng": {"label": "示範工程師", "responsibility": "寫程式、跑測試", "cmd": "claude",
            "bot": {"avatar": {"shape": "egg", "color": "amber"}, "memory": True},
            "cli": {"key": "claude", "label": "Claude Code"}, "pin": {"ok": True, "model": "demo-model"}},
    "res": {"label": "示範研究員", "responsibility": "查資料、寫摘要", "cmd": "codex",
            "bot": {"avatar": {"shape": "drop", "color": "teal"}, "memory": True},
            "cli": {"key": "codex", "label": "Codex"}, "pin": {"ok": True}},
}
BOTS = {"enabled": True, "roster": ROSTER, "primary": "chief", "memory_root": "/tmp/demo",
        "sessions": {"s700": {"bot": "chief"}, "s701": {"bot": "eng"}},
        "kinds": {"s700": "temp", "s701": "temp", "s702": "temp", "s703": "terminal"}}
SESSIONS = [{"sid": "s700", "cmd": "claude", "alive": True, "label": "示範秘書長"},
            {"sid": "s701", "cmd": "claude", "alive": True, "label": "示範工程師"},
            {"sid": "s702", "cmd": "claude", "alive": True, "label": "示範臨時分頁"},
            {"sid": "s703", "cmd": "zsh", "alive": True, "label": "示範終端機"}]
LOOP_NEXT = NOW + 20 * 60 + 5
STATUS = {"s700": {"state": "idle", "dot": "", "summary": "idle"},
          "s701": {"state": "working", "dot": "sig-working", "action": "Running the demo tests",
                   "loop": {"kind": "loop", "status": "sleeping", "next_ts": LOOP_NEXT, "delay": 1200, "rounds": 3,
                            "reason": "每輪看一次示範建置有沒有跑完，跑完才回報；" + "很長的說明" * 12, "crons": []}},
          "s702": {"state": "decision", "dot": "sig-decision", "narration": "示範：要選方案 A 還是 B？",
                   "loop": {"kind": "cron", "status": "scheduled", "next_ts": None, "reason": "示範每半小時的檢查", "rounds": 0,
                            "crons": [{"desc": "示範每半小時的檢查", "schedule": "*/30 * * * *"}]}}}


def task(tid, title, status="doing", owner="eng", requester="chief", waiting_on="", next_step="", due=None, ago=600,
         kind="work"):
    return {"id": tid, "title": title, "kind": kind, "status": status, "owner": owner, "requester": requester,
            "waiting_on": waiting_on, "next_step": next_step, "due_at": due, "created_at": NOW - ago - 5000,
            "updated_at": NOW - ago}


T_DECISION = task(31, "示範方案 A 或 B", "waiting", "user", "res", "user", "等你選一個", NOW + 7200, 1200, "decision")
T_STUCK = task(12, "示範登入頁改版", "doing", "eng", "chief", "", "補上表單驗證", None, 90000)
T_OPEN = [T_STUCK, T_DECISION,
          task(13, "示範比較表", "todo", "res", "user", "", "列出候選", NOW - 3600, 3000),
          task(14, "示範單元測試", "doing", "eng", "chief", "res", "等研究員給資料", None, 300),
          task(15, "示範分頁的事", "waiting", "示範臨時分頁", "sfctl", "user", "等你回覆", None, 400)]
TASKS = {"success": True, "decisions": [T_DECISION], "stuck": [dict(T_STUCK, idle_s=90000)], "open": T_OPEN}
MANY = {"success": True, "decisions": [T_DECISION], "stuck": [dict(T_STUCK, idle_s=90000)],
        "open": T_OPEN + [task(20 + i, f"示範多餘的任務 {i}", "todo", "res") for i in range(6)]}
THREE = {"success": True, "decisions": [], "stuck": [], "open": [task(1, "示範一", "todo"), task(2, "示範二"), task(3, "示範三")]}
EMPTY = {"success": True, "decisions": [], "stuck": [], "open": []}
EVENTS = [{"id": 100 + i, "task_id": 12, "ts": NOW - i * 600, "actor": "eng", "kind": "note" if i % 3 else "status",
           "text": f"第 {25 - i} 筆記錄" if i % 3 else "todo → doing", "ref": ""} for i in range(25)]
SCHED = {"schedules": [
    {"id": "demo.backup", "title": "示範每日備份", "when": {"en": "Daily 02:30", "zh": "每天 02:30"}, "enabled": True,
     "running": False, "kind": "calendar", "cmd": "/bin/secret-command --token abc"},
    {"id": "demo.report", "title": "示範週報彙整", "when": {"en": "Mon-Fri 09:50", "zh": "週一至週五 09:50"}, "enabled": True,
     "running": True, "kind": "calendar"},
    {"id": "demo.old", "title": "示範舊排程", "when": {"en": "Sun 23:00", "zh": "每週日 23:00"}, "enabled": False,
     "running": False, "kind": "calendar"}], "unsupported": False, "configured": True, "launchd_ok": True}
PEERS = {"peers": {}}
CFG = {"settings": {"language": "zh-TW", "experimental_bots": True, "experimental_office": True,
                    "experimental_loops": False, "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {},
       "guide": {"tour": "done", "seen": "0.48.0"}}
GDATA = {"success": True, "features": guide.for_surface("desktop", CFG["settings"], DATA), "tours": DATA.get("tours"),
         "state": {"tour": "done", "seen": "0.48.0", "version": guide.app_version()}}


def stub():
    return """
if (window.top === window) {
window.__calls = []; window.__args = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s; window.__tasks = %s;
window.__peers = %s; window.__events = %s; window.__sched = %s; window.__guide = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push(prop); window.__args.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's701'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'link_peer_bots': return R(window.__peers);
      case 'tasks_overview': return R(window.__tasks);
      case 'task_detail': return R({success: true, task: {id: +args[0], title: 'x', events: window.__events}});
      case 'schedules_list': return R(window.__sched);
      case 'office_prefs': return R({success: true, prefs: {mode: 'iso'}});
      case 'office_prefs_save': return R({success: true});
      case 'office_helpers': return R({success: true, helpers: {}});
      case 'guide_data': return R(window.__guide);
      case 'guide_mark': return R({success: true, state: window.__guide.state});
      case 'get_changelog': return R('');
      case 'preset_groups': return R([]);
      default: return R('{}');
    }
  };
}})};
setInterval(() => { try { window.__sfAgentStatus && window.__sfAgentStatus(window.__status); } catch (e) {} }, 500);
}
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, BOTS, STATUS, TASKS, PEERS, EVENTS, SCHED, GDATA))


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def office(page):
    return next((f for f in page.frames if f.url.endswith("/office.html")), None)


def calls(page, name):
    return page.evaluate("(n) => window.__calls.filter(c => c === n).length", name)


def open_office(page, tasks=None):
    """(Re)open the office with `tasks` as the store's answer; the lobby in view."""
    if page.evaluate("SFOffice.isOpen()"):
        page.evaluate("SFOffice.close()")
    if tasks is not None:
        page.evaluate("(t) => { window.__tasks = t; }", tasks)
    page.evaluate("SFOffice.open()")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && Object.keys(f.contentWindow.__office.assignments()).length >= 2
               && f.contentWindow.__office.board() !== null; } catch (e) { return false; } }""", timeout=15000)
    fr = office(page)
    fr.evaluate("__office.setMode('iso', true); __office.setArea('lobby', true)")
    page.wait_for_timeout(700)
    return fr


def wall_click(page, fr, which):
    pt = fr.evaluate("(k) => __office.wallPoint(k)", which)
    page.mouse.move(pt["x"], pt["y"])
    page.wait_for_timeout(250)
    tip = fr.evaluate("__office.hoverTip()")
    cursor = fr.evaluate("document.getElementById('stage').classList.contains('pt')")
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(600)
    return pt, tip, cursor


def run(browser, base, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.add_init_script(stub())
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    page.wait_for_function("""['s701','s702'].every(sid => { const s = window.__sfHost.sessions()[sid];
        return s && s._serverStatus && s._serverStatus.loop; })""", timeout=20000)

    print(f"[{engine}] the loop reaches the office with the Loops switch off")
    check("settings.experimental_loops is off", page.evaluate("window.__cfg.settings.experimental_loops") is False)
    fr = open_office(page)
    snap = page.evaluate("SFOffice.lastSnapshot()")
    lp = {x["key"]: x for x in snap["loops"]}
    check("the snapshot lists the two tabs that run a loop or a cron",
          sorted(lp) == ["b:eng", "t:s702"] and lp["b:eng"]["kind"] == "loop" and lp["t:s702"]["kind"] == "cron", lp)
    check("…the /loop's interval, rounds and summary come through; its next wake is in seconds",
          lp["b:eng"]["delay"] == 1200 and lp["b:eng"]["rounds"] == 3 and lp["b:eng"]["next_ts"] > NOW
          and lp["b:eng"]["reason"].startswith("每輪看一次"), lp["b:eng"])
    check("schedules_list has not been asked for (the view is not open)", calls(page, "schedules_list") == 0)

    print(f"[{engine}] the whiteboard")
    check("tasks_overview's decisions are kept (the board needs them)",
          [x["id"] for x in snap["board"]["decisions"]] == [31] and [x["id"] for x in snap["board"]["stuck"]] == [12], snap["board"])
    check("each task is in one list only (#12 is stuck, #31 a decision: neither is also 'open')",
          [x["id"] for x in snap["board"]["open"]] == [13, 14, 15], [x["id"] for x in snap["board"]["open"]])
    notes = fr.evaluate("__office.notes()")
    check("five tasks → five sticky notes, the decision and the stuck one red (first)",
          len(notes) == 5 and notes[:2] == ["#f7768e", "#f7768e"] and "#f7768e" not in notes[2:], notes)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-board-wall.png"))
    pt, tip, cursor = wall_click(page, fr, "board")
    check("the whiteboard is a hit target: pointer cursor and the tooltip 任務看板", tip == "任務看板" and cursor, (tip, cursor))
    check("clicking it opens the panel's board view", fr.evaluate("__office.view()") == "board"
          and fr.evaluate("document.getElementById('panel').classList.contains('open') && document.getElementById('panel').classList.contains('viewing')"))
    check("…titled 任務看板, and nobody is followed", fr.evaluate("document.getElementById('vTitle').textContent") == "任務看板"
          and fr.evaluate("__office.following()") is None)
    heads = fr.evaluate("[...document.querySelectorAll('#vBody .vsec > h3')].map(h => h.firstChild.textContent)")
    check("three sections: 等你決定, 卡住了, 進行中", heads == ["等你決定", "卡住了", "進行中"], heads)
    rows = fr.evaluate("""() => [...document.querySelectorAll('#vBody .trow')].map(r => ({id: r.dataset.id, sec: r.closest('.vsec').getAttribute('aria-label'),
        text: r.innerText}))""")
    ids = [(r["id"], r["sec"]) for r in rows]
    check("one row per task, each once, in its section",
          ids == [("31", "等你決定"), ("12", "卡住了"), ("13", "進行中"), ("14", "進行中"), ("15", "進行中")], ids)
    by = {r["id"]: r["text"] for r in rows}
    check("a row: #id, title, status in Chinese, next step, waiting on whom, due, how long it did not move",
          "#12" in by["12"] and "示範登入頁改版" in by["12"] and "進行中" in by["12"] and "補上表單驗證" in by["12"] and "1 天前沒動" in by["12"]
          and "待辦" in by["13"] and "已逾期" in by["13"] and "等待中" in by["31"] and "等你選一個" in by["31"], by)
    check("…waiting on 'you' reads 你; waiting on a bot shows that bot", "你" in by["31"] and "示範研究員" in by["14"], (by["31"], by["14"]))
    groups = fr.evaluate("[...document.querySelectorAll('#vBody .vown .who')].map(b => b.textContent.trim())")
    check("進行中 is grouped by owner, as the office names them (an intern's tab by its title)",
          groups == ["示範研究員", "示範工程師", "示範臨時分頁"], groups)

    print(f"[{engine}] a row's history")
    check("history is asked for only when a row is opened", calls(page, "task_detail") == 0)
    fr.click('#vBody .trow[data-id="12"] .th')
    page.wait_for_function("window.__calls.filter(c => c === 'task_detail').length >= 1")
    page.wait_for_timeout(500)
    asked = page.evaluate("window.__args.filter(a => a[0] === 'task_detail').map(a => String(a[1][0]))")
    hist = fr.evaluate("[...document.querySelectorAll('#vBody .trow[data-id=\"12\"] .thist li')].map(l => l.innerText.replace(/\\s+/g, ' '))")
    check("…then asked once, for that task", asked == ["12"], asked)
    check("…at most 20 events (the store sent 25), newest first", len(hist) == 20 and "第 25" not in hist[0] and "狀態" in hist[0], hist[:2])
    check("…a status event reads in Chinese (待辦 → 進行中)", "待辦 → 進行中" in hist[0], hist[0])
    check("…the row says it is open", fr.evaluate("document.querySelector('#vBody .trow[data-id=\"12\"] .th').getAttribute('aria-expanded')") == "true")
    page.wait_for_timeout(1500)
    check("…and is not asked again while it does not change", calls(page, "task_detail") == 1)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-board-open.png"))
    fr.evaluate("window.__tagRow = document.querySelector('#vBody .trow[data-id=\"13\"]'); window.__tagRow.__mine = 1")
    for _ in range(2):
        page.evaluate("SFOffice.refresh()")                   # a snapshot every time: the view must not be rebuilt for it
        page.wait_for_timeout(500)
    check("the view is not rebuilt when a snapshot arrives (the same row node is still there: a click or a selection survives)",
          fr.evaluate("document.body.contains(window.__tagRow) && window.__tagRow.__mine === 1"))
    fr.click('#vBody .trow[data-id="12"] .th')
    check("a second click closes it", fr.evaluate("document.querySelectorAll('#vBody .trow[data-id=\"12\"] .thist').length") == 0)

    print(f"[{engine}] an owner selects the person")
    fr.click('#vBody .vown .who[data-key="b:eng"]')
    page.wait_for_timeout(500)
    check("clicking the owner closes the view and follows that person",
          fr.evaluate("__office.view()") is None and fr.evaluate("__office.following()") == "b:eng"
          and fr.evaluate("!document.getElementById('panel').classList.contains('viewing')"))
    fr.evaluate("__office.follow('b:eng')")
    page.wait_for_timeout(400)
    fr.evaluate("__office.openView('board')")                 # the camera is on the person: the wall may be out of view
    page.wait_for_timeout(300)
    check("the board opens over a followed person (the follow is dropped)",
          fr.evaluate("__office.view()") == "board" and fr.evaluate("__office.following()") is None)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    check("Esc closes the view first, not the office", fr.evaluate("__office.view()") is None and page.evaluate("SFOffice.isOpen()"))
    fr.evaluate("__office.follow('b:eng')")
    fr.evaluate("__office.openView('cal')")
    check("opening a view while following drops the follow", fr.evaluate("__office.following()") is None and fr.evaluate("__office.view()") == "cal")
    fr.evaluate("__office.closeView()")

    print(f"[{engine}] sticky notes follow the data, and an empty board says so")
    fr = open_office(page, MANY)
    notes = fr.evaluate("__office.notes()")
    check("more than seven tasks → seven notes", len(notes) == 7 and notes.count("#f7768e") == 2, notes)
    fr = open_office(page, THREE)
    notes = fr.evaluate("__office.notes()")
    check("three tasks → three notes, none red", len(notes) == 3 and "#f7768e" not in notes, notes)
    fr = open_office(page, EMPTY)
    check("no tasks → a bare board (no notes)", fr.evaluate("__office.notes()") == [])
    wall_click(page, fr, "board")
    one = fr.evaluate("""() => ({n: document.querySelectorAll('#vBody .vsec').length, rows: document.querySelectorAll('#vBody .trow').length,
        text: document.getElementById('vBody').innerText.trim(), sub: document.getElementById('vSub').textContent})""")
    check("an empty board is one line telling how a task gets there",
          one["n"] == 0 and one["rows"] == 0 and one["text"] == "看板上還沒有任務：bot 用 sfctl task add 記下手上的事後會出現在這裡", one)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-board-empty.png"))
    fr.evaluate("__office.closeView()")

    print(f"[{engine}] the calendar")
    fr = open_office(page, TASKS)
    base_calls = calls(page, "schedules_list")                # the cal view opened above (to test the follow) asked once
    pt, tip, cursor = wall_click(page, fr, "cal")
    check("the calendar is a hit target: pointer cursor and the tooltip 排程", tip == "排程" and cursor, (tip, cursor))
    check("…a click opens the schedule view", fr.evaluate("__office.view()") == "cal" and fr.evaluate("document.getElementById('vTitle').textContent") == "排程")
    check("…its badge is the number of tabs with a loop (2)", fr.evaluate("__office.loops().length") == 2)
    heads = fr.evaluate("[...document.querySelectorAll('#vBody .vsec > h3')].map(h => h.firstChild.textContent)")
    check("two sections: 對話裡的排程, 這台電腦的排程", heads == ["對話裡的排程", "這台電腦的排程"], heads)
    page.wait_for_timeout(400)
    lrows = fr.evaluate("""() => [...document.querySelectorAll('#vBody .lrow')].map(r => ({key: r.dataset.key, text: r.innerText.replace(/\\s+/g, ' ')}))""")
    by = {r["key"]: r["text"] for r in lrows}
    check("a row per tab with a loop: its name, the kind, the interval", len(lrows) == 2 and "示範工程師" in by["b:eng"] and "/loop" in by["b:eng"]
          and "每 20 分鐘" in by["b:eng"] and "示範臨時分頁" in by["t:s702"] and "cron" in by["t:s702"] and "每 30 分鐘" in by["t:s702"], by)
    check("…the next wake as a clock time and a countdown", "下一次" in by["b:eng"] and "分" in by["b:eng"] and "秒後" in by["b:eng"], by["b:eng"])
    check("…a one-line summary of what it does, cut short", "每輪看一次示範建置" in by["b:eng"] and by["b:eng"].count("很長的說明") < 12
          and "…" in by["b:eng"], by["b:eng"])
    c1 = fr.evaluate("document.querySelector('#vBody .lrow[data-key=\"b:eng\"] .cd').textContent")
    page.wait_for_timeout(2300)
    c2 = fr.evaluate("document.querySelector('#vBody .lrow[data-key=\"b:eng\"] .cd').textContent")
    check("the countdown ticks", c1 != c2 and "秒後" in c2, (c1, c2))
    srows = fr.evaluate("[...document.querySelectorAll('#vBody .srow')].map(r => r.innerText.replace(/\\s+/g, ' '))")
    check("this computer's schedules: title, when, on or off (the command is not shown)",
          len(srows) == 3 and "示範每日備份" in srows[0] and "每天 02:30" in srows[0] and "啟用中" in srows[0] and "已停用" in srows[2]
          and "執行中" in srows[1] and not any("secret" in r for r in srows), srows)
    check("schedules_list was asked once, when the view opened", calls(page, "schedules_list") - base_calls == 1, calls(page, "schedules_list") - base_calls)
    check("…its default pace is once a minute", fr.evaluate("__office.schedPollMs()") == 60000)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-calendar-view.png"))
    fr.evaluate("window.__tagLoop = document.querySelector('#vBody .lrow'); window.__tagLoop.__mine = 1")
    fr.evaluate("__office.setSchedPoll(1200)")
    page.wait_for_timeout(2900)
    n_open = calls(page, "schedules_list") - base_calls
    check("while the view is open it is asked again, no faster than the pace", 2 <= n_open <= 4, n_open)
    check("…and the list is not rebuilt by the snapshots or the answers (same row node)",
          fr.evaluate("document.body.contains(window.__tagLoop) && window.__tagLoop.__mine === 1"))
    fr.evaluate("__office.closeView()")
    page.wait_for_timeout(300)
    n_closed = calls(page, "schedules_list")
    page.wait_for_timeout(2500)
    check("with the view closed it is not asked at all", calls(page, "schedules_list") == n_closed, (n_closed, calls(page, "schedules_list")))
    fr.evaluate("__office.openView('board')")
    page.wait_for_timeout(1500)
    check("…nor while the board is the view", calls(page, "schedules_list") == n_closed)
    fr.evaluate("__office.closeView()")
    fr.evaluate("__office.openView('cal')")
    page.wait_for_timeout(500)
    fr.click('#vBody .lrow[data-key="b:eng"] .who')
    page.wait_for_timeout(500)
    check("clicking a name in the list selects that person", fr.evaluate("__office.following()") == "b:eng" and fr.evaluate("__office.view()") is None)
    fr.evaluate("__office.unfollow()")

    print(f"[{engine}] the person's desk and info tab")
    marks = {m["key"]: m["wall"] for m in fr.evaluate("__office.loopMarks()")}
    check("a person whose tab runs a loop has the calendar on the desk, an intern one on the table",
          marks == {"b:eng": True, "t:s702": True}, marks)
    fr.evaluate("__office.setArea('lobby', true)")
    page.wait_for_timeout(500)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-calendar-wall.png"))
    fr.evaluate("__office.follow('b:eng')")
    page.wait_for_timeout(500)
    fr.click("#tabInfo")
    page.wait_for_timeout(500)
    line = fr.evaluate("document.getElementById('pLoop').textContent")
    hhmm = page.evaluate("(t) => { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }",
                         lp["b:eng"]["next_ts"])
    check("the info tab has the line 「排程：每 20 分鐘，下一次 HH:MM」", line == f"排程：每 20 分鐘，下一次 {hhmm}", (line, hhmm))
    fr.evaluate("__office.follow('t:s702')")
    page.wait_for_timeout(500)
    fr.click("#tabInfo")
    check("…a cron reads 「排程：cron 每 30 分鐘」", fr.evaluate("document.getElementById('pLoop').textContent") == "排程：cron 每 30 分鐘",
          fr.evaluate("document.getElementById('pLoop').textContent"))
    if shoot:
        page.screenshot(path=str(SHOTS / "office-calendar-info.png"))
    fr.evaluate("__office.unfollow()")
    page.evaluate("window.__status.s701.loop = null; window.__status.s702.loop = null;")
    page.wait_for_timeout(1000)
    page.evaluate("SFOffice.refresh()")
    page.wait_for_timeout(800)
    check("when the loops are over the marks and the badge are gone",
          fr.evaluate("__office.loopMarks()") == [] and fr.evaluate("__office.loops()") == [])
    print(f"[{engine}] a narrow window")
    page.set_viewport_size({"width": 600, "height": 800})
    page.wait_for_timeout(500)
    fr.evaluate("__office.openView('board')")
    page.wait_for_timeout(700)
    nar = fr.evaluate("""() => { const p = document.getElementById('panel').getBoundingClientRect(), b = document.getElementById('vBody');
        return {pw: p.width, w: innerWidth, over: b.scrollWidth > b.clientWidth + 1}; }""")
    check("the view takes the window's width as a sheet, nothing spills sideways", nar["pw"] == nar["w"] and not nar["over"], nar)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-board-narrow.png"))
    fr.evaluate("__office.closeView()")
    check("no console error from the page or the office", not errors, errors[:5])
    page.close()


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
