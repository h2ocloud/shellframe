#!/usr/bin/env python3
"""The deliverables object's hooks (office_deliverables.py) over a temp database, with a stand-in `ctx`.

  - summary: the red number = new and never opened (closed tabs too); per person; per task
  - list: one person → grouped by what the person decided; everybody → grouped by owner, closed tabs last; filters, search
    (a taken one is found), sort; a missing file is marked and nothing is thrown; candidates only with their filter
  - item: opening marks it seen (it stays undecided); Markdown is masked (the secret never leaves); a txt/csv is fenced;
    html is never shown, only "open in browser"; a picture is an inline image; secret-flagged: no body, no image
  - action: keep / take / accept / dismiss; open: a flagged one asks first, an unknown extension (a .command) is shown in
    the file manager, never opened; 「請他改」 is one line in the protocol form with the long file name intact, and a task
    gets its note only after the line went in; nothing is stored of the file's content
  - the host: `by_owner` and a picture pass `clean_*`, a long file name survives the line to a bot, a plugin's does not
Temp folders only. Demo data. Run: .venv/bin/python tests_office_deliverables.py
"""
import base64
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deliverables as dv  # noqa: E402
import office_deliverables as od  # noqa: E402
import office_objects as oo  # noqa: E402
import state_store as ss  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


TMP = Path(tempfile.mkdtemp(prefix="sf-office-deliv-"))
HOME = TMP / "home"
HOME.mkdir()
DB = TMP / "state.db"
NOW = 1_800_000_000.0
LIVE = {"s1": "1700000001"}
NOTES = []
THUMBS = []
ROSTER = {"eng": {"label": "示範工程師", "bot": {"memory": True}}}


def ctx(lang="zh", live=None):
    return oo.Ctx(lang, live_tabs=lambda: dict(LIVE if live is None else live), tab_labels=lambda: {"s1": "示範分頁"},
                  roster=lambda: ROSTER, task_note=lambda tid, text: NOTES.append((tid, text)),
                  deliv=lambda: {"path": DB, "home": HOME, "now": NOW + 100, "thumb": lambda p: THUMBS.append(p) or "data:image/png;base64,AAAA"})


def make(rel, data="示範\n", age=60):
    p = TMP / "work" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.utime(p, (NOW - age, NOW - age))
    return p


def reg(owner, p, **kw):
    kind, key, label = owner
    return dv.add(kind, key, label, p, now=kw.pop("now", NOW), home=HOME, path=DB, **kw)


ENG, TAB, OLD = ("bot", "eng", "示範工程師"), ("tab", "s1@1700000001", "示範分頁"), ("tab", "s9@1600000000", "舊分頁")
O = od.Deliverables()

md = make("週報.md", "# 示範週報\n\n<script>alert(1)</script>\n\nAPI_TOKEN=abc123xyz\nhttps://x.test/?token=zz9\n\n正常的字。\n")
r_md = reg(ENG, md, title="示範週報草稿", note="第 3 點是我猜的 API_TOKEN=abc123xyz")
r_html = reg(ENG, make("頁.html", "<script>window.hacked=1</script><p>hi</p>"), title="示範網頁")
r_pdf = reg(ENG, make("報告.pdf", b"%PDF-1.4"), title="示範報告")
png = make("圖.png", b"\x89PNG\r\n\x1a\n" + b"x" * 40)
r_img = reg(ENG, png, title="示範截圖")
r_txt = reg(TAB, make("a.txt", "第一行\n第二行 ``` 三個反引號\n"), title="示範文字")
r_csv = reg(TAB, make("t.csv", "a,b\n1,2\n"), title="示範表")
r_old = reg(OLD, make("舊.md", "x"), title="舊分頁的")
r_env = reg(ENG, make("設定.env", "API_TOKEN=abc123xyz\n"), title="示範設定")
r_key = reg(ENG, make("金鑰說明.md", "連線用 ghp_demoabcdefghijkl 請小心\n"), title="示範金鑰說明")
r_cmd = reg(ENG, make("run.command", "echo hi"), title="示範腳本")
r_zip = reg(ENG, make("包.zip", b"PK"), title="示範壓縮")
r_link = reg(ENG, "https://demo.example.test/preview/login?token=zz9", title="示範預覽站")
r_gone = reg(ENG, make("消失.md", "x"), title="示範會消失的")
os.unlink(TMP / "work" / "消失.md")

print("summary")
s = O.summary(ctx())
check("the red number: new and never opened, everywhere (a closed tab's too)", s["badge"] == 13, s)
check("per person: the bot's desk (count, new, the newest three types), an open tab's, the closed tab's separately",
      s["by_owner"]["b:eng"]["count"] == 10 and len(s["by_owner"]["b:eng"]["types"]) == 3
      and s["by_owner"]["t:s1"]["count"] == 2 and s["by_owner"]["x:s9@1600000000"]["closed"], s["by_owner"].keys())
clean = oo.clean_summary(s)
check("the host keeps by_owner and by_task", clean["by_owner"]["t:s1"]["new"] == 2 and "by_task" in clean and clean["badge"] == 13)

print("the list")
lst = O.list(ctx())
check("everybody: grouped by owner (the bot, the open tab with its tag), closed tabs last",
      [g["id"] for g in lst["groups"]] == ["b:eng", "t:s1", "closed"] and lst["groups"][1]["tag"] == "分頁"
      and lst["groups"][0]["title"] == "示範工程師" and lst["groups"][2]["title"] == "已關閉的分頁", lst["groups"])
clean = oo.clean_list(lst, "zh")
by_title = {i["title"]: i for i in clean["items"]}
check("a closed tab's row has no owner key (it cannot be told anything)", by_title["舊分頁的"]["owner_key"] == "" and by_title["示範週報草稿"]["owner_key"] == "b:eng")
check("a new one is tagged 新; the missing file 檔案不見了 and its tone is bad; the secret one is tagged",
      by_title["示範週報草稿"]["tags"][0]["label"] == "新" and by_title["示範週報草稿"]["fresh"]
      and any(t["label"] == "檔案不見了" for t in by_title["示範會消失的"]["tags"]) and by_title["示範會消失的"]["tone"] == "bad"
      and any(t["label"] == "看起來含金鑰" for t in by_title["示範設定"]["tags"]))
check("the note is shown masked in the row", "abc123xyz" not in by_title["示範週報草稿"]["subtitle"] and "附註" in by_title["示範週報草稿"]["subtitle"])
check("a link's row shows the masked address", "zz9" not in by_title["示範預覽站"]["subtitle"] and "demo.example.test" in by_title["示範預覽站"]["subtitle"])
one = O.list(ctx(), filters={"owner": "b:eng"})
check("one person: grouped 還沒決定 first", [g["id"] for g in one["groups"]] == ["new"] and all(i["group"] == "new" for i in one["items"]))
check("the type filter", {i["type"] for i in O.list(ctx(), filters={"type": "pdf"})["items"]} == {"pdf"})
check("a search finds a title and a note", [i["title"] for i in O.list(ctx(), query="猜的")["items"]] == ["示範週報草稿"])
check("sort by name", [i["title"] for i in O.list(ctx(), sort="title")["items"]] == sorted(i["title"] for i in O.list(ctx(), sort="title")["items"]))

print("an entry")
before = dv.get(r_md["id"], DB)
it = oo.clean_item(O.item(ctx(), str(r_md["id"])), od.DECLARATION["actions"])
check("a note is Markdown, masked: nothing of the secrets reaches the answer",
      "abc123xyz" not in it["markdown"] and "zz9" not in it["markdown"] and "<redacted>" in it["markdown"] and "正常的字" in it["markdown"], it["markdown"])
check("html in a note is plain text to the page (it is escaped by the renderer; the backend never makes HTML)", "<script>alert(1)</script>" in it["markdown"])
check("the agent's note comes first, as a quote", it["markdown"].startswith("> **它說**"))
check("a chip says how many were masked", any("已遮蔽" in c["text"] for c in it["chips"]), it["chips"])
check("opening it marked it seen: still new (undecided), not counted any more",
      dv.get(r_md["id"], DB)["state"] == "new" and dv.get(r_md["id"], DB)["seen_at"] and O.summary(ctx())["badge"] == 12 and before["seen_at"] is None)
check("the actions of an undecided one", it["actions"] == ["open", "keep", "ask_change", "take"])
check("the subtitle says whose and where (the path is shown masked, long names whole)", it["subtitle"].startswith("示範工程師 / "))
check("html: nothing shown, and the button says open in browser",
      "不會在這個視窗裡執行" in O.item(ctx(), str(r_html["id"]))["markdown"] and "<script" not in O.item(ctx(), str(r_html["id"]))["markdown"]
      and O.item(ctx(), str(r_html["id"]))["action_labels"]["open"] == "用瀏覽器打開")
check("a txt is fenced with a fence that the text cannot close", O.item(ctx(), str(r_txt["id"]))["markdown"].startswith("````\n"))
check("a csv is shown as plain text", "a,b" in O.item(ctx(), str(r_csv["id"]))["markdown"])
im = oo.clean_item(O.item(ctx(), str(r_img["id"])), od.DECLARATION["actions"])
check("a picture is an inline image (a validated data URL), read only now", im["image"].startswith("data:image/png;base64,")
      and base64.b64decode(im["image"].split(",", 1)[1]).startswith(b"\x89PNG"))
check("the host drops an image that is not a raster data URL", oo.clean_item({"image": "data:image/svg+xml;base64,AAAA"})["image"] == ""
      and oo.clean_item({"image": "https://x.test/a.png"})["image"] == "" and oo.clean_item({"image": 'data:image/png;base64,AA" onerror="x'})["image"] == "")
check("a file with a wrong magic number is not shown as a picture", O._image({"ref": str(make("bad.png", b"not a png"))}) == "")
pdf = O.item(ctx(), str(r_pdf["id"]))
check("a PDF: the thumbnail comes from the renderer, read only now, only for this entry", pdf["image"] and THUMBS == [r_pdf["ref"]])
env = O.item(ctx(), str(r_env["id"]))
check("a flagged file: no body, no image, no thumbnail, a chip says why", "不預覽" in env["markdown"] and not env["image"]
      and any("金鑰" in c["text"] for c in env["chips"]) and "abc123xyz" not in repr(env))
check("a link: its address is masked in the entry and the button says open in browser", "zz9" not in repr(O.item(ctx(), str(r_link["id"])))
      and O.item(ctx(), str(r_link["id"]))["action_labels"]["open"] == "用瀏覽器打開")
gone = O.item(ctx(), str(r_gone["id"]))
check("a missing file: a red chip, no open, still takeable", any("不見了" in c["text"] for c in gone["chips"]) and "open" not in gone["actions"] and "take" in gone["actions"])
later = make("later.md", "fine\n")
r_later = reg(ENG, later)
check("a file that became secret gains the flag (checked at every opening)", r_later["flag"] == "")
later.write_text("-----BEGIN PGP PRIVATE KEY BLOCK-----\nx\n", encoding="utf-8")
O.item(ctx(), str(r_later["id"]))
check("…the verdict is stored", dv.get(r_later["id"], DB)["flag"] == "secret")
check("an id that is not a row: nothing (not an error)", O.item(ctx(), "99999") == {} and O.item(ctx(), "../../etc/passwd") == {})

print("actions")
r = O.action(ctx(), str(r_env["id"]), "open", {})
check("opening a flagged one asks first (nothing opens)", r["confirm"]["args"] == {"confirmed": "1"} and "open" not in r and "reveal" not in r)
r = O.action(ctx(), str(r_env["id"]), "open", {"confirmed": "1"})
check("…and opens after the person said yes", r.get("reveal") or r.get("open"))
check("a registered .command is shown in the file manager, never opened", O.action(ctx(), str(r_cmd["id"]), "open", {}).get("reveal") == r_cmd["ref"])
check("a zip is shown in the file manager (not unpacked)", O.action(ctx(), str(r_zip["id"]), "open", {}).get("reveal") == r_zip["ref"])
check("a normal document is opened", O.action(ctx(), str(r_md["id"]), "open", {}).get("open") == r_md["ref"])
check("a link is opened in the browser, as it was registered", O.action(ctx(), str(r_link["id"]), "open", {"confirmed": "1"}).get("url") == r_link["ref"])
check("a link that carries a token asks first", "confirm" in O.action(ctx(), str(r_link["id"]), "open", {}))
check("a missing file cannot be opened", O.action(ctx(), str(r_gone["id"]), "open", {}) .get("ok") is False)
r = O.action(ctx(), str(r_pdf["id"]), "keep", {})
check("先放著", r["ok"] and r["refresh"] and dv.get(r_pdf["id"], DB)["state"] == "kept")
check("…a kept one offers 請他改 and 拿走 but not 先放著 again", oo.clean_item(O.item(ctx(), str(r_pdf["id"])), od.DECLARATION["actions"])["actions"] == ["open", "ask_change", "take"])
n_before = O.summary(ctx())["by_owner"]["b:eng"]["count"]
r = O.action(ctx(), str(r_pdf["id"]), "take", {})
check("拿走", r["ok"] and dv.get(r_pdf["id"], DB)["state"] == "taken" and O.summary(ctx())["by_owner"]["b:eng"]["count"] == n_before - 1)
check("…it is still listed (拿走過) and found by search", any(i["title"] == "示範報告" and i["tone"] == "dim" for i in O.list(ctx())["items"])
      and [i["title"] for i in O.list(ctx(), filters={"state": "taken"})["items"]] == ["示範報告"])
check("a taken one offers only open", oo.clean_item(O.item(ctx(), str(r_pdf["id"])), od.DECLARATION["actions"])["actions"] == ["open"])
check("an action that no longer fits is refused in words, not an error", O.action(ctx(), str(r_pdf["id"]), "keep", {})["ok"] is False)

print("請他改")
tk = ss.add("示範任務", now=NOW, path=DB)
linked = reg(ENG, make("連任務.md", "內容"), title="demo-quarterly-report-2026-11-19-v2-final", task_id=tk["id"])
res = O.action(ctx(), str(linked["id"]), "ask_change", {"text": "第 3 點改成表格  \n謝謝"})
line = res["tell"]["text"]
check("one line, the protocol form: （交付物）請改〈標題〉：〈你的話〉；檔案在〈路徑〉",
      line == f"（交付物）請改demo-quarterly-report-2026-11-19-v2-final：第 3 點改成表格 謝謝；檔案在{linked['ref']}" and "\n" not in line, line)
check("it goes to the bot's tab through the chat view's send", res["tell"]["to"] == "b:eng")
clean = oo.clean_action(res, trusted=True)
check("the host keeps a long file name in that line (the 32-character rule is for untrusted text)",
      "demo-quarterly-report-2026-11-19-v2-final" in clean["tell"]["text"] and "<redacted>" not in clean["tell"]["text"], clean["tell"]["text"])
check("…and a plugin's line is masked as before", "<redacted>" in oo.clean_action(res, trusted=False)["tell"]["text"])
check("a linked task gets its note only after the line went in (a follow-up the page asks for)",
      clean["tell"]["after"]["action"] == "ask_logged" and NOTES == [])
O.action(ctx(), str(linked["id"]), "ask_logged", {"text": "第 3 點改成表格"})
check("…the note is on the task, with the title and the words", NOTES and NOTES[0][0] == tk["id"] and "第 3 點改成表格" in NOTES[0][1], NOTES)
res = O.action(ctx(), str(r_md["id"]), "ask_change", {"text": "token=abc123xyz 改掉"})
check("an unlinked deliverable: no follow-up; a secret in the person's words is masked", "after" not in res["tell"] and "abc123xyz" not in res["tell"]["text"])
check("nothing to say: refused", O.action(ctx(), str(r_md["id"]), "ask_change", {"text": "  "})["ok"] is False)
check("a closed tab cannot be told", "tab" in O.action(ctx(), str(r_old["id"]), "ask_change", {"text": "x"})["message"] or "分頁" in O.action(ctx(), str(r_old["id"]), "ask_change", {"text": "x"})["message"])
flag_line = O.action(ctx(), str(r_env["id"]), "ask_change", {"text": "改"})["tell"]["text"]
check("a flagged file's folder is not put in the line (its name only)", "設定.env" in flag_line and str(TMP) not in flag_line, flag_line)
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
en = O.action(oo.Ctx("en", live_tabs=lambda: LIVE, deliv=lambda: {"path": DB, "home": HOME, "now": NOW}), str(r_md["id"]), "ask_change", {"text": "fix"})
check("English has its own wording", en["tell"]["text"].startswith("(Deliverable) Please change "), en["tell"]["text"])
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"

print("candidates")
cand_file = make("候選.xlsx", b"PK")
dv.add("bot", "eng", "示範工程師", str(cand_file), origin="auto", source="比較表整理好了，存在 " + str(cand_file) + "，A 方案便宜。", state="candidate", now=NOW, home=HOME, path=DB)
cl = O.list(ctx(), filters={"state": "candidate"})
check("candidates have their own filter and are tagged 待收下", len(cl["items"]) == 1 and cl["items"][0]["tags"][0]["label"] == "待收下")
cid = cl["items"][0]["id"]
ci = O.item(ctx(), cid)
check("a candidate shows the sentence and the reasons; it does not mark anything; only accept / ignore / ignore all",
      "比較表整理好了" in ci["markdown"] and "為什麼猜" in ci["markdown"] and ci["actions"] == ["accept", "dismiss", "dismiss_all"])
check("a candidate is not in the desk counts", "b:eng" in O.summary(ctx())["by_owner"] and dv.overview(LIVE, path=DB)["candidates_total"] == 1)
O.action(ctx(), cid, "accept", {})
check("收下: it is on the desk as new", dv.get(int(cid), DB)["state"] == "new" and dv.get(int(cid), DB)["seen_at"] is None)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
