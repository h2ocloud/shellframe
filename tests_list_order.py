#!/usr/bin/env python3
"""list_sessions answers in the saved tab order (session_order), which the page adopts.

Reported: a tab dragged to the top of the sidebar kept jumping back. The drag was saved, but the page
takes list_sessions' order a few seconds after a drag (so a phone's drag shows up), and list_sessions
answered in the order the tabs were opened.
Run: .venv/bin/python tests_list_order.py
"""
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(Path(__file__).parent))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")
import main  # noqa: E402

main.CONFIG_FILE = Path(tempfile.mkdtemp(prefix="sf-order-")) / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "session_order": ["s3", "s1", "s2"]}), encoding="utf-8")
api = main.Api.__new__(main.Api)
tab = lambda: types.SimpleNamespace(alive=True, cmd="claude", _custom_label=None)
api.sessions = {"s1": tab(), "s2": tab(), "s3": tab()}
order = [x["sid"] for x in json.loads(api.list_sessions())]
ok = order == ["s3", "s1", "s2"]
print("  [PASS]" if ok else "  [FAIL]", "the tabs come in the saved order, not the order they were opened", order)
api.sessions["s4"] = tab()
order = [x["sid"] for x in json.loads(api.list_sessions())]
ok2 = order == ["s3", "s1", "s2", "s4"]
print("  [PASS]" if ok2 else "  [FAIL]", "…a tab the order does not know yet comes last", order)
if not (ok and ok2):
    sys.exit(1)
print("ALL PASS")
