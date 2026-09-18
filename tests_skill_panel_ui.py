#!/usr/bin/env python3
"""「給 AI 的說明」面板：狀態看得懂、按鈕真的會動。

這個面板存在的理由是「不用人做事」——啟動時就把指標 skill 裝進 Claude Code 會
自己讀的目錄。所以面板的主體是**狀態**：到底裝了沒、裝在哪、AI 該跑哪一行。
按鈕是補救用的，排在後面。

這支把 index.html 裡真正的 markup 與 handler 跑起來，確認：
  - 讀得到狀態，兩個目標各自標示已裝／未裝
  - 指標要 AI 跑的那行指令有顯示出來
  - 沒有 ~/.codex 的機器，Codex 那顆要 disabled，而且說明原因
  - 已寫入時按鈕改成「移除」，並以 remove=true 呼叫
  - 沒有 JS 例外

需要 playwright；沒裝就 SKIP。
跑法：.venv/bin/python tests_skill_panel_ui.py
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
SHOT = HERE / "qa-shots" / "skill-panel.png"
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
    print("SKIP  tests_skill_panel_ui.py（沒裝 playwright）")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
STYLE = re.search(r"<style>(.*?)</style>", html, re.S).group(1)


def grab(pattern):
    m = re.search(pattern, html, re.S)
    assert m, f"index.html 找不到：{pattern}"
    return m.group(0)


MARKUP = grab(r'<div class="about-section" id="about-skill-section">.*?</div>\s*</div>')
REFRESH = grab(r"  async function refreshSkillInstall\(\) \{.*?\n  \}\n")
RUN = grab(r"  async function runSkillInstall\(target, remove\) \{.*?\n  \}\n")
WIRE = grab(r"  document\.getElementById\('btn-install-claude'\)\n.*?"
            r"dataset\.remove === '1'\)\);\n")

PAGE = """<!doctype html><meta charset="utf-8">
<style>__STYLE__
  body { background:#24283b; margin:0; padding:16px; width:560px; color:#c0caf5; }
  .about-section p { font-size:12px; line-height:1.6; color:#9aa5ce; }
</style>
__MARKUP__
<script>
window.__errors = [];
window.onerror = (m) => { window.__errors.push(String(m)); };
window.__installs = [];
// Claude installed, Codex present but not written to — the state a machine is in
// right after a first launch, which is what the panel mostly has to explain.
let STATE = { claude: true, claude_path: '/Users/x/.claude/skills/shellframe/SKILL.md',
              codex: false, codex_path: '/Users/x/.codex/AGENTS.md',
              codex_available: true, sfctl: '/Users/x/.local/bin/sfctl' };
const pywebview = { api: {
  ai_skill_status: () => Promise.resolve(JSON.stringify({ success: true, details: STATE })),
  ai_skill_install: (target, remove) => {
    window.__installs.push([target, remove]);
    if (target === 'codex') STATE = Object.assign({}, STATE, { codex: !remove });
    return Promise.resolve(JSON.stringify(
      { success: true, message: remove ? '已移除' : '已寫入', details: STATE }));
  },
}};
__REFRESH__
__RUN__
__WIRE__
window.ready = refreshSkillInstall().then(() => true);
</script>"""
PAGE = (PAGE.replace("__STYLE__", STYLE).replace("__MARKUP__", MARKUP)
            .replace("__REFRESH__", REFRESH).replace("__RUN__", RUN)
            .replace("__WIRE__", WIRE))

tmp = HERE / ".skillpanel_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
SHOT.parent.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 560, "height": 420},
                                device_scale_factor=2)
        console = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}")
                if m.type == "error" else None)
        page.goto(tmp.resolve().as_uri())
        page.wait_for_function("document.getElementById('skill-install-status')"
                               ".innerText.includes('Claude Code')", timeout=5000)

        status = page.locator("#skill-install-status").inner_text()
        check("Claude Code 標成已裝", "已裝" in status, status)
        check("Codex 標成未寫入", "未寫入" in status, status)
        check("說得出裝在哪", ".claude/skills/shellframe" in status, status)
        check("AI 要跑的那行有出現", "sfctl skill" in status, status)

        cx = page.locator("#btn-install-codex")
        check("Codex 按鈕文案是寫入", "寫入" in cx.inner_text(), cx.inner_text())
        page.screenshot(path=str(SHOT))

        cx.click()
        page.wait_for_function(
            "document.getElementById('btn-install-codex').innerText.includes('移除')",
            timeout=5000)
        check("寫入後按鈕變成移除", "移除" in page.locator("#btn-install-codex").inner_text())
        check("第一次是寫入不是移除",
              page.evaluate("window.__installs")[0] == ["codex", False],
              str(page.evaluate("window.__installs")))
        page.locator("#btn-install-codex").click()
        page.wait_for_function("window.__installs.length === 2", timeout=5000)
        check("再按一次是移除",
              page.evaluate("window.__installs")[1] == ["codex", True],
              str(page.evaluate("window.__installs")))

        # 沒有 ~/.codex 的機器
        page.evaluate("STATE = Object.assign({}, STATE, "
                      "{ codex_available: false, codex: false }); refreshSkillInstall()")
        page.wait_for_function(
            "document.getElementById('skill-install-status').innerText.includes('沒有')",
            timeout=5000)
        check("沒裝 Codex 的機器把按鈕停用",
              page.locator("#btn-install-codex").is_disabled())
        check("而且講出原因",
              "沒有 ~/.codex" in page.locator("#skill-install-status").inner_text())

        check("沒有 JS 例外", len(page.evaluate("window.__errors")) == 0,
              str(page.evaluate("window.__errors")))
        check("console 沒有錯誤", not console, str(console))
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\n截圖：{SHOT}")
print(f"Results: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
