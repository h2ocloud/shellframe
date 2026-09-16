#!/usr/bin/env python3
"""配對成功不該留下一片空白面板（v0.35.7）。

回報畫面：視窗下半部約 260px 全黑，左上角只有一個「—」、右上角一個 ✕。那是
Frame Link 的訊息／檔案面板被展開，但沒有選任何 peer 的訊息或檔案——面板
只承載這兩種內容，沒有選擇就沒有東西可畫，開了只是從終端偷走 260px。

根因：配對成功後無條件 toggleLinkPanel(true)，而 linkView（選了哪個 peer 的
訊息還是檔案）還是 null。修法兩層：配對成功只更新側欄；toggleLinkPanel 在
沒有 linkView 時拒絕展開，任何呼叫端都不可能再開出空白面板。

這支把 web/index.html 裡真正的那幾支函式挖出來跑，驗四件事：配對成功不開、
選訊息會開且有標題、✕ 關掉、關掉時終端有重新 fit。順便存截圖。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_link_panel_empty.py
"""
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
SHOT_DIR = HERE / "qa-shots"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_LINKPANEL_QA_REEXEC") != "1":
        env = dict(os.environ, _LINKPANEL_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_link_panel_empty.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
STYLE = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
PANEL_HTML = re.search(
    r'<div id="link-resizer".*?</div>\s*<div id="link-panel">.*?\n</div>\n', html, re.S)
assert PANEL_HTML, "找不到 link-panel 的 HTML"
PANEL_HTML = PANEL_HTML.group(0)


def grab(pattern):
    m = re.search(pattern, html, re.S)
    assert m, f"index.html 找不到：{pattern}"
    return m.group(0)


TOGGLE = grab(r"  function toggleLinkPanel\(force\) \{.*?\n  \}\n")
# ✕ 的實際接線，不是測試自己補一個 listener
CLOSE_WIRING = grab(r"  const _btnLinkClose = document\.getElementById\('btn-link-close'\);\n"
                    r"  if \(_btnLinkClose\)[^\n]*\n")
OPEN_MSG = grab(r"  async function openLinkMessages\(peer\) \{.*?\n  \}\n")
# 配對成功那段的判斷式，原封不動搬過來。錨在 `linkRemoteTabs = {}` 上——
# 這個檔案裡還有另一處 `if (res.success) { finish();`（遠端開新 session），
# 只錨 finish() 會抓到那一段。
JOIN_OK = grab(r"        if \(res\.success\) \{\n          finish\(\);\n"
               r"          linkRemoteTabs = \{\};.*?\n        \} else \{")

PAGE = """<!doctype html><meta charset="utf-8">
<style>__STYLE__
  html, body { margin:0; height:100%; }
  #shell { display:flex; flex-direction:column; height:100vh; background:#1a1b26; }
  #fake-term { flex:1; background:#1a1b26; color:#565f89; padding:8px;
               font:12px ui-monospace, monospace; }
</style>
<div id="shell">
  <div id="fake-term">（假終端區：面板開合會壓縮這塊高度）</div>
__PANEL__
</div>
<script>
window.__fits = 0;
let linkView = null;
let linkRemoteTabs = {};
let activeId = 's1';
const sessions = { s1: { fitAddon: { fit: () => { window.__fits++; } } } };
const esc2 = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function linkMainTitle(h) { document.getElementById('link-main-title').innerHTML = h; }
function renderSidebar() { window.__sidebarRenders = (window.__sidebarRenders||0)+1; }
function refreshLinkStatus() { renderSidebar(); }
const linkState = { frame_name: 'me' };
const pywebview = { api: {
  link_recent_events: () => Promise.resolve(JSON.stringify([
    { kind: 'message', peer_id: 'p1', peer_name: 'mba', dir: 'in',
      text: '哈囉', ts: 1757000000 }])),
}};
function appendLinkMsg(ev) {
  const body = document.getElementById('link-main-body');
  const d = document.createElement('div');
  d.className = 'link-msg';
  d.textContent = ev.text;
  body.appendChild(d);
}
__TOGGLE__
__OPEN_MSG__
// 配對成功的那段判斷，跑在受控的 stub 上
function finish() { window.__modalClosed = true; }
async function pairJoin(res) {
__JOIN_OK__
      window.__pairFailed = true;
    }
}
__CLOSE_WIRING__
window.linkViewGet = () => linkView;
window.openMsgFor = () => openLinkMessages({ id: 'p1', name: 'mba' });
</script>"""
PAGE = (PAGE.replace("__STYLE__", STYLE).replace("__PANEL__", PANEL_HTML)
            .replace("__TOGGLE__", TOGGLE).replace("__OPEN_MSG__", OPEN_MSG)
            .replace("__JOIN_OK__", JOIN_OK)
            .replace("__CLOSE_WIRING__", CLOSE_WIRING))

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


tmp = HERE / ".linkpanel_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
SHOT_DIR.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 500})
        page.goto(tmp.resolve().as_uri())

        term_h0 = page.evaluate("document.getElementById('fake-term').offsetHeight")
        check("初始面板是收起的", not page.locator("#link-panel.open").count())

        # ── 配對成功 ──
        page.evaluate("() => pairJoin({ success: true })")
        page.wait_for_function("window.__modalClosed === true", timeout=3000)
        check("配對成功後面板仍然收起（不再是 260px 空白）",
              page.locator("#link-panel.open").count() == 0)
        check("配對成功有更新側欄",
              page.evaluate("window.__sidebarRenders") >= 1)
        check("終端高度沒有被偷走",
              page.evaluate("document.getElementById('fake-term').offsetHeight") == term_h0,
              f"{term_h0} → {page.evaluate('document.getElementById(chr(39)+chr(39))')}"
              if False else "")
        page.screenshot(path=str(SHOT_DIR / "link-panel-after-pair.png"))

        # ── 直接硬要開（守門） ──
        page.evaluate("() => toggleLinkPanel(true)")
        check("沒有選訊息／檔案時，硬要展開也開不起來",
              page.locator("#link-panel.open").count() == 0)

        # ── 點 💬 才開 ──
        page.evaluate("() => window.openMsgFor()")
        page.wait_for_selector("#link-panel.open", timeout=3000)
        check("選了訊息就會展開", page.locator("#link-panel.open").count() == 1)
        title = page.locator("#link-main-title").inner_text()
        check("標題有填內容（不是佔位的「—」）", "mba" in title and title.strip() != "—", title)
        check("內容區有訊息", page.locator("#link-main-body .link-msg").count() >= 1)
        check("面板展開後終端變矮",
              page.evaluate("document.getElementById('fake-term').offsetHeight") < term_h0)
        page.screenshot(path=str(SHOT_DIR / "link-panel-messages.png"))

        # ── ✕ 關閉 ──
        fits_before = page.evaluate("window.__fits")
        page.locator("#btn-link-close").click()
        check("✕ 關得掉", page.locator("#link-panel.open").count() == 0)
        page.wait_for_function(f"window.__fits > {fits_before}", timeout=3000)
        check("關閉後終端有重新 fit", page.evaluate("window.__fits") > fits_before)
        check("關閉後終端高度回復",
              page.evaluate("document.getElementById('fake-term').offsetHeight") == term_h0)
        check("關閉會清掉 linkView（下次不會憑舊選擇開起來）",
              page.evaluate("window.linkViewGet()") is None)
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
