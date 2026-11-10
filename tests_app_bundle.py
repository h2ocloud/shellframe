#!/usr/bin/env python3
"""The app's new name (app_bundle.py): the macOS bundle installed as ShellBot.app, found
under either name, an older install renamed once (LaunchServices and the Dock told), the
update's copy of the template; the Windows desktop shortcut.

The folders are temporary, the Dock's prefs are a stand-in and every system command is
recorded, not run: nothing here touches /Applications, LaunchServices or the Dock. What
those do was measured with a throwaway app (docs: app_bundle.py's docstring).

Run: .venv/bin/python tests_app_bundle.py
"""
import hashlib
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import app_bundle as ab  # noqa: E402

ab.DOCK_SETTLE_S = 0

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


TMP = Path(tempfile.mkdtemp(prefix="sf-app-bundle-"))
ROOT = os.geteuid() == 0 if hasattr(os, "geteuid") else False


def make_app(path, bid=ab.BUNDLE_ID, payload="payload"):
    (path / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    with open(path / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bid, "CFBundleName": "ShellFrame"}, f)
    (path / "Contents" / "MacOS" / "shellframe").write_text(payload)
    return path


def folders(name):
    a, b = TMP / name / "Applications", TMP / name / "home-Applications"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    return a, b


class Run:
    """subprocess.run as app_bundle calls it: `cp` really runs; lsregister, defaults and
    killall are recorded. The Dock's prefs are kept here: `defaults import` replaces them,
    and with `overwrite` a Dock asked to quit writes back what it had (the race measured
    on a real Dock): `"once"` only on the first plain quit, `"always"` even when killed."""

    def __init__(self, dock=None, overwrite=None):
        self.calls, self.dock, self.imported, self.overwrite = [], dock, None, overwrite
        self.imports = 0

    def __call__(self, args, **kw):
        args = [str(a) for a in args]
        self.calls.append(args)
        if args[0] == "cp":
            return subprocess.run(args, **kw)
        if args[:2] == ["defaults", "export"]:
            ok = self.dock is not None
            return subprocess.CompletedProcess(args, 0 if ok else 1, plistlib.dumps(self.dock or {}), b"")
        if args[:2] == ["defaults", "import"]:
            self.before, self.imports = self.dock, self.imports + 1
            self.dock = self.imported = plistlib.loads(kw["input"])
        if args[0] == "killall" and self.overwrite:
            if self.overwrite == "always" or (args == ["killall", "Dock"] and self.overwrite == "once"):
                self.dock = self.before
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def ls(self):
        return [(c[1], Path(c[2]).parent.name + "/" + Path(c[2]).name) for c in self.calls if c[0] == ab.LSREGISTER]

    def ran(self, *head):
        return [i for i, c in enumerate(self.calls) if c[:len(head)] == list(head)]


def pin(url, label, bid=ab.BUNDLE_ID, book=b"bookmark"):
    tile = {"bundle-identifier": bid, "file-data": {"_CFURLString": url, "_CFURLStringType": 15},
            "file-label": label, "file-type": 41}
    if book is not None:
        tile["book"] = book
    return {"tile-data": tile, "tile-type": "file-tile"}


print("1. finding the app: either name, the new one first")
a, b = folders("find")
make_app(b / "ShellFrame.app")
check("an older install's ShellFrame.app is found", ab.find([a, b]) == (b / "ShellFrame.app").resolve(), ab.find([a, b]))
make_app(a / "ShellFrame.app")
check("the first folder (/Applications) before the next", ab.find([a, b]) == (a / "ShellFrame.app").resolve())
make_app(a / "ShellBot.app")
check("the new name before the old in the same folder", ab.find([a, b]) == (a / "ShellBot.app").resolve())
c, d = folders("find-link")
make_app(d / "real.app")
os.symlink(d / "real.app", c / "ShellBot.app")
check("a symlink is followed", ab.find([c]) == (d / "real.app").resolve(), ab.find([c]))
e, _ = folders("find-none")
make_app(TMP / "install" / "ShellFrame.app")
check("nothing installed: the install dir's template", ab.find([e], template_dir=TMP / "install") == (TMP / "install" / "ShellFrame.app").resolve())
check("…and nothing at all: None", ab.find([e]) is None)
check("the template keeps its name", ab.TEMPLATE == "ShellFrame.app" and ab.NAME == "ShellBot.app")

print("2. an older install renamed once, LaunchServices and the Dock told")
a, b = folders("migrate")
old = make_app(a / "ShellFrame.app")
inode = old.stat().st_ino
other = pin("file:///Applications/Notes.app/", "Notes", bid="com.example.notes")
dock = {"persistent-apps": [other, pin(old.as_uri() + "/", "ShellFrame"), pin("file:///Applications/Mail.app/", "Mail", "com.example.mail")],
        "persistent-others": [{"tile-data": {"file-label": "Downloads"}}]}
run, logs = Run(dock), []
moved = ab.migrate([a, b], run=run, log=logs.append)
new = a / "ShellBot.app"
check("renamed: the same folder (inode) under the new name", moved == [new] and new.stat().st_ino == inode
      and not (a / "ShellFrame.app").exists(), (moved, logs))
check("LaunchServices: the old path dropped, the new one registered (it keeps the old path otherwise)",
      run.ls() == [("-u", "Applications/ShellFrame.app"), ("-f", "Applications/ShellBot.app")], run.ls())
apps = (run.imported or {}).get("persistent-apps") or []
tile = apps[1]["tile-data"] if len(apps) == 3 else {}
check("the Dock pin points at the new path, labelled ShellBot, its bookmark dropped",
      tile.get("file-data", {}).get("_CFURLString") == new.as_uri() + "/" and tile.get("file-label") == "ShellBot"
      and "book" not in tile, tile)
check("…the other pins, their order and the rest of the Dock as they were",
      [x["tile-data"].get("file-label") for x in apps] == ["Notes", "ShellBot", "Mail"]
      and apps[0] == other and run.imported.get("persistent-others") == dock["persistent-others"], apps)
check("…written through `defaults`, then the Dock restarted (it reads its pins only at launch)",
      run.ran("defaults", "import") and run.ran("killall", "Dock")
      and run.ran("defaults", "import")[0] < run.ran("killall", "Dock")[0], run.calls)
run2 = Run(dock)
check("done once: a second start changes nothing", ab.migrate([a, b], run=run2) == [] and run2.calls == [], run2.calls)

c, _ = folders("migrate-race")
make_app(c / "ShellFrame.app")
run, logs = Run({"persistent-apps": [pin((c / "ShellFrame.app").as_uri() + "/", "ShellFrame", book=None)]}, overwrite="once"), []
ab.migrate([c], run=run, log=logs.append)
tile = run.dock["persistent-apps"][0]["tile-data"]
check("a Dock that writes its own pins over ours as it quits: read back, and the second try stops it unsaved",
      run.imports == 2 and run.ran("killall", "Dock") and run.ran("killall", "-KILL", "Dock")
      and tile["file-data"]["_CFURLString"] == (c / "ShellBot.app").as_uri() + "/" and "written its own" in " ".join(logs),
      (run.calls[-6:], logs))
c, _ = folders("migrate-stubborn")
make_app(c / "ShellFrame.app")
run, logs = Run({"persistent-apps": [pin((c / "ShellFrame.app").as_uri() + "/", "ShellFrame")]}, overwrite="always"), []
moved = ab.migrate([c], run=run, log=logs.append)
check("…one that keeps them anyway: two tries, then said, and the app renamed all the same",
      run.imports == 2 and moved == [c / "ShellBot.app"] and "by hand" in " ".join(logs), (run.imports, logs))

c, _ = folders("migrate-later")
make_app(c / "ShellFrame.app")
run, jobs = Run({"persistent-apps": [pin((c / "ShellFrame.app").as_uri() + "/", "ShellFrame")]}), []
moved = ab.migrate([c], run=run, dock=jobs.append)
check("the Dock as a job for later (the app's start runs it in the background): renamed now, the Dock untouched yet",
      moved == [c / "ShellBot.app"] and len(jobs) == 1 and not run.ran("defaults", "import"), run.calls)
jobs[0]()
check("…and the job re-pins it", run.dock["persistent-apps"][0]["tile-data"]["file-label"] == "ShellBot")
c, _ = folders("migrate-nopin")
make_app(c / "ShellFrame.app")
run = Run({"persistent-apps": [other]})
ab.migrate([c], run=run)
check("no pin of it in the Dock: the Dock is left alone", not run.ran("defaults", "import") and not run.ran("killall", "Dock"), run.calls)
c, _ = folders("migrate-nodock")
make_app(c / "ShellFrame.app")
run = Run(None)
check("the Dock's prefs cannot be read: renamed all the same", ab.migrate([c], run=run) == [c / "ShellBot.app"]
      and not run.ran("killall", "Dock"))

c, d = folders("migrate-leave")
make_app(d / "real.app")
os.symlink(d / "real.app", c / "ShellFrame.app")
run = Run({})
check("a symlink under the old name is not moved", ab.migrate([c], run=run) == [] and (c / "ShellFrame.app").is_symlink()
      and run.calls == [])
e, f = folders("migrate-other")
make_app(e / "ShellFrame.app", bid="com.example.someone-else")
logs = []
check("another app called ShellFrame.app is not moved", ab.migrate([e], run=Run({}), log=logs.append) == []
      and (e / "ShellFrame.app").is_dir() and "not this app" in " ".join(logs), logs)
make_app(f / "ShellFrame.app")
make_app(f / "ShellBot.app")
logs = []
check("both names there: nothing moves at start (the next update keeps one)",
      ab.migrate([f], run=Run({}), log=logs.append) == [] and (f / "ShellFrame.app").is_dir() and (f / "ShellBot.app").is_dir()
      and "both" in " ".join(logs), logs)
if not ROOT:
    g, _ = folders("migrate-readonly")
    make_app(g / "ShellFrame.app")
    os.chmod(g, 0o555)
    logs = []
    try:
        res = ab.migrate([g], run=Run({}), log=logs.append)
    finally:
        os.chmod(g, 0o755)
    check("a folder that cannot be written: the app keeps its name and runs", res == [] and (g / "ShellFrame.app").is_dir()
          and "keeps its name" in " ".join(logs), logs)

print("3. the Dock's pins, rewritten (pure)")
moved_pairs = [(Path("/Applications/ShellFrame.app"), Path("/Applications/ShellBot.app"))]
apps = [pin("file:///Applications/ShellFrame.app/", "ShellFrame"), pin("file:///Applications/ShellFrame.app", "x", book=None),
        pin("file:///Users/someone/Applications/ShellBot.app/", "ShellBot"), "not a dict", {"tile-type": "spacer-tile"}]
check("both spellings of the old URL re-pointed; a pin elsewhere and odd entries kept",
      ab.dock_repin(apps, moved_pairs) and [x["tile-data"]["file-data"]["_CFURLString"] for x in apps[:3]]
      == ["file:///Applications/ShellBot.app/", "file:///Applications/ShellBot.app/", "file:///Users/someone/Applications/ShellBot.app/"]
      and apps[3] == "not a dict", apps[:3])
check("nothing of it pinned: nothing changed", ab.dock_repin([pin("file:///Applications/Mail.app/", "Mail", "x")], moved_pairs) is False)
followed = [pin("file:///Applications/ShellBot.app/", "ShellFrame")]
check("a pin the Dock moved itself, with the old label: relabelled",
      ab.dock_repin(followed, moved_pairs) and followed[0]["tile-data"]["file-label"] == "ShellBot", followed)
done = [pin("file:///Applications/ShellBot.app/", "ShellBot")]
check("…and one already right (its new bookmark kept): nothing to do",
      ab.dock_repin(done, moved_pairs) is False and "book" in done[0]["tile-data"])

print("4. the update's copy of the template")
tpl = make_app(TMP / "tpl" / "ShellFrame.app", payload="new build")
a, b = folders("refresh")
make_app(a / "ShellFrame.app", payload="old build")
make_app(b / "ShellFrame.app")
make_app(b / "ShellBot.app")
make_app(b / "Keep.app")
dock = {"persistent-apps": [pin((a / "ShellFrame.app").as_uri() + "/", "ShellFrame")]}
run = Run(dock)
prepared = []
steps = ab.refresh(tpl, [a, b], run=run, prepare=lambda p: prepared.append(p) or (True, "built"))
check("copied as ShellBot.app, the launcher built in it",
      (a / "ShellBot.app" / "Contents" / "MacOS" / "shellframe").read_text() == "new build" and prepared == [a / "ShellBot.app"], steps)
check("the old-named copy beside it removed, and this app's copies in the later folder",
      not (a / "ShellFrame.app").exists() and not (b / "ShellFrame.app").exists() and not (b / "ShellBot.app").exists()
      and (b / "Keep.app").is_dir(), steps)
check("…LaunchServices told, the new copy registered",
      ("-u", "Applications/ShellFrame.app") in run.ls() and run.ls()[-1] == ("-f", "Applications/ShellBot.app"), run.ls())
tile = ((run.imported or {}).get("persistent-apps") or [{}])[0].get("tile-data", {})
check("…and the Dock pin of the removed copy points at the new one",
      tile.get("file-data", {}).get("_CFURLString") == (a / "ShellBot.app").as_uri() + "/", tile)
run = Run({})
steps = ab.refresh(tpl, [a, b], run=run)
check("the next update: only ShellBot.app replaced, nothing else touched",
      (a / "ShellBot.app").is_dir() and not run.ran("defaults", "import") and run.ls() == [("-f", "Applications/ShellBot.app")], (steps, run.ls()))
c, _ = folders("refresh-other")
make_app(c / "ShellFrame.app", bid="com.example.someone-else")
ab.refresh(tpl, [c], run=Run({}))
check("another app called ShellFrame.app stays", bundle_ok := ab.bundle_id(c / "ShellFrame.app") == "com.example.someone-else", bundle_ok)
if not ROOT:
    g, _ = folders("refresh-readonly")
    make_app(g / "ShellFrame.app", payload="old build")
    os.chmod(g, 0o555)
    try:
        steps = ab.refresh(tpl, [g], run=Run({}))
    finally:
        os.chmod(g, 0o755)
    check("a folder that cannot be written: refreshed in place under the old name, not nested",
          (g / "ShellFrame.app" / "Contents" / "MacOS" / "shellframe").read_text() == "new build"
          and not (g / "ShellFrame.app" / "ShellFrame.app").exists() and not (g / "ShellBot.app").exists(), steps)
check("no template: nothing done", ab.refresh(TMP / "missing" / "ShellFrame.app", [a], run=Run({})) == [])

print("5. Windows: the desktop shortcut")
desk = TMP / "Desktop"
desk.mkdir()
(desk / "ShellFrame.lnk").write_bytes(b"lnk")
check("an older install's ShellFrame.lnk renamed ShellBot.lnk",
      ab.rename_desktop_shortcut(desk) and (desk / "ShellBot.lnk").read_bytes() == b"lnk" and not (desk / "ShellFrame.lnk").exists())
(desk / "ShellFrame.lnk").write_bytes(b"old")
check("ShellBot.lnk already there: the other is left", ab.rename_desktop_shortcut(desk) is False
      and (desk / "ShellFrame.lnk").exists() and (desk / "ShellBot.lnk").read_bytes() == b"lnk")
check("no desktop found: nothing done", ab.rename_desktop_shortcut(TMP / "no-such-desktop") is False)

print("5b. an isolated instance leaves the installed app alone")
calls = []
real_migrate, real_rename = ab.migrate, ab.rename_desktop_shortcut
ab.migrate = lambda **kw: calls.append("migrate") or []
ab.rename_desktop_shortcut = lambda **kw: calls.append("shortcut") or False
saved = {k: os.environ.get(k) for k in ("SHELLFRAME_TESTS", "SHELLFRAME_CONFIG_DIR", "HOME")}


def env(**kv):
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


try:
    for label, kv in (("a test run", {"SHELLFRAME_TESTS": "1"}),
                      ("a config folder of its own", {"SHELLFRAME_TESTS": None, "SHELLFRAME_CONFIG_DIR": str(TMP / "cfg")}),
                      ("a HOME of its own", {"SHELLFRAME_TESTS": None, "SHELLFRAME_CONFIG_DIR": None, "HOME": str(TMP / "home")})):
        env(**kv)
        calls.clear()
        logs = []
        ab.at_start(log=logs.append)
        check(f"{label}: nothing renamed, and said why", calls == [] and label in " ".join(logs), (calls, logs))
    env(SHELLFRAME_TESTS=None, SHELLFRAME_CONFIG_DIR=None)
    calls.clear()
    ab.at_start()
    expected = ["migrate"] if sys.platform == "darwin" else (["shortcut"] if os.name == "nt" else [])
    check("the app itself (its own HOME, no test settings): renamed", calls == expected, calls)
finally:
    env()
    ab.migrate, ab.rename_desktop_shortcut = real_migrate, real_rename

print("6. what must not change, and who asks app_bundle")
h = hashlib.sha256()
tdir = HERE / "ShellFrame.app"
for p in sorted(x for x in tdir.rglob("*") if x.is_file()):
    h.update(str(p.relative_to(tdir)).encode() + b"\0" + p.read_bytes() + b"\0")
# Every Mac's update copies these bytes and signs the copy: a change gives the installed app a
# new signature (cdhash), and macOS then asks again for the permissions it had (Accessibility,
# microphone, Downloads). The rename kept them; change this only on purpose.
check("the template's bytes are unchanged", h.hexdigest() == "596589fb0f2dbd6027f5e619cb94fa40d6bf40266b6a1e77cf73a4219a258f6a",
      h.hexdigest())
src = {n: (HERE / n).read_text(encoding="utf-8") for n in ("main.py", "api_update.py", "sfctl.py")}
check("no installed-bundle path is spelled out elsewhere",
      all('"/Applications/ShellFrame.app' not in s and '"ShellFrame.app"' not in s for s in src.values()))
check("the update copies through refresh(), the restart finds through find()",
      "app_bundle.refresh(main.APP_DIR / app_bundle.TEMPLATE" in src["api_update.py"]
      and "app_bundle.find(template_dir=main.APP_DIR)" in src["api_update.py"])
s = src["sfctl.py"]
i_pid, i_mig = s.find('"ShellBot PID file not found"'), s.find("app_bundle.migrate()")
check("sfctl restart renames only once the relaunch is its own (after the PID check)", 0 < i_pid < i_mig, (i_pid, i_mig))
m = src["main.py"]
body = m[m.index("def main():\n"):]
check("the app renames after the single-instance guard, before the app is built",
      0 < body.find("    _ensure_single_instance()\n") < body.find("    app_bundle.at_start(") < body.find("    api = Api()\n"))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
