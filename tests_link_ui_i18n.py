#!/usr/bin/env python3
"""Pairing and remote-maintenance UI strings use the locale helper."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
remote = (ROOT / "api_remote.py").read_text(encoding="utf-8")
failures = []

if "title=\"配對另一台 ShellBot" in index or "尚未配對其他 ShellBot" in index:
    failures.append("sidebar pairing prompt remains hard-coded")
if "<h2 style=\"font-size:14px\">🔗 配對裝置</h2>" in index:
    failures.append("pair chooser title remains hard-coded")
if "<h2 style=\"font-size:14px\">🔗 加入配對</h2>" in index:
    failures.append("join dialog title remains hard-coded")
if "<h2 style=\"font-size:14px\">⟳ 維運" in index:
    failures.append("maintenance title remains hard-coded")
if "sf_lang.pick(" not in remote or "Frame Link — {'on'" not in remote:
    failures.append("api_remote link status is missing sf_lang.pick")
if "Pairing code: {res['code']}" not in remote and "配對碼：{res['code']}" not in remote:
    failures.append("api_remote pairing message is missing sf_lang.pick")

if failures:
    print("FAIL")
    print("\n".join(failures))
    raise SystemExit(1)
print("PASS: pairing and maintenance UI/API strings use i18n")
