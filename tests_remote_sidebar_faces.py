#!/usr/bin/env python3
"""Another machine's tabs in the sidebar: every row gets the same light/face slot.

Reported: under a paired machine, the first few tabs had faces and the rest had
none, so the names below were shifted left. The faces are drawn on this machine
(seeded by machine + tab name); the other machine only reports each tab's state.
The row drew its light (and the face inside it) only when that state was
non-empty, and the other machine leaves it empty for any tab its status monitor
has no snapshot of. Measured on a real peer: 4 of 27 tabs reported "done", 23
reported "", which is exactly the 4 faces / 23 bare rows seen.

This loads the whole web/index.html with a stand-in for the Python side and one
paired machine whose tabs report "done", "working" and "" (and one that sends
no agent_state at all, as an old peer would), then checks:
  - bots on: every remote row has a face, and all names start at the same x
  - the light still follows the reported state (working = on; unknown is said so)
  - bots off: no faces, the names are still aligned, only the working row lights

Screenshots: qa-shots/remote-sidebar-faces-{on,off}.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_remote_sidebar_faces.py
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
    print("SKIP  tests_remote_sidebar_faces.py（沒裝 playwright）")
    sys.exit(0)

REMOTE = [  # what the other machine's /link list returns, trimmed to what the row uses
    {"sid": "r1", "label": "開發｜Demo", "agent_state": "done"},
    {"sid": "r2", "label": "知識調研", "agent_state": "working"},
    {"sid": "r3", "label": "監看｜Build", "agent_state": ""},
    {"sid": "r4", "label": "總管｜Guard", "agent_state": ""},
    {"sid": "r5", "label": "教練｜Ear", "agent_state": "done"},
    {"sid": "r6", "label": "舊版分頁"},          # an old peer sends no agent_state at all
]
PEER = {"id": "peer7", "name": "Demo-MacBook", "host": "198.51.100.7", "port": 1234,
        "reachable": True, "can_control": True, "mode": "duplex"}
LOCAL = [{"sid": "s101", "cmd": "claude", "alive": True, "label": "本機分頁", "bridge_enabled": True}]


def stub(bots_on):
    cfg = {"settings": {"experimental_bots": bots_on, "language": "zh-TW", "sidebar_open": True},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    bots = {"enabled": bots_on, "roster": {}, "primary": "", "memory_root": "", "sessions": {}}
    return """
window.__cfg = %s; window.__sessions = %s; window.__bots = %s;
window.__link = %s; window.__remote = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's101'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'link_status': return R(window.__link);
      case 'link_remote_tabs': return R({success: true, details: {sessions: window.__remote}});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(cfg, ensure_ascii=False), json.dumps(LOCAL, ensure_ascii=False),
       json.dumps(bots, ensure_ascii=False), json.dumps({"enabled": True, "peers": [PEER]}),
       json.dumps(REMOTE, ensure_ascii=False))


ROWS = "[...document.querySelectorAll('#sidebar-sessions .sb-item.sb-remote')]"
MEASURE = """() => %s.map(r => {
  const dot = r.querySelector('.busy-dot');
  return { rsid: r.dataset.rsid,
           face: !!r.querySelector('.busy-dot svg.sfb'),
           dot: !!dot, on: !!(dot && dot.classList.contains('on')),
           title: dot ? dot.title : '',
           x: Math.round(r.querySelector('.sb-label').getBoundingClientRect().left) };
})""" % ROWS


def load(pw, bots_on):
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 420, "height": 1000}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(bots_on))
    page.goto((HERE / "web" / "index.html").as_uri())
    try:
        page.wait_for_function(f"{ROWS}.length === {len(REMOTE)}", timeout=30000)
        if bots_on:
            page.wait_for_function("document.body.classList.contains('sfb-on')", timeout=15000)
    except Exception as e:
        print("remote rows never rendered:", e, errors[:5])
    page.wait_for_timeout(400)
    return browser, page, errors


SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    print("bots on")
    browser, page, errors = load(pw, True)
    rows = page.evaluate(MEASURE)
    check("every remote tab has a row", len(rows) == len(REMOTE), str(rows))
    check("every remote row has a face, whatever the peer reported",
          rows and all(r["face"] for r in rows), str([(r["rsid"], r["face"]) for r in rows]))
    check("all remote names start at the same x", len({r["x"] for r in rows}) == 1, str([r["x"] for r in rows]))
    by = {r["rsid"]: r for r in rows}
    check("the working tab's light is on", by.get("r2", {}).get("on") is True, str(by.get("r2")))
    check("the others are not", not any(by[k]["on"] for k in by if k != "r2"), str(rows))
    check("a reported idle tab says idle", by.get("r1", {}).get("title") == "閒置", str(by.get("r1")))
    check("a tab with no reported state says so, not idle",
          by.get("r3", {}).get("title") == "對方沒有回報狀態" and by.get("r6", {}).get("title") == "對方沒有回報狀態",
          str((by.get("r3"), by.get("r6"))))
    page.locator("#sidebar").screenshot(path=str(SHOTS / "remote-sidebar-faces-on.png"))
    check("no page errors", not errors, str(errors[:3]))
    browser.close()

    print("bots off")
    browser, page, errors = load(pw, False)
    rows = page.evaluate(MEASURE)
    check("no faces with bots off", rows and not any(r["face"] for r in rows), str(rows))
    check("names still aligned with bots off", len({r["x"] for r in rows}) == 1, str([r["x"] for r in rows]))
    check("only the working tab lights", [r["rsid"] for r in rows if r["on"]] == ["r2"], str(rows))
    page.locator("#sidebar").screenshot(path=str(SHOTS / "remote-sidebar-faces-off.png"))
    check("no page errors", not errors, str(errors[:3]))
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
