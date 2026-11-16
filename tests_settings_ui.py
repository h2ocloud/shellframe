#!/usr/bin/env python3
"""Settings after the clean-up: web/index.html in a browser with a stand-in Python side.

  - Shift+Enter always puts a new line in, also with an old config that had it switched off
    (settings.shiftEnterNewline: false): `\\n` to Claude Code, `CSI 27;2;13~` to Codex. Settings
    has no switch for it, and the tip at the foot of General says it is always so
  - Settings → Bots: the Bots switch is there, its label no longer says "experimental"
  - the tabs, in order: General, Bots, Telegram, LINE, Connect, Advanced; no Plugins
  - Advanced holds what moved: auto /compact and its threshold, the model badge, the local HTTP
    API (Swagger, copy token), the status hooks, idle tab cleanup, Telegram's prefix, wrapper tag
    and prompt, LINE's prefix and wrapper prompt. Each is on the page once, none is left in
    General, Telegram or LINE, and each still saves to the key it had (the two prefixes are read
    when Connect is pressed, as before). LINE's webhook fields stay in LINE, folded until LINE is
    connected or someone opens them (10 in Advanced + 1 folded in LINE = the 11 that moved)
  - gone from the page: the System Prompt (Telegram tab and the TG button's window; Connect sends
    ''), the Plugins tab, the glasses switch and every tab's glasses button (also with an old
    config that had them on), the master reminder, auto-delegate, the handoff switch, the init
    prompt switch and the Session prompt
  - a screenshot of each settings tab: qa-shots/settings-cleanup-<tab>.png
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed; the
screenshots are WebKit's when it runs. Demo data only. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_settings_ui.py
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


CLAUDE, CODEX = "claude --dangerously-skip-permissions", "codex"
PRESETS = [{"name": "Bash", "cmd": "bash", "icon": "▶"}, {"name": "Claude", "cmd": CLAUDE, "icon": "C"},
           {"name": "Codex", "cmd": CODEX, "icon": "X"}]
SESSIONS = [{"sid": "s1", "cmd": CLAUDE, "alive": True, "label": "寫程式", "bridge_enabled": True},
            {"sid": "s2", "cmd": CODEX, "alive": True, "label": "研究", "bridge_enabled": True}]
ROSTER = {"助理": {"label": "助理", "responsibility": "日常任務的第一站", "cmd": CLAUDE, "model": "", "coordinator": False,
                   "former_names": [], "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True, "skills": []},
                   "pin": None, "cli": {"key": "claude", "label": "Claude Code"}}}
BOTS = {"enabled": True, "roster": ROSTER, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
        "sessions": {}, "kinds": {"s1": "temp", "s2": "temp"}}
# An install that had every switch of the old settings page turned on still carries these.
OLD_SETTINGS = {"shiftEnterNewline": False, "glasses_enabled": True, "inject_init_prompt": True,
                "master_turn_preamble_enabled": True, "auto_delegate_enabled": True}
T = lambda en, zh: {"en": en, "zh": zh}
CONNECT = {"status": {"service": "demo", "name": T("Demo notes", "示範備忘"), "icon": "mail", "kind": "", "state": "not_started",
                      "route": "", "step": None, "clis": {}, "scopes": [], "connected_at": 0, "checked_at": 0,
                      "problem": None, "attention": False},
           "desc": {"schema": 1, "id": "demo", "name": T("Demo notes", "示範備忘"), "icon": "mail",
                    "summary": T("Lets your bots read and send demo notes.", "讓 bot 讀取、寄出示範備忘。"),
                    "scopes": [], "routes": [], "faq": [], "problems": {}}}
STUB = """
window.__calls = [];
window.__cfg = %(cfg)s; window.__sessions = %(sessions)s; window.__bots = %(bots)s; window.__connect = %(connect)s;
window.__lineStatus = {exists: false};
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
const API_INFO = on => ({enabled: on, running: on, host: '127.0.0.1', port: 8765, token: on ? 'demo-token' : '',
                         docs_url: 'http://127.0.0.1:8765/docs'});
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's1'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'list_plugin_panels': return R([]);
      case 'bots_state': return R(window.__bots);
      case 'get_saved_bridge': case 'get_saved_line_bridge': return R('null');
      case 'get_bridge_status': return R({exists: false});
      case 'get_line_bridge_status': return R(window.__lineStatus);
      case 'stt_status': return R({backend: 'auto', local: {ready: false}, plugin: {ready: false, path: '~/.config/shellframe/stt_plugin.py'},
                                   remote: {ready: false, endpoints: []}});
      case 'stt_get_providers': return R([]);
      case 'get_api_server_info': return R(API_INFO(false));
      case 'set_api_server_enabled': return R(API_INFO(!!args[0]));
      case 'get_status_hooks_info': return R({installed: false, installed_count: 0, total: 2});
      case 'set_status_hooks_enabled': return R({installed: !!args[0], installed_count: args[0] ? 2 : 0, total: 2});
      case 'save_idle_reaper': window.__cfg.idle_reaper = Object.assign({}, window.__cfg.idle_reaper, JSON.parse(args[0])); return R(window.__cfg);
      case 'start_bridge': return R({success: true, bot: 'demo_bot', active: true, sessions: 2});
      case 'start_line_bridge': return R({success: true, active: true, delivery_mode: 'poll', webhook_url: 'http://127.0.0.1:8787/line/webhook'});
      case 'connect_list': return R({success: true, services: [window.__connect.status], clis: []});
      case 'connect_describe': return R({success: true, descriptor: window.__connect.desc});
      case 'connect_status': return R({success: true, status: window.__connect.status});
      case 'marketplace_list': return R({plugins: [{name: 'demo-plugin', description: 'A made-up plugin (stand-in data)', version: '1.0.0',
                                                    installed: false, enabled: false, bundled: false}]});   // the page before the clean-up
      default: return R('{}');
    }
  };
}})};
"""


def build_stub(lang="zh-TW", settings=None):
    cfg = {"settings": dict({"language": lang, "sidebar_open": True, "ui_style": "cli", "experimental_bots": True},
                            **(settings or {})),
           "presets": PRESETS, "agent_roster": {},
           "idle_reaper": {"enabled": False, "idle_sec": 1800, "summary_grace_sec": 120}}
    sessions = [dict(x, glasses_enabled=True) for x in SESSIONS] if (settings or {}).get("glasses_enabled") else SESSIONS
    return STUB % {k: json.dumps(v, ensure_ascii=False) for k, v in
                   (("cfg", cfg), ("sessions", sessions), ("bots", BOTS), ("connect", CONNECT))}


def open_page(browser, lang="zh-TW", settings=None, web=HERE / "web"):
    page = browser.new_page(viewport={"width": 1180, "height": 1700}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(build_stub(lang, settings))
    page.goto((web / "index.html").as_uri())
    page.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid]').length >= 2", timeout=20000)
    page.wait_for_timeout(800)
    return page, errors


def open_settings(page, tab="general"):
    if not page.evaluate("document.getElementById('modal-settings').classList.contains('open')"):
        page.click("#btn-settings")
        page.wait_for_selector("#modal-settings.open")
    page.click(f'#settings-tabs-bar [data-tab="{tab}"]')
    page.wait_for_timeout(300)


def shoot_tabs(page, out_dir, prefix, also=()):
    """One screenshot per settings tab, the whole panel (the modal's own scroll lifted for it)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": 1180, "height": 3400})        # the longest panel fits, top-aligned
    page.add_style_tag(content="#modal-settings .modal { max-height: none !important; overflow: visible !important; }"
                               " #modal-settings { align-items: flex-start !important; padding-top: 24px; }")
    page.add_style_tag(content=".sfb-edit, .sfb-pv { visibility: hidden !important; }")   # what the pointer last touched
    tabs = page.evaluate("[...document.querySelectorAll('#settings-tabs-bar .settings-tab')].map(t => t.dataset.tab)")
    for tab in tabs:
        open_settings(page, tab)
        page.wait_for_timeout(500)
        page.locator("#modal-settings .modal").screenshot(path=str(out_dir / f"{prefix}-{tab}.png"))
    for name, setup in also:
        setup(page)
        page.wait_for_timeout(400)
        page.locator("#modal-settings .modal").screenshot(path=str(out_dir / f"{prefix}-{name}.png"))
    return tabs


def calls(page, name):
    return [c[1] for c in page.evaluate("window.__calls") if c[0] == name]


def last_settings(page):
    saved = calls(page, "save_settings")
    return json.loads(saved[-1][0]) if saved else {}


ADVANCED = ["setting-auto-compact", "setting-auto-compact-threshold", "setting-model-badge",
            "setting-api-server", "btn-api-docs", "btn-api-copy-token", "setting-status-hooks",
            "setting-idle-enabled", "setting-idle-value", "setting-idle-unit", "setting-idle-grace", "btn-save-idle-reaper",
            "settings-bridge-prefix", "setting-show-tg-wrapper", "setting-tg-prompt",
            "settings-line-prefix", "setting-line-prompt"]
LINE_FOLDED = ["settings-line-mode", "settings-line-port", "settings-line-path", "settings-line-poll-path",
               "settings-line-public-url", "settings-line-forward-secret", "settings-line-help", "btn-settings-line-copy-url"]
GONE = ["setting-shift-enter", "setting-glasses", "setting-master-preamble", "setting-master-turn-preamble",
        "setting-auto-delegate", "setting-idle-handoff", "setting-inject-init", "setting-ui-prompt",
        "settings-bridge-prompt", "bridge-prompt", "settings-panel-marketplace", "settings-marketplace-list"]


def where(page, el_id):
    """How many elements have the id, and the settings panel the first one is in."""
    return page.evaluate("""id => { const all = document.querySelectorAll('[id="' + id + '"]');
      const p = all[0] && all[0].closest('.settings-tab-panel'); return [all.length, p ? p.id : null]; }""", el_id)


def writes(page, sid):
    return [c[1][1] for c in page.evaluate("window.__calls") if c[0] == "write_input" and c[1][0] == sid]


def run(browser, engine):
    print(f"[{engine}] 1. Shift+Enter puts a new line in, whatever an old config says")
    page, errors = open_page(browser, settings=OLD_SETTINGS)
    for sid, want, what in (("s1", "\n", "Claude Code gets \\n"), ("s2", "\x1b[27;2;13~", "Codex gets CSI 27;2;13~")):
        page.locator(f"#sidebar-sessions [data-sid='{sid}']").first.click()
        page.wait_for_timeout(400)
        page.locator(".xterm-screen:visible").first.click()
        before = len(writes(page, sid))
        page.keyboard.press("Shift+Enter")
        page.wait_for_timeout(300)
        got = writes(page, sid)[before:]
        check(f"{what}, nothing else (an Enter would send the message)", got == [want], got)

    print(f"[{engine}] 2. Settings has no switch for it; the tip says it is always so")
    open_settings(page, "general")
    check("no Shift+Enter switch on the page", page.locator("#setting-shift-enter").count() == 0)
    tip = page.locator('#settings-panel-general [data-i18n="shiftEnterHint"]')
    check("the tip in General: Shift+Enter always makes a new line, not a send",
          tip.count() == 1 and "Shift+Enter" in tip.inner_text() and "一律換行" in tip.inner_text(), tip.inner_text() if tip.count() else "")

    print(f"[{engine}] 3. Settings → Bots: the switch, no longer called experimental")
    open_settings(page, "bots")
    label = page.evaluate("document.getElementById('setting-experimental-bots').closest('#settings-panel-bots > div').querySelector('label').textContent")
    check("the Bots switch is in Settings → Bots, on", page.locator("#settings-panel-bots #setting-experimental-bots.on").count() == 1)
    check("its label has no 實驗 / experimental", "實驗" not in label and "xperimental" not in label and label.startswith("Bots"), label)
    check("no page errors", not errors, errors)
    page.close()

    page, errors = open_page(browser, lang="en", settings=OLD_SETTINGS)
    open_settings(page, "bots")
    label = page.evaluate("document.getElementById('setting-experimental-bots').closest('#settings-panel-bots > div').querySelector('label').textContent")
    check("in English too: 'Bots: faces, memory, primary bot'", label.strip() == "Bots: faces, memory, primary bot", label)
    open_settings(page, "general")
    tip = page.locator('#settings-panel-general [data-i18n="shiftEnterHint"]').inner_text()
    check("…and the tip: always a new line without sending", "always inserts a new line without sending" in tip, tip)
    check("the Advanced tab is called Advanced", page.locator('#settings-tabs-bar [data-tab="advanced"]').inner_text() == "Advanced")
    page.close()

    print(f"[{engine}] 4. the tabs, and what is no longer on the page")
    page, errors = open_page(browser, settings=OLD_SETTINGS)
    open_settings(page, "general")
    tabs = page.evaluate("[...document.querySelectorAll('#settings-tabs-bar .settings-tab')].map(t => t.dataset.tab)")
    check("General, Bots, Telegram, LINE, Connect, Advanced, in that order; no Plugins",
          tabs == ["general", "bots", "bridge", "line", "connect", "advanced"], tabs)
    check("進階 in Chinese", page.locator('#settings-tabs-bar [data-tab="advanced"]').inner_text() == "進階")
    left = [i for i in GONE if page.locator(f'[id="{i}"]').count()]
    check("none of the retired controls is on the page", not left, left)
    check("Telegram tab: token, users, Connect; the only text box left is STT's providers",
          page.evaluate("[...document.querySelectorAll('#settings-panel-bridge textarea')].map(t => t.id)") == ["stt-providers"]
          and page.locator('#settings-panel-bridge [data-i18n="bridgeSystemPrompt"]').count() == 0)
    check("the TG button's connect window has no System Prompt either", page.locator("#modal-bridge textarea").count() == 0)
    check("no glasses button on any tab's row, though the old config had the glasses on",
          page.locator('#sidebar-sessions [data-action="toggle-glasses"], #sidebar-sessions .sb-glasses').count() == 0
          and "\U0001F453" not in page.inner_text("#sidebar-sessions"))

    print(f"[{engine}] 5. Advanced holds what moved; LINE keeps its webhook fields, folded")
    for el_id in ADVANCED:
        n, panel = where(page, el_id)
        check(f"#{el_id}: once on the page, in Advanced", (n, panel) == (1, "settings-panel-advanced"), (n, panel))
    for el_id in LINE_FOLDED:
        n, panel = where(page, el_id)
        inside = page.evaluate("id => !!document.getElementById(id).closest('#settings-line-details')", el_id)
        check(f"#{el_id}: once, in LINE, inside the folded details", (n, panel, inside) == (1, "settings-panel-line", True), (n, panel, inside))
    open_settings(page, "line")
    check("not connected: the details are folded, the access token and Connect are not",
          page.evaluate("document.getElementById('settings-line-details').open") is False
          and not page.locator("#settings-line-port").is_visible() and page.locator("#settings-line-token").is_visible()
          and page.locator("#btn-settings-line-connect").is_visible())
    page.click("#settings-line-details > summary")
    page.wait_for_timeout(200)
    check("opening them shows the port, the paths and Copy local URL",
          page.locator("#settings-line-port").is_visible() and page.locator("#btn-settings-line-copy-url").is_visible())

    print(f"[{engine}] 6. each one in Advanced saves where it always did")
    open_settings(page, "advanced")
    page.evaluate("window.__calls.length = 0")
    page.click("#setting-auto-compact")
    page.wait_for_timeout(200)
    check("auto /compact off → settings.claude_auto_compact = false", last_settings(page).get("claude_auto_compact") is False, last_settings(page))
    page.fill("#setting-auto-compact-threshold", "8")
    page.dispatch_event("#setting-auto-compact-threshold", "change")
    page.wait_for_timeout(200)
    check("its threshold → settings.claude_auto_compact_threshold = 8", last_settings(page).get("claude_auto_compact_threshold") == 8, last_settings(page))
    page.click("#setting-model-badge")
    page.wait_for_timeout(200)
    check("model badge off → settings.show_model_badge = false", last_settings(page).get("show_model_badge") is False, last_settings(page))
    page.click("#setting-show-tg-wrapper")
    page.wait_for_timeout(200)
    check("TG wrapper tag off → settings.show_tg_wrapper = false", last_settings(page).get("show_tg_wrapper") is False, last_settings(page))
    page.fill("#setting-tg-prompt", "Reply briefly.")
    page.locator("#setting-tg-prompt").blur()
    page.wait_for_timeout(200)
    check("custom TG prompt → settings.tg_prompt", last_settings(page).get("tg_prompt") == "Reply briefly.", last_settings(page))
    page.fill("#setting-line-prompt", "LINE style, please.")
    page.locator("#setting-line-prompt").blur()
    page.wait_for_timeout(200)
    check("LINE wrapper prompt → settings.line_prompt", last_settings(page).get("line_prompt") == "LINE style, please.", last_settings(page))
    page.click("#setting-api-server")
    page.wait_for_timeout(300)
    check("local HTTP API on → set_api_server_enabled(true); Swagger and Copy token show",
          calls(page, "set_api_server_enabled") == [[True]] and page.locator("#btn-api-docs").is_visible()
          and page.locator("#btn-api-copy-token").is_visible(), calls(page, "set_api_server_enabled"))
    page.click("#setting-status-hooks")
    page.wait_for_timeout(300)
    check("status hooks on → set_status_hooks_enabled(true)", calls(page, "set_status_hooks_enabled") == [[True]], calls(page, "set_status_hooks_enabled"))
    page.click("#setting-idle-enabled")
    page.fill("#setting-idle-value", "3")
    page.select_option("#setting-idle-unit", "hours")
    page.fill("#setting-idle-grace", "5")
    page.click("#btn-save-idle-reaper")
    page.wait_for_timeout(300)
    sent = json.loads(calls(page, "save_idle_reaper")[-1][0]) if calls(page, "save_idle_reaper") else {}
    check("idle tab cleanup → save_idle_reaper {enabled, idle_sec, summary_grace_sec}, no handoff",
          sent == {"enabled": True, "idle_sec": 10800, "summary_grace_sec": 300}, sent)
    page.click("#settings-bridge-prefix")
    open_settings(page, "bridge")
    page.fill("#settings-bridge-token", "000000:demo-token-not-real")
    page.click("#btn-settings-bridge-connect")
    page.wait_for_timeout(400)
    sb = calls(page, "start_bridge")
    check("TG prefix off in Advanced, then Connect: start_bridge(…, prefix=false, '') — nothing typed into a tab",
          len(sb) == 1 and sb[0][2] is False and sb[0][3] == "", sb)
    open_settings(page, "advanced")
    page.click("#settings-line-prefix")
    open_settings(page, "line")
    page.click("#btn-settings-line-connect")
    page.wait_for_timeout(400)
    sl = calls(page, "start_line_bridge")
    check("LINE prefix off in Advanced, then Connect LINE: start_line_bridge(…, prefix=false, …)", len(sl) == 1 and sl[0][3] is False, sl)
    page.keyboard.press("Escape")                                   # the "LINE started" note
    page.wait_for_timeout(300)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 7. LINE connected: its details are shown")
    page, errors = open_page(browser)
    page.evaluate("window.__lineStatus = {exists: true, active: true, delivery_mode: 'poll'}")
    open_settings(page, "line")
    page.wait_for_timeout(400)
    check("connected → the folded fields are open", page.evaluate("document.getElementById('settings-line-details').open") is True
          and page.locator("#settings-line-port").is_visible())
    page.close()

    print(f"[{engine}] 8. a screenshot of each tab (demo data)")
    page, errors = open_page(browser)
    tabs = shoot_tabs(page, SHOTS, "settings-cleanup",
                      also=(("line-open", lambda pg: (open_settings(pg, "line"),
                                                      pg.evaluate("document.getElementById('settings-line-details').open = true"))),))
    check("one per tab", tabs == ["general", "bots", "bridge", "line", "connect", "advanced"]
          and all((SHOTS / f"settings-cleanup-{t}.png").exists() for t in tabs), tabs)
    check("no page errors", not errors, errors)
    page.close()


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP  tests_settings_ui.py（沒裝 playwright）")
        return 0
    with sync_playwright() as pw:
        SHOTS.mkdir(exist_ok=True)
        for engine in ("chromium", "webkit"):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as e:                   # webkit is optional: `playwright install webkit`
                print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
                continue
            try:
                run(browser, engine)
            finally:
                browser.close()
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
