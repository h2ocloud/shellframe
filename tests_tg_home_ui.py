#!/usr/bin/env python3
"""The primary bot's Telegram panel, in the real page (Chromium, stand-in API).

Asked for: first-time setup in three steps (paste a token, bind a phone,
test), the connection and who is bound in one place, the switch that sends the
phone's messages to the primary bot, quiet hours, and a red warning while the
allowlist is empty (anyone could drive the computer).

  - the 📱 on the primary bot's card opens it
  - not set up: step 1 (token) only; connecting calls the app with the token
  - connected with an empty allowlist: step 2/3 and the red warning
  - "Bind a phone" shows a QR code of the one-time t.me link
  - once bound, the warning is gone and the person is listed, removable
  - the switches save exactly what was changed

Screenshots: qa-shots/bots-telegram-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_tg_home_ui.py
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
    print("SKIP  tests_tg_home_ui.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {"主控": {"label": "主控", "responsibility": "盤點與派工", "cmd": "claude",
                   "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}}}
STATE = {"enabled": True, "roster": ROSTER, "primary": "主控", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s301": {"bot": "主控"}}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s301", "cmd": "claude", "alive": True, "label": "主控", "bridge_enabled": True}]
TG = {"configured": False, "connected": False, "bot": "", "users": [], "allow_all": True, "primary": "主控",
      "pairing": None, "default_primary": True, "quiet": {"on": False, "from": "23:00", "to": "08:00"}}

STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__tg = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    const tg = window.__tg;
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's301'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'tg_home_status': return R(tg);
      case 'start_bridge': Object.assign(tg, {configured: true, connected: true, bot: 'demo_shellbot'});
                           return R({success: true});
      case 'tg_pair_start': tg.pairing = {code: 'a1b2c3d4', link: 'https://t.me/demo_shellbot?start=a1b2c3d4',
                                          expires_in: 600};
                            return R(Object.assign({success: true}, tg));
      case 'tg_unbind': tg.users = tg.users.filter(u => String(u) !== String(args[0]));
                        tg.users_info = (tg.users_info || []).filter(u => String(u.id) !== String(args[0]));
                        tg.allow_all = !tg.users.length; return R(tg);
      case 'tg_home_set': { const d = JSON.parse(args[0]);
                            if ('default_primary' in d) tg.default_primary = d.default_primary;
                            if (d.quiet) tg.quiet = Object.assign({}, tg.quiet, d.quiet);
                            return R(tg); }
      case 'tg_test': return R({success: true, message: '已送到 1 個對話'});
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, STATE, TG))

SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector("#sfb-primary .sfb-card-tg", timeout=30000)

    print("open")
    page.click("#sfb-primary .sfb-card-tg")
    page.wait_for_selector(".sfb-tg #sfb-tg-state", timeout=5000)
    page.wait_for_timeout(300)
    vis = lambda sel: page.evaluate(f"!document.querySelector('{sel}').hidden")
    check("the 📱 on the primary card opens its Telegram panel", page.is_visible(".sfb-tg h2"))
    check("not set up: only step 1, the token", vis("#sfb-tg-token") and not vis("#sfb-tg-bind")
          and "還沒設定" in page.text_content("#sfb-tg-state"))
    page.locator(".sfb-tg .modal").screenshot(path=str(SHOTS / "bots-telegram-setup.png"))

    print("connect")
    page.fill("#sfb-tg-tok", "123456:ABC-test-token")
    page.click(".sfb-tg [data-act=connect]")
    page.wait_for_timeout(500)
    started = [c for c in page.evaluate("window.__calls") if c[0] == "start_bridge"]
    check("connecting hands the token to the app", started and started[0][1][0] == "123456:ABC-test-token", started)
    check("connected: steps 2 and 3, and the bot's name", vis("#sfb-tg-bind") and not vis("#sfb-tg-token")
          and "@demo_shellbot" in page.text_content("#sfb-tg-state"))
    check("…said once (no second 'connected' line under it)", "已連線" not in page.text_content("#sfb-tg-msg"))
    check("an empty allowlist shows the red warning", "任何找到這個 bot 的人" in page.text_content("#sfb-tg-users"))

    print("bind")
    page.click(".sfb-tg [data-act=pair]")
    page.wait_for_selector(".sfb-tg .sfb-tg-qr svg", timeout=5000)
    check("a QR code of the one-time link", page.text_content(".sfb-tg .sfb-tg-qr a")
          == "https://t.me/demo_shellbot?start=a1b2c3d4" and page.is_visible(".sfb-tg .sfb-tg-qr svg"))
    page.locator(".sfb-tg .modal").screenshot(path=str(SHOTS / "bots-telegram-pair.png"))
    page.evaluate("Object.assign(window.__tg, {users: [555], allow_all: false, pairing: null, "
                  "users_info: [{id: 555, label: '@ann_dev'}]})")
    page.wait_for_timeout(2600)                          # the panel refreshes every 2 s
    check("bound: the warning goes, the person is listed by @username", "@ann_dev" in page.text_content("#sfb-tg-users")
          and "任何找到" not in page.text_content("#sfb-tg-users") and not page.is_visible(".sfb-tg .sfb-tg-qr"))
    page.locator(".sfb-tg .modal").screenshot(path=str(SHOTS / "bots-telegram-bound.png"))

    print("switches")
    page.uncheck("#sfb-tg-home")
    page.wait_for_timeout(300)
    sets = [json.loads(c[1][0]) for c in page.evaluate("window.__calls") if c[0] == "tg_home_set"]
    check("turning the default off saves it", sets and sets[-1]["default_primary"] is False, sets)
    page.check("#sfb-tg-quiet")
    page.fill("#sfb-tg-from", "22:30")
    page.dispatch_event("#sfb-tg-from", "change")
    page.wait_for_timeout(300)
    sets = [json.loads(c[1][0]) for c in page.evaluate("window.__calls") if c[0] == "tg_home_set"]
    check("quiet hours on, from 22:30", sets[-1]["quiet"] == {"on": True, "from": "22:30", "to": "08:00"}, sets[-1])
    page.click(".sfb-tg [data-act=test]")
    page.wait_for_timeout(300)
    check("the test button reports back", "已送到" in page.text_content("#sfb-tg-msg"))
    page.click(".sfb-tg [data-act=unbind]")
    page.wait_for_timeout(400)
    check("removing the person brings the warning back", "任何找到" in page.text_content("#sfb-tg-users"))
    check("no page errors", not errors, errors[:3])

    en = browser.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=2)
    en.add_init_script(STUB.replace('"language": "zh-TW"', '"language": "en"'))
    en.goto((HERE / "web" / "index.html").as_uri())
    en.wait_for_selector("#sfb-primary .sfb-card-tg", timeout=30000)
    en.click("#sfb-primary .sfb-card-tg")
    en.wait_for_selector(".sfb-tg #sfb-tg-state", timeout=5000)
    en.wait_for_timeout(300)
    check("English words", "Not set up yet" in en.text_content("#sfb-tg-state")
          and "@BotFather" in en.text_content("#sfb-tg-token"))
    en.locator(".sfb-tg .modal").screenshot(path=str(SHOTS / "bots-telegram-setup-en.png"))
    browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
