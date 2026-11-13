#!/usr/bin/env python3
"""A bot's own skills in its editor, and saving memory while a bot stays open in Settings → Bots, in the real
page: web/index.html in a browser with a stand-in Python side (the real rules are in tests_bot_skills.py and
tests_bot_memory.py).

  - the editor's Skills cell shows the shared skills it always did and, under them, the bot's own: name, what
    it is for, a tag when it replaces a shared one, a folder button and Delete on each row
  - a blank skill (a name is asked for first), a copy of a shared skill, the folder (the skills folder, or one
    skill's), delete (asked first, in the page's own question; "no" changes nothing)
  - a bot that does not exist yet and a bot outside ShellBot say where its skills go instead of offering them
  - Settings → Bots: the memory-saving switch (on by default) and the idle minutes (5 to 1440), both saved
    with the other settings, and the words that say it uses quota

Demo data only. Screenshots (WebKit only) go to qa-shots/botskills-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_bot_skills_ui.py
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
    print("SKIP  tests_bot_skills_ui.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {"助理": {"label": "助理", "responsibility": "日常任務的第一站", "cmd": "claude", "model": "", "coordinator": False,
                 "former_names": [], "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True,
                                              "skills": ["demo-review"]}},
          "寫程式": {"label": "寫程式", "responsibility": "程式與測試", "cmd": "claude", "model": "", "coordinator": False,
                  "former_names": [], "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True, "skills": []}},
          "外部": {"label": "外部", "cmd": "zsh", "bot": {"external": {"kind": "webhook", "url_set": True, "auth_set": False,
                                                                  "url_mask": "https://example.com/***"}}}}
STATE = {"enabled": True, "roster": ROSTER, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
         "sessions": {}, "kinds": {"s4": "terminal"}}
CFG = {"settings": {"experimental_bots": True, "language": "%LANG%", "sidebar_open": True, "ui_style": "cli"},
       "presets": [{"name": "Claude Code", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SHARED = [{"name": "demo-review", "description": "check a change before it goes in"},
          {"name": "demo-weekly", "description": "write the weekly report"},
          {"name": "發版清單", "description": "共用的發版清單"}]
OWN = {"助理": [{"name": "發版清單", "folder": "發版清單", "description": "發版前逐項檢查，只有助理用", "path": "/demo/bots/助理/skills/發版清單/SKILL.md", "shadows": True},
              {"name": "daily-notes", "folder": "daily-notes", "description": "keep the day's notes in one file", "path": "/demo/bots/助理/skills/daily-notes/SKILL.md", "shadows": False}],
       "寫程式": []}

STUB = """
window.__calls = [];
window.__cfg = %s; window.__bots = %s; window.__own = %s; window.__shared = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
const own = role => (window.__own[role] = window.__own[role] || []);
const row = (name, folder, desc) => ({name, folder, description: desc, path: '/demo/bots/x/skills/' + folder + '/SKILL.md', shadows: window.__shared.some(k => k.name === name)});
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R([{sid: 's4', cmd: 'zsh', alive: true, label: 'zsh', bridge_enabled: true}]);
      case 'get_active_tab': return R({sid: 's4'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'bots_skill_catalog': return R({skills: window.__shared});
      case 'bots_model_info': return R({choices: {}, by_cmd: {}});
      case 'bots_own_skills': return R({success: true, skills: own(args[0]), dir: '/demo/bots/' + args[0] + '/skills'});
      case 'bots_skill_new': { const f = args[1].trim().replace(/\\s+/g, '-'); own(args[0]).push(row(f, f, args[2] || ''));
        return R({success: true, message: '已新增「' + f + '」；請把它的 SKILL.md 填好', folder: f}); }
      case 'bots_skill_copy': { const k = window.__shared.find(x => x.name === args[1]);
        if (own(args[0]).some(x => x.folder === args[1])) return R({success: false, code: 'exists', message: '「' + args[1] + '」已經是它的 skill'});
        own(args[0]).push(row(k.name, k.name, k.description)); return R({success: true, message: '已把「' + k.name + '」複製成它自己的 skill', folder: k.name}); }
      case 'bots_skill_delete': window.__own[args[0]] = own(args[0]).filter(x => x.folder !== args[1]);
        return R({success: true, message: '已刪除「' + args[1] + '」'});
      case 'bots_skill_open': return R({success: true, message: '/demo'});
      default: return R('{}');
    }
  };
}})};
"""

CALLS = "window.__calls.map(c => c[0])"


def calls(page, name):
    return page.evaluate("(n) => window.__calls.filter(c => c[0] === n).map(c => c[1])", name)


def open_page(browser, lang):
    cfg = json.loads(json.dumps(CFG).replace("%LANG%", lang))
    page = browser.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, STATE, OWN, SHARED)))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_timeout(500)
    return page, errors


def settings_bots(page):
    page.click("#btn-settings")
    page.wait_for_timeout(250)
    page.click('#settings-tabs-bar [data-tab="bots"]')
    page.wait_for_function("document.querySelectorAll('#sfb-settings-list .sfb-bl-row').length >= 2", timeout=15000)


def open_editor(page, role):
    page.locator(f'#sfb-settings-list [data-sfb-edit="{role}"]').click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    page.wait_for_selector("#sfb-ed-own .sfb-own-add, #sfb-ed-own .sfb-own-empty")


def close_editor(page):
    page.click(".sfb-modal [data-act='cancel']")
    page.wait_for_selector(".sfb-modal", state="detached")


def rows(page):
    return page.evaluate("[...document.querySelectorAll('#sfb-ed-own .sfb-own-row')].map(r => ({name: r.querySelector('b').textContent, "
                         "tag: !!r.querySelector('.tag'), desc: r.querySelector('.who span').textContent}))")


def run(browser, engine):
    shot = lambda loc, name: loc.screenshot(path=str(SHOTS / f"botskills-{name}-{engine}.png")) if engine == "webkit" else None

    print(f"[{engine}] 1. Settings → Bots: saving memory while a bot stays open")
    page, errors = open_page(browser, "zh-TW")
    settings_bots(page)
    check("the switch is there and on (on unless turned off), with 45 minutes",
          page.locator("#setting-bot-memory-autosave.on").count() == 1 and page.input_value("#setting-bot-memory-idle") == "45")
    text = page.inner_text("#sfb-memsave")
    check("it says what it does and that each time costs a turn of quota", "記憶" in text and "一輪對話" in text and "額度" in text
          and "輸入框有你還沒送出的字" in text, text)
    shot(page.locator("#modal-settings .modal"), "settings")
    page.click("#setting-bot-memory-autosave")
    page.wait_for_timeout(150)
    saved = page.evaluate("window.__cfg.settings")
    check("off: saved with the other settings, nothing else lost", saved.get("bot_memory_autosave") is False
          and saved.get("experimental_bots") is True and saved.get("language") == "zh-TW", saved)
    check("…the minutes are greyed", page.is_disabled("#setting-bot-memory-idle"))
    page.click("#setting-bot-memory-autosave")
    page.wait_for_timeout(150)
    check("…and on again", page.evaluate("window.__cfg.settings.bot_memory_autosave") is True and page.locator("#setting-bot-memory-autosave.on").count() == 1)
    page.fill("#setting-bot-memory-idle", "90")
    page.dispatch_event("#setting-bot-memory-idle", "change")
    page.wait_for_timeout(150)
    check("90 minutes is saved", page.evaluate("window.__cfg.settings.bot_memory_idle_min") == 90)
    for typed, want in (("1", 5), ("99999", 1440), ("", 45)):
        page.fill("#setting-bot-memory-idle", typed)
        page.dispatch_event("#setting-bot-memory-idle", "change")
        page.wait_for_timeout(120)
        check(f"typed {typed!r}: kept between 5 and 1440 (or 45), in the box too",
              page.evaluate("window.__cfg.settings.bot_memory_idle_min") == want and page.input_value("#setting-bot-memory-idle") == str(want),
              (page.evaluate("window.__cfg.settings.bot_memory_idle_min"), page.input_value("#setting-bot-memory-idle")))
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 2. the editor's Skills cell: a bot's own skills")
    page, errors = open_page(browser, "zh-TW")
    settings_bots(page)
    open_editor(page, "助理")
    labels = page.evaluate("[...document.querySelectorAll('.sfb-modal .sfb-form > label')].map(l => l.textContent.trim()).slice(0, 6).join('|')")
    check("the row is still Skills, between model and face (the own skills are in its cell)", labels == "名稱|執掌|CLI|模型|Skills|頭像", labels)
    check("the shared skills are there as before", page.locator("#sfb-ed-skills input").count() == 3
          and page.locator('#sfb-ed-skills input[value="demo-review"]:checked').count() == 1)
    got = rows(page)
    check("its own skills are listed: name and what it is for", [r["name"] for r in got] == ["發版清單", "daily-notes"]
          and got[1]["desc"] == "keep the day's notes in one file", got)
    check("…the one that has the name of a shared skill says it replaces it", got[0]["tag"] and not got[1]["tag"], got)
    check("…each has a folder button and Delete; the add row has a name box, Blank, a list to copy from, Copy and the folder",
          page.locator('#sfb-ed-own [data-own="open"]').count() == 2 and page.locator('#sfb-ed-own [data-own="delete"]').count() == 2
          and all(page.locator(f'#sfb-ed-own {s}').count() == 1 for s in ('#sfb-own-name', '[data-own="new"]', '#sfb-own-from', '[data-own="copy"]', '[data-own="folder"]')))
    check("…the shared list offers the shared skills", page.evaluate("[...document.querySelectorAll('#sfb-own-from option')].map(o => o.value).join('|')") == "|demo-review|demo-weekly|發版清單")
    check("…and the words say where they live, who gets them and that they follow a rename or a new CLI",
          "/demo/bots/助理/skills" in page.inner_text("#sfb-ed-own") and "只有這個 bot" in page.inner_text("#sfb-ed-own")
          and "改名或換 CLI" in page.inner_text("#sfb-ed-own"), page.inner_text("#sfb-ed-own"))

    page.click('#sfb-ed-own [data-own="new"]')
    page.wait_for_timeout(120)
    check("a blank one without a name: asked for the name, nothing called", "bots_skill_new" not in page.evaluate(CALLS)
          and "名字" in page.inner_text("#sfb-ed-msg"), page.inner_text("#sfb-ed-msg"))
    page.fill("#sfb-own-name", "週報整理")
    page.click('#sfb-ed-own [data-own="new"]')
    page.wait_for_function("document.querySelectorAll('#sfb-ed-own .sfb-own-row').length === 3", timeout=5000)
    check("a blank one: the name goes to Python, the list shows it, the box is empty again and the answer is said",
          calls(page, "bots_skill_new") == [["助理", "週報整理", ""]] and rows(page)[-1]["name"] == "週報整理"
          and page.input_value("#sfb-own-name") == "" and "已新增" in page.inner_text("#sfb-ed-msg"), calls(page, "bots_skill_new"))
    page.click('#sfb-ed-own [data-own="copy"]')
    page.wait_for_timeout(100)
    check("a copy with nothing picked: asked to pick, nothing called", calls(page, "bots_skill_copy") == [] and "選" in page.inner_text("#sfb-ed-msg"))
    page.select_option("#sfb-own-from", "demo-weekly")
    page.click('#sfb-ed-own [data-own="copy"]')
    page.wait_for_function("document.querySelectorAll('#sfb-ed-own .sfb-own-row').length === 4", timeout=5000)
    check("a copy of a shared skill: asked for by its name, listed", calls(page, "bots_skill_copy") == [["助理", "demo-weekly"]]
          and rows(page)[-1]["name"] == "demo-weekly" and rows(page)[-1]["tag"])
    page.select_option("#sfb-own-from", "demo-weekly")
    page.click('#sfb-ed-own [data-own="copy"]')
    page.wait_for_timeout(200)
    check("a copy it already has: the refusal is said, the list is as it was", "已經是它的 skill" in page.inner_text("#sfb-ed-msg")
          and len(rows(page)) == 4 and page.evaluate("document.getElementById('sfb-ed-msg').style.color") != "", page.inner_text("#sfb-ed-msg"))
    page.click('#sfb-ed-own [data-own="folder"]')
    page.click('#sfb-ed-own .sfb-own-row[data-folder="daily-notes"] [data-own="open"]')
    page.wait_for_timeout(120)
    check("the folder buttons: the skills folder, and one skill's", calls(page, "bots_skill_open") == [["助理", ""], ["助理", "daily-notes"]], calls(page, "bots_skill_open"))
    page.locator("#sfb-ed-own").scroll_into_view_if_needed()
    page.evaluate("document.querySelector('.sfb-modal .modal').scrollTop = 99999")
    page.wait_for_timeout(150)
    shot(page.locator(".sfb-modal .modal"), "editor")

    print(f"[{engine}] 3. delete: asked first")
    page.click('#sfb-ed-own .sfb-own-row[data-folder="週報整理"] [data-own="delete"]')
    page.wait_for_selector(".sf-dialog .sf-dialog-msg")
    msg = page.inner_text(".sf-dialog .sf-dialog-msg")
    check("the page's own question, naming the skill and what goes", "週報整理" in msg and "資料夾" in msg and "刪" in msg, msg)
    shot(page.locator(".sf-dialog"), "delete-confirm")
    page.click(".sf-dialog .btn-ghost")
    page.wait_for_selector(".sf-dialog", state="detached")
    check("no: nothing is called, the skill is still listed", calls(page, "bots_skill_delete") == [] and len(rows(page)) == 4)
    page.click('#sfb-ed-own .sfb-own-row[data-folder="週報整理"] [data-own="delete"]')
    page.wait_for_selector(".sf-dialog .sf-dialog-msg")
    page.click(".sf-dialog .btn-primary")
    page.wait_for_function("document.querySelectorAll('#sfb-ed-own .sfb-own-row').length === 3", timeout=5000)
    check("yes: that skill's folder is deleted by its folder name, and the row goes", calls(page, "bots_skill_delete") == [["助理", "週報整理"]]
          and "週報整理" not in [r["name"] for r in rows(page)], calls(page, "bots_skill_delete"))
    check("the Save button is not touched by any of this (no bots_save)", "bots_save" not in page.evaluate(CALLS))
    close_editor(page)
    open_editor(page, "寫程式")
    check("a bot with none says so and offers the two ways to add one", "還沒有" in page.inner_text("#sfb-ed-own") and page.locator('#sfb-ed-own [data-own="new"]').count() == 1)
    close_editor(page)
    page.locator('#sfb-settings-list [data-sfb-edit="外部"]').click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    page.wait_for_selector("#sfb-ed-own .sfb-own-empty")
    check("a bot outside ShellBot: told where its skills are, nothing to add", "放在它自己那邊" in page.inner_text("#sfb-ed-own")
          and page.locator('#sfb-ed-own [data-own="new"]').count() == 0)
    close_editor(page)
    page.click("#sfb-settings-new")
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    page.wait_for_selector("#sfb-ed-own .sfb-own-empty")
    check("a bot that does not exist yet: save it first, then add skills", "先儲存" in page.inner_text("#sfb-ed-own")
          and page.locator('#sfb-ed-own [data-own="new"]').count() == 0)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 4. in English")
    page, errors = open_page(browser, "en")
    settings_bots(page)
    check("the setting's words in English, with the quota", "Save a bot’s memory while it stays open" in page.inner_text("#sfb-memsave")
          and "one turn" in page.inner_text("#sfb-memsave") and "quota" in page.inner_text("#sfb-memsave"), page.inner_text("#sfb-memsave"))
    open_editor(page, "助理")
    body = page.inner_text("#sfb-ed-own")
    check("the Skills cell in English", "Open folder" in body and "＋ Blank" in body and "replaces the shared one" in body
          and "follow the bot when it is renamed" in body, body)
    page.click('#sfb-ed-own .sfb-own-row[data-folder="daily-notes"] [data-own="delete"]')
    page.wait_for_selector(".sf-dialog .sf-dialog-msg")
    check("the question in English", "Delete the skill" in page.inner_text(".sf-dialog .sf-dialog-msg"))
    page.click(".sf-dialog .btn-ghost")
    page.click(".sfb-modal [data-act='cancel']")
    page.click("#sfb-settings-new")
    page.wait_for_selector("#sfb-ed-own .sfb-own-empty")
    shot(page.locator(".sfb-modal .modal"), "editor-new-en")
    check("a bot that does not exist yet, in English", "Save the bot first" in page.inner_text("#sfb-ed-own"))
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
    SHOTS.mkdir(exist_ok=True)
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:                       # webkit is optional: `playwright install webkit`
            print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
            continue
        try:
            run(browser, engine)
        finally:
            browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
