#!/usr/bin/env python3
"""Small contract test for the Office language bridge."""
import pathlib
import re
import sys


HERE = pathlib.Path(__file__).resolve().parent
HTML = (HERE / "web" / "office.html").read_text(encoding="utf-8")
JS = (HERE / "web" / "office.js").read_text(encoding="utf-8")


def check(name, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return ok


ok = True
ok &= check("office.html exposes tr(en, zh) through HOST.lang()",
            bool(re.search(r"const tr = \(en, zh\) => \(isZh\(\) \? zh : en\);", HTML))
            and "const isZh = () => /^zh/i.test((HOST && HOST.lang && HOST.lang())" in HTML)
ok &= check("no top-level t(): office.html uses t for a task card in many functions, where t(...) would call the card",
            not re.search(r"^const t = ", HTML, re.M))
ok &= check("officeHost exposes lang()",
            bool(re.search(r"const officeHost = \{[\s\S]*?\n\s*lang: \(\) => lang\(\),", JS)))

# Every toolbar button with words gets them from applyOfficeCopy (reported: 「排程」 stayed Chinese in English).
# The demo-only buttons (addOne, addTen: hidden with live data) are left out.
copy = HTML[HTML.index("function applyOfficeCopy"):HTML.index("applyOfficeCopy();")]
worded = [m.group(1) for m in re.finditer(r'<button class="tb[^"]*" id="([A-Za-z]+)"[^>]*>([^<]*[^\s<][^<]*)</button>', HTML)]
worded += [m.group(1) for m in re.finditer(r'<button class="tb[^"]*" id="([A-Za-z]+)"[^>]*>(?:<i [^>]*></i>)?<span[^>]*>[^<]*[^\s<]', HTML)]
missing = [b for b in worded if b not in ("addOne", "addTen") and not re.search(r"(?:text|spanText)\('#" + b + r"\b", copy)]
ok &= check(f"every worded toolbar button is translated in applyOfficeCopy ({len(worded)} found)", len(worded) >= 4 and not missing)
if missing:
    print("     missing:", missing)
ok &= check("the lobby's wall boards name themselves in the page's language (hover tip, view title)",
            "get name() { return tr('Task board', '任務看板'); }" in HTML and "get name() { return tr('Schedules', '排程'); }" in HTML)

sys.exit(0 if ok else 1)
