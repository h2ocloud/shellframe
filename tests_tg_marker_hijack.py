#!/usr/bin/env python3
"""P0-1 回歸測試：strip_ansi 的 legacy `>>>…<<<` 劫持（2026-08-17）。

修前：strip_ansi 開頭有一段舊 `>>> response <<<` marker 方案的殘骸——
    m = re.search(r'>>>\\s*(.*?)\\s*<<<', clean, re.DOTALL)
    if m: return m.group(1).strip()
只要 120KB pending_raw 裡任何位置出現一組 `>>>` … `<<<`（Python REPL 提示、
bash here-string、git conflict、diff 輸出——agent 畫面上極常見），整個 buffer
就只剩那一小段，`[[TG_REPLY_…]]` 區塊被完全抹除。

Howard 親自實測的輸入（本檔 test_howard_repro 用的就是這一組）：
    ">>> some python repl\\nprint(1)\\n<<<\\n[[TG_REPLY_ab]]真正的回覆[[/TG_REPLY_ab]]"
修前輸出：'print(1)'      ← 回覆與 marker 全被丟棄
修後：整段保留，_pick_marker_reply 抽得到「真正的回覆」

致命之處在於它會自我維持：marker 永遠抽不到 → marker_forwarded 永遠 False →
走 fallback → fallback 又要求 turn_ended → 也永遠不成立 → **完全靜默，零 log**。

跑法：.venv/bin/python tests_tg_marker_hijack.py
"""

import importlib.util
import os
import types

_spec = importlib.util.spec_from_file_location(
    "bt", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_telegram.py"))
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)


def _slot(raw, token="ab"):
    return types.SimpleNamespace(
        sid="s87",
        expect_marker=True,
        reply_start_marker=f"[[TG_REPLY_{token}]]",
        reply_end_marker=f"[[/TG_REPLY_{token}]]",
        pending_raw=raw,
        peek_fn=None,
        marker_prompt="",
        sent_responses=set(),
        _dbg_clean_has=None,
    )


def _bridge():
    br = object.__new__(_bt.TelegramBridge)
    br._perf_enabled = False
    return br


# ── 1. Howard 的實測輸入：marker 區塊必須存活 ──
def test_howard_repro():
    raw = ("some python repl\n>>> print(1)\n<<<\n"
           "[[TG_REPLY_ab]]真正的回覆[[/TG_REPLY_ab]]")
    out = _bt.strip_ansi(raw, sent_texts=[])
    assert "[[TG_REPLY_ab]]" in out, f"start marker 被吃掉了: {out!r}"
    assert "[[/TG_REPLY_ab]]" in out, f"end marker 被吃掉了: {out!r}"
    assert "真正的回覆" in out, out
    reply, has_open = _bridge()._pick_marker_reply(_slot(raw), allow_inprogress=False)
    assert reply == "真正的回覆", (reply, has_open)


# ── 2. bash here-string（`cmd <<< "text"`）不得劫持 ──
def test_here_string_does_not_hijack():
    raw = ('$ grep foo <<< "haystack"\n>>> nothing\n'
           '[[TG_REPLY_cd]]部署跑完了，三個服務都正常。[[/TG_REPLY_cd]]')
    reply, _ = _bridge()._pick_marker_reply(_slot(raw, "cd"), allow_inprogress=False)
    assert reply == "部署跑完了，三個服務都正常。", reply


# ── 3. git conflict marker 不得劫持 ──
def test_git_conflict_does_not_hijack():
    raw = ("<<<<<<< HEAD\nold line\n=======\nnew line\n>>>>>>> feature\n"
           "[[TG_REPLY_ef]]衝突已解掉。[[/TG_REPLY_ef]]")
    reply, _ = _bridge()._pick_marker_reply(_slot(raw, "ef"), allow_inprogress=False)
    assert reply == "衝突已解掉。", reply


# ── 4. 大 buffer：`>>>`/`<<<` 出現在遠處也不能吞掉尾端的 marker ──
def test_large_buffer_far_away_repl_prompt():
    noise = ("\n".join(f">>> x{i}\n<<< y{i}" for i in range(200)))
    raw = noise + "\n[[TG_REPLY_gh]]最後的回覆內容。[[/TG_REPLY_gh]]"
    reply, _ = _bridge()._pick_marker_reply(_slot(raw, "gh"), allow_inprogress=False)
    assert reply == "最後的回覆內容。", reply


# ── 5. 殘骸本身不得再出現在源碼裡（防有人「順手」加回來）──
def test_legacy_strategy_removed_from_source():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "bridge_telegram.py"), encoding="utf-8").read()
    body = src.split("def strip_ansi", 1)[1].split("\ndef ", 1)[0]
    # 只看實際會執行的程式碼：切掉 docstring（它引用了舊 pattern 當警語）與註解
    body = body.split('"""', 2)[-1]
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    assert "re.search(r'>>>" not in code, "legacy Strategy 1 又被加回來了"
    assert "marker_match" not in code, "legacy Strategy 1 又被加回來了"


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                fails.append(name)
    print(f"\n=== {'ALL PASS' if not fails else f'{len(fails)} FAILED'} ===")
    raise SystemExit(1 if fails else 0)
