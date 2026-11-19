#!/usr/bin/env python3
"""The office's furniture and its shared popup, in the real page. The Python side is the REAL backend (the js_api
mixin, the bookshelf and the personnel cabinet over a temp folder, through playwright's expose_function), the rest of
the app's calls are the board test's stand-in. Every answer the backend gave is kept and checked for the secrets planted
in the demo files.

  - the bookshelf and the personnel cabinet stand on the lobby wall: hit targets (pointer, tooltip) that open one popup;
    the toolbar has 書櫃 and 人事 buttons that open the same popup and toggle it
  - the bookshelf's red number = bots whose MEMORY.md is more than 80 % of the cap (4,800 is not, 4,801 is), on the
    furniture's data and on its toolbar button
  - the list: groups per bot and shared skills, search (title and text), filters, sort; a skill shows SKILL.md and the
    .md files it links to, never a script
  - the preview is Markdown, with secrets masked before it reaches the page
  - 「請 X 更新」 sends ONE line through the chat view's send to that bot's tab; disabled for a bot with no tab
  - 「用編輯器打開」 asks the backend's opener (the page never gets the path)
  - the cabinet: external and local-model bots are labelled; a task, a schedule and the bookshelf are links; 「編輯執掌」 opens
    the bot editor (the office closes)
  - Esc: the search clears, then the popup closes, then the office
  - at 1280 and 900 px wide, at Interface sizes small, medium and large: nothing spills sideways, nothing overlaps; at 900 the
    list is above the preview
  - English too; no console error
Demo data only. Headless Chromium and WebKit when playwright has it.
Screenshots (Chromium): qa-shots/office-objects-*.png. Run: .venv/bin/python tests_office_objects_ui.py
"""
import json
import os
import pathlib
import sys
import tempfile
import types
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="sf-office-objects-ui-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
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
    print("SKIP  tests_office_objects_ui.py（沒裝 playwright）")
    sys.exit(0)

_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_board_harness")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)   # its stand-in Python side and helpers, not its run

import api_office_objects  # noqa: E402
import bots  # noqa: E402
import office_objects as oo  # noqa: E402

SHOTS = H.SHOTS
SECRETS = ["hunter22", "s3cretpw", "sk-demo1234567890abcdef", "tok-demo-abcdef123456", "ghp_demoabcdefghijkl"]

# ── demo files ───────────────────────────────────────────────────────────────
ROSTER = {
    "chief": {"label": "示範秘書長", "responsibility": "統籌所有 bot 與分頁：盤點誰在忙、誰卡住，把工作交給合適的人。", "cmd": "claude", "model": "opus",
              "bot": {"primary": True, "memory": True, "avatar": {"shape": "star", "color": "purple"}}},
    "eng": {"label": "示範工程師", "responsibility": "寫程式、跑測試", "cmd": "claude", "former_names": ["示範舊工程師"],
            "bot": {"memory": True, "avatar": {"shape": "egg", "color": "amber"}}},
    "res": {"label": "示範研究員", "responsibility": "查資料、寫摘要", "cmd": "codex", "bot": {"memory": True}},
    "local": {"label": "示範地端助理", "responsibility": "本機小模型處理雜事", "cmd": "pi --provider demo-local", "bot": {"memory": True}},
    "outside": {"label": "示範外部總管", "responsibility": "雲端的外部 bot", "cmd": "",
                "bot": {"external": {"kind": "webhook", "url": "https://hooks.example.test/secret-path", "auth": "tok-demo-123456"}}},
}
SK = TMP / "home"
SKILLS = [SK / ".claude" / "skills", SK / ".codex" / "skills"]


def put(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


put(bots.memory_file("chief"), "# 示範秘書長記憶\n\n## 習慣與偏好\n\n- 回報要附連結和截圖；改動先開獨立分支。\n- 測試沒全綠不發版。\n"
    "- 需要使用者決定的事，整理成一張卡等他回。\n\n## 近期\n\n- 11/18：示範版發出，等研究員給資料後再接示範登入頁。\n")
put(bots.memory_file("eng"), "# 示範工程師記憶\n\n## 2026-11-18\n\n- 發出示範版：隔壁房的白板改成唯讀。\n- 方法：先確認對方版本再動手。\n\n"
    "## 連線資訊\n\n| 項目 | 內容 |\n|---|---|\n| 示範主機 | `/srv/demo/host` |\n| 密碼 | password: hunter22 |\n\n"
    "- 倉庫 https://demo:s3cretpw@git.example.test/demo.git\n- 金鑰 sk-demo1234567890abcdef\n\n## 備忘\n\n" + "- 示範備忘。\n" * 600)
put(bots.memory_file("res"), "# 示範研究員記憶\n\n" + "研" * 5200)                                          # near full, no tab
put(bots.memory_file("local"), "# 示範地端助理記憶\n\n" + "y" * 4850)                                      # near full
for n in range(3):
    put(bots.sessions_dir("eng") / f"2026111{n}-100000-s70{n}.md", f"# 示範對話摘要 {n}\n\n- 做了示範的事 {n}\n")
put(bots.skills_dir("eng") / "own-demo" / "SKILL.md", "---\nname: own-demo\ndescription: 示範自己的技能\n---\n# 自己的技能\n")
put(SKILLS[0] / "demo-release" / "SKILL.md",
    "---\nname: demo-release\ndescription: 示範發版流程\n---\n# 示範發版流程\n\n先看 [檢查清單](checklist.md)，再跑 [腳本](run.sh)。\n\ntoken = tok-demo-abcdef123456\n")
put(SKILLS[0] / "demo-release" / "checklist.md", "# 檢查清單\n\n- 跑完測試\n- 更新 CHANGELOG\n")
put(SKILLS[0] / "demo-release" / "run.sh", "#!/bin/sh\nexport GITHUB_TOKEN=ghp_demoabcdefghijkl\n")
put(SKILLS[1] / "demo-release" / "SKILL.md", "# the Codex copy\n")
put(SKILLS[1] / "demo-write" / "SKILL.md", "---\nname: demo-write\ndescription: 示範寫週報\n---\n# 寫週報\n")

TASKS = [{"id": 31, "title": "示範方案 A 或 B", "status": "waiting", "owner": "示範工程師"},
         {"id": 14, "title": "示範單元測試", "status": "doing", "owner": "eng"}]
SCHEDULES = [{"id": "demo.backup", "title": "示範每日備份", "when": {"en": "Daily 02:30", "zh": "每天 02:30"}, "owner": "eng"}]
LOG = []              # every answer the backend gave the page
OPENED = []


class Host(api_office_objects.OfficeObjectsApiMixin):
    def _office_on(self):
        return True

    def _oo_ctx(self, lang):
        return oo.Ctx(lang, roster=lambda: ROSTER, skill_roots=lambda: SKILLS,
                      bot_states=lambda: {"chief": {"state": "awake", "sid": "s700", "position": 1},
                                          "eng": {"state": "awake", "sid": "s701", "position": 2}},
                      open_tasks=lambda: TASKS, schedules=lambda: SCHEDULES, groups=lambda: {},
                      pi_hosts=lambda: {"demo-local": "192.168.1.20"})

    def open_local_file(self, path):
        OPENED.append(path)
        return json.dumps({"success": True})


HOST = Host()


def py_call(name, args_json):
    out = getattr(HOST, name)(*json.loads(args_json))
    LOG.append(out)
    return out


PATCH = """
if (window.top === window) {
  const base = window.pywebview.api;
  window.__sends = [];
  window.pywebview.api = new Proxy({}, { get: (t, prop) => {
    if (prop === 'then') return undefined;
    if (String(prop).startsWith('office_object')) return (...a) => window.py_call(String(prop), JSON.stringify(a));
    if (prop === 'chat_send') return (...a) => { window.__sends.push({sid: a[0], text: a[1], clear: !!a[2]});
      return Promise.resolve(JSON.stringify({success: true, queued: false, id: 'm1'})); };
    return base[prop];
  }});
}
"""

UI_ROSTER = {k: dict(ROSTER[k], cli={"key": ROSTER[k]["cmd"].split()[0], "label": "Claude Code" if ROSTER[k]["cmd"] == "claude" else "Codex"},
                     pin={"ok": True}) for k in ("chief", "eng", "res")}
H.BOTS["roster"] = UI_ROSTER                       # the page's own bots (tabs: chief s700, eng s701; res has none)


def frame_metrics(fr):
    return fr.evaluate("""() => {
      const R = s => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return {l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height}; };
      const box = document.querySelector('.oo-box').getBoundingClientRect();
      const out = e => { const b = e.getBoundingClientRect(); return b.width > 0 && (b.right > box.right + 1 || b.left < box.left - 1 || b.bottom > box.bottom + 1); };
      const foot = [...document.querySelectorAll('#ooNote:not(:empty), #ooMsg:not(:empty), .oo-acts > button')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.getBoundingClientRect());
      let clash = 0;
      for (let i = 0; i < foot.length; i++) for (let j = i + 1; j < foot.length; j++) {
        const a = foot[i], b = foot[j];
        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) clash++;
      }
      const x = document.querySelector('.oo-box').getBoundingClientRect();
      return {box: R('.oo-box'), head: R('.oo-head'), bar: R('.oo-bar'), list: R('.oo-list'), view: R('.oo-view'), foot: R('.oo-foot'),
              sw: document.documentElement.scrollWidth, iw: innerWidth, bsw: document.body.scrollWidth,
              spill: [...document.querySelectorAll('.oo-head *, .oo-bar *, .oo-foot *')].filter(out).length, clash};
    }""")


def no_overlap(m, narrow):
    ok = m["head"]["b"] <= m["bar"]["t"] + 1 and m["bar"]["b"] <= min(m["list"]["t"], m["view"]["t"]) + 1 \
        and m["foot"]["t"] >= max(m["list"]["b"], m["view"]["b"]) - 1
    if narrow:
        ok = ok and m["list"]["b"] <= m["view"]["t"] + 1
    else:
        ok = ok and m["list"]["r"] <= m["view"]["l"] + 1
    return ok and m["clash"] == 0


def pick(page, fr, row_text):
    fr.click(f'.oo-it:has-text("{row_text}")')
    page.wait_for_timeout(350)


def titles(fr):
    return fr.evaluate("[...document.querySelectorAll('.oo-it .oo-it-t')].map(e => e.textContent)")


def run(browser, base, engine):
    shoot = engine == "chromium"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.expose_function("py_call", py_call)
    page.add_init_script(H.stub())
    page.add_init_script(PATCH)
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    page.evaluate("window.SFBots.editBot = role => { window.__edited = role; }")

    print(f"[{engine}] the furniture")
    fr = H.open_office(page)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.objects().objects.length === 2; } catch (e) { return false; } }""", timeout=10000)
    st = fr.evaluate("__office.objects()")
    check("the snapshot carries the two objects: a bookshelf and a cabinet", [(o["id"], o["furniture"]) for o in st["objects"]] == [("bookshelf", "bookshelf"), ("personnel", "cabinet")], st)
    badge = {o["id"]: o["badge"] for o in st["objects"]}
    want = sum(1 for k in ROSTER if bots.memory_file(k).is_file()
               and len(bots.memory_file(k).read_text(encoding="utf-8").strip()) > bots.MEMORY_CAP * 0.8)
    check("the bookshelf's red number is the count of memories over 80% of the cap (the boundary itself: tests_office_shelf.py)",
          badge == {"bookshelf": want, "personnel": 0} and want >= 2, (badge, want))
    tb = fr.evaluate("[...document.querySelectorAll('#tools button[data-obj]')].map(b => ({id: b.dataset.obj, text: b.textContent, badge: b.dataset.badge || '', guide: b.getAttribute('data-guide')}))")
    check("the toolbar has 書櫃 and 人事 (with the number on the first), each marked for the guide",
          [(b["id"], b["text"], b["guide"]) for b in tb] == [("bookshelf", "書櫃", "office-bookshelf"), ("personnel", "人事", "office-personnel")]
          and tb[0]["badge"] == str(want) and tb[1]["badge"] == "", tb)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-objects-lobby-1280.png"))
        tools = fr.evaluate("(() => { const r = document.getElementById('tools').getBoundingClientRect(); return {x: r.left - 8, y: r.top - 8, width: r.width + 16, height: r.height + 16}; })()")
        page.screenshot(path=str(SHOTS / "office-objects-toolbar-1280.png"), clip=tools)

    for oid, tip in (("bookshelf", "書櫃"), ("personnel", "人事檔案櫃")):
        pt = fr.evaluate("(id) => __office.objPoint(id)", oid)
        page.mouse.move(pt["x"], pt["y"])
        page.wait_for_timeout(250)
        hover = fr.evaluate("__office.hoverTip()")
        cursor = fr.evaluate("document.getElementById('stage').classList.contains('pt')")
        check(f"the {oid} is a hit target: pointer cursor and the tooltip {tip}", hover == tip and cursor, (hover, cursor))
    pt = fr.evaluate("(id) => __office.objPoint(id)", "bookshelf")
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(700)
    check("clicking the bookshelf opens the popup", fr.evaluate("__office.objects().open") == "bookshelf"
          and fr.evaluate("document.getElementById('ooTitle').textContent") == "書櫃")
    n_wall = fr.evaluate("__office.view()")
    check("…and no wall view is open under it", n_wall is None)

    print(f"[{engine}] the bookshelf's list")
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length > 5", timeout=10000)
    grp = fr.evaluate("[...document.querySelectorAll('.oo-grp span')].map(e => e.textContent)")
    check("groups: the primary bot first, the other bots, then the shared skills", grp[0] == "示範秘書長" and grp[-1] == "共用技能" and "示範工程師" in grp, grp)
    check("a group per bot that has something (the external bot has no shelf)", "示範外部總管" not in grp, grp)
    rows = titles(fr)
    check("a skill is its SKILL.md and the .md it links to: checklist.md is there, run.sh is not",
          any("demo-release / checklist.md" in t for t in rows) and not any("run.sh" in t for t in rows), rows)
    check("both programs that read demo-release are tagged", fr.evaluate("""() => { const r = [...document.querySelectorAll('.oo-it')].find(e => e.textContent.includes('技能 · demo-release') && !e.textContent.includes('/'));
        return r ? [...r.querySelectorAll('.oo-tag')].map(t => t.textContent) : []; }""") == ["Claude Code", "Codex"])
    check("the near-full memories are marked", fr.evaluate("document.querySelectorAll('.oo-it.warn').length") == want, fr.evaluate("document.querySelectorAll('.oo-it.warn').length"))
    check("the sort options and the two filters are there", fr.evaluate("document.querySelectorAll('select[data-f]').length") == 2
          and fr.evaluate("[...document.querySelectorAll('select[data-sort] option')].map(o => o.textContent)") == ["最近修改", "名稱"])
    check("the 誰的 filter lists the groups", "示範工程師" in fr.evaluate("[...document.querySelectorAll('select[data-f=owner] option')].map(o => o.textContent)"))
    if shoot:
        page.screenshot(path=str(SHOTS / "office-objects-bookshelf-1280.png"))

    print(f"[{engine}] search and filters")
    fr.fill("#ooSearch", "檢查清單")
    page.wait_for_timeout(700)
    check("a search reaches the text of a file (the checklist is found by what it says)", any("checklist.md" in t for t in titles(fr)) and len(titles(fr)) <= 3, titles(fr))
    fr.fill("#ooSearch", "不存在的東西zzz")
    page.wait_for_timeout(600)
    check("nothing matches: it says so", "沒有符合" in fr.evaluate("document.getElementById('ooList').innerText"))
    fr.fill("#ooSearch", "")
    page.wait_for_timeout(600)
    fr.select_option("select[data-f=type]", "skill")
    page.wait_for_timeout(500)
    check("filter by type: skills only", titles(fr) and all(t.startswith("技能") or "/" in t for t in titles(fr)), titles(fr))
    fr.select_option("select[data-f=type]", "")
    fr.select_option("select[data-f=owner]", "eng")
    page.wait_for_timeout(500)
    check("filter by whose: one bot", fr.evaluate("[...document.querySelectorAll('.oo-grp span')].map(e => e.textContent)") == ["示範工程師"])
    fr.select_option("select[data-f=owner]", "")
    fr.select_option("select[data-sort]", "title")
    page.wait_for_timeout(500)
    check("sort by name still lists everything", len(titles(fr)) > 8)
    fr.select_option("select[data-sort]", "mtime")
    page.wait_for_timeout(400)

    print(f"[{engine}] an entry: Markdown, masked")
    pick(page, fr, "記憶 · MEMORY.md")                               # the first one: the primary bot's
    view = fr.evaluate("document.getElementById('ooView').innerText")
    check("the primary bot's memory is shown as Markdown (heading and list items)", fr.evaluate("document.querySelectorAll('#ooView .cm-h').length") >= 1
          and fr.evaluate("document.querySelectorAll('#ooView .cm-li').length") >= 1 and "測試沒全綠不發版" in view, view[:200])
    fr.fill("#ooSearch", "示範工程師記憶")
    page.wait_for_timeout(600)
    pick(page, fr, "記憶 · MEMORY.md")
    view = fr.evaluate("document.getElementById('ooView').innerText")
    check("the engineer's memory: a table is a table, and the secrets are masked on the page",
          fr.evaluate("document.querySelectorAll('#ooView table').length") == 1 and "<redacted>" in view
          and not any(s in view for s in SECRETS), view[:300])
    check("the length line, with the cap, and the 'last changed' line", "/ 6,000 字" in view and "最後修改" in view, view[:200])
    check("no secret reached the page at all (every backend answer so far is clean)", not any(s in a for a in LOG for s in SECRETS), [s for s in SECRETS if any(s in a for a in LOG)])
    fr.fill("#ooSearch", "")
    page.wait_for_timeout(500)
    pick(page, fr, "技能 · demo-release")
    view = fr.evaluate("document.getElementById('ooView').innerText")
    check("a skill: the token written in its SKILL.md is masked, front matter is not shown", "tok-demo-abcdef123456" not in view and "description:" not in view and "示範發版流程" in view, view[:200])
    links = fr.evaluate("[...document.querySelectorAll('#ooView .oo-link span')].map(e => e.textContent)")
    check("…it links to its .md files only (not the script it mentions)", links == ["checklist.md"], links)
    fr.click("#ooView .oo-link")
    page.wait_for_timeout(500)
    check("a link opens that document", "檢查清單" in fr.evaluate("document.getElementById('ooView').innerText") and "SKILL.md" in fr.evaluate("document.getElementById('ooView').innerText"))
    check("a shared skill has no 「請 … 更新」 (nobody owns it), only the editor", fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)") == ["用編輯器打開"])

    print(f"[{engine}] 「請 X 更新」 and 「用編輯器打開」")
    fr.fill("#ooSearch", "示範工程師記憶")
    page.wait_for_timeout(600)
    pick(page, fr, "記憶 · MEMORY.md")
    acts = fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => ({t: b.textContent, d: b.disabled}))")
    check("the engineer has a tab: 「請 示範工程師 更新」 is enabled next to 用編輯器打開", acts == [{"t": "用編輯器打開", "d": False}, {"t": "請 示範工程師 更新", "d": False}], acts)
    before = page.evaluate("window.__sends.length")
    fr.click('#ooActs [data-action="ask_update"]')
    page.wait_for_timeout(600)
    sends = page.evaluate("window.__sends")
    check("one line goes into that bot's tab through the chat send, the person's draft is left alone",
          len(sends) == before + 1 and sends[-1]["sid"] == "s701" and sends[-1]["clear"] is False
          and sends[-1]["text"].startswith("（書櫃）請更新你的 MEMORY.md：") and "\n" not in sends[-1]["text"], sends[-1:])
    check("the popup says it was sent", "已傳給示範工程師" in fr.evaluate("document.getElementById('ooMsg').textContent"), fr.evaluate("document.getElementById('ooMsg').textContent"))
    fr.click('#ooActs [data-action="open_editor"]')
    page.wait_for_timeout(500)
    check("the editor: the backend's opener got the memory file's path, the page never saw it", OPENED and OPENED[-1].endswith("eng/MEMORY.md")
          and not any(OPENED[-1] in a for a in LOG), OPENED[-1:])
    fr.fill("#ooSearch", "示範研究員")
    page.wait_for_timeout(500)
    fr.fill("#ooSearch", "")
    page.wait_for_timeout(500)
    fr.select_option("select[data-f=owner]", "res")
    page.wait_for_timeout(500)
    pick(page, fr, "記憶 · MEMORY.md")
    acts = fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => ({t: b.textContent, d: b.disabled, title: b.title}))")
    check("a bot with no open tab: its ask button is disabled and says why", acts[1]["d"] and acts[1]["t"] == "請 示範研究員 更新" and "沒有開著的分頁" in acts[1]["title"], acts)
    before = page.evaluate("window.__sends.length")
    fr.click('#ooActs [data-action="ask_update"]', force=True, timeout=2000)
    page.wait_for_timeout(300)
    check("…and clicking it sends nothing", page.evaluate("window.__sends.length") == before)
    fr.select_option("select[data-f=owner]", "")

    print(f"[{engine}] the toolbar and Esc")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("the popup covers the toolbar (like the whiteboard's sheet); Esc closes it", fr.evaluate("__office.objects().open") is None)
    fr.click('#tools button[data-obj="personnel"]')
    page.wait_for_timeout(600)
    check("the 人事 button opens the cabinet in the same popup", fr.evaluate("__office.objects().open") == "personnel"
          and fr.evaluate("document.getElementById('ooTitle').textContent") == "人事檔案櫃")
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length >= 5", timeout=10000)
    fr.evaluate("document.getElementById('tools').querySelector('[data-obj=\"bookshelf\"]').dispatchEvent(new MouseEvent('click', {bubbles: true}))")
    page.wait_for_timeout(500)
    check("the 書櫃 button, pressed (by keyboard) while another popup is open, switches to the shelf", fr.evaluate("__office.objects().open") == "bookshelf")
    fr.evaluate("document.querySelector('#tools [data-obj=\"bookshelf\"]').click()")
    page.wait_for_timeout(300)
    check("…pressed again it closes", fr.evaluate("__office.objects().open") is None)
    fr.evaluate("__office.objOpen('bookshelf')")
    page.wait_for_timeout(500)
    fr.fill("#ooSearch", "abc")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc in the search box clears it first", fr.evaluate("__office.objects().open") == "bookshelf" and fr.evaluate("document.getElementById('ooSearch').value") == "")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("the next Esc closes the popup, not the office", fr.evaluate("__office.objects().open") is None and page.evaluate("SFOffice.isOpen()"))
    fr.evaluate("__office.objOpen('bookshelf')")
    page.wait_for_timeout(300)
    fr.click(".oo-veil", position={"x": 4, "y": 4})
    page.wait_for_timeout(300)
    check("a click outside the box closes it", fr.evaluate("__office.objects().open") is None)

    print(f"[{engine}] the personnel cabinet")
    fr.evaluate("__office.objOpen('personnel')")
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length >= 5", timeout=10000)
    rows = fr.evaluate("""() => [...document.querySelectorAll('.oo-it')].map(r => ({t: r.querySelector('.oo-it-t').textContent, tags: [...r.querySelectorAll('.oo-tag')].map(x => x.textContent)}))""")
    by = {r["t"]: r["tags"] for r in rows}
    check("all five bots are on file; the primary is marked", len(rows) == 5 and "主要" in by["示範秘書長"], by)
    check("an external bot is labelled 外部; a bot on a local model is labelled 地端模型; a cloud one is neither",
          "外部" in by["示範外部總管"] and "地端模型" in by["示範地端助理"] and by["示範工程師"] == [] and by["示範研究員"] == [], by)
    if shoot:
        page.screenshot(path=str(SHOTS / "office-objects-personnel-list-1280.png"))
    pick(page, fr, "示範外部總管")
    view = fr.evaluate("document.getElementById('ooView').innerText")
    check("an external bot's file: says it lives outside, shows nothing of its endpoint, no bookshelf link",
          "在 ShellBot 外面" in view and "hooks.example" not in view and "secret-path" not in view and "tok-demo" not in view and "書櫃" not in view, view[:300])
    pick(page, fr, "示範地端助理")
    chips = fr.evaluate("[...document.querySelectorAll('#ooView .oo-chip')].map(c => c.textContent)")
    check("a local-model bot's file says 地端模型", "地端模型" in chips, chips)
    pick(page, fr, "示範工程師")
    view = fr.evaluate("document.getElementById('ooView').innerText")
    check("the engineer's file: duty, open tasks (by label and by role), the schedule it owns, how much it keeps",
          "寫程式、跑測試" in view and "#31" in view and "#14" in view and "示範每日備份" in view and "每天 02:30" in view and "1 份記憶" in view, view[:500])
    check("its only action is 編輯執掌", fr.evaluate("[...document.querySelectorAll('#ooActs button')].map(b => b.textContent)") == ["編輯執掌"])
    if shoot:
        page.screenshot(path=str(SHOTS / "office-objects-personnel-file-1280.png"))
    fr.click('#ooView .oo-link:has-text("#31")')
    page.wait_for_timeout(700)
    check("a task link closes the popup and opens that card on the whiteboard", fr.evaluate("__office.objects().open") is None and fr.evaluate("__office.view()") == "board")
    fr.evaluate("__office.closeView()")
    fr.evaluate("__office.objOpen('personnel')")
    page.wait_for_timeout(700)
    pick(page, fr, "示範工程師")
    fr.click('#ooView .oo-link:has-text("示範每日備份")')
    page.wait_for_timeout(600)
    check("a schedule link opens the calendar", fr.evaluate("__office.view()") == "cal" and fr.evaluate("__office.objects().open") is None)
    fr.evaluate("__office.closeView()")
    fr.evaluate("__office.objOpen('personnel')")
    page.wait_for_timeout(700)
    pick(page, fr, "示範工程師")
    fr.click('#ooView .oo-link:has-text("書櫃")')
    page.wait_for_timeout(900)
    check("the bookshelf link opens the shelf already filtered to that bot", fr.evaluate("__office.objects().open") == "bookshelf"
          and fr.evaluate("document.querySelector('select[data-f=owner]').value") == "eng"
          and fr.evaluate("[...document.querySelectorAll('.oo-grp span')].map(e => e.textContent)") == ["示範工程師"])
    check("…and the other bots are still choices in the filter", len(fr.evaluate("[...document.querySelectorAll('select[data-f=owner] option')].map(o => o.value)")) >= 4)
    fr.evaluate("__office.objOpen('personnel')")
    page.wait_for_timeout(700)
    pick(page, fr, "示範工程師")
    fr.click('#ooActs [data-action="edit_duty"]')
    page.wait_for_timeout(700)
    check("編輯執掌 opens the bot editor and the office gets out of the way", page.evaluate("window.__edited") == "eng" and not page.evaluate("SFOffice.isOpen()"))

    print(f"[{engine}] sizes")
    fr = H.open_office(page)
    for w, h, narrow in ((1280, 800, False), (900, 700, True)):
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(500)
        for scale in (0.85, 1, 1.18):
            page.evaluate("(s) => document.documentElement.style.setProperty('--ui-scale', String(s))", scale)
            page.evaluate("SFOffice.refresh()")
            page.wait_for_timeout(700)
            for oid in ("bookshelf", "personnel"):
                fr.evaluate("(id) => __office.objOpen(id)", oid)
                page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length >= 4", timeout=10000)
                fr.evaluate("(id) => document.querySelectorAll('.oo-it')[id === 'personnel' ? 0 : 1].click()", oid)
                page.wait_for_timeout(600)
                m = frame_metrics(fr)
                tag = f"{oid} at {w}px, interface size {scale}"
                check(f"{tag}: nothing spills sideways", m["sw"] <= m["iw"] and m["bsw"] <= m["iw"] and m["spill"] == 0, {k: m[k] for k in ("sw", "bsw", "iw", "spill")})
                x = page.evaluate("(() => { const r = document.querySelector('.sfo-close').getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom}; })()")
                check(f"{tag}: the office's own ✕ is clear of the popup", x["l"] >= m["box"]["r"] - 1 or x["r"] <= m["box"]["l"] + 1 or x["b"] <= m["box"]["t"] + 1, (x, m["box"]))
                check(f"{tag}: nothing overlaps" + (" (the list is above the preview)" if narrow else " (list left, preview right)"), no_overlap(m, narrow), m)
                if shoot and scale == 1:
                    page.screenshot(path=str(SHOTS / f"office-objects-{oid}-{w}.png"))
                fr.evaluate("__office.objClose()")
    page.evaluate("document.documentElement.style.setProperty('--ui-scale', '1')")
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(400)

    check("no console error from the page or the office", not errors, errors[:5])
    page.close()

    print(f"[{engine}] English")
    errors = []
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.on("console", lambda m: errors.append(f"console {m.text}") if m.type == "error" else None)
    page.expose_function("py_call", py_call)
    page.add_init_script(H.stub())
    page.add_init_script(PATCH)
    page.add_init_script("if (window.top === window) window.__cfg.settings.language = 'en';")
    page.goto(base + "/index.html")
    page.wait_for_selector("#sb-footer-row", timeout=20000)
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    fr = H.open_office(page)
    page.wait_for_function("""() => { const f = document.querySelector('#sfo-overlay iframe'); try { return f.contentWindow.__office.objects().objects.length === 2; } catch (e) { return false; } }""", timeout=10000)
    tb = fr.evaluate("[...document.querySelectorAll('#tools button[data-obj]')].map(b => b.textContent)")
    check("the toolbar buttons are in English (the short word: Personnel)", tb == ["Bookshelf", "Personnel"], tb)
    fr.evaluate("__office.objOpen('bookshelf')")
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length > 5", timeout=10000)
    check("the popup, its filters and its rows are in English", fr.evaluate("document.getElementById('ooTitle').textContent") == "Bookshelf"
          and "Memory · MEMORY.md" in titles(fr) and fr.evaluate("document.querySelector('select[data-sort] option').textContent") == "Recently changed"
          and "Shared skills" in fr.evaluate("[...document.querySelectorAll('.oo-grp span')].map(e => e.textContent)"), titles(fr)[:3])
    pick(page, fr, "Memory · MEMORY.md")
    fr.evaluate("__office.objOpen('personnel')")
    page.wait_for_function("document.querySelector('#sfo-overlay iframe').contentDocument.querySelectorAll('.oo-it').length >= 5", timeout=10000)
    pick(page, fr, "示範外部總管")
    check("an external bot's file in English", "Lives outside ShellBot" in fr.evaluate("document.getElementById('ooView').innerText"))
    check("no console error from the page or the office", not errors, errors[:5])
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
