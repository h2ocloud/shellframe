#!/usr/bin/env python3
"""Settings is a wide two-column dialog: categories on the left, the chosen one on the right.

web/index.html in a browser with a stand-in Python side (tests_settings_ui's), at 900, 1280 and 1920px wide,
at the Interface sizes M and L, in Chromium and in WebKit (the Mac app's engine) when playwright has it:

  - the dialog fits the window and grows with it up to 1200px; at 900px the categories are a row on top
  - the categories, in order: General, Bots, Office, Loops, Launch presets, Connect, Telegram, LINE, Advanced;
    clicking one shows its panel and only its panel
  - every setting is in the category it belongs to, and visible there (nothing is left in a category it moved out of)
  - nothing in a panel runs past the panel, nothing past the window; no horizontal scroll in a panel
  - Launch presets: the command column shows the whole command (the demo ones are long) and the name its whole name;
    Enter does not put a line break in a command; the table keeps its columns at 1280 and takes two lines a row at 900
  - the Bots, A2A, groups, Loops and Office switches sit in their categories and still save their setting
  - every guide entry that says `open: settings:<tab>` finds its mark there, on screen
  - screenshots: qa-shots/settings-layout-*.png (WebKit's when it runs)
Demo data only. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_settings_layout_ui.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
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
    print("SKIP  tests_settings_layout_ui.py（沒裝 playwright）")
    sys.exit(0)

import guide  # noqa: E402
import tests_settings_ui as S  # noqa: E402   (its stand-in host)

# Long commands and long CJK names: the page that cut them off had three short ones.
DEMO = [
    {"name": "Bash", "cmd": "bash", "icon": "▶"},
    {"name": "Claude", "cmd": "claude --permission-mode bypassPermissions --model opus --effort xhigh", "icon": "🚀"},
    {"name": "Claude（家用地端帳號）", "cmd": "sf-claude-home --permission-mode bypassPermissions", "icon": "🏠"},
    {"name": "Codex", "cmd": "sf-codex --dangerously-bypass-approvals-and-sandbox", "icon": "🤖"},
    {"name": "OpenCode（另一個很長的名稱）", "cmd": "/opt/demo-tools/bin/opencode --workspace /srv/demo/projects/example", "icon": "🧩"},
    {"name": "Grok Build", "cmd": "grok --permission-mode bypassPermissions --model grok-build-latest", "icon": "𝕏"},
    {"name": "Cursor Agent", "cmd": "cursor-agent --model composer-2.5 --trust --workspace /srv/demo/projects/example", "icon": "▮"},
]
S.PRESETS[:] = DEMO
# the stand-in host keeps the presets the page asks to save, delete or reorder, as the real one does
S.STUB = S.STUB.replace("      default: return R('{}');", """      case 'save_preset': { const [n, c, i] = args, ps = window.__cfg.presets, k = ps.findIndex(p => p.name === n);
                            if (k >= 0) ps[k] = {name: n, cmd: c, icon: i}; else ps.push({name: n, cmd: c, icon: i}); return R(window.__cfg); }
      case 'delete_preset': window.__cfg.presets = window.__cfg.presets.filter(p => p.name !== args[0]); return R(window.__cfg);
      case 'reorder_presets': return R(window.__cfg);
      default: return R('{}');""")
TABS = ["general", "bots", "office", "loops", "presets", "connect", "bridge", "line", "advanced"]
# where each setting lives: panel -> ids that must be in it, and visible when it is shown
EXPECT = {
    "general": ["setting-ui-style", "setting-fontsize", "setting-uiscale", "setting-lang", "setting-autoupdate",
                "setting-global-hotkey", "setting-complete-notify", "setting-usage-pace"],
    "bots": ["setting-experimental-bots", "setting-bot-memory-autosave", "setting-bot-memory-idle", "setting-experimental-a2a",
             "setting-experimental-groups", "sfb-settings-new"],
    "office": ["setting-experimental-office"],
    "loops": ["setting-experimental-loops"],
    "presets": ["settings-presets", "add-icon", "add-name", "add-cmd", "btn-add-preset"],
    "bridge": ["settings-bridge-token", "settings-bridge-users", "btn-settings-bridge-connect", "stt-backend", "stt-providers",
               "setting-mic-button", "setting-voice-apply-gate", "btn-stt-save"],
    "line": ["settings-line-token", "settings-line-secret", "settings-line-users", "btn-settings-line-connect"],
    "advanced": ["setting-auto-compact", "setting-auto-compact-threshold", "setting-model-badge", "setting-api-server",
                 "setting-status-hooks", "setting-idle-enabled", "setting-idle-value", "btn-save-idle-reaper",
                 "settings-bridge-prefix", "setting-show-tg-wrapper", "setting-tg-prompt", "settings-line-prefix", "setting-line-prompt"],
}
SWITCHES = [("setting-experimental-bots", "bots", "experimental_bots"), ("setting-experimental-a2a", "bots", "experimental_a2a"),
            ("setting-experimental-groups", "bots", "experimental_groups"), ("setting-experimental-loops", "loops", "experimental_loops"),
            ("setting-experimental-office", "office", "experimental_office")]

# one panel's geometry, in one call: what runs past the panel or the window
GEOM = """() => {
  const modal = document.querySelector('#modal-settings .modal'), P = document.querySelector('#modal-settings .settings-tab-panel.active');
  const mr = modal.getBoundingClientRect(), pr = P.getBoundingClientRect(), out = [];
  for (const el of P.querySelectorAll('*')) {
    if (!el.getClientRects().length) continue;
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' || el.closest('summary, [hidden]')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    if (r.right > pr.right + 1.5 || r.left < pr.left - 1.5) out.push((el.id || el.className || el.tagName) + ' x ' + Math.round(r.left - pr.left) + '..' + Math.round(r.right - pr.left) + '/' + Math.round(pr.width));
  }
  const nav = document.getElementById('settings-tabs-bar').getBoundingClientRect();
  return {modal: [Math.round(mr.left), Math.round(mr.top), Math.round(mr.right), Math.round(mr.bottom), Math.round(mr.width)], vw: innerWidth, vh: innerHeight,
          panelScrollX: P.scrollWidth - P.clientWidth, out: out.slice(0, 6),
          navRight: Math.round(nav.right), navBottom: Math.round(nav.bottom), panelLeft: Math.round(pr.left), panelTop: Math.round(pr.top),
          tabsOverflow: document.getElementById('settings-tabs-bar').scrollWidth - document.getElementById('settings-tabs-bar').clientWidth};
}"""


def open_page(browser, w, h, scale=None):
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(S.build_stub("zh-TW", {"experimental_bots": True, "experimental_loops": False, "experimental_office": False,
                                                "experimental_a2a": False, "experimental_groups": False}))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("document.querySelectorAll('#sidebar-sessions .sb-item[data-sid]').length >= 2", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots", timeout=20000)
    page.wait_for_timeout(600)
    page.click("#btn-settings")
    page.wait_for_selector("#modal-settings.open")
    if scale:
        page.click(f'#setting-uiscale [data-scale="{scale}"]')
        page.wait_for_timeout(200)
    return page, errors


def go(page, tab):
    page.click(f'#settings-tabs-bar [data-tab="{tab}"]')
    page.wait_for_timeout(250)


def vis(page, el_id):
    return page.evaluate("id => { const e = document.getElementById(id); return !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'); }", el_id)


def edit_paths(page, tag, w):
    """Editing a row, the icon picker, Add, Delete and a drag over the Add row, in the new table."""
    rows = "#settings-presets .settings-preset"
    cmd = "claude --model opus " + "--flag-with-a-long-name " * 6
    ta = page.locator(rows + " .sp-cmd").nth(1)
    ta.fill(cmd)
    save = page.locator(rows).nth(1).locator("button.btn-primary")
    check(f"{tag} editing a command shows the row's save button", save.is_visible())
    fit = page.evaluate("""() => { const r = document.querySelectorAll('#settings-presets .settings-preset')[1], b = r.querySelector('button.btn-primary'),
        d = r.querySelector('.sp-del'), rr = r.getBoundingClientRect(), br = b.getBoundingClientRect(), dr = d.getBoundingClientRect();
        return {inRow: br.left >= rr.left && dr.right <= rr.right + 1, apart: dr.left >= br.right - 1}; }""")
    check(f"{tag} the save button and the delete cross both fit in the row, side by side", fit["inRow"] and fit["apart"], fit)
    save.click()
    page.wait_for_timeout(300)
    saved = [c[1] for c in page.evaluate("window.__calls") if c[0] == "save_preset"]
    check(f"{tag} saving sends the whole command", saved and saved[-1][0] == DEMO[1]["name"] and saved[-1][1] == cmd, saved[-1:] )
    check(f"{tag} the list is drawn again with that command", page.locator(rows + " .sp-cmd").nth(1).input_value() == cmd)
    page.locator(rows + " .sp-icon").nth(0).click()
    check(f"{tag} the icon button opens the picker", page.locator(".emoji-picker.open").count() == 1)
    page.locator(".emoji-picker.open span").nth(3).click()
    page.wait_for_timeout(150)
    check(f"{tag} picking an icon closes it", page.locator(".emoji-picker.open").count() == 0)
    n0 = page.locator(rows).count()
    page.fill("#add-name", "Demo tool")
    page.fill("#add-cmd", "demo-tool --flag")
    page.click("#btn-add-preset")
    page.wait_for_timeout(300)
    check(f"{tag} Add saves a new row and clears the fields", page.locator(rows).count() == n0 + 1
          and page.input_value("#add-name") == "" and page.input_value("#add-cmd") == "")
    page.locator(rows).nth(n0).locator(".sp-del").click()
    page.wait_for_timeout(300)
    check(f"{tag} Delete removes that row", page.locator(rows).count() == n0)
    grip = page.locator(rows + " .sp-grip").nth(0).bounding_box()
    add = page.locator("#add-name").bounding_box()
    page.mouse.move(grip["x"] + 4, grip["y"] + 4)
    page.mouse.down()
    page.mouse.move(add["x"] + 20, add["y"] + 10, steps=6)
    page.mouse.up()
    page.wait_for_timeout(200)
    check(f"{tag} dragging a row over the Add row marks nothing and reorders nothing",
          page.locator(".sp-drag-over, .sp-dragging").count() == 0
          and not [c for c in page.evaluate("window.__calls") if c[0] == "reorder_presets"])


def run(browser, engine):
    for w, h, scale in ((1920, 1080, None), (1280, 800, None), (900, 700, None), (1280, 800, "1.18"), (900, 700, "1.18")):
        tag = f"[{engine} {w}x{h}{' L' if scale else ''}]"
        print(f"{tag} the dialog and its categories")
        page, errors = open_page(browser, w, h, scale)
        names = page.evaluate("[...document.querySelectorAll('#settings-tabs-bar .settings-tab')].map(t => t.dataset.tab)")
        check(f"{tag} the categories are {', '.join(TABS)}", names == TABS, names)
        g = page.evaluate(GEOM)
        m = g["modal"]
        check(f"{tag} the dialog is inside the window", m[0] >= -1 and m[1] >= -1 and m[2] <= g["vw"] + 1 and m[3] <= g["vh"] + 1, (m, g["vw"], g["vh"]))
        want = min(1200, g["vw"] - 48)
        check(f"{tag} it takes the window up to 1200px", abs(m[4] - want) <= 3 * (scale and 1.18 or 1) + 24 or m[4] >= want - 80, (m[4], want))
        if w >= 1280:
            check(f"{tag} categories on the left, content to their right", g["navRight"] <= g["panelLeft"] + 1, g)
        else:
            check(f"{tag} categories in a row above the content, none cut off", g["navBottom"] <= g["panelTop"] + 1 and g["tabsOverflow"] <= 1, g)

        for tab in TABS:
            go(page, tab)
            if tab == "connect":
                page.wait_for_selector("#settings-panel-connect .cg-row", timeout=5000)
            act = page.evaluate("[...document.querySelectorAll('#modal-settings .settings-tab-panel.active')].map(p => p.id)")
            nav = page.evaluate("[...document.querySelectorAll('#settings-tabs-bar .settings-tab.active')].map(t => t.dataset.tab)")
            check(f"{tag} {tab}: only its panel and its category are active", act == [f"settings-panel-{tab}"] and nav == [tab], (act, nav))
            g = page.evaluate(GEOM)
            check(f"{tag} {tab}: nothing runs past the panel, no sideways scroll", not g["out"] and g["panelScrollX"] <= 1, g)
            missing = [i for i in EXPECT.get(tab, []) if not vis(page, i)]
            check(f"{tag} {tab}: its settings are all shown", not missing, missing)
            inside = page.evaluate("ids => ids.filter(id => !document.getElementById(id).closest('#settings-panel-%s'))" % tab, EXPECT.get(tab, []))
            check(f"{tag} {tab}: and they are in this panel", not inside, inside)

        print(f"{tag} Launch presets: the whole command")
        go(page, "presets")
        rows = page.evaluate("""() => [...document.querySelectorAll('#settings-presets .settings-preset')].map(r => {
            const n = r.querySelector('.sp-name'), c = r.querySelector('.sp-cmd'), box = r.querySelector('.sp-cmdbox');
            const cr = c.getBoundingClientRect(), br = r.getBoundingClientRect();
            return {name: n.value, nameCut: n.scrollWidth > n.clientWidth + 1, cmd: c.value, cmdCut: c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1,
                    cmdW: Math.round(cr.width), inRow: cr.right <= br.right + 1 && cr.left >= br.left - 1, ghost: box.dataset.v === c.value}; })""")
        check(f"{tag} every preset is listed with its command as saved", [(r["name"], r["cmd"]) for r in rows] == [(p["name"], p["cmd"]) for p in DEMO], rows)
        check(f"{tag} no command is cut off", not [r["cmd"] for r in rows if r["cmdCut"]], [r["cmd"] for r in rows if r["cmdCut"]])
        check(f"{tag} no name is cut off", not [r["name"] for r in rows if r["nameCut"]], [r["name"] for r in rows if r["nameCut"]])
        check(f"{tag} each command box is inside its row and as tall as its text", all(r["inRow"] and r["ghost"] for r in rows), rows)
        if w >= 1280:
            check(f"{tag} the command column is wide (>= 330px) next to name, icon and delete", min(r["cmdW"] for r in rows) >= 330, [r["cmdW"] for r in rows])
            cols = page.evaluate("""() => { const r = document.querySelector('#settings-presets .settings-preset');
                return [...r.children].map(c => Math.round(c.getBoundingClientRect().top)); }""")
            check(f"{tag} one table row: grip, icon, name, command, delete share a top (±40px)", max(cols) - min(cols) <= 40, cols)
        else:
            tops = page.evaluate("""() => { const r = document.querySelector('#settings-presets .settings-preset');
                return [r.querySelector('.sp-name'), r.querySelector('.sp-cmd')].map(e => Math.round(e.getBoundingClientRect().top)); }""")
            check(f"{tag} narrow: the command takes its own line under the name", tops[1] > tops[0] + 20, tops)
        if scale is None and w == 1280:
            ta = page.locator("#settings-presets .sp-cmd").nth(0)
            ta.click()
            before = page.evaluate("document.querySelectorAll('#settings-presets .settings-preset')[0].getBoundingClientRect().height")
            ta.fill("bash --login " + "-x " * 60)
            after = page.evaluate("document.querySelectorAll('#settings-presets .settings-preset')[0].getBoundingClientRect().height")
            check(f"{tag} a long command typed into a row grows the row instead of scrolling", after > before + 10, (before, after))
            page.keyboard.press("Enter")
            check(f"{tag} Enter does not put a line break in a command", "\n" not in ta.input_value())
        edit_paths(page, tag, w)
        page.close()
        check(f"{tag} no page errors", not errors, errors)

    print(f"[{engine}] switches sit in their categories and save")
    page, errors = open_page(browser, 1280, 800)
    for el_id, tab, key in SWITCHES:
        go(page, tab)
        inside = page.evaluate("id => !!document.getElementById(id).closest('#settings-panel-%s')" % tab, el_id)
        check(f"[{engine}] {el_id} is in the {tab} category", inside)
        on0 = page.evaluate("id => document.getElementById(id).classList.contains('on')", el_id)
        page.click(f"#{el_id}")
        page.wait_for_timeout(350)
        saved = page.evaluate("window.__cfg.settings.%s" % key)
        check(f"[{engine}] {el_id}: a click saves {key} = {not on0}", saved is (not on0), (on0, saved))
    check(f"[{engine}] nothing of the five is left in General", page.evaluate(
        "ids => ids.filter(id => document.querySelector('#settings-panel-general #' + id))", [s[0] for s in SWITCHES]) == [])

    print(f"[{engine}] the guide finds its marks")
    data = guide.load()
    for f in data["features"]:
        m = (f.get("open") or "")
        if "desktop" not in f["surfaces"] or not m.startswith("settings:"):
            continue
        tab = m.split(":", 1)[1]
        go(page, tab)
        ok = page.evaluate("""id => [...document.querySelectorAll('[data-guide="' + id + '"]')].some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')""", f["id"])
        check(f"[{engine}] guide '{f['id']}' ({m}) is on screen once that category is open", ok)
    page.close()
    check(f"[{engine}] no page errors", not errors, errors)

    if engine == "webkit" or not SHOTS.exists():
        SHOTS.mkdir(exist_ok=True)
        for (w, h), tab, name in (((1920, 1080), "general", "general-1920"), ((1920, 1080), "presets", "presets-1920"),
                                  ((1280, 800), "bots", "bots-1280"), ((900, 700), "general", "general-900"),
                                  ((900, 700), "presets", "presets-900")):
            page, _ = open_page(browser, w, h)
            go(page, tab)
            page.screenshot(path=str(SHOTS / f"settings-layout-{name}.png"))
            page.close()


def main():
    with sync_playwright() as pw:
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
