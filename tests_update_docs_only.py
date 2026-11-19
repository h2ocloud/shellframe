#!/usr/bin/env python3
"""The update check does not offer an update for a commit that only changed documentation.

Reported: a machine running v0.59.1 showed 「v0.59.1 可更新（點我）」 after main gained a commit that only changed the
product page. check_update compares the install's HEAD with origin/main's SHA (so two releases with the same version
number are still told apart); now, when the version strings are the same, it fetches once and looks at the files:
docs/, qa-shots/, .github/ and Markdown only → no update. Anything else (a code change on the same version, a newer
version, a diff it cannot read) is still an update. Real git repos in a temp folder; the remote version.json read is
stubbed. Run: .venv/bin/python tests_update_docs_only.py
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
import main  # noqa: E402
import api_update  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


TMP = pathlib.Path(tempfile.mkdtemp(prefix="sf-update-docs-"))
ORIGIN, WORK, APP = TMP / "origin.git", TMP / "work", TMP / "app"


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def commit(files, msg, version=None):
    for name, text in files.items():
        p = WORK / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if version:
        (WORK / "version.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    git(WORK, "add", "-A")
    git(WORK, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "-m", msg)
    git(WORK, "push", "-q", "origin", "HEAD:main")
    REMOTE["version"] = json.loads((WORK / "version.json").read_text())["version"]


REMOTE = {"version": "0.0.0"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=5):
    return _Resp(json.dumps({"version": REMOTE["version"]}).encode())


class U(api_update.UpdateApiMixin):
    pass


try:
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(ORIGIN)], check=True)
    subprocess.run(["git", "clone", "-q", str(ORIGIN), str(WORK)], check=True, capture_output=True)
    git(WORK, "checkout", "-q", "-b", "main")
    commit({"main.py": "print(1)\n", "README.md": "# demo\n"}, "v1", version="0.9.1")
    subprocess.run(["git", "clone", "-q", "-b", "main", str(ORIGIN), str(APP)], check=True, capture_output=True)
    main.APP_DIR = APP
    main.VERSION_FILE = APP / "version.json"
    urllib.request.urlopen = fake_urlopen
    u = U()
    ask = lambda: json.loads(u.check_update())

    r = ask()
    check("the same commit: no update", r["update_available"] is False, r)

    commit({"docs/index.html": "<p>new page</p>\n", "README.md": "# demo, better\n", "qa-shots/a.png": "x"}, "docs only")
    r = ask()
    check("a docs-only commit on the same version (docs/, README, qa-shots): no update", r["update_available"] is False, r)
    check("…and the answer is kept for that commit (one fetch)", git(ORIGIN, "rev-parse", "main") in api_update._DOCS_ONLY, list(api_update._DOCS_ONLY))

    commit({"main.py": "print(2)\n"}, "code, same version")
    r = ask()
    check("a code change on the same version is still an update", r["update_available"] is True, r)

    git(APP, "pull", "-q", "origin", "main")
    commit({"CHANGELOG.md": "## v0.9.2\n"}, "v0.9.2", version="0.9.2")
    r = ask()
    check("a newer version is an update, whatever it changed", r["update_available"] is True and r["remote"] == "0.9.2", r)

    check("_doc_path: docs, shots, Markdown and .github yes; code, json and web no",
          all(api_update._doc_path(p) for p in ("docs/x.html", "qa-shots/a.png", "README.md", "docs/guide.md", ".github/workflows/ci.yml"))
          and not any(api_update._doc_path(p) for p in ("main.py", "version.json", "web/index.html", "guide.json")))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
