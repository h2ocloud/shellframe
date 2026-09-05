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


def test_env_for_blank_ref_returns_no_overrides():
    """沒釘 profile 的舊 tab（ref=None）要回空 env，不能炸也不能指到 provider 根目錄。"""
    with tempfile.TemporaryDirectory() as td:
        manager = AccountManager(root=os.path.join(td, "profiles"), home=td,
                                 keychain_getter=lambda: {})
        manager.write_profile("codex", "codex-a", {"tokens": {"access_token": "a"}})
        for ref in (None, "", 0, {}):
            assert manager.env_for("codex", ref) == {}, ref
        assert manager.env_for("codex", "codex-a")["CODEX_HOME"]


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


def test_account_env_reaches_tmux_new_session_via_dash_e():
    """回歸（Howard 2026-09-05：切帳號後對話消失、要重新 /login）。

    根因：`tmux new-session` 是從 tmux SERVER 的環境 spawn pane，client 的
    `env=` 在 server 已存在時（多分頁時必然）被忽略。所以帳號 env
    （CLAUDE_CONFIG_DIR / CLAUDE_CODE_OAUTH_TOKEN）必須用 `-e KEY=VAL` 傳，
    否則新 pane 拿不到、等於用預設帳號啟動＝看起來沒登入。
    這裡直接攔 tmux new-session 的 argv，確認帳號 env 有進 `-e`。
    """
    import main

    saved = {
        "run": main.subprocess.run,
        "exists": main._tmux_session_exists,
        "hastmux": main._has_tmux,
        "fork": main.pty.fork,
        "thread": main.threading.Thread,
        "env_for": main.ACCOUNT_MANAGER.env_for,
    }
    captured = []

    class _R:
        returncode = 0
        stdout = b""
        stderr = b""

    class _NoThread:
        def __init__(self, *a, **k):
            pass
        def start(self):
            pass

    try:
        main.subprocess.run = lambda argv, *a, **k: (captured.append(list(argv)) or _R())
        main._tmux_session_exists = lambda name: False
        main._has_tmux = lambda: True
        main.pty.fork = lambda: (4242, 9)          # parent branch → 不真的 exec
        main.threading.Thread = _NoThread            # 別讓 reader 讀壞 fd
        main.ACCOUNT_MANAGER.env_for = lambda provider, ref: {
            "CLAUDE_CONFIG_DIR": "/tmp/sf-profiles/claude/claude-b",
            "CLAUDE_CODE_OAUTH_TOKEN": "tok-abc123",
        }
        main.Session("sTEST", "claude", 80, 24, account_refs={"claude": "claude-b"})
    finally:
        main.subprocess.run = saved["run"]
        main._tmux_session_exists = saved["exists"]
        main._has_tmux = saved["hastmux"]
        main.pty.fork = saved["fork"]
        main.threading.Thread = saved["thread"]
        main.ACCOUNT_MANAGER.env_for = saved["env_for"]

    new_sessions = [c for c in captured
                    if len(c) > 1 and c[0] == "tmux" and c[1] == "new-session"]
    assert new_sessions, "沒有攔到 tmux new-session"
    argv = new_sessions[0]
    assert "CLAUDE_CONFIG_DIR=/tmp/sf-profiles/claude/claude-b" in argv, argv
    assert "CLAUDE_CODE_OAUTH_TOKEN=tok-abc123" in argv, argv
    # 每個帳號 env 前面都要有一個 `-e`
    assert argv.count("-e") >= 3, argv   # SF_SID + 2 個帳號 var


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
