#!/usr/bin/env python3
"""遠端分頁在頂部要一眼認得出來（v0.35.4）。

回報：切到 remote session 之後，頂部看不出「這是遠端」也看不出「哪一台的哪個
分頁」。當時 chip 其實有畫，但跟本機分頁共用同一套 .tab 樣式，只在前面多一個
🌐，而且標籤只有對方的分頁名、沒有機器名——切過去等於沒有定位資訊。

這支把 renderTabs 裡真正的遠端 chip 程式碼挖出來跑在真的 CSS 上，驗三件事：
機器名有出現、樣式跟本機分頁不同、關閉鈕只收檢視。順便存一張截圖。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_remote_tab_chip.py
"""
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
SHOT = HERE / "qa-shots" / "remote-tab-chip.png"

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_RMT_CHIP_QA_REEXEC") != "1":
        env = dict(os.environ, _RMT_CHIP_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_remote_tab_chip.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
STYLE = re.search(r"<style>(.*?)</style>", html, re.S).group(1)

# renderTabs 裡實際畫遠端 chip 的那一段，原封不動搬過來
CHIP = re.search(
    r"    // 開著的遠端 session 接在本機分頁後面.*?\n    \}\);\n", html, re.S)
assert CHIP, "index.html 找不到遠端 chip 區塊"
CHIP = CHIP.group(0)

PAGE = """<!doctype html><meta charset="utf-8">
<style>__STYLE__</style>
<div id="tab-bar">
  <button class="tab-action" id="btn-sidebar" style="font-size:14px">☰</button>
  <div id="tabs"></div>
  <button id="btn-new-tab">+</button>
  <div class="tab-spacer"></div>
</div>
<!-- 左側分頁名標籤：遠端只換顏色（機器名交給上面的 chip） -->
<div id="terminal-wrap" style="position:relative;height:60px">
  <div id="tab-hint">claude</div>
</div>
<script>
const esc = (s) => String(s).replace(/[&<>"']/g, c => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $tabs = document.getElementById('tabs');
const activeId = 'rmt:peer7:s42';
window.__closed = null;
function closeTab(sid) { window.__closed = sid; }
function switchTab(sid) { window.switched = sid; }
const sessions = {
  s1: { label: 'sf dev', isRemote: false },
  'rmt:peer7:s42': { label: 'claude', isRemote: true, peerName: 'mba',
                     _remoteState: 'working' },
  // 非 active 的遠端分頁：多數時間長這樣，不該吵
  'rmt:peer7:s9': { label: 'scrum', isRemote: true, peerName: 'mba',
                    _remoteState: '' },
};
// 先畫兩個本機分頁（樣式對照組），再跑真正的遠端 chip 程式碼
[['1', 'sf dev', true], ['2', 'scrum', false]].forEach(([n, label, active]) => {
  const t = document.createElement('button');
  t.className = 'tab' + (active ? ' active' : '');
  t.innerHTML = `<span class="busy-dot"></span>`
    + `<span class="icon" style="opacity:.45;font-size:10px;min-width:12px">${n}</span>`
    + `<span class="label">${label}</span><span class="close-tab">&times;</span>`;
  $tabs.appendChild(t);
});
function renderRemote() {
__CHIP__
}
renderRemote();
</script>"""
PAGE = PAGE.replace("__STYLE__", STYLE).replace("__CHIP__", CHIP)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


tmp = HERE / ".rmt_chip_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
SHOT.parent.mkdir(exist_ok=True)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 860, "height": 90},
                                device_scale_factor=2)
        page.goto(tmp.resolve().as_uri())
        page.wait_for_selector(".tab-remote", timeout=5000)

        check("兩個遠端分頁都畫出來", page.locator(".tab-remote").count() == 2)
        chip = page.locator(".tab-remote.active")
        text = chip.inner_text()
        check("chip 帶對方機器名", "mba" in text, text)
        check("chip 帶對方分頁名", "claude" in text, text)
        check("chip 有遠端圖示", "⇄" in text, text)

        local = page.locator(".tab.active:not(.tab-remote)").first
        cs = page.evaluate("""() => {
          const g = (el) => {
            const s = getComputedStyle(el);
            return { border: s.borderTopStyle, color: s.color, bg: s.backgroundColor };
          };
          return { remote: g(document.querySelector('.tab-remote.active')),
                   local: g(document.querySelector('.tab.active:not(.tab-remote)')) };
        }""")
        check("遠端 chip 用虛線框（本機是無框）",
              cs["remote"]["border"] == "dashed" and cs["local"]["border"] != "dashed",
              str(cs))
        check("遠端 chip 顏色跟本機 active 不同",
              cs["remote"]["color"] != cs["local"]["color"], str(cs))
        check("active 的遠端 chip 有底色（看得出是當前分頁）",
              cs["remote"]["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), str(cs))

        check("狀態燈跟著對方的 agent_state 亮",
              page.locator(".tab-remote .busy-dot.on").count() == 1)

        page.locator(".tab-remote.active .close-tab").click()
        check("✕ 走 closeTab（只收這台的檢視）",
              page.evaluate("window.__closed") == "rmt:peer7:s42",
              str(page.evaluate("window.__closed")))

        page.locator(".tab-remote.active .label").click()
        check("點標籤會切過去", page.evaluate("window.switched") == "rmt:peer7:s42")

        idle = page.evaluate("""() => {
          const s = getComputedStyle(document.querySelector('.tab-remote:not(.active)'));
          return { color: s.color, border: s.borderTopColor, bg: s.backgroundColor };
        }""")
        check("非 active 的遠端 chip 是暗的（多數時間不吵）",
              idle["bg"] in ("rgba(0, 0, 0, 0)", "transparent")
              and idle["color"] != cs["remote"]["color"], str(idle))

        # 同一個元素切換 .remote，才驗得到真正的 CSS 規則（選擇器是
        # `#tab-hint.remote`，另開一個 id 不同的元素根本不會命中）
        hint = page.evaluate("""() => {
          const el = document.getElementById('tab-hint');
          el.classList.remove('remote');
          const local = getComputedStyle(el).color;
          el.classList.add('remote');
          const remote = getComputedStyle(el).color;
          return { local, remote };
        }""")
        check("左側標籤在遠端時換色", hint["local"] != hint["remote"], str(hint))
        check("標籤換的是跟 chip 同一個青色",
              hint["remote"] == cs["remote"]["color"], str(hint))

        page.locator("#tab-bar").screenshot(path=str(SHOT))
        check(f"截圖已存（{SHOT.relative_to(HERE)}）", SHOT.exists())
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
