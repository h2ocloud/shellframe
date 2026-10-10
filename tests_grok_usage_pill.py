#!/usr/bin/env python3
"""Grok 的週配額要真的畫進頂列膠囊：wk 百分比、pc 配速、tooltip 的產品拆分。

數字來自後端；這裡只確認 index.html 裡那支 refreshUsageBrief 吃到 grok 的
形狀時會畫出來。配速窗口釘成「還剩 2 天／共 7 天」，已用 14% → 應累積 71%。

需要 playwright；沒裝就 SKIP。
跑法：.venv/bin/python tests_grok_usage_pill.py
"""
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).parent
SHOT = HERE / "qa-shots" / "grok-usage-pill.png"
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
    print("SKIP  tests_grok_usage_pill.py（沒裝 playwright）")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")

# 從活的 index.html 切出來，不要另抄一份（抄的會在本尊改掉之後繼續 PASS）。
# 不用括號配對：樣板字串裡的 } 會把配對截斷。
func_at = html.find("function _usageLevel")
func_end = html.find("let _accountPanelEl", func_at)
assert func_at > 0 and func_end > func_at
FUNCS = html[func_at:func_end]
style_at = html.find("#usage-brief {")
style_end = html.find("/* Account switcher", style_at)
assert style_at > 0 and style_end > style_at
STYLE = html[style_at:style_end]

now = time.time()
reset = int(now + 2 * 86400)
window = 7 * 24 * 60
# tab_usage_brief 回的是 JSON 字串，refreshUsageBrief 會 JSON.parse。
payload = json.dumps(json.dumps({
    "ai": "grok", "account": "user@example.com", "five_hr": None,
    "week": {"pct": 14, "reset": "10-12 08:35", "reset_epoch": reset,
             "window_minutes": window},
    "groups": [
        {"name": "Grok Build", "pct": 12, "reset": "10-12 08:35", "window": "weekly"},
        {"name": "App Builder", "pct": 2, "reset": "10-12 08:35", "window": "weekly"},
    ],
    "error": None, "stale": False,
}, ensure_ascii=False))

PAGE = """<!doctype html><meta charset="utf-8">
<style>
  body { background:#1a1b26; margin:0; padding:24px; }
  __STYLE__
</style>
<button id="usage-brief" title="" style="display:none"></button>
<script>
window.__errors = [];
window.onerror = (m) => { window.__errors.push(String(m)); };
var activeId = 't1';
var config = { settings: { usage_pace: true } };
var AI_PROVIDERS = { grok: { label: 'Grok Build', binaries: ['grok'] } };
function _cmdTokens() { return ['grok']; }
const pywebview = { api: { tab_usage_brief: () => Promise.resolve(__PAYLOAD__) } };
__FUNCS__
window.ready = refreshUsageBrief();
</script>"""
PAGE = (PAGE.replace("__STYLE__", STYLE).replace("__FUNCS__", FUNCS)
        .replace("__PAYLOAD__", payload))
tmp = HERE / ".grok_usage_pill_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
SHOT.parent.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 520, "height": 160},
                                device_scale_factor=2)
        console = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}")
                if m.type == "error" else None)
        page.goto(tmp.resolve().as_uri())
        page.wait_for_function(
            "document.getElementById('usage-brief').innerText.includes('14%')",
            timeout=5000)
        text = page.locator("#usage-brief").inner_text()
        title = page.locator("#usage-brief").get_attribute("title") or ""
        check("膠囊顯示週已用 14%", "14%" in text, text)
        check("膠囊有 wk 標籤", "wk" in text, text)
        check("配速顯示應累積 71%", "71%" in text, text)
        check("膠囊有 pc 標籤", "pc" in text, text)
        check("落後配速用向下箭頭", "▼" in text, text)
        check("沒有編出 5h 窗口", "5h" not in text, text)
        check("tooltip 有帳號與每週配速",
              "user@example.com" in title and "每週配速" in title and "落後" in title, title)
        check("tooltip 列出 Grok Build 的拆分", "Grok Build 12%" in title, title)
        errors = page.evaluate("window.__errors")
        check("沒有 JS 例外", errors == [], str(errors))
        check("console 沒有錯誤", not console, str(console))
        page.screenshot(path=str(SHOT))
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\n截圖：{SHOT}")
print(f"Results: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
