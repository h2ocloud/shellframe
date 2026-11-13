#!/usr/bin/env python3
"""The connector core knows no service by name.

The core is `connectors/*.py` (not `connectors/services/`, where a service's own description and adapter
live) and `api_connect.py`. A service name there means a branch for one service crept in, and a new
service would no longer cost only a description and an adapter. Run: .venv/bin/python tests_connectors_hygiene.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = ("gmail", "google", "slack", "notion", "drive", "calendar", "outlook", "workspace", "github", "jira",
         "dropbox", "onedrive", "sharepoint", "linebot", "discord", "confluence", "asana", "trello", "salesforce")
WORD = re.compile(r"(?i)(?<![a-z0-9])(" + "|".join(NAMES) + r")(?![a-z0-9])")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


core = sorted(p for p in (HERE / "connectors").glob("*.py")) + [HERE / "api_connect.py"]
check("the core files exist", len(core) >= 7 and all(p.exists() for p in core[:-1]), str(core))
for p in core:
    if not p.exists():
        continue
    hits = [f"{i}: {m.group(1)}" for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) for m in WORD.finditer(ln)]
    check(f"{p.relative_to(HERE)} names no service", not hits, "; ".join(hits[:5]))

svc = HERE / "connectors" / "services"
check("services/ is the only place a service lives", svc.is_dir() and (svc / "__init__.py").exists())
# the shipped descriptions are valid and their adapters have what they name
sys.path.insert(0, str(HERE))
from connectors import registry  # noqa: E402
reg = registry.Registry()
check("every shipped description is valid", not reg.errors, str(reg.errors))
for sid in reg.ids():
    ad = reg.adapter(sid)
    miss = registry.validate_adapter(reg.get(sid), ad) if ad is not None else ["no adapter"]
    desc = reg.get(sid)
    needs_code = any(st.get("check") or any(not a.get("builtin") for a in st.get("actions") or [])
                     for r in desc["routes"] for st in r["steps"])
    check(f"{sid}: its adapter has every check and action it names", not miss or (ad is None and not needs_code), str(miss[:3]))

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
