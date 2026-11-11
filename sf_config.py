"""The one lock every config.json writer holds.

config.json is written from several places: main.py's save_config / update_config,
the Api mixins, and the Telegram bridge's settings writer. Each read-modify-write
must hold this lock from the read through the write, otherwise two writers that
read the same version silently drop one another's change. Reads do not take it;
writes are atomic (temp file + os.replace), so a reader never sees a torn file.

It lives in its own leaf module so the mixins and the bridge can import it
directly (neither may import main.py). Re-entrant: save_config and the
migrations inside load_config may run while it is held.
"""
import os
import sys
import tempfile
import threading
from pathlib import Path

CONFIG_LOCK = threading.RLock()


def _running_tests() -> bool:
    """A test is the program: run_tests.sh says so, or a tests_*.py was started by hand."""
    if os.environ.get("SHELLFRAME_TESTS") == "1":
        return True
    entry = getattr(sys.modules.get("__main__"), "__file__", "") or (sys.argv[0] if sys.argv else "")
    return os.path.basename(entry).startswith(("tests_", "test_"))


def config_dir() -> Path:
    """The app's folder: ~/.config/shellframe (config.json, account profiles, Telegram state),
    or SHELLFRAME_CONFIG_DIR. A test never gets the real one: tests that loaded main read the
    running app's config.json, and one migration wrote into it. Without the variable each test
    process gets a folder of its own, and the processes it starts inherit it."""
    env = os.environ.get("SHELLFRAME_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    if _running_tests():               # one per test process, so tests cannot see each other's
        root = os.environ.get("SHELLFRAME_TEST_ROOT") or None   # run_tests.sh removes it afterwards
        os.environ["SHELLFRAME_CONFIG_DIR"] = tempfile.mkdtemp(prefix="shellframe-test-config-", dir=root)
        return Path(os.environ["SHELLFRAME_CONFIG_DIR"])
    return Path.home() / ".config" / "shellframe"


def config_file() -> Path:
    return config_dir() / "config.json"


def state_dir() -> Path:
    """~/.local/state/shellframe: what the app keeps between runs (the A2A log, handoffs). A test
    gets a folder inside its own config folder: the suite wrote its refusals into the real A2A
    log, where `sfctl a2a-log` showed them as the app's own."""
    if _running_tests():
        return config_dir() / "state"
    return Path.home() / ".local" / "state" / "shellframe"
