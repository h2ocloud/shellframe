"""Offline tests for ShellFrame's per-provider account profiles."""

import base64
import json
import os
import tempfile

from account_manager import AccountManager


def _jwt(payload):
    def enc(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return enc({"alg": "none"}) + "." + enc(payload) + ".sig"


def test_current_accounts_are_discovered_without_secrets_in_state():
    with tempfile.TemporaryDirectory() as td:
        home = os.path.join(td, "home")
        os.makedirs(os.path.join(home, ".codex"))
        os.makedirs(os.path.join(home, ".claude"))
        codex_token = _jwt({
            "https://api.openai.com/profile": {"email": "codex@example.test"},
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
        })
        json.dump({"tokens": {"access_token": codex_token, "account_id": "acct-1"}},
                  open(os.path.join(home, ".codex", "auth.json"), "w"))
        json.dump({"oauthAccount": {"emailAddress": "claude@example.test",
                                     "organizationName": "Example"}},
                  open(os.path.join(home, ".claude.json"), "w"))

        manager = AccountManager(root=os.path.join(td, "profiles"), home=home,
                                 keychain_getter=lambda: {
                                     "claudeAiOauth": {
                                         "accessToken": "claude-secret",
                                         "refreshToken": "refresh-secret",
                                         "subscriptionType": "team",
                                     }
                                 })
        cfg = {}
        changed = manager.ensure(cfg)
        assert changed
        assert cfg["accounts"]["global"]["codex"]
        assert cfg["accounts"]["global"]["claude"]
        safe = json.dumps(manager.safe_state(cfg))
        assert "codex-secret" not in safe
        assert "claude-secret" not in safe
        assert "codex@example.test" in safe
        assert "claude@example.test" in safe


def test_session_refs_snapshot_global_and_env_is_profile_specific():
    with tempfile.TemporaryDirectory() as td:
        manager = AccountManager(root=os.path.join(td, "profiles"), home=td,
                                 keychain_getter=lambda: {})
        cfg = {"accounts": {"global": {"codex": "codex-a", "claude": "claude-a"},
                            "profiles": {"codex": [{"id": "codex-a", "email": "a@test"}],
                                         "claude": [{"id": "claude-a", "email": "a@test"}]},
                            "sessions": {}}}
        manager.write_profile("codex", "codex-a", {"tokens": {"access_token": "secret-a"}})
        manager.write_profile("codex", "codex-b", {"tokens": {"access_token": "secret-b"}})

        refs = manager.session_refs(cfg)
        assert refs == {"codex": "codex-a", "claude": "claude-a"}
        refs["codex"] = "codex-b"
        assert cfg["accounts"]["global"]["codex"] == "codex-a"
        env = manager.env_for("codex", "codex-b")
        assert env["CODEX_HOME"].endswith(os.path.join("codex", "codex-b"))
        assert os.path.exists(os.path.join(env["CODEX_HOME"], "auth.json"))


def test_switching_global_does_not_mutate_existing_session_snapshot():
    with tempfile.TemporaryDirectory() as td:
        manager = AccountManager(root=os.path.join(td, "profiles"), home=td,
                                 keychain_getter=lambda: {})
        cfg = {"accounts": {"global": {"codex": "codex-a", "claude": None},
                            "profiles": {"codex": [{"id": "codex-a", "email": "a"},
                                                     {"id": "codex-b", "email": "b"}],
                                         "claude": []},
                            "sessions": {"s1": {"codex": "codex-a", "claude": None}}}}
        manager.write_profile("codex", "codex-a", {"tokens": {"access_token": "a"}})
        manager.write_profile("codex", "codex-b", {"tokens": {"access_token": "b"}})
        before = dict(manager.session_refs(cfg, "s1"))
        manager.set_global(cfg, "codex", "codex-b")
        after = dict(manager.session_refs(cfg, "s1"))
        assert before == after == {"codex": "codex-a", "claude": None}
        assert cfg["accounts"]["global"]["codex"] == "codex-b"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
