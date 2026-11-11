#!/usr/bin/env python3
"""A message from another agent in the chat view: web/index.html in a browser with a stand-in
Python side, the turns built with agent_link's own headers (format_delivery). The header can
hold an [[SF:TO:…]] of its own; the bubble used to keep its tail ("…回覆它]]"), and the message
was drawn as the person's own (a blue bubble on the right with a small "from" under it).
  - several A2A messages in a row, A2A between the person's own messages, one long A2A: no
    bubble shows any of the header ("]]", "SF:TO", "不是使用者本人"); each A2A is on the left
    with its sender's face (a bot's own, else one made from its name) and name above it; the
    person's own words stay on the right; the message itself is there whole
Only what is drawn changes: the CLI gets the header as it was (agent_link is not touched).
Demo data only. Screenshots: qa-shots/chat-a2a-{row,mixed,long}.png. SF_A2A_BEFORE=<a chat.js>
draws the same with that file instead and saves …-before.png, without checks.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_a2a_ui.py
"""
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
sys.path.insert(0, str(HERE))
import agent_link  # noqa: E402

passed = failed = 0
BEFORE = os.environ.get("SF_A2A_BEFORE", "")


def check(name, ok, detail=""):
    global passed, failed
    if BEFORE:
        return
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_chat_a2a_ui.py（沒裝 playwright）")
    sys.exit(0)


def a2a(who, text):
    return {"kind": "user_msg", "text": agent_link.format_delivery(who, text, lang="zh")}


def you(text):
    return {"kind": "user_msg", "text": text}


def bot(text):
    return {"kind": "assistant_text", "text": text}


LONG = ("比較表整理好了，重點如下：\n\n"
        "1. 三家的價格差不多，差在維護費：第一家含在年費裡，第二家另外收，第三家依工時計。\n"
        "2. 上線時間：第一家最快，兩週；另外兩家要四到六週，因為要等他們的排程。\n"
        "3. 資料放在哪裡：只有第二家能放在我們自己的機房，另外兩家都是他們的雲端。\n\n"
        "我的建議是先跟第二家談，因為資料要留在自己的機房；價格的部分可以拿第一家的報價去談。"
        "細節都寫在 docs/compare.md，表格最後一欄是我查到的出處，有疑問的地方我用黃色標起來了。"
        "如果要我先寫一封詢問信給第二家，跟我說一聲，我會先給你看草稿再寄。")
CASES = {
    "row": [a2a("研究", "比較表整理好了，在 docs/compare.md。"),
            a2a("寫程式", "relay 的 timeout 已經調成 30 秒，測試都過了。"),
            a2a("排程", "明天 10 點的會議改到 11 點。"),
            bot("收到三則：我先看比較表，再回覆會議時間。")],
    "mixed": [you("幫我問研究，比較表做好了沒"),
              bot("已經用 A2A 問了研究。"),
              a2a("研究", "做好了，在 docs/compare.md。"),
              bot("研究說做好了，在 docs/compare.md。"),
              you("好，謝謝")],
    "long": [a2a("研究", LONG), bot("收到，我整理成三點回給你。")],
}
SENDERS = {"row": ["研究", "寫程式", "排程"], "mixed": ["研究"], "long": ["研究"]}
BOTS = {"enabled": True, "primary": "", "memory_root": "~/.config/shellframe/bots", "sessions": {}, "kinds": {},
        "roster": {"研究": {"label": "研究", "cmd": "claude", "bot": {"avatar": {"shape": "hex", "color": "blue"}, "memory": True}},
                   "寫程式": {"label": "寫程式", "cmd": "codex", "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True}}}}
CFG = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat", "experimental_bots": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s401", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]


def stub(turns):
    for i, t in enumerate(turns):
        t["ts"] = 1794190800 + i * 20
    view = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None,
            "rev": "r1", "turns": turns}
    return """
window.__cfg = %s; window.__sessions = %s; window.__view = %s; window.__bots = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's401'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      case 'bots_state': return R(window.__bots);
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(view, ensure_ascii=False),
       json.dumps(BOTS, ensure_ascii=False))


SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for name, turns in CASES.items():
        page = browser.new_page(viewport={"width": 900, "height": 900 if name != "long" else 1000}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        if BEFORE:
            old = pathlib.Path(BEFORE).read_text(encoding="utf-8")
            page.route("**/chat.js", lambda route: route.fulfill(status=200, content_type="text/javascript", body=old))
        page.add_init_script(stub(turns))
        page.goto((HERE / "web" / "index.html").as_uri())
        page.wait_for_function(f"document.querySelectorAll('#chat-list .cm-row').length >= {len(turns)}", timeout=15000)
        print(f"{name}: {len(turns)} turns")
        bubbles = page.evaluate("[...document.querySelectorAll('#chat-list .cm-bubble')].map(b => b.innerText)")
        names = page.evaluate("[...document.querySelectorAll('#chat-list .cm-peer .cm-peer-name')].map(m => m.innerText)")
        faces = page.evaluate("[...document.querySelectorAll('#chat-list .cm-peer .cm-peer-face')].map(f => !!f.querySelector('svg'))")
        mine = page.evaluate("[...document.querySelectorAll('#chat-list .cm-user .cm-bubble')].map(b => b.innerText)")
        leftover = [b for b in bubbles if any(x in b for x in ("]]", "SF:TO", "不是使用者本人", "回覆它"))]
        check("no bubble shows any of the A2A header", not leftover, leftover)
        if not BEFORE:
            check("each A2A is on the left, its sender's name above it", names == SENDERS[name], names)
            check("…with its sender's face", faces and all(faces), faces)
            check("the person's own words stay on the right", mine == [t["text"] for t in turns if t["kind"] == "user_msg"
                                                                       and not t["text"].startswith("[[A2A")], mine)
        bodies = [t["text"].split("\n", 1)[1] for t in turns if t["kind"] == "user_msg" and t["text"].startswith("[[A2A")]
        check("the message itself is there, from its first word",
              all(any(b.startswith(body[:12]) for b in bubbles) for body in bodies), (bodies, bubbles))
        check("no page errors", not errors, errors)
        page.locator("#chat-layer").screenshot(path=str(SHOTS / f"chat-a2a-{name}{'-before' if BEFORE else ''}.png"))
        page.close()
    browser.close()

if BEFORE:
    print("before shots saved")
    sys.exit(0)
print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
