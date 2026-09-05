#!/usr/bin/env python3
"""遠端分頁上滑歷史的實際接線（v0.35.2）。

回報「remote 連線的部分無法上滑看歷史」。壞在兩層：連線上沒有 history 端點，
而遠端 pane 從來沒掛滾輪監聽——只修一層，看到的還是「上滑沒反應」。後端那半
可以直接對執行中的 app 打 `history` 指令驗，前端這半不行（要有配對的 peer），
所以這裡把 index.html 裡的真函式挖出來，餵真的 xterm 與真的 wheel 事件，
確認 rmt: 分頁會走遠端端點、而且回傳形狀真的餵得進 overlay。

需要 playwright；沒裝就 SKIP。

跑法：python3 tests_remote_scroll_history.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_RMT_HIST_QA_REEXEC") != "1":
        env = dict(os.environ, _RMT_HIST_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_remote_scroll_history.py（沒裝 playwright）\nALL PASS")
    sys.exit(0)

html = (HERE / "web/index.html").read_text(encoding="utf-8")


def grab(pattern):
    m = re.search(pattern, html, re.S)
    assert m, f"index.html 找不到：{pattern}"
    return m.group(0)


# 真正上場的四支，原封不動搬過來——改了實作這裡就會跟著變
IS_REMOTE = grab(r"  function isRemoteSid\(sid\) \{[^\n]*\n")
PARSE_REMOTE = grab(r"  function parseRemoteSid\(sid\) \{.*?\n  \}\n")
FETCH = grab(r"  async function fetchHistory\(sid, cols\) \{.*?\n  \}\n")
SETUP = grab(r"  function setupScrollHistory\(sid, pane\) \{.*?\n  \}\n")

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<style>body{margin:0;background:#16161e}.pane{position:relative;width:900px;height:300px}</style>
<script>
window.__calls = [];       // 打了哪支 API
window.__shown = null;     // overlay 收到什麼
const sessions = {};
const ScrollHistory = {
  isOpen: () => false,
  show: (text, sid, opts) => { window.__shown = { text, sid, opts }; },
};
const pywebview = { api: {
  link_remote_history: (peerId, rsid, cols) => {
    window.__calls.push(['link_remote_history', peerId, rsid, cols]);
    return Promise.resolve(JSON.stringify({
      success: true,
      details: { text: 'REMOTE-HISTORY\\nline2', source: 'tmux (normal)', ansi: true },
    }));
  },
  link_remote_history_fail: null,
  get_clean_history: (sid, lines, ansi, cols) => {
    window.__calls.push(['get_clean_history', sid, lines, ansi, cols]);
    return Promise.resolve(JSON.stringify({
      success: true, text: 'LOCAL-HISTORY', source: 'pyte', ansi: true,
    }));
  },
}};
__IS_REMOTE__
__PARSE_REMOTE__
__FETCH__
__SETUP__
window.ready = true;
// 每個分頁一塊自己的 pane：共用一塊的話上一輪掛的監聽還在，本機分頁會連帶
// 觸發遠端那條，測出來的「沒打錯端點」就是假的。
let _pane = null;
window.mount = function (sid) {
  if (_pane) _pane.remove();
  _pane = document.createElement('div');
  _pane.className = 'pane';
  document.body.appendChild(_pane);
  const term = new Terminal({ fontSize: 14 });
  term.open(_pane);
  sessions[sid] = { term };
  window.__calls = []; window.__shown = null;
  setupScrollHistory(sid, _pane);
};
window.wheelUp = function () {
  _pane.dispatchEvent(
    new WheelEvent('wheel', { deltaY: -100, bubbles: true, cancelable: true }));
};
</script>"""
PAGE = (PAGE.replace("__IS_REMOTE__", IS_REMOTE).replace("__PARSE_REMOTE__", PARSE_REMOTE)
            .replace("__FETCH__", FETCH).replace("__SETUP__", SETUP))

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


tmp = HERE / ".rmt_hist_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 400})
        page.goto(tmp.resolve().as_uri())
        page.wait_for_function("window.ready === true", timeout=15000)

        # ── 遠端分頁 ──
        page.evaluate("window.mount('rmt:peer7:s42')")
        page.evaluate("window.wheelUp()")
        page.wait_for_function("window.__shown !== null", timeout=5000)
        calls = page.evaluate("window.__calls")
        shown = page.evaluate("window.__shown")

        check("遠端分頁上滑會呼叫 link_remote_history",
              any(c[0] == "link_remote_history" for c in calls), json.dumps(calls))
        check("peer 與遠端 sid 有正確拆出來",
              calls and calls[0][1] == "peer7" and calls[0][2] == "s42", json.dumps(calls))
        check("欄寬有帶過去（表格要照觀看端的寬度排）",
              bool(calls and isinstance(calls[0][3], int) and calls[0][3] > 0), json.dumps(calls))
        check("不會誤打本機的 get_clean_history",
              all(c[0] != "get_clean_history" for c in calls), json.dumps(calls))
        check("overlay 真的收到遠端內容",
              shown and shown["text"].startswith("REMOTE-HISTORY"), json.dumps(shown))
        check("source／ansi 一路帶到 overlay",
              shown and shown["opts"]["source"] == "tmux (normal)" and shown["opts"]["ansi"] is True,
              json.dumps(shown))

        # ── 對方是舊版（沒有 /link/history）──
        page.evaluate("""() => {
          pywebview.api.link_remote_history = () => Promise.resolve(
            JSON.stringify({ success: false, message: 'unknown path' }));
        }""")
        page.evaluate("window.mount('rmt:peer7:s42')")
        page.evaluate("window.wheelUp()")
        page.wait_for_function("window.__shown !== null", timeout=5000)
        shown = page.evaluate("window.__shown")
        check("對方舊版時 overlay 照開，說得出原因（不是靜默沒反應）",
              shown and "取不到遠端歷史" in shown["text"] and "0.35.2" in shown["text"],
              json.dumps(shown))

        # ── 本機分頁不受影響 ──
        page.evaluate("window.mount('s9')")
        page.evaluate("window.wheelUp()")
        page.wait_for_function("window.__shown !== null", timeout=5000)
        calls = page.evaluate("window.__calls")
        shown = page.evaluate("window.__shown")
        check("本機分頁仍走 get_clean_history",
              any(c[0] == "get_clean_history" for c in calls), json.dumps(calls))
        check("本機分頁不會打遠端端點",
              all(c[0] != "link_remote_history" for c in calls), json.dumps(calls))
        check("本機 overlay 內容正確",
              shown and shown["text"] == "LOCAL-HISTORY", json.dumps(shown))

        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
