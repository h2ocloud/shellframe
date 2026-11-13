#!/usr/bin/env python3
"""A bot's own skills (bots/<bot>/skills/<skill>/SKILL.md): only that bot gets them.

  - they are read from the bot's folder (bots.skills_dir), the name from the front matter, else the
    folder's; a folder without a SKILL.md, and a hidden one, are not skills
  - the wake-up brief lists its own and the shared ones it was given, each with its SKILL.md path;
    where a shared skill has the same name (or folder) the bot's own is the one
  - a CLI that loads skills by itself and one that does not get the same lines; changing the bot's
    CLI keeps them (they live in its folder, not in a CLI's)
  - the editor's calls: list, a blank skill, a copy of a shared skill (its whole folder), delete,
    open the folder; a name that is a path, `..` or hidden never reaches the disk
  - the folder's README is brought up to date once, unless someone edited it

Demo data only. Run: .venv/bin/python tests_bot_skills.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-bot-skills-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import api_bot_memory  # noqa: E402
import bot_skills  # noqa: E402
import bots  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


main.CONFIG_FILE = TMP / "config.json"
BOTS = TMP / "bots"
SHARED = TMP / "shared"
bots.SKILL_ROOTS = (str(SHARED),)


def skill(root, folder, name=None, desc="", extra=None):
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    head = f"---\nname: {name}\ndescription: {desc}\n---\n" if name else ""
    (d / "SKILL.md").write_text(head + f"# {folder}\n", encoding="utf-8")
    for rel, body in (extra or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body, encoding="utf-8")
    return d


skill(SHARED, "review", "review", "check a change")
skill(SHARED, "deploy", "deploy", "ship it", extra={"scripts/run.sh": "echo demo\n", "notes/a.md": "demo\n"})
skill(SHARED, "weekly", "weekly", "a weekly report")

ROSTER = {"alpha": {"label": "alpha", "cmd": "claude", "bot": {"memory": True, "skills": ["review", "deploy"]}},
          "beta": {"label": "beta", "cmd": "codex", "bot": {"memory": True, "skills": ["review", "deploy"]}},
          "gamma": {"label": "gamma", "cmd": "claude", "bot": {"external": {"kind": "webhook", "url": "https://example.com/h"}}}}
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True}, "agent_roster": ROSTER}), encoding="utf-8")

print("1. reading a bot's own skills")
check("a bot with no skills folder has none", bots.own_skill_catalog("alpha") == [])
bots.ensure_dirs("alpha")
own = bots.skills_dir("alpha")
check("its skills folder is in its own folder", own == BOTS / "alpha" / "skills")
skill(own, "daily-notes", "daily-notes", "keep the day's notes")
skill(own, "plain-folder")                                   # no front matter: the folder's name
(own / "no-skill-md").mkdir()                                # a folder without a SKILL.md
skill(own, ".hidden", "hidden")
cat = bots.own_skill_catalog("alpha")
check("the name is the front matter's, else the folder's; no SKILL.md and hidden ones are left out",
      [(k["name"], k["folder"]) for k in cat] == [("daily-notes", "daily-notes"), ("plain-folder", "plain-folder")], cat)
check("the description is read, the path is the SKILL.md",
      cat[0]["description"] == "keep the day's notes" and cat[0]["path"] == str(own / "daily-notes" / "SKILL.md"))
check("another bot does not see them", bots.own_skill_catalog("beta") == [])

print("2. the wake-up brief")
skill(own, "review", "review", "my own way to review")        # the same name as a shared one
mine, shared = bots.brief_skills("alpha", ["review", "deploy"])
check("where a shared skill has the name of one of its own, the bot's own is the one",
      [n for n, _ in mine] == ["daily-notes", "plain-folder", "review"] and [n for n, _ in shared] == ["deploy"], (mine, shared))
check("…and the path it is told is its own", dict(mine)["review"] == str(own / "review" / "SKILL.md"))
skill(own, "ship-it", "something else")                      # a folder named like a shared skill, another name inside
skill(own, "deploy", "deploy-mine")
mine, shared = bots.brief_skills("alpha", ["review", "deploy"])
check("a folder with the shared skill's name counts as the same skill too", shared == [], shared)
import shutil  # noqa: E402
shutil.rmtree(own / "deploy")
shutil.rmtree(own / "ship-it")
mine, shared = bots.brief_skills("alpha", ["review", "deploy"])
brief = bots.compose_brief("alpha", ROSTER["alpha"], ROSTER, own_skills=mine, skills=shared, with_ack=False, lang="en")
check("the brief lists its own skills first, with the folder they live in, then the shared ones",
      brief.index("Your own skills") < brief.index("- daily-notes: ") < brief.index("Skills (read the SKILL.md")
      < brief.index("- deploy: ") and str(own) in brief, brief)
check("each line carries the SKILL.md path", f"- review: {own / 'review' / 'SKILL.md'}" in brief
      and f"- deploy: {SHARED / 'deploy' / 'SKILL.md'}" in brief)
check("a shared skill it shadows is not listed a second time", brief.count("- review:") == 1)
zh = bots.compose_brief("alpha", ROSTER["alpha"], ROSTER, own_skills=mine, skills=shared, with_ack=False, lang="zh")
check("in Chinese too", "你自己的 skill" in zh and "- daily-notes：" in zh and "Skills（需要時" in zh)
check("no own skills: the brief is what it was", "Your own skills" not in bots.compose_brief(
    "beta", ROSTER["beta"], ROSTER, skills=[("review", "/x")], lang="en"))
check("the rule about saving memory says it happens while the tab is open too, when asked to",
      "quiet" in bots.compose_brief("beta", ROSTER["beta"], ROSTER, autosave=True, lang="en")
      and "quiet" not in bots.compose_brief("beta", ROSTER["beta"], ROSTER, lang="en"))

print("3. the same words for every CLI, and across a change of CLI")
api = Api.__new__(Api)
skill(bots.skills_dir("beta"), "daily-notes", "daily-notes", "keep the day's notes")
skill(bots.skills_dir("beta"), "review", "review", "mine")


def skill_lines(role, cmd):
    """The skill lines of the brief this bot wakes with when it runs on `cmd`."""
    entry = dict(json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["agent_roster"][role], cmd=cmd)
    text = api._bots_brief(role, entry, {role: entry}, with_ack=False)
    return [ln.replace(role, "<bot>") for ln in text.splitlines() if ln.startswith("- ") and "SKILL.md" in ln]


on_claude, on_codex = skill_lines("alpha", "claude"), skill_lines("alpha", "codex")
check("the same bot on Claude and on Codex is told the same skills, own first", on_claude == on_codex and len(on_claude) == 4
      and on_claude[0].startswith("- daily-notes: ") and on_claude[-1].startswith("- deploy: "), (on_claude, on_codex))
check("a Claude bot and a Codex bot with the same skills get the same line for one of them",
      skill_lines("beta", "codex")[0] == on_claude[0], (skill_lines("beta", "codex"), on_claude))
check("a shared skill both were given and neither shadows is listed for both",
      any(ln.startswith("- deploy: ") for ln in on_claude) and any(ln.startswith("- deploy: ") for ln in skill_lines("beta", "codex")))

import api_bots  # noqa: E402
api_bots._cli_choices = lambda: [{"key": "claude", "label": "Claude Code", "installed": True, "models": True},
                                 {"key": "codex", "label": "Codex", "installed": True, "models": True}]
api.sessions = {}
api._cmd_for_cli = lambda cli: cli
api._bots_notify_ui = lambda: None
before = sorted(str(p.relative_to(BOTS)) for p in (BOTS / "alpha" / "skills").rglob("*"))
r = api._bots_switch("alpha", "codex")
cfg_now = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["agent_roster"]["alpha"]
check("the bot's CLI changed through bots_switch", r["success"] and cfg_now["cmd"] == "codex", (r, cfg_now))
check("…its own skills are where they were, and still in the brief",
      sorted(str(p.relative_to(BOTS)) for p in (BOTS / "alpha" / "skills").rglob("*")) == before
      and skill_lines("alpha", "codex") == on_claude)

print("4. the editor's calls")
refused = json.loads(api.bots_own_skills("nobody"))
check("a name that is no bot is refused", refused["success"] is False and refused["code"] == "no_bot")
check("so is a bot outside ShellBot", json.loads(api.bots_skill_new("gamma", "x"))["code"] == "external")
r = json.loads(api.bots_own_skills("alpha"))
rows = {k["name"]: k for k in r["skills"]}
check("the list: name, folder, description, path, and which of them shadow a shared skill",
      set(rows) == {"daily-notes", "plain-folder", "review"} and rows["review"]["shadows"] is True
      and rows["daily-notes"]["shadows"] is False and r["dir"] == str(own), rows)

r = json.loads(api.bots_skill_new("alpha", "meeting notes", "turn a meeting into notes"))
made = own / "meeting-notes" / "SKILL.md"
check("a blank skill: a folder named from what was typed, and a SKILL.md with its front matter",
      r["success"] and r["folder"] == "meeting-notes" and made.is_file()
      and made.read_text(encoding="utf-8").startswith("---\nname: meeting-notes\ndescription: turn a meeting into notes\n---"), r)
check("…which is listed at once, under its name", "meeting-notes" in [k["name"] for k in json.loads(api.bots_own_skills("alpha"))["skills"]])
r = json.loads(api.bots_skill_new("alpha", "meeting notes"))
check("the same name again is refused and nothing is overwritten", r["success"] is False and r["code"] == "exists"
      and "turn a meeting into notes" in made.read_text(encoding="utf-8"))
for bad in ("", "   ", "../x", "a/b", "a\\b", "..", ".", ".hidden", "///", "a\x00b"):
    r = json.loads(api.bots_skill_new("alpha", bad))
    check(f"a name that is a path, `..`, hidden or empty is refused ({bad!r})", r["success"] is False and r.get("code") == "bad_name", r)
check("…and nothing was made outside the skills folder (or hidden in it)",
      not (BOTS / "alpha" / "x").exists() and not (own / "hidden").exists() and not (own / "ab").exists())

r = json.loads(api.bots_skill_copy("alpha", "weekly"))
check("a shared skill copied: its folder, as one of the bot's own", r["success"] and (own / "weekly" / "SKILL.md").is_file()
      and (SHARED / "weekly" / "SKILL.md").is_file(), r)
r = json.loads(api.bots_skill_copy("beta", "deploy"))
dep = bots.skills_dir("beta") / "deploy"
check("…with everything in it (scripts, notes)", r["success"] and (dep / "scripts" / "run.sh").read_text(encoding="utf-8") == "echo demo\n"
      and (dep / "notes" / "a.md").is_file(), r)
(dep / "SKILL.md").write_text("# changed by the bot\n", encoding="utf-8")
check("the copy is the bot's to change: the shared one is as it was", "changed" not in (SHARED / "deploy" / "SKILL.md").read_text(encoding="utf-8"))
check("a copy over one it already has is refused", json.loads(api.bots_skill_copy("beta", "deploy"))["code"] == "exists"
      and "changed" in (dep / "SKILL.md").read_text(encoding="utf-8"))
check("a shared skill that is not there is refused", json.loads(api.bots_skill_copy("beta", "nope"))["code"] == "no_skill")
if os.name != "nt":
    (SHARED / "weekly" / "link").symlink_to(TMP)
    json.loads(api.bots_skill_copy("beta", "weekly"))
    check("a link inside a shared skill is not followed or copied", not (bots.skills_dir("beta") / "weekly" / "link").exists())
big = skill(SHARED, "huge", "huge")
for i in range(bot_skills.COPY_MAX_FILES + 1):
    (big / f"f{i}.txt").write_text("x", encoding="utf-8")
r = json.loads(api.bots_skill_copy("beta", "huge"))
check("one that is really a project is not copied, and leaves no half a skill", r["code"] == "too_big"
      and not (bots.skills_dir("beta") / "huge").exists(), r)

opened = []
api_bot_memory.subprocess.Popen = lambda cmd, **kw: opened.append(cmd)
r = json.loads(api.bots_skill_open("alpha", "daily-notes"))
check("open: one skill's folder", r["success"] and opened[-1][1] == str(own / "daily-notes"), (r, opened))
r = json.loads(api.bots_skill_open("alpha"))
check("open: without a name, the skills folder", opened[-1][1] == str(own))
r = json.loads(api.bots_skill_open("alpha", "../beta"))
check("open: a path out of it is refused", r["success"] is False and opened[-1][1] == str(own))
fresh_bot = json.loads(api.bots_skill_open("beta"))
check("open: the skills folder of a bot with none is made, so there is something to open",
      (bots.skills_dir("beta")).is_dir() and fresh_bot["success"])

r = json.loads(api.bots_skill_delete("alpha", "meeting-notes"))
check("delete: the skill's folder is gone", r["success"] and not (own / "meeting-notes").exists())
check("…the others are not", (own / "daily-notes" / "SKILL.md").is_file() and (SHARED / "review" / "SKILL.md").is_file())
check("delete of one that is not there", json.loads(api.bots_skill_delete("alpha", "meeting-notes"))["code"] == "missing")
for bad in ("..", "../beta", "a/b", "", ".hidden", "daily-notes/.."):
    r = json.loads(api.bots_skill_delete("alpha", bad))
    check(f"delete: a path or a hidden name is refused ({bad!r})", r["success"] is False and r.get("code") == "bad_name", r)
check("…nothing outside was touched", (BOTS / "beta" / "skills" / "deploy" / "SKILL.md").is_file() and (own / "daily-notes").is_dir())
if os.name != "nt":
    (own / "linked").symlink_to(SHARED / "review")
    check("a skill that is a link to another folder is listed", "linked" in [k["folder"] for k in bots.own_skill_catalog("alpha")]
          or "review" in [k["name"] for k in bots.own_skill_catalog("alpha")])
    r = json.loads(api.bots_skill_delete("alpha", "linked"))
    check("…and deleting it removes the link, never the folder it points to",
          r["success"] and not (own / "linked").exists() and (SHARED / "review" / "SKILL.md").is_file(), r)

print("5. the folder's README")
root_readme = BOTS / "README.md"
check("a new README mentions skills/", "skills/<name>/SKILL.md" in root_readme.read_text(encoding="utf-8"))
root_readme.write_text(bots._OLD_README[0], encoding="utf-8")
bots.ensure_dirs("alpha")
check("an older README that nobody edited is brought up to date", "skills/<name>/SKILL.md" in root_readme.read_text(encoding="utf-8"))
root_readme.write_text("my own notes\n", encoding="utf-8")
bots.ensure_dirs("alpha")
check("…one the user edited is left alone", root_readme.read_text(encoding="utf-8") == "my own notes\n")

print("6. the page calls what exists")
js = (HERE / "web" / "bots.js").read_text(encoding="utf-8")
import re  # noqa: E402
called = set(re.findall(r"api\(\)\.(bots_(?:own_skills|skill_\w+))", js))
check("every skill call bots.js makes is a method of the app", called and all(hasattr(Api, n) for n in called), called)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
