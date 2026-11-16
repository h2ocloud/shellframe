#!/usr/bin/env python3
"""Settings → Connect as a catalog of apps (web/connect.js, web/connect.css): web/index.html in a headless
browser with a stand-in Python side whose connect_list carries the catalog (each service's category,
recommended, local_app, whether it can be connected here and why not) for a dozen made-up services.

What is checked is the page's side:
  1. the frame: the title, "N connected ›" counted from the statuses, the search box, one bubble per category
     (fullest first, past four under "More ▾"), a bubble for this computer's apps; Settings is wider while
     the tab shows, two columns, and back to its own width on another tab
  2. the home list: "Needs you" first (red, Reconnect), "Recommended for you" with "See all", "This Mac",
     the rest; a connected service's button says so
  3. search: by name in either language, by a line or a category's name; nothing found says so; Esc clears it
  4. the bubbles: a category, this computer's apps, one from "More ▾"; "See all" lists every service,
     one made for another kind of computer included and marked
  5. "N connected ›" lists the connected ones and needs-reconnecting ones, and goes back
  6. a service that cannot be connected yet: "Not yet" opens its reason in the row, never the wizard; opened
     anyway (a bot's tag), the card says why and shows no step
  7. "Connect" opens the same wizard (connect_status, the route, connect_start); "Reconnect" starts again
  8. English; a narrow window has one column; no page errors
WebKit (the Mac app's engine), headless. Made-up services only. Screenshots: qa-shots/connect-catalog-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_connect_catalog_ui.py
"""
import json
import pathlib
import sys

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
    print("SKIP  tests_connect_catalog_ui.py（沒裝 playwright）")
    sys.exit(0)


def T(en, zh):
    return {"en": en, "zh": zh}


def cat(cid, en, zh):
    return {"id": cid, "name": T(en, zh)}


PROD, COMM, DESIGN = cat("productivity", "Productivity", "生產力"), cat("communication", "Communication", "通訊"), cat("design", "Design", "設計")
MEDIA, FORMS, SALES = cat("media", "Media", "媒體"), cat("forms", "Forms", "表單"), cat("sales", "Sales", "銷售")

# id, name, icon, summary, category, recommended, local_app, state, available, why
SERVICES = [
    ("demo_files", T("Demo Files", "示範檔案櫃"), "folder", T("Let your bots find demo files", "讓 bot 找示範檔案"), PROD, True, False, "not_started", True, None),
    ("demo_plan", T("Demo Planner", "示範行程表"), "calendar", T("Let your bots see demo plans", "讓 bot 看示範行程"), PROD, True, False, "not_started", True, None),
    ("demo_pages", T("Demo Pages", "示範筆記本"), "doc", T("Search and read demo pages", "搜尋、讀示範頁面"), PROD, True, False, "not_started", True, None),
    ("demo_chat", T("Demo Chat", "示範聊天室"), "chat", T("Read a demo chat", "讀示範聊天室"), COMM, True, False, "not_started", True, None),
    ("demo_mail", T("Demo Mail", "示範信箱"), "mail", T("Read demo mail", "讀示範信件"), COMM, True, False, "connected", True, None),
    ("demo_board", T("Demo Board", "示範看板"), "plug", T("Read a demo board", "讀示範看板"), PROD, True, False, "needs_reconnect", True, None),
    ("demo_sketch", T("Demo Sketch", "示範設計稿"), "plug", T("Look at demo designs", "看示範設計稿"), DESIGN, False, False, "not_started", True, None),
    ("demo_photos", T("Demo Photos", "示範相簿"), "plug", T("Look at demo photos", "看示範相片"), MEDIA, False, False, "not_started", True, None),
    ("demo_forms", T("Demo Forms", "示範表單"), "plug", T("Read demo forms", "讀示範表單"), FORMS, False, False, "not_started", True, None),
    ("demo_deals", T("Demo Deals", "示範商機"), "plug", T("Read demo deals", "讀示範商機"), SALES, False, False, "not_started", True, None),
    ("demo_texts", T("Demo Texts", "示範訊息"), "message", T("Read and send demo texts", "讀、傳示範訊息"), COMM, False, True, "not_started", False, "not_available"),
    ("demo_memo", T("Demo Memo", "示範備忘"), "note", T("Write demo memos", "寫示範備忘"), PROD, False, True, "not_started", False, "not_available"),
    ("demo_todo", T("Demo To-do", "示範待辦"), "checklist", T("See demo to-dos", "看示範待辦"), PROD, False, True, "not_started", True, None),
    ("demo_winapp", T("Demo PC App", "示範視窗工具"), "plug", T("A demo app for another computer", "另一種電腦的示範工具"), PROD, False, False, "not_started", False, "not_on_platform"),
]
WHY = {
    "not_available": (T("Not yet: there is no demo server to recommend.", "還不能接：還沒有可以推薦的示範伺服器。"),
                      T("It will come once there is one to stand behind.", "等有信得過的就會加上。")),
    "not_on_platform": (T("This service cannot be connected on this kind of computer.", "這種電腦上接不了這個服務。"),
                        T("Connect it from a computer it is made for.", "請在它支援的電腦上接。")),
}


def status(row):
    sid, name, icon, _s, _c, _r, _l, state, avail, why = row
    problem = None
    if state == "needs_reconnect":
        problem = {"code": "expired", "text": T("Its sign-in ran out.", "它的登入過期了。"), "next": T("Connect it again.", "重新接上就好。"), "step": ""}
    elif not avail:
        problem = {"code": why, "text": WHY[why][0], "next": WHY[why][1], "step": ""}
    return {"service": sid, "name": name, "icon": icon, "kind": "claude_connector" if state != "not_started" else "", "state": state,
            "route": "app" if state != "not_started" else "", "step": None, "clis": {}, "scopes": [],
            "connected_at": 1794190841 if state in ("connected", "needs_reconnect") else 0, "checked_at": 0, "problem": problem,
            "attention": state in ("needs_reconnect", "failed"), "available": avail}


def catalog(row):
    sid, _n, _i, summary, category, rec, local, _st, avail, why = row
    return {"id": sid, "summary": summary, "category": category, "recommended": rec, "local_app": local,
            "platforms": ["windows"] if why == "not_on_platform" else ["mac"] if local else ["mac", "windows", "linux"],
            "available": avail, "why": why}


def descriptor(row):
    sid, name, icon, summary, _c, _r, _l, _st, avail, why = row
    d = {"schema": 1, "id": sid, "name": name, "icon": icon, "summary": summary, "scopes": [{"id": "read", "label": T("Read", "讀取"), "required": True}],
         "faq": [], "problems": {}}
    if why == "not_available":
        d.update(routes=[], unavailable={"text": WHY[why][0], "next": WHY[why][1]})
        return d
    d["routes"] = [{"id": "app", "kind": "claude_connector", "name": T("Through the demo account", "透過示範帳號"), "blurb": T("A demo way.", "示範的接法。"),
                    "recommended": True, "minutes": 2, "needs": [], "scopes": ["read"], "clis": {"alpha": {"via": "connector"}},
                    "steps": [{"id": "s1", "title": T("Open the demo page", "打開示範頁面"), "how": [T("Open it.", "打開它。")]},
                              {"id": "s2", "kind": "test", "title": T("Test it", "測試"), "intro": T("A demo test.", "示範測試。")}],
                    "test": {"via": T("the demo", "示範"), "verdicts": {}}}]
    return d


STUB = r"""
window.__calls = []; window.__cfg = %(cfg)s; window.__rows = %(rows)s; window.__cat = %(cat)s; window.__desc = %(desc)s;
window.__state = {};
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
const st = id => Object.assign({}, window.__rows.find(r => r.service === id), window.__state[id] || {});
const view = id => {
  const d = window.__desc[id], s = st(id);
  if (!d.routes.length || s.state === 'not_started') return {route: null, routes: d.routes.map(r => ({id: r.id, name: r.name, recommended: true})), steps: [], step: null};
  const r = d.routes[0];
  return {route: {id: r.id, kind: r.kind, name: r.name}, routes: [{id: r.id, name: r.name, recommended: true}],
          steps: r.steps.map((x, i) => ({id: x.id, title: x.title, state: i ? 'todo' : 'current'})), step: Object.assign({}, r.steps[0], {state: 'current', result: null, message: null})};
};
const ok = (id, extra) => Object.assign({success: true, status: st(id), view: view(id)}, extra || {});
window.__api = {
  connect_list: () => ({success: true, services: window.__rows.map(r => st(r.service)), clis: [{id: 'alpha', name: {en: 'Alpha CLI', zh: 'Alpha CLI'}}],
                        catalog: window.__cat, platform: 'mac'}),
  connect_describe: (id) => ({success: true, descriptor: window.__desc[id]}),
  connect_status: (id) => ok(id),
  connect_start: (id, a) => {
    a = JSON.parse(a || '{}');
    if (!window.__desc[id].routes.length) return {success: false, code: 'not_available', message: {text: window.__desc[id].unavailable.text, next: window.__desc[id].unavailable.next}, status: st(id)};
    window.__state[id] = {state: 'waiting_user', route: 'app', attention: false, problem: null};
    return ok(id);
  },
  connect_step: (id) => ok(id),
  connect_test: (id) => ok(id, {result: {state: 'running', handle: 'h1', phase: 'start'}}),
  connect_signal: (reason) => {
    const id = String(reason).split(':')[1];
    const d = window.__desc[id];
    if (d && !d.routes.length) return {success: false, code: 'not_available', message: {text: d.unavailable.text, next: d.unavailable.next}, status: st(id)};
    return ok(id, {service: id});
  },
};
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    if (prop in window.__api) return R(window.__api[prop](...args));
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R([{sid: 's501', cmd: 'claude', alive: true, label: '示範分頁'}]);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      default: return R('{}');
    }
  };
}})};
"""


def stub(lang, data=None):
    """The stand-in page side; `data` = (statuses, catalog, descriptors) instead of the made-up services."""
    cfg = {"settings": {"language": lang, "sidebar_open": True}, "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    rows, cat_, desc = data or ([status(r) for r in SERVICES], [catalog(r) for r in SERVICES], {r[0]: descriptor(r) for r in SERVICES})
    return STUB % {"cfg": json.dumps(cfg, ensure_ascii=False), "rows": json.dumps(rows, ensure_ascii=False),
                   "cat": json.dumps(cat_, ensure_ascii=False), "desc": json.dumps(desc, ensure_ascii=False)}


def shipped():
    """What the Python side lists for the services that ship, on a Mac with nothing connected: the real
    registry and engine (stand-in programs, a state file of its own). No screenshot is taken of it."""
    import os
    import tempfile
    sys.path.insert(0, str(HERE))
    os.environ.setdefault("SHELLFRAME_TESTS", "1")
    import _connectors_fake as fake
    from connectors import clis, engine, registry
    reg = registry.Registry()
    eng = engine.Engine(registry=reg, path=os.path.join(tempfile.mkdtemp(prefix="catalog-ui-"), "connectors.json"),
                        clis=clis.Clis(runner=fake.FakeRunner(), finder=fake.finder()), backend=fake.backend(), platform="mac")
    return eng.list(), reg.catalog("mac"), {i: reg.get(i) for i in reg.ids()}, reg


def calls(page, name):
    return page.evaluate("(n) => window.__calls.filter(c => c[0] === n).map(c => c[1])", name)


def open_page(browser, lang="zh-TW", width=1100, height=820, data=None):
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(lang, data))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("window.SFConnect && SFConnect.list().loaded", timeout=15000)
    page.click("#btn-settings")
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector("#settings-panel-connect .cg-row", timeout=5000)
    page.wait_for_timeout(300)
    return page, errors


def shot(page, name, focus=None):
    """The Settings window as it is on screen; `focus` scrolls that part of the catalog into its view first."""
    SHOTS.mkdir(exist_ok=True)
    if focus:
        page.locator(focus).first.evaluate("el => el.scrollIntoView({block: 'center'})")
        page.wait_for_timeout(100)
    page.locator("#modal-settings .modal").screenshot(path=str(SHOTS / name))
    page.evaluate("document.querySelector('#modal-settings .modal').scrollTop = 0")


def sec(page, key):
    return page.locator(f'#settings-panel-connect .cg-sec[data-sec="{key}"]')


def names(loc):
    return [x.strip() for x in loc.locator(".cn-main b").evaluate_all("els => els.map(e => e.firstChild ? e.firstChild.textContent : '')")]


def pills(page):
    return page.locator("#settings-panel-connect .cg-pill").evaluate_all("els => els.map(e => e.textContent.trim())")


def run(browser):
    print("1. the frame")
    page, errors = open_page(browser)
    panel = page.locator("#settings-panel-connect")
    check("the title and the count of what is connected (connected and needs-reconnecting: 2)",
          panel.locator(".cg-head h3").inner_text() == "連結應用程式" and panel.locator(".cg-count").inner_text() == "已連結 2 個 ›",
          panel.locator(".cg-head").inner_text())
    check("a search box that says what it searches", panel.locator(".cg-search input").get_attribute("placeholder") == "搜尋應用程式")
    p = pills(page)
    check("bubbles: All, this Mac's apps, then the categories from the descriptions, fullest first, four of them, then More ▾",
          p[:3] == ["全部", "Mac", "生產力"] and p[3] == "通訊" and len(p) == 7 and p[-1] == "更多 ▾", p)
    w = page.evaluate("document.querySelector('#modal-settings .modal').getBoundingClientRect().width")
    cols = page.evaluate("getComputedStyle(document.querySelector('#settings-panel-connect .cg-grid')).gridTemplateColumns.split(' ').length")
    check("Settings is wider while the catalog shows (two columns of rows)", 700 <= w <= 720 and cols == 2, (w, cols))
    page.click('#settings-tabs-bar [data-tab="general"]')
    page.wait_for_timeout(100)
    w2 = page.evaluate("document.querySelector('#modal-settings .modal').getBoundingClientRect().width")
    check("…and back to its own width on another tab", abs(w2 - 560) < 2, w2)
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector("#settings-panel-connect .cg-row")
    page.wait_for_timeout(200)

    print("2. the home list")
    keys = panel.locator(".cg-sec").evaluate_all("els => els.map(e => e.dataset.sec)")
    check("sections: Needs you, Recommended for you, This Mac, More services", keys == ["attn", "rec", "local", "rest"], keys)
    attn = sec(page, "attn")
    check("Needs you: the service that needs reconnecting, marked, with its reason and a Reconnect button",
          names(attn) == ["示範看板"] and "它的登入過期了" in attn.inner_text() and "重新接上就好" in attn.inner_text()
          and attn.locator(".cg-row.attn button.primary").inner_text() == "重新接上", attn.inner_text())
    rec = sec(page, "rec")
    check("Recommended for you: the recommended ones not shown above, with See all",
          names(rec) == ["示範檔案櫃", "示範行程表", "示範筆記本", "示範聊天室", "示範信箱"] and rec.locator('[data-cg="all"]').inner_text() == "查看全部", names(rec))
    check("a row is the icon, the name, its one line and a Connect button",
          rec.locator(".cg-row").first.locator(".cn-ico svg").count() == 1 and "讓 bot 找示範檔案" in rec.locator(".cg-row").first.inner_text()
          and rec.locator(".cg-row").first.locator("button").inner_text() == "連結")
    check("a connected service's button says Connected", rec.locator('.cg-row[data-service="demo_mail"] button').inner_text() == "✓ 已連結")
    local = sec(page, "local")
    check("This Mac: the computer's own apps, the ones that cannot be connected yet marked Not yet",
          local.locator("h4").inner_text() == "這台 Mac" and names(local) == ["示範訊息", "示範備忘", "示範待辦"]
          and local.locator(".cg-off").count() == 2 and local.locator('.cg-row[data-service="demo_texts"] button').inner_text() == "還不能接"
          and local.locator('.cg-row[data-service="demo_todo"] button').inner_text() == "連結", local.inner_text())
    check("More services: the rest; one made for another kind of computer is not on the home list",
          names(sec(page, "rest")) == ["示範設計稿", "示範相簿", "示範表單", "示範商機"], names(sec(page, "rest")))
    shot(page, "connect-catalog-home-zh.png")

    print("3. search")
    box = panel.locator(".cg-search input")
    box.fill("筆記")
    check("by a Chinese name", names(sec(page, "search")) == ["示範筆記本"], names(sec(page, "search")))
    check("…the box keeps the focus while the list redraws", page.evaluate("document.activeElement.closest('.cg-search') !== null"))
    box.fill("chat")
    check("by the English name on a Chinese page", names(sec(page, "search")) == ["示範聊天室"], names(sec(page, "search")))
    box.fill("通訊")
    check("by a category's name", set(names(sec(page, "search"))) == {"示範聊天室", "示範信箱", "示範訊息"}, names(sec(page, "search")))
    check("…and no bubble is on while searching", panel.locator(".cg-pill.on").count() == 0)
    shot(page, "connect-catalog-search-zh.png")
    box.fill("沒有這種東西")
    check("nothing found says so", "找不到符合「沒有這種東西」的服務" in sec(page, "search").inner_text())
    box.press("Escape")
    check("Esc clears the search, back home, Settings still open",
          box.input_value() == "" and sec(page, "rec").count() == 1 and page.locator("#modal-settings.open").count() == 1)

    print("4. the bubbles, See all")
    panel.locator('.cg-pill[data-id="communication"]').click()
    check("a category bubble lists its services", names(sec(page, "cat")) == ["示範聊天室", "示範信箱", "示範訊息"]
          and panel.locator(".cg-pill.on").inner_text() == "通訊", names(sec(page, "cat")))
    shot(page, "connect-catalog-category-zh.png")
    panel.locator('.cg-pill[data-id="local"]').click()
    check("the Mac bubble lists this computer's apps", names(sec(page, "local")) == ["示範訊息", "示範備忘", "示範待辦"])
    panel.locator('[data-cg="more"]').click()
    extra = panel.locator(".cg-more-list .cg-pill").evaluate_all("els => els.map(e => e.textContent.trim())")
    check("More ▾ shows the rest of the categories (the ties go by name: Media, Sales)", extra == ["媒體", "銷售"], extra)
    panel.locator(".cg-more-list .cg-pill").first.click()
    one = names(sec(page, "cat"))
    check("…one of them lists its service, and More is marked on", len(one) == 1 and panel.locator(".cg-more.on").count() == 1, one)
    panel.locator('.cg-pill[data-id="all"]').click()
    panel.locator('[data-cg="all"]').click()
    every = names(sec(page, "all"))
    check("See all: every service, by name", len(every) == len(SERVICES) and sec(page, "all").locator('[data-cg="home"]').count() == 1, every)
    check("…one made for another kind of computer is there, marked", sec(page, "all").locator('.cg-row[data-service="demo_winapp"] button').inner_text() == "這台不能用")
    panel.locator('[data-cg="home"]').click()
    check("Back goes home", sec(page, "rec").count() == 1)

    print("5. N connected ›")
    panel.locator(".cg-count").click()
    check("lists the connected and the needs-reconnecting ones", sorted(names(sec(page, "connected"))) == sorted(["示範信箱", "示範看板"]), names(sec(page, "connected")))
    shot(page, "connect-catalog-connected-zh.png")
    panel.locator(".cg-count").click()
    check("pressed again, it goes home", sec(page, "rec").count() == 1)

    print("6. a service that cannot be connected yet")
    page.evaluate("window.__calls.length = 0")
    local = sec(page, "local")
    local.locator('.cg-row[data-service="demo_texts"] button').click()
    check("Not yet opens its reason in the row, and what comes next",
          "還沒有可以推薦的示範伺服器" in local.locator('.cg-row[data-service="demo_texts"]').inner_text()
          and "等有信得過的就會加上" in local.locator('.cg-row[data-service="demo_texts"]').inner_text())
    check("…and no wizard, no call to start it", not page.locator("#modal-connect.open").count()
          and not calls(page, "connect_start") and not calls(page, "connect_status"))
    shot(page, "connect-catalog-mac-why-zh.png", '#settings-panel-connect .cg-row[data-service="demo_texts"]')
    page.evaluate("SFConnect.openFromChat('demo_texts', {tag: 'connect:demo_texts'})")
    page.wait_for_selector("#modal-connect.open #cn-card .cn-msg.bad", timeout=5000)
    check("opened anyway (a bot's tag): the card says why, and shows no way and no step",
          "還沒有可以推薦的示範伺服器" in page.locator("#cn-card").inner_text() and page.locator("#cn-card .cn-route").count() == 0
          and page.locator("#cn-card .cn-how").count() == 0)
    page.keyboard.press("Escape")

    print("7. Connect and Reconnect open the same wizard")
    page.evaluate("window.__calls.length = 0")
    sec(page, "rec").locator('.cg-row[data-service="demo_pages"] button').click()
    page.wait_for_selector("#cn-card .cn-route", timeout=5000)
    check("Connect opens the wizard on its way to connect (connect_status of that service)",
          calls(page, "connect_status")[0][0] == "demo_pages" and "示範筆記本" in page.locator("#cn-card .cn-head").inner_text())
    page.click('#cn-card [data-act="route-go"]')
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("…and Next starts it (connect_start with the route): step 1", [c for c in calls(page, "connect_start") if c[0] == "demo_pages"] != []
          and "打開示範頁面" in page.locator("#cn-card .cn-title").inner_text())
    shot(page, "connect-catalog-wizard-zh.png")
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('#modal-connect.open')")
    page.wait_for_timeout(300)
    page.evaluate("window.__calls.length = 0")
    sec(page, "attn").locator("button.primary").click()
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("Reconnect starts again (connect_start with reconnect) at step 1",
          any(json.loads(c[1]).get("reconnect") for c in calls(page, "connect_start") if c[0] == "demo_board"), calls(page, "connect_start"))
    page.keyboard.press("Escape")
    check("no page errors", not errors, errors)
    page.close()

    print("8. English, a narrow window")
    page, errors = open_page(browser, "en")
    panel = page.locator("#settings-panel-connect")
    check("the frame in English", panel.locator(".cg-head h3").inner_text() == "Connect apps" and panel.locator(".cg-count").inner_text() == "2 connected ›"
          and panel.locator(".cg-search input").get_attribute("placeholder") == "Search apps")
    check("…sections and buttons in English", sec(page, "rec").locator("h4").inner_text() == "Recommended for you" and sec(page, "local").locator("h4").inner_text() == "This Mac"
          and sec(page, "local").locator('.cg-row[data-service="demo_texts"] button').inner_text() == "Not yet"
          and sec(page, "rec").locator('.cg-row[data-service="demo_files"] button').inner_text() == "Connect")
    shot(page, "connect-catalog-home-en.png")
    check("no page errors", not errors, errors)
    page.close()
    page, errors = open_page(browser, "zh-TW", 640, 760)
    cols = page.evaluate("getComputedStyle(document.querySelector('#settings-panel-connect .cg-grid')).gridTemplateColumns.split(' ').length")
    fits = page.evaluate("(() => { const m = document.querySelector('#modal-settings .modal').getBoundingClientRect(); return m.left >= 0 && m.right <= innerWidth; })()")
    check("a narrow window: one column, and Settings stays inside it", cols == 1 and fits, (cols, fits))
    shot(page, "connect-catalog-narrow-zh.png")
    check("no page errors", not errors, errors)
    page.close()

    print("9. the services that ship, as the Python side lists them (checked by id; no screenshot)")
    rows, cat_, desc, reg = shipped()
    page, errors = open_page(browser, data=(rows, cat_, desc))
    ids = lambda key: sec(page, key).locator(".cg-row").evaluate_all("els => els.map(e => e.dataset.service)")
    recommended = sorted(i for i in reg.ids() if desc[i].get("recommended") and not desc[i].get("local_app"))
    local = sorted(i for i in reg.ids() if desc[i].get("local_app"))
    check("Recommended for you: every recommended service, each with a Connect button",
          sorted(ids("rec")) == recommended and len(recommended) >= 5
          and all(b == "連結" for b in sec(page, "rec").locator("button.cn-btn").all_inner_texts()), (ids("rec"), recommended))
    check("This Mac: the Mac apps, each marked Not yet (nothing to connect them with yet)",
          sorted(ids("local")) == local and len(local) == 4 and sec(page, "local").locator(".cg-off").count() == 4
          and set(sec(page, "local").locator("button.cn-btn").all_inner_texts()) == {"還不能接"}, ids("local"))
    check("the bubbles are the descriptions' categories", pills(page) == ["全部", "Mac", "生產力", "通訊"], pills(page))
    check("nothing connected: 0", page.locator("#settings-panel-connect .cg-count").inner_text() == "已連結 0 個 ›")
    sec(page, "local").locator(".cg-row button").first.click()
    check("a Mac app's reason is its description's words", pick_zh(desc[local[0]]["unavailable"]["text"]) in sec(page, "local").inner_text())
    check("no page errors", not errors, errors)
    page.close()


def pick_zh(t):
    return t["zh"]


with sync_playwright() as p:
    browser = p.webkit.launch(headless=True)
    try:
        run(browser)
    finally:
        browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
