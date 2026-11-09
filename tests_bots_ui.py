#!/usr/bin/env python3
"""Bots in the real page: the whole web/index.html in a browser, with a stand-in
for the Python side, so every hook index.html calls is exercised for real.

  - the flag on: every tab's light is a face, the second line shows the agent's
    latest line, the primary card sits on top, sleeping bots are listed
  - each state shows its own expression (idle / working / question / stuck / done)
  - a sidebar rebuild does not restart the animations (wall-clock phase)
  - click on a face still cycles the manual light; right-click opens the picker
  - closing a bot tab asks to write memory first; a plain tab gets the old confirm
  - the external primary opens its panel; a sleeping bot is woken on click
  - the settings switch turns everything off, and off means no faces at all

Screenshots go to qa-shots/bots-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_bots_ui.py
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
    print("SKIP  tests_bots_ui.py（沒裝 playwright）")
    sys.exit(0)

TABS = [  # sid, label, cmd, state, line
    ("s101", "Coding", "claude", "working", {"action": "Editing relay.py", "narration": "先把 timeout 拉到 30 秒"}),
    ("s102", "研究", "claude", "decision", {"narration": "兩個方案：A 改快取、B 改索引，要選哪個？"}),
    ("s103", "relay 除錯", "claude", "working", {"action": "Running npm test"}),
    ("s104", "週報整理", "claude", "done", {"narration": "週報整理好了，放在 reports/weekly.md"}),
    ("s105", "部署檢查", "claude", "stuck", {"narration": "等對方開防火牆才能繼續"}),
    ("s106", "文件翻譯", "claude", "idle", {"narration": "第 3 章翻完，等下一段"}),
    ("s107", "Codex 實驗", "codex", "idle", {"narration": "準備好了"}),
    ("s108", "Grok 筆記", "grok", "done", {"narration": "筆記存好了"}),
    ("s109", "opencode 試跑", "opencode", "working", {"action": "Searching src/"}),
    ("s110", "pi 測試", "pi", "idle", {}),
    ("s111", "agy 草稿", "agy", "decision", {"narration": "要覆蓋舊檔嗎？"}),
    ("s112", "cursor 任務", "cursor-agent", "done", {"narration": "改好 3 個檔案"}),
]
DOT = {"working": "sig-working", "decision": "sig-decision", "done": "sig-done", "stuck": "sig-stuck", "idle": ""}
ROSTER = {
    "總管": {"label": "幕僚｜總管", "responsibility": "派工與驗收，不接實作", "cmd": "zsh", "coordinator": True,
             "bot": {"avatar": {"shape": "star", "color": "purple"}, "primary": True,
                     "external": {"kind": "webhook", "url_set": True, "auth_set": True,
                                  "url_mask": "https://hooks.example/***"}}},
    "Coding": {"label": "Coding", "responsibility": "程式、測試、部署", "cmd": "claude",
               "bot": {"avatar": {"shape": "pill", "color": "red"}, "memory": True, "skills": ["demo-skill"]}},
    "研究": {"label": "研究", "responsibility": "資料調研與彙整", "cmd": "claude",
             "bot": {"avatar": {"shape": "hex", "color": "blue"}, "memory": True}},
    "日程": {"label": "日程", "responsibility": "行程、信件、提醒", "cmd": "claude",
             "bot": {"avatar": {"shape": "cloud", "color": "green"}, "memory": True}},
    "知識庫": {"label": "知識庫", "responsibility": "筆記與知識整理", "cmd": "codex",
               "bot": {"avatar": {"shape": "ghost", "color": "gray"}, "memory": True}},
    "陪練": {"label": "陪練", "responsibility": "練習與回饋", "cmd": "grok",
             "bot": {"avatar": {"shape": "drop", "color": "amber"}, "memory": True}},
    "Worker": {"label": "Worker-CLD", "responsibility": "一般派工", "cmd": "claude"},
}
STATE = {"enabled": True, "roster": ROSTER, "primary": "總管", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s101": {"bot": "Coding"}, "s102": {"bot": "研究"}}}
CFG = {"settings": {"experimental_bots": True, "language": "zh-TW", "sidebar_open": True,
                    "experimental_a2a": True},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}, {"name": "Codex", "cmd": "codex", "icon": "X"},
                   {"name": "Grok Build", "cmd": "grok", "icon": "G"}],
       "agent_roster": {}}
SESSIONS = [{"sid": sid, "cmd": cmd, "alive": True, "label": label, "bridge_enabled": True}
            for sid, label, cmd, *_ in TABS]
STATUS = {sid: dict({"state": st, "dot": DOT[st], "summary": st}, **extra) for sid, _, _, st, extra in TABS}

STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]);
                            window.__bots.enabled = window.__cfg.settings.experimental_bots === true;
                            return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's101'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      case 'bots_skill_catalog': return R({skills: [{name: 'demo-skill', description: 'demo'},
                                                    {name: 'shellframe', description: 'ops'}]});
      case 'bots_external_info': return R({success: true, routine: 'routine text', log: [
        {kind: 'message', from_label: 'Coding', target: '總管', text: 'relay 修好了'},
        {kind: 'result', from_label: '幕僚｜總管', target: 'a1b2', text: '收到，下一步做 Y'}]});
      case 'bots_open': return R({success: true, details: {sid: 's199'}});
      case 'bots_close': return R({success: true, message: 'ok'});
      case 'bots_save': case 'bots_set_avatar': case 'bots_external_send':
        return R({success: true, message: 'ok'});
      default: return R('{}');
    }
  };
}})};
setInterval(() => { try { window.__sfAgentStatus && window.__sfAgentStatus(window.__status); } catch (e) {} }, 500);
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False),
       json.dumps(STATE, ensure_ascii=False), json.dumps(STATUS, ensure_ascii=False))

SHEET = """() => {
  const S = window.SFBots, shapes = Object.keys(S.SHAPES);
  const cols = [['', '發呆'], ['sig-working', '運作'], ['sig-decision', '問號'], ['sig-stuck', '卡住'],
                ['sig-done', '完成'], ['asleep', '休息'], ['writing', '寫記憶']];
  const colors = ['red','orange','amber','green','teal','sky','blue','indigo','purple','pink','brown','ink'];
  let h = '<div id="qa-sheet" style="position:fixed;inset:0;z-index:9999;background:#16161e;padding:18px;' +
          'font:12px -apple-system,sans-serif;color:#a9b1d6;overflow:auto"><table style="border-spacing:10px 8px">' +
          '<tr><td></td>' + cols.map(c => `<td style="text-align:center">${c[1]}</td>`).join('') + '</tr>';
  shapes.forEach((k, i) => {
    const av = {shape: k, color: colors[i % colors.length]};
    h += `<tr><td style="color:#565f89">${S.SHAPES[k].name}</td>` + cols.map(([c]) => {
      const cls = (c === 'asleep' || c === 'writing') ? '' : c;
      const opt = {seed: k + c, asleep: c === 'asleep', writing: c === 'writing', primary: i === 9};
      return `<td><span class="busy-dot ${cls}" style="--sfb-size:44px;display:inline-block;width:44px;height:44px">` +
             S.faceSvg(av, opt) + '</span></td>';
    }).join('') + '</tr>';
  });
  document.body.insertAdjacentHTML('beforeend', h + '</table></div>');
}"""

SHOTS.mkdir(exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1180, "height": 1480}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    try:
        page.wait_for_function("document.querySelectorAll('#sidebar-sessions .busy-dot svg.sfb').length >= 12",
                               timeout=30000)
    except Exception as e:
        print("page did not render faces:", e, errors[:5])
    page.wait_for_timeout(1500)

    print("faces")
    check("the flag puts the page in bot mode", page.evaluate("document.body.classList.contains('sfb-on')"))
    check("every sidebar light is a face",
          page.evaluate("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .busy-dot svg.sfb').length") == 12)
    check("every tab-bar light is a face",
          page.evaluate("document.querySelectorAll('#tabs .tab[data-sid] .busy-dot svg.sfb').length") == 12)
    check("a bot's tab wears the bot's face", page.evaluate(
        "document.querySelector('#sidebar-sessions [data-sid=\"s101\"] svg.sfb').dataset.shape") == "pill")
    style = """([sid, sel, prop]) => { const el = document.querySelector(`#sidebar-sessions [data-sid="${sid}"] svg.sfb ${sel}`);
                return el ? getComputedStyle(el)[prop] : null; }"""
    check("working: it bobs", page.evaluate(style, ["s101", ".rig", "animationName"]) == "sfb-bob")
    check("question: the ? is shown", page.evaluate(style, ["s102", ".q", "opacity"]) == "1")
    check("stuck: it sweats", page.evaluate(style, ["s105", ".sweat", "opacity"]) == "1")
    check("done: it smiles", page.evaluate(style, ["s104", ".happy", "opacity"]) == "1"
          and page.evaluate(style, ["s104", ".eye", "opacity"]) == "0")
    check("idle: it breathes and blinks", page.evaluate(style, ["s106", ".rig", "animationName"]) == "sfb-breathe"
          and page.evaluate(style, ["s106", ".eye", "animationName"]) == "sfb-blink")
    check("the old coloured light is gone", page.evaluate(style, ["s101", "", "boxShadow"]) in (None, "none")
          and page.evaluate("getComputedStyle(document.querySelector('#sidebar-sessions [data-sid=\"s101\"] .busy-dot')).boxShadow") == "none")

    print("rows")
    sub = lambda sid: page.evaluate(f"(document.querySelector('#sidebar-sessions [data-sid=\"{sid}\"] .sfb-sub')||{{}}).textContent")
    check("second line: what it is doing", "Editing relay.py" in (sub("s101") or ""), sub("s101"))
    check("second line: what it asked", "要選哪個" in (sub("s102") or ""), sub("s102"))
    check("a bot row is tagged Bot", page.evaluate(
        "!!document.querySelector('#sidebar-sessions [data-sid=\"s101\"] .sfb-tag')"))
    check("a plain row is not", not page.evaluate(
        "!!document.querySelector('#sidebar-sessions [data-sid=\"s106\"] .sfb-tag')"))
    sleepers = page.evaluate("[...document.querySelectorAll('.sfb-sleeper')].map(r => r.dataset.sfbRole)")
    check("sleeping bots listed, not the awake ones or the primary", sorted(sleepers) == sorted(["日程", "知識庫", "陪練"]),
          sleepers)
    check("primary card shows the primary bot", page.evaluate(
        "(document.querySelector('#sfb-primary .sfb-card-name')||{}).textContent") == "幕僚｜總管")
    check("…with the star badge", page.evaluate(
        "!!document.querySelector('#sfb-primary svg.sfb.is-primary')"))

    print("continuity")
    phase = ("() => { const at = Date.now(); window.__sfHost.renderSidebar(); return [at, parseFloat("
             "document.querySelector('#sidebar-sessions [data-sid=\"s106\"] svg.sfb').style.getPropertyValue('--t'))]; }")
    t0 = page.evaluate(phase)
    page.wait_for_timeout(1200)
    t1 = page.evaluate(phase)
    drift = (t0[1] - t1[1]) - (t1[0] - t0[0])     # the phase moved exactly as far as the clock
    check("a rebuilt row keeps its animation phase", abs(drift) < 60, f"drift={drift}ms")

    sb = page.locator("#sidebar")
    sb.screenshot(path=str(SHOTS / "bots-sidebar.png"))
    page.locator("#tab-bar").screenshot(path=str(SHOTS / "bots-tabbar.png"))

    print("interactions")
    page.click("#sidebar-sessions [data-sid='s106'] .busy-dot")
    page.wait_for_timeout(500)
    check("click still cycles the light by hand", "sig-done" in page.evaluate(
        "document.querySelector('#sidebar-sessions [data-sid=\"s106\"] .busy-dot').className"))
    page.click("#sidebar-sessions [data-sid='s106'] .busy-dot", button="right")
    page.wait_for_timeout(300)
    check("right-click opens the avatar picker", page.evaluate("!!document.querySelector('.sfb-pop')"))
    check("…12 shapes, 14 colours, 5 expressions", page.evaluate(
        "[document.querySelectorAll('.sfb-pop button[data-shape]').length, document.querySelectorAll('.sfb-pop button[data-color]').length,"
        " document.querySelectorAll('.sfb-pop .sfb-preview figure').length]") == [12, 14, 5])
    page.locator(".sfb-pop").screenshot(path=str(SHOTS / "bots-picker.png"))
    page.click(".sfb-pop [data-shape='cloud']")
    page.wait_for_timeout(300)
    call = [c for c in page.evaluate("window.__calls") if c[0] == "bots_set_avatar"]
    check("picking a shape saves it", call and call[-1][1][0] == "s106" and json.loads(call[-1][1][1])["shape"] == "cloud",
          call[-1:] if call else None)
    page.keyboard.press("Escape")

    page.click(".sfb-sleeper[data-sfb-role='日程']")
    page.wait_for_timeout(300)
    check("clicking a sleeping bot wakes it", any(c[0] == "bots_open" and c[1][0] == "日程"
                                                  for c in page.evaluate("window.__calls")))

    page.click("[data-sfb='new']")
    page.wait_for_selector("#sfb-ed-name", timeout=5000)
    page.fill("#sfb-ed-name", "寫手")
    page.fill("#sfb-ed-resp", "寫文件與簡報草稿")
    page.click("#sfb-ed-shapes [data-shape='egg']")
    page.click("#sfb-ed-colors [data-color='teal']")
    page.wait_for_timeout(200)
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / "bots-editor.png"))
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_timeout(400)
    call = [c for c in page.evaluate("window.__calls") if c[0] == "bots_save"]
    data = json.loads(call[-1][1][1]) if call else {}
    check("the editor saves name, 執掌 and face", call and call[-1][1][0] == "寫手"
          and data.get("responsibility") == "寫文件與簡報草稿" and data.get("avatar") == {"shape": "egg", "color": "teal"}
          and data.get("memory") is True, data)

    page.hover("#sidebar-sessions [data-sid='s101']")
    page.click("#sidebar-sessions [data-sid='s101'] [data-action='close']")
    page.wait_for_timeout(300)
    check("closing a bot tab asks to write memory first", page.evaluate(
        "!!document.querySelector('.sfb-modal [data-act=\"save\"]')") and not page.evaluate(
        "!!document.getElementById('close-tab-confirm')"))
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / "bots-close.png"))
    page.click(".sfb-modal [data-act='save']")
    page.wait_for_timeout(300)
    check("…and hands it to the backend flow", any(c[0] == "bots_close" and c[1] == ["s101", "save"]
                                                   for c in page.evaluate("window.__calls")))
    page.hover("#sidebar-sessions [data-sid='s106']")
    page.click("#sidebar-sessions [data-sid='s106'] [data-action='close']")
    page.wait_for_timeout(300)
    check("a plain tab keeps the normal confirm", page.evaluate("!!document.getElementById('close-tab-confirm')"))
    page.keyboard.press("Escape")

    page.click("#sfb-primary .sfb-card")
    page.wait_for_selector("#sfb-x-text", timeout=5000)
    page.fill("#sfb-x-text", "今天的部署順利嗎？")
    page.locator(".sfb-modal .modal").screenshot(path=str(SHOTS / "bots-external.png"))
    page.click(".sfb-modal [data-act='send']")
    page.wait_for_timeout(300)
    check("the external primary takes a message from its panel", any(
        c[0] == "bots_external_send" and c[1] == ["總管", "今天的部署順利嗎？"] for c in page.evaluate("window.__calls")))
    page.click(".sfb-modal [data-act='close']")

    page.evaluate(SHEET)
    page.wait_for_timeout(400)
    page.locator("#qa-sheet table").screenshot(path=str(SHOTS / "bots-expressions.png"))
    page.evaluate("document.getElementById('qa-sheet').remove()")

    print("switch off")
    page.click("#btn-settings")
    page.wait_for_timeout(400)
    check("the switch is in settings and on", page.evaluate(
        "!!document.getElementById('setting-experimental-bots') && document.getElementById('setting-experimental-bots').classList.contains('on')"))
    page.click("#setting-experimental-bots")
    page.wait_for_timeout(600)
    check("turning it off is saved", page.evaluate("window.__cfg.settings.experimental_bots") is False)
    check("…and every face is gone", page.evaluate("document.querySelectorAll('svg.sfb').length") == 0
          and not page.evaluate("document.body.classList.contains('sfb-on')"))
    check("…and the primary card too", page.evaluate(
        "getComputedStyle(document.getElementById('sfb-primary')).display") == "none")
    check("no page errors", not errors, errors[:3])
    browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
