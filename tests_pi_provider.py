#!/usr/bin/env python3
"""pi coding agent 支援回歸測試（v0.29.41）。

兩件事：
1. 燈號——pi 狀態列固定有「↑79k ↓1.5k 14.5%/128k」，那個 ↑ 命中共用
   SPINNER_RE 的 "↑" → pi 分頁永遠是 working、跑完不變燈（2026-08-24 回報的
   蒸餾任務實案）。改用 pi 專屬狀態機：braille spinner ＝工作中，token 數
   停止變動＝完成。
2. 安裝引導——registry 要有 pi 的 npm 安裝資訊。

跑法：.venv/bin/python tests_pi_provider.py
"""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_status as A
import usage_probe as U

IDLE = "↑79k ↓1.5k 14.5%/128k (auto)   spark-vision"
BUSY = "⠧ Working...\n" + IDLE
GROWN = "↑82k ↓1.7k 15.1%/128k (auto)   spark-vision"


def _tracker():
    return A.StatusTracker() if hasattr(A, "StatusTracker") else None


def _st(tr, screen, now):
    return tr.status_for("s1", {"cmd": "sf-pi-spark", "cwd": "~"},
                         screen_tail=screen, now=now)


# ── 1. registry 認得 pi，且不誤判 pip/pipenv ──
def test_detect():
    assert U.detect_ai("sf-pi-spark") == "pi"
    assert U.detect_ai("pi --provider spark --model spark-vision") == "pi"
    for bad in ("pip install x", "pipenv shell", "python -m pip"):
        assert U.detect_ai(bad) != "pi", bad


# ── 2. 安裝引導齊全（npm 指令 + Node 版本 + computer-use + allowScripts 提醒）──
def test_install_hint():
    ins = U.PROVIDER_SPECS["pi"]["install"]
    assert "npm i -g @earendil-works/pi-coding-agent" == ins["command"]
    note = ins.get("note", "")
    assert "22.19" in note, "缺 Node 版本需求"
    assert "pi-computer-use" in note, "缺 computer-use 擴充指令"
    assert "allowScripts" in note, "缺 postinstall 被擋的提醒"
    assert ins.get("docs")


# ── 3. braille spinner → working ──
def test_spinner_is_working():
    tr = _tracker()
    if not tr:
        return
    assert _st(tr, BUSY, 1000.0)["state"] == "working"


# ── 4. 核心回歸：token 停住不動 → 不可以永遠 working ──
#      （舊行為：SPINNER_RE 的 "↑" 命中 → 永遠 working）
def test_settled_tokens_not_working_forever():
    tr = _tracker()
    if not tr:
        return
    _st(tr, BUSY, 1000.0)                      # 工作中
    st = _st(tr, IDLE, 1000.0 + 30)            # spinner 消失、token 不動
    assert st["state"] != "working", \
        f"token 停住仍判 working（↑ 誤判回歸）：{st['state']} / {st['why']}"
    assert st["state"] in ("done", "idle"), st


# ── 5. token 還在長 → working ──
def test_growing_tokens_is_working():
    tr = _tracker()
    if not tr:
        return
    _st(tr, IDLE, 2000.0)
    st = _st(tr, GROWN, 2000.5)
    assert st["state"] == "working", st


# ── 6. 共用 compute_state 對 pi 狀態列會誤判（記錄為何需要專屬狀態機）──
def test_shared_path_would_misjudge():
    state, _act, _why = A.compute_state([], now=1000.0, screen_tail=IDLE)
    assert state == "working", \
        "若共用路徑不再誤判，_pi_status 的必要性需重新評估"


# ── 7. 模型徽章：pi 的 session 檔就有 model_change / thinking_level_change ──
#      舊行為：detect_model_info 沒有 pi 分支 → pi 分頁永遠沒有模型徽章。
import glob as _glob            # noqa: E402
import json as _json            # noqa: E402
import os as _os                # noqa: E402
import tempfile as _tempfile    # noqa: E402


def _pi_session_dir(lines_by_file, start=1_000_000.0):
    """建一個假的 ~/.pi/agent/sessions 樹，回 (root, worker, restore)。"""
    root = _tempfile.mkdtemp(prefix="sf_pi_test_")
    cwd = _os.path.join(root, "work")
    _os.makedirs(cwd, exist_ok=True)
    slug_dir = _os.path.join(root, "sessions", A._pi_cwd_slug(cwd))
    _os.makedirs(slug_dir, exist_ok=True)
    for i, (name, records) in enumerate(lines_by_file):
        path = _os.path.join(slug_dir, name)
        with open(path, "w") as fh:
            for r in records:
                fh.write(_json.dumps(r) + "\n")
        _os.utime(path, (start + i, start + i))     # 後面的檔比較新
    saved = (A.PI_SESSIONS, A._tmux_pane_pid, A._proc_start_epoch)
    A.PI_SESSIONS = _os.path.join(root, "sessions")
    A._tmux_pane_pid = lambda name: 4242
    A._proc_start_epoch = lambda pid: start

    def restore():
        A.PI_SESSIONS, A._tmux_pane_pid, A._proc_start_epoch = saved

    return {"cmd": "sf-pi-spark", "cwd": cwd, "tmux_name": "sf_x"}, restore


def _pi_records(model, thinking=None):
    recs = [{"type": "session", "version": 3, "cwd": "/x",
             "timestamp": "2026-08-28T14:37:29.665Z"},
            {"type": "model_change", "provider": "spark", "modelId": model,
             "timestamp": "2026-08-28T14:37:29.699Z"}]
    if thinking:
        recs.append({"type": "thinking_level_change", "thinkingLevel": thinking,
                     "timestamp": "2026-08-28T14:37:29.699Z"})
    return recs


def test_cwd_slug():
    assert A._pi_cwd_slug("/Users/alice") == "--Users-alice--"


def test_file_epoch_parsed_from_name():
    got = A._pi_file_epoch("2026-08-28T14-24-15-712Z_01a048c1.jsonl")
    assert got and abs(got - 1787927055.712) < 0.01, got
    assert A._pi_file_epoch("not-a-session.jsonl") is None


def test_model_badge():
    worker, restore = _pi_session_dir(
        [("2026-08-28T14-37-29-665Z_aaa.jsonl", _pi_records("spark-main"))])
    try:
        info = A.detect_model_info(worker)
    finally:
        restore()
    assert info and info["name"] == "spark-main", info
    assert info["provider"] == "pi", info


def test_thinking_off_is_not_shown_but_on_is():
    worker, restore = _pi_session_dir(
        [("2026-08-28T14-37-29-665Z_aaa.jsonl",
          _pi_records("spark-main", "off"))])
    try:
        assert A.detect_model_info(worker)["effort"] == ""
    finally:
        restore()
    worker, restore = _pi_session_dir(
        [("2026-08-28T14-37-29-665Z_bbb.jsonl",
          _pi_records("spark-main", "high"))])
    try:
        assert A.detect_model_info(worker)["effort"] == "high"
    finally:
        restore()


def test_midsession_model_switch_wins():
    recs = _pi_records("spark-main") + [
        {"type": "model_change", "provider": "spark", "modelId": "spark-vision",
         "timestamp": "2026-08-28T15:00:00.000Z"}]
    worker, restore = _pi_session_dir(
        [("2026-08-28T14-37-29-665Z_ccc.jsonl", recs)])
    try:
        assert A.detect_model_info(worker)["name"] == "spark-vision"
    finally:
        restore()


# ── 8. 同 cwd 多個 session 檔：只認這個 process 起來之後建立的，取最新寫入 ──
#      （分頁中途重開 session 會再生一個檔；上一個分頁留下的舊檔不能贏）
def test_picks_this_process_newest_file():
    worker, restore = _pi_session_dir([
        # 舊檔：檔名時刻早於 process 起始（1_000_000 → 1970-01-12T13:46:40Z）
        ("1970-01-11T00-00-00-000Z_old.jsonl", _pi_records("stale-model")),
        ("1970-01-12T14-00-00-000Z_new.jsonl", _pi_records("live-model")),
    ])
    try:
        got = A._pi_session_file(worker)
        assert got and got.endswith("_new.jsonl"), got
        assert A.detect_model_info(worker)["name"] == "live-model"
    finally:
        restore()


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}"); fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
