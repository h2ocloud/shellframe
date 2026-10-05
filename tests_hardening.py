"""Regression tests for the robustness fixes found in the architecture review.

Each case pins a defect that failed silently (the error was swallowed or the
damage only showed up later):

* config.json read while another writer was mid-write turned into DEFAULT_CONFIG,
  and the next save wiped presets, accounts, the bot token and paired peers;
* the Telegram settings writer was non-atomic and rewrote the whole file from
  ``{}`` when it could not parse it;
* two tabs opened at once could get the same sid;
* the LINE bridge rejected the keywords the host registers tabs with, so
  late-restored tabs never reached LINE;
* /group was in the Telegram bot menu but never reached its handler;
* the status monitor iterated dicts other threads were changing.
"""
import ast
import inspect
import json
import os
import pathlib
import re
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import main  # noqa: E402
import bridge_telegram as bt  # noqa: E402
import bridge_line  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def test_unreadable_config_keeps_last_good_copy():
    with tempfile.TemporaryDirectory() as td:
        saved_file = main.CONFIG_FILE
        saved_text = getattr(main, "_LAST_GOOD_CONFIG_TEXT", None)
        main.CONFIG_FILE = pathlib.Path(td) / "config.json"
        main._LAST_GOOD_CONFIG_TEXT = None
        try:
            good = {"presets": [{"name": "Mine", "cmd": "my-cli"}],
                    "bridge": {"bot_token": "kept"}}
            main.CONFIG_FILE.write_text(json.dumps(good), encoding="utf-8")
            first = main.load_config()
            check("a readable config loads", first.get("bridge", {}).get("bot_token") == "kept")
            main.CONFIG_FILE.write_text('{"presets": [{"name": "Mi', encoding="utf-8")  # torn write
            second = main.load_config()
            check("a torn config.json does not become the defaults",
                  second.get("bridge", {}).get("bot_token") == "kept",
                  f"got keys {sorted(second)}")
            # (falling back may already have healed the file through a migration save)
            main.CONFIG_FILE.write_text('{"presets": [{"name": "Mi', encoding="utf-8")
            main._LAST_GOOD_CONFIG_TEXT = None
            third = main.load_config()
            check("with no good copy at all, the defaults still come back",
                  third == main.DEFAULT_CONFIG)
        finally:
            main.CONFIG_FILE, main._LAST_GOOD_CONFIG_TEXT = saved_file, saved_text


class _HomeIn:
    """Point bridge_telegram's home directory at a temp dir."""

    def __init__(self, td):
        self.td = td

    def __enter__(self):
        td = self.td

        class _P(pathlib.Path):
            @classmethod
            def home(cls):
                return pathlib.Path(td)
        self.saved = bt._Path
        bt._Path = _P
        cfg_dir = pathlib.Path(td) / ".config" / "shellframe"
        cfg_dir.mkdir(parents=True)
        return cfg_dir / "config.json"

    def __exit__(self, *exc):
        bt._Path = self.saved


def test_tg_settings_write_is_atomic_and_never_blanks_the_file():
    with tempfile.TemporaryDirectory() as td, _HomeIn(td) as cfg_file:
        cfg_file.write_text(json.dumps({"presets": [1, 2], "settings": {"a": 1}}), encoding="utf-8")
        replaced = []
        real_replace = bt._os.replace
        bt._os.replace = lambda src, dst: (replaced.append((str(src), str(dst))), real_replace(src, dst))
        try:
            ok = bt._update_settings({"b": 2})
        finally:
            bt._os.replace = real_replace
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
        check("settings write succeeds", ok is True)
        check("settings write goes through a temp file + os.replace",
              len(replaced) == 1 and replaced[0][1] == str(cfg_file))
        check("other config keys survive", data.get("presets") == [1, 2]
              and data["settings"] == {"a": 1, "b": 2})
        cfg_file.write_text('{"presets": [1, 2], "settin', encoding="utf-8")  # unreadable
        ok = bt._update_settings({"c": 3})
        check("an unreadable config.json is not overwritten with only settings",
              ok is False and cfg_file.read_text(encoding="utf-8").startswith('{"presets"'))


def test_sid_allocation_is_unique_under_concurrency():
    api = object.__new__(main.Api)
    api._counter = 0
    sids, lock = [], threading.Lock()

    def grab():
        mine = [api._next_sid() for _ in range(400)]
        with lock:
            sids.extend(mine)
    threads = [threading.Thread(target=grab) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("12 threads x 400 new tabs get 4800 distinct sids", len(set(sids)) == 4800,
          f"{len(sids) - len(set(sids))} duplicates")
    src = inspect.getsource(main.Api.new_session)
    check("new_session takes its sid from the locked allocator",
          "self._next_sid()" in src and "self._counter += 1" not in src)


def _register_kwargs_used_by_host():
    tree = ast.parse((HERE / "api_bridges.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register_session"):
            names |= {k.arg for k in node.keywords if k.arg}
    return names


def test_both_bridges_accept_the_hosts_registration_keywords():
    used = _register_kwargs_used_by_host()
    check("the host passes registration keywords", {"prepare_fn", "cmd", "cols", "rows"} <= used,
          f"{sorted(used)}")
    for cls in (bt.TelegramBridge, bridge_line.LineBridge):
        params = inspect.signature(cls.register_session).parameters
        missing = sorted(k for k in used if k not in params)
        check(f"{cls.__name__}.register_session accepts {sorted(used)}", not missing,
              f"missing {missing}")
    check("LineBridge has refresh_commands like TelegramBridge",
          callable(getattr(bridge_line.LineBridge, "refresh_commands", None)))


def test_every_menu_command_reaches_the_bridge():
    s = (HERE / "bridge_telegram.py").read_text(encoding="utf-8")
    start = s.index("def _set_bot_commands")
    menu = re.findall(r'"command":\s*"([^"]+)"', s[start:s.index("\n    def ", start + 10)])
    allow = re.search(r"if cmd in \(('list'.*?)\) or cmd\.isdigit\(\):", s).group(1)
    allowed = set(re.findall(r"'([^']+)'", allow))
    missing = [c for c in menu if c not in allowed]
    check("every command in the bot menu is handled by the bridge", not missing, f"missing {missing}")


def test_status_monitor_snapshots_shared_dicts():
    src = (HERE / "api_status.py").read_text(encoding="utf-8")
    check("stale-entry sweep iterates a snapshot of the status cache", "in list(cache)" in src)
    check("stale-entry sweep iterates a snapshot of the hook events", "in list(self._hook_events)" in src)


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
