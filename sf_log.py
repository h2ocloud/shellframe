"""ShellFrame logging primitives — shared by main.py and the Api mixin
modules (api_history / api_schedules) without circular imports.

Extracted from main.py in the God-class 分批拆解 (v0.23.0). Behavior is
byte-identical: best-effort append to the debug log, auto-truncate at 1MB,
never raises.
"""

import os
import sys
import tempfile as _tempfile
from datetime import datetime
from pathlib import Path

IS_WIN = sys.platform == "win32"

# Cross-platform temp dir — keep /tmp on Unix for continuity with existing
# installs, fall back to %TEMP% on Windows
TMP_DIR = Path("/tmp") if not IS_WIN else Path(_tempfile.gettempdir())


def _debug_log_path() -> str:
    """The app's log; a test process writes its own, in its run's folder (run_tests.sh
    removes it), so a test's lines never land in the log of the ShellBot running on that
    computer, where they read as the app's own (an unreadable config, a Traceback)."""
    import sf_config
    if sf_config._running_tests():
        root = os.environ.get("SHELLFRAME_TEST_ROOT")
        return str(Path(root) / f"shellframe_debug-{os.getpid()}.log") if root \
            else str(Path(_tempfile.gettempdir()) / "shellframe_debug-tests.log")
    return str(TMP_DIR / "shellframe_debug.log")


DEBUG_LOG = _debug_log_path()

_LOG_MAX_BYTES = 1 * 1024 * 1024  # 1MB — auto-truncate to prevent unbounded growth


def _dlog(category: str, msg: str):
    """Append a timestamped line to the debug log. Best-effort, never raises.
    Auto-truncates when file exceeds _LOG_MAX_BYTES (keeps last half)."""
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with open(DEBUG_LOG, 'a', encoding='utf-8') as f:
            f.write(f"{ts} [{category}] {msg}\n")
        # Lazy size check (not every call — amortized via file size)
        try:
            if os.path.getsize(DEBUG_LOG) > _LOG_MAX_BYTES:
                with open(DEBUG_LOG, 'r', encoding='utf-8') as f:
                    content = f.read()
                with open(DEBUG_LOG, 'w', encoding='utf-8') as f:
                    f.write(content[len(content) // 2:])
        except Exception:
            pass
    except Exception:
        pass


def _swallow(context: str):
    """Log-and-swallow for known-safe except paths. The TG poll loop taught
    us that silent `except: pass` reads as "feels unstable, nothing in the
    logs" — same failure, now with a breadcrumb. Call from inside an except
    block; never raises."""
    try:
        import traceback
        exc = traceback.format_exc(limit=2).strip().splitlines()
        _dlog("swallow", f"{context}: {exc[-1] if exc else '?'}")
    except Exception:
        pass
