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
ok &= check("office.html exposes t(en, zh) through HOST.lang()",
            bool(re.search(r"const t = \(en, zh\) => \(isZh\(\) \? zh : en\);", HTML))
            and "const isZh = () => /^zh/i.test((HOST && HOST.lang && HOST.lang())" in HTML)
ok &= check("officeHost exposes lang()",
            bool(re.search(r"const officeHost = \{[\s\S]*?\n\s*lang: \(\) => lang\(\),", JS)))

sys.exit(0 if ok else 1)
