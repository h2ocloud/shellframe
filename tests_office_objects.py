#!/usr/bin/env python3
"""The office-object extension point (office_objects.py) and its js_api mixin (api_office_objects.py).

  - masking keeps lines (Markdown stays Markdown) and hides keys, `password: x`, `user:pass@` URLs, private
    key blocks; a long skill name in a title is not masked, the same run in a body is
  - a declaration is normalized (unknown furniture → generic, bad id dropped, {en, zh} resolved to one language)
  - every answer is cleaned: capped, masked, the page's language picks the words, an action the object did not
    declare is refused, a link that points nowhere is dropped, a `tell` is one line
  - a hook that raises or hangs makes only its own call fail (`unavailable` / `timeout`), the others work
  - a plugin whose manifest has `office_objects` is an object; a built-in id is not taken over; a plugin with no
    hooks answers empty
  - the mixin: refused while the office is off, `open` goes to the app's opener and never reaches the page,
    an `item_id` the object does not know is "gone"
Demo data only. Run: .venv/bin/python tests_office_objects.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-office-objects-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import office_objects as oo  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


print("masking")
text = ("# Demo notes\n\n- keep this line\n- password: hunter22\n- token = abcd1234efgh\n"
        "- repo https://demouser:s3cret@git.example.test/x.git\n- key sk-demo1234567890abcdef\n"
        "- Authorization: Bearer abc.def.ghijklmnop\n- aws AKIAIOSFODNN7EXAMPLE\n"
        "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEYBODYFORTESTSONLYxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n-----END RSA PRIVATE KEY-----\n"
        "after the block\n" + "-" * 40 + "\nplain line with a/path/to/file.md\n")
red = oo.redact_text(text)
check("line breaks survive", red.count("\n") == text.count("\n") - 2, red)
for secret in ("hunter22", "abcd1234efgh", "s3cret", "sk-demo1234567890abcdef", "abc.def.ghijklmnop",
               "AKIAIOSFODNN7EXAMPLE", "FAKEKEYBODYFORTESTS"):
    check(f"hides {secret[:8]}…", secret not in red, red)
check("ordinary text, a rule of dashes and a path stay", "keep this line" in red and "-" * 40 in red
      and "a/path/to/file.md" in red and "after the block" in red)
long_name = "demo-skill-with-a-rather-long-folder-name-for-writing"
check("a long name is kept in a title, masked as a long run in a body",
      oo.redact_text(long_name, strict=False) == long_name and oo.redact_text(long_name) == "<redacted>")
check("a url without a password is untouched", "https://example.test/a" in oo.redact_text("see https://example.test/a"))

print("declaration")
d = oo.normalize_declaration({"id": "demo-desk", "title": {"en": "Desk", "zh": "桌"}, "furniture": "sofa", "place": "moon",
                              "filters": [{"id": "who", "label": {"zh": "誰的"}}, {"id": "BAD id"}],
                              "actions": [{"id": "go", "label": {"en": "Go", "zh": "去"}}, {"label": "no id"}]}, "zh")
check("title in the page's language, unknown furniture → generic, bad place → lobby:wall",
      d["title"] == "桌" and d["furniture"] == "generic" and d["place"] == "lobby:wall", d)
check("a filter or action without a usable id is dropped", [f["id"] for f in d["filters"]] == ["who"]
      and [a["id"] for a in d["actions"]] == ["go"], d)
check("no id, a bad id: no declaration", oo.normalize_declaration({"title": "x"}) is None
      and oo.normalize_declaration({"id": "Bad Id"}) is None and oo.normalize_declaration("x") is None)
check("zone:<id> is a place", oo.normalize_declaration({"id": "a", "place": "zone:ops"})["place"] == "zone:ops")

print("cleaning what a hook returns")
lst = oo.clean_list({"groups": [{"id": "g", "title": "G"}, {"nope": 1}],
                     "items": [{"id": "i" * 500, "title": "password: hunter22", "mtime": "5", "tags": [{"label": "x", "tone": "zzz"}]},
                               {"title": "no id"}] + [{"id": str(n)} for n in range(600)]})
check("items capped, an item without an id dropped, a tone outside the set emptied, an id cut",
      len(lst["items"]) == oo.LIST_MAX and len(lst["items"][0]["id"]) <= 300 and lst["items"][0]["tags"][0]["tone"] == ""
      and len(lst["groups"]) == 1, len(lst["items"]))
check("a title is masked", "hunter22" not in lst["items"][0]["title"], lst["items"][0]["title"])
acts = [{"id": "open", "label": "Open"}]
it = oo.clean_item({"title": "T", "markdown": "line\npassword: abc123\n", "actions": ["open", "evil"],
                    "links": [{"label": "to board", "view": "board", "task": "7"}, {"label": "x", "object": "Bad Id"},
                              {"label": "shelf", "object": "bookshelf", "filters": {"owner": "x"}}, {"label": "go", "view": "moon"}],
                    "meta": [["k", "token: zzz999"]]}, acts)
check("only declared actions survive", it["actions"] == ["open"], it["actions"])
check("the body keeps its lines and is masked", "abc123" not in it["markdown"] and it["markdown"].startswith("line\n"), it["markdown"])
check("meta values are masked", "zzz999" not in it["meta"][0][1], it["meta"])
check("links: view and object kept, an unknown view or a bad object id dropped",
      [(l.get("view"), l.get("object")) for l in it["links"]] == [("board", None), (None, "bookshelf")], it["links"])
big = oo.clean_item({"markdown": "x " * 100000})
check("a body is capped", len(big["markdown"]) <= oo.MARKDOWN_MAX)
ac = oo.clean_action({"ok": True, "tell": {"to": "b:demo", "text": "line one\nline two  password: hunter22"}, "open": "/srv/demo/a.md",
                      "go": {"settings": "demo"}})
check("a tell is one masked line", "\n" not in ac["tell"]["text"] and "hunter22" not in ac["tell"]["text"], ac)
check("a go to a bot's settings is kept", ac["go"] == {"settings": "demo"}, ac)
check("a tell with no text or no target is dropped", "tell" not in oo.clean_action({"tell": {"to": "b:x", "text": "  "}})
      and "tell" not in oo.clean_action({"tell": {"text": "hi"}}))
check("summary: a badge is a non-negative number", oo.clean_summary({"badge": -4})["badge"] == 0
      and oo.clean_summary({"badge": "3"})["badge"] == 3 and oo.clean_summary("x")["badge"] == 0)

print("the registry")


class Demo(oo.OfficeObject):
    declaration = {"id": "demo", "title": "Demo", "actions": [{"id": "act", "label": "Act"}]}
    calls = 0

    def summary(self, ctx):
        return {"badge": 4}

    def list(self, ctx, query="", filters=None, sort="", offset=0, limit=200):
        return {"items": [{"id": "a", "title": "Alpha"}], "groups": []}

    def item(self, ctx, item_id):
        return {"title": item_id, "markdown": "hi", "actions": ["act"]} if item_id == "a" else {}

    def action(self, ctx, item_id, action_id, args):
        return {"ok": True, "message": "done"}


class Broken(oo.OfficeObject):
    declaration = {"id": "broken", "title": "Broken", "actions": [{"id": "act", "label": "Act"}]}

    def summary(self, ctx):
        raise RuntimeError("boom")

    def list(self, ctx, *a, **k):
        raise RuntimeError("boom")

    def item(self, ctx, item_id):
        time.sleep(5)


class FakePlugin:
    class manifest:
        raw = {"office_objects": [{"id": "plug", "title": {"en": "Plug", "zh": "插"}, "furniture": "mailbox",
                                   "actions": [{"id": "act", "label": "Act"}]},
                                  {"id": "demo", "title": "Takeover"}]}

    def office_summary(self, obj_id):
        return {"badge": 2}

    def office_list(self, obj_id, query, filters, sort, offset, limit):
        return {"items": [{"id": "p1", "title": query or "all"}]}


class BarePlugin:
    class manifest:
        raw = {"office_objects": [{"id": "bare", "title": "Bare"}]}


oo.HOOK_TIMEOUT = 0.3
reg = oo.Registry([Demo(), Broken()], lambda: [FakePlugin(), BarePlugin()])
ctx = oo.Ctx("en")
check("built-ins first, a plugin object added, a plugin cannot take a built-in's id",
      list(reg.objects()) == ["demo", "broken", "plug", "bare"] and isinstance(reg.objects()["demo"], Demo))
desc = {d["id"]: d for d in reg.describe(ctx, "en")}
check("describe: a badge from summary; a failing summary shows none, the others still do",
      desc["demo"]["badge"] == 4 and desc["broken"]["badge"] == 0 and desc["plug"]["badge"] == 2, desc)
check("a plugin object's furniture and language", desc["plug"]["furniture"] == "mailbox"
      and reg.describe(ctx, "zh")[2]["title"] == "插")
r = reg.list("demo", ctx, "en")
check("list: cleaned, success", r["success"] and r["items"][0]["title"] == "Alpha", r)
check("a plugin's list gets the query", reg.list("plug", ctx, "en", "typed")["items"][0]["title"] == "typed")
r = reg.list("broken", ctx, "en")
check("a hook that raises: unavailable, in words", r["success"] is False and r["code"] == "unavailable" and r["message"], r)
t0 = time.time()
r = reg.item("broken", ctx, "en", "a")
check("a hook that hangs: timeout after the limit, not after its sleep", r["code"] == "timeout" and time.time() - t0 < 2, r)
check("the others still answer", reg.item("demo", ctx, "en", "a")["item"]["actions"] == ["act"])
check("an entry the object does not know is gone", reg.item("demo", ctx, "zh", "zzz")["code"] == "not_found")
check("an unknown object", reg.list("nope", ctx, "en")["code"] == "unknown")
check("an action the declaration does not list is refused, the object never sees it",
      reg.action("demo", ctx, "en", "a", "delete", {})["code"] == "bad_action")
check("a declared action runs", reg.action("demo", ctx, "en", "a", "act", {}) == {"ok": True, "message": "done", "success": True})
check("a plugin with no hooks answers empty", reg.list("bare", ctx, "en")["items"] == []
      and reg.item("bare", ctx, "en", "x")["code"] == "not_found")
check("the Chinese words come back in Chinese", "找不到" not in reg.list("nope", ctx, "zh")["message"]
      and "沒有" in reg.list("nope", ctx, "zh")["message"])
check("Ctx: a provider the host did not give answers None; t() follows the language",
      oo.Ctx("zh-TW").t("a", "b") == "b" and oo.Ctx("en").roster() is None)

print("the mixin")
import api_office_objects as aoo  # noqa: E402


class Host(aoo.OfficeObjectsApiMixin):
    on = True
    opened = []

    def _office_on(self):
        return self.on

    def _oo_registry(self):
        return oo.Registry([Demo()], lambda: [])

    def open_local_file(self, path):
        self.opened.append(path)
        return json.dumps({"success": True})


class DemoOpen(Demo):
    def action(self, ctx, item_id, action_id, args):
        return {"ok": True, "open": "/srv/demo/notes.md"}


h = Host()
h._oo_registry = lambda: oo.Registry([DemoOpen()], lambda: [])
h._oo_ctx = lambda lang: oo.Ctx(lang)
res = json.loads(h.office_object_action("demo", "a", "act", "{}", "en"))
check("an `open` is handed to the app's opener and the path is not in the answer",
      h.opened == ["/srv/demo/notes.md"] and "open" not in res and "/srv/demo" not in json.dumps(res) and res["success"], res)
h.on = False
off = json.loads(h.office_objects("en"))
check("everything is refused while the office is off", off["success"] is False and off["objects"] == []
      and json.loads(h.office_object_list("demo", "{}", "en"))["code"] == "off"
      and json.loads(h.office_object_item("demo", "a", "en"))["code"] == "off")
h.on = True
got = json.loads(h.office_objects("zh"))
check("office_objects: the declarations with their badge", got["success"] and got["objects"][0]["badge"] == 4, got)
check("bad arguments are tolerated", json.loads(h.office_object_list("demo", "not json", "en"))["success"])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
