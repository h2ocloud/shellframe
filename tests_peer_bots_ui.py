#!/usr/bin/env python3
"""Another machine's bots in the page, and the usage strip at the sidebar's foot: web/index.html in a
browser with a stand-in Python side.

Asked for in daily use, with two computers paired:
  a. the sidebar's Frame Link zone showed the other computer's primary bot with a face guessed here
     from its tab name (a yellow egg), while that computer shows it as a red star with the primary
     badge. Now each remote tab has the face that computer gives it (link_peer_bots, kept from its
     GET /link/bots), its primary bot is marked (the face's badge and a "★ 主要" tag), a terminal
     there is >_; a computer that says nothing keeps the old guess. An opened remote tab has the
     same face.
  b. the chat view's # finds the other computer's bots, awake and asleep (not its external ones),
     labelled with the computer and the primary marked; letters filter by the computer's name too;
     what goes in is #<computer>/<bot>, the A2A target; a computer this one may not control lists
     nothing.
  c. on a fresh install the usage/pace strip at the sidebar's foot ran past the sidebar's right edge
     (cut off). Now it wraps inside: no element's right edge passes the sidebar's, nothing scrolls
     sideways, at the default width (220px) and the narrowest (140px).
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed. Screenshots
(WebKit): qa-shots/peer-bots-*.png. Demo data only. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_peer_bots_ui.py
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
    print("SKIP  tests_peer_bots_ui.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {"助理": {"label": "助理", "cmd": "claude",
                   "bot": {"avatar": {"shape": "round", "color": "purple"}, "memory": True, "primary": True}}}
BOTS = {"enabled": True, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
        "sessions": {"s501": {"bot": "助理"}}, "kinds": {"s501": "bot", "s502": "temp"}, "roster": ROSTER}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "助理", "bridge_enabled": True},
            {"sid": "s502", "cmd": "claude", "alive": True, "label": "週報草稿", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}
# Two paired computers: desk-b (this one may control it) and desk-c (one-way: it controls this one).
LINK = {"enabled": True, "running": True, "frame_id": "me", "frame_name": "desk-here", "addresses": [], "peers": [
    {"id": "pb1", "name": "desk-b", "host": "10.0.0.2", "port": 8767, "kind": "", "reachable": True,
     "mode": "duplex", "can_control": True, "via_relay": False},
    {"id": "pc1", "name": "desk-c", "host": "10.0.0.3", "port": 8767, "kind": "", "reachable": True,
     "mode": "slave", "can_control": False, "via_relay": False}]}
REMOTE_TABS = [{"sid": "s1", "label": "總管", "agent_state": "idle", "cmd": "claude"},
               {"sid": "s2", "label": "週報", "agent_state": "working", "cmd": "claude"},
               {"sid": "s3", "label": "zsh", "agent_state": "", "cmd": "zsh"}]
PEER_BOTS = {"pending": 0, "peers": {"pb1": {
    "name": "desk-b", "addr": "desk-b", "enabled": True, "primary": "總管",
    "bots": [{"role": "研究", "label": "研究-CLD", "former_names": [], "avatar": {"shape": "hex", "color": "blue"},
              "primary": False, "state": "asleep", "sid": ""},
             {"role": "總管", "label": "總管", "former_names": ["主控"], "avatar": {"shape": "star", "color": "red"},
              "primary": True, "state": "awake", "sid": "s1"},
             {"role": "信箱", "label": "信箱", "former_names": [], "avatar": {"shape": "round", "color": "ink"},
              "primary": False, "state": "external", "sid": ""}],
    "tabs": {"s1": {"bot": "總管", "avatar": {"shape": "star", "color": "red"}, "kind": "bot"},
             "s2": {"bot": "", "avatar": {"shape": "cloud", "color": "teal"}, "kind": "temp"},
             "s3": {"bot": "", "avatar": {"shape": "round", "color": "sky"}, "kind": "terminal"}}}}}
NOW = time.time()
USAGE = {"ai": "claude", "account": "demo@example.com", "error": None, "stale": False,
         "five_hr": {"pct": 31, "reset": "18:00", "reset_epoch": int(NOW + 2.4 * 3600), "window_minutes": 300},
         "week": {"pct": 80, "reset": "11-17 09:00", "reset_epoch": int(NOW + 1.3 * 86400), "window_minutes": 7 * 1440}}


def stub(style, peer_bots):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": style, "experimental_bots": True,
                        "usage_pace": True},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": ROSTER}
    return """
window.__sent = []; window.__peerAsks = 0;
window.__cfg = %s; window.__sessions = %s; window.__view = %s; window.__bots = %s;
window.__link = %s; window.__rtabs = %s; window.__peerBots = %s; window.__usage = %s;
try { localStorage.removeItem('sf_link_ui'); } catch (_) {}
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      case 'bots_state': return R(window.__bots);
      case 'preset_groups': return R([]);
      case 'link_status': return R(window.__link);
      case 'link_remote_tabs': return R({success: true, details: {sessions: args[0] === 'pb1' ? window.__rtabs : []}});
      case 'link_peer_bots': window.__peerAsks++; return R(window.__peerBots);
      case 'tab_usage_brief': return R(window.__usage);
      case 'chat_send': window.__sent.push(args[1]); return R({success: true, queued: false});
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, SESSIONS, VIEW, BOTS, LINK, REMOTE_TABS, peer_bots, USAGE))


FACE = """rsid => { const row = document.querySelector(`#sidebar-sessions .sb-remote[data-pid="pb1"][data-rsid="${rsid}"]`);
  if (!row) return null; const svg = row.querySelector('.busy-dot svg.sfb'), term = row.querySelector('.busy-dot .sfb-term');
  return { shape: svg ? svg.dataset.shape : '', color: svg ? svg.style.getPropertyValue('--c').trim() : '',
           primary: !!(svg && svg.classList.contains('is-primary')), term: !!term,
           tag: (row.querySelector('.sfb-peer-primary') || {}).textContent || '' }; }"""
SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
ITEMS = """() => document.getElementById('chat-pick').hidden ? null :
  [...document.querySelectorAll('#chat-pick .cm-pick-item')].map(i => [i.querySelector('b').textContent, i.querySelector('.cm-pick-sub').textContent])"""
# Every element in the sidebar's foot, its right edge against the sidebar's; and sideways scrolling.
FIT = """() => { const sb = document.getElementById('sidebar'), r = sb.getBoundingClientRect();
  const over = [...document.querySelectorAll('#sidebar-footer, #sidebar-footer *')]
    .filter(e => e.getClientRects().length).map(e => [e.id || e.className || e.tagName, e.getBoundingClientRect().right - r.right])
    .filter(x => x[1] > 0.5);
  const dock = document.getElementById('sb-dock'), foot = document.getElementById('sidebar-footer');
  return { width: r.width, over, sbScroll: sb.scrollWidth - sb.clientWidth, footScroll: foot.scrollWidth - foot.clientWidth,
           dockScroll: dock ? dock.scrollWidth - dock.clientWidth : -1, inDock: !!document.querySelector('#sb-dock #usage-brief'),
           text: (document.getElementById('usage-brief') || {}).innerText || '' }; }"""
# How wide the pill is on one line: what ran past the sidebar before.
ONE_LINE = """() => { const el = document.getElementById('usage-brief'), c = el.cloneNode(true);
  c.style.cssText = 'position:absolute;left:0;top:0;visibility:hidden;white-space:nowrap;flex-wrap:nowrap;max-width:none';
  c.querySelectorAll('*').forEach(x => { x.style.flexWrap = 'nowrap'; });
  document.body.appendChild(c); const w = c.getBoundingClientRect().width; c.remove();
  return [w, document.getElementById('sb-dock').clientWidth]; }"""


def open_page(browser, style="cli", peer_bots=None, viewport=(1100, 760)):
    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(style, PEER_BOTS if peer_bots is None else peer_bots))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("!!document.getElementById('sidebar-sessions')", timeout=15000)
    page.wait_for_function("!!document.querySelector('#sidebar-sessions .sb-remote[data-rsid=\"s1\"]')", timeout=15000)
    page.wait_for_timeout(900)
    return page, errors


def run(browser, engine):
    print(f"[{engine}] a. the Frame Link zone: the other computer's own faces")
    page, errors = open_page(browser)
    page.wait_for_function("window.__peerAsks > 0 && !!document.querySelector('#sidebar-sessions .sfb-peer-primary')",
                           timeout=10000)
    f1, f2, f3 = (page.evaluate(FACE, s) for s in ("s1", "s2", "s3"))
    red, teal = page.evaluate("[SFBots.PALETTE.red, SFBots.PALETTE.teal]")
    check("its primary bot: the red star it is over there", f1 and f1["shape"] == "star" and f1["color"] == red, f1)
    check("…with the primary badge on the face", f1 and f1["primary"], f1)
    check("…and a ★ 主要 tag on the row", f1 and "★" in f1["tag"] and "主要" in f1["tag"], f1)
    guess = page.evaluate("SFBots.defaultAvatar('desk-b/週報')")
    check("another tab: the face that computer gives it, not the guess from its name",
          f2 and f2["shape"] == "cloud" and f2["color"] == teal and (guess["shape"], guess["color"]) != ("cloud", "teal"),
          (f2, guess))
    check("…no badge, no tag on it", f2 and not f2["primary"] and not f2["tag"], f2)
    check("a terminal over there is >_, as it is there", f3 and f3["term"], f3)
    check("the one-way computer lists no tabs here (as before)",
          not page.evaluate("!!document.querySelector('#sidebar-sessions .sb-remote[data-pid=\"pc1\"]')"))
    if engine == "webkit":
        side = page.locator("#sidebar").bounding_box()
        page.screenshot(path=str(SHOTS / "peer-bots-sidebar.png"),
                        clip={"x": side["x"], "y": side["y"], "width": side["width"] + 4, "height": side["height"]})
    page.click('#sidebar-sessions .sb-remote[data-rsid="s1"]')
    page.wait_for_function("(window.__sfHost.active() || '').indexOf('s1') > 0", timeout=5000)
    opened = page.evaluate("(() => { const d = document.createElement('div'); d.innerHTML = SFBots.face(window.__sfHost.active());"
                           " const s = d.querySelector('svg'); return s ? [s.dataset.shape, s.classList.contains('is-primary')] : null; })()")
    check("the opened remote tab has the same face, badge and all", opened == ["star", True], opened)
    chip = page.evaluate("(() => { const s = document.querySelector('#tabs .tab-remote.active .busy-dot svg'); return s ? s.dataset.shape : ''; })()")
    check("…in its chip at the top", chip == "star", chip)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}]    a computer that gives nothing keeps today's guess")
    page, errors = open_page(browser, peer_bots={"peers": {}, "pending": 0})
    page.wait_for_function("window.__peerAsks > 0", timeout=10000)
    page.wait_for_timeout(300)
    f2 = page.evaluate(FACE, "s2")
    guess = page.evaluate("SFBots.defaultAvatar('desk-b/週報')")
    check("…the face made from computer + tab name, no badge, no tag",
          f2 and (f2["shape"], f2["color"]) == (guess["shape"], page.evaluate(f"SFBots.PALETTE['{guess['color']}']"))
          and not f2["primary"] and not f2["tag"], (f2, guess))
    check("…and its terminal tab is a face too, as before", not page.evaluate(FACE, "s3")["term"])
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] b. # in the chat view finds the other computer's bots")
    page, errors = open_page(browser, style="chat")
    page.wait_for_function(SHOWN, timeout=10000)
    page.wait_for_function("window.__peerAsks > 0", timeout=10000)
    page.wait_for_timeout(300)
    page.click("#chat-input")
    page.keyboard.type("#")
    page.wait_for_timeout(200)
    items = page.evaluate(ITEMS) or []
    names = [n for n, _ in items]
    check("this computer's tab first, then the other computer's bots, primary first",
          names == ["週報草稿", "總管 · desk-b", "研究-CLD · desk-b"], items)
    subs = dict(items)
    check("…labelled: the primary marked, awake / asleep", subs.get("總管 · desk-b") == "★ 主要 · 醒著"
          and subs.get("研究-CLD · desk-b") == "休息中，會叫醒", items)
    check("…with their faces from over there", page.evaluate(
        "[...document.querySelectorAll('#chat-pick .cm-pick-item')].slice(1).map(i => i.querySelector('svg').dataset.shape)")
          == ["star", "hex"])
    check("its external bot is not listed; the one-way computer's are not either",
          not any("信箱" in n or "desk-c" in n for n in names), names)
    if engine == "webkit":
        page.screenshot(path=str(SHOTS / "peer-bots-mention.png"), clip={"x": 220, "y": 420, "width": 880, "height": 340})
    page.keyboard.type("desk")
    page.wait_for_timeout(150)
    check("letters filter by the computer's name", [n for n, _ in page.evaluate(ITEMS) or []]
          == ["總管 · desk-b", "研究-CLD · desk-b"], page.evaluate(ITEMS))
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    check("Enter puts #<computer>/<bot> in, the A2A target", page.input_value("#chat-input") == "#desk-b/總管 ",
          page.input_value("#chat-input"))
    page.keyboard.type("幫我看一下")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    check("…and that is what is sent", page.evaluate("window.__sent") == ["#desk-b/總管 幫我看一下"], page.evaluate("window.__sent"))
    page.fill("#chat-input", "")
    page.keyboard.type("#主控")
    page.wait_for_timeout(150)
    check("a former name over there finds it too", [n for n, _ in page.evaluate(ITEMS) or []] == ["總管 · desk-b"],
          page.evaluate(ITEMS))
    page.keyboard.press("Escape")
    check("no page errors", not errors, errors)

    print(f"[{engine}] c. the usage strip at the sidebar's foot fits inside the sidebar")
    page.wait_for_function("(document.getElementById('usage-brief') || {}).innerText.includes('wk')", timeout=10000)
    for width in (220, 140):
        page.evaluate(f"document.getElementById('sidebar').style.setProperty('--sidebar-w', '{width}px')")
        page.wait_for_timeout(350)
        fit = page.evaluate(FIT)
        check(f"{width}px: the strip is at the sidebar's foot, all of it there", fit["inDock"] and all(
            k in fit["text"] for k in ("5h", "31%", "wk", "80%")) and fit["text"].count("pc") == 2, fit)
        check(f"{width}px: no element's right edge passes the sidebar's (sidebar {fit['width']:.0f}px)", not fit["over"], fit["over"])
        check(f"{width}px: nothing scrolls sideways", fit["sbScroll"] <= 0 and fit["footScroll"] <= 0 and fit["dockScroll"] <= 0, fit)
        one, room = page.evaluate(ONE_LINE)
        if width == 140:      # at 220 it is a few px either way by engine (WebKit, the Mac app's, is the wider one)
            check(f"…on one line it would not fit ({one:.0f}px in {room}px): the case that was cut off", one > room, (one, room))
        else:
            print(f"         (220px: one line is {one:.0f}px for {room}px of room in {engine})")
        if engine == "webkit":
            box = page.locator("#sidebar").bounding_box()
            foot = page.locator("#sidebar-footer").bounding_box()
            page.screenshot(path=str(SHOTS / f"peer-bots-dock-{width}.png"),
                            clip={"x": box["x"], "y": foot["y"] - 40, "width": box["width"] + 60, "height": foot["height"] + 40})
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
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
