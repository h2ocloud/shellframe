#!/usr/bin/env python3
"""The bookshelf and the personnel cabinet (office_bookshelf.py, office_personnel.py), against a temp folder.

Bookshelf
  - the red number: bots whose MEMORY.md is MORE than 80 % of bots.MEMORY_CAP characters (4,801 and up),
    counted in characters (a CJK memory of 4,801 characters is 14 KB; 1,700 of them are 5 KB and not full);
    a bot with memory off, an external bot and an empty file are not counted
  - the list: a group per bot (the primary first) and the shared skills; filters (whose, type), a search that
    reaches the text, sorts; a skill is its SKILL.md and the .md files it links to, nothing else of its folder
  - what is linked: relative, inside the skill's folder, .md only: `..`, an absolute path, a URL, a script, a
    folder link that leaves the folder, a non-.md file are not on the shelf and cannot be asked for by id
  - an entry is masked, its front matter is left out; an id from the page is only looked up
  - actions: open in the editor (the path is the object's), ask the bot (one line, to its key; a shared skill has
    no owner to ask)
Personnel
  - one file per bot: duty, CLI and model, awake / asleep (tab), primary, local model (pi on a local provider, a
    local-server provider name; pi on a public host is not), external (nothing of its endpoint)
  - its open tasks (by role, label or former name), the schedules it owns, links to the whiteboard card, the
    calendar and the bookshelf's filter; the one action opens the bot's settings
Demo data only (made-up names, paths under a temp folder). Run: .venv/bin/python tests_office_shelf.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-office-shelf-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import bots  # noqa: E402
import office_bookshelf as shelf  # noqa: E402
import office_objects as oo  # noqa: E402
import office_personnel as staff  # noqa: E402
import sf_lang  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def prompt_lang(lang):
    os.environ["SHELLFRAME_PROMPT_LANG"] = lang
    if hasattr(sf_lang.prompt_lang, "cache_clear"):
        sf_lang.prompt_lang.cache_clear()


ROSTER = {
    "chief": {"label": "示範秘書長", "responsibility": "統籌、分派工作", "cmd": "claude", "model": "opus",
              "bot": {"primary": True, "memory": True}},
    "eng": {"label": "示範工程師", "responsibility": "寫程式、跑測試", "cmd": "claude", "former_names": ["示範舊工程師"],
            "bot": {"memory": True}},
    "local": {"label": "示範地端助理", "responsibility": "本機小模型處理雜事", "cmd": "pi --provider demo-local",
              "bot": {"memory": True}},
    "cloudpi": {"label": "示範雲端pi", "responsibility": "pi on a public host", "cmd": "pi --provider demo-cloud",
                "bot": {"memory": True}},
    "mute": {"label": "示範無記憶", "responsibility": "記憶關閉", "cmd": "codex", "bot": {"memory": False}},
    "outside": {"label": "示範外部", "responsibility": "雲端的外部 bot", "cmd": "",
                "bot": {"external": {"kind": "webhook", "url": "https://hooks.example.test/secret-path", "auth": "tok-demo-123456"}}},
}
SKILLS_A, SKILLS_B = TMP / "home" / ".claude" / "skills", TMP / "home" / ".codex" / "skills"
EVIL = TMP / "outside-the-skill"
write(EVIL / "other.md", "# not part of the skill\n")
write(TMP / "secret.md", "# a secret file\n")


def ctx(lang="en", **over):
    base = dict(roster=lambda: ROSTER, skill_roots=lambda: [SKILLS_A, SKILLS_B],
                bot_states=lambda: {"chief": {"state": "awake", "sid": "s1", "position": 3}},
                open_tasks=lambda: [], schedules=lambda: [], groups=lambda: {}, pi_hosts=lambda: {})
    base.update(over)
    return oo.Ctx(lang, **base)


# ── the shelf's contents ─────────────────────────────────────────────────────
write(bots.memory_file("chief"), "# 示範秘書長記憶\n\n- 發版前先跑測試\n- password: hunter22\n- key sk-demo1234567890abcdef\n")
write(bots.memory_file("eng"), "x" * 4800)                                   # exactly 80 %: not "more than"
write(bots.memory_file("local"), "y" * 4801)
write(bots.memory_file("cloudpi"), "字" * 4801)                               # CJK: 14 KB, 4,801 characters
write(bots.memory_file("mute"), "z" * 5900)                                  # memory off: not counted
for n in range(7):
    write(bots.sessions_dir("chief") / f"20261101-10000{n}-s{n}.md", f"summary {n}\n")
write(bots.skills_dir("chief") / "own-skill" / "SKILL.md", "---\nname: own-skill\ndescription: Demo own skill\n---\n# Own\nSee [notes](notes.md).\n")
write(bots.skills_dir("chief") / "own-skill" / "notes.md", "# own notes\n")
write(SKILLS_A / "demo-release" / "SKILL.md",
      "---\nname: demo-release\ndescription: Release steps\n---\n# Release\n"
      "[checklist](checklist.md) [again](./checklist.md#top) [deep](docs/deep.md?x=1) "
      "[up](../../../../secret.md) [abs](/etc/passwd.md) [url](https://example.test/a.md) [file](file:///x.md) "
      "[script](run.sh) [text](notes.txt) [self](SKILL.md) [home](~/a.md) [missing](nothere.md) [leave](linked-out/other.md)\n"
      "token = tok-demo-abcdef123456\n")
write(SKILLS_A / "demo-release" / "checklist.md", "# Checklist\n- run tests\n")
write(SKILLS_A / "demo-release" / "docs" / "deep.md", "# Deep\n")
write(SKILLS_A / "demo-release" / "run.sh", "#!/bin/sh\nexport TOKEN=ghp_demoabcdefghijkl\n")
write(SKILLS_A / "demo-release" / "notes.txt", "not markdown\n")
(SKILLS_A / "demo-release" / "linked-out").symlink_to(EVIL)
write(SKILLS_B / "demo-release" / "SKILL.md", "---\nname: demo-release\n---\nthe Codex copy\n")
write(SKILLS_B / "demo-only-codex" / "SKILL.md", "# only here\n")

print("the red number")
sh = shelf.Bookshelf()
check("more than 80 % of MEMORY_CAP: 4,800 is not, 4,801 is; CJK counted in characters; memory-off not counted",
      sh.summary(ctx()) == {"badge": 2}, sh.summary(ctx()))
write(bots.memory_file("cloudpi"), "字" * 1700)
check("1,700 CJK characters (5 KB) are not near full", sh.summary(ctx()) == {"badge": 1}, sh.summary(ctx()))
write(bots.memory_file("cloudpi"), "字" * 4801)
check("a bot that lives outside has no memory to count", sh.summary(ctx(roster=lambda: {"outside": ROSTER["outside"]})) == {"badge": 0})
check("no bots, no number", sh.summary(ctx(roster=lambda: {})) == {"badge": 0})

print("the list")
lst = sh.list(ctx())
titles = {i["id"]: i for i in lst["items"]}
check("groups: the primary bot first, then the others, then the shared skills",
      [g["id"] for g in lst["groups"]][:2] == ["chief", "eng"] and lst["groups"][-1]["id"] == "shared", lst["groups"])
check("a bot's memory has its length and the cap; near full is tagged", titles["mem:local"]["size"] == 4801
      and titles["mem:local"]["limit"] == bots.MEMORY_CAP and titles["mem:local"]["tags"][0]["id"] == "near-full"
      and not titles["mem:eng"]["tags"], titles["mem:local"])
check("only the latest five session summaries per bot", sum(1 for i in lst["items"] if i["type"] == "session") == 5)
check("own skill and its linked note, shared skills with the programs that read them",
      "skill:own.chief:own-skill" in titles and "doc:own.chief:own-skill:notes.md" in titles
      and [t["label"] for t in titles["skill:shared:demo-release"]["tags"]] == ["Claude Code", "Codex"]
      and [t["label"] for t in titles["skill:shared:demo-only-codex"]["tags"]] == ["Codex"], sorted(titles))
docs = sorted(i for i in titles if i.startswith("doc:shared:demo-release:"))
check("a skill lists SKILL.md and only the .md files it links to inside its folder",
      docs == ["doc:shared:demo-release:checklist.md", "doc:shared:demo-release:docs/deep.md"], docs)
check("no script, no .txt, nothing outside the folder is on the shelf (not even through a folder link)",
      not any("run.sh" in i or "notes.txt" in i or "other.md" in i or "secret" in i or "passwd" in i for i in titles), sorted(titles))
check("filter: whose", {i["group"] for i in sh.list(ctx(), filters={"owner": "chief"})["items"]} == {"chief"})
check("filter: type", {i["type"] for i in sh.list(ctx(), filters={"type": "memory"})["items"]} == {"memory"})
check("search reaches the text", [i["id"] for i in sh.list(ctx(), query="run tests")["items"]] == ["doc:shared:demo-release:checklist.md"])
check("search reaches the title", any(i["id"] == "mem:chief" for i in sh.list(ctx(), query="MEMORY")["items"]))
by_name = [i["title"].casefold() for i in sh.list(ctx(), filters={"owner": "shared"}, sort="title")["items"]]
check("sort by name", by_name == sorted(by_name) and len(by_name) >= 3, by_name)
check("the Chinese words", sh.list(ctx("zh"))["groups"][-1]["title"] == "共用技能")
check("items carry the bot's key for the ask button",
      titles["mem:eng"]["owner_key"] == "b:eng" and titles["skill:shared:demo-release"]["owner_key"] == "")

print("an entry")
it = sh.item(ctx(), "mem:chief")
check("the object returns the file's text; the host masks it", "hunter22" in it["markdown"])
clean = oo.clean_item(it, shelf.DECLARATION["actions"])
check("…masked once it goes through the host's cleaning", "hunter22" not in clean["markdown"]
      and "sk-demo1234567890abcdef" not in clean["markdown"] and "發版前先跑測試" in clean["markdown"], clean["markdown"])
check("the length line and the actions", any(m[0] == "Length" and m[1].endswith("/ 6,000 chars") for m in clean["meta"])
      and clean["actions"] == ["open_editor", "ask_update"], clean["meta"])
sk = oo.clean_item(sh.item(ctx(), "skill:shared:demo-release"), shelf.DECLARATION["actions"])
check("a skill: front matter left out, its token masked, links to its documents, no ask button (nobody owns it)",
      "description:" not in sk["markdown"] and "tok-demo-abcdef123456" not in sk["markdown"]
      and [l["label"] for l in sk["links"]] == ["checklist.md", "docs/deep.md"] and sk["actions"] == ["open_editor"], sk)
check("a linked document links back to its SKILL.md",
      sh.item(ctx(), "doc:shared:demo-release:checklist.md")["links"][0]["item"] == "skill:shared:demo-release")
for bad in ("doc:shared:demo-release:../../../secret.md", "doc:shared:demo-release:run.sh", "doc:shared:demo-release:linked-out/other.md",
            "mem:../../etc", "sess:chief:../../secret.md", "skill:shared:../x", "/etc/passwd", "", "mem:outside", "mem:ghost"):
    check(f"an id from the page that is not on the shelf gets nothing: {bad!r}",
          sh.item(ctx(), bad) == {} and sh.action(ctx(), bad, "open_editor", {})["ok"] is False)

print("actions")
a = sh.action(ctx(), "mem:eng", "open_editor", {})
check("open in the editor: the object's own path", a == {"ok": True, "open": str(bots.memory_file("eng")), "message": "Opened in your editor."}, a)
a = sh.action(ctx(), "mem:eng", "ask_update", {})
check("ask the bot to update: one line to its key", a["tell"]["to"] == "b:eng"
      and a["tell"]["text"].startswith("(Bookshelf) Please update your MEMORY.md") and "\n" not in a["tell"]["text"], a)
prompt_lang("zh")
a = sh.action(ctx(), "mem:eng", "ask_update", {})
check("…in the OS language for the bot: 「（書櫃）請更新你的 MEMORY.md：…」",
      a["tell"]["text"].startswith("（書櫃）請更新你的 MEMORY.md："), a["tell"]["text"])
prompt_lang("en")
check("a shared skill has nobody to ask; a session summary neither",
      sh.action(ctx(), "skill:shared:demo-release", "ask_update", {})["ok"] is False
      and sh.action(ctx(), next(i for i in titles if i.startswith("sess:")), "ask_update", {})["ok"] is False)
check("own skill: ask its bot", sh.action(ctx(), "skill:own.chief:own-skill", "ask_update", {})["tell"]["to"] == "b:chief")

# ── the cabinet ──────────────────────────────────────────────────────────────
print("the cabinet: list")
pe = staff.Personnel()
HOSTS = {"demo-local": "192.168.1.20", "demo-cloud": "api.example.test"}
lst = pe.list(ctx(pi_hosts=lambda: HOSTS))
rows = {i["id"]: i for i in lst["items"]}
tags = {k: [t["id"] for t in v["tags"]] for k, v in rows.items()}
check("every bot is a file; the primary is marked; the external one is marked and has no key to talk to",
      len(rows) == 6 and "primary" in tags["bot:chief"] and "external" in tags["bot:outside"]
      and rows["bot:outside"]["owner_key"] == "", tags)
check("a pi bot on a local provider is a local model; on a public host it is not; a Claude bot is not",
      "local" in tags["bot:local"] and "local" not in tags["bot:cloudpi"] and "local" not in tags["bot:chief"], tags)
check("groups: by CLI name unless the office has a group for the bot",
      {g["id"] for g in lst["groups"]} >= {"Claude Code", "pi", "Codex"}, lst["groups"])
check("a custom group wins", pe.list(ctx(groups=lambda: {"eng": "Build team"}))["groups"][0]["id"] == "Build team")
check("state: awake / asleep / external",
      (rows["bot:chief"]["type"], rows["bot:eng"]["type"], rows["bot:outside"]["type"]) == ("awake", "asleep", "external"))
check("filter by state and search by duty", [i["id"] for i in pe.list(ctx(), filters={"state": "awake"})["items"]] == ["bot:chief"]
      and [i["id"] for i in pe.list(ctx(), query="測試")["items"]] == ["bot:eng"])
check("provider names of local servers count, a model prefix too",
      staff.runs_local("opencode --model ollama/demo") and staff.runs_local("x", "lmstudio/demo")
      and not staff.runs_local("claude --model opus") and not staff.runs_local("opencode --model openai/gpt"))
check("pi with nothing to look at counts as local; on a loopback host too; on a public host it does not",
      staff.runs_local("pi") and staff.runs_local("pi --provider p", "", {"p": "127.0.0.1"})
      and not staff.runs_local("pi --provider p", "", {"p": "api.example.test"}))
mj = TMP / "models.json"
mj.write_text(json.dumps({"providers": {"demo-local": {"baseUrl": "http://localhost:11434/v1", "apiKey": "key-must-not-be-read",
                                                          "models": [{"id": "m"}]}}}), encoding="utf-8")
check("pi's models.json: only a provider's host is read", staff.pi_provider_hosts(str(mj)) == {"demo-local": "localhost"})

print("the cabinet: a file")
TASKS = [{"id": 31, "title": "示範方案 A 或 B", "status": "waiting", "owner": "示範工程師"},
         {"id": 14, "title": "示範單元測試", "status": "doing", "owner": "示範舊工程師"},
         {"id": 15, "title": "someone else's", "status": "todo", "owner": "示範秘書長"}]
SCHED = [{"id": "demo.backup", "title": "示範每日備份", "when": {"en": "Daily 02:30", "zh": "每天 02:30"}, "owner": "eng"},
         {"id": "demo.other", "title": "elsewhere", "when": "Mon", "owner": "chief"}]
c = ctx(open_tasks=lambda: TASKS, schedules=lambda: SCHED, pi_hosts=lambda: HOSTS)
it = oo.clean_item(pe.item(c, "bot:eng"), staff.DECLARATION["actions"])
check("its tasks are found by label or a former name, not someone else's",
      [l.get("task") for l in it["links"] if l.get("view") == "board"] == [31, 14], it["links"])
check("its schedule links to the calendar, its files to the bookshelf's filter for it",
      any(l.get("view") == "cal" and "示範每日備份" in l["label"] and "Daily 02:30" in l["label"] for l in it["links"])
      and any(l.get("object") == "bookshelf" and l["filters"] == {"owner": "eng"} for l in it["links"]), it["links"])
check("the duty is the body", "寫程式、跑測試" in it["markdown"] and it["actions"] == ["edit_duty"])
big = oo.clean_item(pe.item(c, "bot:chief"), staff.DECLARATION["actions"])
texts = [x["text"] for x in big["chips"]]
check("chips: primary, awake with its tab, CLI, model, memory",
      texts[:3] == ["★ Primary", "Awake", "Tab 3"] and "Claude Code" in texts and "Memory on" in texts, texts)
loc = oo.clean_item(pe.item(c, "bot:local"), staff.DECLARATION["actions"])
check("a local model bot says so", "Local model" in [x["text"] for x in loc["chips"]], loc["chips"])
blob = json.dumps(pe.item(c, "bot:outside"), ensure_ascii=False)
check("an external bot is labelled and shows nothing of its endpoint", "Lives outside ShellBot" in blob and "hooks.example" not in blob
      and "secret-path" not in blob and "tok-demo" not in blob and "bookshelf" not in blob, blob)
check("the one action opens the bot's settings", pe.action(c, "bot:eng", "edit_duty", {}) == {"ok": True, "go": {"settings": "eng"}}
      and oo.clean_action(pe.action(c, "bot:eng", "edit_duty", {}))["go"] == {"settings": "eng"})
check("an unknown bot gets nothing", pe.item(c, "bot:ghost") == {} and pe.action(c, "bot:ghost", "edit_duty", {})["ok"] is False)
check("English and Chinese", "Open tasks" in pe.item(c, "bot:eng")["markdown"]
      and "手上任務" in pe.item(ctx("zh", open_tasks=lambda: TASKS), "bot:eng")["markdown"])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
