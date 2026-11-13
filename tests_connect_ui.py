#!/usr/bin/env python3
"""Settings → Connect, the wizard card, the sidebar card and the chat card (web/connect.js, web/connect.css):
web/index.html in a browser with a stand-in Python side that implements the connector js_api for one
made-up service ("demo": two ways to connect, a test at the end).

The stand-in follows the interface the connector core fixed (connect_list / connect_describe /
connect_status / connect_start / connect_step / connect_test / connect_disconnect / connect_signal and the
status and `view` shapes), so what is checked here is the page's side of it:
  1. Settings has a Connect tab; a service not started shows as a row with a "Connect" button, no mark
  2. the card opens on the choice of a way (the recommended one picked), at a moderate size: at most 640px
     wide, text 12-13px, room around it, and it is the same size on every step
  3. a step: its words and link, a tick box and a button that does one thing, the check that runs on
     entering, "I did it" that is refused with message.text and message.next, then goes on
  4. "I'm stuck": the usual problems, and "ask a bot" sends route, step and the ticked problem to connect_step(stuck)
  5. the test: running, then passed (the status says connected); a failed one points at a step and goes back to it
  6. a service that needs reconnecting: the row is marked, the tab says so, a small card shows in the sidebar
     (connect_list only, no refresh) and opens the wizard at its first step; the page asks connect_status(refresh)
     for what is connected while the tab is open
  7. the chat view's [[SF:RED:connect:demo]] question is a card; its button opens the wizard through
     connect_signal and answers the bot with option 1; any other question stays a question
  8. nothing in the page's own words names a service; Esc closes the card and not Settings under it
WebKit only (the Mac app's engine). Demo data only. Screenshots go to qa-shots/connect-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_connect_ui.py
"""
import json
import pathlib
import re
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
    print("SKIP  tests_connect_ui.py（沒裝 playwright）")
    sys.exit(0)


def T(en, zh):
    return {"en": en, "zh": zh}


STEP_ADD = {
    "id": "add", "title": T("Add it to your AI tools", "把它加進你的 AI 工具"),
    "how": [T("Open the page and sign in.", "打開頁面並登入。"), T("Come back here and press the button.", "回到這裡按下面的按鈕。")],
    "url": "https://example.com/demo-setup",
    "see": T("The tool list shows Demo service with a green dot.", "工具清單裡會看到「示範服務」，旁邊是綠點。"),
    "note": T("Nothing is installed outside your own settings.", "只會改你自己的設定，不會裝別的東西。"),
    "check": {"id": "present", "on": "enter", "gate": True, "pass": ["ok"], "states": {
        "ok": {"tone": "ok", "text": T("Found in your tools", "在你的工具裡找到了"), "hint": T("", "")},
        "missing": {"tone": "warn", "text": T("Not in your tools yet", "你的工具裡還沒有"), "hint": T("Press the button below.", "按下面的按鈕加進去。")}}},
    "actions": [{"id": "add", "label": T("Add it for me", "幫我加進去"), "builtin": "cli_add", "after": "check",
                 "results": {"added": {"tone": "ok", "text": T("Added", "加好了")}}}],
    "options": [{"id": "all", "label": T("Add it to every AI that can use it", "每個能用的 AI 都加"), "default": True},
                {"id": "ask", "label": T("Ask me before each one", "每一個都先問我"), "default": False}],
    "stuck": [{"id": "nothing", "q": T("The tool list is empty", "工具清單是空的"), "a": T("Close the AI tool and open it again, then press the button once more.", "把 AI 工具關掉再開，然後再按一次按鈕。"),
               "links": [{"label": T("Open the page", "打開頁面"), "url": "https://example.com/demo-help"}]},
              {"id": "twice", "q": T("It shows up twice", "出現兩個一樣的"), "a": T("Remove the older one in the tool's own settings.", "到工具自己的設定把舊的刪掉。"), "links": []}],
}
STEP_SIGN = {"id": "sign", "title": T("Sign in once", "登入一次"), "how": [T("Sign in with the account you use for it.", "用你平常用的帳號登入。")],
             "see": T("The page says you are signed in.", "頁面上會寫已經登入。"),
             "check": {"id": "signed", "on": "done", "gate": True, "pass": ["ok"], "states": {
                 "ok": {"tone": "ok", "text": T("Signed in", "已登入"), "hint": T("", "")},
                 "no": {"tone": "warn", "text": T("Not signed in", "還沒登入"), "hint": T("", "")}}}}
STEP_TEST = {"id": "test", "kind": "test", "title": T("Test the connection", "測試連線"),
             "intro": T("ShellBot asks a temporary tab to use it once.", "ShellBot 會請一個暫時的分頁實際用一次。")}
STEP_KEY = {"id": "key", "title": T("Paste the key", "貼上金鑰"), "how": [T("Paste it into the box in the next window.", "貼進下一個視窗的欄位。")]}
DESC = {
    "schema": 1, "id": "demo", "name": T("Demo service", "示範服務"), "icon": "mail", "summary": T("Lets your bots read and send demo notes.", "讓 bot 讀取、寄出示範備忘。"),
    "scopes": [{"id": "read", "label": T("Read notes", "讀取備忘"), "required": True}, {"id": "send", "label": T("Send notes", "寄出備忘"), "required": False}],
    "routes": [
        {"id": "app", "kind": "local_mcp", "name": T("Through ShellBot's helper", "透過 ShellBot 的小幫手"), "blurb": T("ShellBot adds a small helper to your AI tools.", "ShellBot 幫你把一個小幫手加進 AI 工具。"),
         "recommended": True, "minutes": 3, "needs": [T("An account", "一個帳號")], "scopes": ["read"],
         "clis": {"alpha": {"via": "mcp"}, "beta": {"via": "mcp"}, "gamma": {"via": "none"}},
         "steps": [STEP_ADD, STEP_SIGN, STEP_TEST],
         "test": {"via": T("through the helper", "透過小幫手"), "verdicts": {
             "no_tools": {"text": T("The test tab found no tools", "測試分頁找不到工具"), "next": T("Add it again in step 1.", "回到第 1 步重新加一次。")}}}},
        {"id": "key", "kind": "api_key", "name": T("With a key", "用金鑰"), "blurb": T("You paste a key; it is kept in the system keychain.", "你貼一把金鑰，存在系統鑰匙圈。"),
         "recommended": False, "minutes": 6, "needs": [], "scopes": ["read"], "clis": {"alpha": {"via": "mcp"}}, "steps": [STEP_KEY, STEP_TEST],
         "test": {"via": T("with the key", "用金鑰"), "verdicts": {}}}],
    "faq": [], "problems": {},
}
CLIS = [{"id": "alpha", "name": T("Alpha CLI", "Alpha CLI")}, {"id": "beta", "name": T("Beta CLI", "Beta CLI")}, {"id": "gamma", "name": T("Gamma CLI", "Gamma CLI")}]

STUB = r"""
window.__calls = []; window.__tabs = []; window.__decision = null; window.__answers = [];
window.__cfg = %(cfg)s; window.__sessions = %(sessions)s; window.__desc = %(desc)s; window.__clis = %(clis)s;
window.__svc = {state: 'not_started', route: '', idx: 0, results: {}, message: null, connected_at: 0, problem: null, testFail: false, polls: 0};
const T = (en, zh) => ({en, zh});
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
const route = () => window.__desc.routes.find(r => r.id === window.__svc.route) || null;
const status = () => {
  const s = window.__svc, r = route();
  const connected = s.state === 'connected' || s.state === 'needs_reconnect';
  return {service: 'demo', name: window.__desc.name, icon: 'mail', kind: r ? r.kind : '', state: s.state, route: s.route,
    step: r && (s.state === 'waiting_user' || s.state === 'verifying') ? {id: r.steps[s.idx].id, index: s.idx + 1, total: r.steps.length} : null,
    clis: connected ? {alpha: 'connected', beta: 'connected', gamma: 'unsupported'} : {alpha: 'missing', beta: 'missing', gamma: 'unsupported'},
    scopes: r ? r.scopes : [], connected_at: s.connected_at, checked_at: 0, problem: s.problem,
    attention: s.state === 'needs_reconnect' || s.state === 'failed'};
};
const view = () => {
  const s = window.__svc, r = route();
  const routes = window.__desc.routes.map(x => ({id: x.id, name: x.name, recommended: !!x.recommended}));
  if (!r) return {route: null, routes, steps: [], step: null};
  return {route: {id: r.id, kind: r.kind, name: r.name}, routes,
    steps: r.steps.map((x, i) => ({id: x.id, title: x.title, state: i < s.idx ? 'done' : i === s.idx ? 'current' : 'todo'})),
    step: Object.assign({}, r.steps[s.idx], {state: 'current', result: s.results[r.steps[s.idx].id] || null, message: s.message})};
};
const ok = (extra) => Object.assign({success: true, status: status(), view: view()}, extra || {});
const fail = (code, text, next) => ({success: false, code, message: {text: T(text[0], text[1]), next: T(next[0], next[1])}, status: status(), view: view()});
window.__api = {
  connect_list: () => ({success: true, services: [status()], clis: window.__clis}),
  connect_describe: () => ({success: true, descriptor: window.__desc}),
  connect_status: (svc, a) => { a = JSON.parse(a || '{}'); return ok(); },
  connect_start: (svc, a) => {
    a = JSON.parse(a || '{}'); const s = window.__svc;
    if (a.route) { s.route = a.route; s.idx = 0; s.results = {}; s.state = 'waiting_user'; }
    else if (a.reconnect) { s.state = 'waiting_user'; s.idx = 0; s.results = {}; s.problem = null; if (!s.route) s.route = 'app'; }
    else if (s.state === 'not_started') { s.route = 'app'; s.state = 'waiting_user'; }
    s.message = null;
    return ok();
  },
  connect_step: (svc, op, a) => {
    a = JSON.parse(a || '{}'); const s = window.__svc, r = route(), st = r.steps[s.idx];
    s.message = null;
    if (op === 'check') {
      if (st.check) s.results[st.id] = st.check.id === 'present'
        ? (s.added ? {code: 'ok', tone: 'ok', text: st.check.states.ok.text} : {code: 'missing', tone: 'warn', text: st.check.states.missing.text})
        : (s.signed ? {code: 'ok', tone: 'ok', text: st.check.states.ok.text} : {code: 'no', tone: 'warn', text: st.check.states.no.text});
      return ok();
    }
    if (op === 'act') { if (a.action === 'add') { s.added = true; s.results[st.id] = {code: 'added', tone: 'ok', text: T('Added', '加好了')}; } return ok(); }
    if (op === 'done') {
      if (st.id === 'sign' && !s.signed) { s.signed = window.__signNext === true; if (!s.signed) { s.message = {code: 'gate', text: T('The page does not show you as signed in yet.', '頁面上還看不到你已登入。'), next: T('Sign in there, then press this again.', '先在那邊登入，再按一次。')}; return Object.assign(fail('gate', ['The page does not show you as signed in yet.', '頁面上還看不到你已登入。'], ['Sign in there, then press this again.', '先在那邊登入，再按一次。'])); } }
      if (s.idx < r.steps.length - 1) { s.idx += 1; if (r.steps[s.idx].kind === 'test') s.state = 'waiting_user'; }
      return ok();
    }
    if (op === 'back') { if (s.idx > 0) s.idx -= 1; return ok(); }
    if (op === 'route') { s.route = a.route; s.idx = 0; s.results = {}; return ok(); }
    if (op === 'stuck') { window.__stuck = a; return {success: true, sid: 's9', opened: true}; }
    return fail('bad_op', ['No such step', '沒有這個步驟'], ['', '']);
  },
  connect_test: (svc, a) => {
    a = JSON.parse(a || '{}'); const s = window.__svc;
    if (a.cancel) { s.state = 'waiting_user'; return ok({result: {state: 'done', ok: false, code: 'cancelled', failed_step: ''}}); }
    if (!a.handle) { s.state = 'verifying'; s.polls = 0; return ok({result: {state: 'running', handle: 'h1', phase: 'start'}}); }
    s.polls += 1;
    if (s.polls < 2) return ok({result: {state: 'running', handle: 'h1', phase: 'asking'}});
    if (s.testFail) { s.state = 'waiting_user'; return ok({result: {state: 'done', ok: false, code: 'no_tools', failed_step: 'add'}}); }
    s.state = 'connected'; s.connected_at = 1794190841; s.problem = null;
    return ok({result: {state: 'done', ok: true}});
  },
  connect_disconnect: () => { Object.assign(window.__svc, {state: 'not_started', route: '', idx: 0, results: {}, added: false, signed: false}); return {success: true, removed: ['demo']}; },
  connect_signal: (reason) => {
    const m = /^connect:([a-z0-9_-]+)$/.exec(reason);
    if (!m || m[1] !== 'demo') return {success: false, code: 'unknown_service', message: {text: T('Not a service ShellBot knows', '這不是 ShellBot 認得的服務'), next: T('', '')}};
    const s = window.__svc; if (!s.route) s.route = 'app'; s.state = 'waiting_user'; s.idx = 0;
    return ok({service: 'demo'});
  },
};
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    if (prop in window.__api) return R(window.__api[prop](...args));
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'open_url': window.__opened = args[0]; return R('ok');
      case 'chat_view': return R({success: true, readable: true, provider: 'claude', activity: 'done', decision: window.__decision, rev: 'r' + JSON.stringify(window.__decision) + window.__answers.length,
                                  turns: [{kind: 'user_msg', ts: 1794190841, text: '幫我看一下今天的備忘'}, {kind: 'assistant_text', ts: 1794190843, text: '我需要先接上示範服務才能讀。'}], queue: {queued: [], given_up: []}});
      case 'bots_link_answer': window.__answers.push(args); return R({success: true});
      default: return R('{}');
    }
  };
}})};
"""

CFG = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]


def stub(lang="zh-TW"):
    cfg = json.loads(json.dumps(CFG))
    cfg["settings"]["language"] = lang
    return STUB % {"cfg": json.dumps(cfg, ensure_ascii=False), "sessions": json.dumps(SESSIONS, ensure_ascii=False),
                   "desc": json.dumps(DESC, ensure_ascii=False), "clis": json.dumps(CLIS, ensure_ascii=False)}


def calls(page, name):
    return page.evaluate("(n) => window.__calls.filter(c => c[0] === n).map(c => c[1])", name)


def op_calls(page, op):
    return [c for c in calls(page, "connect_step") if c[1] == op]


def open_page(browser, lang="zh-TW"):
    page = browser.new_page(viewport={"width": 1100, "height": 800}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(lang))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("window.SFConnect && SFConnect.list().loaded", timeout=15000)
    page.wait_for_timeout(500)
    return page, errors


def open_settings_tab(page):
    page.click("#btn-settings")
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector("#settings-panel-connect .cn-row", timeout=5000)


def card_box(page):
    return page.evaluate("(() => { const b = document.getElementById('cn-card').getBoundingClientRect();"
                         " return {w: b.width, h: b.height, top: b.top, left: b.left, vw: innerWidth, vh: innerHeight}; })()")


def shot(page, name, clip_selector=None):
    SHOTS.mkdir(exist_ok=True)
    target = page.locator(clip_selector) if clip_selector else page
    target.screenshot(path=str(SHOTS / name))


def run(browser):
    print("1. Settings → Connect: the row of a service not started")
    page, errors = open_page(browser)
    tabs = page.evaluate("[...document.querySelectorAll('#settings-tabs-bar .settings-tab')].map(b => b.dataset.tab)")
    check("the Connect tab is in Settings, right before Plugins", "connect" in tabs and tabs.index("connect") == tabs.index("marketplace") - 1, tabs)
    open_settings_tab(page)
    row = page.locator("#settings-panel-connect .cn-row")
    check("one row per service: its name, a sentence on where it stands, a Connect button",
          row.count() == 1 and "示範服務" in row.inner_text() and "讓 bot 讀取、寄出示範備忘" in row.inner_text()
          and row.locator("button").inner_text() == "開始連接", row.inner_text())
    check("…and no mark on it, since nothing needs the person", row.locator(".cn-flag").count() == 0 and "attn" not in (row.get_attribute("class") or ""))
    shot(page, "connect-list-zh-webkit.png", "#modal-settings .modal")

    print("2. the card opens on the choice of a way, at a moderate size")
    row.locator("button").click()
    page.wait_for_selector("#cn-card .cn-route", timeout=5000)
    check("two ways, the recommended one picked and badged",
          page.locator("#cn-card .cn-route").count() == 2 and page.locator("#cn-card .cn-route.on").count() == 1
          and "推薦" in page.locator("#cn-card .cn-route.on").inner_text() and "透過 ShellBot 的小幫手" in page.locator("#cn-card .cn-route.on").inner_text())
    check("…with what each takes (steps, minutes, what is needed) and which AI tools it is for, by the table's names",
          "3 步" in page.locator("#cn-card .cn-route.on").inner_text() and "約 3 分鐘" in page.locator("#cn-card .cn-route.on").inner_text()
          and "適用：Alpha CLI、Beta CLI" in page.locator("#cn-card .cn-route.on").inner_text(), page.locator("#cn-card .cn-route.on").inner_text())
    b = card_box(page)
    fonts = page.evaluate("(() => { const c = document.getElementById('cn-card'); return [getComputedStyle(c).fontSize, getComputedStyle(c.querySelector('.cn-route-blurb')).fontSize,"
                          " getComputedStyle(c.querySelector('.cn-title')).fontSize]; })()")
    check("the card is at most 640px wide, with room around it, and its text is 12-13px (title 14px)",
          b["w"] <= 640 and b["left"] >= 48 and b["top"] >= 24 and b["h"] <= b["vh"] * 0.84 and fonts == ["12.5px", "12.5px", "14px"], (b, fonts))
    shot(page, "connect-route-zh-webkit.png", "#cn-card")
    page.locator("#cn-card .cn-route", has_text="用金鑰").click()
    check("picking another way moves the choice", "用金鑰" in page.locator("#cn-card .cn-route.on").inner_text())
    page.locator("#cn-card .cn-route", has_text="小幫手").click()

    print("3. a step: words, a link, a button that does one thing, a check")
    page.click('#cn-card [data-act="route-go"]')
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("Next starts the picked way (connect_start with its route) and shows step 1 of 3",
          [c for c in calls(page, "connect_start") if json.loads(c[1]).get("route") == "app"] != []
          and "第 1 步，共 3 步" in page.locator("#cn-card .cn-prog-label").inner_text()
          and "把它加進你的 AI 工具" in page.locator("#cn-card .cn-title").inner_text())
    page.wait_for_selector("#cn-card .cn-msg.warn", timeout=5000)
    check("the check that runs on entering asked the Python side and shows what it found",
          len(op_calls(page, "check")) == 1 and "你的工具裡還沒有" in page.locator("#cn-card .cn-msg").inner_text()
          and "按下面的按鈕加進去" in page.locator("#cn-card .cn-msg").inner_text())
    check("the options start where the descriptor says (one ticked, one not)",
          page.locator("#cn-card .cn-opt.on").count() == 1 and page.locator("#cn-card .cn-opt").count() == 2)
    page.locator("#cn-card .cn-opt", has_text="每一個都先問我").click()
    page.locator('#cn-card [data-act="do"]').click()
    page.wait_for_selector("#cn-card .cn-msg.ok", timeout=5000)
    act = op_calls(page, "act")
    check("the button sends the action and the ticked options (connect_step act), then checks again",
          len(act) == 1 and json.loads(act[0][2]) == {"action": "add", "options": {"all": True, "ask": True}} and len(op_calls(page, "check")) == 2, act)
    check("…and the check now says it is found", "在你的工具裡找到了" in page.locator("#cn-card .cn-msg").inner_text())
    page.click('#cn-card [data-act="open"]')
    check("Open page opens the step's https link through the app", page.evaluate("window.__opened") == "https://example.com/demo-setup")
    shot(page, "connect-step-zh-webkit.png", "#cn-card")
    b2 = card_box(page)
    check("the card keeps its width from step to step", abs(b2["w"] - b["w"]) < 1, (b2["w"], b["w"]))

    print("4. I'm stuck")
    page.click('#cn-card [data-act="stuck"]')
    page.locator("#cn-card .cn-prob-q", has_text="工具清單是空的").click()
    check("the usual problems of this step, one open with its answer and a link",
          page.locator("#cn-card .cn-prob").count() == 2 and "關掉再開" in page.locator("#cn-card .cn-prob.on").inner_text()
          and page.locator("#cn-card .cn-prob.on .cn-link").count() == 1)
    shot(page, "connect-stuck-zh-webkit.png", "#cn-card")
    page.click('#cn-card [data-act="ask"]')
    page.wait_for_function("window.__stuck")
    stuck = page.evaluate("window.__stuck")
    check("Ask a bot sends the ticked problem and the tab it came from (connect_step stuck)", stuck.get("problem") == "nothing" and stuck.get("from_sid") == "s501", stuck)
    check("…and the card closes", not page.locator("#modal-connect.open").count())
    page.close()

    print("5. a way through to a passed test, and a failed one")
    page, errors = open_page(browser)
    page.evaluate("SFConnect.open('demo')")
    page.wait_for_selector("#cn-card .cn-route")
    page.click('#cn-card [data-act="route-go"]')
    page.wait_for_selector("#cn-card .cn-msg", timeout=5000)
    page.locator('#cn-card [data-act="do"]').click()
    page.wait_for_selector("#cn-card .cn-msg.ok")
    page.click('#cn-card [data-act="did"]')
    page.wait_for_selector("#cn-card .cn-title:has-text('登入一次')")
    check("I did it goes to step 2, which has no check yet (it runs on done)", "還沒檢查" in page.locator("#cn-card .cn-msg").inner_text())
    page.click('#cn-card [data-act="did"]')
    page.wait_for_selector("#cn-card .cn-msg.bad", timeout=5000)
    check("a refusal says message.text and message.next, and the card stays on the step",
          "頁面上還看不到你已登入" in page.locator("#cn-card .cn-msg.bad").inner_text() and "先在那邊登入，再按一次" in page.locator("#cn-card .cn-msg.bad").inner_text()
          and "登入一次" in page.locator("#cn-card .cn-title").inner_text())
    page.evaluate("window.__signNext = true")
    page.click('#cn-card [data-act="did"]')
    page.wait_for_selector("#cn-card .cn-title:has-text('測試連線')")
    check("when it goes through, the last step is the test, with a Start button", page.locator('#cn-card [data-act="test-start"]').count() == 1
          and "第 3 步，共 3 步" in page.locator("#cn-card .cn-prog-label").inner_text())
    page.evaluate("window.__svc.testFail = true")
    page.click('#cn-card [data-act="test-start"]')
    page.wait_for_selector("#cn-card .cn-spin", timeout=3000)
    check("while it runs: a spinner and Stop", page.locator('#cn-card [data-act="test-stop"]').count() == 1)
    page.wait_for_selector("#cn-card .cn-result.bad", timeout=8000)
    check("a failed test says the verdict's words and next step, and points at the step",
          "測試分頁找不到工具" in page.locator("#cn-card .cn-result").inner_text() and "回到第 1 步重新加一次" in page.locator("#cn-card .cn-result").inner_text()
          and "第 1 步沒做好" in page.locator("#cn-card .cn-result").inner_text())
    page.click('#cn-card [data-act="goto"]:text-matches("回到第 1 步")')
    page.wait_for_selector("#cn-card .cn-how")
    check("Back to step 1 goes there (back, step by step) and the test is forgotten",
          "把它加進你的 AI 工具" in page.locator("#cn-card .cn-title").inner_text() and len(op_calls(page, "back")) == 2)
    page.evaluate("window.__svc.testFail = false; window.__signNext = true")
    page.click('#cn-card [data-act="did"]')
    page.wait_for_selector("#cn-card .cn-title:has-text('登入一次')")
    page.click('#cn-card [data-act="did"]')
    page.wait_for_selector("#cn-card .cn-title:has-text('測試連線')")
    page.click('#cn-card [data-act="test-start"]')
    page.wait_for_selector("#cn-card .cn-result.ok", timeout=8000)
    check("a passed test says Connected and the status follows (connected)",
          "已接上" in page.locator("#cn-card .cn-result").inner_text() and "透過小幫手" in page.locator("#cn-card .cn-result").inner_text()
          and page.evaluate("SFConnect.list().by.demo.state") == "connected" and page.locator("#cn-card .cn-sub").inner_text() == "已接上")
    shot(page, "connect-done-zh-webkit.png", "#cn-card")
    page.click('#cn-card [data-act="finish"]')
    page.wait_for_function("!document.querySelector('#modal-connect.open')")
    open_settings_tab(page)
    check("the row says Connected, with the way and the date, and the button is Manage",
          "已接上" in page.locator("#settings-panel-connect .cn-row").inner_text() and "透過 ShellBot 的小幫手" in page.locator("#settings-panel-connect .cn-row").inner_text()
          and page.locator("#settings-panel-connect .cn-row button").inner_text() == "管理")
    page.locator("#settings-panel-connect .cn-row button").click()
    page.wait_for_selector("#cn-card .cn-kv")
    check("a connected service opens on what it is: each AI's state and what it may do",
          "Alpha CLI" in page.locator("#cn-card").inner_text() and "不支援" in page.locator("#cn-card").inner_text() and "讀取備忘" in page.locator("#cn-card").inner_text())
    page.keyboard.press("Escape")
    check("Esc closes the card and leaves Settings open", not page.locator("#modal-connect.open").count() and page.locator("#modal-settings.open").count() == 1)

    print("6. a service that needs reconnecting")
    page.evaluate("Object.assign(window.__svc, {state: 'needs_reconnect', problem: {code: 'expired', text: {en: 'Its sign-in ran out.', zh: '它的登入過期了。'}, next: {en: 'Connect it again.', zh: '重新接上就好。'}}})")
    page.evaluate("window.__calls.length = 0")
    page.click('#settings-tabs-bar [data-tab="general"]')
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector("#settings-panel-connect .cn-row.attn", timeout=5000)
    check("opening the tab asks connect_status(refresh) for what is connected",
          any(json.loads(c[1]).get("refresh") for c in calls(page, "connect_status")), calls(page, "connect_status"))
    r = page.locator("#settings-panel-connect .cn-row")
    check("the row is marked: Needs you, its reason and what to do, a Reconnect button; the tab carries a dot",
          "要處理" in r.inner_text() and "它的登入過期了" in r.inner_text() and "重新接上就好" in r.inner_text() and r.locator("button").inner_text() == "重新接上"
          and page.locator('#settings-tabs-bar [data-tab="connect"]').inner_text().endswith("●"))
    page.click("#btn-close-settings")
    page.evaluate("window.__calls.length = 0")
    page.evaluate("SFConnect.poll()")
    page.wait_for_selector("#cn-side .cn-sc", timeout=5000)
    check("a small card in the sidebar says so, with a Reconnect button",
          "示範服務 需要重新接上" in page.locator("#cn-side .cn-sc").inner_text() and "它的登入過期了" in page.locator("#cn-side .cn-sc").inner_text())
    check("…and the sidebar poll asks connect_list and never connect_status(refresh)",
          calls(page, "connect_list") != [] and not [c for c in calls(page, "connect_status") if json.loads(c[1]).get("refresh")])
    shot(page, "connect-sidebar-zh-webkit.png", "#sidebar")
    page.click("#cn-side [data-cn-side]")
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("Reconnect opens the wizard at step 1 (connect_start with reconnect)",
          [c for c in calls(page, "connect_start") if json.loads(c[1]).get("reconnect")] != []
          and "第 1 步，共 3 步" in page.locator("#cn-card .cn-prog-label").inner_text())
    page.keyboard.press("Escape")
    page.evaluate("Object.assign(window.__svc, {state: 'needs_reconnect'})")
    page.evaluate("SFConnect.poll()")
    page.wait_for_selector("#cn-side .cn-sc", timeout=5000)
    page.click("#cn-side .cn-sc-x")
    check("a card closed by the person stays closed until the state changes", page.locator("#cn-side").is_hidden())
    page.close()

    print("7. the chat view's [[SF:RED:connect:demo]] question")
    page, errors = open_page(browser)
    page.evaluate("window.__decision = {id: 'd1', kind: 'red', tag: 'connect:demo', reason: '要讀今天的備忘，需要先接上示範服務。', options: [{n: 1, text: '好，我去接上'}, {n: 2, text: '先不要，之後再說'}]}")
    page.wait_for_selector("#chat-ask .cm-opt[data-act='connect']", timeout=8000)
    check("it is a card: what is asked, and a button that connects step by step; the other option stays an option",
          "需要接上 示範服務" in page.locator("#chat-ask").inner_text() and "要讀今天的備忘" in page.locator("#chat-ask").inner_text()
          and page.locator("#chat-ask .cm-opt[data-act='answer']").count() == 1)
    shot(page, "connect-chat-zh-webkit.png", "#chat-ask")
    page.click("#chat-ask .cm-opt[data-act='connect']")
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    sig = calls(page, "connect_signal")
    check("the button goes through connect_signal with the tag, and opens the same wizard (step 1)",
          sig == [["connect:demo"]] and "第 1 步，共 3 步" in page.locator("#cn-card .cn-prog-label").inner_text(), sig)
    page.keyboard.press("Escape")
    page.evaluate("window.__decision = {id: 'd2', kind: 'red', tag: 'connect:nothing-like-it', reason: '別的問題', options: [{n: 1, text: '好'}, {n: 2, text: '不要'}]}")
    page.wait_for_selector("#chat-ask [data-id='d2']", timeout=8000)
    check("a tag the page does not know is a plain question", page.locator("#chat-ask .cm-opt[data-act='connect']").count() == 0)
    check("no page errors", not errors, errors)
    page.close()

    print("8. English, and no service named in the page's own code")
    page, errors = open_page(browser, "en")
    open_settings_tab(page)
    check("the tab and the row follow the page's language",
          page.locator('#settings-tabs-bar [data-tab="connect"]').inner_text() == "Connect"
          and page.locator("#settings-panel-connect .cn-row button").inner_text() == "Connect")
    page.locator("#settings-panel-connect .cn-row button").click()
    page.wait_for_selector("#cn-card .cn-route")
    check("the card is in English too", "How do you want to connect it?" in page.locator("#cn-card").inner_text()
          and "Recommended" in page.locator("#cn-card").inner_text() and "For: Alpha CLI, Beta CLI" in page.locator("#cn-card").inner_text())
    shot(page, "connect-route-en-webkit.png", "#cn-card")
    check("no page errors", not errors, errors)
    page.close()
    src = (HERE / "web" / "connect.js").read_text(encoding="utf-8") + (HERE / "web" / "connect.css").read_text(encoding="utf-8")
    names = re.findall(r"gmail|google|notion|slack|github|figma|telegram|outlook|jira|drive", src, re.I)
    check("connect.js and connect.css name no service", not names, names)


with sync_playwright() as p:
    browser = p.webkit.launch()
    try:
        run(browser)
    finally:
        browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
