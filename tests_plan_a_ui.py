#!/usr/bin/env python3
"""Plan A in the real page: web/index.html in a browser with a stand-in Python side.
  - each sidebar row says what the tab is: a bot (its tag), a temporary bot ("臨時" and
    a Keep button), a terminal (>_ instead of a face); a tab marked "not a bot" offers
    no Keep
  - Keep opens a small box with the tab's title; a name another bot has is refused and
    the free name offered is put in the box; the next try keeps it, the row turns into
    a bot and the page says so
Demo data only. Screenshots go to qa-shots/plan-a-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_plan_a_ui.py
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
    print("SKIP  tests_plan_a_ui.py（沒裝 playwright）")
    sys.exit(0)

TABS = [  # sid, label, cmd, state, line
    ("s201", "寫程式", "claude", "working", {"action": "Editing relay.py"}),
    ("s202", "查資料", "claude", "done", {"narration": "三篇比較整理好了"}),
    ("s203", "看合約", "codex", "idle", {"narration": "第 4 條要再確認"}),
    ("s204", "zsh", "zsh", "idle", {}),
    ("s205", "草稿", "claude", "idle", {"narration": "先放著"}),
    ("s206", "查資料", "claude", "idle", {"narration": "上週的比較在這裡"}),
]
DOT = {"working": "sig-working", "decision": "sig-decision", "done": "sig-done", "stuck": "sig-stuck", "idle": ""}
ROSTER = {"寫程式": {"label": "寫程式", "responsibility": "程式、測試", "cmd": "claude",
                  "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}},
          "查資料": {"label": "查資料", "responsibility": "資料調研", "cmd": "claude",
                   "bot": {"avatar": {"shape": "hex", "color": "blue"}, "memory": True}}}
STATE = {"enabled": True, "roster": ROSTER, "primary": "", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s201": {"bot": "寫程式"}, "s205": {"plain": True}, "s206": {"bot": "查資料"}},
         "kinds": {"s202": "temp", "s203": "temp", "s204": "terminal", "s205": "temp"}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True, "ui_style": "cli"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}, {"name": "Pi", "cmd": "pi", "icon": "P"}],
       "agent_roster": {}}
SESSIONS = [{"sid": sid, "cmd": cmd, "alive": True, "label": label, "bridge_enabled": True} for sid, label, cmd, *_ in TABS]
STATUS = {sid: dict({"state": st, "dot": DOT[st], "summary": st}, **extra) for sid, _, _, st, extra in TABS}

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
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's201'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'bots_keep': {
        const [sid, name] = args;
        if (name === '查資料') return R({success: false, code: 'name_taken', suggestion: '查資料 2',
                                         message: '「查資料」已經有別的 bot 在用'});
        // as the app does: the face it had (bots.resolved_avatar of its sid), its tab renamed
        window.__bots.roster[name] = {label: name, cmd: 'claude', bot: {avatar: window.SFBots.defaultAvatar(sid), memory: true}};
        window.__bots.sessions[sid] = {bot: name};
        delete window.__bots.kinds[sid];
        const s = window.__sessions.find(x => x.sid === sid); if (s) s.label = name;
        const live = window.__sfHost && window.__sfHost.sessions()[sid]; if (live) live.label = name;
        return R({success: true, role: name, message: '已建立「' + name + '」'});
      }
      case 'bots_skill_catalog': return R({skills: []});
      case 'bots_save': return R({success: true, message: '已更新'});
      case 'bots_switch': return R({success: true, role: args[0], pending: true});
      default: return R('{}');
    }
  };
}})};
setInterval(() => { try { window.__sfAgentStatus && window.__sfAgentStatus(window.__status); } catch (e) {} }, 500);
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False),
       json.dumps(STATE, ensure_ascii=False), json.dumps(STATUS, ensure_ascii=False))

SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 1300}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .sfb-two').length >= 6",
                           timeout=15000)
    row = lambda sid: f'#sidebar-sessions .sb-item[data-sid="{sid}"]'
    page.wait_for_function("(document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s201\"] .sfb-sub') || {})"
                           ".textContent === 'Editing relay.py'", timeout=10000)        # the agents' state is in

    def shot(name):                       # the sidebar and the box that opens beside it
        b = page.locator("#sidebar").bounding_box()
        page.screenshot(path=str(SHOTS / name), clip={"x": b["x"], "y": b["y"], "width": b["width"] + 230, "height": 560})

    print("1. each row says what the tab is")
    import os
    if os.environ.get("SF_DEBUG_ROW"):
        print(page.evaluate("document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s202\"]').innerHTML"))
    check("a bot: its tag, no Keep", page.locator(row("s201") + " .sfb-tag").first.inner_text() == "Bot"
          and page.locator(row("s201") + " .sfb-keep").count() == 0)
    check("a temporary bot titled like a bot open elsewhere is not that bot: 臨時 and a Keep button",
          page.locator(row("s202") + " .sfb-temp").inner_text() == "臨時"
          and page.locator(row("s202") + " .sfb-keep").inner_text() == "記住")
    check("…a Codex tab too", page.locator(row("s203") + " .sfb-keep").count() == 1)
    check("a terminal: >_ instead of a face, no Keep", page.locator(row("s204") + " .sfb-term").count() == 1
          and page.locator(row("s204") + " svg.sfb").count() == 0 and page.locator(row("s204") + " .sfb-keep").count() == 0)
    check("a tab marked not a bot: no Keep", page.locator(row("s205") + " .sfb-keep").count() == 0)
    shot("plan-a-sidebar.png")

    print("2. Keep")
    active_before = page.evaluate("document.querySelector('#sidebar-sessions .sb-item.active') && "
                                  "document.querySelector('#sidebar-sessions .sb-item.active').dataset.sid")
    page.locator(row("s202") + " .sfb-keep").click()
    page.wait_for_selector("#sfb-keep")
    check("a box with the tab's title, and the click did not switch tabs",
          page.input_value("#sfb-keep-name") == "查資料" and page.evaluate(
              "document.querySelector('#sidebar-sessions .sb-item.active') && "
              "document.querySelector('#sidebar-sessions .sb-item.active').dataset.sid") == active_before)
    shot("plan-a-keep-box.png")
    page.locator('#sfb-keep [data-k="ok"]').click()
    page.wait_for_function("document.querySelector('#sfb-keep .sfb-keep-msg').textContent.length > 0")
    check("a name another bot has: said, and the free one put in the box",
          "已經有別的 bot 在用" in page.inner_text("#sfb-keep .sfb-keep-msg") and page.input_value("#sfb-keep-name") == "查資料 2")
    page.locator("#sfb-keep").screenshot(path=str(SHOTS / "plan-a-keep-taken.png"))
    page.locator("#sfb-keep-name").press("Enter")
    page.wait_for_function("!document.getElementById('sfb-keep')")
    page.wait_for_function("(() => { const r = document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s202\"] .sfb-tag');"
                           " return r && r.textContent === 'Bot'; })()", timeout=8000)
    check("kept: the row is a bot now, with no Keep", page.locator(row("s202") + " .sfb-keep").count() == 0)
    calls = page.evaluate("window.__calls.filter(c => c[0] === 'bots_keep').map(c => c[1])")
    check("the page asked to keep it by the name in the box", calls == [["s202", "查資料"], ["s202", "查資料 2"]], calls)
    shot("plan-a-kept.png")

    print("3. another CLI or model for a bot")
    page.evaluate("window.SFBots.openSettings('s201', document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s201\"] .busy-dot'))")
    page.locator('.sfb-pop [data-act="edit"]').click()
    page.wait_for_selector("#sfb-ed-cmd")
    check("an awake bot's editor says what stays and offers to switch now",
          "這次的對話不會搬到新的 CLI" in page.inner_text(".sfb-modal") and page.locator('[data-act="save-now"]').count() == 1)
    page.select_option("#sfb-ed-cmd", value="pi")
    check("a CLI that picks its model inside: the model box is off and empty",
          page.locator("#sfb-ed-model").is_disabled() and page.input_value("#sfb-ed-model") == "")
    page.select_option("#sfb-ed-cmd", value="claude")
    page.fill("#sfb-ed-model", "sonnet")
    check("…another one: on again", not page.locator("#sfb-ed-model").is_disabled())
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / "plan-a-switch.png"))
    page.locator('[data-act="save-now"]').click()
    page.wait_for_function("window.__calls.some(c => c[0] === 'bots_switch')", timeout=8000)
    saves = page.evaluate("window.__calls.filter(c => c[0] === 'bots_save').map(c => [c[1][0], JSON.parse(c[1][1]).cmd, JSON.parse(c[1][1]).model])")
    sw = page.evaluate("window.__calls.filter(c => c[0] === 'bots_switch').map(c => c[1])")
    check("saved with the CLI and model, then switched now", saves[-1:] == [["寫程式", "claude", "sonnet"]]
          and sw == [["寫程式", "", None, "now"]], (saves, sw))
    check("no page errors", not errors, errors)
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
