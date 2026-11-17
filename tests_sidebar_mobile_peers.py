#!/usr/bin/env python3
"""The sidebar's paired machines: computers listed, phones and tablets folded into one row.

Reported: paired phones and tablets filled the sidebar, each with Message / File / Address actions and "no address,
waiting for it to poll", though a phone or a tablet cannot run a CLI and is never driven from here. So:
  - a phone or a tablet (kind "ios", or an old pairing with no kind and no address of its own) is not listed with
    the computers; a computer says "shellframe" when it pairs, so one behind NAT (no address) stays a computer
  - they are one row, "📱 行動裝置" with how many (and how many are on), folded by default
  - unfolded, each is one line: its light, its name and ✕ to unpair; no Message / File; an old pairing with no kind
    (it could be a computer) keeps ✎ to give it an address
  - computers are as before (their actions, their remote tabs); the divider counts both
  - the fold is remembered like the other folds
Demo data only. Run: .venv/bin/python tests_sidebar_mobile_peers.py
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
    print("SKIP  tests_sidebar_mobile_peers.py（沒裝 playwright）")
    sys.exit(0)

PEERS = [
    {"id": "mac1", "name": "示範桌機", "host": "198.51.100.7", "port": 8767, "reachable": True, "can_control": True, "mode": "duplex"},
    {"id": "mac2", "name": "示範筆電", "host": "198.51.100.9", "port": 8767, "reachable": False, "can_control": True, "mode": "master"},
    {"id": "mac3", "name": "示範NAT桌機", "host": "", "port": 0, "reachable": False, "can_control": False, "mode": "slave", "kind": "shellframe"},
    {"id": "ph1", "name": "示範手機", "host": "", "port": 0, "reachable": False, "can_control": True, "mode": "duplex", "kind": "ios"},
    {"id": "tb1", "name": "示範平板", "host": "", "port": 0, "reachable": True, "can_control": True, "mode": "duplex", "kind": "ios"},
    {"id": "ph2", "name": "示範舊手機", "host": "", "port": 0, "reachable": False, "can_control": True, "mode": "duplex", "kind": ""},
]
LOCAL = [{"sid": "s101", "cmd": "claude", "alive": True, "label": "本機分頁", "bridge_enabled": True}]
CFG = {"settings": {"language": "zh-TW", "sidebar_open": True}, "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}],
       "agent_roster": {}}

STUB = """
window.__cfg = %s; window.__sessions = %s; window.__link = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's101'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'link_status': return R(window.__link);
      case 'link_remote_tabs': return R({success: true, details: {sessions: [{sid: 'r1', label: '示範遠端分頁', agent_state: 'done'}]}});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(LOCAL, ensure_ascii=False), json.dumps({"enabled": True, "peers": PEERS}, ensure_ascii=False))

HEADS = "[...document.querySelectorAll('#sidebar-sessions .sb-peer-head:not(.sb-mobile-head)')].map(h => h.dataset.pid)"

SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 420, "height": 900}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(f"{HEADS}.length >= 3", timeout=30000)
    page.wait_for_timeout(400)

    print("folded (the default)")
    check("only the computers get a machine row, one behind NAT included", page.evaluate(HEADS) == ["mac1", "mac2", "mac3"], page.evaluate(HEADS))
    mob = page.locator("#sidebar-sessions .sb-mobile-head")
    check("phones and tablets are one row, with how many and how many are on",
          mob.count() == 1 and "行動裝置" in mob.inner_text() and "3" in mob.inner_text() and "1 在線" in mob.inner_text(), mob.inner_text() if mob.count() else "")
    check("…folded: none of them is listed", page.locator("#sidebar-sessions .sb-mobile-row").count() == 0)
    text = page.locator("#sidebar-sessions").inner_text()
    check("no phone gets Message / File / Address or 'no address, waiting' (only the computer behind NAT says it)",
          text.count("無位址") == 1 and "示範手機" not in text, text[:300])
    check("the divider counts computers and phones", "(3 + 📱3)" in text, text[:200])
    check("a computer keeps its actions", page.locator('#sidebar-sessions [data-pact="msg"][data-pid="mac1"]').count() == 1)
    check("…and one behind NAT keeps ✎ 位址", page.locator('#sidebar-sessions [data-pact="edit"][data-pid="mac3"]').count() == 1)
    page.locator("#sidebar").screenshot(path=str(SHOTS / "sidebar-mobile-folded.png"))

    print("unfolded")
    mob.click()
    page.wait_for_timeout(300)
    rows = page.evaluate("""[...document.querySelectorAll('#sidebar-sessions .sb-mobile-row')].map(r => ({
        name: r.querySelector('.sb-label').innerText, on: r.querySelector('.sb-peer-dot').classList.contains('on'),
        x: !!r.querySelector('[data-action="unpair-peer"]'), acts: [...r.querySelectorAll('[data-pact]')].map(a => a.dataset.pact)}))""")
    check("each phone or tablet is one line: its light, its name, ✕ to unpair; only the one with no kind keeps ✎",
          [r["name"] for r in rows] == ["示範手機", "示範平板", "示範舊手機"] and [r["on"] for r in rows] == [False, True, False]
          and page.evaluate("getComputedStyle(document.querySelector('.sb-mobile-row .sb-peer-dot.on')).backgroundColor") == "rgb(158, 206, 106)"
          and all(r["x"] for r in rows) and [r["acts"] for r in rows] == [[], [], ["edit"]], rows)
    check("…the fold is remembered", page.evaluate("JSON.parse(localStorage.getItem('sfLinkUi') || '{}').sbMobileOpen") is True
          or page.evaluate("Object.values(localStorage).some(v => /sbMobileOpen\\\":true/.test(v))"))
    page.locator("#sidebar").screenshot(path=str(SHOTS / "sidebar-mobile-open.png"))
    check("no page errors", not errors, errors[:3])
    browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
