#!/usr/bin/env python3
"""終端逸出序列：應用程式的要求不能破壞既有的取捨（v0.35.17）。

回報（Windows + Codex）：游標一直閃、畫面很卡、啟動時輸入框出現
`0;rgb:a9a9/b1b1/d6d6…11;rgb:1a1a/1b1b/2626`、Shift+Enter 換不了行。

用真的 xterm.js 5.5.0 量到的：

  1. `CSI Ps SP q`（DECSCUSR，奇數＝閃爍）與 `CSI ? 12 h` 都會把 cursorBlink
     從 false 翻成 true。Codex 的二進位裡有 `crossterm::cursor::SetCursorStyle`
     ——所以關掉 cursorBlink 這個刻意的取捨（閃爍游標每秒重繪兩次）被它蓋掉了。
  2. OSC 10/11 查詢的回覆是走 onData 出去的，也就是被當成使用者輸入寫進 PTY。
     回報看到的那串就是它，數字正是 ShellFrame 自己的前景 #a9b1d6 與背景 #1a1b26。
  3. Shift+Enter 在 xterm 一律送 `\\r`，跟純 Enter 一樣，開了 modifyOtherKeys
     也一樣——終端表達不出「帶 Shift 的 Enter」，位元組只能由 ShellFrame 合成。

需要 playwright；沒裝時前端那半 SKIP，純文字的檢查照跑。

跑法：python3 tests_terminal_escapes.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
html = (HERE / "web/index.html").read_text(encoding="utf-8")
main_src = (HERE / "main.py").read_text(encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── 原始碼層：設定與分派 ─────────────────────────────────────────────────────
check("三處建 Terminal 共用同一份 options",
      html.count("new Terminal(termOptions())") == 3,
      f"只有 {html.count('new Terminal(termOptions())')} 處")
opts = html.split("function termOptions(extra) {")[1].split("\n  }\n")[0]
check("Windows 上告知 xterm 後端是 ConPTY",
      "windowsPty" in opts and "conpty" in opts, opts)
check("非 Windows 不設 windowsPty（那是 ConPTY 專用的處理）",
      "if (IS_WIN)" in opts, opts)
check("每個 term 都掛上游標與顏色查詢的防護",
      html.count("keepCursorSteady(term);") == 3
      and html.count("swallowColorQueries(term);") == 3)

check("配色改用 COLORFGBG 帶外告知", 'COLORFGBG' in main_src)
env_fn = main_src.split("def _session_env() -> dict:")[1].split("\ndef ")[0]
check("COLORFGBG 指的是深背景（15;0）", '"15;0"' in env_fn, env_fn[-300:])
check("使用者自己設過就不覆蓋", "setdefault" in env_fn, env_fn[-300:])

# Shift+Enter 的分派
se = html.split("if (ev.key === 'Enter' && ev.shiftKey")[1].split("}")[0]
check("Codex 用 modifyOtherKeys 的編碼，其他維持 \\n",
      "isCodexSession(sid)" in se and "\\x1b[27;2;13~" in se and "'\\n'" in se, se)

# codex wrapper 判斷
codex_fn = html.split("function isCodexSession(sid) {")[1].split("\n  }\n")[0]
check("isCodexSession 認得 sf-codex 這種 wrapper",
      "sf-" in codex_fn, codex_fn)


def _is_codex(cmd):
    """跟前端同一條規則，用來驗它到底認得哪些指令。"""
    for tok in cmd.split():
        base = re.split(r"[\\/]", tok)[-1]
        base = re.sub(r"\.\w+$", "", base).lower()
        if base == "codex" or re.match(r"^sf-(.+-)?codex(-.+)?$", base):
            return True
    return False


for cmd, want in [("codex", True), ("sf-codex --search", True),
                  ("/usr/local/bin/codex", True), ("codex.exe", True),
                  ("sf-codex-spark", True), ("claude --resume x", False),
                  ("bash", False), ("my-codex-notes", False)]:
    check(f"指令判斷：{cmd!r} → {want}", _is_codex(cmd) is want)

# ── 前端行為：真的 xterm ────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("_TERMESC_QA_REEXEC") != "1":
        env = dict(os.environ, _TERMESC_QA_REEXEC="1")
        sys.exit(subprocess.call(["python3", str(pathlib.Path(__file__).resolve())], env=env))
    print("SKIP  tests_terminal_escapes.py 的前端那半（沒裝 playwright）")
    print(f"\nResults: {passed} passed, {failed} failed")
    print("ALL PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)


def grab(pat):
    m = re.search(pat, html, re.S)
    assert m, pat
    return m.group(0)


KEEP = grab(r"  function keepCursorSteady\(term\) \{.*?\n  \}\n")
SWALLOW = grab(r"  function swallowColorQueries\(term\) \{.*?\n  \}\n")

PAGE = """<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<div id="t" style="width:900px;height:300px"></div>
<script>
window.out = [];
const term = new Terminal({ cursorBlink: false, allowProposedApi: true,
  theme: { foreground: '#a9b1d6', background: '#1a1b26' } });
term.open(document.getElementById('t'));
term.onData(d => window.out.push(d));
__KEEP__
__SWALLOW__
keepCursorSteady(term);
swallowColorQueries(term);
window.ready = true;
window.feed = (s) => new Promise(r => term.write(s, r));
window.state = () => ({ blink: term.options.cursorBlink, style: term.options.cursorStyle });
window.pressShiftEnter = () => {
  window.out = [];
  const ta = document.querySelector('.xterm-helper-textarea');
  ta.focus();
  ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter',
    keyCode: 13, which: 13, shiftKey: true, bubbles: true, cancelable: true }));
  return window.out.slice();
};
</script>"""
PAGE = PAGE.replace("__KEEP__", KEEP).replace("__SWALLOW__", SWALLOW)

tmp = HERE / ".termesc_qa.html"
tmp.write_text(PAGE, encoding="utf-8")
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(tmp.resolve().as_uri())
        page.wait_for_function("window.ready === true", timeout=15000)

        check("起始不閃", page.evaluate("window.state()")["blink"] is False)
        for seq, shape, label in [("\\x1b[5 q", "bar", "閃爍豎線"),
                                  ("\\x1b[3 q", "underline", "閃爍底線"),
                                  ("\\x1b[1 q", "block", "閃爍方塊")]:
            page.evaluate(f"() => window.feed('{seq}')")
            page.wait_for_timeout(120)
            st = page.evaluate("window.state()")
            check(f"應用程式要求「{label}」→ 仍不閃，形狀照給",
                  st["blink"] is False and st["style"] == shape, str(st))
        page.evaluate("() => window.feed('\\x1b[?12h')")
        page.wait_for_timeout(120)
        check("CSI ?12h 開閃爍也擋掉",
              page.evaluate("window.state()")["blink"] is False)
        page.evaluate("() => window.feed('\\x1b[6 q')")
        page.wait_for_timeout(120)
        st = page.evaluate("window.state()")
        check("本來就不閃的形狀請求照常生效",
              st["style"] == "bar" and st["blink"] is False, str(st))

        page.evaluate("() => { window.out = []; }")
        page.evaluate(r"() => window.feed('\x1b]10;?\x1b\\\x1b]11;?\x1b\\\x1b]12;?\x1b\\')")
        page.wait_for_timeout(250)
        out = page.evaluate("window.out")
        check("顏色查詢不再產生任何輸入（就是那串亂碼）", out == [], json.dumps(out))
        check("回覆裡的顏色本來就是 ShellFrame 自己的（確認來源判斷無誤）",
              "a9a9/b1b1/d6d6" not in json.dumps(out))

        # 「真的設色」的請求不能被一起吞掉。這件事沒辦法從 options 觀察——實測
        # 就算完全不掛 handler，xterm 收到 OSC 11 設色也不會去改
        # options.theme.background。所以改驗兩件看得到的事：設色不會變成輸入
        # （跟查詢一樣），而且 handler 的判斷確實只綁在 payload 是不是 "?"。
        page.evaluate("() => { window.out = []; }")
        page.evaluate(r"() => window.feed('\x1b]11;#102030\x1b\\')")
        page.wait_for_timeout(200)
        check("設色請求不會變成輸入", page.evaluate("window.out") == [])
        check("只吞查詢：判斷綁在 payload 是不是 \"?\"",
              "=== '?'" in SWALLOW, SWALLOW)

        check("xterm 自己對 Shift+Enter 只送 \\r（所以一定要攔）",
              page.evaluate("() => window.pressShiftEnter()") == ["\r"])
        browser.close()
finally:
    tmp.unlink(missing_ok=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
