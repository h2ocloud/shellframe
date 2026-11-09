#!/usr/bin/env python3
"""The sidebar's search, idle view and newest-first order, in the real page.

Asked for: with 29 tabs it is hard to find one, to see which bots are free, and
which just finished. The tools are a view over the list, never a change to it:
your own order (also Telegram's /1 /2) is left as it is.

  - every row says how long ago it last answered ("剛剛" while it runs)
  - search matches a tab's name, its bot's job and its latest line, several
    words at once; Esc clears it; typing there never reaches a terminal
  - 「只看閒置」 hides what is running and turns on newest-first;
    turning it off goes back to your own order
  - 「超過 30 分鐘」 keeps only tabs quiet for more than 30 minutes
  - newest first puts running tabs on top, then by last answer
  - while sorted by time, dragging a row does not reorder (you would not see it)
  - the view is remembered; with bots off there are no tools at all

Screenshots: qa-shots/bots-sidebar-tools*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_bots_sidebar_tools.py
"""
import json
import pathlib
import sys
import time

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
    print("SKIP  tests_bots_sidebar_tools.py（沒裝 playwright）")
    sys.exit(0)

NOW = time.time()
TABS = [  # sid, label, cmd, state, seconds since its last answer (None = unknown), extra
    ("s201", "Coding", "claude", "working", 0, {"action": "Editing relay.py"}),
    ("s202", "研究", "claude", "decision", 3 * 60, {"narration": "兩個方案要選哪個？"}),
    ("s203", "週報整理", "claude", "done", 2 * 3600, {"narration": "放在 reports/weekly.md"}),
    ("s204", "部署檢查", "claude", "idle", 45 * 60, {"narration": "等對方開防火牆"}),
    ("s205", "文件翻譯", "codex", "idle", 10 * 60, {"narration": "第 3 章翻完"}),
    ("s206", "Shell", "zsh", "unknown", None, {}),
    ("s207", "relay 除錯", "claude", "working", 0, {"action": "Running npm test"}),
]
DOT = {"working": "sig-working", "decision": "sig-decision", "done": "sig-done", "idle": "", "unknown": ""}
ROSTER = {
    "Coding": {"label": "Coding", "responsibility": "程式、測試、部署", "cmd": "claude",
               "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}},
    "日程": {"label": "日程", "responsibility": "行程、信件、提醒", "cmd": "claude",
             "bot": {"avatar": {"shape": "cloud", "color": "green"}, "memory": True}},
    "主控": {"label": "主控", "responsibility": "盤點與派工", "cmd": "claude",
             "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}},
}
STATE = {"enabled": True, "roster": ROSTER, "primary": "主控", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s201": {"bot": "Coding"}}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": sid, "cmd": cmd, "alive": True, "label": label, "bridge_enabled": True}
            for sid, label, cmd, *_ in TABS]
STATUS = {sid: dict({"state": st, "dot": DOT[st], "summary": st,
                     **({"activity_at": NOW - ago} if ago is not None else {})}, **extra)
          for sid, _, _, st, ago, extra in TABS}

STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]);
                            window.__bots.enabled = window.__cfg.settings.experimental_bots === true;
                            return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's201'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      default: return R('{}');
    }
  };
}})};
setInterval(() => { try { window.__sfAgentStatus && window.__sfAgentStatus(window.__status); } catch (e) {} }, 400);
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False),
       json.dumps(STATE, ensure_ascii=False), json.dumps(STATUS, ensure_ascii=False))

ORDER = [t[0] for t in TABS]
VISIBLE = """() => [...document.querySelectorAll('#sidebar-sessions .sb-item[data-section="local"][data-sid]')]
  .filter(r => !r.classList.contains('sfb-hide')).map(r => r.dataset.sid)"""
AGO = """sid => (document.querySelector(`#sidebar-sessions [data-sid="${sid}"] .sfb-ago`) || {}).textContent"""


def ready(page):
    page.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .sfb-ago').length >= 7"
                           " && !!document.getElementById('sfb-q')", timeout=30000)
    page.wait_for_function("""['s201:sig-working','s202:sig-decision','s203:sig-done'].every(p => {
        const [sid, c] = p.split(':');
        const d = document.querySelector(`#sidebar-sessions [data-sid="${sid}"] .busy-dot`);
        return d && d.classList.contains(c); })""", timeout=15000)
    page.wait_for_timeout(300)


def search(page, text):
    page.fill("#sfb-q", text)
    page.wait_for_timeout(150)
    return page.evaluate(VISIBLE)


def chip(page, name):
    page.click(f"#sfb-tools [data-view='{name}']")
    page.wait_for_timeout(250)


SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1180, "height": 1000}, device_scale_factor=2)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    ready(page)

    print("how long ago")
    ago = lambda sid: page.evaluate(AGO, sid)
    check("a running tab: just now", ago("s201") == "剛剛", ago("s201"))
    check("minutes", ago("s202") == "3 分前", ago("s202"))
    check("hours", ago("s203") == "2 小時前", ago("s203"))
    check("unknown (a shell with no output yet): left blank", ago("s206") == "", ago("s206"))
    # Measured after a restart: every reattached tab redraws its screen, and read "now".
    page.evaluate("window.__sfHost.sessions().s206._lastOutputTs = Date.now()")
    page.wait_for_timeout(1700)
    check("the redraw a tab sends when it is attached is not an answer", ago("s206") == "", ago("s206"))
    page.evaluate("window.__sfHost.sessions().s206._lastOutputTs = Date.now() + 60000")
    page.wait_for_timeout(1700)
    check("…output well after that is", ago("s206") == "剛剛", ago("s206"))
    page.evaluate("window.__sfHost.sessions().s206._lastOutputTs = 0")
    page.wait_for_timeout(1700)

    print("search")
    check("the box is there, with a hint of what it searches",
          page.get_attribute("#sfb-q", "placeholder") == "搜尋名稱、執掌、最新一句")
    calls_before = len(page.evaluate("window.__calls"))
    page.click("#sfb-q")
    got = search(page, "部署")
    check("a bot's job counts (Coding: 程式、測試、部署) and so does a name (部署檢查)",
          got == ["s201", "s204"], got)
    check("…and the count says how many of how many",
          page.text_content("#sfb-tools .sfb-count") == "2/7", page.text_content("#sfb-tools .sfb-count"))
    typed = [c[0] for c in page.evaluate("window.__calls")[calls_before:]]
    check("typing in the box never reaches a terminal",
          not any(c in ("write_input", "send_keys", "paste_text") for c in typed), typed)
    check("the box keeps the focus", page.evaluate("document.activeElement && document.activeElement.id") == "sfb-q")
    page.locator("#sidebar").screenshot(path=str(SHOTS / "bots-sidebar-tools-search.png"))
    got = search(page, "weekly")
    check("the latest line counts", got == ["s203"], got)
    got = search(page, "relay npm")
    check("several words: all of them", got == ["s207"], got)
    got = search(page, "日程")
    check("sleeping bots are searched too", page.evaluate(
        "[...document.querySelectorAll('.sfb-sleeper')].filter(r => !r.classList.contains('sfb-hide')).map(r => r.dataset.sfbRole)")
        == ["日程"] and got == [], got)
    search(page, "zzz-nothing")
    check("nothing matches: it says so", page.evaluate(
        "(document.querySelector('#sidebar-sessions .sfb-empty') || {}).textContent") == "沒有符合的分頁")
    page.press("#sfb-q", "Escape")
    page.wait_for_timeout(150)
    check("Esc clears it and every tab is back", page.evaluate(VISIBLE) == ORDER and page.input_value("#sfb-q") == "")

    print("idle")
    chip(page, "idle")
    got = page.evaluate(VISIBLE)
    check("「只看閒置」 hides what is running", "s201" not in got and "s207" not in got and len(got) == 5, got)
    check("…and turns on newest first", page.evaluate(
        "document.querySelector('#sfb-tools [data-view=recent]').classList.contains('on')"))
    check("…so the one that just answered is on top, the unknown last",
          got == ["s202", "s205", "s204", "s203", "s206"], got)
    check("your own order is untouched (it is also Telegram's /1 /2)", page.evaluate(
        "window.__sfHost.order()") == ORDER)
    page.locator("#sidebar").screenshot(path=str(SHOTS / "bots-sidebar-tools-idle.png"))
    chip(page, "long")
    got = page.evaluate(VISIBLE)
    check("「超過 30 分鐘」 keeps only the long-quiet ones", got == ["s204", "s203", "s206"], got)
    chip(page, "idle")
    check("turning idle off: everything, back in your own order",
          page.evaluate(VISIBLE) == ORDER and not page.evaluate(
              "document.querySelector('#sfb-tools [data-view=recent]').classList.contains('on')"),
          page.evaluate(VISIBLE))
    check("…and 「超過 30 分鐘」 went off with it", not page.evaluate(
        "document.querySelector('#sfb-tools [data-view=long]').classList.contains('on')"))

    print("newest first")
    chip(page, "recent")
    got = page.evaluate(VISIBLE)
    check("running tabs first (in your order), then by last answer",
          got == ["s201", "s207", "s202", "s205", "s204", "s203", "s206"], got)

    box = page.locator("#sidebar-sessions").bounding_box()
    page.evaluate("window.__status.s205 = Object.assign({}, window.__status.s205, {state: 'working', dot: 'sig-working'})")
    before = page.evaluate(VISIBLE)
    for i in range(12):                        # aiming: the pointer keeps moving over the list
        page.mouse.move(box["x"] + 40, box["y"] + 60 + (i % 3) * 30)
        page.wait_for_timeout(100)
    check("while the pointer moves over the list, rows stay put", page.evaluate(VISIBLE) == before,
          page.evaluate(VISIBLE))
    page.mouse.move(900, 500)
    page.wait_for_timeout(2200)
    got = page.evaluate(VISIBLE)
    check("…and move once it rests: a tab that started running goes up", got[:3] == ["s201", "s205", "s207"], got)
    page.evaluate("window.__status.s205 = Object.assign({}, window.__status.s205, {state: 'idle', dot: ''})")
    page.wait_for_timeout(1500)

    def drag(src, dst):
        a = page.locator(f"#sidebar-sessions [data-sid='{src}'] .sfb-name").bounding_box()
        b = page.locator(f"#sidebar-sessions [data-sid='{dst}'] .sfb-name").bounding_box()
        page.mouse.move(a["x"] + 10, a["y"] + a["height"] / 2)
        page.mouse.down()
        for i in range(1, 11):
            page.mouse.move(a["x"] + 10, a["y"] + (b["y"] - a["y"]) * i / 10 + a["height"] / 2)
        page.mouse.up()
        page.wait_for_timeout(300)
    drag("s206", "s201")
    check("while sorted by time, a drag does not reorder", page.evaluate("window.__sfHost.order()") == ORDER,
          page.evaluate("window.__sfHost.order()"))

    print("#tab name links")
    m = lambda text: page.evaluate("t => SFBots.tabMentions(t).map(h => h.sid + ':' + t.substr(h.index, h.length))", text)
    check("#name of a tab is a mention", m("請看 #Coding 和 #研究") == ["s201:#Coding", "s202:#研究"], m("請看 #Coding 和 #研究"))
    check("a name with a space", m("#relay 除錯 好了") == ["s207:#relay 除錯"], m("#relay 除錯 好了"))
    check("Chinese runs on without a space (#研究結果)", m("#研究結果出來了") == ["s202:#研究"], m("#研究結果出來了"))
    check("…but a Latin name does not run on into a word (#Codingx)", m("#Codingx") == [], m("#Codingx"))
    check("a # inside a URL or a word is not a mention", m("https://example.com/#Coding a#Coding") == [],
          m("https://example.com/#Coding a#Coding"))
    check("not a tab: nothing", m("#nothing #部署") == [], m("#nothing #部署"))
    check("the longest name wins", m("#部署檢查完成") == ["s204:#部署檢查"], m("#部署檢查完成"))

    page.evaluate("window.__sfHost.switchTab('s201')")
    page.wait_for_timeout(300)
    page.evaluate("window.__sfHost.sessions().s201.term.write('\\r\\n\\r\\n下一步：前往 #研究 看結果\\r\\n')")
    page.wait_for_timeout(500)
    spot = page.evaluate("""() => {
      const term = window.__sfHost.sessions().s201.term, buf = term.buffer.active;
      for (let y = buf.length - 1; y >= 0; y--) {
        const line = buf.getLine(y), text = line && line.translateToString(true);
        if (!text || !text.includes('#研究')) continue;
        let col = 0; const at = text.indexOf('#研究');
        for (let i = 0; i < at; i++) { const c = line.getCell(col); col += c && c.getWidth() > 1 ? 2 : 1; }
        const r = term.element.querySelector('.xterm-screen').getBoundingClientRect();
        const cw = r.width / term.cols, ch = r.height / term.rows;
        return {x: r.left + (col + 2) * cw, y: r.top + (y - buf.viewportY + 0.5) * ch};
      }
      return null; }""")
    if spot:
        page.mouse.move(spot["x"], spot["y"])
        page.wait_for_timeout(300)
        page.mouse.click(spot["x"], spot["y"])
        page.wait_for_timeout(400)
        check("a plain click on #研究 does not switch (it may be a text selection)",
              page.evaluate("window.__sfHost.active()") == "s201", page.evaluate("window.__sfHost.active()"))
        page.mouse.move(spot["x"] + 3, spot["y"] + 300)     # away, then back onto the name
        page.wait_for_timeout(200)
        page.keyboard.down("Meta")
        page.mouse.move(spot["x"] + 3, spot["y"], steps=5)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "bots-tab-link.png"),
                        clip={"x": max(0, spot["x"] - 260), "y": spot["y"] - 40, "width": 560, "height": 80})
        page.mouse.click(spot["x"] + 3, spot["y"])
        page.keyboard.up("Meta")
        page.wait_for_timeout(400)
        check("⌘-click on #研究 opens that tab", page.evaluate("window.__sfHost.active()") == "s202",
              page.evaluate("window.__sfHost.active()"))
    else:
        check("the mention was written to the terminal", False, "not found in the buffer")
    page.evaluate("window.__sfHost.switchTab('s201')")

    print("remembered")
    page.reload()
    ready(page)
    page.mouse.move(900, 500)               # the pointer rests away from the list
    page.wait_for_timeout(2000)
    check("after a reload the order choice is still on, sorted once the states arrive", page.evaluate(
        "document.querySelector('#sfb-tools [data-view=recent]').classList.contains('on')")
        and page.evaluate(VISIBLE)[:2] == ["s201", "s207"], page.evaluate(VISIBLE))
    chip(page, "recent")
    check("…and off again: your own order", page.evaluate(VISIBLE) == ORDER)
    check("no page errors", not errors, errors[:3])

    print("bots off")
    off = ctx.new_page()
    off.add_init_script(STUB.replace('"experimental_bots": true', '"experimental_bots": false')
                        .replace('"enabled": true', '"enabled": false'))
    off.goto((HERE / "web" / "index.html").as_uri())
    off.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid]').length >= 7", timeout=30000)
    off.wait_for_timeout(800)
    check("with bots off there are no tools and nothing is hidden", off.evaluate(
        "!document.querySelector('#sfb-tools') || getComputedStyle(document.querySelector('#sfb-tools')).display === 'none'")
        and off.evaluate("document.querySelectorAll('#sidebar-sessions .sfb-hide').length") == 0)

    print("english")
    en = ctx.new_page()
    en.add_init_script(STUB.replace('"language": "zh-TW"', '"language": "en"'))
    en.goto((HERE / "web" / "index.html").as_uri())
    ready(en)
    check("English words", en.get_attribute("#sfb-q", "placeholder") == "Search name, job, latest line"
          and en.text_content("#sfb-tools [data-view=idle]") == "Idle"
          and en.evaluate(AGO, "s202") == "3m")
    en.locator("#sidebar").screenshot(path=str(SHOTS / "bots-sidebar-tools-en.png"))
    browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
