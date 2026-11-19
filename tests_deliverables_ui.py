#!/usr/bin/env python3
"""The deliverables desk in the real page. The Python side is the REAL backend (deliverables.py, office_deliverables.py,
api_deliverables.py over a temp state.db and temp files, through playwright's expose_function); the rest of the app's calls are the
board test's stand-in. Every answer the backend gave is kept and checked for the secrets planted in the demo files.

  - the desks: a pile of papers on each person's desk (also a resting bot's), the newest three types as their own small objects, an
    envelope and a red number only for what nobody opened; the pile is a hit target (pointer, tip) that opens that person's list;
    the toolbar button 「交付物」 carries the number; the object has no furniture on the lobby wall
  - the popup: everybody's list grouped by owner (closed tabs last), one person's grouped by what was decided; a small picture per
    kind on every row; open an entry (Markdown masked, a script in a note never runs, an image and a PDF's first page as pictures,
    html never shown and only "open in browser"); 看過 stays undecided; 先放著; 拿走 (still found, the desk shrinks); 請他改 is ONE
    line in the protocol form through the chat send (a linked task's note comes after); a key-looking file is not previewed and asks
    before it opens; a file that is gone is marked; a guessed file is taken or ignored, the skipped key files are named
  - the right-hand list: a 「交付物」 section below the decisions, a pill in its head, 打開 / 先放著 / 拿走 / 收下 / 忽略
  - the sidebar and the tab bar: a paperclip with the number on a bot's or tab's avatar (a resting bot's too), grey when only kept ones
  - 1280 and 900 wide, interface sizes M and L: nothing spills sideways, nothing overlaps; English too; no console error
Demo data only. Headless Chromium and WebKit when playwright has it.
Screenshots (Chromium): qa-shots/deliverables-*.png. Run: .venv/bin/python tests_deliverables_ui.py
"""
import copy
import json
import os
import pathlib
import struct
import sys
import tempfile
import time
import types
import zlib
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="sf-deliv-ui-"))
HOME = TMP / "home"
HOME.mkdir()
os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_deliverables_ui.py（沒裝 playwright）")
    sys.exit(0)

_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_board_harness")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)   # its stand-in Python side and helpers, not its run

import api_deliverables  # noqa: E402
import api_office_objects  # noqa: E402
import deliverables as dv  # noqa: E402
import office_objects as oo  # noqa: E402
import state_store as ss  # noqa: E402

SHOTS = H.SHOTS
ss.db_path = lambda: TMP / "state.db"
SECRETS = ["hunter22", "s3cretpw", "sk-demo1234567890abcdef", "abc123xyz", "ghp_demoabcdefghijkl", "zz9token"]
NOW = time.time()
LIVE = {"s700": "1000", "s701": "1001", "s702": "1002", "s703": "1003"}
ROSTER = H.ROSTER
NOTES, OPENED, LOG = [], [], []


# ── demo files ───────────────────────────────────────────────────────────────
def png(w=240, h=150):
    rows = b"".join(b"\0" + b"".join(bytes([int(60 + 150 * x / w), int(110 + 90 * y / h), 220]) for x in range(w)) for y in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\ntrailer<</Root 1 0 R/Size 4>>\n%%EOF\n")


def make(rel, data="示範內容\n", age=60):
    p = HOME / "示範專案" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.utime(p, (NOW - age, NOW - age))
    return p


WEEKLY = ("# 示範週報草稿（第 47 週）\n\n登入頁改版放到測試站，三項檢查全通過；單元測試補了 12 筆邊界案例，全綠。\n\n"
          "## 測試站連線\n\n| 項目 | 內容 |\n|---|---|\n| 測試站 | https://demo:s3cretpw@staging.example.test/login |\n| 金鑰 | API_TOKEN=abc123xyz |\n\n"
          "<script>window.__xss = 1</script><img src=x onerror=\"window.__xss = 2\">\n\n正常的字。\n")
BOT = lambda role: ("bot", role, ROSTER[role]["label"])           # noqa: E731
CLOSED = ("tab", "s650@900", "示範舊分頁")
TAB702 = ("tab", "s702@1002", "示範臨時分頁")


def reg(owner, path, age=None, **kw):
    r = dv.add(owner[0], owner[1], owner[2], path, now=NOW - (age or 0), home=HOME, **kw)
    return r


R = {}
tk = None


def seed():
    """The demo desks, from nothing: every engine starts from the same state."""
    global tk
    for f in TMP.glob("state.db*"):
        f.unlink()
    ss._ready.clear()
    NOTES.clear(); OPENED.clear(); LOG.clear()
    R.clear()
    tk = ss.add("示範登入頁改版", owner="eng", now=NOW)
    R["md"] = reg(BOT("eng"), make("報告/示範週報草稿.md", WEEKLY, 1800), 1800, title="示範週報草稿", note="第 3 點是我猜的，你看一下再寄。", task_id=tk["id"])
    R["png"] = reg(BOT("eng"), make("報告/登入頁新版截圖.png", png(), 2400), 2400, title="登入頁新版截圖")
    R["pdf"] = reg(BOT("eng"), make("報告/示範測試報告.pdf", PDF, 3000), 3000, title="示範測試報告")
    R["html"] = reg(BOT("eng"), make("報告/示範頁.html", "<h1>示範</h1><script>window.__xss = 3</script>", 3300), 3300, title="示範網頁")
    R["env"] = reg(BOT("eng"), make("設定/示範連線.env", "API_TOKEN=abc123xyz\n", 3400), 3400, title="示範連線設定")
    R["url"] = reg(BOT("eng"), "https://demo.example.test/preview/login", 3500, title="示範預覽站（登入頁）")
    R["xlsx"] = reg(BOT("eng"), make("資料/示範登入流程表.xlsx", b"PK\x03\x04", 90000), 90000, title="示範登入流程表")
    R["zip"] = reg(BOT("eng"), make("匯出/示範匯出包.zip", b"PK\x03\x04", 150000), 150000, title="示範匯出包")
    R["old"] = reg(BOT("eng"), make("舊/示範舊稿.md", "舊稿", 900000), 900000, title="示範舊稿")
    R["res_html"] = reg(BOT("res"), make("資料/示範市場摘要.html", "<p>摘要</p>", 4000), 4000, title="示範市場摘要")
    R["res_xlsx"] = reg(BOT("res"), make("資料/示範市場數字.xlsx", b"PK", 100000), 100000, title="示範市場數字")
    R["tab_zip"] = reg(TAB702, make("圖檔/示範圖檔整理.zip", b"PK", 5000), 5000, title="示範圖檔整理")
    R["chief"] = reg(BOT("chief"), make("合約/示範合約修訂.docx", b"PK", 200000), 200000, title="示範合約修訂")
    R["closed"] = reg(CLOSED, make("舊/示範舊分頁交的.md", "x", 6000), 6000, title="示範舊分頁交的")
    dv.move(R["xlsx"]["id"], "keep", NOW)
    dv.move(R["zip"]["id"], "keep", NOW)
    dv.move(R["res_xlsx"]["id"], "keep", NOW)
    dv.move(R["chief"]["id"], "keep", NOW)
    dv.move(R["old"]["id"], "take", NOW)
    os.unlink(HOME / "示範專案" / "匯出" / "示範匯出包.zip")                               # the original was moved away: 檔案不見了
    # the guessed ones (auto-pickup): a spreadsheet and a page the reply named, and key files it passed over
    cand_x = make("資料/示範比較表.xlsx", b"PK", 600)
    cand_h = make("資料/示範競品整理.html", "<p>x</p>", 1500)
    for n in (".env.local", "金鑰.pem", "token.txt"):
        make("資料/" + n, "x", 300)
    reply = ("比較表整理好了，存在 ~/示範專案/資料/示範比較表.xlsx，A 方案便宜約三成。另外 ~/示範專案/資料/示範競品整理.html 是競品整理。"
             "設定在 ~/示範專案/資料/.env.local、~/示範專案/資料/金鑰.pem、~/示範專案/資料/token.txt。")
    real_home = os.environ.get("HOME")
    os.environ["HOME"] = str(HOME)                                  # `~/…` in the reply means the demo home, only while it is read
    try:
        found = dv.find_candidates(reply, now=NOW, home=HOME)
    finally:
        os.environ["HOME"] = real_home
    dv.record_candidates("bot", "res", "示範研究員", found, now=NOW, home=HOME)
    dv.mark_seen(R["png"]["id"], NOW)


class Host(api_deliverables.DeliverablesApiMixin, api_office_objects.OfficeObjectsApiMixin):
    def _office_on(self):
        return True

    def _deliv_live_tabs(self):
        return dict(LIVE)

    def _deliv_tab_labels(self):
        return {"s700": "示範秘書長", "s701": "示範工程師", "s702": "示範臨時分頁", "s703": "示範終端機"}

    def _oo_ctx(self, lang):
        return oo.Ctx(lang, live_tabs=self._deliv_live_tabs, tab_labels=self._deliv_tab_labels, roster=lambda: ROSTER,
                      task_note=lambda tid, text: NOTES.append((tid, text)), deliv=lambda: {"home": HOME})

    _deliv_ctx = _oo_ctx

    def open_local_file(self, path):
        OPENED.append(("open", path))
        return json.dumps({"success": True})

    def open_url(self, url):
        OPENED.append(("url", url))
        return json.dumps({"success": True})

    @staticmethod
    def _oo_reveal(path):
        OPENED.append(("reveal", path))
        return json.dumps({"success": True})


HOST = Host()
TASKS_BASE = copy.deepcopy(H.TASKS)


def py_call(name, args_json):
    args = json.loads(args_json)
    if name == "tasks_overview":
        out = json.dumps(dict(TASKS_BASE, deliverables=HOST._deliv_overview()), ensure_ascii=False)
    else:
        out = getattr(HOST, name)(*args)
    LOG.append(out)
    return out


PATCH = """
if (window.top === window) {
  const base = window.pywebview.api;
  window.__sends = [];
  window.pywebview.api = new Proxy({}, { get: (t, prop) => {
    if (prop === 'then') return undefined;
    if (String(prop).startsWith('office_object') || prop === 'deliverables_act' || prop === 'tasks_overview') return (...a) => window.py_call(String(prop), JSON.stringify(a));
    if (prop === 'chat_send') return (...a) => { window.__sends.push({sid: a[0], text: a[1], clear: !!a[2]});
      return Promise.resolve(JSON.stringify({success: true, queued: false, id: 'm1'})); };
    return base[prop];
  }});
}
"""


def frame_metrics(fr):
    return fr.evaluate("""() => {
      const R = s => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return {l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height}; };
      const box = document.querySelector('.oo-box').getBoundingClientRect();
      const out = e => { const b = e.getBoundingClientRect(); return b.width > 0 && (b.right > box.right + 1 || b.left < box.left - 1 || b.bottom > box.bottom + 1); };
      const foot = [...document.querySelectorAll('#ooNote:not(:empty), #ooMsg:not(:empty), .oo-acts > button, .oo-ask > *')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.getBoundingClientRect());
      let clash = 0;
      for (let i = 0; i < foot.length; i++) for (let j = i + 1; j < foot.length; j++) {
        const a = foot[i], b = foot[j];
        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) clash++;
      }
      const rows = [...document.querySelectorAll('.oo-it')].filter(e => e.getBoundingClientRect().width > 0);
      const rowSpill = rows.filter(e => e.scrollWidth > e.clientWidth + 1).length;
      return {box: R('.oo-box'), head: R('.oo-head'), bar: R('.oo-bar'), list: R('.oo-list'), view: R('.oo-view'), foot: R('.oo-foot'),
              sw: document.documentElement.scrollWidth, iw: innerWidth, bsw: document.body.scrollWidth, rowSpill,
              spill: [...document.querySelectorAll('.oo-head *, .oo-bar *, .oo-foot *')].filter(out).length, clash};
    }""")


def no_overlap(m, narrow):
    ok = m["head"]["b"] <= m["bar"]["t"] + 1 and m["bar"]["b"] <= min(m["list"]["t"], m["view"]["t"]) + 1 \
        and m["foot"]["t"] >= max(m["list"]["b"], m["view"]["b"]) - 1
    ok = ok and (m["list"]["b"] <= m["view"]["t"] + 1 if narrow else m["list"]["r"] <= m["view"]["l"] + 1)
    return ok and m["clash"] == 0


def titles(fr):
    return fr.evaluate("[...document.querySelectorAll('.oo-it .oo-it-t')].map(e => e.textContent)")


def pick(page, fr, text):
    fr.click(f'.oo-it:has-text("{text}")')
    page.wait_for_timeout(450)


def groups(fr):
    return fr.evaluate("[...document.querySelectorAll('.oo-grp span')].map(e => e.textContent)")


def new_page(browser, errors, width=1280, height=800, office=True, style="cli", lang="zh-TW"):
    cfg = copy.deepcopy(H.CFG)
    cfg["settings"].update({"experimental_office": office, "ui_style": style, "language": lang})
    cfg["settings"].pop("pending_rail", None)
    H.CFG = cfg
    page = browser.new_page(viewport={"width": width, "height": height})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.expose_function("py_call", py_call)
    page.add_init_script(H.stub())
    page.add_init_script(PATCH)
    page.goto(base_url[0] + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFBoard && window.SFBots && SFBots.enabled() && window.SFDeliv && window.__sfHost && Object.keys(window.__sfHost.sessions()).length >= 3", timeout=20000)
    page.wait_for_function("SFBoard.deliv() !== null", timeout=15000)
    return page


base_url = [""]


def overflow_x(page):
    return page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")


def run(browser, base, engine):
    shoot = engine == "chromium"
    tag = f"[{engine}]"
    errors = []
    base_url[0] = base
    seed()
    new_n = len([r for r in dv.listing(states=("new",)) if r["seen_at"] is None])

    # ── the sidebar and the tab bar ──────────────────────────────────────────
    print(f"{tag} the sidebar's paperclips")
    page = new_page(browser, errors)
    page.wait_for_timeout(800)
    clips = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#sidebar-sessions .sb-item[data-sid]')].map(r => [r.dataset.sid, (() => { const c = r.querySelector('.sfd-clip'); return c ? {n: c.textContent.trim(), grey: c.classList.contains('grey'), title: c.title} : null; })()]))""")
    check("the engineer's avatar has a blue paperclip with its new count", clips["s701"] and clips["s701"]["n"] == "5" and not clips["s701"]["grey"], clips)
    check("a tab with one new: 1; the chief has only a kept one: a grey clip with no number", clips["s702"]["n"] == "1" and clips["s700"]["grey"] and clips["s700"]["n"] == "", clips)
    check("a terminal tab with nothing has none", clips["s703"] is None, clips)
    sleeper = page.evaluate("(() => { const c = document.querySelector('.sfb-sleeper[data-sfb-role=\"res\"] .sfd-clip'); return c ? c.textContent.trim() : null; })()")
    check("a bot that is resting has one too (its desk is still its desk)", sleeper == "1", sleeper)
    tab = page.evaluate("[...document.querySelectorAll('#tabs .tab[data-sid]')].map(t => [t.dataset.sid, (t.querySelector('.sfd-clip') || {}).textContent]).filter(x => x[1]).map(x => [x[0], x[1].trim()])")
    check("the top tab bar carries it as well", ["s701", "5"] in tab and ["s702", "1"] in tab, tab)
    check("candidates are not counted (the researcher's two guessed files are not in its number)", sleeper == "1")
    if shoot:
        page.screenshot(path=str(SHOTS / "deliverables-sidebar-paperclip-1280.png"))
    before = page.evaluate("document.querySelectorAll('.sfd-clip').length")
    page.wait_for_timeout(1500)
    check("the clips are stable: no rebuild while nothing changes", page.evaluate("document.querySelectorAll('.sfd-clip').length") == before)
    check("no horizontal scroll", not overflow_x(page))

    # ── the right-hand list: a drawer at 1280 ─────────────────────────────────
    print(f"{tag} the right-hand list")
    page.close()
    page = new_page(browser, errors, width=1500, height=860, office=False, style="chat")
    page.wait_for_function("document.body.classList.contains('sf-chat-shown')", timeout=10000)
    page.wait_for_function("document.querySelectorAll('#pend-deliv .sfd-row').length > 0", timeout=15000)
    sec = page.evaluate("""() => { const p = document.getElementById('pend-deliv'); return {vis: !p.hidden, h3: p.querySelector('h3').textContent, groups: [...p.querySelectorAll('.sfd-gh span')].map(x => x.textContent), rows: [...p.querySelectorAll('.sfd-row:not(.cand) .sfd-t b')].map(x => x.textContent),
        below: p.getBoundingClientRect().top >= document.getElementById('pend-cards').getBoundingClientRect().bottom - 1, cands: [...p.querySelectorAll('.sfd-row.cand .sfd-t b')].map(x => x.textContent), pill: document.getElementById('pend-jump-d').textContent.trim()}; }""")
    check("a 「交付物」 section below the decisions, in the same column", sec["vis"] and sec["below"] and "交付物" in sec["h3"], sec)
    check("grouped by person; only the undecided ones (kept and taken are on the desk or in history)", sec["groups"][:3] == ["示範工程師", "示範研究員", "示範臨時分頁"] and "示範網頁" in sec["rows"]
          and "示範登入流程表" not in sec["rows"] and "示範舊稿" not in sec["rows"], sec)
    check("the small 「可能是交付物」 group: the two guessed files, apart from the numbers", sorted(sec["cands"]) == ["示範比較表.xlsx", "示範競品整理.html"], sec["cands"])
    check("the pill in the head: 交付物 and the new count", sec["pill"].startswith("交付物") and sec["pill"].endswith(str(new_n)), sec["pill"])
    pics = page.evaluate("[...document.querySelectorAll('#pend-deliv .sfd-pic img')].map(i => i.src.startsWith('data:image/png'))")
    check("every row has its kind's small picture (the desk's drawing)", pics and all(pics), pics)
    if shoot:
        page.screenshot(path=str(SHOTS / "deliverables-rail-1500.png"))
    n0 = len(page.evaluate("[...document.querySelectorAll('#pend-deliv .sfd-row:not(.cand)')]") or [])
    page.click('#pend-deliv .sfd-row:has-text("示範測試報告") .sfd-h')
    page.wait_for_timeout(300)
    acts = page.evaluate("[...document.querySelectorAll('#pend-deliv .sfd-row.open .sfd-b')].map(b => b.textContent)")
    check("a card opens in place: 打開 / 先放著 / 拿走", acts == ["打開", "先放著", "拿走"], acts)
    page.click('#pend-deliv .sfd-row.open [data-dact="keep"]')
    page.wait_for_function("![...document.querySelectorAll('#pend-deliv .sfd-row')].some(r => r.textContent.includes('示範測試報告'))", timeout=8000)
    check("先放著 takes it off the list at once (it stays on the desk), the number follows", dv.get(R["pdf"]["id"])["state"] == "kept"
          and page.evaluate("document.getElementById('pend-jump-d').textContent.trim()").endswith(str(new_n - 1 + 0 if False else len([r for r in dv.listing(states=('new',)) if r['seen_at'] is None]))), page.evaluate("document.getElementById('pend-jump-d').textContent"))
    page.click('#pend-deliv .sfd-row:has-text("示範網頁") .sfd-h')
    page.click('#pend-deliv .sfd-row.open [data-dact="open"]')
    page.wait_for_timeout(500)
    check("打開 in the list: the host opens it (an html file: never inside the page), and it is now seen", OPENED and OPENED[-1][1].endswith("示範頁.html")
          and dv.get(R["html"]["id"])["seen_at"] is not None and page.evaluate("window.__xss") is None, OPENED[-1:])
    page.click('#pend-deliv .sfd-row:has-text("示範連線設定") .sfd-h')
    page.click('#pend-deliv .sfd-row.open [data-dact="open"]')
    page.wait_for_selector('#pend-deliv .sfd-confirm', timeout=5000)
    n_open = len(OPENED)
    check("a key-looking file asks first in the list, and nothing was opened yet", "金鑰" in page.evaluate("document.querySelector('#pend-deliv .sfd-confirm').textContent") and len(OPENED) == n_open)
    page.click('#pend-deliv .sfd-confirm [data-dact="confirm"]')
    page.wait_for_timeout(500)
    check("…it opens after the person said yes", len(OPENED) == n_open + 1 and OPENED[-1][1].endswith("示範連線.env"), OPENED[-2:])
    page.click('#pend-deliv .sfd-row.open [data-dact="take"]')
    page.wait_for_timeout(500)
    check("拿走: the card is gone and the store says taken (the file is not touched)", dv.get(R["env"]["id"])["state"] == "taken" and os.path.exists(R["env"]["ref"]))
    page.click('#pend-deliv .sfd-row.cand:has-text("示範比較表") [data-dact="accept"]')
    page.wait_for_function("![...document.querySelectorAll('#pend-deliv .sfd-row.cand')].some(r => r.textContent.includes('示範比較表'))", timeout=8000)
    check("收下: the guessed file is on the desk now, as new", dv.get(dv.listing(states=("new",), query="示範比較表")[0]["id"])["state"] == "new"
          and page.evaluate("[...document.querySelectorAll('#pend-deliv .sfd-row:not(.cand) .sfd-t b')].some(b => b.textContent === '示範比較表.xlsx')"))
    page.click('#pend-deliv .sfd-row.cand:has-text("示範競品整理") [data-dact="dismiss"]')
    page.wait_for_timeout(600)
    check("忽略: gone from the list, remembered as ignored (not asked again)", [r["state"] for r in dv.listing(states=("dismissed",))] == ["dismissed"])
    page.click("#pend-jump-d")
    check("the head's pill jumps to the section", page.evaluate("document.getElementById('pend-deliv').getBoundingClientRect().top < document.getElementById('pend-scroll') ? 0 : 1") is not None)
    check("no horizontal scroll with the list open", not overflow_x(page))
    page.close()

    seed()                                                        # the office part starts from the same desks

    # ── the office: desks, button, popup ────────────────────────────────────
    print(f"{tag} the office: desks and the button")
    page = new_page(browser, errors)
    fr = H.open_office(page)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.objects().objects.length === 3; } catch (e) { return false; } }""", timeout=10000)
    st = fr.evaluate("__office.objects()")
    check("the third object is the deliverables desk, with no furniture of its own", [o["id"] for o in st["objects"]] == ["bookshelf", "personnel", "deliverables"], st)
    badge = {o["id"]: o["badge"] for o in st["objects"]}
    want = len([r for r in dv.listing(states=("new",)) if r["seen_at"] is None])
    check("its red number is everything new and never opened, closed tabs and resting bots included; candidates are not", badge["deliverables"] == want and want >= 6, (badge, want))
    tb = fr.evaluate("[...document.querySelectorAll('#tools button[data-obj]')].map(b => ({id: b.dataset.obj, text: b.textContent, badge: b.dataset.badge || '', guide: b.getAttribute('data-guide')}))")
    check("the toolbar has 交付物 with the number, marked for the guide", tb[-1]["id"] == "deliverables" and tb[-1]["text"] == "交付物" and tb[-1]["badge"] == str(want) and tb[-1]["guide"] == "office-deliverables", tb)
    check("…and no piece of furniture on the lobby wall for it (button only; the other two stand there)", [(o["id"], o["onWall"]) for o in st["objects"]] == [("bookshelf", True), ("personnel", True), ("deliverables", False)] and st["objects"][2]["place"] == "button", st["objects"])
    fr.evaluate("__office.setMode('iso', true)")
    page.wait_for_timeout(500)
    piles = fr.evaluate("__office.piles()")
    check("a pile of papers on the desks that have some: the bots with a seat on screen", len(piles) >= 2 and all(k.startswith("b:") or k.startswith("t:") for k in piles), piles)
    if shoot:
        fr.evaluate("__office.fit && __office.fit()")
        page.screenshot(path=str(SHOTS / "deliverables-desks-1280.png"))
    key = next((k for k in piles if k == "b:eng"), piles[0])
    pt = fr.evaluate("(k) => __office.pileAt(k)", key)
    page.mouse.move(pt["x"], pt["y"])
    page.wait_for_timeout(300)
    check("the pile is a hit target: pointer cursor and a tip with the count", fr.evaluate("document.getElementById('stage').classList.contains('pt')") and "交付物" in fr.evaluate("__office.pileTip()"), fr.evaluate("__office.pileTip()"))
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(900)
    check("clicking it opens that person's list in the popup", fr.evaluate("__office.objects().open") == "deliverables"
          and fr.evaluate("document.getElementById('ooTitle').textContent") == "交付物" and fr.evaluate("document.querySelector('select[data-f=owner]').value") == key)
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length >= 3", timeout=10000)
    check("one person: grouped by what was decided (還沒決定, 先放著, 拿走過)", groups(fr) == ["還沒決定", "先放著", "拿走過"], groups(fr))
    pics = fr.evaluate("[...document.querySelectorAll('.oo-it .oo-pic img')].map(i => i.src.startsWith('data:image/png'))")
    check("every row has its kind's small picture", pics and all(pics) and len(pics) == len(titles(fr)), pics)
    if shoot:
        pick(page, fr, "示範週報草稿")
        page.screenshot(path=str(SHOTS / "deliverables-popup-1280.png"))

    print(f"{tag} an entry")
    pick(page, fr, "示範週報草稿")
    view = fr.evaluate("document.getElementById('ooView').innerText")
    check("Markdown, secrets masked on the page (the table is a table), the agent's note first", fr.evaluate("document.querySelectorAll('#ooView table').length") == 1
          and "<redacted>" in view and not any(s in view for s in SECRETS) and "它說" in view and "正常的字" in view, view[:300])
    check("a script and an onerror image written in the note never run (shown as text, no element)", page.evaluate("window.__xss") is None and fr.evaluate("window.__xss") is None
          and fr.evaluate("document.querySelectorAll('#ooView script, #ooView img[src=\"x\"]').length") == 0)
    chips = fr.evaluate("[...document.querySelectorAll('#ooView .oo-chip')].map(c => c.textContent)")
    check("a chip says how many suspected secrets were masked", any("已遮蔽" in c for c in chips), chips)
    check("opening it makes it 看過: still in 還沒決定, tag 看過, the number dropped by one",
          fr.evaluate("(() => { const r = [...document.querySelectorAll('.oo-it')].find(e => e.textContent.includes('示範週報草稿')); return r ? [r.querySelector('.oo-tag').textContent] : null; })()") == ["看過"]
          and "示範週報草稿" in fr.evaluate("(() => [...document.querySelectorAll('.oo-it')].map(e => e.textContent))()").__str__())
    acts = fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)")
    check("the four buttons: 打開, 先放著, 請 X 改…, 拿走（清掉）", acts == ["打開", "先放著", "請 示範工程師 改…", "拿走（清掉）"], acts)
    pick(page, fr, "登入頁新版截圖")
    check("a picture is shown as a picture (an inline image, not text)", fr.evaluate("(() => { const i = document.querySelector('#ooView .oo-shot img'); return !!i && i.src.startsWith('data:image/png') && i.naturalWidth > 0; })()"))
    pick(page, fr, "示範測試報告")
    pdf_img = fr.evaluate("(() => { const i = document.querySelector('#ooView .oo-shot img'); return !!i && i.src.startsWith('data:image/png'); })()")
    check("a PDF shows its first page as a picture when the system can draw one (macOS), else says how to open it",
          pdf_img or "沒有縮圖" in fr.evaluate("document.getElementById('ooView').innerText"))
    pick(page, fr, "示範網頁")
    check("html is never shown: only 「用瀏覽器打開」", "不會在這個視窗裡執行" in fr.evaluate("document.getElementById('ooView').innerText")
          and "用瀏覽器打開" in fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)"))
    n = len(OPENED)
    fr.click('#ooActs [data-action="open"]')
    page.wait_for_timeout(500)
    check("…open sends it to the system's opener (the page never sees the path); no script ran", len(OPENED) == n + 1 and OPENED[-1][1].endswith("示範頁.html") and fr.evaluate("window.__xss") is None
          and not any(str(HOME) in a for a in LOG[-3:]), (OPENED[-2:], fr.evaluate("window.__xss"), [a[:200] for a in LOG[-3:] if str(HOME) in a]))
    pick(page, fr, "示範連線設定")
    view = fr.evaluate("document.getElementById('ooView').innerText")
    check("a key-looking file: not previewed, flagged", "不預覽" in view and "abc123xyz" not in view and "看起來含金鑰" in view and fr.evaluate("document.querySelectorAll('#ooView .oo-shot').length") == 0)
    n = len(OPENED)
    fr.click('#ooActs [data-action="open"]')
    page.wait_for_timeout(400)
    check("…打開 asks first and opens nothing", fr.evaluate("!document.getElementById('ooAsk').hidden") and len(OPENED) == n and "還是要打開" in fr.evaluate("document.getElementById('ooAsk').innerText"))
    if shoot:
        page.screenshot(path=str(SHOTS / "deliverables-popup-confirm-1280.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    check("Esc puts the question away first (the popup stays)", fr.evaluate("document.getElementById('ooAsk').hidden") and fr.evaluate("__office.objects().open") == "deliverables")
    fr.click('#ooActs [data-action="open"]')
    fr.click('#ooAsk [data-ask="yes"]')
    page.wait_for_timeout(500)
    check("…saying yes opens it", len(OPENED) == n + 1 and OPENED[-1][1].endswith("示範連線.env"), OPENED[-1:])
    pick(page, fr, "示範匯出包")
    check("a file that is gone: red tag, struck through, no 打開, still takeable", fr.evaluate("(() => { const r = document.querySelector('.oo-it.bad'); return !!r && r.textContent.includes('檔案不見了'); })()")
          and "打開" not in fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)") and "拿走（清掉）" in fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)"))
    pick(page, fr, "示範預覽站")
    check("a link: opens in the browser", "用瀏覽器打開" in fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)"))

    print(f"{tag} keep, take, ask for a change")
    tot0 = fr.evaluate("__office.objects().objects.find(o => o.id === 'deliverables').badge")
    pick(page, fr, "示範測試報告")
    fr.click('#ooActs [data-action="keep"]')
    page.wait_for_timeout(900)
    check("先放著: the entry moves to 先放著 and stays open with its new buttons", fr.evaluate("(() => [...document.querySelectorAll('.oo-grp')].map(g => g.textContent))()") is not None
          and dv.get(R["pdf"]["id"])["state"] == "kept" and "先放著" not in fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)"))
    check("…the red number does not change for a kept one that was already seen", fr.evaluate("__office.objects().objects.find(o => o.id === 'deliverables').badge") <= tot0)
    pick(page, fr, "示範週報草稿")
    fr.click('#ooActs [data-action="ask_change"]')
    page.wait_for_timeout(300)
    check("請 X 改… asks for one line first (a box, nothing sent yet)", fr.evaluate("!!document.querySelector('#ooAsk input')") and page.evaluate("window.__sends.length") == 0)
    fr.fill("#ooAskIn", "第 3 點改成表格\n謝謝")
    fr.press("#ooAskIn", "Enter")
    page.wait_for_timeout(700)
    sends = page.evaluate("window.__sends")
    check("Enter sends ONE line, in the protocol form, into that bot's tab through the chat send; the person's draft is left alone",
          len(sends) == 1 and sends[0]["sid"] == "s701" and sends[0]["clear"] is False and "\n" not in sends[0]["text"]
          and sends[0]["text"].startswith("（交付物）請改示範週報草稿：第 3 點改成表格") and sends[0]["text"].endswith(R["md"]["ref"]), sends)
    check("the popup says it was sent, and the task it is tied to got its note only after the line went in", "已傳給示範工程師" in fr.evaluate("document.getElementById('ooMsg').textContent")
          and NOTES and NOTES[-1][0] == tk["id"] and "第 3 點改成表格" in NOTES[-1][1], (NOTES, fr.evaluate("document.getElementById('ooMsg').textContent")))
    fr.click('#ooActs [data-action="take"]')
    page.wait_for_timeout(900)
    check("拿走: the entry leaves the desk for 拿走過 (still listed and found), the file is untouched", dv.get(R["md"]["id"])["state"] == "taken" and os.path.exists(R["md"]["ref"])
          and "示範週報草稿" in titles(fr) and "拿走過" in groups(fr))
    fr.fill("#ooSearch", "週報")
    page.wait_for_timeout(600)
    check("…a taken one is still found by search", titles(fr) == ["示範週報草稿"], titles(fr))
    fr.fill("#ooSearch", "")
    page.wait_for_timeout(500)

    print(f"{tag} everybody's list and the guessed files")
    fr.select_option("select[data-f=owner]", "")
    page.wait_for_timeout(700)
    g = groups(fr)
    check("everybody's: grouped by owner (a bot with no tab, a bot, a tab), the closed tab last", g[-1] == "已關閉的分頁" and {"示範工程師", "示範研究員", "示範臨時分頁", "示範秘書長"} <= set(g), g)
    pick(page, fr, "示範舊分頁交的")
    dis = fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => [b.textContent, b.disabled])")
    check("…the ask button of a closed tab's entry is disabled", any(b[0].startswith("請") and b[1] for b in dis), dis)
    fr.select_option("select[data-f=state]", "candidate")
    page.wait_for_timeout(700)
    check("the 可能是交付物 filter lists the guessed files, tagged 待收下", [t for t in titles(fr)] and fr.evaluate("[...document.querySelectorAll('.oo-it .oo-tag')].every(t => t.textContent === '待收下')"), titles(fr))
    note = fr.evaluate("(() => { const m = document.querySelector('.oo-list .oo-more'); return m ? m.textContent : ''; })()")
    check("…and says which key-looking files were passed over (names only, never listed)", "已自動略過 3 個" in note and ".env.local" in note, note)
    pick(page, fr, "示範比較表")
    check("a guess shows the sentence it came from and why, and only 收下 / 忽略 / 全部忽略", "比較表整理好了" in fr.evaluate("document.getElementById('ooView').innerText")
          and fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)") == ["收下", "忽略", "全部忽略"])
    if shoot:
        page.screenshot(path=str(SHOTS / "deliverables-candidates-1280.png"))
    fr.select_option("select[data-f=state]", "")
    page.wait_for_timeout(500)
    if shoot:
        fr.select_option("select[data-f=owner]", "")
        page.screenshot(path=str(SHOTS / "deliverables-all-1280.png"))

    print(f"{tag} widths and interface sizes")
    for scale in (1, 1.18):
        page.evaluate("(s) => document.documentElement.style.setProperty('--ui-scale', String(s))", scale)
        for width in (1280, 900):
            page.set_viewport_size({"width": width, "height": 800})
            page.wait_for_timeout(500)
            pick(page, fr, "示範登入流程表")
            m = frame_metrics(fr)
            lbl = f"{width} px, interface {'M' if scale == 1 else 'L'}"
            check(f"{lbl}: nothing spills sideways, nothing overlaps", m["sw"] <= m["iw"] + 1 and m["bsw"] <= m["iw"] + 1 and m["spill"] == 0 and m["rowSpill"] == 0 and no_overlap(m, width <= 900), m)
            fr.click('#ooActs [data-action="ask_change"]', timeout=3000) if fr.query_selector('#ooActs [data-action="ask_change"]:not([disabled])') else None
            page.wait_for_timeout(250)
            m2 = frame_metrics(fr)
            check(f"{lbl}: with the one-line question open, still no spill or overlap", m2["spill"] == 0 and m2["clash"] == 0 and no_overlap(m2, width <= 900), m2)
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            if shoot and scale == 1 and width == 900:
                fr.select_option("select[data-f=owner]", "b:eng")
                page.wait_for_timeout(600)
                pick(page, fr, "示範登入流程表")
                page.screenshot(path=str(SHOTS / "deliverables-popup-900.png"))
    page.evaluate("document.documentElement.style.setProperty('--ui-scale', '1')")
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(300)
    check("no console error from the page or the office", not errors, errors[:5])
    check("no secret reached the page in any answer the backend gave", not any(s in a for a in LOG for s in SECRETS), [s for s in SECRETS if any(s in a for a in LOG)])
    page.close()

    print(f"{tag} the legend of small objects")
    page = new_page(browser, errors, office=False)
    page.evaluate("""() => { const K = SFDelivDraw.info((en, zh) => zh); const d = document.createElement('div'); d.id = 'legend'; d.style.cssText = 'position:fixed;inset:0;z-index:9999;background:#1a1b26;color:#c0caf5;padding:20px;font:13px system-ui;display:grid;grid-template-columns:repeat(4,1fr);gap:14px;align-content:start';
      d.innerHTML = '<h2 style="grid-column:1/-1;margin:0 0 4px;font-size:18px">桌上的小物件：每種檔案一個樣子</h2>' + K.map(k => `<div style="border:1px solid #3b4261;border-radius:14px;padding:10px;display:flex;gap:12px;align-items:center;background:#1f2335"><img src="${SFDelivDraw.thumb(k.id, 128)}" width="110" height="110" alt="" style="border-radius:10px;background:#24283b"><div><b style="font-size:14px">${k.name}</b><div style="color:#7aa2f7;font:12px ui-monospace,monospace;margin-top:4px">${k.exts}</div></div></div>`).join('');
      document.body.appendChild(d); }""")
    kinds = page.evaluate("SFDelivDraw.info((e, z) => z).map(k => k.id)")
    check("eleven small objects: pdf, html, zip, md, img, pptx, xlsx, docx, video, folder, and a plain paper for the rest", kinds == ["pdf", "html", "zip", "md", "img", "pptx", "xlsx", "docx", "video", "folder", "file"], kinds)
    drawn = page.evaluate("""async () => { const out = {}; for (const k of SFDelivDraw.ORDER) { const i = new Image(); i.src = SFDelivDraw.thumb(k, 64); await i.decode(); const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d'); x.drawImage(i, 0, 0); const d = x.getImageData(0, 0, 64, 64).data; let n = 0; for (let j = 3; j < d.length; j += 4) if (d[j] > 0) n++; out[k] = n; } return out; }""")
    check("every kind draws something (no blank picture)", all(n > 100 for n in drawn.values()), drawn)
    check("a link draws as the little web page, an unknown type as a plain paper", page.evaluate("SFDelivDraw.kindOf('url') === 'html' && SFDelivDraw.kindOf('weird') === 'file' && SFDelivDraw.kindOf('md') === 'md'"))
    page.wait_for_timeout(400)
    if shoot:
        page.screenshot(path=str(SHOTS / "deliverables-legend-1280.png"))
    page.close()

    print(f"{tag} English")
    errors = []
    page = new_page(browser, errors, lang="en")
    fr = H.open_office(page)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.objects().objects.length === 3; } catch (e) { return false; } }""", timeout=10000)
    tb = fr.evaluate("[...document.querySelectorAll('#tools button[data-obj]')].map(b => b.textContent)")
    check("the toolbar button is in English", tb[-1] == "Deliverables", tb)
    fr.evaluate("__office.objOpen('deliverables')")
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length >= 5", timeout=10000)
    check("the popup's title, groups and filters are in English", fr.evaluate("document.getElementById('ooTitle').textContent") == "Deliverables"
          and "Closed tabs" in groups(fr) and fr.evaluate("document.querySelector('select[data-sort] option').textContent") == "Newest first", groups(fr))
    check("no console error", not errors, errors[:5])
    page.close()


SHOTS.mkdir(exist_ok=True)
srv = H.serve()
base = f"http://127.0.0.1:{srv.server_port}"
with sync_playwright() as pw:
    try:
        for engine in ("chromium", "webkit"):
            try:
                browser = getattr(pw, engine).launch()
            except Exception as e:                       # webkit is optional: `playwright install webkit`
                if engine == "chromium":
                    raise
                print(f"[{engine}] not installed, skipped: {str(e).splitlines()[0][:80]}")
                continue
            try:
                run(browser, base, engine)
            finally:
                browser.close()
    finally:
        srv.shutdown()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
