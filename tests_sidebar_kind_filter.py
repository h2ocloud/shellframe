#!/usr/bin/env python3
"""The sidebar's chips filter by kind: bots, or the other tabs (web/bots.js applyView).

Asked for: the filter chips (idle, > 30 min, recent first) should also filter by session and by bot.
  - 「Bot」: only bots, awake (their tab) or asleep; the temporary tabs and terminals are hidden
  - 「分頁」: only the other tabs (temporary ones and terminals); bots, asleep ones too, are hidden
  - one or the other: turning one on turns the other off; again turns it off
  - it adds to the search and is remembered (the sidebar's view is)
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_sidebar_kind_filter.py
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
    print("SKIP  tests_sidebar_kind_filter.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {
    "Coding": {"label": "Coding", "responsibility": "程式", "cmd": "claude",
               "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}},
    "日程": {"label": "日程", "responsibility": "行程與提醒", "cmd": "claude",
             "bot": {"avatar": {"shape": "cloud", "color": "green"}, "memory": True}},
}
STATE = {"enabled": True, "roster": ROSTER, "primary": "", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s701": {"bot": "Coding"}}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s701", "cmd": "claude", "alive": True, "label": "Coding", "bridge_enabled": True},
            {"sid": "s702", "cmd": "claude", "alive": True, "label": "示範臨時", "bridge_enabled": True},
            {"sid": "s703", "cmd": "zsh", "alive": True, "label": "示範終端機", "bridge_enabled": True}]
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
      case 'get_active_tab': return R({sid: 's701'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(STATE, ensure_ascii=False))

SHOWN = """() => [...document.querySelectorAll('#sidebar-sessions .sb-item[data-section="local"][data-sid], #sidebar-sessions .sfb-sleeper')]
  .filter(r => r.offsetParent !== null).map(r => r.dataset.sid || ('asleep:' + r.dataset.sfbRole)).sort()"""
ON = "k => !!document.querySelector(`#sfb-tools [data-view='${k}'].on`)"


def chip(page, k):
    page.click(f"#sfb-tools [data-view='{k}']")
    page.wait_for_timeout(400)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .sfb-ago').length >= 3"
                           " && !!document.querySelector(\"#sfb-tools [data-view='bots']\")", timeout=30000)
    page.wait_for_timeout(600)
    everything = page.evaluate(SHOWN)
    check("the chips say Bot and 分頁", page.inner_text("#sfb-tools [data-view='bots']") == "Bot"
          and page.inner_text("#sfb-tools [data-view='tabs']") == "分頁")
    check("nothing on: every tab and the sleeping bot", everything == ["asleep:日程", "s701", "s702", "s703"], everything)

    print("Bot")
    chip(page, "bots")
    check("only bots: its tab and the sleeping one", page.evaluate(SHOWN) == ["asleep:日程", "s701"], page.evaluate(SHOWN))
    print("分頁")
    chip(page, "tabs")
    check("only the other tabs: the temporary one and the terminal", page.evaluate(SHOWN) == ["s702", "s703"],
          page.evaluate(SHOWN))
    check("…and Bot turned off", not page.evaluate(ON, "bots") and page.evaluate(ON, "tabs"))
    page.fill("#sfb-q", "終端")
    page.wait_for_timeout(200)
    check("it adds to the search", page.evaluate(SHOWN) == ["s703"], page.evaluate(SHOWN))
    page.fill("#sfb-q", "")
    page.wait_for_timeout(200)
    saved = json.loads(page.evaluate("localStorage.getItem('sfb-sidebar-view') || '{}'"))
    check("remembered with the sidebar's view", saved.get("kind") == "tabs", saved)
    chip(page, "tabs")
    check("again: off, everything back", page.evaluate(SHOWN) == everything and not page.evaluate(ON, "tabs"),
          page.evaluate(SHOWN))
    page.locator("#sfb-tools").screenshot(path=str(HERE / "qa-shots" / "sidebar-kind-chips.png"))
    check("no page errors", not errors, errors)
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
