#!/usr/bin/env python3
"""側欄捲到下面時，雙擊改名不能被清單重建打斷（v0.35.13）。

回報：側欄有捲軸，捲下去要雙擊某個對話改名時「都會跳走」。

機制：點某一列的第一下會經過 switchTab → renderTabs → renderSidebar，而
renderSidebar 每次都清空 innerHTML、把整份清單換成全新的節點。使用者的第二下
是對著螢幕上同一個位置點的——清單只要動一下，第二下就落在別的列上，改名的判定
（同一個 sid 且在時間窗內）因此永遠不成立，反而切到那另一個分頁。

清空 innerHTML 會不會把 scrollTop 歸零是**平台相依**的：Chromium 在同一個 task
內把內容填回去就不會（這支測試自己驗過），WebKit 會。所以修法不押在平台行為上，
而是讓雙擊對「清單有沒有動」免疫：雙擊窗口內不重建 DOM，只就地換 active 的
class（切分頁的回饋因此沒有延遲），完整重建延後到窗口結束；另外重建時一律保留
scrollTop，因為狀態燈／TG chip／模型 badge 每隔幾秒就會讓它重跑一次。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_sidebar_scroll_rename.py
"""
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_SBSCROLL_QA_REEXEC") != "1":
        env = dict(os.environ, _SBSCROLL_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_sidebar_scroll_rename.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 守住實作 ────────────────────────────────────────────────────────────────
body = html.split("function renderSidebar() {")[1].split("\n  }\n")[0]
wipe = body.index("container.innerHTML = ''")
head = body[:wipe]
check("雙擊窗口內不重建 DOM（延後）",
      "_sbRenderDeferred = setTimeout(renderSidebar" in head, head[-300:])
check("守門旗標在 mousedown 就武裝（第一下的重建也要擋住）",
      "_sbClickGuardUntil = Date.now() + SB_DBLCLICK_MS" in html
      and "'mousedown'" in html.split("_sbClickGuardUntil = Date.now()")[0][-600:],
      "mousedown 沒有武裝守門，第一下仍會重建")
check("renderSidebar 用的是這個獨立旗標，不是 _sbLastClickTime",
      "Date.now() < _sbClickGuardUntil" in head, head[-300:])
check("延後時仍然立刻套用 active 高亮（切分頁不能有延遲）",
      "classList.add('active')" in head and "classList.remove('active')" in head,
      head[-300:])
check("重建前先記下 scrollTop", "container.scrollTop" in head, head[-200:])
check("重建後還原 scrollTop",
      "container.scrollTop = keepScroll" in body[wipe:], body[wipe:][-200:])

# ── 真實的雙擊偵測程式碼 ────────────────────────────────────────────────────
DECL = re.search(r"  let _sbLastClickSid = null, _sbLastClickTime = 0;\n"
                 r"  const SB_DBLCLICK_MS = \d+;\n", html).group(0)
HANDLER = re.search(
    r"  // Double-click sidebar label to rename.*?\n  \}\n", html, re.S).group(0)

PAGE = """<!doctype html><meta charset="utf-8">
<style>
  body { margin:0; font:13px system-ui; background:#16161e; color:#c0caf5; }
  #sidebar-sessions { height:160px; overflow-y:auto; width:220px; }
  .sb-item { height:32px; display:flex; align-items:center; padding:0 8px;
             border-bottom:1px solid #292e42; }
</style>
<div id="sidebar-sessions"></div>
<script>
window.__renamed = null;
window.__switched = [];
window.__rebuilds = 0;
let activeId = null;
function renameSession(sid) { window.__renamed = sid; }
__DECL__
// renderSidebar 的關鍵形狀：清空重建。RESTORE 由測試切換，用來證明「沒還原」
// 真的會壞。
// renderSidebar 的關鍵形狀：延後守門 + 清空重建 + 還原捲動。守門與還原這兩段
// 是從 web/index.html 挖出來的，不是測試自己寫的簡化版。
let _sbRenderDeferredT = 0;
let _sbClickGuardUntil = 0;
function rebuild() {
  const container = document.getElementById('sidebar-sessions');
__GUARD__
  window.__rebuilds++;
  const keepScroll = container.scrollTop;
  container.innerHTML = '';
  for (let i = 1; i <= 20; i++) {
    const d = document.createElement('div');
    d.className = 'sb-item' + (('s' + i) === activeId ? ' active' : '');
    d.dataset.sid = 's' + i;
    d.textContent = 'session ' + i;
    container.appendChild(d);
  }
  if (keepScroll) container.scrollTop = keepScroll;
}
// 第一下點擊會切分頁，而切分頁會重建側欄——這是真實流程
// mousedown 武裝守門——順序跟真實程式碼一致（mousedown 比 click 早）
document.getElementById('sidebar-sessions').addEventListener('mousedown', (e) => {
  const item = e.target.closest('.sb-item');
  if (!item || !item.dataset.sid) return;
  _sbClickGuardUntil = Date.now() + SB_DBLCLICK_MS;
});
document.getElementById('sidebar-sessions').addEventListener('click', (e) => {
  const item = e.target.closest('.sb-item');
  if (!item) return;
  activeId = item.dataset.sid;
  window.__switched.push(item.dataset.sid);
  rebuild();
});
__HANDLER__
rebuild();
</script>"""
# 延後守門那一段，原封不動搬過來（把變數名接到測試的 rebuild 上）
GUARD = re.search(r"    // 雙擊窗口內不要重建 DOM。.*?\n    \}\n", html, re.S).group(0)
GUARD = GUARD.replace("_sbRenderDeferred", "_sbRenderDeferredT") \
             .replace("setTimeout(renderSidebar", "setTimeout(rebuild")
PAGE = (PAGE.replace("__DECL__", DECL).replace("__HANDLER__", HANDLER)
            .replace("__GUARD__", GUARD))

tmp = HERE / ".sbscroll_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 360, "height": 260})
        page.goto(tmp.resolve().as_uri())
        page.wait_for_selector(".sb-item")

        def dbl_click_row(sid):
            page.evaluate("() => { window.__renamed = null; window.__switched = [];"
                          "window.__rebuilds = 0;"
                          "document.getElementById('sidebar-sessions').scrollTop = 300; }")
            box = page.locator(f'.sb-item[data-sid="{sid}"]').bounding_box()
            # 對著同一個螢幕座標點兩下——使用者的手不會跟著清單跳
            page.mouse.click(box["x"] + 40, box["y"] + 10)
            page.mouse.click(box["x"] + 40, box["y"] + 10)
            return {
                "renamed": page.evaluate("window.__renamed"),
                "switched": page.evaluate("window.__switched"),
                "rebuilds": page.evaluate("window.__rebuilds"),
                "scrollTop": page.evaluate(
                    "document.getElementById('sidebar-sessions').scrollTop"),
                "activeSid": page.evaluate(
                    "(document.querySelector('.sb-item.active')||{}).dataset?.sid"
                    " || null"),
            }

        scrolled = page.evaluate("() => { const c = "
                                 "document.getElementById('sidebar-sessions');"
                                 "c.scrollTop = 300; return c.scrollTop; }")
        check("側欄真的可以捲動（測試前提）", scrolled > 0, str(scrolled))

        after = dbl_click_row("s14")
        check("雙擊改名觸發在正確的 sid 上", after["renamed"] == "s14", str(after))
        check("捲動位置沒有被重置", after["scrollTop"] > 0, str(after))
        check("兩下都落在同一列（不會誤切到別的分頁）",
              set(after["switched"]) == {"s14"}, str(after))
        check("雙擊過程中完全沒有重建 DOM（清單不可能動）",
              after["rebuilds"] == 0, str(after))
        check("延後期間 active 高亮仍然立刻跟上",
              after["activeSid"] == "s14", str(after))

        # 延後的重建要真的補跑，否則側欄會停在舊狀態
        page.wait_for_function("window.__rebuilds > 0", timeout=3000)
        check("雙擊窗口結束後補跑重建",
              page.evaluate("window.__rebuilds") >= 1)
        check("補跑之後捲動位置還在",
              page.evaluate(
                  "document.getElementById('sidebar-sessions').scrollTop") > 0,
              str(page.evaluate(
                  "document.getElementById('sidebar-sessions').scrollTop")))

        # 單擊仍然只是切分頁，不能誤觸改名
        page.evaluate("() => { window.__renamed = null; }")
        box = page.locator('.sb-item[data-sid="s3"]').bounding_box()
        page.mouse.click(box["x"] + 40, box["y"] + 10)
        page.wait_for_timeout(500)          # 超過雙擊判定窗
        check("單擊只切分頁，不會誤觸改名",
              page.evaluate("window.__renamed") is None,
              str(page.evaluate("window.__renamed")))
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
