#!/usr/bin/env python3
"""The model chip on an AI tab and its pop-up: web/index.html in a browser with a stand-in Python side
(docs/tab-model.md; web/tabmodel.js).

  - the chip says which model the tab runs, "unknown" when ShellBot cannot read it; it is in the chat
    view's header, the terminal's header in the chat style, and the tab bar in the native CLI style
  - clicking it lists the models the Python side gives (no list of its own) and the thinking levels; a pick
    and Switch send exactly that to tab_model_set
  - a tab in a turn: the chip says it waits, with a Cancel; text in the input box: asked about (wait / clear /
    cancel), never overwritten; a box that cannot be read: asked first; a failure: its reason, in red
  - a CLI that has to be started again says so; for a remembered bot the existing "save and switch now"
  - after a switch of a remembered bot: "keep it as the bot's model?" and nothing is saved until yes
Screenshots (WebKit only, demo data) go to qa-shots/tab-model-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_tab_model_ui.py
"""
import copy
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
    print("SKIP  tests_tab_model_ui.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {"助理": {"label": "助理", "cmd": "claude",
                   "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}}}
BOTS = {"enabled": True, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
        "sessions": {"s501": {"bot": "助理"}}, "kinds": {"s501": "bot"}, "roster": ROSTER}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "助理", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "幫我看一下這份示範文件"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "好的，這份示範文件有三段，我整理成重點給你。"}]}
EFFORTS = [{"key": k, "label": lab} for k, lab in (("low", "低"), ("medium", "中"), ("high", "高"), ("xhigh", "超高"), ("max", "最高"))]
CLAUDE = {"success": True, "ai": True, "sid": "s501", "cli": "claude", "cli_name": "Claude Code", "in_session": True, "restart_note": "",
          "current": {"name": "Opus 5.5", "effort": "high", "from": "status"}, "current_key": "opus",
          "models": [{"key": "", "label": "CLI 預設"}, {"key": "opus", "label": "Opus"}, {"key": "sonnet", "label": "Sonnet"},
                     {"key": "haiku", "label": "Haiku"}, {"key": "demo-x", "label": "示範用模型"}],
          "custom": True, "efforts": EFFORTS, "persists": False, "unverified": False,
          "bot": {"role": "助理", "label": "助理", "can_save": True}, "job": None}
PI = dict(CLAUDE, cli="pi", cli_name="Pi", current=None, current_key="", models=[], persists=True,
          efforts=[{"key": k, "label": k} for k in ("off", "minimal", "low", "medium", "high")], bot=None)
RESTART = dict(CLAUDE, cli="agy", cli_name="Antigravity", in_session=False, restart_note="Antigravity 沒辦法在對話中換模型，要重開才能換。", efforts=[])


def stub(style):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": style, "experimental_bots": True},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": ROSTER}
    return """
window.__cfg = %s; window.__sessions = %s; window.__view = %s; window.__bots = %s;
window.__info = {}; window.__calls = []; window.__setReply = {success: true, state: 'switching', id: 'j1'};
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
      case 'tab_model_info': return R(window.__info[args[0]] || {success: false});
      case 'tab_model_set': window.__calls.push(['set', JSON.parse(args[1])]); return R(window.__setReply);
      case 'tab_model_cancel': case 'tab_model_dismiss': window.__calls.push([prop.replace('tab_model_', '')]); return R({success: true});
      case 'tab_model_save_bot': window.__calls.push(['save_bot', args[1]]); return R({success: true});
      case 'bots_switch': window.__calls.push(['bots_switch', ...args]); return R({success: true, pending: true});
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, SESSIONS, VIEW, BOTS))


SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
POP = "(() => { const p = document.getElementById('tm-pop'); return !!p && !p.hidden && p.getClientRects().length > 0; })()"


def open_page(browser, style, info):
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(style))
    page.goto((HERE / "web" / "index.html").as_uri())
    if style == "chat":
        page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(800)
    setinfo(page, info)
    return page, errors


def setinfo(page, info):
    page.evaluate("i => { window.__info.s501 = i; return window.SFTabModel.refresh('s501', true); }", info)
    page.wait_for_timeout(500)


def calls(page):
    return page.evaluate("window.__calls")


def run(browser, engine):
    shot = lambda page, name: page.screenshot(path=str(SHOTS / f"tab-model-{name}.png")) if engine == "webkit" else None

    print(f"[{engine}] 1. the chip in the chat view's header")
    page, errors = open_page(browser, "chat", CLAUDE)
    chip = page.locator("#chat-head .tm-chip")
    check("one chip, naming the model and its level", chip.count() == 1 and chip.inner_text().strip().startswith("Opus 5.5 · high"), chip.all_inner_texts())
    check("…with a hint", "換這個分頁的模型" in (chip.get_attribute("title") or ""))
    page.evaluate("window.SFChat.setMode('s501', 'cli')")
    page.wait_for_timeout(500)
    check("chat style, the terminal showing: the terminal's header has it too", page.locator("#term-head .tm-chip").count() == 1
          and page.locator("#term-head .tm-chip").inner_text().strip().startswith("Opus 5.5"))
    page.evaluate("window.SFChat.setMode('s501', 'chat')")
    page.wait_for_timeout(400)
    setinfo(page, dict(CLAUDE, current=None, current_key=""))
    check("a model ShellBot cannot read says unknown, it does not guess", "模型：未知" in page.locator("#chat-head .tm-chip").inner_text())
    setinfo(page, dict(CLAUDE, ai=False))
    check("a terminal tab has no chip", page.locator(".tm-chip").count() == 0)
    setinfo(page, CLAUDE)

    print(f"[{engine}] 2. the pop-up: the list is the Python side's")
    page.locator("#chat-head .tm-chip").click()
    page.wait_for_timeout(300)
    labels = page.locator("#tm-pop .tm-opt").all_inner_texts()
    check("opens under the chip, with the models it was given (a made-up one proves nothing is hard-coded)",
          page.evaluate(POP) and [x.split(" ")[0] for x in labels] == ["CLI", "Opus", "Sonnet", "Haiku", "示範用模型"], labels)
    check("the current one is marked", "✓" in page.locator('#tm-pop [data-key="opus"]').inner_text())
    check("the thinking levels are there, the current one marked", page.locator("#tm-pop .tm-seg button").count() == 5
          and "cur" in page.locator('#tm-pop .tm-seg [data-key="high"]').get_attribute("class"))
    check("nothing picked: Switch is off", page.locator("#tm-pop .tm-go").is_disabled())
    shot(page, "popup-claude")
    page.locator('#tm-pop [data-key="sonnet"]').click()
    page.locator('#tm-pop .tm-seg [data-key="low"]').click()
    page.locator("#tm-pop .tm-go").click()
    page.wait_for_timeout(400)
    sent = [c[1] for c in calls(page) if c[0] == "set"]
    check("a pick and a level go to tab_model_set exactly as picked", sent == [{"model": "sonnet", "effort": "low", "custom": False}], sent)
    page.mouse.click(900, 600)
    page.wait_for_timeout(200)
    check("a click elsewhere closes it", not page.evaluate(POP))
    page.locator("#chat-head .tm-chip").click()
    page.locator(".tm-custom").fill("claude-opus-4-8")
    page.locator("#tm-pop .tm-go").click()
    page.wait_for_timeout(300)
    check("a typed name is sent as one, to be checked on the Python side", calls(page)[-1][1] == {"model": "claude-opus-4-8", "effort": None, "custom": True}, calls(page)[-1])
    page.mouse.click(900, 600)

    print(f"[{engine}] 3. a tab in a turn waits")
    job = {"id": "j1", "state": "waiting", "model": "haiku", "effort": None, "reason": "working", "message": "", "code": ""}
    setinfo(page, dict(CLAUDE, job=job))
    check("the chip says it will switch when the tab is done", "等它做完再換" in page.locator("#chat-head .tm-chip").inner_text())
    page.locator("#chat-head .tm-chip").click()
    page.wait_for_timeout(300)
    check("the pop-up says why and offers Cancel", "它正在跑" in page.locator("#tm-pop .tm-job").inner_text() and page.locator('#tm-pop [data-tm="cancel"]').count() == 1)
    shot(page, "waiting")
    page.locator('#tm-pop [data-tm="cancel"]').click()
    page.wait_for_timeout(300)
    check("Cancel asks the Python side to drop it", ["cancel"] in calls(page))
    page.mouse.click(900, 600)
    setinfo(page, CLAUDE)

    print(f"[{engine}] 4. text in the input box is asked about, never written over")
    page.evaluate("window.__setReply = {success: false, code: 'draft', draft: '這是我打到一半的句子', message: '這個分頁的輸入框裡有你還沒送出的字。'}")
    page.locator("#chat-head .tm-chip").click()
    page.locator('#tm-pop [data-key="haiku"]').click()
    page.locator("#tm-pop .tm-go").click()
    page.wait_for_timeout(400)
    card = page.locator("#tm-pop .tm-card")
    check("a card shows the text and the three choices", card.count() == 1 and "這是我打到一半的句子" in card.inner_text()
          and page.locator('#tm-pop [data-tm="draft-wait"]').count() == 1 and page.locator('#tm-pop [data-tm="draft-clear"]').count() == 1, card.all_inner_texts())
    shot(page, "draft")
    page.evaluate("window.__setReply = {success: true, state: 'waiting', id: 'j2'}")
    page.locator('#tm-pop [data-tm="draft-wait"]').click()
    page.wait_for_timeout(400)
    last = calls(page)[-1][1]
    check("'switch after I send it' asks again with draft: wait, and the same pick", last.get("draft") == "wait" and last.get("model") == "haiku", last)
    page.mouse.click(900, 600)
    page.evaluate("window.__setReply = {success: false, code: 'unreadable', message: 'ShellBot 讀不到這個分頁的輸入框，不知道裡面有沒有你打到一半的字。'}")
    page.locator("#chat-head .tm-chip").click()
    page.locator('#tm-pop [data-key="sonnet"]').click()
    page.locator("#tm-pop .tm-go").click()
    page.wait_for_timeout(300)
    check("a box that cannot be read: asked first", page.locator('#tm-pop [data-tm="go-anyway"]').count() == 1)
    page.evaluate("window.__setReply = {success: true, state: 'switching', id: 'j3'}")
    page.locator('#tm-pop [data-tm="go-anyway"]').click()
    page.wait_for_timeout(300)
    check("…and 'switch anyway' says unreadable_ok", calls(page)[-1][1].get("unreadable_ok") is True, calls(page)[-1])
    page.mouse.click(900, 600)

    print(f"[{engine}] 5. a failure is shown as one")
    bad = {"id": "j4", "state": "failed", "model": "opus", "effort": None, "reason": "", "code": "not_listed",
           "message": "Claude Code 的選單裡沒有「opusplan」（有：Default (recommended), Opus 5.5, Sonnet 5.5）。"}
    setinfo(page, dict(CLAUDE, job=bad))
    check("the chip says it was not switched", "沒換成" in page.locator("#chat-head .tm-chip").inner_text()
          and "tm-bad" in page.locator("#chat-head .tm-chip").get_attribute("class"))
    check("…and the pop-up opened by itself (the switch was started here) and says why",
          page.evaluate(POP) and "選單裡沒有" in page.locator("#tm-pop .tm-job").inner_text())
    shot(page, "failed")
    page.locator('#tm-pop [data-tm="dismiss"]').click()
    page.wait_for_timeout(300)
    check("OK forgets it", ["dismiss"] in calls(page))
    page.mouse.click(900, 600)
    setinfo(page, CLAUDE)

    print(f"[{engine}] 6. a remembered bot: keep it? (not saved unless yes)")
    page.evaluate("window.__calls.length = 0")
    done = {"id": "j5", "state": "ok", "model": "sonnet", "effort": "low", "reason": "", "code": "", "name": "Sonnet 5.5", "message": "已換成 Sonnet 5.5 · low"}
    page.evaluate("window.__setReply = {success: true, state: 'switching', id: 'j5'}")
    page.locator("#chat-head .tm-chip").click()
    page.locator('#tm-pop [data-key="sonnet"]').click()
    page.locator("#tm-pop .tm-go").click()
    page.wait_for_timeout(300)
    setinfo(page, dict(CLAUDE, job=done, current={"name": "Sonnet 5.5", "effort": "low", "from": "switch"}, current_key="sonnet"))
    check("the switch ended: the pop-up opens by itself with the question, default not saved",
          page.evaluate(POP) and "也存成「助理」的預設模型" in page.locator("#tm-pop .tm-ask").inner_text()
          and not [c for c in calls(page) if c[0] in ("save_bot", "bots_switch")], page.locator("#tm-pop").inner_text())
    check("the chip already shows what the CLI reported", "Sonnet 5.5 · low" in page.locator("#chat-head .tm-chip").inner_text())
    page.locator('#tm-pop [data-tm="bot-no"]').click()
    page.wait_for_timeout(200)
    check("'No' saves nothing and the question goes", not [c for c in calls(page) if c[0] == "save_bot"] and page.locator("#tm-pop .tm-ask").count() == 0)
    page.mouse.click(900, 600)
    setinfo(page, CLAUDE)
    page.evaluate("window.__calls.length = 0")
    page.evaluate("window.__setReply = {success: true, state: 'switching', id: 'j6'}")
    page.locator("#chat-head .tm-chip").click()
    page.locator('#tm-pop [data-key="haiku"]').click()
    page.locator("#tm-pop .tm-go").click()
    page.wait_for_timeout(300)
    setinfo(page, dict(CLAUDE, job=dict(done, id="j6", model="haiku", name="Haiku 5.5")))
    page.locator('#tm-pop [data-tm="bot-yes"]').click()
    page.wait_for_timeout(300)
    check("'Keep it' saves the model, for the next wake", ["save_bot", "haiku"] in calls(page), calls(page))
    page.mouse.click(900, 600)
    page.close()

    print(f"[{engine}] 7. a CLI with no list of its own, and one that cannot switch inside a tab")
    page, errors = open_page(browser, "chat", PI)
    check("no model known: unknown, and no pretending", "模型：未知" in page.locator("#chat-head .tm-chip").inner_text())
    page.locator("#chat-head .tm-chip").click()
    page.wait_for_timeout(300)
    check("the pop-up has the name box and says the CLI keeps the pick itself", page.locator("#tm-pop .tm-opt").count() == 0
          and "打模型名稱" in page.locator(".tm-custom").get_attribute("placeholder") and "自己會把你選的存成新分頁的預設" in page.locator("#tm-pop").inner_text())
    page.mouse.click(900, 600)
    setinfo(page, RESTART)
    page.locator("#chat-head .tm-chip").click()
    page.wait_for_timeout(300)
    check("'this one has to be restarted to change'", "要重開才能換" in page.locator("#tm-pop .tm-note").inner_text() and page.locator("#tm-pop .tm-seg").count() == 0)
    check("a remembered bot gets 'save and switch now' (disabled until a model is picked)", page.locator('#tm-pop [data-tm="restart"]').is_disabled())
    shot(page, "restart")
    page.locator('#tm-pop [data-key="haiku"]').click()
    page.locator('#tm-pop [data-tm="restart"]').click()
    page.wait_for_timeout(400)
    page.locator(".sf-dialog .btn-primary, .sf-dialog button").last.click()
    page.wait_for_timeout(400)
    check("…and it is the existing bots_switch, now", ["bots_switch", "助理", "", "haiku", "now"] in calls(page), calls(page))
    page.close()

    print(f"[{engine}] 8. the native CLI style: the tab bar has it")
    page, errors = open_page(browser, "cli", CLAUDE)
    slot = page.locator("#tab-bar #tm-slot .tm-chip")
    check("one chip in the tab bar, none in a header that is not there", slot.count() == 1 and slot.inner_text().strip().startswith("Opus 5.5 · high")
          and page.locator("#chat-head .tm-chip").count() == 0, slot.all_inner_texts())
    slot.click()
    page.wait_for_timeout(300)
    check("it opens the same pop-up", page.evaluate(POP) and page.locator("#tm-pop .tm-opt").count() == 5)
    shot(page, "cli-style")
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as p:
    SHOTS.mkdir(exist_ok=True)
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(p, engine).launch()
        except Exception as e:                       # webkit is optional: `playwright install webkit`
            print(f"SKIP  {engine}: {str(e)[:80]}")
            continue
        run(browser, engine)
        browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
