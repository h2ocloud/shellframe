#!/usr/bin/env python3
"""Codex account fallback: relaunch env and model-cache lookup."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="sf-codex-profile-fallback-"))
os.environ["SHELLFRAME_CONFIG_DIR"] = str(TMP / "config")
os.environ["HOME"] = str(TMP / "home")
os.environ["CODEX_HOME"] = str(TMP / "env-codex")
os.environ["SHELLFRAME_TESTS"] = "1"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402
import agent_model  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


GLOBAL = "codex-global"
PINNED = "codex-pinned"
profile_root = Path(main.CONFIG_DIR) / "account-profiles" / "codex"
global_dir = profile_root / GLOBAL
pinned_dir = profile_root / PINNED
global_dir.mkdir(parents=True, exist_ok=True)
pinned_dir.mkdir(parents=True, exist_ok=True)
(Path(os.environ["CODEX_HOME"])).mkdir(parents=True, exist_ok=True)
(Path.home() / ".codex").mkdir(parents=True, exist_ok=True)


def config(global_ref=GLOBAL):
    return {"accounts": {
        "global": {"codex": global_ref, "claude": None},
        "profiles": {"codex": [{"id": GLOBAL}, {"id": PINNED}], "claude": []},
        "sessions": {},
    }}


def write_config(value):
    main.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    main.CONFIG_FILE.write_text(json.dumps(value), encoding="utf-8")


print("relaunch account refs")
cfg = config()
resolved = main.ACCOUNT_MANAGER.refs_with_global_fallback(cfg, {"codex": None, "claude": None})
env = main.Session._account_env_overrides(SimpleNamespace(account_refs=resolved))
check("null ref uses the global Codex profile", env.get("CODEX_HOME") == str(global_dir), env)

captured = {}
real_session_class = main.Session


class FakeSession:
    def __init__(self, sid, cmd, cols, rows, on_data=None, tmux_name=None, account_refs=None, **kwargs):
        captured["env"] = real_session_class._account_env_overrides(SimpleNamespace(account_refs=account_refs))
        self.sid, self.cmd, self.cols, self.rows = sid, cmd, cols, rows
        self._tmux_name, self.account_refs = tmux_name, account_refs
        self._bridge_enabled = True


old = SimpleNamespace(cmd="codex", cols=120, rows=30, _tmux_name="sf_s1",
                      account_refs={"codex": None, "claude": None}, kill=lambda: None,
                      session_id="")
api = object.__new__(main.Api)
api.sessions = {"s1": old}
api.bridge = api.line_bridge = None
api._output_event = SimpleNamespace(set=lambda: None)
api._account_config = lambda: cfg
api._codex_session_id = lambda *_: ""
api._bots_tab_launch_cmd = lambda _sid, cmd: cmd
api._start_startup_trust_watcher = lambda *_: None
api._persist_session_manifest = lambda: None
api._notify_ui_sessions_changed = lambda: None
saved_session_class = main.Session
main.Session = FakeSession
try:
    api._restart_session_for_account("s1", dict(old.account_refs))
finally:
    main.Session = saved_session_class
check("relaunch resolves null ref before creating the session",
      captured.get("env", {}).get("CODEX_HOME") == str(global_dir), captured)

resolved = main.ACCOUNT_MANAGER.refs_with_global_fallback(cfg, {"codex": PINNED, "claude": None})
env = main.Session._account_env_overrides(SimpleNamespace(account_refs=resolved))
check("an explicit ref is unchanged", env.get("CODEX_HOME") == str(pinned_dir), env)

resolved = main.ACCOUNT_MANAGER.refs_with_global_fallback(config(None), {"codex": None, "claude": None})
env = main.Session._account_env_overrides(SimpleNamespace(account_refs=resolved))
check("no global ref keeps the provider default", "CODEX_HOME" not in env, env)


def write_cache(directory, slug):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": slug, "display_name": slug, "visibility": "list", "priority": 1},
    ]}), encoding="utf-8")


print("model cache priority")
write_config(config())
write_cache(global_dir, "gpt-9-global")
write_cache(pinned_dir, "gpt-9-pinned")
write_cache(Path(os.environ["CODEX_HOME"]), "gpt-9-env")
write_cache(Path.home() / ".codex", "gpt-9-home")
agent_model._CODEX_CACHE.update(key=None, models=[])
check("global profile cache wins over CODEX_HOME", [m["key"] for m in agent_model.codex_models()] == ["gpt-9-global"])
check("explicit account ref reads that profile", [m["key"] for m in agent_model.codex_models(account_ref=PINNED)] == ["gpt-9-pinned"])

(global_dir / "models_cache.json").unlink()
agent_model._CODEX_CACHE.update(key=None, models=[])
check("missing global cache falls back to CODEX_HOME", [m["key"] for m in agent_model.codex_models()] == ["gpt-9-env"])

(Path(os.environ["CODEX_HOME"]) / "models_cache.json").unlink()
agent_model._CODEX_CACHE.update(key=None, models=[])
check("missing CODEX_HOME cache falls back to home", [m["key"] for m in agent_model.codex_models()] == ["gpt-9-home"])

write_config(config(None))
agent_model._CODEX_CACHE.update(key=None, models=[])
check("no global profile starts with CODEX_HOME", [m["key"] for m in agent_model.codex_models()] == ["gpt-9-home"])

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
