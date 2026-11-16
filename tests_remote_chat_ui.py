#!/usr/bin/env python3
"""Another computer's tab follows this computer's mode (the chat view or the terminal) and says it is remote.

Asked for: a tab opened from the sidebar's other-computer section should be shown the way local tabs are
(the chat view in chat style), with a reminder that it runs on another computer.
  - in chat style a remote AI tab opens as a conversation; its turns come from that computer (link_chat
    'view' → its POST /link/chat), not from this computer's chat_view
  - the header says "遠端 · <machine>"; the message box names the machine
  - a message goes to that computer (link_chat 'send', by name: text, clear_draft); the switch takes it to
    its terminal and back, and the terminal's header keeps the reminder
  - a computer on an older ShellBot (no /link/chat: 404 → old_peer): its terminal, and a line saying to update
  - a local tab still reads its own chat_view
Run in Chromium. Demo data only. Screenshots: qa-shots/remote-chat-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_remote_chat_ui.py
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
    print("SKIP  tests_remote_chat_ui.py（沒裝 playwright）")
    sys.exit(0)

PEER = {"id": "peer7", "name": "Demo-MacBook", "host": "198.51.100.7", "port": 1234,
        "reachable": True, "can_control": True, "mode": "duplex"}
REMOTE = [{"sid": "r1", "label": "示範助理", "cmd": "claude", "agent_state": "done"}]
LOCAL = [{"sid": "s101", "cmd": "claude", "alive": True, "label": "本機分頁", "bridge_enabled": True}]
TURNS = [{"kind": "user_msg", "ts": 1794190841, "text": "幫我整理這週的筆記"},
         {"kind": "assistant_text", "ts": 1794190843, "text": "好的，這週有三件事：示範一、示範二、示範三。"}]


def stub(old_peer):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "chat", "experimental_bots": False},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    body = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None,
            "rev": "r1", "turns": TURNS, "login": {}, "queue": {"queued": [], "given_up": []}}
    return """
window.__calls = []; window.__cfg = %s; window.__sessions = %s; window.__link = %s; window.__remote = %s;
window.__body = %s; window.__old = %s;
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
      case 'link_remote_tabs': return R({success: true, details: {sessions: window.__remote}});
      case 'link_chat':
        window.__calls.push(['link_chat', args[0], args[1], args[2], JSON.parse(args[3] || '{}')]);
        if (window.__old) return R({success: false, code: 'old_peer'});
        if (args[2] === 'send') return R({success: true, queued: false, id: 'q1'});
        return R(window.__body);
      case 'chat_view': window.__calls.push(['chat_view', args[0]]);
        return R(Object.assign({}, window.__body, {turns: [{kind: 'assistant_text', ts: 1, text: '本機的對話'}]}));
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, LOCAL, {"enabled": True, "peers": [PEER]}, REMOTE, body, old_peer))


def open_remote(page):
    page.wait_for_selector("#sidebar-sessions .sb-item.sb-remote", timeout=30000)
    page.click("#sidebar-sessions .sb-item.sb-remote")
    page.wait_for_timeout(800)


with sync_playwright() as pw:
    browser = pw.chromium.launch()

    print("a remote tab in chat style")
    page = browser.new_page(viewport={"width": 1100, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(False))
    page.goto((HERE / "web" / "index.html").as_uri())
    open_remote(page)
    page.wait_for_selector("#chat-layer:not([hidden]) .cm-bubble", timeout=10000)
    calls = page.evaluate("window.__calls")
    check("it opens as a conversation, read from that computer",
          any(c[:4] == ["link_chat", "peer7", "r1", "view"] for c in calls), calls)
    check("…not from this computer's chat_view", not any(c[0] == "chat_view" and c[1] != "s101" for c in calls), calls)
    check("its turns are drawn", "示範一" in page.inner_text("#chat-layer"))
    head = page.inner_text("#chat-layer .cm-who")
    check("the header says it is remote, and which computer", "遠端 · Demo-MacBook" in head, head)
    ph = page.get_attribute("#chat-layer textarea, #chat-layer input", "placeholder") or ""
    check("the message box names the computer", "Demo-MacBook" in ph, ph)
    page.locator("#chat-layer").screenshot(path=str(HERE / "qa-shots" / "remote-chat-view.png"))
    box = page.locator("#chat-layer textarea, #chat-layer input").first
    box.fill("再幫我列一下下週")
    box.press("Enter")
    page.wait_for_timeout(500)
    sent = [c for c in page.evaluate("window.__calls") if c[0] == "link_chat" and c[3] == "send"]
    check("a message goes to that computer, by name", sent and sent[-1][4] == {"text": "再幫我列一下下週", "clear_draft": False}, sent)

    print("its terminal")
    page.click("#chat-layer [data-act='to-term']")
    page.wait_for_timeout(400)
    check("the switch takes it to its terminal", page.evaluate("document.getElementById('chat-layer').hidden"))
    th = page.inner_text("#term-head") if page.query_selector("#term-head") else ""
    check("…and the terminal's header keeps the reminder", "遠端 · Demo-MacBook" in th, th)
    page.screenshot(path=str(HERE / "qa-shots" / "remote-chat-terminal.png"))
    check("no page errors", not errors, errors)
    page.close()

    print("a computer on an older ShellBot")
    page = browser.new_page(viewport={"width": 1100, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(True))
    page.goto((HERE / "web" / "index.html").as_uri())
    open_remote(page)
    page.wait_for_timeout(800)
    check("its terminal shows", page.evaluate("document.getElementById('chat-layer').hidden"))
    note = page.inner_text("#chat-notice") if page.query_selector("#chat-notice") else page.evaluate(
        "[...document.querySelectorAll('.cm-notice, #chat-notice')].map(e => e.innerText).join(' ')")
    check("…with a line saying to update that computer", "Demo-MacBook" in note and "0.46.1" in note, note)
    page.screenshot(path=str(HERE / "qa-shots" / "remote-chat-old-peer.png"))
    check("no page errors", not errors, errors)
    page.close()
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
