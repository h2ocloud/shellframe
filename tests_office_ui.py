#!/usr/bin/env python3
"""The office (settings.experimental_office) in the real page, with a stand-in Python side.

  - the switch off: no button in the sidebar's foot, no iframe; the switch sits with the other
    experimental switches (Settings → General) and carries the guide's mark
  - on: the button appears; pressing it opens the office over the whole window, in an iframe
  - the iframe gets a snapshot built from the page's own data (roster, tab lights, tasks, the
    paired machine's bots, the open tabs' subagents): bots at their desks, the primary bot at
    the lobby desk, a bot with no tab asleep, an AI tab that is no bot at the interns' table,
    a terminal not shown, part-timers beside the tab that launched them (working, stuck), more
    than four on one desk drawn as three and "+N"
  - a bot missing from the next snapshot shows as left (seat held); a finished part-timer walks out
  - what the office keeps (its view mode) goes through office_prefs_save and comes back from
    office_prefs after the page is loaded again (pywebview clears localStorage on every start)
  - Esc inside the office closes it; the iframe is gone and the 3 s refresh stops; the window
    in the background does not refresh either
  - no console error from either page
Demo data only (made-up names). Headless Chromium, and WebKit (the macOS app's engine) when playwright
has it. Screenshots (Chromium): qa-shots/office-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_ui.py
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
    print("SKIP  tests_office_ui.py（沒裝 playwright）")
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
    "outside": {"label": "示範外部", "responsibility": "外部服務", "cmd": "",
                "bot": {"avatar": {"shape": "hex", "color": "blue"}, "external": {"kind": "webhook"}}},
    "worker": {"label": "示範派工", "responsibility": "一般派工", "cmd": "claude"},
}
BOTS = {"enabled": True, "roster": ROSTER, "primary": "chief", "memory_root": "/tmp/demo",
        "sessions": {"s700": {"bot": "chief"}, "s701": {"bot": "eng"}},
        "kinds": {"s700": "temp", "s701": "temp", "s702": "temp", "s703": "terminal"}}
SESSIONS = [{"sid": "s700", "cmd": "claude", "alive": True, "label": "示範秘書長"},
            {"sid": "s701", "cmd": "claude", "alive": True, "label": "示範工程師"},
            {"sid": "s702", "cmd": "claude", "alive": True, "label": "示範臨時分頁"},
            {"sid": "s703", "cmd": "zsh", "alive": True, "label": "示範終端機"}]
STATUS = {"s700": {"state": "idle", "dot": "", "summary": "idle"},
          "s701": {"state": "working", "dot": "sig-working", "action": "Running the demo tests"},
          "s702": {"state": "decision", "dot": "sig-decision", "narration": "示範：要選方案 A 還是 B？"}}
TASKS = {"success": True, "decisions": [], "stuck": [{"id": 12, "idle_s": 90000}],
         "open": [{"id": 12, "title": "示範登入頁改版", "kind": "work", "status": "doing", "owner": "eng",
                   "requester": "chief", "waiting_on": "", "next_step": "補上表單驗證",
                   "created_at": NOW - 200000, "updated_at": NOW - 90000},
                  {"id": 13, "title": "示範比較表", "kind": "work", "status": "todo", "owner": "res",
                   "requester": "user", "waiting_on": "user", "next_step": "等你選",
                   "created_at": NOW - 3000, "updated_at": NOW - 3000}]}
PEERS = {"peers": {"pid-demo-1": {"name": "示範電腦", "addr": "示範電腦", "primary": "builder",
                                  "bots": [{"role": "builder", "label": "示範建置員", "state": "awake", "primary": True,
                                            "avatar": {"shape": "ghost", "color": "green"}, "activity": "working",
                                            "responsibility": "建置與測試"}],
                                  "tabs": {"r1": {"kind": "temp", "name": "示範隔壁分頁", "cli": "codex",
                                                  "activity": "idle"}}}}}


def helper(aid, desc, state, started_ago, last_ago, model="demo-model"):
    return {"id": aid, "description": desc, "model": model, "agent_type": "general-purpose", "state": state,
            "started_at": NOW - started_ago, "last_at": NOW - last_ago}


HELPERS = {"s701": [helper("a1", "示範：跑一次完整測試", "working", 300, 20)],
           "s702": [helper("b1", "示範：查三份文件的差異", "stuck", 1800, 900)]}
CFG = {"settings": {"language": "zh-TW", "experimental_bots": True, "experimental_office": False, "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {},
       "guide": {"tour": "done", "seen": "0.48.0"}}
GDATA = {"success": True, "features": guide.for_surface("desktop", CFG["settings"], DATA), "tours": DATA.get("tours"),
         "state": {"tour": "done", "seen": "0.48.0", "version": guide.app_version()}}


def stub(prefs):
    return """
if (window.top === window) {
window.__calls = []; window.__saved = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s; window.__tasks = %s;
window.__peers = %s; window.__helpers = %s; window.__guide = %s; window.__prefs = %s;
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
      case 'link_peer_bots': return R(window.__peers);
      case 'tasks_overview': return R(window.__tasks);
      case 'office_prefs': return R({success: true, prefs: window.__prefs});
      case 'office_prefs_save': window.__saved.push(JSON.parse(args[0])); return R({success: true});
      case 'office_helpers': return R({success: true, helpers: window.__helpers});
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
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, BOTS, STATUS, TASKS, PEERS, HELPERS, GDATA, prefs))


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


def run(browser, base, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.add_init_script(stub({}))
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    page.wait_for_function("""['s701:sig-working','s702:sig-decision'].every(p => { const [sid, c] = p.split(':');
        const s = window.__sfHost.sessions()[sid]; return s && s._dotClass === c; })""", timeout=20000)

    print(f"[{engine}] switch off")
    check("no office button while the switch is off", page.query_selector("#btn-office") is None)
    check("…and no iframe", page.query_selector("iframe") is None)
    page.click("#btn-settings")
    page.wait_for_selector("#setting-experimental-office", state="attached", timeout=5000)
    page.click('#settings-tabs-bar [data-tab="office"]')
    sw = page.evaluate("""() => { const b = document.getElementById('setting-experimental-office'),
        loops = document.getElementById('setting-experimental-loops');
        return {on: b.classList.contains('on'), guide: b.dataset.guide, shown: !!b.getClientRects().length,
                panel: b.closest('.settings-tab-panel').id, loopsPanel: loops.closest('.settings-tab-panel').id,
                label: document.querySelector('#sfo-setting .sfo-label').textContent}; }""")
    check("the switch sits in Settings → Office (its own category, apart from Loops), off, with the guide's mark",
          sw["panel"] == "settings-panel-office" and sw["loopsPanel"] == "settings-panel-loops" and sw["shown"]
          and not sw["on"] and sw["guide"] == "office", sw)
    check("…its label is in the page's language", "辦公室" in sw["label"], sw["label"])
    if shoot:
        page.locator("#settings-panel-office .sx-card").screenshot(path=str(SHOTS / "office-switch.png"))
    page.click("#setting-experimental-office")
    page.wait_for_timeout(300)
    check("switching it on saves experimental_office", page.evaluate("window.__cfg.settings.experimental_office") is True)
    page.click("#btn-close-settings")
    check("…and puts the office button in the sidebar's foot",
          page.evaluate("!!document.querySelector('#sb-footer-row #btn-office')"))
    check("…named in the page's language", page.get_attribute("#btn-office", "aria-label") == "辦公室（實驗性）")

    print(f"[{engine}] open")
    page.click("#btn-office")
    page.wait_for_selector("#sfo-overlay iframe", timeout=5000)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && Object.keys(f.contentWindow.__office.assignments()).length >= 3; } catch (e) { return false; } }""",
                           timeout=15000)
    page.wait_for_timeout(600)
    fr = office(page)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-overview.png"))      # as it opens: nothing moved by the test
    seen = fr.evaluate("""() => { const a = __office.assignments(), keys = ['sec', a.eng, a.res, 'p:pid-demo-1:0'];
        return keys.map(k => { const p = __office.seatScreen(k); return [k, !!p && p.x > 0 && p.x < innerWidth && p.y > 58 && p.y < innerHeight]; }); }""")
    check("as it opens, the whole floor is in view (lobby, office zone, the paired machine's room)",
          all(ok for _, ok in seen), seen)
    check("the office opens over the whole window", page.evaluate("""() => { const r = document.getElementById('sfo-overlay').getBoundingClientRect();
        return r.left === 0 && r.top === 0 && r.width === innerWidth && r.height === innerHeight; }"""))
    check("…what it keeps was asked for before it loaded", calls(page, "office_prefs") == 1)
    snap = page.evaluate("SFOffice.lastSnapshot()")
    by = {b["id"]: b for b in snap["bots"]}
    check("the snapshot's bots: the roster's own bots, not the external one or a plain role",
          sorted(by) == ["chief", "eng", "res"], sorted(by))
    check("…states from the tab lights; a bot with no tab is asleep",
          (by["chief"]["state"], by["eng"]["state"], by["res"]["state"]) == ("idle", "working", "asleep"),
          {k: v["state"] for k, v in by.items()})
    check("…faces as #rrggbb, the egg shape kept",
          by["eng"]["avatar"]["shape"] == "egg" and by["eng"]["avatar"]["color"].startswith("#"), by["eng"]["avatar"])
    check("…created_at in roster order", by["chief"]["created_at"] < by["eng"]["created_at"] < by["res"]["created_at"])
    t = by["eng"]["task"] or {}
    check("…each bot's open task, with the store's stale verdict and its requester",
          t.get("title") == "示範登入頁改版" and t.get("stale") is True and t.get("from") == "chief"
          and t.get("next") == "補上表單驗證", t)
    check("…interns: the AI tab that is no bot; the terminal is left out",
          [x["id"] for x in snap["temps"]] == ["s702"] and snap["temps"][0]["state"] == "decision", snap["temps"])
    check("…the paired machine's bots and tabs (already in memory, no new request)",
          snap["peers"] and snap["peers"][0]["bots"][0]["name"] == "示範建置員" and snap["peers"][0]["temps"][0]["id"] == "r1",
          snap["peers"])
    hs = {h["id"]: h for h in snap["helpers"]}
    check("…part-timers under the tab that launched them",
          hs.get("s701/a1", {}).get("parent") == "eng" and hs.get("s702/b1", {}).get("parent_kind") == "temp", hs)

    seats = fr.evaluate("__office.assignments()")
    check("the primary bot sits at the lobby desk", seats.get("chief") == "sec", seats)
    check("the others sit in the office zone, the sleeping one keeps a desk",
          str(seats.get("eng", "")).startswith("z:") and str(seats.get("res", "")).startswith("z:"), seats)
    people = {p["key"]: p for p in fr.evaluate("__office.people()")}
    check("the intern sits at the long table", str(people.get("t:s702", {}).get("seat", "")).startswith("t:"), people.get("t:s702"))
    check("the terminal is not in the office", "t:s703" not in people)
    check("the tab's label is the intern's name", people.get("t:s702", {}).get("name") == "示範臨時分頁")
    check("the paired machine has a room", any(r["id"] == "peer:pid-demo-1" for r in fr.evaluate("__office.rooms()")))
    hp = {h["key"]: h for h in fr.evaluate("__office.helpers()")}
    check("part-timers stand by their parents: working beside the bot, stuck beside the intern",
          hp.get("h:s701/a1", {}).get("parent") == "b:eng" and hp["h:s701/a1"]["state"] == "working"
          and hp.get("h:s702/b1", {}).get("parent") == "t:s702" and hp["h:s702/b1"]["state"] == "stuck", hp)
    check("…tagged 工讀生：<what it does>", hp.get("h:s701/a1", {}).get("name", "").startswith("工讀生："))
    check("the demo is off and the demo buttons are hidden",
          fr.evaluate("!__office.demoLive() && document.body.classList.contains('live-data') && getComputedStyle(document.getElementById('addOne')).display === 'none'"))
    check("the tag says experimental", fr.evaluate("document.getElementById('tag').textContent") == "實驗性")

    print(f"[{engine}] a part-timer's panel")
    fr.evaluate("__office.follow('h:s702/b1')")
    page.wait_for_timeout(1300)
    panel = fr.evaluate("document.getElementById('panel').innerText")
    check("the panel names it 工讀生, its job, model, how long it has run and who sent it",
          "工讀生" in panel and "查三份文件的差異" in panel and "demo-model" in panel and "跑了" in panel
          and "示範臨時分頁" in panel, panel[:300])
    check("…and says it has been quiet too long", "超過 10 分鐘" in panel)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-follow-helper.png"))
    fr.evaluate("__office.unfollow()")

    print(f"[{engine}] the next snapshots")
    page.evaluate("""() => { delete window.__bots.roster.res;
        window.__helpers = {s701: ['a1','a2','a3','a4','a5','a6'].map((id, i) => ({id, description: '示範：第 ' + (i + 1) + ' 件小事',
            model: 'demo-model', state: 'working', started_at: Date.now() / 1000 - 600 + i, last_at: Date.now() / 1000 - 5}))}; }""")
    page.evaluate("SFBots.refresh(true)")
    page.wait_for_timeout(300)
    page.evaluate("SFOffice.refresh()")
    page.wait_for_timeout(700)
    seats = fr.evaluate("__office.assignments()")
    check("a bot missing from the next snapshot shows as left, its seat held",
          "(已離開)" in str(seats.get("res", "")) and str(seats.get("res", "")).startswith("z:"), seats)
    hp = fr.evaluate("__office.helpers()")
    out = [h for h in hp if h["key"] == "h:s702/b1"]
    check("a finished part-timer walks out", not out or out[0]["walk"] == "out", out)
    mine = [h for h in hp if h["parent"] == "b:eng" and h["walk"] != "out"]
    check("six on one desk: three drawn, the third says +3",
          len(mine) == 6 and sum(1 for h in mine if not h["hidden"]) == 3 and max(h["more"] for h in mine) == 3,
          [(h["key"], h["hidden"], h["more"]) for h in mine])
    page.wait_for_timeout(5000)          # the newcomers walk in from the door
    fr.evaluate("__office.setArea('lobby', true)")
    page.wait_for_timeout(300)
    fr.evaluate("__office.setArea('z:Claude Code#1', true)")
    page.wait_for_timeout(900)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-left-and-plus.png"))

    print(f"[{engine}] zoning")
    check("by default staff sit by the AI they run on: a walled room per CLI, in the CLIs' order",
          fr.evaluate("__office.zones()") == ["Claude Code", "Codex"] and fr.evaluate("document.getElementById('zoning').value") == "cli",
          fr.evaluate("__office.zones()"))
    seats = fr.evaluate("__office.assignments()")
    check("…each bot in its CLI's room (the primary at the lobby desk)", seats.get("chief") == "sec"
          and seats.get("eng", "").startswith("z:Claude Code#1:") and seats.get("res", "").startswith("z:Codex#1:"), seats)
    if shoot:
        fr.evaluate("__office.setArea('all', true)")
        page.wait_for_timeout(700)
        page.screenshot(path=str(SHOTS / "office-zones-cli.png"))
    fr.select_option("#zoning", "none")
    page.wait_for_timeout(500)
    check("不分區: everyone in one room", fr.evaluate("__office.zones()") == ["辦公區"], fr.evaluate("__office.zones()"))
    fr.select_option("#zoning", "custom")
    page.wait_for_timeout(500)
    check("自訂分組 before any group: everyone in 未分組", fr.evaluate("__office.zones()") == ["未分組"], fr.evaluate("__office.zones()"))
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_selector("#pTabs:not([hidden])", timeout=5000)
    fr.click("#tabInfo")
    fr.wait_for_selector("#pGroup:not([hidden]) #gNew", timeout=5000)
    fr.fill("#gNew", "示範專案組")
    page.wait_for_timeout(3500)                       # a snapshot lands while the name is typed
    check("a group name being typed survives the 3 s refresh", fr.evaluate("document.getElementById('gNew').value") == "示範專案組")
    fr.press("#gNew", "Enter")
    page.wait_for_timeout(600)
    check("a new group from the panel: its own room, the bot moves there",
          fr.evaluate("__office.zones()") == ["示範專案組", "未分組"]
          and fr.evaluate("__office.assignments()").get("eng", "").startswith("z:示範專案組#1:"), fr.evaluate("__office.zones()"))
    fr.evaluate("__office.setGroup('res', '示範專案組')")     # res left earlier (seat held): it cannot be followed
    page.wait_for_timeout(600)
    check("a bot that left keeps its held seat, in its group's room; the empty 未分組 room goes",
          fr.evaluate("__office.zones()") == ["示範專案組"] and fr.evaluate("__office.assignments()").get("res", "").startswith("z:示範專案組#1:"),
          (fr.evaluate("__office.zones()"), fr.evaluate("__office.assignments()")))
    fr.click("#pGroup button[data-g='']")
    page.wait_for_timeout(600)
    check("未分組 takes it out again", fr.evaluate("__office.zones()") == ["示範專案組", "未分組"]
          and fr.evaluate("__office.groups()") == {"res": "示範專案組"}, (fr.evaluate("__office.zones()"), fr.evaluate("__office.groups()")))
    fr.click("#pGroup button[data-g='示範專案組']")
    page.wait_for_timeout(600)
    check("…and its chip puts it back", fr.evaluate("__office.zones()") == ["示範專案組"]
          and fr.evaluate("document.querySelector(\"#pGroup button[data-g='示範專案組']\").classList.contains('on')"), fr.evaluate("__office.zones()"))
    if shoot:
        page.screenshot(path=str(SHOTS / "office-zones-custom.png"))
    fr.select_option("#zoning", "cli")
    page.wait_for_timeout(600)
    check("back to 依 AI: the rooms per CLI again, the groups kept for later",
          fr.evaluate("__office.zones()") == ["Claude Code", "Codex"]
          and fr.evaluate("__office.groups()") == {"eng": "示範專案組", "res": "示範專案組"},
          (fr.evaluate("__office.zones()"), fr.evaluate("__office.groups()")))
    check("…and the panel says groups only count in 自訂分組", "改成自訂分組" in fr.evaluate("document.getElementById('pGroup').innerText"))
    fr.click("#tabChat")
    fr.evaluate("__office.unfollow()")
    page.wait_for_timeout(600)
    saved = page.evaluate("window.__saved")
    check("the zoning and the groups are saved through office_prefs_save",
          saved and saved[-1].get("zoning") == "cli" and saved[-1].get("groups") == {"eng": "示範專案組", "res": "示範專案組"}, saved[-1:] if saved else saved)

    print(f"[{engine}] what it keeps")
    fr.click("#mode button[data-m='flat']")
    page.wait_for_timeout(800)
    saved = page.evaluate("window.__saved")
    check("the view mode is saved through office_prefs_save, not localStorage",
          bool(saved) and saved[-1].get("mode") == "flat"
          and fr.evaluate("localStorage.getItem('sbOffice.v2.mode')") is None, saved[-1:] if saved else saved)

    print(f"[{engine}] closing")
    fr.select_option("#area", "lobby")
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    check("Esc right after picking an area (focus on the picker) closes the office", page.query_selector("#sfo-overlay") is None)
    page.click("#btn-office")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && Object.keys(f.contentWindow.__office.assignments()).length >= 2; } catch (e) { return false; } }""",
                           timeout=15000)       # a fresh office: the two bots still in the roster
    fr = office(page)
    box = page.locator("#sfo-overlay iframe").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 30, box["y"] + 110)     # into the office, on its sky
    page.wait_for_timeout(200)
    if fr.evaluate("__office.following()"):
        fr.evaluate("__office.unfollow()")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    check("Esc pressed inside the office closes it", page.query_selector("#sfo-overlay") is None)
    check("…the iframe is gone", page.query_selector("iframe") is None and office(page) is None)
    n = calls(page, "office_helpers")
    page.wait_for_timeout(3600)
    check("…and the refresh stopped", calls(page, "office_helpers") == n, (n, calls(page, "office_helpers")))

    print(f"[{engine}] the window in the background")
    page.click("#btn-office")
    page.wait_for_selector("#sfo-overlay iframe", timeout=5000)
    page.wait_for_timeout(1500)
    page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})")
    n = calls(page, "office_helpers")
    page.wait_for_timeout(3600)
    check("no refresh while the window is hidden", calls(page, "office_helpers") == n, (n, calls(page, "office_helpers")))
    page.evaluate("delete document.hidden")
    page.focus("#sfo-overlay")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc with the focus on ShellBot's side closes it too", page.query_selector("#sfo-overlay") is None)
    saved_now = page.evaluate("window.__saved")
    check("no console error from the page or the office", not errors, errors[:5])
    page.close()
    return saved_now[-1] if saved_now else {}


def reopen(browser, base, prefs, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    cfg_on = dict(CFG, settings=dict(CFG["settings"], experimental_office=True))
    page.add_init_script(stub(prefs).replace(json.dumps(CFG, ensure_ascii=False), json.dumps(cfg_on, ensure_ascii=False), 1))
    page.goto(base + "/index.html")
    page.wait_for_selector("#btn-office", timeout=20000)
    page.click("#btn-office")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && Object.keys(f.contentWindow.__office.assignments()).length >= 3; } catch (e) { return false; } }""",
                           timeout=15000)
    fr = office(page)
    page.wait_for_timeout(500)
    check("after a restart the office comes back in the mode it was left in",
          fr.evaluate("document.querySelector('#mode button[data-m=\"flat\"]').getAttribute('aria-pressed')") == "true")
    seats = fr.evaluate("__office.assignments()")
    check("…a seat moved by hand is kept", seats.get("eng") == "z:Claude Code#1:7", seats)
    check("…the zoning and the groups too", fr.evaluate("__office.zoning()") == "cli"
          and fr.evaluate("__office.groups()") == {"eng": "示範專案組", "res": "示範專案組"}, fr.evaluate("__office.groups()"))
    check("…and the accessories picked", fr.evaluate("__office.items('b:eng')") == ["globe", "duck"],
          fr.evaluate("__office.items('b:eng')"))
    if shoot:
        page.screenshot(path=str(SHOTS / "office-flat.png"))
    check("no console error after the restart", not errors, errors[:5])
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
                prefs = run(browser, base, engine)
                reopen(browser, base, dict(prefs, seats={"eng": {"zone": "Claude Code#1", "slot": 7}}, items={"eng": ["globe", "duck"]}), engine)
            finally:
                browser.close()
    finally:
        srv.shutdown()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
