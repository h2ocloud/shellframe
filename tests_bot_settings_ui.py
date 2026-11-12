#!/usr/bin/env python3
"""A bot's settings can be found and changed afterwards, and the model is picked from a list:
web/index.html in a browser with a stand-in Python side. The model lists and the live check of a
typed key are the real ones: the page's calls go to the real Api.bots_model_info / bots_model_check.

Reported in daily use: after creating the primary bot (the editor asked for its model and job) there was
nowhere to change them. The editor was always there, but only behind a right-click on an awake bot's face;
a sleeping bot (the primary one most often) had no way in, and Settings had no list. And the model was a
text box where a key that was not exactly the CLI's was stored and did nothing.
  - three ways in, for the primary bot and any other, asleep or awake:
      the card (its Edit, or a right-click) and the conversation's big face,
      Edit on a bot's row in the sidebar, Settings → Bots (a list, Edit on each)
  - the editor asks for the same things it asked at creation: name, face, job, CLI, model, skills
  - the model is a list for the CLI ("CLI default" first, "Other (custom)" last), greyed for a CLI that
    picks its model inside it; another CLI starts the choice over; what is sent is the key
  - under "Other (custom)" a typed key is checked on the spot: "unknown model" and the likely key, and
    nothing is saved until it is right; a key the bot already had is flagged, not blocking
  - the last step of creating or keeping a bot says where to change it afterwards
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Screenshots go to qa-shots/botset-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_bot_settings_ui.py
"""
import json
import pathlib
import sys
from unittest.mock import MagicMock

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
    print("SKIP  tests_bot_settings_ui.py（沒裝 playwright）")
    sys.exit(0)

sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import agent_model  # noqa: E402
import agent_status  # noqa: E402
from main import Api  # noqa: E402

backend = Api.__new__(Api)                       # bots_model_info / bots_model_check need no state

CLAUDE, CODEX = "claude --dangerously-skip-permissions", "codex"
CLI_NAMES = {"claude": "Claude Code", "codex": "Codex", "pi": "Pi", "opencode": "OpenCode", "agy": "Antigravity",
             "grok": "Grok Build", "cursor": "Cursor Agent"}
PRESETS = [{"name": "Claude Code", "cmd": CLAUDE, "icon": "C"}, {"name": "Codex", "cmd": CODEX, "icon": "X"},
           {"name": "Grok Build", "cmd": "grok", "icon": "G"}, {"name": "Pi", "cmd": "pi", "icon": "P"},
           {"name": "OpenCode", "cmd": "opencode", "icon": "O"}]


def row(label, cmd, model, job, avatar, primary=False):
    """A roster row as the page gets it from bots_state: with what the role runs on and whether ShellBot knows it."""
    kind = agent_status.worker_kind(cmd)
    return {"label": label, "responsibility": job, "cmd": cmd, "model": model, "coordinator": False, "former_names": [],
            "bot": {"avatar": avatar, "memory": True, "primary": primary, "skills": []},
            "pin": agent_model.pinned(cmd, model),
            "cli": {"key": "" if kind == "other" else kind, "label": CLI_NAMES.get(kind, "")}}


ROSTER = {
    "助理": row("助理", CLAUDE, "sonnet", "日常任務的第一站，盤點與派工", {"shape": "star", "color": "purple"}, primary=True),
    "寫程式": row("寫程式", CLAUDE, "opus", "程式、測試、部署", {"shape": "pill", "color": "red"}),
    "研究": row("研究", CODEX, "gpt-5-codex", "資料調研與彙整", {"shape": "hex", "color": "blue"}),
    "舊設定": row("舊設定", CLAUDE, "sonet", "以前手打的模型，少了一個 n", {"shape": "cloud", "color": "green"}),
}
TABS = [("s2", "寫程式", CLAUDE, "working", {"action": "Editing relay.py"}),
        ("s3", "草稿", CLAUDE, "idle", {"narration": "先放著"}),
        ("s4", "zsh", "zsh", "idle", {})]
DOT = {"working": "sig-working", "decision": "sig-decision", "done": "sig-done", "stuck": "sig-stuck", "idle": ""}
SESSIONS = [{"sid": sid, "cmd": cmd, "alive": True, "label": label, "bridge_enabled": True} for sid, label, cmd, *_ in TABS]
STATUS = {sid: dict({"state": st, "dot": DOT[st], "summary": st}, **extra) for sid, _, _, st, extra in TABS}
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}

STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s; window.__view = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: window.__active || 's2'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      case 'bots_state': return R(window.__bots);
      case 'bots_skill_catalog': return R({skills: [{name: 'demo-skill', description: 'demo'}]});
      case 'bots_model_info': return window.__model_info(args[0]);      // the real Api, through Playwright
      case 'bots_model_check': return window.__model_check(args[0], args[1]);
      case 'bots_open': return R(window.__open || {success: true, details: {sid: 's9'}});
      case 'bots_save': return R({success: true, message: '已更新', details: {role: args[0], label: args[0]}});
      case 'bots_keep': return R({success: true, role: args[1] || args[0]});
      case 'bots_switch': return R({success: true});
      default: return R('{}');
    }
  };
}})};
setInterval(() => { try { window.__sfAgentStatus && window.__sfAgentStatus(window.__status); } catch (e) {} }, 500);
"""

REAL = {"bots_model_info": backend.bots_model_info, "bots_model_check": backend.bots_model_check}


def build(style, lang, awake_primary, sleeping=True):
    roster = {k: dict(v) for k, v in ROSTER.items()}
    sessions = list(SESSIONS)
    bound = {"s2": {"bot": "寫程式"}}
    if awake_primary:
        sessions = [{"sid": "s1", "cmd": CLAUDE, "alive": True, "label": "助理", "bridge_enabled": True}] + sessions
        bound["s1"] = {"bot": "助理"}
    state = {"enabled": True, "roster": roster, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
             "sessions": bound, "kinds": {"s3": "temp", "s4": "terminal"}}
    cfg = {"settings": {"language": lang, "sidebar_open": True, "ui_style": style, "experimental_bots": True},
           "presets": PRESETS, "agent_roster": {}}
    status = dict(STATUS)
    if awake_primary:
        status["s1"] = {"state": "idle", "dot": "", "summary": "idle", "narration": "盤點好了"}
    return (STUB % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, sessions, state, status, VIEW))
            + "window.__active = %s;" % json.dumps("s1" if awake_primary else "s2"))


POP = "(() => { const p = document.querySelector('.sfb-pop'); return !!p && p.getClientRects().length > 0; })()"
EDITOR = "(() => { const m = document.querySelector('.sfb-modal'); return !!m && m.getClientRects().length > 0; })()"
CALLS = "window.__calls.map(c => c[0])"


def open_page(browser, style="cli", lang="zh-TW", awake_primary=False, width=1100):
    page = browser.new_page(viewport={"width": width, "height": 860}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    for name, fn in REAL.items():
        page.expose_function("__" + {"bots_model_info": "model_info", "bots_model_check": "model_check"}[name], fn)
    page.add_init_script(build(style, lang, awake_primary))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .sfb-two').length >= 2", timeout=20000)
    if style == "cli":                      # the chat style words the line its own way
        page.wait_for_function("(document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s2\"] .sfb-pv') || {}).textContent === 'Editing relay.py'",
                               timeout=15000)
    page.wait_for_timeout(600)
    return page, errors


def calls(page, name):
    return page.evaluate("(n) => window.__calls.filter(c => c[0] === n).map(c => c[1])", name)


def close_editor(page):
    if page.evaluate(EDITOR):
        page.click(".sfb-modal [data-act='cancel']")
        page.wait_for_timeout(150)


def model_options(page):
    return page.evaluate("[...document.querySelectorAll('#sfb-ed-model option')].map(o => [o.value, o.textContent])")


def shot_sidebar(page, name):
    b = page.locator("#sidebar").bounding_box()
    page.screenshot(path=str(SHOTS / name), clip={"x": b["x"], "y": b["y"], "width": b["width"], "height": min(b["height"], 520)})


def run(browser, engine):
    tag = f"-{engine}"
    print(f"[{engine}] 1. the card: the primary bot, asleep")
    page, errors = open_page(browser)
    card_edit = page.locator("#sfb-primary .sfb-card-edit")
    check("the card has an Edit of its own, with a hint saying what it changes",
          card_edit.count() == 1 and "名字、頭像、執掌、CLI、模型、skill" in (card_edit.get_attribute("title") or ""), card_edit.count())
    shot_sidebar(page, f"botset-sidebar{tag}.png")
    card_edit.click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("…it opens the primary bot's editor, not its conversation, and nothing was woken",
          page.input_value("#sfb-ed-name") == "助理" and "bots_open" not in page.evaluate(CALLS), page.evaluate(CALLS))
    check("the editor asks what creating it asked: name, job, CLI, model, skills, face",
          page.evaluate("[...document.querySelectorAll('.sfb-modal .sfb-form > label')].map(l => l.textContent.trim()).slice(0, 6).join('|')")
          == "名稱|執掌|CLI|模型|Skills|頭像")
    check("…with what it has now: its job, CLI, and the model it runs on",
          page.input_value("#sfb-ed-resp") == "日常任務的第一站，盤點與派工" and page.input_value("#sfb-ed-cmd") == CLAUDE
          and page.input_value("#sfb-ed-model") == "sonnet")
    check("…and without the 'where to change it later' note: that is for the editor that makes one",
          page.locator(".sfb-modal .sfb-where").count() == 0)
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / f"botset-editor-from-card{tag}.png"))
    close_editor(page)
    page.locator("#sfb-primary .sfb-card-name").click()
    page.wait_for_timeout(300)
    check("a click on the card itself still wakes the bot (the card's own work), and opens no editor",
          "bots_open" in page.evaluate(CALLS) and not page.evaluate(EDITOR))
    page.locator("#sfb-primary .sfb-card .sfb-avatar").click(button="right")
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("a right-click on the card: the editor too (a sleeping bot has no tab to show settings for)",
          page.input_value("#sfb-ed-name") == "助理")
    close_editor(page)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 2. the card, with the bot awake: the conversation's settings, and Edit bot… in them")
    page, errors = open_page(browser, awake_primary=True)
    page.locator("#sfb-primary .sfb-card .sfb-avatar").click(button="right")
    page.wait_for_timeout(300)
    check("right-click on the card opens the conversation's settings (its tab exists now)", page.evaluate(POP))
    check("…with Edit bot… up in the header, and in the bot's box", page.locator('.sfb-pop [data-act="edit-top"]').count() == 1
          and page.locator('.sfb-pop [data-act="edit"]').count() == 1)
    check("…and what the bot runs on, in words", "Claude Code · Sonnet" in page.inner_text(".sfb-pop .sfb-botbox"),
          page.inner_text(".sfb-pop .sfb-botbox"))
    page.locator('.sfb-pop [data-act="edit-top"]').click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("Edit bot… in the header opens the editor", page.input_value("#sfb-ed-name") == "助理")
    close_editor(page)
    page.close()

    print(f"[{engine}] 3. a bot's row in the sidebar")
    page, errors = open_page(browser)
    row_edit = page.locator('#sidebar-sessions .sb-item[data-sid="s2"] .sfb-edit')
    check("an awake bot's row has Edit, at the end of its second line (the latest line is still the line's own text)", row_edit.count() == 1 and row_edit.inner_text() == "編輯"
          and page.locator('#sidebar-sessions .sb-item[data-sid="s2"] .sfb-sub').inner_text() == "Editing relay.py")
    check("…its name and the Bot tag are still whole", page.locator('#sidebar-sessions .sb-item[data-sid="s2"] .sfb-name').inner_text() == "寫程式Bot"
          and page.evaluate("(() => { const n = document.querySelector('#sidebar-sessions .sb-item[data-sid=\"s2\"] .sfb-name');"
                            " return n.scrollWidth <= n.clientWidth + 1; })()"))
    check("a temporary bot's row has Keep, not Edit; a terminal has neither",
          page.locator('#sidebar-sessions .sb-item[data-sid="s3"] .sfb-keep').count() == 1
          and page.locator('#sidebar-sessions .sb-item[data-sid="s3"] .sfb-edit').count() == 0
          and page.locator('#sidebar-sessions .sb-item[data-sid="s4"] .sfb-edit, #sidebar-sessions .sb-item[data-sid="s4"] .sfb-keep').count() == 0)
    active = page.evaluate("document.querySelector('#sidebar-sessions .sb-item.active').dataset.sid")
    page.locator('#sidebar-sessions .sb-item[data-sid="s3"]').click()          # another tab is the open one
    page.wait_for_timeout(200)
    row_edit.click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("Edit opens that bot's editor, and the click did not switch tabs",
          page.input_value("#sfb-ed-name") == "寫程式" and page.input_value("#sfb-ed-model") == "opus"
          and page.evaluate("document.querySelector('#sidebar-sessions .sb-item.active').dataset.sid") == "s3" and active in ("s2", "s3"))
    close_editor(page)
    sleeper_edit = page.locator('.sfb-sleeper[data-sfb-role="研究"] .sfb-edit')
    check("a sleeping bot's row has Edit beside Wake", sleeper_edit.count() == 1 and sleeper_edit.inner_text() == "編輯"
          and page.locator('.sfb-sleeper[data-sfb-role="研究"] .sfb-wake').count() == 1)
    page.evaluate("window.__calls.length = 0")
    sleeper_edit.click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("…Edit opens its editor and does not wake it", page.input_value("#sfb-ed-name") == "研究"
          and page.input_value("#sfb-ed-cmd") == CODEX and page.input_value("#sfb-ed-model") == "gpt-5-codex"
          and "bots_open" not in page.evaluate(CALLS), page.evaluate(CALLS))
    close_editor(page)
    page.locator('.sfb-sleeper[data-sfb-role="研究"] .sfb-avatar').click(button="right")
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("…and a right-click on a sleeping bot's face does the same", page.input_value("#sfb-ed-name") == "研究")
    close_editor(page)
    page.locator('.sfb-sleeper[data-sfb-role="研究"] .sfb-name').click()
    page.wait_for_timeout(300)
    check("a click on the row itself still wakes it", "bots_open" in page.evaluate(CALLS))
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 4. Settings → Bots")
    page, errors = open_page(browser)
    page.click("#btn-settings")
    page.wait_for_timeout(300)
    tabs = page.evaluate("[...document.querySelectorAll('#settings-tabs-bar .settings-tab')].map(t => t.textContent.trim())")
    check("Settings has a Bots tab, after General", tabs[:2] == ["一般", "Bots"], tabs)
    page.click('#settings-tabs-bar [data-tab="bots"]')
    page.wait_for_timeout(300)
    check("it shows the Bots switch and the list; General no longer holds the switch",
          page.locator("#settings-panel-bots #setting-experimental-bots").count() == 1
          and page.locator("#settings-panel-general #setting-experimental-bots").count() == 0
          and page.locator("#settings-panel-bots.active").count() == 1 and page.locator("#settings-panel-general.active").count() == 0)
    names = page.evaluate("[...document.querySelectorAll('#sfb-settings-list .sfb-bl-row .sfb-bl-name')].map(n => n.firstChild.textContent)")
    check("every bot is listed, the primary first", names == ["助理", "寫程式", "研究", "舊設定"], names)
    rows = page.evaluate("Object.fromEntries([...document.querySelectorAll('#sfb-settings-list .sfb-bl-row')].map(r => [r.dataset.sfbRole, r.innerText.replace(/\\s+/g, ' ')]))")
    check("…each with its CLI and model", "Claude Code · Sonnet" in rows["助理"] and "★ 主要 Bot" in rows["助理"]
          and "Claude Code · Opus" in rows["寫程式"] and "Codex · GPT-5 Codex" in rows["研究"], rows)
    check("…awake or asleep", "醒著" in rows["寫程式"] and "休息中" in rows["研究"], rows)
    check("…a model ShellBot does not know is flagged on its row",
          "這個模型可能沒生效" in rows["舊設定"] and "sonet" in rows["舊設定"] and "可能沒生效" not in rows["助理"], rows["舊設定"])
    page.locator("#modal-settings .modal").screenshot(path=str(SHOTS / f"botset-settings-bots{tag}.png"))
    page.locator('#sfb-settings-list [data-sfb-edit="研究"]').click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("Edit on a row opens that bot's editor (above Settings)", page.input_value("#sfb-ed-name") == "研究"
          and page.evaluate("document.getElementById('modal-settings').classList.contains('open')"))
    close_editor(page)
    page.click("#settings-tabs-bar [data-tab='general']")
    page.wait_for_timeout(200)
    check("the other tabs still switch", page.locator("#settings-panel-general.active").count() == 1
          and page.locator("#settings-panel-bots.active").count() == 0)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 5. the conversation's own face (chat view)")
    page, errors = open_page(browser, style="chat", awake_primary=True)
    page.wait_for_function("(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()", timeout=15000)
    page.locator("#chat-head [data-sfb-settings]").click()
    page.wait_for_timeout(300)
    check("the big face opens the settings, and Edit bot… is in them", page.evaluate(POP)
          and page.locator('.sfb-pop [data-act="edit-top"]').count() == 1)
    page.screenshot(path=str(SHOTS / f"botset-chat-face{tag}.png"), clip={"x": 0, "y": 0, "width": 1100, "height": 330})
    page.locator('.sfb-pop [data-act="edit-top"]').click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    check("…and it opens the editor", page.input_value("#sfb-ed-name") == "助理")
    close_editor(page)
    page.close()

    print(f"[{engine}] 6. the model: a list for the CLI")
    page, errors = open_page(browser)
    page.locator('#sidebar-sessions .sb-item[data-sid="s2"] .sfb-edit').click()
    page.wait_for_selector("#sfb-ed-model")
    opts = model_options(page)
    check("Claude Code: CLI default, then its models by name, then Other (custom)",
          opts == [["", "CLI 預設"], ["opus", "Opus"], ["sonnet", "Sonnet"], ["haiku", "Haiku"], ["fable", "Fable"],
                   ["__custom__", "其他（自訂）…"]], opts)
    check("the bot's own model is the one chosen, and the note says what is sent",
          page.input_value("#sfb-ed-model") == "opus" and "--model opus" in page.inner_text("#sfb-ed-model-note"))
    page.evaluate("document.getElementById('sfb-ed-model').size = 6")
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / f"botset-model-list{tag}.png"))
    page.evaluate("document.getElementById('sfb-ed-model').size = 1")
    page.select_option("#sfb-ed-cmd", value=CODEX)
    page.wait_for_timeout(300)
    opts = model_options(page)
    check("another CLI starts the choice over, from that CLI's own list (opus is no Codex model)",
          [o[0] for o in opts] == ["", "gpt-5.5", "gpt-5-codex", "gpt-5", "o3", "__custom__"] and page.input_value("#sfb-ed-model") == "", opts)
    page.select_option("#sfb-ed-model", value="gpt-5.5")
    check("a Codex model is sent as -m", "-m gpt-5.5" in page.inner_text("#sfb-ed-model-note"))
    page.select_option("#sfb-ed-cmd", value="grok")
    page.wait_for_timeout(300)
    check("Grok Build: its own three", [o[0] for o in model_options(page)] == ["", "grok-4.7", "grok-4.7-build-fast", "grok-4.6", "__custom__"])
    page.select_option("#sfb-ed-cmd", value="opencode")
    page.wait_for_timeout(300)
    check("OpenCode has no list of its own: the default and Other", [o[0] for o in model_options(page)] == ["", "__custom__"])
    page.select_option("#sfb-ed-cmd", value="pi")
    page.wait_for_timeout(300)
    check("a CLI that picks its model inside it: the field is greyed, on the default, and says why",
          page.locator("#sfb-ed-model").is_disabled() and page.input_value("#sfb-ed-model") == ""
          and "要在它裡面選" in page.inner_text("#sfb-ed-model-note") and [o[0] for o in model_options(page)] == [""])
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / f"botset-model-greyed{tag}.png"))
    page.select_option("#sfb-ed-cmd", value=CLAUDE)
    page.wait_for_timeout(300)
    check("…and back on Claude Code it is on again, starting from the default",
          not page.locator("#sfb-ed-model").is_disabled() and page.input_value("#sfb-ed-model") == "")
    page.select_option("#sfb-ed-model", value="haiku")
    page.evaluate("window.__calls.length = 0")
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_function("window.__calls.some(c => c[0] === 'bots_save')", timeout=5000)
    sent = json.loads(calls(page, "bots_save")[-1][1])
    check("saved with the key the CLI takes, not the name", sent["model"] == "haiku" and sent["cmd"] == CLAUDE, sent)
    page.close()

    print(f"[{engine}] 7. Other (custom): checked on the spot, never saved wrong")
    page, errors = open_page(browser)
    page.locator('#sidebar-sessions .sb-item[data-sid="s2"] .sfb-edit').click()
    page.wait_for_selector("#sfb-ed-model")
    page.select_option("#sfb-ed-model", value="__custom__")
    check("a text box appears, empty, and asks for the key (nothing to save yet)",
          page.locator("#sfb-ed-model-custom").is_visible() and page.input_value("#sfb-ed-model-custom") == ""
          and "請輸入模型的 key" in page.inner_text("#sfb-ed-model-note"))
    page.fill("#sfb-ed-model-custom", "sonet")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').innerText.includes('不認得這個模型')", timeout=5000)
    note = page.inner_text("#sfb-ed-model-note")
    check("a key ShellBot does not know: 不認得這個模型, and it would not take effect", "不認得這個模型" in note and "sonet" in note and "不會生效" in note, note)
    check("…with the likely key to take", "是不是要 sonnet" in note and page.locator("#sfb-ed-model-note a[data-use-model]").inner_text() == "sonnet", note)
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / f"botset-model-unknown{tag}.png"))
    page.evaluate("window.__calls.length = 0")
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_timeout(400)
    check("Save does nothing while it is wrong, and says what to do",
          "bots_save" not in page.evaluate(CALLS) and page.evaluate(EDITOR) and "請先處理模型" in page.inner_text("#sfb-ed-msg"))
    page.fill("#sfb-ed-model-custom", "Sonnet 4.5")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').innerText.includes('一個詞')", timeout=5000)
    check("two words: a model is one word", "模型是一個詞" in page.inner_text("#sfb-ed-model-note"))
    page.fill("#sfb-ed-model-custom", "claude-opus-4-8")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').classList.contains('ok')", timeout=5000)
    check("a full id of a family it knows: recognised, and the flag it is sent with",
          "ShellBot 認得這個模型" in page.inner_text("#sfb-ed-model-note") and "--model claude-opus-4-8" in page.inner_text("#sfb-ed-model-note"))
    page.fill("#sfb-ed-model-custom", "gpt-5")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').classList.contains('bad')", timeout=5000)
    check("another CLI's model is not accepted on this one", "不認得這個模型" in page.inner_text("#sfb-ed-model-note"))
    page.fill("#sfb-ed-model-custom", "sonet")
    page.wait_for_function("document.querySelector('#sfb-ed-model-note a[data-use-model]')", timeout=5000)
    page.click("#sfb-ed-model-note a[data-use-model]")
    page.wait_for_timeout(300)
    check("taking the suggestion picks that model from the list", page.input_value("#sfb-ed-model") == "sonnet"
          and not page.locator("#sfb-ed-model-custom").is_visible() and "--model sonnet" in page.inner_text("#sfb-ed-model-note"))
    page.select_option("#sfb-ed-model", value="__custom__")
    page.fill("#sfb-ed-model-custom", "claude-haiku-4-5-20251001")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').classList.contains('ok')", timeout=5000)
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_function("window.__calls.some(c => c[0] === 'bots_save')", timeout=5000)
    sent = json.loads(calls(page, "bots_save")[-1][1])
    check("a good custom key is saved as typed", sent["model"] == "claude-haiku-4-5-20251001", sent)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 8. a key already stored that ShellBot does not know")
    page, errors = open_page(browser)
    page.locator('.sfb-sleeper[data-sfb-role="舊設定"] .sfb-edit').click()
    page.wait_for_selector("#sfb-ed-model")
    check("it is shown, not hidden: under Other (custom), as stored", page.input_value("#sfb-ed-model") == "__custom__"
          and page.input_value("#sfb-ed-model-custom") == "sonet")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').classList.contains('warn')", timeout=5000)
    note = page.inner_text("#sfb-ed-model-note")
    check("…flagged 這個模型可能沒生效, with why and the likely key", "這個模型可能沒生效" in note and "ShellBot 不認得「sonet」" in note
          and page.locator("#sfb-ed-model-note a[data-use-model]").inner_text() == "sonnet", note)
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / f"botset-model-flagged{tag}.png"))
    page.evaluate("window.__calls.length = 0")
    page.fill("#sfb-ed-resp", "改了執掌，模型沒動")
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_function("window.__calls.some(c => c[0] === 'bots_save')", timeout=5000)
    sent = json.loads(calls(page, "bots_save")[-1][1])
    check("editing only the job is not blocked by it: the key goes back as it was", sent["model"] == "sonet"
          and sent["responsibility"] == "改了執掌，模型沒動", sent)
    page.locator('.sfb-sleeper[data-sfb-role="舊設定"] .sfb-edit').click()
    page.wait_for_selector("#sfb-ed-model")
    page.fill("#sfb-ed-model-custom", "sonet2")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').classList.contains('bad')", timeout=5000)
    page.evaluate("window.__calls.length = 0")
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_timeout(400)
    check("a different wrong key is not let through: the flag turns into a block, nothing is sent",
          "bots_save" not in page.evaluate(CALLS) and "請先處理模型" in page.inner_text("#sfb-ed-msg"))
    page.select_option("#sfb-ed-model", value="")
    page.wait_for_timeout(200)
    page.evaluate("window.__calls.length = 0")
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_function("window.__calls.some(c => c[0] === 'bots_save')", timeout=5000)
    check("choosing CLI default sends an empty model (the server takes the pin off)", json.loads(calls(page, "bots_save")[-1][1])["model"] == "")
    check("the row in Settings and the card agree it is flagged (bots_state's pin)",
          ROSTER["舊設定"]["pin"]["ok"] is False and ROSTER["舊設定"]["pin"]["suggestion"] == "sonnet")
    page.close()

    print(f"[{engine}] 9. the last step of making a bot says where to change it")
    page, errors = open_page(browser)
    page.locator("#sidebar-sessions .sfb-add").click()
    page.wait_for_selector(".sfb-modal #sfb-ed-name")
    note = page.inner_text(".sfb-modal .sfb-where")
    check("the creation editor ends with where it is changed later: the face, the row's Edit, Settings → Bots",
          "點它的頭像" in note and "側欄那一列的「編輯」" in note and "設定 → Bots" in note, note)
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / f"botset-create-note{tag}.png"))
    page.fill("#sfb-ed-name", "新的助手")
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_function("window.__calls.some(c => c[0] === 'bots_save')", timeout=5000)
    page.wait_for_timeout(600)
    toast = page.evaluate("(() => { const els = [...document.querySelectorAll('body > div')].filter(d => d.style.position === 'fixed' && d.style.display === 'block');"
                          " return els.map(e => e.textContent).join('|'); })()")
    check("…and so does the message after it is made", "新的助手" in toast and "設定 → Bots" in toast, toast)
    page.locator('#sidebar-sessions .sb-item[data-sid="s3"] .sfb-keep').click()
    page.wait_for_selector("#sfb-keep")
    check("the Keep box says its job, CLI, model and skills are for later, and where",
          "之後在側欄那一列的「編輯」或 設定 → Bots 補上" in page.inner_text("#sfb-keep"), page.inner_text("#sfb-keep"))
    if engine == "chromium":
        page.locator("#sfb-keep").screenshot(path=str(SHOTS / "botset-keep-note-chromium.png"))
    page.click('#sfb-keep [data-k="ok"]')
    page.wait_for_timeout(500)
    toast = page.evaluate("(() => [...document.querySelectorAll('body > div')].filter(d => d.style.position === 'fixed' && d.style.display === 'block')"
                          ".map(e => e.textContent).join('|'))()")
    check("…and so does the message after keeping it", "編輯" in toast and "設定 → Bots" in toast, toast)
    page.close()

    print(f"[{engine}] 10. waking a bot whose model ShellBot does not know")
    page, errors = open_page(browser)
    page.evaluate("window.__open = {success: true, details: {sid: 's9', model_warning: '不認得這個模型'}}")
    page.locator('.sfb-sleeper[data-sfb-role="舊設定"] .sfb-name').click()
    page.wait_for_timeout(500)
    toast = page.evaluate("(() => [...document.querySelectorAll('body > div')].filter(d => d.style.position === 'fixed' && d.style.display === 'block')"
                          ".map(e => e.textContent).join('|'))()")
    check("it wakes, and the page says the model may not take effect and where to fix it",
          "可能沒生效" in toast and "編輯" in toast, toast)
    page.close()

    print(f"[{engine}] 11. English")
    page, errors = open_page(browser, lang="en")
    check("the card's Edit and the rows' Edit read in English",
          "Edit this bot" in (page.locator("#sfb-primary .sfb-card-edit").get_attribute("title") or "")
          and page.locator('#sidebar-sessions .sb-item[data-sid="s2"] .sfb-edit').inner_text() == "Edit"
          and page.locator('.sfb-sleeper[data-sfb-role="研究"] .sfb-edit').inner_text() == "Edit")
    page.locator('.sfb-sleeper[data-sfb-role="舊設定"] .sfb-edit').click()
    page.wait_for_selector("#sfb-ed-model")
    page.wait_for_function("document.getElementById('sfb-ed-model-note').classList.contains('warn')", timeout=5000)
    check("the flag and the model words in English", "This model may not take effect" in page.inner_text("#sfb-ed-model-note")
          and 'ShellBot does not know "sonet"' in page.inner_text("#sfb-ed-model-note") and "Did you mean sonnet?" in page.inner_text("#sfb-ed-model-note"),
          page.inner_text("#sfb-ed-model-note"))
    check("…and the list's first and last", [o[1] for o in model_options(page)][0] == "CLI default"
          and [o[1] for o in model_options(page)][-1] == "Other (custom)…", model_options(page))
    if engine == "chromium":
        page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / "botset-model-flagged-en-chromium.png"))
    close_editor(page)
    page.click("#btn-settings")
    page.click('#settings-tabs-bar [data-tab="bots"]')
    page.wait_for_timeout(300)
    check("Settings → Bots in English", "Your bots · 4" in page.inner_text("#sfb-settings-list") and "Primary bot" in page.inner_text("#sfb-settings-list")
          and "This model may not take effect" in page.inner_text("#sfb-settings-list"), page.inner_text("#sfb-settings-list"))
    page.close()


with sync_playwright() as pw:
    SHOTS.mkdir(exist_ok=True)
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
