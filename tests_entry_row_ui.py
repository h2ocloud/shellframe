#!/usr/bin/env python3
"""The entry row: 「Bots · 辦公室 · 看板 N」 (each with its icon) at the top of the sidebar (web/entry.js), in the real page with a
stand-in Python side. Demo data only.

Asked for: Bots, the office and the board were hard to find (an icon at the sidebar's foot, a switch in Settings).
  - the row is the first thing in the sidebar in the terminal view and sits right under 「＋ 新對話」 in the chat view,
    whether the features are on or off; an off feature is drawn dashed
  - 看板's count is SFBoard's; the board button is not at the foot any more (one board entry), nor are Bots / the office:
    the foot keeps 設定 · 導覽 · 給 AI · ?, with words beside the icons when they fit
  - a feature that is off opens an intro card (title, three bullets, a picture, 「打開」 / 「先不要」): 「打開」 saves the
    switch through save_settings and opens the feature, 「先不要」 / Esc / a click outside change nothing; on, the button just opens it
  - a narrow sidebar: icons with titles, nothing spills out
  - the tour after an update points at the three, once; a first install's tour includes them
Run in Chromium, and in WebKit when playwright has it. Screenshots (Chromium): qa-shots/q69-impl-*.png.
Run: .venv/bin/python tests_entry_row_ui.py
"""
import copy
import json
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guide  # noqa: E402

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
D31 = H.T_DECISION
D32 = task(32, "示範要不要加班趕工", "waiting", "user", "eng", "user", "等你回一句", None, 800, "decision")
D33 = task(33, "示範週報要寄給誰", "waiting", "user", "chief", "user", "等你點頭", None, 500, "decision")
TASKS = {"success": True, "decisions": [D31, D32, D33], "stuck": [], "open": [D31, D32, D33]}
DATA = guide.load()
BOTS0, CFG0 = copy.deepcopy(H.BOTS), copy.deepcopy(H.CFG)      # every page starts from these
VERSION = guide.app_version()
SHOTS = HERE / "qa-shots"

# the stand-in saves like the real one (the page's whole settings) and bots_state follows the saved switch
SAVE_OLD = "      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);"
SAVE_NEW = ("      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); window.__bots.enabled = window.__cfg.settings.experimental_bots === true; "
            "return R(window.__cfg);")


def stub(guide_state=None):
    s = H.stub()
    assert s.count(SAVE_OLD) == 1
    s = s.replace(SAVE_OLD, SAVE_NEW)
    st = guide_state or {"tour": "done", "seen": VERSION}
    gdata = {"success": True, "features": guide.for_surface("desktop", H.CFG["settings"], DATA), "tours": DATA.get("tours"),
             "state": dict(st, version=VERSION)}
    return s + "\nif (window.top === window) window.__guide = %s;\n" % json.dumps(gdata, ensure_ascii=False)


def new_page(browser, errors, bots=True, office=True, style="cli", lang="zh-TW", width=1280, height=800, roster=True, guide_state=None):
    cfg = copy.deepcopy(CFG0)
    cfg["settings"].update({"experimental_bots": bots, "experimental_office": office, "ui_style": style, "language": lang})
    H.CFG = cfg
    H.TASKS = copy.deepcopy(TASKS)
    H.PEERS = {"peers": {}}
    bots_state = copy.deepcopy(BOTS0)
    bots_state["enabled"] = bots
    if not roster:
        bots_state.update(roster={}, primary="", sessions={})
    H.BOTS = bots_state
    page = browser.new_page(viewport={"width": width, "height": height})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.add_init_script(stub(guide_state))
    return page


def start(page, base):
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-entry #btn-taskboard", timeout=20000)
    page.wait_for_function("window.SFBoard && window.SFBots && window.__sfHost && Object.keys(window.__sfHost.sessions()).length >= 3", timeout=20000)
    page.wait_for_function("SFBoard.count() === 3", timeout=10000)
    page.wait_for_timeout(500)


def saves(page):
    return page.evaluate("window.__args.filter(a => a[0] === 'save_settings').map(a => JSON.parse(a[1][0]))")


def labels_shown(page):
    return page.evaluate("""() => [...document.querySelectorAll('#sb-entry .sfe-label')].map(e => e.getBoundingClientRect().width > 0)""")


def spills(page, sel_row, sel_btns):
    return page.evaluate("""([row, btns]) => { const r = document.querySelector(row).getBoundingClientRect(), sb = document.getElementById('sidebar').getBoundingClientRect();
      return [...document.querySelectorAll(btns)].filter(b => { const x = b.getBoundingClientRect(); return x.width > 0 && (x.right > sb.right + 0.5 || x.left < sb.left - 0.5 || x.right > r.right + 1.5); }).map(b => b.id); }""",
                         [sel_row, sel_btns])


def run(browser, base, engine):
    shoot = engine == "chromium"
    errors = []

    print(f"[{engine}] terminal view, Bots on, office off: the row is first")
    page = new_page(browser, errors, bots=True, office=False, style="cli")
    start(page, base)
    ids = page.evaluate("[...document.querySelectorAll('#sb-entry button')].map(b => b.id)")
    check("three buttons: Bots, 辦公室, 看板", ids == ["btn-bots", "btn-office", "btn-taskboard"], ids)
    check("the row is the first thing in the sidebar", page.evaluate("document.getElementById('sidebar').firstElementChild.id") == "sb-entry")
    check("…above the sessions", page.evaluate("document.getElementById('sb-entry').getBoundingClientRect().bottom <= document.querySelector('#sidebar .sb-sessions-head').getBoundingClientRect().top + 1"))
    words = page.evaluate("[...document.querySelectorAll('#sb-entry .sfe-label')].map(e => e.textContent)")
    check("labels: Bots / 辦公室 / 看板", words == ["Bots", "辦公室", "看板"], words)
    check("at 220 px the labels fit and show (zh)", all(labels_shown(page)), labels_shown(page))
    icons = page.evaluate("[...document.querySelectorAll('#sb-entry .sfe-btn')].map(b => { const s = b.querySelector('.sfe-ico svg.sfi'), r = s && s.getBoundingClientRect(); return [!!s, !!r && r.width > 8 && r.height > 8, b.querySelector('.sfe-label').getBoundingClientRect().width > 0]; })")
    check("default width: every button shows its drawn icon AND its word (sfe-words)", icons == [[True, True, True]] * 3
          and page.evaluate("document.getElementById('sb-entry').classList.contains('sfe-words')"), icons)
    check("Bots on: solid; the office is off: dashed", page.evaluate("!document.getElementById('btn-bots').classList.contains('off') && document.getElementById('btn-office').classList.contains('off')"))
    check("…and the dashed one says it is off in its title", "未開啟" in page.get_attribute("#btn-office", "title"))
    badge = page.evaluate("(() => { const b = document.querySelector('#btn-taskboard .sfb-badge'); return b && !b.hidden ? b.textContent : ''; })()")
    check("看板's count is SFBoard's (3)", badge == "3" and page.evaluate("SFBoard.count()") == 3, badge)
    check("the badge sits inline after the word (labels mode)", page.evaluate("""() => { const l = document.querySelector('#btn-taskboard .sfe-label').getBoundingClientRect(), b = document.querySelector('#btn-taskboard .sfb-badge').getBoundingClientRect();
        return b.left >= l.right - 1 && Math.abs((b.top + b.bottom) / 2 - (l.top + l.bottom) / 2) < 8; }"""))
    check("one board entry: #btn-taskboard exists once and not in the foot", page.evaluate("document.querySelectorAll('#btn-taskboard').length") == 1
          and page.evaluate("!document.querySelector('#sb-footer-row #btn-taskboard')"))
    foot = page.evaluate("[...document.querySelectorAll('#sb-footer-row > button')].map(b => b.id)")
    check("the foot keeps 設定 · 導覽 · 給 AI · ?", foot == ["btn-settings", "btn-guide", "btn-ai-skill", "btn-about"], foot)
    fw = page.evaluate("[...document.querySelectorAll('#sb-footer-row .sb-word')].map(e => [e.textContent, e.getBoundingClientRect().width > 0])")
    check("the foot's icons get words beside them: 導覽, 給 AI", fw == [["導覽", True], ["給 AI", True]], fw)
    check("…the ? stays an icon with its name on hover", page.evaluate("document.getElementById('btn-about').title") == "關於")
    check("the foot does not spill at 220 px", not spills(page, "#sb-footer-row", "#sb-footer-row > button"), spills(page, "#sb-footer-row", "#sb-footer-row > button"))
    check("the guide mark is on each (tour points)", page.evaluate("['entry-bots','entry-office','taskboard'].every(g => document.querySelector('#sb-entry [data-guide=' + g + ']'))"))
    if shoot:
        page.screenshot(path=str(SHOTS / "q69-impl-sidebar-terminal-1280.png"))

    print(f"[{engine}] pressing Bots (on)")
    page.click("#btn-bots")
    page.wait_for_function("document.querySelector('#sidebar .sfb-section').classList.contains('sfe-flash')", timeout=3000, polling=50)
    check("shows the BOTS section lit, no dialog", page.evaluate("!document.getElementById('sfe-intro') && !document.querySelector('.sfb-modal')"))
    check("…saves nothing", not saves(page))
    page.evaluate("SFBots.newBot = () => { window.__newBot = true; }; SFBots.state().roster = {}")
    page.click("#btn-bots")
    check("with no bots at all it starts the 「＋ Bot」 flow", page.evaluate("window.__newBot === true"))
    page.close()

    print(f"[{engine}] switches off: the row is still there, dashed")
    page = new_page(browser, errors, bots=False, office=False, style="cli")
    start(page, base)
    check("both dashed", page.evaluate("['btn-bots','btn-office'].every(i => document.getElementById(i).classList.contains('off'))"))
    check("no office button at the foot, no Bots one", page.evaluate("!document.querySelector('#sb-footer-row #btn-office, #sb-footer-row #btn-bots')"))
    check("看板 is solid (it needs no switch)", page.evaluate("!document.getElementById('btn-taskboard').classList.contains('off')"))

    print(f"[{engine}] the office's intro card")
    page.click("#btn-office")
    page.wait_for_selector("#sfe-intro .sfe-card", timeout=3000)
    check("a card, modal, centred", page.evaluate("""() => { const c = document.querySelector('#sfe-intro .sfe-card').getBoundingClientRect();
        return document.querySelector('#sfe-intro .sfe-card').getAttribute('aria-modal') === 'true' && Math.abs((c.left + c.right) / 2 - innerWidth / 2) < 2 && Math.abs((c.top + c.bottom) / 2 - innerHeight / 2) < 40; }"""))
    check("title 辦公室 + 未開啟", page.locator("#sfe-title svg.sfi").count() == 1 and "辦公室" in page.inner_text("#sfe-title") and "未開啟" in page.inner_text("#sfe-title"))
    check("three bullets", page.evaluate("document.querySelectorAll('#sfe-intro .sfe-list li').length") == 3)
    page.wait_for_function("document.querySelector('#sfe-intro .sfe-pic').complete", timeout=5000)
    check("one picture, loaded", page.evaluate("document.querySelectorAll('#sfe-intro img').length") == 1 and page.evaluate("document.querySelector('#sfe-intro .sfe-pic').naturalWidth") > 100)
    check("buttons 先不要 / 打開", page.evaluate("[...document.querySelectorAll('#sfe-intro .sfe-acts button')].map(b => b.textContent)") == ["先不要", "打開"])
    check("focus is on 打開", page.evaluate("document.activeElement.textContent") == "打開")
    check("a way to turn it off again is named", "設定" in page.inner_text("#sfe-intro .sfe-note"))
    if shoot:
        page.screenshot(path=str(SHOTS / "q69-impl-intro-office-1280.png"))
    page.click('#sfe-intro [data-act="no"]')
    check("先不要: the card goes, nothing saved, still off, the office did not open",
          page.evaluate("!document.getElementById('sfe-intro')") and not saves(page)
          and page.evaluate("window.__cfg.settings.experimental_office") is False and not page.evaluate("SFOffice.isOpen()"))
    check("…focus returns to the button", page.evaluate("document.activeElement.id") == "btn-office")
    page.click("#btn-office")
    page.wait_for_selector("#sfe-intro")
    page.keyboard.press("Escape")
    check("Esc is 先不要", page.evaluate("!document.getElementById('sfe-intro')") and not saves(page))
    page.click("#btn-office")
    page.wait_for_selector("#sfe-intro")
    page.mouse.click(8, 8)
    check("a click outside the card is 先不要", page.evaluate("!document.getElementById('sfe-intro')") and not saves(page))
    page.click("#btn-office")
    page.wait_for_selector("#sfe-intro")
    page.click('#sfe-intro [data-act="yes"]')
    page.wait_for_function("SFOffice.isOpen()", timeout=8000)
    sv = saves(page)
    check("打開: the switch is saved through save_settings (the page's whole settings, experimental_office true, the rest kept)",
          len(sv) == 1 and sv[0].get("experimental_office") is True and sv[0].get("language") == "zh-TW" and sv[0].get("experimental_bots") is False, sv)
    check("…the office opens, the card is gone, the button is solid", page.evaluate("!document.getElementById('sfe-intro') && !document.getElementById('btn-office').classList.contains('off')"))
    page.evaluate("SFOffice.close()")
    page.click("#btn-office")
    page.wait_for_function("SFOffice.isOpen()", timeout=8000)
    check("on: the button just opens it (no card, no second save)", page.evaluate("!document.getElementById('sfe-intro')") and len(saves(page)) == 1)
    page.evaluate("SFOffice.close()")

    print(f"[{engine}] Bots' intro card")
    page.click("#btn-bots")
    page.wait_for_selector("#sfe-intro .sfe-card")
    page.wait_for_function("document.querySelector('#sfe-intro .sfe-pic').complete", timeout=5000)
    check("title Bots + 未開啟, three bullets, a picture", "Bots" in page.inner_text("#sfe-title") and "未開啟" in page.inner_text("#sfe-title")
          and page.evaluate("document.querySelectorAll('#sfe-intro .sfe-list li').length") == 3 and page.evaluate("document.querySelector('#sfe-intro .sfe-pic').naturalWidth") > 100)
    if shoot:
        page.screenshot(path=str(SHOTS / "q69-impl-intro-bots-1280.png"))
    page.click('#sfe-intro [data-act="no"]')
    check("先不要 leaves Bots off", page.evaluate("window.__cfg.settings.experimental_bots") is False and not page.evaluate("SFBots.enabled()") and len(saves(page)) == 1)
    page.click("#btn-bots")
    page.wait_for_selector("#sfe-intro")
    page.click('#sfe-intro [data-act="yes"]')
    page.wait_for_function("SFBots.enabled()", timeout=8000)
    sv = saves(page)
    check("打開: experimental_bots saved true (office stays as it was), Bots is on", len(sv) == 2 and sv[1].get("experimental_bots") is True and sv[1].get("experimental_office") is True, sv)
    page.wait_for_function("document.querySelector('#sidebar .sfb-section') && document.querySelector('#sidebar .sfb-section').classList.contains('sfe-flash')", timeout=5000, polling=50)
    check("…and the BOTS section is revealed", page.evaluate("!document.getElementById('sfe-intro') && !document.getElementById('btn-bots').classList.contains('off')"))
    page.close()

    print(f"[{engine}] Bots turned on with no bots yet: the + Bot flow")
    page = new_page(browser, errors, bots=False, office=True, style="cli", roster=False)
    start(page, base)
    page.click("#btn-bots")
    page.wait_for_selector("#sfe-intro")
    page.click('#sfe-intro [data-act="yes"]')
    page.wait_for_selector(".sfb-modal", timeout=8000)
    check("the bot editor opens", page.evaluate("!!document.querySelector('.sfb-modal')") and page.evaluate("SFBots.enabled()"))
    page.close()

    print(f"[{engine}] a save that fails: the card stays, the switch is not left half on")
    page = new_page(browser, errors, bots=False, office=False, style="cli")
    start(page, base)
    page.evaluate("""() => { const a = window.pywebview.api, f = a.save_settings; window.pywebview = { api: new Proxy(a, { get: (t, p) => p === 'save_settings' ? () => Promise.reject(new Error('disk')) : a[p] }) }; }""")
    page.click("#btn-office")
    page.wait_for_selector("#sfe-intro")
    page.click('#sfe-intro [data-act="yes"]')
    page.wait_for_selector("#sfe-intro .sfe-err:not([hidden])", timeout=3000)
    check("an error line, the buttons usable again, nothing turned on", page.evaluate("document.querySelector('#sfe-intro [data-act=yes]').disabled") is False
          and page.evaluate("__sfHost.cfg().settings.experimental_office") is False and not page.evaluate("SFOffice.isOpen()"))
    page.close()

    print(f"[{engine}] chat view: right under 「＋ 新對話」")
    page = new_page(browser, errors, bots=True, office=False, style="chat")
    start(page, base)
    page.wait_for_selector("#sb-new-convo", state="visible", timeout=10000)
    order = page.evaluate("[...document.getElementById('sidebar').children].filter(e => e.getBoundingClientRect().height > 0).sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top).map(e => e.id || e.className)")
    check("order: 新對話, then the row", order[:2] == ["sb-new-convo", "sb-entry"], order)
    check("the same three buttons, the count the same", page.evaluate("[...document.querySelectorAll('#sb-entry button')].map(b => b.id)") == ["btn-bots", "btn-office", "btn-taskboard"]
          and page.evaluate("document.querySelector('#btn-taskboard .sfb-badge').textContent") == "3")
    check("the office is dashed there too", page.evaluate("document.getElementById('btn-office').classList.contains('off')"))
    check("the foot's words are there in the chat view too", page.evaluate("document.querySelectorAll('#sb-footer-row .sb-word').length") == 2)
    if shoot:
        page.screenshot(path=str(SHOTS / "q69-impl-sidebar-chat-1280.png"))
    page.click("#btn-taskboard")
    page.wait_for_selector("#sfo-overlay", timeout=8000)
    check("看板 in the row opens the board sheet (the same toggle as before)", page.evaluate("SFOffice.isBoard()"))
    page.keyboard.press("Escape")
    page.close()

    print(f"[{engine}] both views x switches on / off: the row is always there, the count is the same")
    for style in ("cli", "chat"):
        for on in (True, False):
            page = new_page(browser, errors, bots=on, office=on, style=style)
            start(page, base)
            tag = f"{style} view, switches {'on' if on else 'off'}"
            ids = page.evaluate("[...document.querySelectorAll('#sb-entry button')].map(b => b.id)")
            check(f"{tag}: the three buttons are there, in order", ids == ["btn-bots", "btn-office", "btn-taskboard"], ids)
            dashed = page.evaluate("['btn-bots', 'btn-office'].map(i => document.getElementById(i).classList.contains('off'))")
            check(f"{tag}: Bots and 辦公室 are {'solid' if on else 'dashed'}, 看板 always solid", dashed == [not on, not on]
                  and page.evaluate("!document.getElementById('btn-taskboard').classList.contains('off')"), dashed)
            check(f"{tag}: 看板's count equals SFBoard's", page.evaluate("document.querySelector('#btn-taskboard .sfb-badge').textContent") == str(page.evaluate("SFBoard.count()")) == "3")
            top = page.evaluate("document.getElementById('sb-entry').getBoundingClientRect().top")
            if style == "chat":
                page.wait_for_selector("#sb-new-convo", state="visible", timeout=10000)
                nb = page.evaluate("document.getElementById('sb-new-convo').getBoundingClientRect().bottom")
                check(f"{tag}: right under 「＋ 新對話」", 0 <= page.evaluate("document.getElementById('sb-entry').getBoundingClientRect().top") - nb < 14)
                check(f"{tag}: 用量 stays in the dock above the foot, not in the row", page.evaluate("!!document.querySelector('#sb-dock #usage-brief')")
                      and page.evaluate("!document.querySelector('#sb-entry #usage-brief, #sb-footer-row #usage-brief')"))
            else:
                check(f"{tag}: the row is at the very top of the sidebar", top <= page.evaluate("document.getElementById('sidebar').getBoundingClientRect().top") + 12)
            page.close()

    print(f"[{engine}] a narrow sidebar: icons with titles, nothing spills")
    page = new_page(browser, errors, bots=True, office=False, style="cli", width=900, height=700)
    start(page, base)
    if shoot:
        page.screenshot(path=str(SHOTS / "q69-impl-sidebar-900.png"))
    page.evaluate("document.getElementById('sidebar').style.setProperty('--sidebar-w', '170px')")
    page.wait_for_timeout(500)
    check("170 px: the row is icons only", not any(labels_shown(page)) and page.evaluate("!document.getElementById('sb-entry').classList.contains('sfe-words')"), labels_shown(page))
    tips = page.evaluate("[...document.querySelectorAll('#sb-entry button')].map(b => [b.title, b.getAttribute('aria-label')])")
    check("…each with a tooltip and an accessible name", all(a and a == b for a, b in tips) and "看板" in tips[2][0] and "3" in tips[2][0], tips)
    check("…the 看板 count rides the corner of its icon", page.evaluate("""() => { const b = document.querySelector('#btn-taskboard').getBoundingClientRect(), c = document.querySelector('#btn-taskboard .sfb-badge').getBoundingClientRect();
        return c.width > 0 && c.right > b.right - 12 && c.top < b.top + 6; }"""))
    check("…nothing in the row spills (the count's corner stays inside the sidebar)", not spills(page, "#sb-entry .sfe-row", "#sb-entry button") and page.evaluate(
        "document.querySelector('#btn-taskboard .sfb-badge').getBoundingClientRect().right <= document.getElementById('sidebar').getBoundingClientRect().right"))
    check("…the foot is icons too (no words), nothing spills", page.evaluate("[...document.querySelectorAll('#sb-footer-row .sb-word')].every(e => e.getBoundingClientRect().width === 0)")
          and not spills(page, "#sb-footer-row", "#sb-footer-row > button"))
    ft = page.evaluate("['btn-guide','btn-ai-skill','btn-about'].map(i => document.getElementById(i).title)")
    check("…and the foot's icons keep their tooltips", all(ft), ft)
    page.evaluate("document.getElementById('sidebar').style.setProperty('--sidebar-w', '300px')")
    page.wait_for_timeout(500)
    check("widening it brings the words back", all(labels_shown(page)))
    page.evaluate("document.getElementById('sidebar').style.setProperty('--sidebar-w', '184px')")
    page.wait_for_timeout(500)
    check("184 px (under the line): icons only", not any(labels_shown(page)) and not spills(page, "#sb-entry .sfe-row", "#sb-entry button"))
    page.evaluate("document.getElementById('sidebar').style.setProperty('--sidebar-w', '190px')")
    page.wait_for_timeout(500)
    check("190 px: the words are back, without the icons (they would not fit with them); nothing spills",
          all(labels_shown(page)) and page.evaluate("document.getElementById('sb-entry').classList.contains('sfe-text')")
          and page.evaluate("[...document.querySelectorAll('#sb-entry .sfe-ico')].every(e => e.getBoundingClientRect().width === 0)")
          and not spills(page, "#sb-entry .sfe-row", "#sb-entry button, #sb-entry .sfb-badge"))
    page.close()

    print(f"[{engine}] English at 220 px")
    page = new_page(browser, errors, bots=True, office=False, style="cli", lang="en")
    start(page, base)
    words = page.evaluate("[...document.querySelectorAll('#sb-entry .sfe-label')].map(e => e.textContent)")
    check("labels: Bots / Office / Board", words == ["Bots", "Office", "Board"], words)
    check("the labels show (icons and words, or the words alone), nothing spills, row and foot", all(labels_shown(page))
          and not spills(page, "#sb-entry .sfe-row", "#sb-entry button, #sb-entry .sfb-badge") and not spills(page, "#sb-footer-row", "#sb-footer-row > button"))
    page.click("#btn-office")
    page.wait_for_selector("#sfe-intro")
    check("the card speaks English", "Not now" in page.inner_text("#sfe-intro .sfe-acts") and "Turn on" in page.inner_text("#sfe-intro .sfe-acts"))
    page.close()

    print(f"[{engine}] the tour after an update")
    page = new_page(browser, errors, guide_state={"tour": "done", "seen": "0.57.0"})
    start(page, base)
    page.wait_for_selector(".sfg-layer .sfg-card", timeout=15000)
    steps = []
    while True:
        card = page.inner_text(".sfg-card")
        steps.append((card.split("\n")[0], page.inner_text(".sfg-step")))
        if page.evaluate("document.querySelector('.sfg-btn.primary').textContent") in ("完成", "Done"):
            break
        if shoot and len(steps) == 1:
            page.screenshot(path=str(SHOTS / "q69-impl-tour-1280.png"))
        page.click(".sfg-btn.primary")
        page.wait_for_function("(n) => !document.querySelector('.sfg-card') || document.querySelector('.sfg-step').textContent !== n", arg=steps[-1][1], timeout=5000)
        page.wait_for_timeout(300)
    check("three steps: Bots, 辦公室, 看板", [s[0] for s in steps] == ["Bots", "辦公室", "看板"] and [s[1] for s in steps] == ["1 / 3", "2 / 3", "3 / 3"], steps)
    spot = page.evaluate("(() => { const r = document.querySelector('.sfg-spot').getBoundingClientRect(), b = document.querySelector('#btn-taskboard').getBoundingClientRect(); return Math.abs(r.left - (b.left - 6)) < 2 && Math.abs(r.top - (b.top - 6)) < 2; })()")
    check("the last spotlight is on the board button", spot)
    page.click(".sfg-btn.primary")
    page.wait_for_function("!document.querySelector('.sfg-layer')", timeout=3000)
    marks = page.evaluate("window.__args.filter(a => a[0] === 'guide_mark').map(a => a[1])")
    check("its end is recorded (tour done, this version seen)", marks and marks[-1] == ["done", True], marks)
    page.close()

    page = new_page(browser, errors, guide_state={"tour": "done", "seen": VERSION})
    start(page, base)
    page.wait_for_timeout(3000)
    check("seen already (the running version): the tour does not come again", page.evaluate("!document.querySelector('.sfg-layer')"))
    page.close()

    page = new_page(browser, errors, guide_state={"tour": "done", "seen": "0.57.0"})
    start(page, base)
    page.wait_for_selector(".sfg-layer .sfg-card", timeout=15000)
    page.click(".sfg-btn:not(.primary)")                      # Skip
    page.wait_for_function("!document.querySelector('.sfg-layer')", timeout=3000)
    marks = page.evaluate("window.__args.filter(a => a[0] === 'guide_mark').map(a => a[1])")
    check("Skip ends it and records it the same", marks and marks[-1] == ["done", True], marks)
    page.close()

    page = new_page(browser, errors, guide_state={"tour": "pending", "seen": ""})
    start(page, base)
    page.wait_for_selector(".sfg-layer .sfg-card", timeout=15000)
    titles = []
    for _ in range(14):
        titles.append(page.inner_text(".sfg-card .sfg-title").split("\n")[0])
        if page.evaluate("document.querySelector('.sfg-btn.primary').textContent") in ("完成", "Done"):
            break
        cur = page.inner_text(".sfg-step")
        page.click(".sfg-btn.primary")
        page.wait_for_function("(n) => !document.querySelector('.sfg-card') || document.querySelector('.sfg-step').textContent !== n", arg=cur, timeout=5000)
        page.wait_for_timeout(250)
    check("a first install's tour includes Bots, 辦公室, 看板 right after the first step", titles[1:4] == ["Bots", "辦公室", "看板"], titles)
    page.close()

    print(f"[{engine}] the Guide's list names them")
    page = new_page(browser, errors, guide_state={"tour": "done", "seen": VERSION})
    start(page, base)
    page.click("#btn-guide")
    page.wait_for_selector(".sfg-help")
    rows = page.evaluate("[...document.querySelectorAll('.sfg-row-title')].map(e => e.textContent)")
    check("Bots, 辦公室 and 看板 are in the list", all(x in rows for x in ("Bots", "辦公室", "看板")), rows)
    page.close()

    check("no page error or console error", not errors, errors[:5])


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
