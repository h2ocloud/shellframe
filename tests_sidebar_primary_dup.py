#!/usr/bin/env python3
"""The primary bot is on its card above the list, not again in the list (web/bots.js applyView).

Asked for: the sidebar showed the primary bot twice, as the card on top and as a row further down.
  - awake: its tab's row is hidden; the card stays, and its other bots' rows stay
  - asleep: its sleeping row is hidden too
  - no longer primary: its row is back
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_sidebar_primary_dup.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
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
    print("SKIP  tests_sidebar_primary_dup.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {
    "主控": {"label": "主控", "responsibility": "盤點與派工", "cmd": "claude",
             "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}},
    "Coding": {"label": "Coding", "responsibility": "程式", "cmd": "claude",
               "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}},
}
STATE = {"enabled": True, "roster": ROSTER, "primary": "主控", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s601": {"bot": "主控"}, "s602": {"bot": "Coding"}}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s601", "cmd": "claude", "alive": True, "label": "主控", "bridge_enabled": True},
            {"sid": "s602", "cmd": "claude", "alive": True, "label": "Coding", "bridge_enabled": True},
            {"sid": "s603", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's603'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(STATE, ensure_ascii=False))

SHOWN = """() => [...document.querySelectorAll('#sidebar-sessions .sb-item[data-section="local"][data-sid], #sidebar-sessions .sfb-sleeper')]
  .filter(r => r.offsetParent !== null).map(r => r.dataset.sid || ('zz:' + r.dataset.sfbRole))"""
CARD = "(() => { const c = document.querySelector('#sfb-primary .sfb-card-name'); return c ? c.textContent : ''; })()"


def reload_bots(page):
    page.evaluate("window.SFBots && SFBots.refresh ? SFBots.refresh() : window.__sfHost.renderSidebar()")
    page.wait_for_timeout(900)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("document.querySelector('#sfb-primary .sfb-card-name')", timeout=20000)
    page.wait_for_timeout(800)
    print("awake")
    shown = page.evaluate(SHOWN)
    check("the card shows the primary bot", page.evaluate(CARD) == "主控", page.evaluate(CARD))
    check("its tab's row is not in the list; the others are", "s601" not in shown and {"s602", "s603"} <= set(shown), shown)

    print("asleep")
    page.evaluate("window.__sessions = window.__sessions.filter(s => s.sid !== 's601'); delete window.__bots.sessions.s601")
    page.evaluate("window.__sfHost.renderSidebar()")
    reload_bots(page)
    shown = page.evaluate(SHOWN)
    check("its sleeping row is not in the list either", not any(s == "zz:主控" for s in shown) and "s601" not in shown, shown)
    check("…the card is still there", page.evaluate(CARD) == "主控")

    print("no longer primary")
    page.evaluate("window.__sessions.push({sid: 's601', cmd: 'claude', alive: true, label: '主控', bridge_enabled: true});"
                  "window.__bots.sessions.s601 = {bot: '主控'}; window.__bots.primary = '';"
                  "window.__bots.roster['主控'].bot.primary = false")
    page.wait_for_function("!!document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s601\"]')", timeout=15000)
    reload_bots(page)
    shown = page.evaluate(SHOWN)
    check("its row is back in the list", "s601" in shown, shown)
    check("no page errors", not errors, errors)
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
