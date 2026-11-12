#!/usr/bin/env python3
"""The sidebar's second line and the primary bot's card, in the real page, for the messages a tab is sent.

A row's second line (and the primary bot's card) says the tab's latest line; for a tab that is not running that
is the person's last message, and a message another agent, Telegram or a paste sent arrived with the wrapper
ShellBot types around it: the card read "[[A2A：來自「研究」的訊息，不是使用者本人。請自行判斷是否採納…", or
"最終要回 Telegram 的文字請放在 [[TGREPLYab12cd34]] 和 …" (the markdown strip ate the underscores), and the
search matched the words of the wrapper. The statuses here are what api_status.py really pushes to the page
(state, summary, task: no narration, no action), made by the real agent_status._detail from the message
shapes measured on a real tab (_paste_shapes.py).

  - another agent's message: "研究：比較表整理好了…"; one the chain limit stopped "排程（未轉達）：…"
  - a Telegram message: the person's words; something pasted: "貼上的內容 · 15 行"; plain words as they are
  - a running tab still says what it runs; a tab with nothing to say says its bot's job
  - the same lines on the primary bot's card, and the search matches the words, not the wrapper
  - a text that still carries the wrapper (a status from a computer on an older version, here a raw
    header as `task`) never shows it: the page runs the chat view's rule on what it is sent (chat.js preview)
  - English: "research: …", "Pasted · 15 lines", "(not passed on)"
  - the tab bar holds the tabs' names and nothing of a message

Chromium, and WebKit (the Mac app's engine) when playwright has it; the screenshot is WebKit's.
Screenshots: qa-shots/preview-sidebar-{webkit,en-webkit}.png (chromium's when there is no WebKit).
SF_PREVIEW_BEFORE=<git ref> draws the same with that revision's agent_status.py and web/bots.js and saves
…-before-….png, without checks. Demo words only. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_msg_preview_ui.py
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.path.insert(0, str(HERE))
import agent_link  # noqa: E402
import agent_status  # noqa: E402
import _paste_shapes as ps  # noqa: E402

passed = failed = 0
BEFORE = os.environ.get("SF_PREVIEW_BEFORE", "")


def check(name, ok, detail=""):
    global passed, failed
    if BEFORE:
        return
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_msg_preview_ui.py（沒裝 playwright）")
    sys.exit(0)


def git_show(path):
    return subprocess.run(["git", "show", f"{BEFORE}:{path}"], cwd=HERE, capture_output=True, text=True, check=True).stdout


STATUS_MOD = agent_status
OLD_BOTS = ""
if BEFORE:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sf-preview-before-")) / "agent_status_before.py"
    tmp.write_text(git_show("agent_status.py"), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("agent_status_before", tmp)
    STATUS_MOD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(STATUS_MOD)
    OLD_BOTS = git_show("web/bots.js")

NOW = time.time()
DOT = {"working": "sig-working", "done": "sig-done", "idle": "", "unknown": ""}


def row(events, state="done", ago=120):
    """What api_status.py pushes for a tab: state, dot, summary, task and the clock, from the transcript's events."""
    action, task, narration = STATUS_MOD._detail(events)
    return {"state": state, "dot": DOT[state], "summary": action or narration or state, "task": task,
            "elapsed": 0, "activity": {}, "loop": None, "model": None, "activity_at": NOW - ago}


class computer_language:
    """SHELLFRAME_PROMPT_LANG for a while: the language the server writes in (sf_lang), as the computer's."""
    def __init__(self, lang):
        self.lang = lang

    def __enter__(self):
        self.old = os.environ.get("SHELLFRAME_PROMPT_LANG")
        os.environ["SHELLFRAME_PROMPT_LANG"] = self.lang

    def __exit__(self, *exc):
        os.environ["SHELLFRAME_PROMPT_LANG"] = self.old or "zh"


def said(text):
    return [{"kind": "user_msg", "text": text}]


RAW_HEADER = agent_link.format_delivery("研究", "比較表整理好了", lang="zh").split("\n")[0]    # what an older version's `task` is
OWN_PICS = ps.SHAPES[3][1]
TABS = [  # sid, label, cmd, the status the page is sent, the line the row should say
    ("s301", "主控", "claude", row(said(ps.SHAPES[0][1])), "研究：比較表整理好了，第 1 行。 比較表整理好了，第 2 行。 比較表整理好了，第 3 行。"),
    ("s302", "週報整理", "claude", row(said(ps.SHAPES[2][1]), ago=600), None),
    ("s303", "文件翻譯", "codex", row(said(ps.SHAPES[6][1]), ago=900), "排程（未轉達）：比較表整理好了，第 1 行。 比較表整理好了，第 2 行。 比較表整理好了，第 3 行。"),
    ("s304", "手機傳來", "claude", row(said(ps.SHAPES[5][1]), ago=1500), "從手機傳的，第 1 行。 從手機傳的，第 2 行。 從手機傳的，第 3 行。 從手機傳的，第 4 行。 從手機傳的，第 5 行。"),
    ("s305", "貼上的筆記", "claude", row(said(OWN_PICS), ago=2400), "貼上的內容 · 15 行"),
    ("s306", "快速問答", "claude", row(said("幫我看一下這個 bug"), ago=3000), "幫我看一下這個 bug"),
    ("s307", "relay 除錯", "claude", row([{"kind": "user_msg", "text": "改一下 relay"}, {"kind": "tool_call", "tool": "Edit", "target": "relay.py"}],
                                       "working", ago=0), "Editing relay.py"),
    ("s308", "舊版電腦的分頁", "claude", dict(row([]), state="done", dot="sig-done", summary="done", task=RAW_HEADER), "研究"),
    ("s309", "Shell", "zsh", dict(row([]), state="unknown", dot="", summary="", task=""), ""),
]
ROSTER = {"主控": {"label": "主控", "responsibility": "盤點與派工", "cmd": "claude",
                   "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}},
          "研究": {"label": "研究", "responsibility": "資料調研", "cmd": "claude",
                   "bot": {"avatar": {"shape": "hex", "color": "blue"}, "memory": True}}}
STATE = {"enabled": True, "roster": ROSTER, "primary": "主控", "memory_root": "~/.config/shellframe/bots",
         "sessions": {"s301": {"bot": "主控"}}}
WRAPPERS = ["[[A2A", "pasted_content", "TG_REPLY", "TGREPLY", "[[SF:", "不是使用者本人", "請自行判斷是否採納", "最終要回 Telegram", "SF:TO"]


def stub(tabs, state, language):
    cfg = {"settings": {"experimental_bots": True, "language": language, "sidebar_open": True},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    sessions = [{"sid": sid, "cmd": cmd, "alive": True, "label": label, "bridge_enabled": True} for sid, label, cmd, *_ in tabs]
    status = {sid: st for sid, _, _, st, *_ in tabs}
    return """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__bots = %s; window.__status = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: '%s'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'bots_state': return R(window.__bots);
      default: return R('{}');
    }
  };
}})};
setInterval(() => { try { window.__sfAgentStatus && window.__sfAgentStatus(window.__status); } catch (e) {} }, 400);
""" % (json.dumps(cfg, ensure_ascii=False), json.dumps(sessions, ensure_ascii=False), json.dumps(state, ensure_ascii=False),
       json.dumps(status, ensure_ascii=False), tabs[0][0])


ROWS = """() => Object.fromEntries([...document.querySelectorAll('#sidebar-sessions .sb-item[data-section="local"][data-sid]')]
  .map(r => [r.dataset.sid, ((r.querySelector('.sfb-pv') || {}).textContent || '').replace(/\\u00a0/g, '')]))"""
VISIBLE = """() => [...document.querySelectorAll('#sidebar-sessions .sb-item[data-section="local"][data-sid]')]
  .filter(r => !r.classList.contains('sfb-hide')).map(r => r.dataset.sid)"""


def open_page(browser, tabs, state, language):
    ctx = browser.new_context(viewport={"width": 1180, "height": 1000}, device_scale_factor=2)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    if BEFORE:
        page.route("**/bots.js", lambda route: route.fulfill(status=200, content_type="text/javascript", body=OLD_BOTS))
    page.add_init_script(stub(tabs, state, language))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(f"document.querySelectorAll('#sidebar-sessions .sb-item[data-sid] .sfb-pv').length >= {len(tabs)}", timeout=30000)
    page.wait_for_timeout(1200)           # the status push, then the rows' own tick
    return ctx, page


def shot(page, name, engine):
    SHOTS.mkdir(exist_ok=True)
    page.locator("#sidebar").screenshot(path=str(SHOTS / f"preview-{name}{'-before' if BEFORE else ''}-{engine}.png"))


def run(browser, engine, shoot):
    print(f"[{engine}] the sidebar and the primary card, in Chinese")
    ctx, page = open_page(browser, TABS, STATE, "zh-TW")
    rows = page.evaluate(ROWS)
    for sid, label, _, _, want in TABS:
        if want is None:
            continue
        check(f"{label}: {want!r}", rows.get(sid) == want, rows.get(sid))
    long_a2a = rows.get("s302", "")
    check("週報整理 (a 31-line message from another agent): the sender first, cut with …, at most 90 characters",
          long_a2a.startswith("排程：週報草稿，第 1 行。") and long_a2a.endswith("…") and len(long_a2a) <= 90, long_a2a)
    check("no row shows any of a wrapper", not [r for r in rows.values() if any(w in r for w in WRAPPERS)], rows)
    card = page.evaluate("(document.querySelector('#sfb-primary .sfb-card-sub') || {}).textContent")
    check("the primary bot's card says the same as its row", card == TABS[0][4], card)
    check("no wrapper on the card either", not any(w in (card or "") for w in WRAPPERS), card)
    search = lambda text: (page.fill("#sfb-q", text), page.wait_for_timeout(200), page.evaluate(VISIBLE))[2]
    check("the search finds the words of a message: 比較表 is on the A2A row and the stopped one", search("比較表") == ["s301", "s303"], search("比較表"))
    check("…and not the words of the wrapper (不是使用者本人)", search("不是使用者本人") == [], search("不是使用者本人"))
    check("…nor the Telegram instruction (最終要回)", search("最終要回") == [], search("最終要回"))
    check("…the person's own words from Telegram are found: 從手機", search("從手機") == ["s304"], search("從手機"))
    page.press("#sfb-q", "Escape")
    page.wait_for_timeout(200)
    tabbar = page.evaluate("[...document.querySelectorAll('#tabs .tab[data-sid] .label')].map(l => l.textContent.trim())")
    check("the tab bar holds the tabs' names and nothing of a message", tabbar == [t[1] for t in TABS], tabbar)
    check("no page errors", not page.errors, page.errors)
    if shoot:
        shot(page, "sidebar", engine)
    ctx.close()

    print(f"[{engine}] English")
    with computer_language("en"):                        # the server writes the words it adds in the computer's language
        en = [("s401", "Lead", "claude", row(said(agent_link.format_delivery("research", "the comparison table is done", lang="en"))),
               "research: the comparison table is done"),
              ("s402", "Docs", "codex", row(said(agent_link.format_blocked("scheduler", "the table is done", agent_link.MAX_DEPTH + 1, lang="en"))),
               "scheduler (not passed on): the table is done"),
              ("s403", "Notes", "claude", row(said(OWN_PICS)), "Pasted · 15 lines"),
              ("s404", "Phone", "claude", row(said(ps.TG_ASK + "\n\nis the weekly report done?")), "is the weekly report done?"),
              ("s405", "Quick", "claude", row(said("look at this bug")), "look at this bug")]
    ctx, page = open_page(browser, en, dict(STATE, roster={}, sessions={}, primary=""), "en")
    rows = page.evaluate(ROWS)
    for sid, label, _, _, want in en:
        check(f"{label}: {want!r}", rows.get(sid) == want, rows.get(sid))
    check("no wrapper in English either", not [r for r in rows.values() if any(w in r for w in WRAPPERS)], rows)
    check("no page errors in English", not page.errors, page.errors)
    if shoot:
        shot(page, "sidebar-en", engine)
    ctx.close()


with sync_playwright() as pw:
    SHOTS.mkdir(exist_ok=True)
    first = True
    for engine in ("webkit", "chromium"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:                       # webkit is optional: `playwright install webkit`
            print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
            continue
        try:
            run(browser, engine, shoot=first)        # one set of screenshots: the first engine that runs
            first = False
        finally:
            browser.close()

if BEFORE:
    print("before shots saved")
    sys.exit(0)
print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
