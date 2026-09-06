#!/usr/bin/env python3
"""上滑 overlay 要跟活畫面像素對齊（v0.30.17）。

日常使用中回報上滑進歷史模式時有一道明顯落差，要求無縫。根因是 v0.30.11 給
.term-pane 加了左側 gutter（放分頁名標籤），overlay 卻還停在 left:0——上滑瞬間整個
畫面往左跳一段。這份測試把 overlay 的 inset 量測公式抽出來，用真實 DOM 驗它跟得上
pane 的實際位置，不是寫死 0 也不是寫死 gutter。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_scroll_overlay_align.py
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
    if os.environ.get("_OVERLAY_QA_REEXEC") != "1":
        env = dict(os.environ, _OVERLAY_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_scroll_overlay_align.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
GUTTER = int(re.search(r"--hint-gutter: (\d+)px", html).group(1))
# 守住實作真的在「量」而不是寫死
assert "leftInset" in html, "overlay 少了 leftInset"
assert "getBoundingClientRect().left - hostRect.left" in html, \
    "leftInset 必須量 live pane 的實際左緣"

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<style>
body{margin:0;background:#16161e}
:root{--hint-gutter:__GUT__px}
#terminal-wrap{position:relative;width:900px;height:400px}
.term-pane{position:absolute;inset:0 0 0 var(--hint-gutter);overflow:hidden}
</style>
<div id="terminal-wrap"><div class="term-pane" id="pane"></div></div>
<script>
const term = new Terminal({ fontSize: 14, theme: { background: '#1a1b26' } });
term.open(document.getElementById('pane'));
window.ready = false;
term.write('row1\\r\\nrow2\\r\\nrow3', () => { window.ready = true; });
// overlay 的 inset 量測公式（與 web/index.html 的 ScrollHistory.show 同一套）
window.measure = function (gutterOn) {
  const pane = document.getElementById('pane');
  pane.style.inset = gutterOn ? '0 0 0 var(--hint-gutter)' : '0';
  const host = document.getElementById('terminal-wrap');
  const hostRect = host.getBoundingClientRect();
  const ls = pane.querySelector('.xterm-screen');
  const lsRect = ls.getBoundingClientRect();
  const rowH = lsRect.height / term.rows;
  return {
    leftInset: Math.max(0, Math.round(pane.getBoundingClientRect().left - hostRect.left)),
    bottomInset: Math.max(0, Math.round(hostRect.bottom - lsRect.bottom + rowH)),
    rowH: Math.round(rowH * 10) / 10,
    rows: term.rows,
  };
};
</script>"""

GRAB = re.search(r"    function grabLiveAnchors\(sid\) \{.*?\n    \}\n", html, re.S).group(0)
ALIGN = re.search(r"    function alignToAnchors\(anchors\) \{.*?\n    \}\n", html, re.S).group(0)

ALIGN_JS = r"""async () => {
  // 活畫面：10 行內容，最後一行是錨點
  const liveLines = [];
  for (let i = 1; i <= 9; i++) liveLines.push('live content line ' + i);
  liveLines.push('ANCHOR-LINE-UNIQUE-0001');
  // 活畫面最後一行是 tmux 綠條，歷史裡被濾掉——錨點只能是它上面那行
  liveLines.push('[sf_s173] 0:2.1.252*    [0,0] "* demo" 13:36');
  // 歷史：同樣內容，但錨點之後還多兩行——capture 的瞬間可能比活畫面新，
  // dedup 也會讓行數對不上。任一種都會讓 scrollToBottom 之後錨點不在活畫面
  // 上的那一行，這正是要修的錯位。
  const histLines = ['old history A', 'old history B']
    .concat(liveLines.slice(0, -1))          // 綠條被濾掉
    .concat(['captured after A', 'captured after B']);

  // write 是異步的——不等 callback 就讀 buffer 會全部抓到空行
  const mk = async (host, lines, rows) => {
    const t = new Terminal({ fontSize: 14, rows, cols: 80,
                             disableStdin: true, cursorBlink: false, convertEol: true });
    t.open(host);
    await new Promise(res => t.write(lines.join('\r\n'), res));
    return t;
  };
  const wrap = document.getElementById('terminal-wrap');
  const h1 = document.createElement('div');
  h1.style.cssText = 'position:absolute;inset:0;visibility:hidden';
  const h2 = document.createElement('div');
  h2.style.cssText = 'position:absolute;inset:0;visibility:hidden';
  wrap.appendChild(h1); wrap.appendChild(h2);

  const liveTerm = await mk(h1, liveLines, 6);      // 只看得到最後 6 行（含綠條）
  const histTermLocal = await mk(h2, histLines, 5); // overlay 少一行

  // 讓抽出來的函式看得到它需要的變數
  const sessions = { s1: { term: liveTerm } };
  const activeId = 's1';
  let histTerm = histTermLocal;
  __GRAB__
  __ALIGN__

  const anchors = grabLiveAnchors('s1');
  const lb = liveTerm.buffer.active;
  let liveRow = -1;
  for (let y = liveTerm.rows - 1; y >= 0; y--) {
    const ln = lb.getLine(lb.viewportY + y);
    if (ln && ln.translateToString(true).trim() === 'ANCHOR-LINE-UNIQUE-0001') { liveRow = y; break; }
  }
  const rowOf = (t) => {
    const b = t.buffer.active;
    for (let y = t.rows - 1; y >= 0; y--) {
      const ln = b.getLine(b.viewportY + y);
      if (ln && ln.translateToString(true).trim() === 'ANCHOR-LINE-UNIQUE-0001') return y;
    }
    return -1;
  };
  histTermLocal.scrollToBottom();
  const before = rowOf(histTermLocal);
  alignToAnchors(anchors);
  const after = rowOf(histTermLocal);
  // 找不到錨點的情況：亂給一個不存在的文字，位置不該變
  histTermLocal.scrollToBottom();
  const missBefore = histTermLocal.buffer.active.viewportY;
  alignToAnchors([{ text: 'THIS-TEXT-DOES-NOT-EXIST-ANYWHERE', row: 2 }]);
  const missAfter = histTermLocal.buffer.active.viewportY;

  h1.remove(); h2.remove();
  return { liveRow, histRowBefore: before, histRowAfter: after, missBefore, missAfter,
           anchorCount: anchors.length };
}""".replace("__GRAB__", GRAB).replace("__ALIGN__", ALIGN)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 960, "height": 460})
    pg.set_content(PAGE.replace("__GUT__", str(GUTTER)))
    pg.wait_for_function("() => window.ready === true")

    on = pg.evaluate("() => window.measure(true)")
    check(f"有 gutter 時 overlay 左緣跟到 {GUTTER}px（不再從 0 開始）",
          on["leftInset"] == GUTTER, str(on))
    check("留給 tmux status bar 的下緣至少一行",
          on["bottomInset"] >= on["rowH"] - 1,
          f"bottomInset={on['bottomInset']} rowH={on['rowH']}")

    off = pg.evaluate("() => window.measure(false)")
    check("沒有 gutter 時退回 0（不寫死 gutter，向後相容）",
          off["leftInset"] == 0, str(off))

    # ── 錨點對齊：歷史經 dedup 會比活畫面短，單純 scrollToBottom 會讓同一段
    #    文字落在不同高度（上滑瞬間的上下段差）。驗錨點把它拉回同一行。
    ali = pg.evaluate(ALIGN_JS)
    check("錨點行回到活畫面上的同一個螢幕行",
          ali["histRowAfter"] == ali["liveRow"],
          f"live 第 {ali['liveRow']} 行 → overlay 第 {ali['histRowAfter']} 行"
          f"（對齊前在第 {ali['histRowBefore']} 行）")
    check("對齊確實動過（證明這個案例本來是歪的）",
          ali["histRowBefore"] != ali["liveRow"],
          "測資沒造出錯位，這項驗不到東西")
    check("找不到錨點時不動（維持 scrollToBottom 的舊行為）",
          ali["missBefore"] == ali["missAfter"],
          f"{ali['missBefore']} → {ali['missAfter']}")
    b.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
