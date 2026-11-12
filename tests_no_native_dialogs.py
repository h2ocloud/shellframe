#!/usr/bin/env python3
"""The page asks through its own dialogs, never a native one (static, no browser).

A native confirm(), alert() or prompt() in WebView2 (Windows) is titled with the page's address,
"127.0.0.1:47342 says"; the page's own (web/dialog.js: SFDialog.confirm and SFDialog.alert, which return
a Promise) says nothing of the kind. This test makes a native one a red test, in index.html and in
every web/*.js, and guards the one way the swap can go wrong without a sound: a question whose answer
is not awaited is a Promise, which is always true, so the "no" would be a "yes".
  - no native confirm / alert / prompt call, and no window.confirm / alert / prompt, outside comments
  - every SFDialog.confirm( is awaited (or handed on: `=> SFDialog.confirm(`, `return SFDialog.confirm(`)
  - dialog.js and dialog.css are loaded, the script before bots.js (which asks through it)
  - the dialog's own file raises no native dialog either
Run: .venv/bin/python tests_no_native_dialogs.py
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
WEB = HERE / "web"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def code_only(text):
    """The text without its comments, line for line (a `//` after a colon is a URL, not a comment)."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in text.splitlines())


NATIVE = re.compile(r"(?<![\w.$])(?:window\.)?(?:alert|confirm|prompt)\s*\(|\bwindow\.(?:alert|confirm|prompt)\b")
files = sorted(WEB.glob("*.js")) + [WEB / "index.html"]
check("the files that are scanned are there", len(files) >= 6 and all(f.exists() for f in files), [f.name for f in files])

print("1. no native dialog")
for f in files:
    src = code_only(f.read_text(encoding="utf-8"))
    hits = [(i + 1, line.strip()[:90]) for i, line in enumerate(src.splitlines()) if NATIVE.search(line)]
    check(f"{f.name}: no native confirm / alert / prompt", not hits, hits[:4])

print("2. every question is awaited")
sites = 0
for f in files:
    src = code_only(f.read_text(encoding="utf-8"))
    for m in re.finditer(r"SFDialog\.confirm\(", src):
        sites += 1
        before = src[max(0, m.start() - 8):m.start()]
        line = src.count("\n", 0, m.start()) + 1
        check(f"{f.name}:{line}: SFDialog.confirm( is awaited or handed on",
              before.endswith(("await ", "=> ", "return ", "await (")) or f.name == "dialog.js", before)
check("there are questions to check (the page asks some)", sites >= 5, sites)

print("3. the dialog is loaded")
html = (WEB / "index.html").read_text(encoding="utf-8")
check("dialog.css is linked", 'href="dialog.css"' in html)
check("dialog.js is loaded, before bots.js", 'src="dialog.js"' in html and html.index('src="dialog.js"') < html.index('src="bots.js"'))
check("the dialog offers confirm and alert", all(f"{k}:" in (WEB / "dialog.js").read_text(encoding="utf-8") for k in ("confirm", "alert")))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
