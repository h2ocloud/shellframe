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
    """回歸（日常使用中回報：切帳號後對話消失、要重新 /login）。

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


def test_account_switch_carries_transcript_and_resumes_uuid():
    """回歸（日常使用中回報：切帳號後對話歷史消失，應該用 uuid resume）。

    切帳號＝換 CLAUDE_CONFIG_DIR，新帳號 projects 裡沒有這段對話→--resume 找不到。
    這裡驗證：(1) transcript 被搬進新帳號的 projects/<同一 slug>/；
    (2) 啟動指令被改成 --resume <uuid>。"""
    import types
    import main

    with tempfile.TemporaryDirectory() as td:
        old_dir = os.path.join(td, "cfg-A")
        new_dir = os.path.join(td, "cfg-B")
        slug = "-Users-alice-proj"
        csid = "11111111-2222-3333-4444-555555555555"
        src = os.path.join(old_dir, "projects", slug, f"{csid}.jsonl")
        os.makedirs(os.path.dirname(src))
        with open(src, "w") as f:
            f.write('{"type":"user","message":"hello history"}\n')

        saved_env_for = main.ACCOUNT_MANAGER.env_for
        try:
            main.ACCOUNT_MANAGER.env_for = lambda provider, ref: (
                {"CLAUDE_CONFIG_DIR": old_dir} if ref == "A"
                else {"CLAUDE_CONFIG_DIR": new_dir} if ref == "B"
                else {})
            api = object.__new__(main.Api)          # 跳過重量級 __init__
            old_session = types.SimpleNamespace(
                account_refs={"claude": "A"}, _hook_transcript_path=src)
            api._carry_claude_transcript(old_session, csid, {"claude": "B"})
        finally:
            main.ACCOUNT_MANAGER.env_for = saved_env_for

        dst = os.path.join(new_dir, "projects", slug, f"{csid}.jsonl")
        assert os.path.isfile(dst), "transcript 沒被搬到新帳號的 projects"
        assert open(dst).read() == open(src).read(), "搬移後內容不一致"

    # 啟動指令要變成 --resume <uuid>（丟掉舊的 --session-id）
    resumed = main.Api._cmd_with_resume(
        "claude --model opus --session-id old-id --dangerously-skip-permissions", csid)
    assert "--resume" in resumed and csid in resumed, resumed
    assert "--session-id" not in resumed, resumed


def _write_claude_json(home, email, org, org_tier, user_tier):
    json.dump({"oauthAccount": {
        "emailAddress": email, "organizationName": org,
        "organizationRateLimitTier": org_tier,
        "userRateLimitTier": user_tier,
    }}, open(os.path.join(home, ".claude.json"), "w"))


def test_discover_flags_credential_metadata_mismatch():
    """回歸（日常使用中回報：兩個帳號都顯示同一人的用量）。

    切帳號殘留 → ~/.claude.json 說 team、keychain token 其實是個人。
    discover 要標記 mismatch，capture/ensure 才擋得下。"""
    with tempfile.TemporaryDirectory() as td:
        home = os.path.join(td, "home")
        os.makedirs(home)
        # 帳號資料是 team（org/user tier = team），token 卻是個人（pro / default_claude_ai）
        _write_claude_json(home, "team@example.test", "Example Org",
                           "default_raven", "default_claude_max_5x")
        manager = AccountManager(root=os.path.join(td, "profiles"), home=home,
                                 keychain_getter=lambda: {"claudeAiOauth": {
                                     "accessToken": "personal-token",
                                     "subscriptionType": "pro",
                                     "rateLimitTier": "default_claude_ai",
                                 }})
        d = manager.discover("claude")
        assert d and d.get("mismatch"), "沒抓到 token 與帳號資料不一致"
        assert d["mismatch"]["token_plan"] == "pro"

        # ensure() 不該把對不上的 profile 記進去
        cfg = {}
        manager.ensure(cfg)
        assert not (cfg["accounts"]["profiles"].get("claude")), \
            "mismatch 的帳號不該被 ensure 記錄"


def test_discover_consistent_account_has_no_mismatch():
    """一致的帳號（token tier 落在 .claude.json 的 tier 集合裡）不該誤判。"""
    with tempfile.TemporaryDirectory() as td:
        home = os.path.join(td, "home")
        os.makedirs(home)
        _write_claude_json(home, "team@example.test", "Example Org",
                           "default_raven", "default_claude_max_5x")
        manager = AccountManager(root=os.path.join(td, "profiles"), home=home,
                                 keychain_getter=lambda: {"claudeAiOauth": {
                                     "accessToken": "team-token",
                                     "subscriptionType": "team",
                                     "rateLimitTier": "default_claude_max_5x",
                                 }})
        d = manager.discover("claude")
        assert d and not d.get("mismatch"), "一致的帳號被誤判成 mismatch"
        cfg = {}
        manager.ensure(cfg)
        assert cfg["accounts"]["profiles"]["claude"], "一致的帳號應該被記錄"


def test_discover_missing_tiers_fails_open():
    """舊帳號沒有 rateLimitTier 欄位時不判定 mismatch（fail-open，別擋正常登入）。"""
    with tempfile.TemporaryDirectory() as td:
        home = os.path.join(td, "home")
        os.makedirs(home)
        json.dump({"oauthAccount": {"emailAddress": "a@test", "organizationName": "X"}},
                  open(os.path.join(home, ".claude.json"), "w"))
        manager = AccountManager(root=os.path.join(td, "profiles"), home=home,
                                 keychain_getter=lambda: {"claudeAiOauth": {
                                     "accessToken": "tok", "subscriptionType": "pro"}})
        d = manager.discover("claude")
        assert d and not d.get("mismatch")


def test_carry_transcript_noop_when_same_config_dir():
    """同帳號（config dir 沒變）不必搬，也不該炸。"""
    import types
    import main
    with tempfile.TemporaryDirectory() as td:
        d = os.path.join(td, "cfg")
        os.makedirs(os.path.join(d, "projects", "slug"))
        saved = main.ACCOUNT_MANAGER.env_for
        try:
            main.ACCOUNT_MANAGER.env_for = lambda p, r: {"CLAUDE_CONFIG_DIR": d}
            api = object.__new__(main.Api)
            old = types.SimpleNamespace(account_refs={"claude": "A"},
                                        _hook_transcript_path="/nonexistent")
            api._carry_claude_transcript(old, "some-uuid", {"claude": "A"})  # 不該丟例外
        finally:
            main.ACCOUNT_MANAGER.env_for = saved


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
