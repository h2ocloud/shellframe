#!/usr/bin/env python3
"""The external locale table is source data, not an untranslated UI hit."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
result = subprocess.run(
    [sys.executable, str(ROOT / "scripts" / "cjk_scan.py")],
    cwd=ROOT,
    check=True,
    capture_output=True,
    text=True,
)
hits = [line for line in result.stdout.splitlines() if "\tweb/i18n.js:" in line]
if hits:
    print("FAIL: web/i18n.js locale data was counted by cjk_scan")
    print("\n".join(hits))
    raise SystemExit(1)
print("PASS: cjk_scan excludes web/i18n.js zh-TW locale data")
