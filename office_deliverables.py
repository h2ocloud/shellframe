"""The office's deliverables desk (a built-in office object, see office_objects.py and deliverables.py).

What a bot or a tab handed the person, as references: its popup is the list of them (per person, or everybody's grouped
by owner), a preview on the right, and the person's own actions: 打開 (open), 先放著 (keep on the desk), 請他改 (one line to
the owner, through the chat view's send, so it waits for the bot to be free), 拿走 (clear it off the desk; kept in
history), and for what auto-pickup only guessed: 收下 / 忽略. Only the person acts: a bot can register (`sfctl deliver
add`) and nothing else.

What differs from the other objects:
  - the object has no furniture (`place: "button"`): the toolbar button only. Its desk piles are drawn by the office
    page on each person's desk, from `summary`'s `by_owner`
  - `summary` answers per person (count on the desk, new and never opened, the newest three types) and per task
  - `item` is where a file is read, for the one entry the person opened (and marks it seen): a text file (256 KB) as
    Markdown, masked; a picture or the first page of a PDF as an image; html is never shown (only "open in browser");
    nothing is read for an entry that looks like it holds secrets, or whose file is gone
  - `action` open: a flagged entry asks first (`confirm`); only a known kind of file is opened by the system (anything
    else, and a zip, is shown in the file manager: a registered `.command` must not run)

Hooks follow office_objects.OfficeObject. `ctx` providers (api_deliverables builds the real one, a test a fake):
`live_tabs()` {sid: opened}, `tab_labels()` {sid: label}, `roster()`, `task_note(task_id, text)`, `deliv()` a dict of
`path` (database), `home`, `now`, `protected`, `thumb` (path → data URL or None).
"""
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import bots
import deliverables as dv
import office_objects as oo
import sf_lang
import state_store as ss

PREVIEW_MAX = 256 * 1024
IMAGE_MAX_BYTES = 3 * 1024 * 1024
THUMB_MAX_BYTES = 1_500_000
THUMB_TIMEOUT = 2.0
STAT_MAX = 200                          # files checked for "gone" in one listing
OPENABLE = frozenset(e for t in ("pdf", "html", "md", "img", "pptx", "xlsx", "docx", "video") for e in dv.TYPE_EXTS[t])
IMG_MAGIC = {"png": b"\x89PNG", "jpg": b"\xff\xd8", "jpeg": b"\xff\xd8", "gif": b"GIF8", "webp": b"RIFF"}
IMG_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
TYPE_WORDS = {   # type → (en, zh)
    "pdf": ("PDF report", "報告"), "html": ("web page", "網頁"), "zip": ("archive", "壓縮檔"), "md": ("note", "便條"),
    "img": ("picture", "圖片"), "pptx": ("slides", "簡報"), "xlsx": ("sheet", "試算表"), "docx": ("document", "文件"),
    "video": ("video", "影片"), "folder": ("folder", "資料夾"), "url": ("link", "連結"), "file": ("file", "檔案"),
}
STATE_TITLES = (("new", "Undecided", "還沒決定"), ("kept", "Kept on the desk", "先放著"),
                ("candidate", "Maybe deliverables", "可能是交付物"), ("taken", "Taken away", "拿走過"))

DECLARATION = {
    "id": "deliverables",
    "title": {"en": "Deliverables", "zh": "交付物"},
    "subtitle": {"en": "Files and links the bots and tabs handed you. Originals are never touched. This computer.",
                 "zh": "bot 和分頁交給你的檔案、連結 · 只看不動原檔 · 這台電腦"},
    "furniture": "generic",
    "place": "button",
    "button": True,
    "badge_hint": {"en": "Deliverables nobody has opened yet", "zh": "還沒打開看過的交付物"},
    "search_hint": {"en": "Search titles and notes…", "zh": "搜尋標題和附註…"},
    "empty": {"en": "Nothing was handed to you yet.", "zh": "還沒有人交東西給你。"},
    "readonly_note": {"en": "Take = off the desk; the file stays and History keeps it.", "zh": "拿走＝從桌上清掉，原檔不刪，「拿走過」搜得到。"},
    "filters": [
        {"id": "owner", "label": {"en": "Whose", "zh": "誰的"}},
        {"id": "state", "label": {"en": "Status", "zh": "狀態"},
         "options": [["new", {"en": "Undecided", "zh": "還沒決定"}], ["kept", {"en": "Kept", "zh": "先放著"}],
                     ["taken", {"en": "Taken away", "zh": "拿走過"}], ["candidate", {"en": "Maybe deliverables", "zh": "可能是交付物"}]]},
        {"id": "type", "label": {"en": "Type", "zh": "類型"},
         "options": [[t, {"en": w[0].capitalize(), "zh": w[1]}] for t, w in TYPE_WORDS.items()]},
    ],
    "sorts": [["newest", {"en": "Newest first", "zh": "最新交來"}], ["title", {"en": "Name", "zh": "名稱"}]],
    "actions": [
        {"id": "open", "label": {"en": "Open", "zh": "打開"}, "primary": True},
        {"id": "keep", "label": {"en": "Keep on desk", "zh": "先放著"}},
        {"id": "ask_change", "label": {"en": "Ask {owner} to change…", "zh": "請 {owner} 改…"}, "needs_tab": True,
         "input": {"en": "What should change? One line", "zh": "要改什麼？一句話就好"}},
        {"id": "take", "label": {"en": "Take (clear)", "zh": "拿走（清掉）"}},
        {"id": "accept", "label": {"en": "Take it onto the desk", "zh": "收下"}, "primary": True},
        {"id": "dismiss", "label": {"en": "Ignore", "zh": "忽略"}},
        {"id": "dismiss_all", "label": {"en": "Ignore all", "zh": "全部忽略"}},
        {"id": "ask_logged", "label": {"en": "(note on the task)", "zh": "（記在任務上）"}},     # never offered: the page's follow-up to ask_change
    ],
}


_tl = threading.local()


def _use(ctx):
    """The page's language for this call (a hook runs on a thread of its own, so it is kept per thread)."""
    _tl.lang = ctx.lang


def _(en, zh):
    """A text for the page, in the page's language (what ctx.lang says); the lines sent to a bot use sf_lang.pick: the language
    agents are spoken to in is the computer's, not the page's."""
    return zh if getattr(_tl, "lang", "en") == "zh" else en


# ── small words ──────────────────────────────────────────────────────────────
def size_text(n) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def when_text(ts, now, zh) -> str:
    d, today = datetime.fromtimestamp(ts), datetime.fromtimestamp(now)
    days = (today.date() - d.date()).days
    hm = d.strftime("%H:%M")
    if days == 0:
        return (f"今天 {hm}" if zh else f"today {hm}")
    if days == 1:
        return (f"昨天 {hm}" if zh else f"yesterday {hm}")
    if 1 < days < 7:
        return ("週" + "一二三四五六日"[d.weekday()]) if zh else d.strftime("%a")
    return d.strftime("%Y-%m-%d")


def short_path(path, home=None) -> str:
    h = str(home or Path.home())
    p = str(path)
    return "~" + p[len(h):] if p.startswith(h.rstrip("/") + "/") else p


# ── what a store call needs (database, clock, home) ──────────────────────────
def _env(ctx) -> dict:
    d = ctx.deliv() or {}
    return {"path": d.get("path"), "home": d.get("home"), "now": d.get("now") or time.time(),
            "protected": tuple(d.get("protected") or ()), "thumb": d.get("thumb")}


def _owner_ids(row, live) -> tuple:
    """(group id, key the chat host talks to, open tab or bot?) for a row."""
    ref = dv.owner_ref(row, live)
    if ref.startswith("x:"):
        return "closed", ""
    return ref, ref


def _owner_name(ctx, row) -> str:
    if row["owner_kind"] == "bot":
        entry = (ctx.roster() or {}).get(row["owner_key"])
        if entry:
            try:
                return bots.label_of(row["owner_key"], entry)
            except Exception:
                pass
    else:
        sid = str(row["owner_key"]).partition("@")[0]
        lab = (ctx.tab_labels() or {}).get(sid)
        if lab and ctx.live_tabs() and str(ctx.live_tabs().get(sid, "")) == str(row["owner_key"]).partition("@")[2]:
            return lab
    return row["owner_label"] or row["owner_key"]


def _gone(row) -> bool:
    return row["kind"] == "file" and not os.path.exists(row["ref"])


# ── the object ───────────────────────────────────────────────────────────────
class Deliverables(oo.OfficeObject):
    declaration = DECLARATION

    def summary(self, ctx) -> dict:
        _use(ctx)
        e = _env(ctx)
        ov = dv.overview(ctx.live_tabs() or {}, path=e["path"])
        return {"badge": ov["new"], "by_owner": ov["by_owner"], "by_task": ov["by_task"]}

    # the list
    def list(self, ctx, query="", filters=None, sort="newest", offset=0, limit=oo.LIST_MAX) -> dict:
        _use(ctx)
        e, filters = _env(ctx), filters or {}
        live = ctx.live_tabs() or {}
        st = filters.get("state") or ""
        states = (st,) if st in dv.STATES else dv.STATES[:-1]                # everything but the ignored ones
        task = filters.get("task")
        try:
            task = int(task) if task not in (None, "") else None
        except ValueError:
            task = None
        rows = dv.listing(states=states, query=query, task_id=task, limit=oo.LIST_MAX * 2, path=e["path"])
        want_owner, want_type = filters.get("owner") or "", filters.get("type") or ""
        out, owners = [], {}
        checked = 0
        for r in rows:
            gid, key = _owner_ids(r, live)
            if want_owner and gid != want_owner:
                continue
            if want_type and r["type"] != want_type:
                continue
            owners.setdefault(gid, {"row": r, "n": 0})["n"] += 1
            gone = False
            if r["kind"] == "file" and r["state"] != "taken" and checked < STAT_MAX:
                checked += 1
                gone = _gone(r)
            out.append(self._row(ctx, r, gid, key, gone, e["now"]))
        if sort == "title":
            out.sort(key=lambda x: x["title"].casefold())
        groups = []
        if want_owner:                                                      # one person: grouped by what the person decided
            for state, en, zh in STATE_TITLES:
                n = sum(1 for x in out if x["state_id"] == state)
                if n:
                    groups.append({"id": state, "title": _(en, zh), "count": n})
            for x in out:
                x["group"] = x["state_id"]
        else:                                                               # everybody: grouped by owner, closed tabs last
            newest = {}
            for x in out:
                newest[x["group"]] = max(newest.get(x["group"], 0), x["mtime"])
            for gid, v in sorted(owners.items(), key=lambda kv: (kv[0] == "closed", -newest.get(kv[0], 0))):
                if gid == "closed":
                    groups.append({"id": gid, "title": _("Closed tabs", "已關閉的分頁"), "count": v["n"]})
                else:
                    groups.append({"id": gid, "title": _owner_name(ctx, v["row"]), "count": v["n"],
                                   "tag": _("tab", "分頁") if gid.startswith("t:") else ""})
        for x in out:
            x.pop("state_id", None)
        note = ""
        if st == "candidate" or (want_owner and not st):
            owner = ("bot", want_owner[2:]) if want_owner.startswith("b:") else None
            names = dv.skipped_names(owner, e["path"]) if owner else dv.all_skipped(e["path"]) if st == "candidate" else []
            if names:
                note = _(f"Passed over {len(names)} file(s) that look like keys or settings: {', '.join(names[:4])}. They are never listed or previewed.",
                         f"已自動略過 {len(names)} 個看起來像金鑰或設定的檔案：{'、'.join(names[:4])}。這類檔不會列出，也不會預覽。")
        return {"groups": groups, "items": out[offset:offset + limit], "total": len(out), "note": note}

    def _row(self, ctx, r, gid, key, gone, now) -> dict:
        zh = ctx.lang == "zh"
        sub = [size_text(r["size"]) if r["kind"] == "file" and r["type"] != "folder" else "", when_text(r["created_at"], now, zh)]
        if r["state"] == "candidate":
            sub = [size_text(r["size"]), _("mentioned ", "提到過 ") + when_text(r["created_at"], now, zh)]
        shown = dv.mask_line(r["ref"], 120, strict=False) if r["kind"] == "url" else ""
        sub = [x for x in sub if x]
        if r["kind"] == "url":
            sub = [shown] + sub[-1:]
        if r["note"]:
            sub.append(_("note", "附註") + " · " + oo.clip(r["note"], 40))
        tags, tone = [], ""
        fresh = r["state"] == "new" and r["seen_at"] is None
        if r["state"] == "new":
            tags.append({"id": "new" if fresh else "seen", "label": _("New", "新") if fresh else _("Seen", "看過"),
                         "tone": "ok" if fresh else "dim"})
        elif r["state"] == "kept":
            tags.append({"id": "kept", "label": _("Kept", "先放著"), "tone": "warn"})
        elif r["state"] == "candidate":
            tags.append({"id": "cand", "label": _("To take", "待收下"), "tone": "info"})
        elif r["state"] == "taken":
            tags.append({"id": "taken", "label": _("Taken", "拿走過"), "tone": "dim"})
            tone = "dim"
        if gone:
            tags.append({"id": "gone", "label": _("File is gone", "檔案不見了"), "tone": "bad"})
            tone = "bad"
        if r["flag"] == "secret":
            tags.append({"id": "secret", "label": _("looks like a key", "看起來含金鑰"), "tone": "warn"})
        return {"id": str(r["id"]), "group": gid, "title": r["title"], "subtitle": " · ".join(sub), "type": r["type"],
                "owner": _owner_name(ctx, r), "owner_key": key, "mtime": int(r["created_at"]), "size": 0, "fresh": fresh,
                "tone": tone, "tags": tags, "state_id": r["state"]}

    # one entry
    def item(self, ctx, item_id) -> dict:
        _use(ctx)
        e = _env(ctx)
        try:
            r = dv.get(int(str(item_id)), e["path"])
        except (ValueError, ss.NotFound):
            return {}
        live = ctx.live_tabs() or {}
        gid, key = _owner_ids(r, live)
        zh = ctx.lang == "zh"
        if r["kind"] == "file":                                          # files change: the verdict is taken again
            flag = "secret" if dv.secret_reason(r["ref"], e["home"], e["protected"]) else ""
            if flag != r["flag"] and os.path.exists(r["ref"]):
                _set_flag(r["id"], flag, e["path"])
                r["flag"] = flag
        gone = _gone(r)
        fresh = r["state"] == "new" and r["seen_at"] is None
        if r["state"] == "new":
            dv.mark_seen(r["id"], e["now"], path=e["path"])
        owner = _owner_name(ctx, r)
        shown_ref = dv.mask_line(r["ref"], 400, strict=False)
        chips, meta, links, md = [], [], [], []
        typ = TYPE_WORDS.get(r["type"], TYPE_WORDS["file"])
        meta.append([_("Type", "類型"), f"{r['type']} {_(typ[0], typ[1])}"])
        if r["kind"] == "file" and r["type"] != "folder" and not gone:
            meta.append([_("Size", "大小"), size_text(r["size"])])
        meta.append([_("Handed over", "交來"), when_text(r["created_at"], e["now"], zh)])
        if r["state"] == "candidate":
            chips.append({"text": _("Maybe a deliverable", "可能是交付物"), "tone": "info"})
            meta.append([_("Status", "狀態"), _("not on the desk yet", "還沒放上桌")])
        elif r["state"] == "new" and r["seen_at"] is not None:
            chips.append({"text": _("Seen", "看過"), "tone": "dim"})
        elif r["state"] == "kept":
            chips.append({"text": _("Kept on the desk", "先放著"), "tone": "warn"})
        elif r["state"] == "taken":
            chips.append({"text": _("Taken away", "拿走過"), "tone": "dim"})
        if gone:
            chips.append({"text": _("File is gone", "檔案不見了"), "tone": "bad"})
        if r["flag"] == "secret":
            chips.append({"text": _("Looks like it holds a key", "看起來含金鑰"), "tone": "warn"})
        image, before = "", 0
        if r["note"]:
            md.append("> **" + _("It says", "它說") + "**：" + r["note"] + "\n")
        if r["state"] == "candidate":
            md.append(self._why(r, zh))
        elif gone:
            md.append(_("The original was moved or deleted, so there is nothing to show. You can still take this entry away.",
                        "原檔被搬走或刪掉了，沒有東西可以預覽。這一列還是可以拿走。"))
        elif r["flag"] == "secret":
            md.append(_("This looks like it holds a key or a password, so it is not previewed. Opening it asks first.",
                        "這份看起來含有金鑰或密碼，所以不預覽。要打開時會先再問你一次。"))
        else:
            body, image, before = self._preview(r, e)
            if body:
                md.append(body)
            elif not image:
                md.append(self._no_preview(r))
        text = "\n".join(md)
        masked_n = max(0, text.count("<redacted>") - before)
        if masked_n:
            chips.append({"text": _(f"Masked {masked_n} suspected secret(s)", f"已遮蔽 {masked_n} 處疑似金鑰"), "tone": "warn"})
        if r["task_id"]:
            links.append({"label": _(f"Task #{r['task_id']}", f"任務 #{r['task_id']}"), "view": "board", "task": r["task_id"]})
        if key:
            links.append({"label": _(f"Go to {owner}'s tab", f"跳到 {owner} 的分頁"), "follow": key})
        acts, labels = self._actions(r, gone), {}
        if r["type"] == "html" and "open" in acts:
            labels["open"] = _("Open in browser", "用瀏覽器打開")
        elif r["type"] == "url" and "open" in acts:
            labels["open"] = _("Open in browser", "用瀏覽器打開")
        elif (r["type"] in ("zip", "folder") or r["type"] == "file") and "open" in acts:
            labels["open"] = _("Show in file manager", "在 Finder 顯示")
        return {"title": r["title"], "subtitle": f"{owner} / {short_path(shown_ref, e['home'])}", "markdown": text, "meta": meta,
                "chips": chips, "links": links, "actions": acts, "action_labels": labels, "image": image,
                "owner": owner, "owner_key": key, "refresh": fresh,
                "action_hints": {"take": _("Clears it off the desk; the file is not deleted.", "從桌上清掉，原檔不刪。")}}

    @staticmethod
    def _actions(r, gone) -> list:
        st = r["state"]
        if st == "candidate":
            return ["accept", "dismiss", "dismiss_all"]
        acts = [] if gone else ["open"]
        if st == "new":
            acts += ["keep"]
        if st in ("new", "kept"):
            acts += ["ask_change", "take"]
        return acts

    @staticmethod
    def _why(r, zh) -> str:
        cause = [_("The reply wrote the full path, and the file really exists", "回覆裡寫了完整路徑，而且檔案真的存在"),
                 _("Its type is a document, picture, archive or web page", "類型是文件、圖片、壓縮檔或網頁"),
                 _("It was saved or changed within the last two hours", "是這兩小時內新存或改過的"),
                 _("It is not in a cache, dependency or system folder", "不在暫存、快取、系統資料夾裡")]
        out = []
        if r["source"]:
            out += [_("**It was mentioned in this sentence**", "**它是在這句話裡提到的**"), "", "> " + r["source"], ""]
        out += [_("**Why it was guessed**", "**為什麼猜它是交付物**"), ""] + [f"✓ {c}\n" for c in cause]
        out += ["", _("**After you choose**", "**你按了之後**"), "",
                _("- Take it: it goes onto the owner's desk as new", "- 收下：桌上多一份公文，標「新」，檔案名稱照原樣"),
                _("- Ignore: nothing happens; it is not asked again unless the file changes", "- 忽略：什麼都不多；同一個檔案不再問（除非它又被改過）")]
        return "\n".join(out)

    @staticmethod
    def _no_preview(r) -> str:
        t = r["type"]
        if t == "html":
            return _("A web page is never run in this window. Use **Open in browser**.", "網頁檔不會在這個視窗裡執行，請按「用瀏覽器打開」。")
        if t == "url":
            return dv.mask(r["ref"]) + "\n\n" + _("Opens in your browser.", "用瀏覽器打開。")
        if t in ("zip", "folder"):
            return _("Shown in the file manager: nothing is unpacked here.", "在 Finder 顯示，這裡不會解開它。")
        if t == "img":
            return _("This picture is too big to show here. Open it to see it.", "這張圖太大，這裡不顯示。打開看。")
        if t == "pdf":
            return _("No thumbnail here. Open it to read it in your PDF viewer.", "這裡沒有縮圖。打開用 PDF 程式看。")
        return _("No preview for this kind of file. Open it with the button below.", "這種檔案沒有預覽，請按下面的「打開」。")

    # the preview of an entry: (markdown, image data URL, how many <redacted> the body already had)
    def _preview(self, r, e):
        t = r["type"]
        if r["kind"] == "url" or t in ("html", "zip", "folder", "video", "pptx", "xlsx", "docx", "file"):
            if t == "xlsx" and dv.ext_of(r["ref"]) == "csv":
                return self._text(r, "csv")
            return "", "", 0
        if t == "md":
            return self._text(r, dv.ext_of(r["ref"]))
        if t == "img":
            return "", self._image(r), 0
        if t == "pdf":
            fn = e["thumb"] or pdf_thumb
            try:
                return "", fn(r["ref"]) or "", 0
            except Exception:
                return "", "", 0
        return "", "", 0

    @staticmethod
    def _text(r, ext):
        try:
            with open(r["ref"], "rb") as f:
                data = f.read(PREVIEW_MAX)
        except OSError:
            return "", "", 0
        if b"\0" in data:
            return "", "", 0
        text = dv.mask(data.decode("utf-8", "replace"))
        if ext in ("md", "markdown"):
            return text, "", 0
        fence = "```"
        while fence in text:
            fence += "`"
        return f"{fence}\n{text}\n{fence}", "", 0

    @staticmethod
    def _image(r) -> str:
        ext = dv.ext_of(r["ref"])
        if ext not in IMG_MIME:
            return ""
        try:
            if os.path.getsize(r["ref"]) > IMAGE_MAX_BYTES:
                return ""
            with open(r["ref"], "rb") as f:
                data = f.read(IMAGE_MAX_BYTES + 1)
        except OSError:
            return ""
        if not data.startswith(IMG_MAGIC[ext]):
            return ""
        return f"data:{IMG_MIME[ext]};base64," + base64.b64encode(data).decode("ascii")

    # the person's actions
    def action(self, ctx, item_id, action_id, args) -> dict:
        _use(ctx)
        e, args = _env(ctx), args or {}
        try:
            r = dv.get(int(str(item_id)), e["path"])
        except (ValueError, ss.NotFound):
            return {"ok": False, "message": _("That entry is gone.", "這一筆已經不在了。")}
        live = ctx.live_tabs() or {}
        _gid, key = _owner_ids(r, live)
        try:
            if action_id == "open":
                return self._open(r, args, e)
            if action_id in ("keep", "take", "accept", "dismiss"):
                dv.move(r["id"], action_id, e["now"], e["path"])
                return {"ok": True, "refresh": True, "message": {
                    "keep": _("Kept on the desk.", "先放著了。"), "take": _("Taken away. History keeps it.", "拿走了，「拿走過」找得到。"),
                    "accept": _("It is on the desk now.", "已放上桌。"), "dismiss": _("Ignored.", "已忽略。")}[action_id]}
            if action_id == "dismiss_all":
                n = dv.dismiss_candidates((r["owner_kind"], r["owner_key"]), e["now"], e["path"])
                return {"ok": True, "refresh": True, "message": _(f"Ignored {n}.", f"已忽略 {n} 件。")}
            if action_id == "ask_change":
                return self._ask(ctx, r, key, args)
            if action_id == "ask_logged":
                if r["task_id"] and str(args.get("text") or "").strip():
                    ctx.task_note(r["task_id"], sf_lang.pick("Asked for a change to “", "請改「") + r["title"] + sf_lang.pick("”: ", "」：")
                                  + dv.mask_line(args["text"], 300, strict=False))
                return {"ok": True}
        except ValueError:
            return {"ok": False, "refresh": True, "message": _("That is no longer possible for this entry.", "這一筆現在不能這樣做了。")}
        return {"ok": False}

    @staticmethod
    def _open(r, args, e) -> dict:
        if r["kind"] != "url" and not os.path.exists(r["ref"]):
            return {"ok": False, "message": _("The file is gone.", "檔案不見了。")}
        flagged = r["flag"] == "secret" or (r["kind"] == "file" and bool(dv.secret_reason(r["ref"], e["home"], e["protected"])))
        if flagged and str(args.get("confirmed") or "") not in ("1", "true", "True"):
            return {"ok": True, "confirm": {"text": _("This looks like it holds a key or a password. Open it anyway?",
                                                     "這份看起來含有金鑰或密碼，還是要打開嗎？"),
                                            "label": _("Open anyway", "還是打開"), "args": {"confirmed": "1"}}}
        if r["state"] == "new":
            dv.mark_seen(r["id"], e["now"], path=e["path"])
        if r["kind"] == "url":
            return {"ok": True, "url": r["ref"], "refresh": True}
        if r["type"] in ("zip", "folder", "file") or dv.ext_of(r["ref"]) not in OPENABLE:
            return {"ok": True, "reveal": r["ref"], "refresh": True}
        return {"ok": True, "open": r["ref"], "refresh": True}

    @staticmethod
    def _ask(ctx, r, key, args) -> dict:
        words = dv.mask_line(args.get("text") or "", 300, strict=False)
        if not words:
            return {"ok": False, "message": _("Write what should change first.", "先寫下要改什麼。")}
        if not key:
            return {"ok": False, "message": _("It has no open tab: wake it first.", "它現在沒有開著的分頁，先叫醒它。")}
        title = dv.mask_line(r["title"], 120, strict=False)
        where = dv.mask_line(r["ref"], 300, strict=False)
        # ONE form, in Chinese whatever the page's language is (the board's 「（看板）…」 lines are the same): a bot is told by protocol
        text = f"（交付物）請改{title}：{words}；檔案在{where}"
        tell = {"to": key, "text": text}
        if r["task_id"]:
            tell["after"] = {"action": "ask_logged", "args": {"text": words}}
        return {"ok": True, "tell": tell}


def _set_flag(did, flag, path) -> None:
    con = ss._open(path)
    try:
        con.execute("UPDATE deliverables SET flag=? WHERE id=?", (flag, int(did)))
    finally:
        con.close()


def pdf_thumb(path, timeout=THUMB_TIMEOUT):
    """The first page of a PDF as a small PNG data URL (macOS: the system's `sips`, time-boxed), else None. The file is
    only rendered, never read as text."""
    if sys.platform != "darwin" or not shutil.which("sips") or not os.path.isfile(path):
        return None
    fd, out = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(["/usr/bin/sips", "-s", "format", "png", "--resampleHeight", "560", path, "--out", out],
                       capture_output=True, timeout=timeout, check=True)
        data = Path(out).read_bytes()
        if not data.startswith(b"\x89PNG") or len(data) > THUMB_MAX_BYTES:
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass
