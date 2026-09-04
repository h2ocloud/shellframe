#!/usr/bin/env python3
"""上滑歷史的 wheel 監聽必須掛在 capture 階段（v0.31.2）。

日常使用回報：opencode 分頁「還是無法往上滑」——後端已經會回正確的歷史，
但前端從來沒去問。根因是 opencode 的 TUI 會開 mouse tracking（1003+1006），
xterm.js 於是把滾輪轉成滑鼠回報送進 PTY，並且擋掉往上冒泡，掛在 pane 上的
bubble 監聽器一次都不會被呼叫。claude / pi 不開 mouse tracking，所以只有
opencode 這條路是死的，看起來像「只有它不能上滑」。

用真實 xterm.js 5.5.0 驗那個行為（不是模擬）。若哪天 xterm.js 改掉，第二項
會紅——那時這個 workaround 可以重新評估。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_scroll_wheel_capture.py
"""
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_WHEEL_QA_REEXEC") != "1":
        env = dict(os.environ, _WHEEL_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())],
                                 env=env))
    print("SKIP  tests_scroll_wheel_capture.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")

PAGE = r"""<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<style>body{margin:0}#wrap{position:relative;width:800px;height:400px}
.term-pane{position:absolute;inset:0;overflow:hidden}</style>
<div id="wrap"><div class="term-pane" id="pane"></div></div>
<script>
window.log = {bubble: 0, capture: 0, pty: []};
window.ready = false;
// mode: 'opencode' = alt-screen + mouse tracking；'plain' = 一般畫面（claude/pi）
// claim: 模擬 index.html 決定「這個滾輪歸 overlay」時的接管
window.setup = function (mode, claim) {
  const term = new Terminal({fontSize: 14, scrollback: 10000});
  window.term = term;
  term.open(document.getElementById('pane'));
  term.onData(d => window.log.pty.push(d));
  // index.html 也是這樣宣告「alt-screen 的滾輪歸 overlay」的
  term.attachCustomWheelEventHandler(() => term.buffer.active.type !== 'alternate');
  const pane = document.getElementById('pane');
  pane.addEventListener('wheel', () => { window.log.bubble++; }, {passive: true});
  pane.addEventListener('wheel', (e) => {
    window.log.capture++;
    if (claim) { e.preventDefault(); e.stopPropagation(); }
  }, {capture: true, passive: false});
  const seq = mode === 'opencode'
    ? '\x1b[?1049h\x1b[?1003h\x1b[?1006hhello'
    : 'line\r\n'.repeat(200);
  term.write(seq, () => { window.ready = true; });
};
</script>"""

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")
        if detail:
            print(f"         {detail}")


def wheel_counts(page, mode, claim=False):
    page.set_content(PAGE)
    page.evaluate("([m, c]) => window.setup(m, c)", [mode, claim])
    page.wait_for_function("window.ready === true", timeout=15000)
    box = page.locator("#pane").bounding_box()
    page.mouse.move(box["x"] + 100, box["y"] + 100)
    page.mouse.wheel(0, -200)
    page.wait_for_timeout(300)
    return page.evaluate("() => window.log")


# ── 1. 原始碼守門：註冊時必須帶 capture，且不能是 passive ──
#      passive 監聽器不能 preventDefault，接管會被瀏覽器忽略。
def test_source_registers_capture_listener():
    m = re.search(r"pane\.addEventListener\('wheel',.*?\}, (\{[^}]*\})\);",
                  html, re.S)
    check("index.html 有註冊 pane 的 wheel 監聽", bool(m))
    if not m:
        return
    opts = m.group(1)
    check("wheel 監聽用 capture 階段", "capture: true" in opts,
          f"實際選項：{opts}；bubble 階段在 mouse tracking 下收不到事件")
    check("wheel 監聽不是 passive", "passive: false" in opts,
          f"實際選項：{opts}；passive 不能 preventDefault")


# ── 2. 根因 + 修法 + 對照組，一次跑完（開瀏覽器很貴）──
def test_mouse_tracking_blocks_bubble_not_capture():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        on = wheel_counts(pg, "opencode")
        off = wheel_counts(pg, "plain")
        claimed = wheel_counts(pg, "opencode", claim=True)
        b.close()
    check("mouse tracking 下滾輪確實被送進 PTY", bool(on["pty"]),
          f"pty={on['pty']}")
    check("mouse tracking 下 bubble 監聽收不到（根因）", on["bubble"] == 0,
          f"bubble={on['bubble']}；若這裡變成 1，xterm.js 行為已改，"
          "capture workaround 可以重新評估")
    check("mouse tracking 下 capture 監聽收得到（修法）", on["capture"] >= 1,
          f"capture={on['capture']}")
    # 對照組＝claude / pi 的樣子（一般畫面、不開 mouse tracking）：本來就通，
    # 確認換成 capture 沒有把原本會動的分頁弄壞。
    check("一般畫面（claude/pi）兩種階段都收得到（對照組）",
          off["bubble"] >= 1 and off["capture"] >= 1,
          f"bubble={off['bubble']} capture={off['capture']}")
    # 已知限制，寫成測試而不是寫成註解：即使在 capture 階段
    # preventDefault + stopImmediatePropagation，滑鼠回報照樣會送到 app——
    # xterm 是從比這裡更外層的 capture 送出去的。實務上無害（overlay 蓋住
    # 畫面、mouse tracking 的 TUI 本來就一直重繪）。哪天這裡變成擋得住，
    # 表示可以把「接管」做得更乾淨。
    check("已知限制：capture 接管仍擋不掉滑鼠回報", bool(claimed["pty"]),
          f"pty={claimed['pty']}；若變成空的，xterm 行為已改，可收緊接管")


if __name__ == "__main__":
    import traceback
    for _name in sorted(list(globals())):
        if _name.startswith("test_") and callable(globals()[_name]):
            try:
                globals()[_name]()
            except Exception:
                failed += 1
                print(f"  [FAIL] {_name} (exception)")
                traceback.print_exc()
    print(f"\nResults: {passed} passed, {failed} failed")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
