#!/usr/bin/env python3
"""The office's panel: a followed person's conversation and a box to message them, in the real page with a
stand-in Python side (chat_view / chat_send / chat_answer as the chat view gets them).

  - a bot with a tab, and an intern: the panel opens on 對話 with their conversation (the chat view's
    call for that one tab), a box named for them; 資料 has the old card; the choice is kept
  - agent text is inert (no tag, no handler survives); a link opens outside, the iframe stays put
  - Enter sends to that tab as the chat view does; Shift+Enter is a new line; Enter while composing
    (注音) does not send; Esc leaves the box first, the second Esc stops following
  - a half-typed message survives the 3 s snapshot (value and focus); it is kept per person and
    never shows in someone else's box
  - unsent text in the tab's terminal: nothing is sent, the card offers to clear it and send
  - queued: said so, and the queue's own words; refused: the reason, the text stays
  - a question: its options in plain words, answered with the option's own number; a reply for
    someone no longer followed is not drawn, and someone seen before is drawn again (not left on 讀取中)
  - a bot asleep (here or on a paired computer), a part-timer: no box, one line why; a part-timer offers
    the conversation of whoever sent it
  - a paired computer's bot with a tab: its conversation and the box, through that computer's /link/chat
    (link_chat with its tab's sid), never a tab on this computer; a computer too old for /link/chat says so
  - 資料 showing, or nobody followed: no more chat_view calls
  - a pasted picture is saved and shown above the box, and sent as its path ahead of the words; a paired computer's
    tab gets it sent there first and named by that computer's path; when it cannot take it, said so
  - in a narrow window the panel is the bottom sheet, full width, with its box
  - "到他的分頁" closes the office and switches the sidebar to that tab
  - no console error from either page
Demo data only (made-up names). Headless Chromium, and WebKit (the macOS app's engine) when playwright
has it. Screenshots (Chromium): qa-shots/office-chat-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_office_chat_ui.py
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
    print("SKIP  tests_office_chat_ui.py（沒裝 playwright）")
    sys.exit(0)

DATA = guide.load()
NOW = time.time()
ROSTER = {
    "chief": {"label": "示範秘書長", "responsibility": "統籌、分派工作", "cmd": "claude",
              "bot": {"avatar": {"shape": "star", "color": "purple"}, "primary": True, "memory": True},
              "cli": {"key": "claude", "label": "Claude Code"}, "pin": {"ok": True}},
    "eng": {"label": "示範工程師", "responsibility": "寫程式、跑測試", "cmd": "claude",
            "bot": {"avatar": {"shape": "egg", "color": "amber"}, "memory": True},
            "cli": {"key": "claude", "label": "Claude Code"}, "pin": {"ok": True}},
    "res": {"label": "示範研究員", "responsibility": "查資料、寫摘要", "cmd": "codex",
            "bot": {"avatar": {"shape": "drop", "color": "teal"}, "memory": True},
            "cli": {"key": "codex", "label": "Codex"}, "pin": {"ok": True}},
}
BOTS = {"enabled": True, "roster": ROSTER, "primary": "chief", "memory_root": "/tmp/demo",
        "sessions": {"s700": {"bot": "chief"}, "s701": {"bot": "eng"}},
        "kinds": {"s700": "temp", "s701": "temp", "s702": "temp"}}
SESSIONS = [{"sid": "s700", "cmd": "claude", "alive": True, "label": "示範秘書長"},
            {"sid": "s701", "cmd": "claude", "alive": True, "label": "示範工程師"},
            {"sid": "s702", "cmd": "claude", "alive": True, "label": "示範臨時分頁"}]
STATUS = {"s700": {"state": "idle", "dot": "", "summary": "idle"},
          "s701": {"state": "working", "dot": "sig-working", "action": "Running the demo tests"},
          "s702": {"state": "decision", "dot": "sig-decision", "narration": "示範：要繼續嗎？"}}
PEERS = {"peers": {"pid-demo-1": {"name": "示範電腦", "addr": "示範電腦", "primary": "builder",
                                  "bots": [{"role": "builder", "label": "示範建置員", "state": "awake", "primary": True, "sid": "r10",
                                            "avatar": {"shape": "ghost", "color": "green"}, "activity": "idle",
                                            "responsibility": "建置與測試"},
                                           {"role": "napper", "label": "示範午睡員", "state": "asleep",
                                            "avatar": {"shape": "round", "color": "teal"}, "responsibility": "休息中"},
                                           {"role": "asker", "label": "示範等待員", "state": "awake", "sid": "r11", "activity": "decision",
                                            "decision": True, "latest": "示範：要不要部署？", "avatar": {"shape": "egg", "color": "pink"},
                                            "responsibility": "部署"}],
                                  "tabs": {"r30": {"kind": "temp", "name": "示範隔壁實習生", "cli": "codex", "activity": "working",
                                                   "latest": "示範：Running npm test", "avatar": {"shape": "round", "color": "sky"}}}},
                   "pid-demo-old": {"name": "示範舊電腦", "addr": "示範舊電腦", "primary": "oldie",
                                    "bots": [{"role": "oldie", "label": "示範老版本", "state": "awake", "primary": True, "sid": "r20",
                                              "avatar": {"shape": "hex", "color": "amber"}, "activity": "idle",
                                              "responsibility": "舊版"}], "tabs": {}}}}
HELPERS = {"s701": [{"id": "a1", "description": "示範：跑一次完整測試", "model": "demo-model", "agent_type": "general-purpose",
                     "state": "working", "started_at": NOW - 300, "last_at": NOW - 20}]}
LONG = "\n\n".join(f"示範段落 {i}：這一段很長，只是為了把泡泡撐高。" for i in range(40))
CHATS = {
    "s700": {"activity": "idle", "turns": [{"kind": "user_msg", "text": "示範：早安", "ts": 1},
                                           {"kind": "assistant_text", "text": "示範秘書長：早安", "ts": 2}]},
    "s701": {"activity": "working", "turns": [
        {"kind": "user_msg", "text": "示範：幫我跑一次測試", "ts": 1},
        {"kind": "tool_call", "tool": "Bash", "target": "npm test", "ts": 2},
        {"kind": "assistant_text", "text": "示範：**測試跑完了**，報告在 [這裡](https://example.com/demo-report)。"
                                           "<img src=x onerror=\"window.__pwned=1\">", "ts": 3},
        {"kind": "assistant_text", "text": LONG, "ts": 4}]},
    "s702": {"activity": "decision", "turns": [{"kind": "user_msg", "text": "示範：清掉暫存檔", "ts": 1},
                                               {"kind": "tool_call", "tool": "Bash", "target": "rm -rf /tmp/demo-cache", "ts": 2}],
             "decision": {"id": "q1", "reason": "Do you want to proceed?", "options": [{"n": 1, "text": "Yes"}, {"n": 2, "text": "No"}]}},
}
CFG = {"settings": {"language": "zh-TW", "experimental_bots": True, "experimental_office": True, "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {},
       "guide": {"tour": "done", "seen": guide.app_version()}}
GDATA = {"success": True, "features": guide.for_surface("desktop", CFG["settings"], DATA), "tours": DATA.get("tours"),
         "state": {"tour": "done", "seen": guide.app_version(), "version": guide.app_version()}}


def stub():
    return """
if (window.top === window) {
window.__calls = []; window.__views = []; window.__sends = []; window.__answers = []; window.__urls = []; window.__saved = [];
window.__sendMode = 'ok'; window.__slow = {}; window.__remote = []; window.__remoteVer = 0; window.__closed = []; window.__rclosed = []; window.__saved_images = []; window.__uploads = []; window.__uploadFail = false;
window.__remoteTurns = [{kind: 'user_msg', text: '示範：幫我建置', ts: 1}, {kind: 'assistant_text', text: '示範建置員：建置完成。', ts: 2}];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s; window.__peers = %s;
window.__helpers = %s; window.__guide = %s; window.__chats = %s; window.__ver = {};
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
const later = (ms, v) => new Promise(ok => setTimeout(() => ok(typeof v === 'string' ? v : JSON.stringify(v)), ms));
function view(sid, limit, rev) {
  window.__views.push(sid);
  const c = window.__chats[sid];
  if (!c) return R({success: false, code: 'no_tab'});
  const cur = sid + ':' + (window.__ver[sid] || 0);
  const body = rev === cur ? {success: true, unchanged: true, rev: cur}
    : Object.assign({success: true, rev: cur, provider: 'claude', readable: true, decision: null, login: {},
                     queue: {queued: [], given_up: []}}, JSON.parse(JSON.stringify(c)));
  return window.__slow[sid] ? later(window.__slow[sid], body) : R(body);
}
function send(sid, text, clear) {
  window.__sends.push({sid, text, clear: !!clear});
  const m = window.__sendMode;
  if (m === 'draft' && !clear) return R({success: false, code: 'draft', draft: '示範：打到一半的字'});
  if (m === 'fail') return R({success: false, code: 'no_tab', message: '示範：分頁不見了'});
  const c = window.__chats[sid];
  if (m === 'queued') {
    c.queue = {queued: ['m1'], given_up: [], busy: {reason: 'working', since: Date.now() / 1000 - 120}};
  } else {
    c.turns.push({kind: 'user_msg', text, ts: Date.now() / 1000});
  }
  window.__ver[sid] = (window.__ver[sid] || 0) + 1;
  return R({success: true, queued: m === 'queued', id: 'm1', reason: m === 'queued' ? 'working' : null});
}
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push(prop);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's700'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'link_peer_bots': return R(window.__peers);
      case 'tasks_overview': return R({success: true, open: [], stuck: [], decisions: []});
      case 'office_prefs': return R({success: true, prefs: {}});
      case 'office_prefs_save': window.__saved.push(JSON.parse(args[0])); return R({success: true});
      case 'office_helpers': return R({success: true, helpers: window.__helpers});
      case 'chat_view': return view(args[0], args[1], args[2]);
      case 'chat_send': return send(args[0], args[1], args[2]);
      case 'chat_answer': window.__answers.push({sid: args[0], n: args[1], qid: args[2]}); return R({success: true});
      case 'link_chat': {                    // a paired computer's chat_<action>, arguments by name
        const [pid, rsid, action, named] = args; const a = JSON.parse(named || '{}');
        window.__remote.push({pid, rsid, action, args: a});
        if (pid === 'pid-demo-old') return R({success: false, code: 'old_peer'});
        if (action === 'view') return R(a.rev === 'r10:' + window.__remoteVer ? {success: true, unchanged: true, rev: a.rev}
          : {success: true, rev: 'r10:' + window.__remoteVer, provider: 'claude', readable: true, activity: 'idle', decision: null, login: {},
             queue: {queued: [], given_up: []}, turns: window.__remoteTurns});
        if (action === 'send') { window.__remoteTurns.push({kind: 'user_msg', text: a.text, ts: Date.now() / 1000}); window.__remoteVer++;
          return R({success: true, queued: false, id: 'x1'}); }
        return R({success: false, code: 'unknown'});
      }
      case 'open_url': window.__urls.push(args[0]); return R({success: true});
      case 'close_session': window.__closed.push(args[0]); window.__sessions = window.__sessions.filter(s => s.sid !== args[0]); return R({success: true});
      case 'save_image': window.__saved_images.push(String(args[0]).slice(0, 22)); return R('/tmp/demo/paste-' + window.__saved_images.length + '.png');
      case 'link_send_file': window.__uploads.push([args[0], args[1]]);
        return R(window.__uploadFail ? {success: false, message: '示範：連不到'} : {success: true, saved: '/remote/demo/' + String(args[1]).split('/').pop()});
      case 'link_remote_close': window.__rclosed.push([args[0], args[1]]); return R({success: true});
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
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, BOTS, STATUS, PEERS, HELPERS, GDATA, CHATS))


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    class Server(http.server.ThreadingHTTPServer):
        request_queue_size = 64   # the page loads many files at once; the default backlog of 5 reset connections under load
        daemon_threads = True
    srv = Server(("127.0.0.1", 0), functools.partial(Quiet, directory=str(HERE / "web")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def office(page):
    return next((f for f in page.frames if f.url.split("#")[0].endswith("/office.html")), None)


def run(browser, base, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.add_init_script(stub())
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-entry #btn-office", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled() && window.SFChat", timeout=20000)
    page.wait_for_function("""['s701:sig-working','s702:sig-decision'].every(p => { const [sid, c] = p.split(':');
        const s = window.__sfHost.sessions()[sid]; return s && s._dotClass === c; })""", timeout=20000)
    page.click("#btn-office")
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe');
        try { return f.contentWindow.__office && Object.keys(f.contentWindow.__office.assignments()).length >= 3; } catch (e) { return false; } }""",
                           timeout=15000)
    fr = office(page)
    fr.evaluate("__office.pause(true)")
    views = lambda sid: page.evaluate("(s) => window.__views.filter(x => x === s).length", sid)  # noqa: E731
    text = lambda sel: fr.evaluate("(s) => document.querySelector(s).innerText", sel)  # noqa: E731

    print(f"[{engine}] a bot's conversation")
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('測試跑完了')", timeout=5000)
    st = fr.evaluate("""() => ({tabs: !document.getElementById('pTabs').hidden, chat: !document.getElementById('pChat').hidden,
        info: !document.getElementById('pInfo').hidden, sel: document.getElementById('tabChat').getAttribute('aria-selected'),
        box: !document.getElementById('cComp').hidden, ph: document.getElementById('cBox').placeholder,
        w: document.getElementById('panel').getBoundingClientRect().width})""")
    check("the panel opens on 對話: tabs, the conversation, a box named for them; 資料 hidden",
          st["tabs"] and st["chat"] and not st["info"] and st["sel"] == "true" and st["box"] and "示範工程師" in st["ph"], st)
    check("…wider than the card-only panel", st["w"] >= 390, st["w"])
    check("…read from that bot's own tab", views("s701") >= 1 and views("s700") == 0, page.evaluate("window.__views"))
    lst = text("#cList")
    check("…in order: the person, the tool calls as one line, the replies", lst.index("幫我跑一次測試") < lst.index("跑了 1 個指令") < lst.index("測試跑完了"), lst[:200])
    check("agent text is inert: no <img>, nothing ran", fr.evaluate("document.querySelectorAll('#cList img').length") == 0
          and fr.evaluate("window.__pwned === undefined") and page.evaluate("window.__pwned === undefined"))
    check("a long reply is folded with 顯示全部", fr.evaluate("document.querySelectorAll('#cList .cm.clip .cbody').length") == 1
          and "顯示全部" in lst)
    check("what it is doing is said above the conversation", "正在" in text("#cDoing"), text("#cDoing"))
    if shoot:
        page.wait_for_timeout(900)          # the panel slides in and the camera settles
        page.screenshot(path=str(SHOTS / "office-chat-bot.png"))
    fr.click("#cList .cm.clip .cmore")
    check("…and opens in place", fr.evaluate("document.querySelectorAll('#cList .cm.clip').length") == 0)
    fr.click("#cList a.cm-link")
    page.wait_for_timeout(200)
    check("a link opens outside (open_url); the office stays put",
          page.evaluate("window.__urls") == ["https://example.com/demo-report"] and office(page) is not None
          and fr.url.endswith("/office.html"), (page.evaluate("window.__urls"), fr.url))

    print(f"[{engine}] sending")
    fr.fill("#cBox", "示範：再跑一次")
    fr.press("#cBox", "Enter")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('再跑一次')", timeout=5000)
    sends = page.evaluate("window.__sends")
    check("Enter sends to that tab, as the person's message", sends == [{"sid": "s701", "text": "示範：再跑一次", "clear": False}], sends)
    check("…the box empties and it says so; the conversation shows it",
          fr.evaluate("document.getElementById('cBox').value") == "" and "已送出" in text("#cNote"), text("#cNote"))
    fr.fill("#cBox", "示範：第一行")
    fr.press("#cBox", "Shift+Enter")
    fr.type("#cBox", "第二行")
    check("Shift+Enter is a new line, not a send", fr.evaluate("document.getElementById('cBox').value") == "示範：第一行\n第二行"
          and len(page.evaluate("window.__sends")) == 1)
    fr.evaluate("""() => { const b = document.getElementById('cBox');
        b.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true, cancelable: true}));
        b.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 229, bubbles: true, cancelable: true})); }""")
    check("Enter while composing (注音選字) sends nothing", len(page.evaluate("window.__sends")) == 1)
    fr.focus("#cBox")
    page.wait_for_timeout(3600)            # at least one 3 s snapshot and one 2 s conversation poll
    keep = fr.evaluate("({v: document.getElementById('cBox').value, f: document.activeElement && document.activeElement.id})")
    check("a half-typed message survives the snapshot: its text and the focus", keep == {"v": "示範：第一行\n第二行", "f": "cBox"}, keep)
    fr.press("#cBox", "Escape")
    check("Esc leaves the box first; still following", fr.evaluate("document.activeElement && document.activeElement.id") != "cBox"
          and fr.evaluate("__office.following()") == "b:eng")

    print(f"[{engine}] another person")
    fr.evaluate("__office.follow('t:s702')")
    fr.wait_for_function("!document.getElementById('cAsk').hidden && document.getElementById('cAsk').innerText.includes('允許')", timeout=5000)
    check("an intern gets the same panel; nothing typed for the bot shows in its box",
          fr.evaluate("document.getElementById('cBox').value") == "" and "示範臨時分頁" in fr.evaluate("document.getElementById('cBox').placeholder"))
    ask = text("#cAsk")
    check("its question in plain words, with what it is about", "要讓它繼續嗎" in ask and "rm -rf /tmp/demo-cache" in ask, ask)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-chat-ask.png"))
    fr.click("#cAsk button[data-n='1']")
    fr.wait_for_function("document.getElementById('cAsk').innerText.includes('已送出')", timeout=5000)
    check("an option is answered with its own number and the question's id",
          page.evaluate("window.__answers") == [{"sid": "s702", "n": 1, "qid": "q1"}], page.evaluate("window.__answers"))
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_function("document.getElementById('cBox').value === '示範：第一行\\n第二行'", timeout=5000)
    check("back on the bot, its unsent text is back", True)

    print(f"[{engine}] a reply that comes late")
    page.evaluate("window.__slow = {s700: 1500}")
    fr.evaluate("__office.follow('b:chief')")
    page.wait_for_timeout(100)
    fr.evaluate("__office.follow('t:s702')")
    page.wait_for_timeout(2200)
    page.evaluate("window.__slow = {}")
    lst = text("#cList")
    check("a reply for someone no longer followed is not drawn; someone seen before is drawn again", "秘書長：早安" not in lst and "清掉暫存檔" in lst,
          (lst[:120], fr.evaluate("JSON.stringify(__office.chat())"), page.evaluate("window.__views.slice(-8)")))

    print(f"[{engine}] what the tab says back")
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_function("document.getElementById('cBox').value.length > 0", timeout=5000)
    page.evaluate("window.__sendMode = 'draft'")
    fr.fill("#cBox", "示範：要清草稿的那句")
    fr.press("#cBox", "Enter")
    fr.wait_for_function("document.getElementById('cNote').innerText.includes('打到一半的字')", timeout=5000)
    check("unsent text in the terminal: nothing sent, the card says what is there, the text stays",
          fr.evaluate("document.getElementById('cBox').value") == "示範：要清草稿的那句"
          and page.evaluate("window.__sends.length") == 2, text("#cNote"))
    if shoot:
        page.screenshot(path=str(SHOTS / "office-chat-draft.png"))
    fr.click("#cNote button[data-act='clear-send']")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('要清草稿的那句')", timeout=5000)
    check("…清掉它，送出我的 sends it with clear_draft", page.evaluate("window.__sends[2]") ==
          {"sid": "s701", "text": "示範：要清草稿的那句", "clear": True}, page.evaluate("window.__sends[2]"))
    page.evaluate("window.__sendMode = 'queued'")
    fr.fill("#cBox", "示範：排隊的那句")
    fr.press("#cBox", "Enter")
    fr.wait_for_function("document.getElementById('cNote').innerText.includes('還在排隊')", timeout=5000)
    note = text("#cNote")
    check("queued: it says so, and why in plain words", "排隊" in note and "示範工程師還在做上一步" in note, note)
    page.evaluate("window.__sendMode = 'fail'; window.__chats.s701.queue = {queued: [], given_up: []}; window.__ver.s701 = 99")
    fr.fill("#cBox", "示範：送不出去的那句")
    fr.press("#cBox", "Enter")
    fr.wait_for_function("document.getElementById('cNote').innerText.includes('分頁不見了')", timeout=5000)
    check("refused: the reason, and the text stays", fr.evaluate("document.getElementById('cBox').value") == "示範：送不出去的那句")
    page.evaluate("window.__sendMode = 'ok'")
    fr.fill("#cBox", "")

    print(f"[{engine}] who gets no box")
    fr.evaluate("__office.follow('b:res')")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('在休息')", timeout=5000)
    check("a bot asleep: 對話 says it has no tab, no box", fr.evaluate("document.getElementById('cComp').hidden")
          and fr.evaluate("!document.getElementById('pTabs').hidden"), text("#cList"))
    fr.evaluate("__office.follow('h:s701/a1')")
    page.wait_for_timeout(200)
    hp = fr.evaluate("""() => ({tabs: !document.getElementById('pTabs').hidden, chat: !document.getElementById('pChat').hidden,
        info: !document.getElementById('pInfo').hidden, task: document.getElementById('pTask').innerText})""")
    check("a part-timer: no tabs, no box, one line why", not hp["tabs"] and not hp["chat"] and hp["info"] and "沒有自己的對話框" in hp["task"], hp)
    fr.click("#pTask button[data-act='parent-chat']")
    fr.wait_for_function("__office.following() === 'b:eng' && !document.getElementById('pChat').hidden", timeout=5000)
    check("…and 看示範工程師的對話 follows whoever sent it, on 對話", True)

    print(f"[{engine}] a paired computer's people")
    fr.evaluate("__office.follow('p:pid-demo-1/builder')")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('建置完成')", timeout=5000)
    rv = page.evaluate("window.__remote.filter(r => r.action === 'view')")
    check("a paired computer's bot with a tab: its conversation, read on that computer (link_chat, its tab's sid)",
          rv and rv[0]["pid"] == "pid-demo-1" and rv[0]["rsid"] == "r10" and rv[0]["args"].get("limit") == 100, rv[:1])
    check("…with tabs and a box", fr.evaluate("!document.getElementById('pTabs').hidden && !document.getElementById('cComp').hidden"))
    if shoot:
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "office-chat-peer.png"))
    fr.fill("#cBox", "示範：再建置一次")
    fr.press("#cBox", "Enter")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('再建置一次')", timeout=5000)
    rs = page.evaluate("window.__remote.filter(r => r.action === 'send')")
    check("…a message goes to that tab on that computer, as the person's",
          rs == [{"pid": "pid-demo-1", "rsid": "r10", "action": "send", "args": {"text": "示範：再建置一次", "clear_draft": False}}], rs)
    check("…and nothing went to a tab on this computer", not any(x["text"] == "示範：再建置一次" for x in page.evaluate("window.__sends")))
    fr.click("#cOpen")
    page.wait_for_timeout(300)
    check("to their tab, when this window cannot open it: says so and the office stays",
          office(page) is not None and "打不開那台電腦的分頁" in text("#cNote"), text("#cNote"))
    fr.evaluate("__office.follow('p:pid-demo-1/napper')")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('在休息')", timeout=5000)
    check("a paired computer's bot asleep: no box, says why", fr.evaluate("document.getElementById('cComp').hidden"), text("#cList"))
    fr.evaluate("__office.follow('p:pid-demo-old/oldie')")
    fr.wait_for_function("document.getElementById('cList').innerText.includes('版本太舊')", timeout=5000)
    check("a computer whose ShellBot has no /link/chat: says which version it needs", "0.46.1" in text("#cList"), text("#cList"))

    print(f"[{engine}] what interns are doing, and asking one to leave")
    fr.evaluate("__office.follow('q:pid-demo-1/r30')")
    page.wait_for_timeout(300)
    fr.click("#tabInfo")
    page.wait_for_timeout(200)
    info = fr.evaluate("({name: document.getElementById('pName').innerText, sub: document.getElementById('pSub').innerText, duty: document.getElementById('pDuty').innerText, chip: document.getElementById('pChip').innerText})")
    check("a paired computer's intern: its tab's name, its CLI, busy, and what it is doing",
          "示範隔壁實習生" in info["name"] and "Codex" in info["sub"] and info["chip"] == "在忙" and "示範：Running npm test" in info["duty"], info)
    fr.evaluate("__office.follow('p:pid-demo-1/asker')")
    page.wait_for_timeout(300)
    info = fr.evaluate("({chip: document.getElementById('pChip').innerText, duty: document.getElementById('pDuty').innerText, bye: !document.getElementById('dismiss').hidden})")
    check("a paired computer's bot waiting on you shows so (no longer always idle), with its job and what it is on; no 請他離開 for a bot",
          info["chip"] == "等你決定" and "部署" in info["duty"] and "要不要部署" in info["duty"] and not info["bye"], info)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-peer-states.png"))
    fr.evaluate("__office.follow('q:pid-demo-1/r30')")
    page.wait_for_timeout(200)
    check("an intern has 請他離開", fr.evaluate("!document.getElementById('dismiss').hidden"))
    fr.click("#dismiss")
    page.wait_for_timeout(150)
    check("…the first press only asks (it is busy: says so); nothing is closed",
          "他還在做" in fr.evaluate("document.getElementById('dismiss').innerText") and page.evaluate("window.__rclosed") == [])
    if shoot:
        page.screenshot(path=str(SHOTS / "office-dismiss-ask.png"))
    fr.click("#dismiss")
    page.wait_for_timeout(400)
    check("…the second closes that tab on its computer (as the sidebar's remote close), and the panel closes",
          page.evaluate("window.__rclosed") == [["pid-demo-1", "r30"]] and not fr.evaluate("__office.following()"), page.evaluate("window.__rclosed"))
    fr.evaluate("__office.follow('t:s702')")
    page.wait_for_timeout(200)
    fr.click("#dismiss")
    fr.wait_for_function("document.getElementById('dismiss').innerText === '請他離開'", timeout=10000)   # 4 s later it is forgotten
    fr.click("#dismiss")
    page.wait_for_timeout(300)
    check("a first press left alone for 4 s is forgotten: the next press asks again", page.evaluate("window.__closed") == []
          and "確定" in fr.evaluate("document.getElementById('dismiss').innerText"))
    fr.click("#dismiss")
    page.wait_for_function("window.__closed.length === 1", timeout=5000)
    check("…and confirmed, this computer's intern tab is closed (close_session, as its ✕)", page.evaluate("window.__closed") == ["s702"])
    window_sessions = page.evaluate("Object.keys(window.__sfHost.sessions())")
    check("…and it is gone from the page", "s702" not in window_sessions, window_sessions)
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_selector("#pTabs:not([hidden])", timeout=5000)
    fr.click("#tabChat")                                   # back on 對話 for what follows

    print(f"[{engine}] when nothing is read")
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_function("!document.getElementById('pChat').hidden", timeout=5000)
    fr.click("#tabInfo")
    page.wait_for_timeout(300)
    n0 = views("s701")
    page.wait_for_timeout(4500)
    check("資料 showing: the old card, and no more chat_view", fr.evaluate("!document.getElementById('pInfo').hidden")
          and views("s701") == n0, (n0, views("s701")))
    check("…the choice is kept on this computer", any(s.get("panelTab") == "info" for s in page.evaluate("window.__saved")),
          page.evaluate("window.__saved")[-1:])
    fr.click("#tabChat")
    fr.wait_for_function("!document.getElementById('pChat').hidden", timeout=5000)
    fr.evaluate("__office.unfollow()")
    page.wait_for_timeout(300)
    n1 = views("s701")
    page.wait_for_timeout(4500)
    check("nobody followed: no chat_view either", views("s701") == n1, (n1, views("s701")))

    print(f"[{engine}] pasting a picture")
    paste = """async () => { const b = document.getElementById('cBox');
        const png = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
        const dt = new DataTransfer(); dt.items.add(new File([png], 'demo.png', {type: 'image/png'}));
        b.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }"""
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_function("!document.getElementById('cComp').hidden", timeout=5000)
    fr.evaluate(paste)
    fr.wait_for_function("document.querySelectorAll('#cFiles .cfile').length === 1", timeout=5000)
    check("a pasted picture is saved (save_image) and shown above the box, with a thumbnail",
          page.evaluate("window.__saved_images.length") == 1 and fr.evaluate("!!document.querySelector('#cFiles .cfile img')"))
    if shoot:
        page.wait_for_timeout(900)            # the panel slides in
        page.screenshot(path=str(SHOTS / "office-chat-paste.png"))
    n0 = len(page.evaluate("window.__sends"))
    fr.fill("#cBox", "示範：看這張圖")
    fr.press("#cBox", "Enter")
    page.wait_for_function(f"window.__sends.length === {n0 + 1}", timeout=5000)
    last = page.evaluate("window.__sends[window.__sends.length - 1]")
    check("…sent with the words: its path first, as the chat view and the terminal send a picture",
          last["sid"] == "s701" and last["text"] == "/tmp/demo/paste-1.png\n\n示範：看這張圖", last)
    check("…and the box and the picture are cleared", fr.evaluate("document.querySelectorAll('#cFiles .cfile').length") == 0
          and fr.evaluate("document.getElementById('cBox').value") == "")
    fr.evaluate(paste)
    fr.wait_for_function("document.querySelectorAll('#cFiles .cfile').length === 1", timeout=5000)
    fr.click("#cFiles button[data-act='unattach']")
    check("× takes a picture off before sending", fr.evaluate("document.querySelectorAll('#cFiles .cfile').length") == 0)
    fr.evaluate("__office.follow('p:pid-demo-1/builder')")
    fr.wait_for_function("!document.getElementById('cComp').hidden", timeout=5000)
    fr.evaluate(paste)
    fr.wait_for_function("document.querySelectorAll('#cFiles .cfile').length === 1", timeout=5000)
    up = page.evaluate("window.__uploads")
    check("a paired computer's tab: the picture is sent there first (link_send_file)", up and up[-1][0] == "pid-demo-1"
          and up[-1][1].startswith("/tmp/demo/paste-"), up)
    fr.fill("#cBox", "示範：給你看")
    fr.press("#cBox", "Enter")
    page.wait_for_function("window.__remote.filter(r => r.action === 'send').length >= 2", timeout=5000)
    rs = [r for r in page.evaluate("window.__remote") if r["action"] == "send"][-1]
    check("…and the message names it by that computer's path, not this one's",
          rs["args"]["text"].startswith("/remote/demo/paste-") and rs["args"]["text"].endswith("\n\n示範：給你看"), rs)
    page.evaluate("window.__uploadFail = true")
    fr.evaluate(paste)
    fr.wait_for_function("document.getElementById('cNote').innerText.includes('收不到檔案')", timeout=5000)
    check("…when it cannot take the file now: said so, nothing attached", fr.evaluate("document.querySelectorAll('#cFiles .cfile').length") == 0)
    page.evaluate("window.__uploadFail = false")

    print(f"[{engine}] a narrow window")
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_function("!document.getElementById('pChat').hidden", timeout=5000)
    page.set_viewport_size({"width": 700, "height": 800})
    page.wait_for_timeout(700)
    nb = fr.evaluate("""() => { const r = document.getElementById('panel').getBoundingClientRect();
        return {l: Math.round(r.left), w: Math.round(r.width), vw: innerWidth, box: document.getElementById('cBox').getBoundingClientRect().height > 0}; }""")
    check("in a narrow window the panel is the bottom sheet, full width, with its box", nb["l"] == 0 and nb["w"] == nb["vw"] and nb["box"], nb)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-chat-narrow.png"))
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(300)

    print(f"[{engine}] to the tab")
    fr.evaluate("__office.follow('b:eng')")
    fr.wait_for_function("!document.getElementById('cOpen').hidden", timeout=5000)
    fr.click("#cOpen")
    page.wait_for_function("!document.getElementById('sfo-overlay')", timeout=5000)
    check("到他的分頁 closes the office and switches to that tab", page.evaluate("window.__sfHost.active()") == "s701",
          page.evaluate("window.__sfHost.active()"))
    check("no console error from either page", not errors, errors[:5])
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
