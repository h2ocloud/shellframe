#!/usr/bin/env python3
"""Renaming a bot in the real page: web/index.html in a browser with a stand-in Python side.
  - the tab's rename dialog on a bot's tab asks first ("rename the bot … ?"); cancelled,
    nothing is sent and the tab keeps its name; confirmed, the page asks the app to rename
    the tab (rename_session), the app renames the bot and sends the label back
  - a temporary bot's tab is renamed as before, without the question
  - the bot's editor lets its name be changed (not an outside bot's), asks the same
    question and renames with bots_rename before it saves the rest under the new name;
    a name another bot has is said and the free one put in the box, nothing saved
Demo data only. Screenshots go to qa-shots/bot-rename-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_bot_rename_ui.py
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
    print("SKIP  tests_bot_rename_ui.py（沒裝 playwright）")
    sys.exit(0)

TABS = [("s301", "助理", "claude"), ("s302", "查資料", "claude"), ("s303", "寫程式", "codex")]
ROSTER = {"助理": {"label": "助理", "responsibility": "排程、提醒", "cmd": "claude",
                 "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}},
          "寫程式": {"label": "寫程式", "responsibility": "程式、測試", "cmd": "codex",
                  "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}},
          "外部": {"label": "外部", "cmd": "zsh",
                 "bot": {"external": {"kind": "webhook", "url": "https://example.com/hook"}}}}
STATE = {"enabled": True, "roster": ROSTER, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s301": {"bot": "助理"}, "s303": {"bot": "寫程式"}}, "kinds": {"s302": "temp"}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True, "ui_style": "cli"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": sid, "cmd": cmd, "alive": True, "label": label, "bridge_enabled": True} for sid, label, cmd in TABS]

STUB = """
window.__calls = []; window.__asked = []; window.__answer = true;
window.confirm = msg => { window.__asked.push(msg); return window.__answer; };
window.__cfg = %s; window.__sessions = %s; window.__bots = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
function renameBot(role, name) {          // what api_bots._bots_rename does to the state the page reads
  const B = window.__bots;
  const taken = Object.keys(B.roster).some(r => r !== role &&
    [r, B.roster[r].label, ...(B.roster[r].former_names || [])].includes(name));
  if (taken) return {success: false, code: 'name_taken', suggestion: name + ' 2',
                     message: '「' + name + '」已經有別的 bot 在用'};
  const e = B.roster[role]; delete B.roster[role];
  e.label = name; e.former_names = [role]; B.roster[name] = e;
  if (B.primary === role) B.primary = name;
  for (const sid of Object.keys(B.sessions)) if (B.sessions[sid].bot === role) {
    B.sessions[sid].bot = name;
    const s = window.__sessions.find(x => x.sid === sid); if (s) s.label = name;
    window.__sfApplyLabel && window.__sfApplyLabel(sid, name);     // the app sends the label back
  }
  return {success: true, role: name, former: role, message: '「' + role + '」改名為「' + name + '」；用舊名字也還找得到它'};
}
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's301'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'bots_skill_catalog': return R({skills: []});
      case 'rename_session': {
        const [sid, name] = args;
        const role = (window.__bots.sessions[sid] || {}).bot;
        if (role && name !== role) return R(renameBot(role, name));
        const s = window.__sessions.find(x => x.sid === sid); if (s) s.label = name;
        return R({success: true});
      }
      case 'bots_rename': return R(renameBot(args[0], args[1]));
      case 'bots_save': return R({success: true, message: '已更新「' + args[0] + '」'});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(STATE, ensure_ascii=False))

SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    row = lambda sid: f'#sidebar-sessions .sb-item[data-sid="{sid}"]'
    page.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .sfb-two').length >= 3",
                           timeout=15000)
    page.wait_for_function("!!document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s301\"] .sfb-tag')",
                           timeout=15000)                                   # the bots' state is in
    calls = lambda name: page.evaluate(f"window.__calls.filter(c => c[0] === '{name}').map(c => c[1])")

    def wait(expr, what):
        """Wait for expr; on a timeout, a FAIL that says what was awaited (not a bare traceback)."""
        try:
            page.wait_for_function(expr, timeout=20000)
        except Exception as e:
            check(f"waited for {what}", False, str(e).splitlines()[0][:120])

    def rename_in_dialog(sid, name):
        page.evaluate(f"window.__sfHost.renameSession('{sid}')")
        page.wait_for_selector("#rename-input")
        page.fill("#rename-input", name)
        page.click("#rename-ok")
        page.wait_for_selector("#rename-input", state="detached")

    def label(sid):
        return page.evaluate(f"window.__sfHost.sessions()['{sid}'].label")

    print("1. a bot's tab, from the rename dialog")
    page.evaluate("window.__answer = false")
    rename_in_dialog("s301", "秘書")
    wait("window.__asked.length >= 1", "the question")
    check("it asks first, naming the bot and the new name, and says what stays",
          len(page.evaluate("window.__asked")) == 1 and "助理" in page.evaluate("window.__asked[0]")
          and "秘書" in page.evaluate("window.__asked[0]") and "記憶" in page.evaluate("window.__asked[0]"),
          page.evaluate("window.__asked"))
    check("cancelled: nothing is sent and the tab keeps its name", calls("rename_session") == [] and label("s301") == "助理")
    page.evaluate("window.__answer = true")
    rename_in_dialog("s301", "秘書")
    wait("window.__sfHost.sessions()['s301'].label === '秘書'", "the tab's new name")
    check("confirmed: the page asks the app to rename the tab, by hand", calls("rename_session") == [["s301", "秘書", True]],
          calls("rename_session"))
    page.evaluate("window.SFBots.refresh(true)")
    wait("(document.querySelector('.sfb-card-name') || {}).textContent === '秘書'", "the card's new name")
    check("the card and the row carry the new name", page.inner_text(".sfb-card-name") == "秘書"
          and "秘書" in page.inner_text(row("s301")))
    b = page.locator("#sidebar").bounding_box()
    page.screenshot(path=str(SHOTS / "bot-rename-after.png"),
                    clip={"x": b["x"], "y": b["y"], "width": b["width"], "height": 520})

    print("2. a temporary bot's tab: renamed as before, no question")
    asked = len(page.evaluate("window.__asked"))
    rename_in_dialog("s302", "新的調查")
    check("no question, and the tab is renamed at once", len(page.evaluate("window.__asked")) == asked
          and label("s302") == "新的調查" and calls("rename_session")[-1] == ["s302", "新的調查", True])

    print("3. the bot's editor")
    page.evaluate("window.SFBots.openSettings('s303', document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s303\"] .busy-dot'))")
    page.locator('.sfb-pop [data-act="edit"]').click()
    page.wait_for_selector("#sfb-ed-name")
    check("an existing bot's name can be changed", page.locator("#sfb-ed-name").get_attribute("readonly") is None)
    page.fill("#sfb-ed-name", "秘書")
    page.locator('.sfb-modal [data-act="save"]').click()
    wait("(document.getElementById('sfb-ed-msg') || {textContent: ''}).textContent.includes('已經有別的 bot')", "the editor's 'taken' message")
    check("a name another bot has: said, the free one put in the box, nothing saved",
          page.input_value("#sfb-ed-name") == "秘書 2" and calls("bots_save") == [])
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / "bot-rename-editor-taken.png"))
    page.fill("#sfb-ed-name", "工程")
    page.locator('.sfb-modal [data-act="save"]').click()
    wait("window.__calls.some(c => c[0] === 'bots_save')", "bots_save")
    check("the same question as the tab's", "寫程式" in page.evaluate("window.__asked.slice(-1)[0]")
          and "工程" in page.evaluate("window.__asked.slice(-1)[0]"))
    check("renamed with bots_rename, then the rest saved under the new name",
          calls("bots_rename")[-1] == ["寫程式", "工程"] and calls("bots_save")[-1][0] == "工程",
          (calls("bots_rename"), calls("bots_save")))
    wait("!document.querySelector('.sfb-modal')", "the editor to close")
    wait("window.__sfHost.sessions()['s303'].label === '工程'", "the edited bot's tab name")
    check("its tab carries the new name", label("s303") == "工程")
    page.evaluate("window.SFBots.openSettings('s301', document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s301\"] .busy-dot'))")
    page.locator('.sfb-pop [data-act="edit"]').click()
    page.wait_for_selector("#sfb-ed-name")
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / "bot-rename-editor.png"))
    page.locator('.sfb-modal [data-act="cancel"]').click()

    print("4. an outside bot")
    src = (HERE / "web" / "bots.js").read_text(encoding="utf-8")
    check("its editor keeps the name read-only (it is named where it lives; the app refuses it too)",
          "existing && isExternal(role) ? 'readonly'" in src)
    check("no page errors", not errors, errors)
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
