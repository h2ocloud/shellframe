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
import threading

CONFIG_LOCK = threading.RLock()
