"""回歸：relaunch／重開機還原要接回對話「實際所在」的 claude 設定家目錄。

日常回報（批次 relaunch 套用 Claude Code 更新）：manifest 的 account_refs 是 null，
但行程是從 tmux 全域環境繼承 CLAUDE_CONFIG_DIR、隱性跑在帳號 profile 裡。
relaunch 只看 refs → 拿預設目錄去 --resume：
  (1) 預設目錄沒有這段對話 → claude 當場結束，分頁消失（9 個分頁）；
  (2) 預設目錄剛好有舊副本 → 重啟「成功」但接回 23 天前的版本（599 行 vs 2952 行）。
"""

from _testsrc import api_class_source, fold_host  # main.py + api_*.py mixins
import inspect
import json
import os
import re
import tempfile
import time
import types

import main

CSID = "b4ecc7f0-d25e-42cf-8f43-158d4df81a51"


def _transcript(home, slug, lines, age_s=0):
    path = os.path.join(home, "projects", slug, f"{CSID}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for i in range(lines):
            f.write(json.dumps({"type": "user", "n": i}) + "\n")
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def _pidfile(home, pid, session_id):
    os.makedirs(os.path.join(home, "sessions"), exist_ok=True)
    with open(os.path.join(home, "sessions", f"{pid}.json"), "w") as f:
        json.dump({"pid": int(pid), "sessionId": session_id}, f)


class _Patch:
    """暫時換掉 main 裡的東西，離開時還原。"""
    def __init__(self, **kw):
        self.kw, self.saved = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            obj, attr = (main.ACCOUNT_MANAGER, k[4:]) if k.startswith("acm_") else (main, k)
            self.saved[k] = getattr(obj, attr)
            setattr(obj, attr, v)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            obj, attr = (main.ACCOUNT_MANAGER, k[4:]) if k.startswith("acm_") else (main, k)
            setattr(obj, attr, v)


def _layout(td):
    """預設家目錄 A（殘留舊副本）＋ 帳號 profile B（正在寫的那份）。"""
    a = os.path.join(td, "default-home")
    b = os.path.join(td, "profiles", "claude", "claude-prof-b")
    os.makedirs(a)
    os.makedirs(b)
    stale = _transcript(a, "-Users-x", 599, age_s=23 * 86400)
    live = _transcript(b, "-Users-x", 2952, age_s=60)
    env_for = lambda p, ref: ({"CLAUDE_CONFIG_DIR": b} if (p, ref) == ("claude", "claude-prof-b")
                              else {})
    return a, b, stale, live, env_for


def test_live_pid_file_marks_the_real_home():
    with tempfile.TemporaryDirectory() as td:
        a, b, *_ = _layout(td)
        _pidfile(a, 4242, "other-session")     # 同 pid 在別的目錄殘留、sessionId 不符
        _pidfile(b, 4242, CSID)
        assert main._claude_dir_for_live_pids(["4242"], CSID, roots=[a, b]) == b
        assert main._claude_dir_for_live_pids(["999"], CSID, roots=[a, b]) == ""


def test_newest_copy_wins_over_stale_default_copy():
    with tempfile.TemporaryDirectory() as td:
        a, b, stale, live, _ = _layout(td)
        assert main._claude_newest_transcript(CSID, roots=[a, b]) == live
        assert main._claude_home_of_transcript(live) == b


def test_ensure_replaces_stale_target_and_keeps_backup():
    with tempfile.TemporaryDirectory() as td:
        a, b, stale, live, _ = _layout(td)
        dst = main._claude_ensure_transcript_in(CSID, a, roots=[a, b])
        assert dst == stale
        assert sum(1 for _ in open(dst)) == 2952, "舊副本沒被換成最新那份"
        baks = [f for f in os.listdir(os.path.dirname(dst)) if ".sf-bak-" in f]
        assert baks, "被換掉的舊副本要留底，不能直接刪"
        assert sum(1 for _ in open(os.path.join(os.path.dirname(dst), baks[0]))) == 599
        # 目標已經是最新的 → 不動
        before = os.stat(dst).st_mtime
        main._claude_ensure_transcript_in(CSID, a, src=live)
        assert os.stat(dst).st_mtime == before


def test_actual_home_prefers_live_pid_then_newest_copy():
    with tempfile.TemporaryDirectory() as td:
        a, b, stale, live, env_for = _layout(td)
        api = object.__new__(main.Api)
        s = types.SimpleNamespace(_tmux_name="sf_t", _hook_transcript_path="",
                                  account_refs={"claude": None}, session_id=CSID)
        with _Patch(_claude_config_roots=lambda: [a, b]):
            _pidfile(b, 777, CSID)
            api._pane_pids = lambda _s: ["777"]
            assert api._actual_claude_home(s, CSID) == b
            # 行程已經死了（分頁消失的那 9 個）→ 退回「最新那份」
            api._pane_pids = lambda _s: []
            assert api._actual_claude_home(s, CSID) == b


def test_same_account_relaunch_keeps_the_home_without_pinning():
    """沒 pin、對話在 profile → 照原樣只帶家目錄；不能改 pin。

    改 pin 會連帶把 profile 當下的 access token 寫死進 CLAUDE_CODE_OAUTH_TOKEN，
    那個 token 幾小時就過期（實測剩 5.7 小時），行程不會自己換新 → 之後 401。"""
    with tempfile.TemporaryDirectory() as td:
        a, b, stale, live, env_for = _layout(td)
        api = object.__new__(main.Api)
        with _Patch(acm_env_for=env_for):
            assert api._claude_home_to_keep({"claude": None}, b) == b
            # 已經 pin → 照 pin 走，不另外帶
            assert api._claude_home_to_keep({"claude": "claude-prof-b"}, b) == ""
            # 預設目錄 → 什麼都不必帶
            assert api._claude_home_to_keep({"claude": None},
                                            os.path.expanduser("~/.claude")) == ""
            assert api._claude_home_to_keep({"claude": None}, "") == ""


def test_home_override_sets_only_config_dir_never_a_token():
    fake = types.SimpleNamespace(account_refs={"claude": None, "codex": None},
                                 _claude_home_override="/x/profile")
    env = main.Session._account_env_overrides(fake)
    assert env == {"CLAUDE_CONFIG_DIR": "/x/profile"}, env
    # 有 pin 時 override 不生效，由 pin 決定
    with _Patch(acm_env_for=lambda p, r: {"CLAUDE_CONFIG_DIR": "/pinned"} if r else {}):
        pinned = types.SimpleNamespace(account_refs={"claude": "p", "codex": None},
                                       _claude_home_override="/x/profile")
        assert main.Session._account_env_overrides(pinned)["CLAUDE_CONFIG_DIR"] == "/pinned"


def test_account_switch_carries_newest_copy_over_stale_target():
    """切到一個殘留舊副本的帳號：舊版只在目標「沒有」時才複製，會接回舊對話。"""
    with tempfile.TemporaryDirectory() as td:
        a, b, stale, live, env_for = _layout(td)
        api = object.__new__(main.Api)
        old = types.SimpleNamespace(account_refs={"claude": "claude-prof-b"},
                                    _hook_transcript_path=live)
        with _Patch(acm_env_for=lambda p, ref: (
                {"CLAUDE_CONFIG_DIR": b} if ref == "claude-prof-b"
                else {"CLAUDE_CONFIG_DIR": a} if ref == "to-a" else {})):
            api._carry_claude_transcript(old, CSID, {"claude": "to-a"})
        assert sum(1 for _ in open(stale)) == 2952


def test_live_config_dir_sees_inherited_profile():
    """狀態／模型／歷史解析也吃同一個判斷：tmux session env 沒有、refs 是 null，
    但行程實際在 profile 裡 → 要讀 profile 的 transcript，不是預設目錄的。"""
    with tempfile.TemporaryDirectory() as td:
        a, b, *_ = _layout(td)
        _pidfile(b, 31337, CSID)
        api = object.__new__(main.Api)
        api._pane_pids = lambda _s: ["31337"]
        s = types.SimpleNamespace(_tmux_name="sf_t", account_refs={"claude": None},
                                  session_id=CSID)
        with _Patch(_tmux_get_env=lambda *_a: "", _claude_config_roots=lambda: [a, b]):
            assert api._live_config_dir(s, "claude") == b


def test_session_hint_when_memory_has_no_uuid():
    """ShellFrame 重開後、閒置分頁還沒送 hook 前 session_id 是空的——實測就是這樣
    又讓一個分頁消失。要能從行程自己的 sessions/<pid>.json 或 --resume 指令補回。"""
    with tempfile.TemporaryDirectory() as td:
        a, b, *_ = _layout(td)
        _pidfile(b, 555, CSID)
        assert main._claude_session_hint(["555"], "claude", roots=[a, b]) == CSID
        cmd = f"env -u CLAUDE_CONFIG_DIR claude --resume {CSID} --model claude-opus-5-5"
        assert main._claude_session_hint([], cmd, roots=[a, b]) == CSID
        assert main._claude_session_hint([], f"claude --session-id={CSID}", roots=[a]) == CSID
        assert main._claude_session_hint([], "claude --model opus", roots=[a]) == ""


def test_relaunch_and_restore_fill_missing_uuid_first():
    src = _body(main.Api._restart_session_for_account)
    i_hint = src.find("_claude_session_hint(")
    i_actual = src.find("_actual_claude_home(")
    assert i_hint != -1 and i_hint < i_actual, "uuid 空的時候要先補，才判斷家目錄"
    block = api_class_source(main.Api)
    block = block[block.find("soft restore from config"):]
    assert block.find("_claude_session_hint(") < block.find("_claude_transcript_exists(csid)")


def _body(fn):
    return fold_host(inspect.getsource(fn))


def test_relaunch_measures_actual_home_before_resume():
    src = _body(main.Api._restart_session_for_account)
    i_actual = src.find("_actual_claude_home(")
    i_keep = src.find("_claude_home_to_keep(")
    i_carry = src.find("_carry_claude_transcript(")
    i_resume = src.find("_cmd_with_resume(cmd, csid)")
    i_spawn = src.find("session = Session(")
    assert -1 not in (i_actual, i_keep, i_carry, i_resume, i_spawn), "relaunch 少了家目錄判斷"
    assert i_actual < i_keep < i_carry < i_resume < i_spawn
    assert re.search(r"src_home\s*=\s*actual", src), "搬 transcript 要從實際家目錄搬"
    assert "claude_home=claude_home" in src[i_spawn:], "新行程要帶著原本的家目錄"


def test_soft_restore_keeps_home_or_carries_before_spawn():
    src = api_class_source(main.Api)
    start = src.find("soft restore from config")
    assert start != -1
    block = src[start:]
    i_home = block.find("_claude_home_to_keep(")
    i_spawn = block.find("session = Session(sid, cmd")
    assert i_home != -1 and i_spawn != -1 and i_home < i_spawn, \
        "重開機還原要在 spawn 前判斷對話所在的家目錄"
    assert block.find("_claude_ensure_transcript_in(") < i_spawn
    assert "claude_home=claude_home" in block[i_spawn:i_spawn + 400]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
