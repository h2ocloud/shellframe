#!/usr/bin/env python3
"""IME 去重 —— 接在**真實 xterm.js 5.5.0** 後面的端對端序列測試。

為什麼要真的載 xterm：雙送是 xterm 內部兩條路（`_inputEvent` 與
`_finalizeComposition` 的 setTimeout）同時放行造成的，光測我們的去重函式
只能驗「給它兩個一樣的字會不會擋」，驗不到「xterm 到底送幾次」。這份測試
把 `_makeImeDedup` 從 web/index.html 抽出來接上真實 xterm 的 onData，
dispatch 真實的 composition/input/keydown/keyup 事件，看**最後進 PTY 的是
什麼**。

第一版去重用 150ms 時間窗口，會吃掉使用者連打的第二個相同字（Howard
2026-09-02 回報）。實測兩次送出只差 ≤0.8ms，窗口大了兩個數量級——所以改成
綁定「這一次 commit」。案例 E/F 就是守這條線的。

需要 playwright（`pip install playwright && playwright install chromium`）。
沒裝就跳過，不讓它擋住其他測試。

跑法：python3 tests_ime_seq.py
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    # .venv 裡沒裝 playwright，系統 python 有。轉手過去跑一次——不然這份測試在
    # run_tests.sh（走 .venv）底下會永遠 SKIP，等於掛在那裡沒人測。
    if os.environ.get("_IME_SEQ_REEXEC") != "1":
        env = dict(os.environ, _IME_SEQ_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(Path(__file__).resolve())], env=env))
    print("SKIP  tests_ime_seq.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")
m = re.search(r"function _makeImeDedup\(\) \{.*?\n  \}\n", html, re.S)
if not m:
    print("FAIL  在 web/index.html 找不到 _makeImeDedup")
    sys.exit(1)
DEDUP_SRC = m.group(0)

# 下面 harness 註冊的 customKeyEventHandler 是 index.html 那道讓渡的等價複製。
# 這行斷言確保它沒被拿掉——不然測試會在「功能已被移除」的情況下繼續全綠。
assert "_imeComposing && _imeComposingSince" in html, \
    "web/index.html 少了 composition 進行中的鍵盤讓渡"

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<div id="t" style="width:600px;height:300px"></div>
<script>
window.DROPPED = [];
window.pywebview = { api: { js_debug: (tag, msg) => window.DROPPED.push(JSON.parse(msg)) } };
__DEDUP__
const term = new Terminal();
term.open(document.getElementById('t'));
const ta = document.querySelector('textarea.xterm-helper-textarea');
const dedup = _makeImeDedup();
// 跟 web/index.html 一樣的接法：composition 事件餵給去重器，它才知道
// 「這一次 commit 的內容是什麼」。少了這兩行就只剩 400ms 後備規則。
let _imeComposing = false, _imeComposingSince = 0;
ta.addEventListener('compositionstart', () => {
  _imeComposing = true; _imeComposingSince = Date.now(); dedup.started();
});
ta.addEventListener('compositionend', (e) => {
  _imeComposing = false; _imeComposingSince = 0; dedup.composed(e && e.data);
});
// index.html 那道讓渡的等價複製：composition 進行中鍵盤完全歸 IME。
window._guard = function (ev) {
  if (ev.type !== 'keydown') return true;
  if (_imeComposing && _imeComposingSince && Date.now() - _imeComposingSince < 30000) {
    return false;
  }
  return true;
};
window._setComposing = function (on) {
  _imeComposing = on; _imeComposingSince = on ? Date.now() : 0;
};
window._ageComposing = function (ms) { _imeComposingSince -= ms; };
term.attachCustomKeyEventHandler(window._guard);
window.RAW = [];      // xterm 實際送出的（去重前）
window.OUT = [];      // 去重後真正會進 PTY 的
term.onData(d => {
  window.RAW.push(d);
  if (!dedup.shouldDrop(d)) window.OUT.push(d);
});
const sleep = ms => new Promise(r => setTimeout(r, ms));

// 打一個中文字。keyupBeforeEnd=true 就是 macOS 注音「按 shift 切英文」的形狀：
// 切換在 keyup 發生，commit 進來時 xterm 的 _keyDownSeen 已被清掉。
window.typeCJK = async function (commit, keyupBeforeEnd) {
  ta.focus();
  const base = ta.value;
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Shift', keyCode: 16, bubbles: true }));
  ta.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
  ta.dispatchEvent(new CompositionEvent('compositionupdate', { data: 'ㄋㄧ', bubbles: true }));
  await sleep(5);
  if (keyupBeforeEnd) ta.dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', keyCode: 16, bubbles: true }));
  ta.value = base + commit;
  ta.dispatchEvent(new CompositionEvent('compositionend', { data: commit, bubbles: true }));
  ta.dispatchEvent(new InputEvent('input', { data: commit, inputType: 'insertText', composed: true, bubbles: true }));
  if (!keyupBeforeEnd) ta.dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', keyCode: 16, bubbles: true }));
  await sleep(50);
};
window.typeAscii = function (ch) {
  ta.focus();
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
  ta.dispatchEvent(new InputEvent('input', { data: ch, inputType: 'insertText', bubbles: true }));
  ta.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
};
// 注音的真實選字流程：打注音 → 空白叫候選清單 → 數字鍵挑字。
// 這條路踩的是 xterm 的 CompositionHelper.keydown——它只對 16/17/18 放行，
// 空白(32)、數字(50) 都會觸發 _finalizeComposition(false)，把還沒選字的注音
// 直接送出去。
window.pickFromCandidates = async function (commit, switchKeyCode) {
  ta.focus(); ta.value = '';
  ta.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
  ta.dispatchEvent(new CompositionEvent('compositionupdate', { data: 'ㄧ', bubbles: true }));
  ta.value = 'ㄧ';
  await sleep(5);
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', keyCode: 32, bubbles: true }));
  ta.dispatchEvent(new KeyboardEvent('keyup', { key: ' ', keyCode: 32, bubbles: true }));
  ta.dispatchEvent(new CompositionEvent('compositionupdate', { data: 'ㄧ', bubbles: true }));
  await sleep(5);
  if (switchKeyCode) {   // 中途切中英文（CapsLock / Shift）
    ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'x', keyCode: switchKeyCode, bubbles: true }));
    ta.dispatchEvent(new KeyboardEvent('keyup', { key: 'x', keyCode: switchKeyCode, bubbles: true }));
    await sleep(5);
  }
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: '2', keyCode: 50, bubbles: true }));
  ta.dispatchEvent(new KeyboardEvent('keyup', { key: '2', keyCode: 50, bubbles: true }));
  ta.value = commit;
  ta.dispatchEvent(new CompositionEvent('compositionend', { data: commit, bubbles: true }));
  ta.dispatchEvent(new InputEvent('input', { data: commit, inputType: 'insertText', composed: true, bubbles: true }));
  await sleep(60);
};
window.reset = function () { window.RAW.length = 0; window.OUT.length = 0; window.DROPPED.length = 0; ta.value = ''; };
</script>"""

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
    browser = pw.chromium.launch()
    page = browser.new_page()
    page.set_content(PAGE.replace("__DEDUP__", DEDUP_SRC))
    page.wait_for_function("() => window.typeCJK && window.OUT")

    # A — 就是這次的 bug：shift 中斷 composition
    page.evaluate("window.reset()")
    page.evaluate("() => window.typeCJK('你', true)")
    raw, out = page.evaluate("window.RAW"), page.evaluate("window.OUT")
    check("A shift 中斷：xterm 真的送兩次", raw == ["你", "你"], f"raw={raw}")
    check("A shift 中斷：去重後只進 PTY 一次", out == ["你"], f"out={out}")

    # B — 正常選字（keyup 在 compositionend 之後）本來就只送一次
    page.evaluate("window.reset()")
    page.evaluate("() => window.typeCJK('你', false)")
    check("B 正常選字：一次進一次出",
          page.evaluate("window.RAW") == ["你"] and page.evaluate("window.OUT") == ["你"],
          f"raw={page.evaluate('window.RAW')} out={page.evaluate('window.OUT')}")

    # E — 守住 Howard 回報的誤吞：連打兩個一樣的字，兩個都要留下
    page.evaluate("window.reset()")
    page.evaluate("async () => { await window.typeCJK('好', false); await window.typeCJK('好', false); }")
    out = page.evaluate("window.OUT")
    check("E 連打兩個相同的字 → 兩個都留（不能吃字）", out == ["好", "好"], f"out={out}")

    # F — shift 中斷之後緊接著正常打同一個字，第二個仍要留下
    page.evaluate("window.reset()")
    page.evaluate("async () => { await window.typeCJK('好', true); await window.typeCJK('好', false); }")
    out = page.evaluate("window.OUT")
    check("F shift 中斷後再正常打同一個字 → 留兩個（一個來自中斷、一個是新打的）",
          out == ["好", "好"], f"out={out}")

    # D — commit 是多字時同樣雙送
    page.evaluate("window.reset()")
    page.evaluate("() => window.typeCJK('你好', true)")
    check("D 多字 commit：雙送擋成一次",
          page.evaluate("window.RAW") == ["你好", "你好"] and page.evaluate("window.OUT") == ["你好"],
          f"raw={page.evaluate('window.RAW')} out={page.evaluate('window.OUT')}")

    # G — 純 ASCII 一律不經手（終端每個按鍵都走這條路）
    page.evaluate("window.reset()")
    page.evaluate("() => { window.typeAscii('a'); window.typeAscii('a'); window.typeAscii('a'); }")
    out = page.evaluate("window.OUT")
    check("G ASCII 連打不去重", out == ["a", "a", "a"], f"out={out}")

    # I — Howard 的真實操作：空白叫候選清單 + 數字鍵選字
    page.evaluate("window.reset()")
    page.evaluate("() => window.pickFromCandidates('依', 0)")
    out = page.evaluate("window.OUT")
    check("I 候選清單選字 → 只進一個字（注音符號與選字數字都不能漏）",
          out == ["依"], f"out={out}")

    # J — 同上，中途按 CapsLock 切中英文（Howard 說主要是想切中英文時發生）
    page.evaluate("window.reset()")
    page.evaluate("() => window.pickFromCandidates('依', 20)")
    out = page.evaluate("window.OUT")
    check("J 選字途中切中英文（CapsLock）→ 仍只進一個字", out == ["依"], f"out={out}")

    page.evaluate("window.reset()")
    page.evaluate("() => window.pickFromCandidates('依', 16)")
    out = page.evaluate("window.OUT")
    check("J2 選字途中按 Shift → 仍只進一個字", out == ["依"], f"out={out}")

    # K — 紅線：讓渡只能在組字期間生效，平常打字一個鍵都不准攔。
    # 直接問讓渡函式本身：走 onData 驗不準——harness 沒 dispatch keypress，
    # xterm 對空白鍵本來就不會從 keydown 生資料，那是模擬的侷限不是行為。
    res = page.evaluate("""() => {
      const mk = (c) => new KeyboardEvent('keydown', { keyCode: c });
      window._setComposing(false);
      const idle = [32, 50, 65, 13, 20, 16].map(c => window._guard(mk(c)));
      window._setComposing(true);
      const composing = [32, 50, 65, 13, 20, 16].map(c => window._guard(mk(c)));
      window._setComposing(false);
      const keyup = window._guard(new KeyboardEvent('keyup', { keyCode: 32 }));
      return { idle, composing, keyup };
    }""")
    check("K 沒在組字時：空白/數字/字母/Enter/Caps/Shift 全部放行",
          all(res["idle"]), f"idle={res['idle']}")
    check("K2 組字中：這些鍵全部讓給 IME",
          not any(res["composing"]), f"composing={res['composing']}")
    check("K3 讓渡只看 keydown，keyup 不攔", res["keyup"] is True)

    # L — 保險：compositionstart 的時間戳超過 30 秒就不再信任這個狀態，
    # 免得 compositionend 沒送到時鍵盤被永久吃掉。這裡把起點往回撥 31 秒來驗。
    res = page.evaluate("""() => {
      const mk = (c) => new KeyboardEvent('keydown', { keyCode: c });
      window._setComposing(true);
      const fresh = window._guard(mk(65));
      window._ageComposing(31000);        // 假裝是 31 秒前開始組字的
      const stale = window._guard(mk(65));
      window._setComposing(false);
      return { fresh, stale };
    }""")
    check("L 組字中（新鮮）→ 攔", res["fresh"] is False)
    check("L2 狀態卡住超過 30 秒 → 放行（鍵盤不會被永久吃掉）", res["stale"] is True)

    # H — 擋掉的要留下足跡，而且理由是 commit-dup（不是時間窗口）
    page.evaluate("window.reset()")
    page.evaluate("() => window.typeCJK('你', true)")
    dropped = page.evaluate("window.DROPPED")
    check("H 擋掉時留 ime-dup 足跡且理由正確",
          len(dropped) == 1 and dropped[0].get("why") == "commit-dup",
          f"dropped={json.dumps(dropped, ensure_ascii=False)}")

    browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
