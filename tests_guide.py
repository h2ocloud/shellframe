#!/usr/bin/env python3
"""The guide stays bound to the features (guide.json, guide.py, web/guide.js).

A tutorial falls behind without a sound: a new button nobody wrote an entry for, an entry
pointing at a button that was renamed. So the binding is a test, as tests_ai_skill.py does
for the AI's manual:
  - every desktop feature in guide.json has its mark on the page (`data-guide="<id>"` in
    web/index.html or web/*.js), and every mark on the page has a feature
  - every `experimental_*` switch in DEFAULT_CONFIG is some feature's `flag`, or `no_guide`
    says why not
  - the newest CHANGELOG version with an `### Added` section has an entry of that version
    (`since`), or `added_without_guide` says why not; `reviewed_for` covers this minor version
  - both languages are written, short enough for the card; `since` is a version, not a future one
  - a tour names features the desktop has; an `open` is a settings tab that exists
  - who gets the tour: a first launch ("pending"), not a computer that already ran ShellBot
    ("skip", what it has counts as seen); the page's whole-settings save cannot undo it

Static (and guide.py's own functions): no app, no browser. The page itself: tests_guide_ui.py.
Run: .venv/bin/python tests_guide.py
"""
import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guide  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


data = json.loads((HERE / "guide.json").read_text(encoding="utf-8"))
features = data.get("features") or []
ids = [f.get("id") for f in features]
desktop = [f for f in features if "desktop" in (f.get("surfaces") or [])]
version = json.loads((HERE / "version.json").read_text(encoding="utf-8"))["version"]

print("each entry")
check("ids are unique kebab-case", len(ids) == len(set(ids)) and all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i or "") for i in ids), ids)
bad = [f["id"] for f in features if not set(f.get("surfaces") or []) or not set(f["surfaces"]) <= set(guide.SURFACES)]
check("every entry is on a known surface (desktop, ios)", not bad, bad)
short = []
for f in features:
    for key, most in (("title", 40), ("body", 260)):
        for lang in ("en", "zh"):
            txt = ((f.get(key) or {}).get(lang) or "").strip()
            if not txt or len(txt) > most:
                short.append(f"{f['id']}.{key}.{lang}={len(txt)}")
check("both languages written, title ≤ 40 and body ≤ 260 characters", not short, short)
bad = [f["id"] for f in desktop if not re.fullmatch(r"\d+\.\d+\.\d+", f.get("since") or "")
       or guide.newer(f["since"], version)]
check(f"a desktop entry's `since` is a version, not past this build's {version}", not bad, bad)
bad = [f["id"] for f in features if "since" in f and "desktop" not in f["surfaces"]]
check("a phone-only entry has no `since` (the app's versions are its own; it counts nothing new yet)", not bad, bad)
phone = [f for f in features if "ios" in (f.get("surfaces") or [])]
check("phone entries are app-*, so where-it-is words never serve both surfaces",
      all(f["id"].startswith("app-") and f["surfaces"] == ["ios"] for f in phone), [f["id"] for f in phone])

print("the page and guide.json name the same places")
web = "\n".join(p.read_text(encoding="utf-8") for p in sorted((HERE / "web").glob("*.js")) + [HERE / "web" / "index.html"]
                if p.name != "guide.js")
marks = set(re.findall(r'data-guide="([a-z0-9-]+)"', web)) | set(
    re.findall(r"setAttribute\('data-guide',\s*'([a-z0-9-]+)'\)", web))
missing = sorted({f["id"] for f in desktop} - marks)
orphans = sorted(marks - {f["id"] for f in desktop})
check("every desktop feature has its data-guide mark on the page", not missing, f"no mark for: {missing}")
check("every data-guide mark on the page has a feature in guide.json", not orphans, f"no entry for: {orphans}")

print("tours and settings tabs")
html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
tabs = set(re.findall(r'class="settings-tab[^"]*" data-tab="([a-z]+)"', html)) | set(
    re.findall(r"tab\.dataset\.tab = '([a-z]+)'", web))          # a tab a module adds (bots.js)
bad = [f["id"] for f in features if f.get("open") and (not re.fullmatch(r"settings:([a-z]+)", f["open"])
                                                       or f["open"].split(":")[1] not in tabs)]
check(f"every `open` is a settings tab that exists ({sorted(tabs)})", not bad, bad)
tours = data.get("tours") or {}
tour_ids = [i for name, t in tours.items() if not name.startswith("app_") for i in t]
bad = [i for i in tour_ids if i not in {f["id"] for f in desktop}]
check("the desktop's tours name desktop features", tour_ids and not bad, bad)
app_ids = [i for name, t in tours.items() if name.startswith("app_") for i in t]
bad = [i for i in app_ids if i not in {f["id"] for f in features if "ios" in f["surfaces"]}]
check("the app's tours (app_*) name phone features (the app checks its own marks)", not bad, bad)
check("there is a first-run tour", bool((data.get("tours") or {}).get("first_run")))

print("a new switch or a new feature needs its entry")
src = (HERE / "main.py").read_text(encoding="utf-8")
tree = ast.parse(src)
default = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
    isinstance(t, ast.Name) and t.id == "DEFAULT_CONFIG" for t in n.targets))
block = ast.get_source_segment(src, default)
flags = sorted(set(re.findall(r'"(experimental_[a-z0-9_]+)"\s*:', block)))
covered = {f.get("flag") for f in features} | set((data.get("no_guide") or {}).keys())
check(f"each of the {len(flags)} experimental_* switches is a feature's flag or in no_guide",
      flags and set(flags) <= covered, sorted(set(flags) - covered))
check("no_guide gives a reason for each", all(len(str(v).strip()) >= 20 for v in (data.get("no_guide") or {}).values()))
check("a flag named in guide.json exists in DEFAULT_CONFIG",
      all(f.get("flag") in flags for f in features if f.get("flag")),
      [f["flag"] for f in features if f.get("flag") and f["flag"] not in flags])
minor = ".".join(version.split(".")[:2])
check(f"reviewed_for covers this build's {minor}.x (look at the guide each minor release)",
      not guide.newer(minor + ".0", (data.get("reviewed_for") or "0") + ".0"), data.get("reviewed_for"))
log = (HERE / "CHANGELOG.md").read_text(encoding="utf-8")
added = None
for m in re.finditer(r"^## v(\d+\.\d+\.\d+)(.*?)(?=^## v|\Z)", log, re.M | re.S):
    if re.search(r"^### Added", m.group(2), re.M):
        added = m.group(1)
        break
if added and guide.newer(added, "0.44.6"):          # the guide starts at 0.44.6
    has = any(f.get("since") == added for f in features) or added in (data.get("added_without_guide") or {})
    check(f"v{added} adds features: guide.json has an entry of that version, or added_without_guide says why",
          has)
else:
    check("no release with ### Added since the guide began (nothing to check yet)", True)

print("who gets the tour")
first = {"guide": {"tour": "pending", "seen": ""}, "settings": {}}
check("DEFAULT_CONFIG's is pending (a first launch)", '"guide": {"tour": "pending", "seen": ""}' in block)
check("a first launch keeps it", guide.keep_old_install(first, "0.45.0") is False and guide.state(first)["tour"] == "pending")
old = {"settings": {"language": "en"}}
check("a config.json from before the guide: skip, what it has counts as seen",
      guide.keep_old_install(old, "0.45.0") is True and guide.state(old) | {"version": ""} == {"tour": "skip", "seen": "0.45.0", "version": ""},
      old)
check("…once (a second load writes nothing)", guide.keep_old_install(old, "0.46.0") is False)
check("the tour done is kept", guide.mark(first, tour="done", seen="0.45.0") and guide.state(first)["tour"] == "done")
check("seen never goes back", guide.mark(first, seen="0.44.0") is False and first["guide"]["seen"] == "0.45.0")
check("it lives outside settings (save_settings replaces settings whole from the page's copy)",
      "guide" not in first["settings"])
check("load_config gives an old config.json its guide state",
      "guide.keep_old_install(cfg)" in src)

print("what the desktop is told")
fl = guide.for_surface("desktop", {"experimental_a2a": False, "experimental_bots": True}, data)
by = {f["id"]: f for f in fl}
check("a feature behind a switch that is off is listed, marked off", by.get("a2a", {}).get("on") is False)
check("…on when its switch is on", by.get("bots", {}).get("on") is True)
check("a feature without a switch is on", by.get("new-tab", {}).get("on") is True)
check("what is new: since newer than seen", guide.newer("0.45.1", "0.45.0") and not guide.newer("0.45.0", "0.45.0")
      and guide.newer("0.45.0", ""))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
