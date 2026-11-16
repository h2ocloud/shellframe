#!/usr/bin/env python3
"""Settings after the clean-up: web/index.html in a browser with a stand-in Python side.

  - Shift+Enter always puts a new line in, also with an old config that had it switched off
    (settings.shiftEnterNewline: false): `\\n` to Claude Code, `CSI 27;2;13~` to Codex. Settings
    has no switch for it, and the tip at the foot of General says it is always so
  - Settings → Bots: the Bots switch is there, its label no longer says "experimental"
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_settings_ui.py
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
STUB = """
window.__calls = [];
window.__cfg = %(cfg)s; window.__sessions = %(sessions)s; window.__bots = %(bots)s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
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
      default: return R('{}');
    }
  };
}})};
"""


def build_stub(lang="zh-TW", settings=None):
    cfg = {"settings": dict({"language": lang, "sidebar_open": True, "ui_style": "cli", "experimental_bots": True},
                            **(settings or {})),
           "presets": PRESETS, "agent_roster": {}}
    return STUB % {k: json.dumps(v, ensure_ascii=False) for k, v in
                   (("cfg", cfg), ("sessions", SESSIONS), ("bots", BOTS))}


def open_page(browser, lang="zh-TW", settings=None, web=HERE / "web"):
    page = browser.new_page(viewport={"width": 1180, "height": 1400}, device_scale_factor=2)
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
