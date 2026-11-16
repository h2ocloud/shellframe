#!/usr/bin/env python3
"""`sfctl version` (and the header of `sfctl skill`) lists only the experimental switches this build has.

Found in a settings inventory: the task board was removed in 0.45.3, but `experimental_board: true` stays in
old config files, and both answers listed every experimental_* key of the config file, so the AI's manual
began by saying a removed feature was on.
  - the listed keys come from DEFAULT_CONFIG only
  - each is read from the person's settings (on or off)
Static: the answer is built in api_remote's "version" branch. Run: .venv/bin/python tests_sfctl_version_flags.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
src = (HERE / "api_remote.py").read_text(encoding="utf-8")
branch = src.split('elif cmd == "version":', 1)[1].split("elif cmd ==", 1)[0]
keys = re.search(r"keys = (.*?)\n\s*return", branch, re.S).group(1)
ok1 = "DEFAULT_CONFIG" in keys and " st " not in keys and "in st" not in keys
ok2 = "bool(st.get(k))" in branch
print(f"  [{'PASS' if ok1 else 'FAIL'}] the listed switches come from DEFAULT_CONFIG only")
print(f"  [{'PASS' if ok2 else 'FAIL'}] each is read from the person's settings")
if not (ok1 and ok2):
    sys.exit(1)
print("ALL PASS")
