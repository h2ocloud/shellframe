#!/usr/bin/env python3
"""The office's schedule calendar (the lobby wall's calendar, view 'cal'): day / week / month, who owns
what, and the two things a person may change.

Part 1, office.html on its own (demo host), the clock pinned to Wed 2026-11-18 10:07 and the data fed in
through the test hooks, so every date below is the same on any day:
  - a 日／週／月 switch (default 週, Monday first), ‹ 今天 › and the range label; the choice is remembered
  - day: hour rows 00–23 and a now-line; week: 7 columns; month: 6x7 cells, at most 3 chips and "+N"
  - only what is still to come is placed: past days and past hours stay empty
  - colour by source (/loop, cron, this computer's schedules, disabled); /loop with a pace is marked 預估
  - high frequency is a band, not dozens of events: a 20-minute /loop is one chip a day in the month,
    a StartInterval job is a band whatever its length
  - the cron parser (numbers, *, */n, a-b, a-b/n, lists, day-of-week 0 and 7, OR when both day fields
    are restricted); what it cannot read is listed under 「沒辦法放進日曆」, not dropped
  - the detail card: owner (clickable) or 未指定; exactly two controls (開關, 頻率), the rest read only;
    Esc closes the card first, then the sheet
  - a launchd switch goes to setEnabled with the label and shows what came back (never flips by itself);
    toggle:false is disabled with the reason; /loop and cron changes, and a launchd frequency, are a
    message to the owner (stubbed here), shown as 已請 … 調整 until the detected state changes, 10 minutes
    later 還沒改; nobody to ask → the request controls are disabled with a reason
  - text is 13px or more; narrow windows have no sideways scroll
Part 2, inside index.html with a stand-in Python side, so the real office.js carries it:
  - the owner's message reaches chat_send for that bot's tab, never clearing the draft; the launchd
    switch calls schedule_set_enabled with the label; the snapshots (every 3 s) do not rebuild the
    calendar; schedules_list is asked at most once a minute while it is open and not at all otherwise
Demo data only. Headless Chromium, and WebKit when playwright has it.
Screenshots (Chromium): qa-shots/office-calendar-{week,month,day,detail,narrow,week-ui118}.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_calendar_ui.py
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
    print("SKIP  tests_office_calendar_ui.py（沒裝 playwright）")
    sys.exit(0)

# ── part 1: the data, as the page would hold it ───────────────────────────
PIN = "new Date(2026, 10, 18, 10, 7).getTime()"          # Wed 2026-11-18 10:07, the browser's own zone
NOWS = "(new Date(2026, 10, 18, 10, 7).getTime() / 1000)"


def loop(key, name, delay, mins, reason, **kw):
    d = {"key": key, "name": name, "kind": "loop", "status": "sleeping", "next_ts": "@@" + str(mins), "delay": delay,
         "reason": reason, "rounds": 1, "crons": []}
    d.update(kw)
    return d


def cron(key, name, sched, reason):
    return {"key": key, "name": name, "kind": "cron", "status": "scheduled", "next_ts": 0, "delay": 0, "reason": reason,
            "rounds": 0, "crons": [{"desc": reason, "schedule": sched}]}


LOOPS = [loop("b:eng", "示範工程師", 1200, 13, "每 20 分鐘看一次示範建置"),
         loop("b:res", "示範研究員", 0, 47, "等示範資料到了再看，我自己決定"),
         loop("", "示範背景分頁", 7200, 30, "每 2 小時檢查一次示範備份"),
         cron("b:ops", "示範維運", "0 * * * *", "每小時整理一次示範清單"),
         cron("b:sec", "示範秘書長", "0 9 * * *", "每天早上整理示範週報"),
         cron("b:doc", "示範文書", "0 9 L * *", "每月最後一天整理示範月報")]


def row(id, title, zh, **kw):
    r = {"id": id, "title": title, "when": {"en": zh, "zh": zh}, "enabled": True, "running": False, "kind": "calendar", "count": 0,
         "owner": "", "toggle": True, "state": "", "slots": {"interval": None, "calendar": []}}
    r.update(kw)
    return r


ROWS = [row("demo.poll", "示範服務輪詢", "每 10 分鐘", kind="interval", owner="eng", slots={"interval": 600, "calendar": []}),
        row("demo.report", "示範週報彙整", "週一～五 09:30", kind="weekly",
            slots={"interval": None, "calendar": [{"Weekday": d, "Hour": 9, "Minute": 30} for d in (1, 2, 3, 4, 5)]}),
        row("demo.backup", "示範每日備份", "每天 02:30", kind="daily", owner="ops", slots={"interval": None, "calendar": [{"Hour": 2, "Minute": 30}]}),
        row("demo.old", "示範舊排程", "週日 23:00", enabled=False, owner="ops", slots={"interval": None, "calendar": [{"Weekday": 0, "Hour": 23, "Minute": 0}]}),
        row("demo.once", "示範單次提醒", "單次・已排 11/20 09:00", kind="once", toggle=False, state="armed", owner="sch",
            slots={"interval": None, "calendar": [{"Month": 11, "Day": 20, "Hour": 9, "Minute": 0}]}),
        row("demo.bot", "示範常駐程式", "由 launchd 保持常駐", kind="daemon", slots={"interval": None, "calendar": []})]


def feed_js(loops=None, rows=None):
    """A page-side call of __office.calFeed with next_ts in seconds from the pinned clock."""
    ls = json.dumps(LOOPS if loops is None else loops, ensure_ascii=False)
    rs = json.dumps(ROWS if rows is None else rows, ensure_ascii=False)
    return (f"(() => {{ const base = {NOWS}; const ls = {ls}.map(l => typeof l.next_ts === 'string' ? Object.assign(l, {{ next_ts: base + Number(l.next_ts.slice(2)) * 60 }}) : l);"
            f" __office.calFeed({{ loops: ls, rows: {rs} }}); }})()")


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def model(page):
    return page.evaluate("__office.calModel()")


def items(m, id_, day=None):
    out = []
    for d in m["days"]:
        if day and d["day"] != day:
            continue
        out += [dict(i, day=d["day"]) for i in d["items"] if i["id"] == id_]
    return out


def open_cal(page, now=True, mode=None):
    page.evaluate("__office.pause(true); __office.openView('cal')")
    page.wait_for_function("__office.view() === 'cal'")
    page.wait_for_timeout(300)
    if now:
        page.evaluate(f"__office.calSetNow({PIN})")
    page.evaluate(feed_js())
    if mode:
        page.evaluate(f"__office.calGo('{mode}')")
    page.wait_for_timeout(250)


def click_ev(page, id_):
    """Click an entry's chip; one hidden behind a cell's "+N" is reached through its first day (day view shows all)."""
    sel = f'#vBody [data-act="ev"][data-id="{id_}"]'
    if not page.evaluate("(s) => !!document.querySelector(s)", sel):
        day = next(i["day"] for i in items(model(page), id_)) if items(model(page), id_) else None
        page.evaluate("(d) => __office.calGo('day', new Date(d + 'T12:00:00').getTime())", day)
        page.wait_for_timeout(100)
    page.click(sel)
    page.wait_for_timeout(150)


def run_part1(browser, base, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.goto(base + "/office.html")
    page.wait_for_timeout(1000)
    open_cal(page)

    print(f"[{engine}] switch and navigation")
    st = page.evaluate("__office.calState()")
    check("default is 週, anchored today", st["mode"] == "week" and st["anchor"] == "2026-11-18", st)
    seg = page.evaluate("[...document.querySelectorAll('#vHeadX .cseg button')].map(b => b.textContent + ':' + b.getAttribute('aria-pressed'))")
    check("the head has 日 週 月, 週 pressed", seg == ["日:false", "週:true", "月:false"], seg)
    check("the range label is the week, Monday first", page.inner_text("#calRange") == "2026 年 11/16 – 11/22", page.inner_text("#calRange"))
    heads = page.evaluate("[...document.querySelectorAll('#vBody .cg.w .ch')].map(h => h.textContent.replace(/\\s+/g, ' ').trim())")
    check("week: seven day columns, Monday to Sunday", heads == ["週一 16", "週二 17", "週三 18", "週四 19", "週五 20", "週六 21", "週日 22"], heads)
    check("week: hour rows 00–23", page.evaluate("document.querySelectorAll('#vBody .chr').length") == 24
          and page.inner_text('#vBody .chr[data-h="0"]') == "00:00" and page.inner_text('#vBody .chr[data-h="23"]') == "23:00")
    page.click('#vHeadX [data-act="cnav"][data-d="1"]')
    check("› goes to next week", page.inner_text("#calRange") == "2026 年 11/23 – 11/29" and page.evaluate("__office.calState().anchor") == "2026-11-25")
    check("…the now-line is hidden when this week is not shown", page.evaluate("document.getElementById('calNL').hidden"))
    page.click('#vHeadX [data-act="ctoday"]')
    check("今天 comes back", page.inner_text("#calRange") == "2026 年 11/16 – 11/22")
    page.click('#vHeadX [data-act="cnav"][data-d="-1"]')
    check("‹ goes to last week", page.inner_text("#calRange") == "2026 年 11/9 – 11/15")
    m = model(page)
    check("last week is empty: there is no run history", m["days"] == [], m["range"])
    page.click('#vHeadX [data-act="ctoday"]')

    page.click('#vHeadX [data-m="day"]')
    check("日: one column, the day in the label", page.inner_text("#calRange") == "11 月 18 日（週三）" and page.evaluate("document.querySelectorAll('#vBody .cg.d .ch').length") == 1)
    page.click('#vHeadX [data-act="cnav"][data-d="1"]')
    check("日 › is tomorrow", page.inner_text("#calRange") == "11 月 19 日（週四）")
    page.click('#vHeadX [data-act="ctoday"]')
    page.click('#vHeadX [data-m="month"]')
    check("月: 6x7 cells, 2026 年 11 月", page.evaluate("document.querySelectorAll('#vBody .cg.m .cm').length") == 42 and page.inner_text("#calRange") == "2026 年 11 月")
    first = page.evaluate("document.querySelector('#vBody .cg.m .cm').dataset.d")
    check("…starting on a Monday (2026-10-26)", page.evaluate("(d) => { const x = new Date(+d); return x.getDay() === 1 && x.getMonth() === 9 && x.getDate() === 26; }", first))
    page.click('#vHeadX [data-act="cnav"][data-d="1"]')
    check("month › is December", page.inner_text("#calRange") == "2026 年 12 月")
    page.click('#vHeadX [data-act="ctoday"]')
    check("the last choice is remembered (月)", page.evaluate("JSON.parse(localStorage.getItem('sbOffice.v2.calMode'))") == "month")
    page.click('#vHeadX [data-m="week"]')

    print(f"[{engine}] what is placed")
    m = model(page)
    days = {d["day"]: d["items"] for d in m["days"]}
    check("nothing before today, and today has only what is still ahead", all(d >= "2026-11-18" for d in days)
          and all(i["band"] or i["time"] > "10:07" for i in days["2026-11-18"]), sorted(days))
    check("the hourly cron today: 11:00 to 23:00 only", [i["time"] for i in days["2026-11-18"] if i["id"] == "c:b:ops:0"] == [f"{h:02d}:00" for h in range(11, 24)])
    check("the daily 09:00 cron is not today (past), but Thursday", not items(m, "c:b:sec:0", "2026-11-18") and len(items(m, "c:b:sec:0", "2026-11-19")) == 1
          and items(m, "c:b:sec:0", "2026-11-19")[0]["time"] == "09:00")
    check("the weekday 09:30 launchd job: Thu and Fri this week, not Sat or Sun", [i["day"] for i in items(m, "s:demo.report")] == ["2026-11-19", "2026-11-20"])
    check("a daily 02:30 launchd job: from tomorrow", [i["day"] for i in items(m, "s:demo.backup")][:1] == ["2026-11-19"] and not items(m, "s:demo.backup", "2026-11-18"))
    once = items(m, "s:demo.once")
    check("a one-off is one date", len(once) == 1 and once[0]["day"] == "2026-11-20" and once[0]["time"] == "09:00", once)
    check("the 20-minute /loop is a band a day (today from its next run, 10:20), est", [(i["day"], i["m0"]) for i in items(m, "l:b:eng")][:2] == [("2026-11-18", 620), ("2026-11-19", 0)]
          and all(i["band"] == "每 20 分鐘" and i["est"] for i in items(m, "l:b:eng")))
    check("a StartInterval job is a band only", items(m, "s:demo.poll") and all(i["band"] == "每 10 分鐘" for i in items(m, "s:demo.poll")))
    check("a 2-hour /loop is individual and 預估: 10:37, 12:37 …", [i["time"] for i in items(m, "l:n:示範背景分頁", "2026-11-18")][:3] == ["10:37", "12:37", "14:37"]
          and all(i["est"] for i in items(m, "l:n:示範背景分頁")))
    check("a self-paced /loop is its one next run, not 預估", [(i["day"], i["time"], i["est"]) for i in items(m, "l:b:res")] == [("2026-11-18", "10:54", False)])
    check("a disabled job is shown, grey: Sunday 23:00", [(i["day"], i["time"], i["cls"]) for i in items(m, "s:demo.old")] == [("2026-11-22", "23:00", "off")])
    check("not placed: the unreadable cron and the keep-alive job are listed, with why",
          {u["id"] for u in m["un"]} == {"c:b:doc:0", "s:demo.bot"} and "看不懂" in next(u["why"] for u in m["un"] if u["id"] == "c:b:doc:0"), m["un"])
    check("…and shown under 沒辦法放進日曆", page.evaluate("[...document.querySelectorAll('#vBody .cun .ce')].map(b => b.dataset.id).sort().join()") == "c:b:doc:0,s:demo.bot"
          and "沒辦法放進日曆" in page.inner_text("#vBody .cun h3"))
    chip = page.evaluate("""() => { const c = document.querySelector('#vBody .cg.w .ce.est:not(.band)'), s = document.querySelector('#vBody .cg.w .ce.c-sched'), o = document.querySelector('#vBody .cg.w .ce.off');
        return {est: !!c, bg: c && getComputedStyle(c).borderTopStyle, sched: !!s, off: !!o}; }""")
    check("chips: dashed for 預估, green for the computer's schedules, grey for disabled", chip["est"] and chip["bg"] == "dashed" and chip["sched"] and chip["off"], chip)
    cls = page.evaluate("[...new Set([...document.querySelectorAll('#vBody .cg.w .ce')].map(c => [...c.classList].find(x => /^c-|^off$/.test(x))))].sort().join()")
    check("…and /loop blue, cron purple are there too", cls == "c-cron,c-loop,c-sched,off", cls)
    page.wait_for_timeout(200)
    nl = page.evaluate("""() => { const nl = document.getElementById('calNL'), r10 = document.querySelector('#vBody .chr[data-h="10"]'), r11 = document.querySelector('#vBody .chr[data-h="11"]');
        return {hidden: nl.hidden, top: nl.offsetTop, a: r10.offsetTop, b: r11.offsetTop}; }""")
    check("the now-line sits in the 10:00 row (10:07)", not nl["hidden"] and nl["a"] <= nl["top"] < nl["b"], nl)
    past = page.evaluate("[...document.querySelectorAll('#vBody .cc.past')].every(c => +c.dataset.d < Date.now() * 2) && document.querySelectorAll('#vBody .cc.today.past').length")
    check("today's hours before now are dimmed (rows 00–09)", past == 10, past)
    if shoot:
        page.evaluate("document.getElementById('vBody').scrollTop = 0")
        page.screenshot(path=str(SHOTS / "office-calendar-week.png"))

    print(f"[{engine}] month: one chip per entry per day")
    page.click('#vHeadX [data-m="month"]')
    page.wait_for_timeout(150)
    cell = page.evaluate("""() => { const c = [...document.querySelectorAll('#vBody .cg.m .cm')].find(x => new Date(+x.dataset.d).getDate() === 25 && new Date(+x.dataset.d).getMonth() === 10);
        return {twenty: c.querySelectorAll('[data-id="l:b:eng"]').length, chips: c.querySelectorAll('.ce').length, more: (c.querySelector('.cmore') || {}).textContent || '',
                hourly: [...c.querySelectorAll('[data-id="c:b:ops:0"]')].map(x => x.textContent)}; }""")
    check("the 20-minute /loop is one chip in a cell, not dozens", cell["twenty"] == 1, cell)
    check("…a cell shows at most 3 chips and +N", cell["chips"] <= 3 and cell["more"].startswith("+"), cell)
    m = model(page)
    check("…every day of the month from today has it as a band, none before", len(items(m, "l:b:eng")) == 19 and items(m, "l:b:eng")[0]["day"] == "2026-11-18" and items(m, "l:b:eng")[-1]["day"] == "2026-12-06", len(items(m, "l:b:eng")))
    past = page.evaluate("[...document.querySelectorAll('#vBody .cg.m .cm.past')].every(c => !c.querySelector('.ce'))")
    check("past days are empty", past)
    # fold: an hourly cron (24 a day) is one chip with a count, not a cell full of it
    page.evaluate(feed_js(loops=[LOOPS[3]], rows=[]))
    page.wait_for_timeout(150)
    hourly = page.evaluate("""() => { const c = [...document.querySelectorAll('#vBody .cg.m .cm')].find(x => new Date(+x.dataset.d).getDate() === 25 && new Date(+x.dataset.d).getMonth() === 10);
        return [...c.querySelectorAll('.ce')].map(x => x.textContent); }""")
    check("an hourly cron is one chip with ×N a day in the month", len(hourly) == 1 and "×24" in hourly[0], hourly)
    page.evaluate(feed_js())
    page.evaluate("__office.calGo('week', new Date(2026, 10, 18).getTime())")

    print(f"[{engine}] the cron parser")

    def nxt(expr, n=3, frm=None):
        return page.evaluate(f"__office.calCron({json.dumps(expr)}, {frm or PIN}, {n})")
    check("1-5: weekdays only", nxt("0 9 * * 1-5") == ["2026-11-19 09:00", "2026-11-20 09:00", "2026-11-23 09:00"], nxt("0 9 * * 1-5"))
    check("day of week 0 and 7 are both Sunday", nxt("30 8 * * 0", 2) == nxt("30 8 * * 7", 2) == ["2026-11-22 08:30", "2026-11-29 08:30"])
    check("5-7 is Fri, Sat, Sun", nxt("0 0 * * 5-7") == ["2026-11-20 00:00", "2026-11-21 00:00", "2026-11-22 00:00"])
    check("*/15 in one hour", nxt("*/15 9 * * *", 4) == ["2026-11-19 09:00", "2026-11-19 09:15", "2026-11-19 09:30", "2026-11-19 09:45"])
    check("a-b/n", nxt("10-20/5 9 * * *") == ["2026-11-19 09:10", "2026-11-19 09:15", "2026-11-19 09:20"])
    check("a list of days of the month", nxt("0 9 1,15 * *", 3) == ["2026-12-01 09:00", "2026-12-15 09:00", "2027-01-01 09:00"])
    check("a list of hours and minutes", nxt("5,35 8,20 * * 4", 3) == ["2026-11-19 08:05", "2026-11-19 08:35", "2026-11-19 20:05"])
    check("OR when both day of month and day of week are restricted", nxt("0 9 20 * 1") == ["2026-11-20 09:00", "2026-11-23 09:00", "2026-11-30 09:00"], nxt("0 9 20 * 1"))
    check("…AND when one of them is a star (*/2 days AND Monday)", nxt("0 9 */2 * 1", 2) == ["2026-11-23 09:00", "2026-12-07 09:00"], nxt("0 9 */2 * 1", 2))
    check("a month field", nxt("0 0 1 1 *", 1) == ["2027-01-01 00:00"])
    check("Feb 30 never happens, so it finds nothing instead of looping", nxt("0 0 30 2 *", 1) == [])
    bad = [e for e in ("0 9 L * *", "@daily", "0 9 * * MON", "61 * * * *", "* * * *", "5-1 * * * *", "*/0 * * * *", "0 9 ? * *", "0 24 * * *", "0 9 * * 8") if nxt(e, 1) is not None]
    check("what it cannot read is null (names, ?, L, @daily, out of range, bad ranges)", bad == [], bad)

    print(f"[{engine}] the detail card")
    page.click('#vHeadX [data-m="week"]')
    click_ev(page, "s:demo.backup")
    pop = page.evaluate("""() => { const p = document.getElementById('calPop'); return {hidden: p.hidden, text: p.innerText.replace(/\\s+/g, ' '), who: (p.querySelector('.who') || {}).textContent || '',
        controls: [...p.querySelectorAll('button, select, input')].filter(x => !x.hidden && x.offsetParent).map(x => x.dataset.act || x.id), sel: !!document.querySelector('#vBody .ce.sel')}; }""")
    check("a chip opens the card: title, source, owner as a name tag, frequency, next run", not pop["hidden"] and "示範每日備份" in pop["text"] and "這台電腦的排程" in pop["text"]
          and pop["who"].strip() == "示範維運" and "每天 02:30" in pop["text"] and "11/19 02:30" in pop["text"] and pop["sel"], pop)
    check("…exactly two controls (開關, 頻率) besides closing, the owner and 跟…說", [c for c in pop["controls"] if c not in ("cpx", "person", "csay")] == ["csw", "cfsel", "cfreq"], pop["controls"])
    check("…everything else is read only, said in a line", "其他設定只能由負責的 agent 調整" in pop["text"] and "跟 示範維運 說" in pop["text"])
    opts = page.evaluate("[...document.querySelectorAll('#calFreq option')].map(o => o.textContent.replace('（現在）', ''))")
    check("the frequency presets", opts[1:] == ["每 5 分鐘", "每 10 分鐘", "每 15 分鐘", "每 30 分鐘", "每 1 小時", "每 2 小時", "每 4 小時", "每 6 小時", "每 12 小時", "每天"], opts)
    time_visible = page.evaluate("!document.getElementById('calTime').hidden")
    page.select_option("#calFreq", "daily")
    check("每天 asks for a time (and only then)", not time_visible and page.evaluate("!document.getElementById('calTime').hidden"))
    page.select_option("#calFreq", "")
    if shoot:
        page.screenshot(path=str(SHOTS / "office-calendar-detail.png"))
    click_ev(page, "s:demo.report")
    pop = page.inner_text("#calPop")
    check("an entry with no owner says 未指定 and has no 跟…說 button", "未指定" in pop and page.evaluate("!document.querySelector('#calPop [data-act=csay]')"))
    dis = page.evaluate("({sw: document.querySelector('#calPop [data-act=csw]').disabled, fq: document.getElementById('calFreq').disabled, go: document.querySelector('#calPop [data-act=cfreq]').disabled, tip: document.getElementById('calFreq').title})")
    check("…its frequency is disabled with a reason (nobody to ask); the launchd switch still works", dis["fq"] and dis["go"] and "沒有指定負責的 agent" in dis["tip"] and not dis["sw"], dis)
    click_ev(page, "l:n:示範背景分頁")
    dis = page.evaluate("({sw: document.querySelector('#calPop [data-act=csw]').disabled, fq: document.getElementById('calFreq').disabled, tip: document.querySelector('#calPop [data-act=csw]').title, text: document.getElementById('calPop').innerText})")
    check("a /loop on a tab no one sits at: both request controls disabled, with a tooltip", dis["sw"] and dis["fq"] and "不在辦公室" in dis["tip"] and "示範背景分頁" in dis["text"], dis)

    print(f"[{engine}] Esc order, and the person is one click away")
    page.keyboard.press("Escape")
    check("Esc closes the card first…", page.evaluate("document.getElementById('calPop').hidden && __office.view() === 'cal'"))
    page.keyboard.press("Escape")
    check("…then the sheet", page.evaluate("__office.view()") is None)
    open_cal(page, now=False)
    click_ev(page, "s:demo.backup")
    page.focus("#calFreq")
    page.keyboard.press("Escape")
    check("Esc with the focus in the frequency select closes the card too", page.evaluate("document.getElementById('calPop').hidden && __office.view() === 'cal'"))
    click_ev(page, "s:demo.backup")
    page.click("#calPop .who")
    page.wait_for_timeout(500)
    check("the owner's name closes the sheet and follows them", page.evaluate("__office.view()") is None and page.evaluate("__office.following()") == "b:ops")
    page.evaluate("__office.unfollow()")

    print(f"[{engine}] the launchd switch")
    open_cal(page, now=False)
    page.evaluate("""() => { window.__se = []; const h = __office.boardHost(); window.__seMode = 'ok';
        h.setEnabled = async (label, on) => { window.__se.push([label, on]); return window.__seMode === 'ok' ? { success: true, enabled: on } : { success: false, enabled: !on, message: 'x' }; }; }""")
    click_ev(page, "s:demo.backup")
    page.click("#calPop [data-act=csw]")
    page.wait_for_timeout(250)
    check("the switch calls setEnabled with the label and the wanted state", page.evaluate("window.__se") == [["demo.backup", False]], page.evaluate("window.__se"))
    check("…and shows what came back: 已停用, the chip is grey now", "已停用" in page.inner_text("#calPop") and page.evaluate("document.querySelector('#vBody [data-id=\"s:demo.backup\"]').classList.contains('off')")
          and page.evaluate("document.querySelector('#calPop .sw').getAttribute('aria-checked')") == "false")
    page.evaluate("window.__seMode = 'fail'")
    page.click("#calPop [data-act=csw]")
    page.wait_for_timeout(250)
    check("a switch that did not take stays as launchd says (still 已停用) and tells why",
          page.evaluate("window.__se.length") == 2 and "已停用" in page.inner_text("#calPop") and "沒有成功" in page.inner_text("#calPop"), page.inner_text("#calPop"))
    page.evaluate("window.__seMode = 'ok'")
    page.click("#calPop [data-act=csw]")
    page.wait_for_timeout(250)
    check("…and turns on again", page.evaluate("window.__se")[-1] == ["demo.backup", True] and "啟用中" in page.inner_text("#calPop"))
    click_ev(page, "s:demo.once")
    d = page.evaluate("({d: document.querySelector('#calPop [data-act=csw]').disabled, t: document.getElementById('calPop').innerText, f: document.getElementById('calFreq').disabled})")
    page.evaluate("document.querySelector('#calPop [data-act=csw]').click()")
    check("a one-off (toggle:false): the switch is disabled with the reason, and a click calls nothing",
          d["d"] and "單次排程沒有開關" in d["t"] and len(page.evaluate("window.__se")) == 3 and d["f"], d)

    print(f"[{engine}] requests to the owner")
    page.evaluate("""() => { window.__rq = []; window.__rqMode = 'ok'; __office.boardHost().request = async (key, text) => { window.__rq.push({ key, text });
        return window.__rqMode === 'ok' ? { success: true } : { success: false, code: window.__rqMode, message: 'x' }; }; }""")
    click_ev(page, "l:b:eng")
    check("a /loop's card: 預估 and its owner, the pace 每 20 分鐘 is the current one", "預估" in page.inner_text("#calPop") and page.evaluate("document.querySelector('#calFreq option:checked').textContent") == "選一個新的頻率…")
    check("…the request button is off until something is picked", page.evaluate("document.querySelector('#calPop [data-act=cfreq]').disabled"))
    page.select_option("#calFreq", "m30")
    check("…and on after", not page.evaluate("document.querySelector('#calPop [data-act=cfreq]').disabled"))
    page.click("#calPop [data-act=cfreq]")
    page.wait_for_timeout(250)
    rq = page.evaluate("window.__rq")
    check("the frequency request is a message to the owner's tab, with the reason in it",
          rq == [{"key": "b:eng", "text": "（辦公室排程）請把你的 /loop「每 20 分鐘看一次示範建置」改成每 30 分鐘一次，改完回我一句。"}], rq)
    check("…the entry shows 已請 示範工程師 調整 and did NOT change (still 每 20 分鐘)", "已請 示範工程師 調整" in page.inner_text("#calPop")
          and page.evaluate("document.querySelector('#vBody [data-id=\"l:b:eng\"]').classList.contains('pend')") and "每 20 分鐘" in page.inner_text('#vBody [data-id="l:b:eng"]')
          and "每 30 分鐘" not in page.inner_text('#vBody [data-id="l:b:eng"]'))
    check("…the controls are held while it waits", page.evaluate("document.getElementById('calFreq').disabled && document.querySelector('#calPop [data-act=csw]').disabled"))
    check("…a pending request is on the list the page keeps", [p["id"] for p in page.evaluate("__office.calPending()")] == ["l:b:eng"])
    page.evaluate("__office.calBackdate(11 * 60 * 1000)")
    page.wait_for_timeout(300)
    check("after 10 minutes it says 示範工程師 還沒改, and the controls are free again", "示範工程師 還沒改" in page.inner_text("#calPop") and not page.evaluate("document.getElementById('calFreq').disabled"))
    # the agent changes the pace: the detected state moves, the badge goes
    page.evaluate(feed_js(loops=[dict(LOOPS[0], delay=1800)] + LOOPS[1:]))
    page.wait_for_timeout(300)
    check("when the detected pace changes the pending badge goes and the entry shows the new pace",
          page.evaluate("__office.calPending()") == [] and "已請" not in page.inner_text("#calPop") and "每 30 分鐘" in page.inner_text('#vBody [data-id="l:b:eng"]'))
    page.evaluate("window.__rqMode = 'draft'")
    page.select_option("#calFreq", "h2")
    page.click("#calPop [data-act=cfreq]")
    page.wait_for_timeout(250)
    check("a refused request (the person's draft is in the box) is said and nothing is pending", "輸入框裡有還沒送出的字" in page.inner_text("#calPop") and page.evaluate("__office.calPending()") == [])
    page.evaluate("window.__rqMode = 'ok'; window.__rq.length = 0")
    page.click("#calPop [data-act=csw]")
    page.wait_for_timeout(250)
    check("stopping a /loop is a request: 停掉", page.evaluate("window.__rq")[0]["text"] == "（辦公室排程）請把你的 /loop「每 20 分鐘看一次示範建置」停掉，停好回我一句。" or "停掉" in page.evaluate("window.__rq")[0]["text"], page.evaluate("window.__rq"))
    check("…and the loop is still on the calendar, marked pending", page.evaluate("!!document.querySelector('#vBody [data-id=\"l:b:eng\"].pend')"))
    page.evaluate(feed_js(loops=LOOPS[1:]))
    page.wait_for_timeout(300)
    check("…it leaves only when detection no longer finds it (card closes, pending cleared)", page.evaluate("__office.calPending()") == [] and page.evaluate("document.getElementById('calPop').hidden")
          and not page.evaluate("!!document.querySelector('#vBody [data-id=\"l:b:eng\"]')"))
    page.evaluate(feed_js())
    click_ev(page, "c:b:sec:0")
    page.select_option("#calFreq", "daily")
    page.fill("#calTime", "07:45")
    page.evaluate("window.__rq.length = 0")
    page.click("#calPop [data-act=cfreq]")
    page.wait_for_timeout(250)
    rq = page.evaluate("window.__rq")
    check("a cron: the message carries its schedule; 每天 takes the time picked",
          len(rq) == 1 and rq[0]["key"] == "b:sec" and "cron 排程「每天早上整理示範週報」（0 9 * * *）改成每天 07:45 一次" in rq[0]["text"], rq)
    click_ev(page, "s:demo.poll")
    page.select_option("#calFreq", "m30")
    page.evaluate("window.__rq.length = 0")
    page.click("#calPop [data-act=cfreq]")
    page.wait_for_timeout(250)
    rq = page.evaluate("window.__rq")
    check("a launchd frequency: names the label and says to edit the plist", len(rq) == 1 and rq[0]["key"] == "b:eng" and "（demo.poll）" in rq[0]["text"] and "launchd plist" in rq[0]["text"]
          and "改成每 30 分鐘一次" in rq[0]["text"], rq)
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")

    print(f"[{engine}] snapshots do not rebuild it")
    open_cal(page, now=False)
    page.evaluate(feed_js())
    page.evaluate("window.__t = document.querySelector('#vBody .ce'); window.__t.__mine = 1; window.__g = document.querySelector('#vBody .cg'); window.__g.__mine = 1")
    for _ in range(3):
        page.evaluate("Office.update(Object.assign(__office.db(), { loops: __office.loops() }))")
        page.wait_for_timeout(400)
    check("an update with the same data keeps the same nodes", page.evaluate("document.body.contains(window.__t) && window.__t.__mine === 1 && window.__g.__mine === 1"))
    page.evaluate("document.getElementById('vBody').scrollTop = 120")
    page.evaluate("Office.update(Object.assign(__office.db(), { loops: __office.loops().slice(1) }))")
    page.wait_for_timeout(300)
    check("a change in the data rebuilds it but keeps the scroll position", page.evaluate("document.getElementById('vBody').scrollTop") == 120)
    page.evaluate(feed_js())

    print(f"[{engine}] readable text, narrow windows")
    sizes = page.evaluate("""() => { const f = s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize);
        return { chip: f('#vBody .cg .ce'), time: f('#vBody .cg .ce .ct'), head: f('#vBody .cg .ch'), range: f('#calRange'), seg: f('#vHeadX .cseg button'), hour: f('#vBody .chr'), leg: f('#vBody .cleg') }; }""")
    check("event text is 13px or more; headings 14px or more", sizes["chip"] >= 13 and sizes["time"] >= 13 and sizes["hour"] >= 13 and sizes["leg"] >= 13 and sizes["head"] >= 14 and sizes["range"] >= 14 and sizes["seg"] >= 14, sizes)
    if shoot:
        page.click('#vHeadX [data-m="month"]')
        page.evaluate("__office.calGo('month')")
        page.wait_for_timeout(200)
        page.screenshot(path=str(SHOTS / "office-calendar-month.png"))
        page.click('#vHeadX [data-m="day"]')
        page.wait_for_timeout(200)
        page.screenshot(path=str(SHOTS / "office-calendar-day.png"))
    page.close()

    page = browser.new_page(viewport={"width": 600, "height": 800})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.goto(base + "/office.html")
    page.wait_for_timeout(1000)
    open_cal(page)
    check("narrow (600 px): the day view is the default", page.evaluate("__office.calState().mode") == "day")
    for mode in ("day", "week", "month"):
        page.evaluate(f"__office.calGo('{mode}')")
        page.wait_for_timeout(200)
        o = page.evaluate("""() => { const b = document.getElementById('vBody'), w = document.getElementById('wBox').getBoundingClientRect(), h = document.querySelector('#wall .vhead').getBoundingClientRect();
            return { over: b.scrollWidth > b.clientWidth + 1, l: w.left, r: w.right, iw: innerWidth, hr: h.right, wr: w.right }; }""")
        check(f"narrow {mode}: no sideways scroll, the sheet fits the window", not o["over"] and o["l"] >= 0 and o["r"] <= o["iw"] and o["hr"] <= o["wr"] + 1, o)
    page.evaluate("__office.calGo('day')")
    page.wait_for_timeout(150)
    click_ev(page, "l:b:eng")
    o = page.evaluate("""() => { const p = document.querySelector('#calPop .cp').getBoundingClientRect(), w = document.getElementById('wBox').getBoundingClientRect(); return { l: p.left >= w.left - 1, r: p.right <= w.right + 1, b: p.bottom <= w.bottom + 1, t: p.top >= w.top }; }""")
    check("narrow: the detail card stays inside the sheet", all(o.values()), o)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-calendar-narrow.png"))
    page.close()

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.goto(base + "/office.html?ui=1.18")
    page.wait_for_timeout(1000)
    open_cal(page)
    page.evaluate("__office.calGo('week')")
    page.wait_for_timeout(200)
    o = page.evaluate("""() => { const b = document.getElementById('vBody'); return { over: b.scrollWidth > b.clientWidth + 1, ui: getComputedStyle(document.documentElement).getPropertyValue('--ui').trim() }; }""")
    check("at interface size 1.18 nothing scrolls sideways", o["ui"] == "1.18" and not o["over"], o)
    click_ev(page, "s:demo.backup")
    inside = page.evaluate("""() => { const p = document.querySelector('#calPop .cp').getBoundingClientRect(), w = document.getElementById('wBox').getBoundingClientRect(); return p.right <= w.right + 1 && p.bottom <= w.bottom + 1 && p.top >= w.top; }""")
    check("…and the card is inside the sheet", inside)
    click_ev(page, "s:demo.backup")
    if shoot:
        page.screenshot(path=str(SHOTS / "office-calendar-week-ui118.png"))
    check("no console error from the page", not errors, errors[:5])
    page.close()


# ── part 2: inside index.html ─────────────────────────────────────────────
DATA = guide.load()
NOW = time.time()
ROSTER = {
    "chief": {"label": "示範秘書長", "responsibility": "統籌", "cmd": "claude", "bot": {"avatar": {"shape": "star", "color": "purple"}, "primary": True, "memory": True},
              "cli": {"key": "claude", "label": "Claude Code"}, "pin": {"ok": True}},
    "eng": {"label": "示範工程師", "responsibility": "寫程式", "cmd": "claude", "bot": {"avatar": {"shape": "egg", "color": "amber"}, "memory": True},
            "cli": {"key": "claude", "label": "Claude Code"}, "pin": {"ok": True, "model": "demo-model"}}}
BOTS = {"enabled": True, "roster": ROSTER, "primary": "chief", "memory_root": "/tmp/demo", "sessions": {"s700": {"bot": "chief"}, "s701": {"bot": "eng"}},
        "kinds": {"s700": "temp", "s701": "temp", "s702": "temp"}}
SESSIONS = [{"sid": "s700", "cmd": "claude", "alive": True, "label": "示範秘書長"}, {"sid": "s701", "cmd": "claude", "alive": True, "label": "示範工程師"},
            {"sid": "s702", "cmd": "claude", "alive": True, "label": "示範臨時分頁"}]
STATUS = {"s700": {"state": "idle", "dot": "", "summary": "idle"},
          "s701": {"state": "working", "dot": "sig-working", "action": "Running the demo tests",
                   "loop": {"kind": "loop", "status": "sleeping", "next_ts": NOW + 20 * 60 + 5, "delay": 1200, "rounds": 3, "reason": "每輪看一次示範建置", "crons": []}},
          "s702": {"state": "decision", "dot": "sig-decision", "narration": "示範",
                   "loop": {"kind": "cron", "status": "scheduled", "next_ts": None, "reason": "示範每半小時的檢查", "rounds": 0,
                            "crons": [{"desc": "示範每半小時的檢查", "schedule": "*/30 * * * *"}]}}}
SCHED2 = {"schedules": [
    {"id": "demo.backup", "title": "示範每日備份", "when": {"en": "Daily 02:30", "zh": "每天 02:30"}, "enabled": True, "running": False, "kind": "daily",
     "owner": "eng", "toggle": True, "slots": {"interval": None, "calendar": [{"Hour": 2, "Minute": 30}]}, "cmd": "/bin/secret-command --token abc"},
    {"id": "demo.stuck", "title": "示範開不起來的排程", "when": {"en": "Every 1 h", "zh": "每 1 小時"}, "enabled": True, "running": False, "kind": "interval",
     "owner": "", "toggle": True, "slots": {"interval": 3600, "calendar": []}},
    {"id": "demo.once", "title": "示範單次", "when": {"en": "One-off", "zh": "單次"}, "enabled": True, "running": False, "kind": "once", "toggle": False,
     "slots": {"interval": None, "calendar": [{"Month": 12, "Day": 24, "Hour": 9, "Minute": 0}]}}], "unsupported": False, "configured": True, "launchd_ok": True}
CFG = {"settings": {"language": "zh-TW", "experimental_bots": True, "experimental_office": True, "experimental_loops": False, "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}, "guide": {"tour": "done", "seen": guide.app_version()}}
GDATA = {"success": True, "features": guide.for_surface("desktop", CFG["settings"], DATA), "tours": DATA.get("tours"),
         "state": {"tour": "done", "seen": guide.app_version(), "version": guide.app_version()}}


def stub():
    return """
if (window.top === window) {
window.__calls = []; window.__send = []; window.__setEn = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s; window.__sched = %s; window.__guide = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push(prop);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's701'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'link_peer_bots': return R({peers: {}});
      case 'tasks_overview': return R({success: true, open: [], stuck: [], decisions: []});
      case 'schedules_list': return R(window.__sched);
      case 'schedule_set_enabled': { window.__setEn.push([args[0], args[1]]); const stuck = args[0] === 'demo.stuck'; return R({success: !stuck, enabled: stuck ? true : !!args[1]}); }
      case 'chat_send': window.__send.push({sid: args[0], text: args[1], clear: args[2]}); return R({success: true, queued: false, id: 'm1'});
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
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, BOTS, STATUS, SCHED2, GDATA))


def office(page):
    return next((f for f in page.frames if f.url.split("#")[0].endswith("/office.html")), None)


def calls(page, name):
    return page.evaluate("(n) => window.__calls.filter(c => c === n).length", name)


def ev2(fr, id_):
    """Open an entry's card inside the app: tomorrow's day view has every entry of this data (no +N)."""
    fr.evaluate("__office.calGo('day', Date.now() + 86400e3)")
    fr.click(f'#vBody [data-act="ev"][data-id="{id_}"]')


def run_part2(browser, base, engine):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.add_init_script(stub())
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    page.wait_for_function("['s701','s702'].every(sid => { const s = window.__sfHost.sessions()[sid]; return s && s._serverStatus && s._serverStatus.loop; })", timeout=20000)
    page.evaluate("SFOffice.open()")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office && Object.keys(f.contentWindow.__office.assignments()).length >= 2; } catch (e) { return false; } }""", timeout=15000)
    fr = office(page)
    page.wait_for_timeout(500)
    print(f"[{engine}] inside the app")
    check("schedules_list has not been asked for (the view is not open)", calls(page, "schedules_list") == 0)
    fr.evaluate("__office.openView('cal')")
    page.wait_for_timeout(700)
    fr.evaluate("__office.calSetNow(Date.now())")
    m = fr.evaluate("__office.calModel()")
    check("the tabs' loops and this computer's schedules are on the calendar (the command never reaches the page)",
          {"l:b:eng", "c:t:s702:0", "s:demo.backup", "s:demo.stuck", "s:demo.once"} <= set(m["ents"]) and "secret" not in json.dumps(m), m["ents"])
    check("schedules_list was asked once, when the view opened", calls(page, "schedules_list") == 1, calls(page, "schedules_list"))
    check("…its default pace is once a minute", fr.evaluate("__office.schedPollMs()") == 60000)
    fr.evaluate("document.querySelector('#vBody .ce').__mine = 1; window.__t = document.querySelector('#vBody .ce'); window.__g = document.querySelector('#vBody .cg'); window.__g.__mine = 1")
    page.wait_for_timeout(7000)
    check("two snapshots later the calendar's nodes are the same ones", fr.evaluate("document.body.contains(window.__t) && window.__t.__mine === 1 && window.__g.__mine === 1"))
    check("…and it was not asked again inside the minute", calls(page, "schedules_list") == 1, calls(page, "schedules_list"))
    # the owner's message
    ev2(fr, "s:demo.backup")
    page.wait_for_timeout(200)
    check("the owner in the schedule file ('eng', a bot id) is that bot's name here", fr.evaluate("document.querySelector('#calPop .who').textContent.trim()") == "示範工程師")
    fr.select_option("#calFreq", "h4")
    fr.click("#calPop [data-act=cfreq]")
    page.wait_for_timeout(500)
    sent = page.evaluate("window.__send")
    check("the request reached chat_send for the owner's tab (s701), as plain text, never clearing the draft",
          len(sent) == 1 and sent[0]["sid"] == "s701" and sent[0]["clear"] is False and sent[0]["text"].startswith("（辦公室排程）請把這台電腦的排程「示範每日備份」（demo.backup）改成每 4 小時一次")
          and "launchd plist" in sent[0]["text"], sent)
    check("…and shows 已請 示範工程師 調整 without flipping the schedule", "已請 示範工程師 調整" in fr.inner_text("#calPop") and "每天 02:30" in fr.inner_text("#calPop"))
    # the direct switch
    ev2(fr, "s:demo.stuck")
    page.wait_for_timeout(200)
    check("a schedule with no owner: the request controls are off, the switch is not", fr.evaluate("document.getElementById('calFreq').disabled") and not fr.evaluate("document.querySelector('#calPop [data-act=csw]').disabled"))
    fr.click("#calPop [data-act=csw]")
    page.wait_for_timeout(500)
    check("the switch called schedule_set_enabled with the label and the wanted state", page.evaluate("window.__setEn") == [["demo.stuck", False]], page.evaluate("window.__setEn"))
    check("…launchd said it is still on: the page says so, and does not flip", "啟用中" in fr.inner_text("#calPop") and "沒有成功" in fr.inner_text("#calPop")
          and fr.evaluate("document.querySelector('#calPop .sw').getAttribute('aria-checked')") == "true")
    # the loop in the tab
    ev2(fr, "l:b:eng")
    page.wait_for_timeout(200)
    fr.select_option("#calFreq", "m30")
    fr.click("#calPop [data-act=cfreq]")
    page.wait_for_timeout(500)
    sent = page.evaluate("window.__send")
    check("a /loop's pace request goes to its own tab, with the reason and the new pace",
          len(sent) == 2 and sent[1]["sid"] == "s701" and sent[1]["text"] == "（辦公室排程）請把你的 /loop「每輪看一次示範建置」改成每 30 分鐘一次，改完回我一句。", sent[-1:])
    ev2(fr, "c:t:s702:0")
    page.wait_for_timeout(200)
    check("a cron on a tab that is no bot: its owner is that tab (the request can reach it)", not fr.evaluate("document.getElementById('calFreq').disabled"))
    fr.evaluate("__office.closeView()")
    n_open = calls(page, "schedules_list")
    page.wait_for_timeout(2500)
    check("with the view closed it is not asked at all", calls(page, "schedules_list") == n_open)
    fr.evaluate("__office.setSchedPoll(1200); __office.openView('cal')")
    page.wait_for_timeout(3600)
    n = calls(page, "schedules_list") - n_open
    check("open again, at the pace it is set to, it is asked again — no faster", 2 <= n <= 5, n)
    fr.evaluate("__office.closeView(); __office.setSchedPoll(60000)")
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
                run_part1(browser, base, engine)
                run_part2(browser, base, engine)
            finally:
                browser.close()
    finally:
        srv.shutdown()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
