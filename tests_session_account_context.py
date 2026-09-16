#!/usr/bin/env python3
"""狀態與模型解析必須跟著分頁自己的帳號目錄走（v0.35.8）。

多帳號是靠 provider 的 config 目錄做的：codex 吃 `CODEX_HOME`、claude 吃
`CLAUDE_CONFIG_DIR`，帳號管理把 profile 指到 `account-profiles/<provider>/<ref>`。
transcript、config、模型全都寫在那個目錄底下。

修之前的缺陷（已隔離重現）：codex resolver 呼叫 lsof 時寫死比對字串
`/.codex/sessions/`，那條字串永遠不會命中 profile 底下的 rollout
（`account-profiles/codex/<ref>/sessions/…` 的 codex 前面沒有點），於是切過帳號的
分頁一律落空，掉進「整棵樹最新 mtime 那一份」的 fallback——讀到不相干的全域
rollout，也就是別的分頁的對話。Codex 分支也完全沒有用 `transcript_hint`。

這支驗：兩個帳號同 cwd 同時輸出、各自對到自己的 rollout；缺檔時回 None 而不是
去讀另一個帳號；claude 的 projects 根目錄同樣跟著帳號；模型 config fallback 也
讀自己那份。

跑法：.venv/bin/python tests_session_account_context.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import agent_status as A  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def touch(path, body="{}\n"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return str(path)


UUID_A = "aaaaaaaa-1111-2222-3333-444444444444"
UUID_B = "bbbbbbbb-1111-2222-3333-444444444444"
UUID_GLOBAL = "cccccccc-1111-2222-3333-444444444444"

td = tempfile.mkdtemp(prefix="sf-acct-ctx-")
prof_a = os.path.join(td, "account-profiles", "codex", "acct-a")
prof_b = os.path.join(td, "account-profiles", "codex", "acct-b")
roll_a = touch(os.path.join(prof_a, "sessions", "2026", "09", "07",
                            f"rollout-2026-09-07T10-00-00-{UUID_A}.jsonl"))
roll_b = touch(os.path.join(prof_b, "sessions", "2026", "09", "07",
                            f"rollout-2026-09-07T10-00-01-{UUID_B}.jsonl"))
global_root = os.path.join(td, "global", ".codex", "sessions")
roll_global = touch(os.path.join(global_root, "2026", "09", "07",
                                 f"rollout-2026-09-07T23-59-59-{UUID_GLOBAL}.jsonl"))
# 全域那份刻意做成最新的——舊 fallback 會挑它
os.utime(roll_global, (2 ** 31 - 1, 2 ** 31 - 1))

# ── 路徑解析 ──
check("沒 pin profile → 用 provider 預設位置",
      A.codex_sessions_root({}) == A.CODEX_SESSIONS)
check("pin 了 profile → 用那個帳號的 sessions",
      A.codex_sessions_root({"config_dir": prof_a})
      == os.path.join(prof_a, "sessions"))
check("_under 是前綴比對不是子字串",
      A._under(roll_a, os.path.join(prof_a, "sessions"))
      and not A._under(roll_b, os.path.join(prof_a, "sessions")))

# ── lsof：profile 底下的 rollout 要被接受 ──
def fake_lsof(open_path):
    def run(argv, **kw):
        return types.SimpleNamespace(
            returncode=0,
            stdout=f"codex 123 user 5w REG 1 100 2 {open_path}\n")
    return run


orig_run = A.subprocess.run
try:
    A.subprocess.run = fake_lsof(roll_a)
    hit = A._lsof_open_jsonl([123], [os.path.join(prof_a, "sessions")])
    check("profile 底下開著的 rollout 會被 lsof 接受", hit == roll_a, str(hit))
    hit = A._lsof_open_jsonl([123], [os.path.join(prof_b, "sessions")])
    check("別的帳號的根目錄不會誤收", hit is None, str(hit))
    hit = A._lsof_open_jsonl([123], [global_root])
    check("全域根目錄也不會誤收 profile 的檔", hit is None, str(hit))
finally:
    A.subprocess.run = orig_run

# ── resolve_transcript：兩個帳號各自對到自己的 ──
orig_pane, orig_tree, orig_sessions = (
    A._tmux_pane_pid, A._pid_tree, A.CODEX_SESSIONS)
try:
    A._tmux_pane_pid = lambda name: 123
    A._pid_tree = lambda pid: [pid]
    A.CODEX_SESSIONS = global_root

    A.subprocess.run = fake_lsof(roll_a)
    got = A.resolve_transcript({"cmd": "sf-codex", "tmux_name": "sf_a",
                                "config_dir": prof_a})
    check("帳號 A 的分頁解析到 A 的 rollout（不是全域最新那份）",
          got == roll_a, f"{got}\n期望 {roll_a}")

    A.subprocess.run = fake_lsof(roll_b)
    got = A.resolve_transcript({"cmd": "sf-codex", "tmux_name": "sf_b",
                                "config_dir": prof_b})
    check("帳號 B 的分頁解析到 B 的 rollout", got == roll_b, str(got))

    # lsof 命中的是別的帳號的檔 → 不能收，也不能退全域
    A.subprocess.run = fake_lsof(roll_b)
    got = A.resolve_transcript({"cmd": "sf-codex", "tmux_name": "sf_a",
                                "config_dir": prof_a})
    check("process 開著的是別帳號的檔 → 回 None，不讀別人的",
          got is None, str(got))

    # 完全認不出來 → None，不是「全域最新的那一份」
    A.subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="")
    got = A.resolve_transcript({"cmd": "sf-codex", "tmux_name": "sf_a",
                                "config_dir": prof_a})
    check("認不出來時回 None（不再挑全域最新的 rollout）", got is None, str(got))

    # 記住的 uuid 是精確錨點
    got = A.resolve_transcript({"cmd": "sf-codex", "tmux_name": "sf_a",
                                "config_dir": prof_a,
                                "codex_session_id": UUID_A})
    check("記住的 rollout uuid 能精確對回自己那份", got == roll_a, str(got))
    got = A.resolve_transcript({"cmd": "sf-codex", "tmux_name": "sf_a",
                                "config_dir": prof_a,
                                "codex_session_id": UUID_B})
    check("uuid 屬於別的帳號 → 在自己目錄裡找不到 → None", got is None, str(got))

    # transcript_hint 有被 codex 分支採用（以前完全沒用）
    got = A.resolve_transcript({"cmd": "sf-codex", "tmux_name": "sf_a",
                                "config_dir": prof_a,
                                "transcript_hint": roll_a})
    check("codex 分支現在會採用 transcript_hint", got == roll_a, str(got))
finally:
    A._tmux_pane_pid, A._pid_tree, A.CODEX_SESSIONS = (
        orig_pane, orig_tree, orig_sessions)
    A.subprocess.run = orig_run

# ── claude：projects 根目錄跟著帳號 ──
cprof = os.path.join(td, "account-profiles", "claude", "acct-a")
slug = A._cwd_slug("/Users/alice/proj")
mine = touch(os.path.join(cprof, "projects", slug, f"{UUID_A}.jsonl"))
touch(os.path.join(td, "global-claude", "projects", slug, f"{UUID_A}.jsonl"))
orig_cp = A.CLAUDE_PROJECTS
try:
    A.CLAUDE_PROJECTS = os.path.join(td, "global-claude", "projects")
    got = A.resolve_transcript({"cmd": "claude", "cwd": "/Users/alice/proj",
                                "session_id": UUID_A, "config_dir": cprof})
    check("claude 的 transcript 從自己帳號的 projects/ 取", got == mine, str(got))
    got = A.resolve_transcript({"cmd": "claude", "cwd": "/Users/alice/proj",
                                "session_id": UUID_A})
    check("沒 pin profile 的 claude 分頁走全域 projects/",
          got == os.path.join(A.CLAUDE_PROJECTS, slug, f"{UUID_A}.jsonl"), str(got))
finally:
    A.CLAUDE_PROJECTS = orig_cp

# ── 模型 config fallback 也讀自己那份 ──
touch(os.path.join(prof_a, "config.toml"), 'model = "gpt-5-account-a"\n')
touch(os.path.join(prof_b, "config.toml"), 'model = "gpt-5-account-b"\n')
A._model_file_cache.clear()
info = A.detect_model_info({"cmd": "sf-codex", "config_dir": prof_a})
check("codex 模型 fallback 讀自己帳號的 config.toml",
      info and "account-a" in (info.get("name") or "").lower(), str(info))
info = A.detect_model_info({"cmd": "sf-codex", "config_dir": prof_b})
check("換帳號就換成另一份 config.toml",
      info and "account-b" in (info.get("name") or "").lower(), str(info))

# ── _live_config_dir：以 CLI 真正吃的環境變數為準 ──────────────────────────
# 實測有分頁的 tmux env 帶著 CODEX_HOME=<profile>、卻沒有 SF_ACCOUNT_CODEX
# marker（那個 marker 只在建立分頁時「有 ref 才寫」）。還原後 account_refs 是
# None，ShellFrame 以為沒 pin 帳號 → 解析回頭找全域路徑 → 那個 process 的
# rollout 根本不在全域樹裡，於是舊版掉進「全域最新一份」讀到別人的對話。
import main as _main  # noqa: E402

api = object.__new__(_main.Api)


class _FakeSession:
    def __init__(self, cmd, tmux_name, refs=None):
        self.cmd = cmd
        self._tmux_name = tmux_name
        self.account_refs = refs or {"codex": None, "claude": None}


orig_get_env = _main._tmux_get_env
try:
    # 只有 CODEX_HOME、沒有 SF_ACCOUNT marker——就是實測到的那個狀態
    _main._tmux_get_env = lambda name, key: prof_a if key == "CODEX_HOME" else ""
    s = _FakeSession("sf-codex", "sf_a")
    got = api._live_config_dir(s, "codex")
    check("account_refs 是 None 但 tmux 有 CODEX_HOME → 用環境變數那個",
          got == prof_a, f"{got} / 期望 {prof_a}")

    # 環境變數指到不存在的目錄 → 當沒設，不要把解析導到空樹
    _main._tmux_get_env = lambda name, key: "/no/such/profile"
    s2 = _FakeSession("sf-codex", "sf_a")
    check("環境變數指到不存在的目錄 → 視為沒設",
          api._live_config_dir(s2, "codex") == "",
          str(api._live_config_dir(s2, "codex")))

    # 讀不到環境變數 → 退回 account_refs 的對應
    _main._tmux_get_env = lambda name, key: ""
    s3 = _FakeSession("sf-codex", "sf_a", {"codex": "acct-a", "claude": None})
    orig_env_for = _main.ACCOUNT_MANAGER.env_for
    try:
        _main.ACCOUNT_MANAGER.env_for = lambda pr, ref: (
            {"CODEX_HOME": prof_a} if (pr, ref) == ("codex", "acct-a") else {})
        check("環境變數讀不到 → 退回 account_refs",
              api._live_config_dir(s3, "codex") == prof_a,
              str(api._live_config_dir(s3, "codex")))
    finally:
        _main.ACCOUNT_MANAGER.env_for = orig_env_for

    # 兩邊都沒有 → 空字串（＝用 provider 預設位置）
    s4 = _FakeSession("sf-codex", "sf_a")
    check("兩邊都沒有 → 空字串（走預設位置）",
          api._live_config_dir(s4, "codex") == "")

    # 快取：TTL 內不再 fork tmux
    calls = []
    _main._tmux_get_env = lambda name, key: (calls.append(key) or prof_a)
    s5 = _FakeSession("sf-codex", "sf_a")
    api._live_config_dir(s5, "codex")
    api._live_config_dir(s5, "codex")
    api._live_config_dir(s5, "codex")
    check("每個分頁只問 tmux 一次（status monitor 每輪都會呼叫這支）",
          len(calls) == 1, f"問了 {len(calls)} 次")

    # 不認識的 provider 不要亂猜
    s6 = _FakeSession("bash", "sf_a")
    check("非 AI 分頁回空字串", api._live_config_dir(s6, "other") == "")
finally:
    _main._tmux_get_env = orig_get_env

import shutil  # noqa: E402
shutil.rmtree(td, ignore_errors=True)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
