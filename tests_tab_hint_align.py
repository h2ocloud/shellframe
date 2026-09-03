"""分頁名標籤要對齊游標那一行——用真實 xterm.js 5.5.0 驗，不是模擬。

Howard 2026-09-02 連退三版（右上角/右下角/寫死倒數第幾行）。輸入行位置會動：
權限提示行、tmux status line、多行輸入都會讓它偏，所以標籤必須跟著 cursorY。
position 邏輯直接從 web/index.html 抽出來跑，不留會走樣的副本。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_tab_hint_align.py
"""
import os
import pathlib
import re
import subprocess
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    # .venv 沒裝 playwright，系統 python 有；轉手過去，不然 run_tests.sh 底下永遠 SKIP
    if os.environ.get("_HINT_QA_REEXEC") != "1":
        env = dict(os.environ, _HINT_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_tab_hint_align.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

SP = pathlib.Path(os.environ.get("TMPDIR", "/tmp"))
html = pathlib.Path("/Users/neux/.local/apps/shellframe/web/index.html").read_text(encoding="utf-8")
# 把 positionTabHint 從 index.html 抽出來跑，不複製一份會走樣的副本
m = re.search(r"  function positionTabHint\(\) \{.*?\n  \}\n", html, re.S)
assert m, "找不到 positionTabHint"
FN = m.group(0)
css = re.search(r"  #tab-hint \{.*?\n  \}", html, re.S).group(0)
# gutter 寬度從 index.html 讀，別在測試裡寫死——它就是給人調的那個旋鈕
GUTTER = int(re.search(r"--hint-gutter: (\d+)px", html).group(1))

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<style>
body{margin:0;background:#1a1b26}
:root{--hint-gutter:__GUT__px}
#terminal-wrap{position:relative;width:900px;height:400px}
__CSS__
</style>
<div id="terminal-wrap"><div id="tab-hint" hidden>影片處理</div></div>
<script>
const term = new Terminal({ fontSize: 14, theme: { background: '#1a1b26' } });
const wrap = document.getElementById('terminal-wrap');
const host = document.createElement('div');
host.style.cssText = 'position:absolute;inset:0 0 0 var(--hint-gutter)';
wrap.appendChild(host);
term.open(host);
window.activeId = 's1';
window.sessions = { s1: { pane: wrap, term } };
let activeId = 's1', sessions = window.sessions;
__FN__
window.positionTabHint = positionTabHint;
window.ready = false;
// 印幾行，游標停在最後一行
term.write('第一行輸出\\r\\n第二行輸出\\r\\n⏺ 上一則回覆\\r\\n> 我打的字', () => { window.ready = true; });
</script>"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 960, "height": 460})
    pg.set_content(PAGE.replace("__CSS__", css).replace("__FN__", FN)
                              .replace("__GUT__", str(GUTTER)))
    pg.wait_for_function("() => window.ready === true")
    pg.wait_for_timeout(300)
    r = pg.evaluate("""() => {
      const el = document.getElementById('tab-hint');
      el.hidden = false;
      window.positionTabHint();
      const rows = document.querySelector('.xterm-rows');
      const y = window.sessions.s1.term.buffer.active.cursorY;
      const rowBox = rows.children[y].getBoundingClientRect();
      const hb = el.getBoundingClientRect();
      const wb = document.getElementById('terminal-wrap').getBoundingClientRect();
      return {
        游標在第幾行: y,
        該行內容: rows.children[y].textContent.trim().slice(0, 20),
        該行範圍: [Math.round(rowBox.top), Math.round(rowBox.bottom)],
        標籤範圍: [Math.round(hb.top), Math.round(hb.bottom)],
        垂直中心差: Math.round(((hb.top + hb.bottom) / 2) - ((rowBox.top + rowBox.bottom) / 2)),
        標籤左緣: Math.round(hb.left - wb.left),
        標籤寬: Math.round(hb.width),
        標籤右緣: Math.round(hb.right - wb.left),
        終端左緣: Math.round(document.querySelector('.xterm-screen').getBoundingClientRect().left - wb.left),
      };
    }""")
    fails = 0

    def check(name, ok, detail=""):
        nonlocal_fails.append(0 if ok else 1)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{'' if ok else '  ' + detail}")

    nonlocal_fails = []
    check("標籤垂直中心對齊游標那一行", abs(r["垂直中心差"]) <= 1, f"差 {r['垂直中心差']}px")
    check("標籤貼在最左（可往側欄那側溢出一點）", r["標籤左緣"] <= 2, f"左緣 {r['標籤左緣']}px")
    # 真正要守的是「不遮終端」，不是「不超出 gutter」——標籤刻意往左吃一點到側欄
    # 那側（字體才放得大又不多佔終端寬度），所以比的是右緣有沒有壓到終端左緣。
    check("標籤右緣沒壓到終端內容", r["標籤右緣"] <= r["終端左緣"],
          f"標籤右緣 {r['標籤右緣']}px vs 終端左緣 {r['終端左緣']}px")
    check("終端內容從 gutter 之後才開始", r["終端左緣"] >= GUTTER, f"終端左緣 {r['終端左緣']}px vs gutter {GUTTER}px")
    check("對到的就是游標所在的那一行", "我打的字" in r["該行內容"], r["該行內容"])

    # 守住 v0.30.12 的破圖：text-orientation:upright 會把拉丁字母一個個立起來，
    # 「sf dev」變 s/f/d/e/v 五行；flex 又會跟 writing-mode 打架擠成一團。
    r2 = pg.evaluate("""() => {
      const el = document.getElementById('tab-hint');
      const cs = getComputedStyle(el);
      const heights = {};
      for (const nm of ['sf dev', '影片處理']) {
        el.textContent = nm; window.positionTabHint();
        heights[nm] = Math.round(el.getBoundingClientRect().height);
      }
      return { upright: cs.textOrientation === 'upright', flex: cs.display === 'flex', heights };
    }""")
    check("英文不逐字立起來（text-orientation 不是 upright）", not r2["upright"])
    check("不用 flex（會跟 writing-mode 打架）", not r2["flex"])
    check("英文分頁名不超過 2.5 行高", r2["heights"]["sf dev"] <= 48,
          f"sf dev 高 {r2['heights']['sf dev']}px")

    # v0.30.14：雙擊標籤要能開改名 popup，所以它必須收回點擊
    r3 = pg.evaluate("""() => {
      const el = document.getElementById('tab-hint');
      const cs = getComputedStyle(el);
      let threw = null;
      try {
        el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      } catch (e) { threw = String(e); }
      return { pe: cs.pointerEvents, cursor: cs.cursor,
               noSelect: cs.userSelect === 'none' || cs.webkitUserSelect === 'none',
               threw };
    }""")
    check("標籤收回點擊（pointer-events: auto）", r3["pe"] == "auto", r3["pe"])
    check("游標是 pointer，看得出可點", r3["cursor"] == "pointer", r3["cursor"])
    check("仍關掉文字選取（雙擊不會選到字）", r3["noSelect"])
    check("dblclick 不炸", r3["threw"] is None, str(r3["threw"]))
    # 靜態守住綁定本身——CSS 對了但沒接 renameSession 一樣沒用
    check("dblclick 有接到 renameSession(activeId)",
          "renameSession(activeId)" in html and "bindTabHintRename" in html)
    pg.screenshot(path=str(SP / "cursor_align.png"))
    b.close()
    print("\nALL PASS" if not sum(nonlocal_fails) else f"\n{sum(nonlocal_fails)} FAILED")
    sys.exit(1 if sum(nonlocal_fails) else 0)
