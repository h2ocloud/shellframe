#!/usr/bin/env python3
"""上滑歷史讀 transcript 的窗口：貼過很多截圖的對話不能只剩最後一點。

回報：最後一個分頁拉不上去看歷史對話。量到的原因：歷史只讀檔案最後 2 MB。
截圖以 base64 內嵌在紀錄裡，那個分頁的對話檔 25 MB、1946 筆紀錄，最後 2 MB 只
涵蓋 137 筆（約 7%）——歷史只有 86 行、一則使用者訊息，前面整段被切掉。

修法：窗口裡的紀錄不足 max_records 時依平均每筆大小往前擴大（opt-in，歷史才開；
狀態列那類頻繁呼叫維持固定的小窗口）。

跑法：.venv/bin/python tests_history_transcript_window.py
"""
import inspect
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules.setdefault("webview", MagicMock())
sys.modules.setdefault("bridge_telegram", MagicMock())
sys.path.insert(0, str(HERE))

import agent_status  # noqa: E402
import api_history  # noqa: E402

H = next(c for c in vars(api_history).values()
         if isinstance(c, type) and hasattr(c, "_TRANSCRIPT_GROW_BYTES"))
MB = 1024 * 1024
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def user(text, i):
    return {"type": "user", "sessionId": "t", "timestamp": f"2026-10-06T10:{i // 60:02d}:{i % 60:02d}Z",
            "message": {"role": "user", "content": text}}


def asst(text, i):
    return {"type": "assistant", "sessionId": "t", "timestamp": f"2026-10-06T10:{i // 60:02d}:{i % 60:02d}Z",
            "message": {"role": "assistant", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


def blob(i, kb=100):
    """貼圖／附件：base64 內嵌，位元組很多、對話內容為零。正規化器不認得它。"""
    return {"type": "attachment", "sessionId": "t",
            "attachment": {"type": "image", "data": "A" * (kb * 1024)}, "n": i}


def build(path, early_turns=3, blobs=150, late_turns=40):
    recs = []
    for i in range(early_turns):
        recs += [user(f"EARLY-USER-{i}", i), asst(f"EARLY-REPLY-{i}", i)]
    recs += [blob(i) for i in range(blobs)]
    for i in range(late_turns):
        recs += [user(f"LATE-USER-{i}", 100 + i), asst(f"LATE-REPLY-{i}", 100 + i)]
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(recs)


def texts(evs):
    return [e.get("text", "") for e in evs if e.get("kind") in ("user_msg", "assistant_text")]


with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, "conv.jsonl")
    total = build(path)
    size = os.path.getsize(path)
    check("fixture is image-heavy: far bigger than the 2 MB window", size > 10 * MB, f"{size / MB:.1f} MB")

    # ── 1. 重現：固定窗口看不到前面 ──
    _, evs, err = agent_status._read_tail_events(path, tail_bytes=2 * MB, max_records=3000)
    t = texts(evs)
    check("fixed window (the old behaviour) loses the start of the conversation",
          not err and "LATE-REPLY-39" in t and "EARLY-USER-0" not in t, f"{len(t)} texts")

    # ── 2. 修正：窗口會擴大 ──
    _, evs, err = agent_status._read_tail_events(path, tail_bytes=2 * MB, max_records=3000,
                                                 grow_to_bytes=256 * MB)
    t = texts(evs)
    check("growing window reaches the first turn", "EARLY-USER-0" in t and "EARLY-REPLY-2" in t)
    check("every turn is there, in order, once",
          t == [x for i in range(3) for x in (f"EARLY-USER-{i}", f"EARLY-REPLY-{i}")]
          + [x for i in range(40) for x in (f"LATE-USER-{i}", f"LATE-REPLY-{i}")], str(t[:4]))

    # ── 3. 頻繁呼叫的路徑維持原本的成本 ──
    _, small, _ = agent_status._read_tail_events(path)
    check("default arguments (status monitor) still read only the small tail",
          "EARLY-USER-0" not in texts(small))
    src = inspect.getsource(agent_status)
    callers = [m.start() for m in re.finditer(r"_read_tail_events\(", src)]
    grow_callers = [m.start() for m in re.finditer(r"grow_to_bytes\s*=\s*[^0\s]", src)]
    check("no caller inside agent_status opts into growth", not grow_callers, str(grow_callers))
    check("the history overlay does opt in",
          "grow_to_bytes=self._TRANSCRIPT_GROW_BYTES" in inspect.getsource(H._transcript_history_response))

    # ── 4. 邊界 ──
    _, capped, _ = agent_status._read_tail_events(path, tail_bytes=2 * MB, max_records=3000,
                                                  grow_to_bytes=4 * MB)
    check("growth never reads past its byte cap", "EARLY-USER-0" not in texts(capped))
    _, bounded, _ = agent_status._read_tail_events(path, tail_bytes=2 * MB, max_records=20,
                                                   grow_to_bytes=256 * MB)
    check("max_records still bounds how much is parsed", len(texts(bounded)) <= 20, str(len(texts(bounded))))
    tiny = os.path.join(td, "tiny.jsonl")
    with open(tiny, "w") as f:
        f.write(json.dumps(user("only turn", 0)) + "\n")
    _, one, e1 = agent_status._read_tail_events(tiny, tail_bytes=2 * MB, max_records=3000,
                                                grow_to_bytes=256 * MB)
    check("a file smaller than the window works as before", not e1 and texts(one) == ["only turn"])
    nonl = os.path.join(td, "nonl.jsonl")
    with open(nonl, "w") as f:
        f.write(json.dumps(user("no trailing newline", 0)))
    _, o2, e2 = agent_status._read_tail_events(nonl, grow_to_bytes=256 * MB)
    check("a last line without a newline is kept", not e2 and texts(o2) == ["no trailing newline"])
    sep = os.path.join(td, "sep.jsonl")
    with open(sep, "w", encoding="utf-8") as f:
        f.write(json.dumps(user("line with a U+2028   inside", 0), ensure_ascii=False) + "\n")
        f.write(json.dumps(asst("after", 1)) + "\n")
    _, o3, _ = agent_status._read_tail_events(sep)
    check("a U+2028 inside a record no longer splits it in two",
          texts(o3) == ["line with a U+2028   inside", "after"], str(texts(o3)))

    # ── 5. 整條鏈路：歷史文字 ──
    _, evs, _ = agent_status._read_tail_events(path, tail_bytes=2 * MB, max_records=3000,
                                               grow_to_bytes=256 * MB)
    out = H._ANSI_STRIP_RE.sub("", H._render_transcript_overlay(evs, True, cols=97))
    check("overlay text starts with the first turn and ends with the last",
          out.index("EARLY-USER-0") < out.index("LATE-REPLY-39"))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
