#!/usr/bin/env python3
"""inline 畫面的 agent，上滑歷史一律是 tmux 的原樣輸出，不是重新排版的版本。

codex、pi、agy、cursor-agent、Claude Code 一般模式都在 normal screen 跑（實測
tmux `#{alternate_on}`=0、不開滑鼠）：捲出去的內容留在 tmux scrollback，帶著原本的
顏色（capture-pane -e）——那就是原本終端機的畫面。重新排版的 transcript 只該是
alt screen 拿不到東西時的備案。這裡逐一 agent 確認：不管 transcript 有沒有，
inline 分頁都拿 tmux，而且顏色原樣保留。

跑法：.venv/bin/python tests_history_inline_source.py
"""
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import api_history  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# 原樣輸出：帶 SGR 的一段（綠色的 ● 與粗體），模擬 tmux capture-pane -e
CAPTURE = "".join(f"\x1b[32m●\x1b[0m \x1b[1mline {i:02d}\x1b[0m done\n" for i in range(40))


def fake_run(cmd, **kw):
    out = ""
    if cmd[:2] == ["tmux", "display-message"]:
        out = "0\n"                    # normal screen
    elif cmd[:2] == ["tmux", "capture-pane"]:
        out = CAPTURE
    return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")


def history_for(cmd):
    api = Api.__new__(Api)
    api.sessions = {"s1": types.SimpleNamespace(_tmux_name="sf_s1", cmd=cmd)}
    api.bridge = api.line_bridge = None
    calls = []
    api._transcript_history_response = lambda *a, **k: calls.append(a) or json.dumps(
        {"success": True, "text": "re-rendered", "ansi": True, "source": "transcript (claude)"})
    real = api_history.subprocess.run
    api_history.subprocess.run = fake_run
    try:
        res = json.loads(api.get_clean_history("s1", max_lines=500, ansi=True, cols=97))
    finally:
        api_history.subprocess.run = real
    return res, calls


for cmd in ("sf-codex --search", "codex --model gpt-5.5", "pi", "agy", "cursor-agent",
            "claude --model opus", "grok --no-alt-screen"):
    res, calls = history_for(cmd)
    name = cmd.split(" ")[0]
    check(f"{name}: inline history comes from tmux, never the re-rendered transcript",
          res.get("source") == "tmux (normal)" and not calls, f"{res.get('source')} calls={len(calls)}")
    check(f"{name}: the original colours are kept", "\x1b[32m●" in res.get("text", ""),
          repr(res.get("text", "")[:80]))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
