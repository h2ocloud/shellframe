#!/usr/bin/env python3
"""Another computer's tab keeps this pane's size: it is sent again whenever the other side may have lost it.

Reported with a screenshot: a Codex tab on another computer drew in an 85x24 corner of the pane, and its tmux
status line showed the window bigger than its client, right after that computer's ShellBot had restarted.
fitRemotePane sends our size only when it changes on this side, so a restart over there (its tabs come back at
its own window's size) was never corrected. Now (web/index.html, resendRemoteSize):
  - opening (attaching to) a remote tab sends our size
  - when the stream comes back after failing (the other side restarted, or the network) it is sent again
  - when the other side lost our stream without an error (it restarted quickly) it is sent again
  - a resize makes the other side redraw and the stream re-attach: that does not loop (at most once in 3 s)
Demo data only. Run: .venv/bin/python tests_remote_pane_size.py
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
    print("SKIP  tests_remote_pane_size.py（沒裝 playwright）")
    sys.exit(0)

PEERS = [{"id": "mac1", "name": "示範桌機", "host": "198.51.100.7", "port": 8767, "reachable": True, "can_control": True,
          "mode": "duplex", "kind": "shellframe"}]
LOCAL = [{"sid": "s101", "cmd": "claude", "alive": True, "label": "本機分頁", "bridge_enabled": True}]
CFG = {"settings": {"language": "zh-TW", "sidebar_open": True}, "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}],
       "agent_roster": {}}

STUB = """
window.__cfg = %s; window.__sessions = %s; window.__link = %s;
window.__resizes = []; window.__streamMode = 'ok'; window.__seq = 0; window.__pulls = 0;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
function stream(pid, rsid, since) {
  window.__pulls++;
  const m = window.__streamMode;
  if (m === 'err') return {success: false, message: '連不上'};
  if (m === 'lost') { window.__streamMode = 'ok'; window.__seq = 0; return {success: true, seq: 0, data: '', reset: true}; }
  if (since < 0) return {success: true, seq: window.__seq, data: '', snapshot: '\\x1b[2J\\x1b[H示範畫面', reset: true};
  return {success: true, seq: ++window.__seq, data: ''};
}
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's101'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'link_status': return R(window.__link);
      case 'link_remote_tabs': return R({success: true, details: {sessions: [{sid: 'r1', label: '示範遠端分頁', cmd: 'codex', agent_state: 'done'}]}});
      case 'link_remote_stream': return R(stream(...args));
      case 'link_remote_resize': window.__resizes.push({cols: args[2], rows: args[3], t: Date.now()}); return R({success: true});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(LOCAL, ensure_ascii=False), json.dumps({"enabled": True, "peers": PEERS}, ensure_ascii=False))

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1300, "height": 820})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("!!(window.__sfHost && window.__sfHost.openRemote && window.__link)", timeout=30000)
    page.wait_for_timeout(500)

    print("opening a remote tab")
    page.evaluate("window.__sfHost.openRemote('mac1', 'r1', '示範遠端分頁')")
    page.wait_for_function("window.__resizes.length >= 1", timeout=10000)
    page.wait_for_timeout(1500)
    sz = page.evaluate("window.__resizes")
    check("our size goes to the other side when the tab is opened", len(sz) >= 1 and sz[0]["cols"] > 80 and sz[0]["rows"] > 20, sz)
    check("…once: the redraw it causes does not send it again", len(sz) == 1, sz)

    print("the other computer restarts (the stream fails, then comes back)")
    page.evaluate("window.__streamMode = 'err'")
    page.wait_for_timeout(3600)                  # past the 3 s gap since the size last went
    before = page.evaluate("window.__resizes.length")
    page.evaluate("window.__streamMode = 'ok'")
    page.wait_for_function(f"window.__resizes.length > {before}", timeout=10000)
    after = page.evaluate("window.__resizes")
    check("when the stream comes back, our size goes again, the same size",
          len(after) == before + 1 and after[-1]["cols"] == sz[0]["cols"] and after[-1]["rows"] == sz[0]["rows"], after)

    print("the other side lost the stream without an error (a quick restart)")
    page.wait_for_timeout(3200)
    before = page.evaluate("window.__resizes.length")
    page.evaluate("window.__streamMode = 'lost'")
    page.wait_for_function(f"window.__resizes.length > {before}", timeout=10000)
    check("an unasked re-attach sends our size again", page.evaluate("window.__resizes.length") == before + 1)

    print("no loop")
    page.wait_for_timeout(2500)
    n = page.evaluate("window.__resizes.length")
    page.wait_for_timeout(3500)
    check("a steady stream sends nothing more", page.evaluate("window.__resizes.length") == n, page.evaluate("window.__resizes.length"))
    page.evaluate("window.__streamMode = 'lost'")
    page.wait_for_timeout(300)
    page.evaluate("window.__streamMode = 'lost'")
    page.wait_for_timeout(1500)
    check("two losses within 3 s send it at most once", page.evaluate("window.__resizes.length") <= n + 1, page.evaluate("window.__resizes.length"))

    print("the window changes size, as before")
    page.wait_for_timeout(3200)
    before = page.evaluate("window.__resizes.length")
    page.set_viewport_size({"width": 1000, "height": 700})
    page.wait_for_function(f"window.__resizes.length > {before}", timeout=10000)
    last = page.evaluate("window.__resizes[window.__resizes.length - 1]")
    check("a smaller window sends the smaller size", last["cols"] < sz[0]["cols"], last)
    check("no page errors", not errors, errors[:3])
    browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
