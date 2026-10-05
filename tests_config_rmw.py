"""config.json read-modify-writes must not drop each other's changes.

22 places did load_config() → change → save_config() without holding the config
lock across the update, and the Telegram settings writer used a lock of its
own. Two writers that read the same version then each saved their copy, and
the first change was silently lost (a preset, a bridge setting, a toggle).

The race window is widened by making every read slow (50 ms); writers are then
run concurrently against one temp config file, and every change must survive.
A static check keeps new read-modify-writes under the shared lock.
"""
import ast
import json
import pathlib
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import main  # noqa: E402
import bridge_telegram as bt  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


class _Sandbox:
    """main.py and the Telegram bridge both pointed at one temp config.json,
    with reads slowed down so concurrent writers really overlap."""

    def __enter__(self):
        self.td = tempfile.TemporaryDirectory()
        home = pathlib.Path(self.td.name)
        cfg_dir = home / ".config" / "shellframe"
        cfg_dir.mkdir(parents=True)
        self.file = cfg_dir / "config.json"
        base = dict(main.DEFAULT_CONFIG)
        base["presets"] = []
        base["settings"] = {}
        base["_default_ai_presets_offered"] = [p["name"] for p in getattr(main, "_DEFAULT_AI_PRESETS", [])]
        self.file.write_text(json.dumps(base), encoding="utf-8")
        self.saved = (main.CONFIG_FILE, main.load_config, bt._Path, bt._read_config,
                      getattr(main, "_LAST_GOOD_CONFIG_TEXT", None))
        main.CONFIG_FILE = self.file
        real_load, real_tg_read = main.load_config, bt._read_config

        def slow_load():
            cfg = real_load()
            time.sleep(0.05)
            return cfg

        def slow_tg_read():
            cfg = real_tg_read()
            time.sleep(0.05)
            return cfg

        class _P(pathlib.Path):
            @classmethod
            def home(cls):
                return home
        main.load_config = slow_load
        bt._read_config = slow_tg_read
        bt._Path = _P
        return self

    def __exit__(self, *exc):
        (main.CONFIG_FILE, main.load_config, bt._Path, bt._read_config,
         main._LAST_GOOD_CONFIG_TEXT) = self.saved
        self.td.cleanup()

    def read(self):
        return json.loads(self.file.read_text(encoding="utf-8"))


def _api():
    api = object.__new__(main.Api)
    api.sessions, api.bridge, api.line_bridge, api._api_httpd = {}, None, None, None
    return api


def _run_all(fns):
    threads = [threading.Thread(target=f) for f in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)


def test_concurrent_writers_keep_every_change():
    with _Sandbox() as sb:
        api = _api()
        names = [f"preset{i}" for i in range(6)]
        jobs = [lambda n=n: api.save_preset(n, f"cmd-{n}", "*") for n in names]
        jobs.append(lambda: api.stt_save_settings("local", None))
        jobs.append(lambda: api.set_api_server_enabled(False))
        jobs.append(lambda: bt._update_settings({"tg_written": 1}))
        _run_all(jobs)
        cfg = sb.read()
        got = sorted(p["name"] for p in cfg.get("presets", []))
        check("six presets saved at once are all kept", got == names, f"kept {got}")
        check("a mixin write (STT backend) is kept",
              (cfg.get("bridge") or {}).get("stt_backend") == "local", cfg.get("bridge"))
        check("another mixin write (api_server toggle) is kept",
              (cfg.get("api_server") or {}).get("enabled") is False, cfg.get("api_server"))
        check("the Telegram bridge's settings write is kept",
              (cfg.get("settings") or {}).get("tg_written") == 1, cfg.get("settings"))


def test_bridge_and_app_share_one_lock():
    lock = getattr(main, "_CONFIG_LOCK", None)
    sf_config = sys.modules.get("sf_config")
    check("main.py's config lock is the shared sf_config lock",
          sf_config is not None and lock is sf_config.CONFIG_LOCK)
    src = (HERE / "bridge_telegram.py").read_text(encoding="utf-8")
    fn = src[src.index("def _update_settings"):]
    fn = fn[:fn.index("\n\n\n")]
    check("the Telegram settings writer holds the shared lock", "_sf_config.CONFIG_LOCK" in fn)


def _rmw_violations():
    """Functions that save config without holding the lock around load+save."""
    bad = []
    for path in [HERE / "main.py"] + sorted(HERE.glob("api_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name in ("load_config", "save_config", "update_config", "_save_config_locked"):
                continue

            def name(c):
                return c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
            calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
            if not any(name(c) == "load_config" for c in calls):
                continue
            locked = [w for w in ast.walk(fn) if isinstance(w, ast.With) and any(
                isinstance(i.context_expr, ast.Name) and i.context_expr.id in ("_CONFIG_LOCK", "CONFIG_LOCK")
                for i in w.items)]
            for c in calls:
                if name(c) != "save_config":
                    continue
                inside = [w for w in locked if w.lineno <= c.lineno <= w.end_lineno
                          and any(isinstance(x, ast.Call) and name(x) == "load_config" for x in ast.walk(w))]
                if not inside:
                    bad.append(f"{path.name}:{c.lineno} {fn.name}")
    return bad


def test_every_read_modify_write_holds_the_lock():
    bad = _rmw_violations()
    check("every load_config → save_config runs under the config lock", not bad,
          "use `with _CONFIG_LOCK:` (main.py) / `with CONFIG_LOCK:` (mixins) or update_config(): "
          + ", ".join(bad))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("ALL PASS")
