#!/usr/bin/env python3
"""Windows: the app's window and taskbar button carry ShellBot's icon, not Python's (main._win_app_identity).

Reported: on Windows the app opened with Python's icon. It runs under pythonw.exe; pywebview takes the executable's
icon when given none, and the taskbar groups the window with other Python programs. So, on Windows only:
  - the process gets an AppUserModelID of its own before the window opens
  - webview.start is given icon.ico (it ships beside main.py)
  - elsewhere nothing is passed (the Mac app bundle has its icon)
On Windows (CI) the real shell32 call runs; elsewhere it is a stand-in. Run: .venv/bin/python tests_win_icon.py
"""
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import main  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


ico = str(HERE / "icon.ico")
check("icon.ico ships beside main.py", os.path.isfile(ico))
if sys.platform == "win32":
    got = main._win_app_identity()
    check("on Windows: icon.ico is given to the window", got == {"icon": ico}, got)
    import ctypes
    buf = ctypes.c_wchar_p()
    ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(buf))
    check("…and the process has its own AppUserModelID", buf.value == "h2ocloud.ShellBot", buf.value)
else:
    calls = []
    fake = types.SimpleNamespace(windll=types.SimpleNamespace(shell32=types.SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=lambda app_id: calls.append(app_id) or 0)))
    with patch.object(main, "IS_WIN", True), patch.dict(sys.modules, {"ctypes": fake}):
        got = main._win_app_identity()
    check("on Windows: icon.ico is given to the window", got == {"icon": ico}, got)
    check("…and the process gets its own AppUserModelID first", calls == ["h2ocloud.ShellBot"], calls)
    with patch.object(main, "IS_WIN", False):
        check("elsewhere nothing is passed", main._win_app_identity() == {})
src = (HERE / "main.py").read_text(encoding="utf-8")
check("webview.start is given it", "webview.start(debug=(\"--debug\" in sys.argv), **_win_app_identity())" in src)

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
