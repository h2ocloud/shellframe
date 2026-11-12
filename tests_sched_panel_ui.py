#!/usr/bin/env python3
"""The Loops panel's schedule section in the real page: the whole web/index.html in
a browser, with a stand-in for the Python side. The rows it is fed are the real ones:
fake plists in a temp folder go through api_schedules.py (launchctl replaced, the
clock pinned), so what the screenshots show is what the backend says.

  - each shape of schedule reads as what it is (a daily job, an interval, five
    weekdays x four times as one phrase, a job launchd has not loaded)
  - a one-off is never "every day": three of them on one script are one row with the
    times under it, "ran 11/10", and a warning that it fires again next year; it has
    no on/off switch, an ordinary row still has
  - armed today / armed tomorrow / only 2 of 3 loaded / not armed (waiting for you)
  - a KeepAlive job that is not running and keeps failing is flagged; a running one is not
  - English and Traditional Chinese; the header and overview counts agree with the rows
  - Edit hands the group (or the job) to the backend; the switch flips the job
  - another platform says it is unsupported; no prefix configured says how to set one
  - the panel does not scroll sideways, at its default width and at a narrow one

Screenshots (demo data only): qa-shots/sched-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_sched_panel_ui.py
"""
import atexit
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# A test never reads or writes the app's real config folder (sf_config.config_dir): say so
# even when this file is started some way run_tests.sh does not.
os.environ.setdefault("SHELLFRAME_TESTS", "1")
SHOTS = HERE / "qa-shots"
NOW = datetime(2026, 11, 12, 14, 20)

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
    print("SKIP  tests_sched_panel_ui.py（沒裝 playwright）")
    sys.exit(0)

import api_schedules  # noqa: E402
import sf_config  # noqa: E402

# ── the backend, over fake plists ──────────────────────────────────────────

LAUNCHCTL = {"list": ""}


def fake_run(cmd, *a, **kw):
    if cmd[:2] == ["launchctl", "list"]:
        return subprocess.CompletedProcess(cmd, 0, LAUNCHCTL["list"], "")
    raise AssertionError(f"unexpected process: {cmd}")


subprocess.run = fake_run


def payload(plists, loaded, cfg=None, supported=True):
    """schedules_list() for a folder of fake plists. `loaded`: 'pid\\tstatus\\tlabel' lines."""
    folder = Path(tempfile.mkdtemp(prefix="sched-ui-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    for label, plist in plists:
        plist = dict(plist, Label=label)
        (folder / f"{label}.plist").write_bytes(plistlib.dumps(plist))
    (sf_config.config_dir() / "schedules.json").write_text(
        json.dumps(cfg if cfg is not None else {"prefixes": ["demo."]}), encoding="utf-8")
    LAUNCHCTL["list"] = "PID\tStatus\tLabel\n" + "".join(l + "\n" for l in loaded)

    class Api(api_schedules.SchedulesApiMixin):
        _launch_agents_dir = staticmethod(lambda: folder)
        _sched_now = staticmethod(lambda: NOW)
        _sched_supported = staticmethod(lambda: supported)

    return json.loads(Api().schedules_list())


def cal(*entries, script="/opt/demo/job.sh"):
    return {"ProgramArguments": ["/bin/bash", script],
            "StartCalendarInterval": list(entries) if len(entries) != 1 else entries[0]}


def at(h, m):
    return {"Hour": h, "Minute": m}


def once(h, m, arg, day, script="/opt/demo/punch.sh", month=11):
    return {"ProgramArguments": ["/bin/zsh", script, arg],
            "StartCalendarInterval": {"Month": month, "Day": day, "Hour": h, "Minute": m}}


WORKWEEK = [{"Weekday": d, "Hour": h, "Minute": m} for d in range(1, 6) for h, m in ((9, 50), (12, 30), (15, 30), (17, 50))]
DEMO = [
    ("demo.backup", cal(at(2, 0), at(21, 0), script="/opt/demo/backup.sh")),
    ("demo.health-check", cal(at(9, 15), at(16, 0), script="/opt/demo/health.sh")),
    ("demo.cache-warm", {"ProgramArguments": ["/bin/bash", "/opt/demo/warm.sh"], "StartInterval": 600}),
    ("demo.newsletter", cal(at(18, 30), script="/opt/demo/newsletter.sh")),
    ("demo.cleanup", cal(at(3, 10), script="/opt/demo/cleanup.sh")),
    ("demo.weekdays", cal(*WORKWEEK, script="/opt/demo/weekdays.sh")),
    ("demo.retired", cal(at(6, 30), script="/opt/demo/retired.py")),
    ("demo.punch-in", once(9, 5, "in", 10)),
    ("demo.punch-out", once(18, 25, "out", 10)),
    ("demo.punch-out-early", once(18, 10, "out", 10)),
    ("demo.chat-bot", {"ProgramArguments": ["/usr/bin/node", "/opt/demo/bot/index.js"], "KeepAlive": True}),
    ("demo.relay", {"ProgramArguments": ["/usr/bin/node", "/opt/demo/relay.js"], "KeepAlive": True}),
]
DEMO_LOADED = ["-\t1\tdemo.backup", "-\t0\tdemo.health-check", "5521\t0\tdemo.cache-warm", "-\t0\tdemo.newsletter",
               "-\t0\tdemo.cleanup", "-\t0\tdemo.weekdays", "-\t0\tdemo.punch-in", "-\t0\tdemo.punch-out",
               "-\t0\tdemo.punch-out-early", "-\t78\tdemo.chat-bot", "3829\t0\tdemo.relay"]
# (demo.retired is on disk but not loaded)

DEMO_PAYLOAD = payload(DEMO, DEMO_LOADED)

STATES = [   # one-off states, as the panel would show them on the afternoon of 11/12
    ("demo.card-in", once(9, 5, "in", 13, "/opt/demo/card.sh")), ("demo.card-out", once(18, 25, "out", 13, "/opt/demo/card.sh")),
    ("demo.card-out-early", once(18, 10, "out", 13, "/opt/demo/card.sh")),
    ("demo.report-1", once(9, 0, "a", 12, "/opt/demo/report.sh")), ("demo.report-2", once(17, 30, "b", 12, "/opt/demo/report.sh")),
    ("demo.report-3", once(18, 45, "c", 12, "/opt/demo/report.sh")),
    ("demo.sweep-1", once(8, 0, "a", 20, "/opt/demo/sweep.sh")), ("demo.sweep-2", once(20, 0, "b", 20, "/opt/demo/sweep.sh")),
    ("demo.once-today", once(18, 40, "x", 12, "/opt/demo/single.sh")),
]
STATES_LOADED = ["-\t0\tdemo.card-in", "-\t0\tdemo.card-out", "-\t0\tdemo.card-out-early",       # all armed for tomorrow
                 "-\t0\tdemo.report-1", "-\t0\tdemo.report-2",                                    # one of three not loaded
                 "-\t0\tdemo.once-today"]                                                         # sweep-*: not loaded
STATES_PAYLOAD = payload(STATES, STATES_LOADED)

# ── the page ───────────────────────────────────────────────────────────────

STUB = """
window.__calls = [];
window.__cfg = %s; window.__sched = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R([{sid: 's1', cmd: 'claude', alive: true, label: 'Demo', bridge_enabled: true}]);
      case 'get_active_tab': return R({sid: 's1'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'schedules_list': return R(window.__sched);
      case 'schedule_set_enabled': return R({success: true, enabled: !!args[1]});
      case 'schedule_prompt': return R({success: true, sid: args[1]});
      default: return R('{}');
    }
  };
}})};
"""


def cfg(lang):
    return {"settings": {"experimental_loops": True, "language": lang, "sidebar_open": True},
            "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}


def open_panel(browser, data, lang="zh-TW", width=1100, board_w=None):
    page = browser.new_page(viewport={"width": width, "height": 1100}, device_scale_factor=2)
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    c = cfg(lang)
    if board_w:
        c["settings"]["board_width"] = board_w
    page.add_init_script(STUB % (json.dumps(c, ensure_ascii=False), json.dumps(data, ensure_ascii=False)))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector("#btn-board", state="visible", timeout=30000)
    page.click("#btn-board")
    page.wait_for_function("document.querySelector('#board-list .loop-group') !== null", timeout=15000)
    page.wait_for_timeout(500)
    return page


def rows(page):
    return page.evaluate("""[...document.querySelectorAll('#board-list .sched-row')].map(r => ({
      id: r.dataset.id, cls: r.className, title: r.querySelector('.sched-title').childNodes[0].textContent,
      freq: r.querySelector('.sched-freq').textContent,
      notes: [...r.querySelectorAll('.sched-note')].map(n => n.textContent),
      members: [...r.querySelectorAll('.sched-member')].map(m => m.textContent),
      times: [...r.querySelectorAll('.sched-mt')].map(m => m.textContent),
      states: [...r.querySelectorAll('.sched-ms')].map(m => m.textContent),
      toggle: !!r.querySelector('.sched-toggle'), on: !!r.querySelector('.sched-toggle.on'),
      edit: !!r.querySelector('.sched-edit'), text: r.textContent }))""")


def by(rs, key):
    return next(r for r in rs if key in r["id"])


SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()

    print("the demo panel, Traditional Chinese")
    page = open_panel(browser, DEMO_PAYLOAD)
    rs = rows(page)
    check("12 plists, the three one-offs as one row: 10 rows", len(rs) == 10, [r["id"] for r in rs])
    kinds = [("k-daemon" in r["cls"]) for r in rs]
    check("the two KeepAlive jobs are a block of their own, after the eight schedules", kinds == [False] * 8 + [True] * 2
          and [r["id"] for r in rs if "k-daemon" in r["cls"]] == ["demo.chat-bot", "demo.relay"], kinds)
    check("an ordinary row has its switch, and it is on", by(rs, "backup")["toggle"] and by(rs, "backup")["on"])
    check("a job launchd has not loaded is off", by(rs, "retired")["toggle"] and not by(rs, "retired")["on"] and " off" in by(rs, "retired")["cls"])
    check("daily with two times", by(rs, "backup")["freq"] == "每天 02:00／21:00")
    check("an interval, and that it is running", by(rs, "cache-warm")["freq"] == "每 10 分鐘" and "執行中" in by(rs, "cache-warm")["text"])
    check("five weekdays x four times read as one phrase, not 'every day'", by(rs, "weekdays")["freq"] == "週一～五 09:50／12:30／15:30／17:50")
    check("the last run's failure is shown", "上次失敗" in by(rs, "backup")["text"] and "上次失敗" not in by(rs, "health-check")["text"])
    g = by(rs, "group:")
    check("the three one-offs are one row, ×3, named for the script", g["title"] == "punch" and "×3" in g["text"], g["title"])
    check("…it reads 'ran 11/10', not 'every day … on'", g["freq"] == "單次・11/10 已執行" and "每天" not in g["text"] and "啟用" not in g["text"], g["freq"])
    check("…with the three times under it, in the order they fire", g["times"] == ["09:05", "18:10", "18:25"]
          and g["states"] == ["已執行"] * 3, (g["times"], g["states"]))
    check("…and the warning that it fires again next year", g["notes"] == ["⚠ 隔年 11/10 會再觸發"] and " warn" in g["cls"], g["notes"])
    check("…it has no on/off switch (armed by asking the agent), but it can be edited", not g["toggle"] and g["edit"])
    bot = by(rs, "chat-bot")
    check("a resident job that is not running and exited 78 is flagged red", bot["notes"] == ["⚠ 沒有在跑・上次 exit 78"] and " bad" in bot["cls"]
          and bot["freq"] == "由 launchd 保持常駐", bot)
    check("a resident job that is running is not", by(rs, "relay")["notes"] == [] and "執行中" in by(rs, "relay")["text"])
    head = page.evaluate("[...document.querySelectorAll('#board-list .loop-group')].map(g => g.textContent)")
    check("the header counts the schedules that are on (the one-off group, having run, is not; the resident jobs are not schedules)",
          head[0] == "排程6/8 開", head)
    check("the resident block has its own header: how many are running", head[1] == "常駐1/2 執行中", head)
    check("the overview line agrees with the schedules", page.evaluate("document.getElementById('feed-overview').textContent.trim()") == "⏰ 6/8")
    check("the page raised no error", not page.errors, page.errors[:3])
    geo = page.evaluate("""(() => {
      const box = e => e.getBoundingClientRect();
      const edits = [...document.querySelectorAll('.sched-edit')].map(e => Math.round(box(e).right));
      return {edits, titles: [...document.querySelectorAll('.sched-title')].map(e => Math.round(box(e).height)),
              chips: [...document.querySelectorAll('.sched-run, .sched-fail, .sched-n')].map(e => Math.round(box(e).height))}; })()""")
    check("every row's Edit button sits in the same column (a one-off's missing switch leaves its place)", len(set(geo["edits"])) == 1, geo["edits"])
    check("a name stays on one line, and so does every chip (no 'running' cut in two)", max(geo["titles"]) <= 20 and max(geo["chips"]) <= 16, geo)
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-panel-zh.png"))

    print("interactions")
    page.click(".sched-row[data-id^='group:'] .sched-edit")
    page.wait_for_timeout(200)
    call = [c for c in page.evaluate("window.__calls") if c[0] == "schedule_prompt"]
    check("Edit on the group hands the group to the backend with the current tab", call and call[-1][1][0].startswith("group:") and call[-1][1][1] == "s1", call)
    page.click(".sched-row[data-id='demo.newsletter'] .sched-toggle")
    page.wait_for_timeout(200)
    call = [c for c in page.evaluate("window.__calls") if c[0] == "schedule_set_enabled"]
    check("the switch flips that job off", call and call[-1][1] == ["demo.newsletter", False], call)
    rs2 = rows(page)
    check("…and the row, the header count and the overview follow", not by(rs2, "newsletter")["on"]
          and page.evaluate("document.querySelector('#board-list .loop-group-n').textContent") == "5/8 開"
          and page.evaluate("document.getElementById('feed-overview').textContent.trim()") == "⏰ 5/8")
    page.close()

    print("English")
    page = open_panel(browser, DEMO_PAYLOAD, lang="en")
    rs = rows(page)
    check("cadence in English", by(rs, "backup")["freq"] == "Daily 02:00 / 21:00" and by(rs, "weekdays")["freq"] == "Mon–Fri 09:50 / 12:30 / 15:30 / 17:50"
          and by(rs, "cache-warm")["freq"] == "Every 10 min")
    g = by(rs, "group:")
    check("the one-off in English", g["freq"] == "One-off · ran 11/10" and g["notes"] == ["⚠ Fires again on 11/10 next year"]
          and g["states"] == ["ran"] * 3, (g["freq"], g["notes"], g["states"]))
    check("the resident job in English", by(rs, "chat-bot")["notes"] == ["⚠ Not running · last exit 78"]
          and by(rs, "chat-bot")["freq"] == "Kept alive by launchd")
    head = page.evaluate("[...document.querySelectorAll('#board-list .loop-group')].map(g => g.textContent)")
    check("both block headers in English", head[:2] == ["Schedules6/8 on", "Resident1/2 running"], head)
    check("the words around the rows are English too", page.evaluate("document.querySelector('#board-list .loop-group').textContent").startswith("Schedules")
          and "last run failed" in by(rs, "backup")["text"] and "Edit" in by(rs, "backup")["text"], by(rs, "backup")["text"])
    check("nothing leaked in Chinese into the English panel's schedule rows", not any(
        ch in r["text"] for r in rs for ch in "每單次週編輯執行常駐"), [r["text"] for r in rs if any(ch in r["text"] for ch in "每單次週編輯執行常駐")][:2])
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-panel-en.png"))
    page.close()

    print("one-off states")
    page = open_panel(browser, STATES_PAYLOAD)
    rs = rows(page)
    card, report, sweep, today = by(rs, "card.sh"), by(rs, "report.sh"), by(rs, "sweep.sh"), by(rs, "once-today")
    check("armed for tomorrow: the date, in green", card["freq"] == "單次・已排 11/13" and " st-armed" in card["cls"] and "off" not in card["cls"].split(), card["freq"])
    check("only 2 of 3 loaded: says so and names the one that will not fire", report["freq"] == "單次・3 個只排了 2 個"
          and any("report-3" in n and "不會觸發" in n for n in report["notes"]) and " st-partial" in report["cls"], report["notes"])
    check("not loaded: not armed, waiting for you; no yearly warning because it cannot fire", sweep["freq"] == "單次・未排（等你說要排）"
          and not any("隔年" in n for n in sweep["notes"]) and sweep["states"] == ["未載入", "未載入"], sweep)
    check("a single one-off armed for later today", today["freq"] == "單次・已排 今天 18:40" and not today["toggle"], today["freq"])
    check("a group says each member's own state: this morning's has run, this evening's is armed, one is not loaded",
          card["states"] == ["已排"] * 3 and report["states"] == ["已執行", "已排", "未載入"], (card["states"], report["states"]))
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-oneoff-states-zh.png"))
    page.close()
    page = open_panel(browser, STATES_PAYLOAD, lang="en")
    rs = rows(page)
    check("the same states in English", by(rs, "card.sh")["freq"] == "One-off · armed 11/13" and by(rs, "report.sh")["freq"] == "One-off · only 2 of 3 armed"
          and by(rs, "sweep.sh")["freq"] == "One-off · not armed (waiting for you)" and by(rs, "once-today")["freq"] == "One-off · armed today 18:40")
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-oneoff-states-en.png"))
    page.close()

    print("narrow panel")
    page = open_panel(browser, DEMO_PAYLOAD, board_w=200)
    ov = page.evaluate("(() => { const l = document.getElementById('board-list'); return [l.scrollWidth, l.clientWidth]; })()")
    check("at 200px the list does not scroll sideways", ov[0] <= ov[1], ov)
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-panel-narrow.png"))
    page.close()
    page = open_panel(browser, DEMO_PAYLOAD)
    ov = page.evaluate("(() => { const l = document.getElementById('board-list'); return [l.scrollWidth, l.clientWidth]; })()")
    check("…nor at the default width", ov[0] <= ov[1], ov)
    page.close()

    print("when something is wrong with what the panel reads")
    page = open_panel(browser, dict(DEMO_PAYLOAD, launchd_ok=False), lang="en")
    msgs = page.evaluate("[...document.querySelectorAll('#board-list .sched-msg')].map(m => m.textContent)")
    check("launchd could not be asked: a warning above the rows, which are still listed", len(msgs) == 1 and "Could not ask launchd" in msgs[0]
          and len(rows(page)) == 10, msgs)
    page.close()
    page = open_panel(browser, dict(payload([], [], cfg={}), config_error="schedules.json: not valid JSON (Expecting value)"), lang="en")
    msgs = page.evaluate("[...document.querySelectorAll('#board-list .sched-msg')].map(m => m.textContent)")
    check("a broken schedules.json is shown, with the way to set a prefix under it", "not valid JSON" in msgs[0] and len(msgs) == 2, msgs)
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-config-error-en.png"))
    page.close()

    print("another platform, and nothing configured")
    page = open_panel(browser, payload([], [], supported=False))
    msg = page.evaluate("(document.querySelector('#board-list .sched-msg') || {}).textContent || ''")
    check("a platform without launchd says it is unsupported, and lists nothing", "尚未支援" in msg and not rows(page), msg)
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-unsupported-zh.png"))
    page.close()
    page = open_panel(browser, payload([], [], supported=False), lang="en")
    check("…in English as well", "not supported yet" in page.evaluate("(document.querySelector('#board-list .sched-msg') || {}).textContent || ''"))
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-unsupported-en.png"))
    page.close()
    page = open_panel(browser, payload(DEMO, DEMO_LOADED, cfg={}), lang="en")
    msg = page.evaluate("(document.querySelector('#board-list .sched-msg') || {}).textContent || ''")
    check("with no prefix configured the panel says how to set one, and lists nothing", "schedules.json" in msg and "prefixes" in msg and not rows(page), msg)
    page.locator("#board-panel").screenshot(path=str(SHOTS / "sched-not-configured-en.png"))
    page.close()
    page = open_panel(browser, payload(DEMO, DEMO_LOADED, cfg={}))
    check("…and in Chinese", "尚未設定" in page.evaluate("(document.querySelector('#board-list .sched-msg') || {}).textContent || ''") or
          "還沒設定" in page.evaluate("(document.querySelector('#board-list .sched-msg') || {}).textContent || ''"))
    page.close()

    browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
