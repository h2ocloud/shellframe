#!/usr/bin/env python3
"""Test that _should_inject_init correctly filters AI CLI tools from regular commands."""

import json
import shlex
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Mock heavy imports before importing main
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()

sys.path.insert(0, str(Path(__file__).parent))

from main import Api, AI_CLI_TOOLS, load_config

api = Api()

# ── Test cases ──

SHOULD_INJECT = [
    ("claude", "bare claude"),
    ("codex", "bare codex"),
    ("aider", "bare aider"),
    ("claude --model opus", "claude with args"),
    ("/usr/local/bin/claude", "claude full path"),
    ("npx codex", "npx wrapper"),
    ("bunx claude", "bunx wrapper"),
    ("cursor --fast", "cursor with flag"),
    ("copilot", "bare copilot"),
    ("goose", "bare goose"),
]

SHOULD_NOT_INJECT = [
    ("bash", "bash shell"),
    ("zsh", "zsh shell"),
    ("sh", "sh shell"),
    ("fish", "fish shell"),
    ("vim", "vim editor"),
    ("nvim", "nvim editor"),
    ("nano", "nano editor"),
    ("emacs", "emacs editor"),
    ("python3", "python repl"),
    ("node", "node repl"),
    ("htop", "htop monitor"),
    ("top", "top monitor"),
    ("python3 -c 'print(1)'", "python one-liner"),
    ("ls -la", "ls command"),
    ("ssh user@host", "ssh"),
]

passed = 0
failed = 0

print(f"AI_CLI_TOOLS = {AI_CLI_TOOLS}\n")

print("── Should inject (expect True) ──")
for cmd, desc in SHOULD_INJECT:
    result = api._should_inject_init(cmd)
    status = "PASS" if result else "FAIL"
    if result:
        passed += 1
    else:
        failed += 1
    print(f"  [{status}] {desc:30s} cmd={cmd!r:30s} -> {result}")

print("\n── Should NOT inject (expect False) ──")
for cmd, desc in SHOULD_NOT_INJECT:
    result = api._should_inject_init(cmd)
    status = "PASS" if not result else "FAIL"
    if not result:
        passed += 1
    else:
        failed += 1
    print(f"  [{status}] {desc:30s} cmd={cmd!r:30s} -> {result}")

# ── Test preset override ──
print("\n── Preset override tests ──")

override_config = {
    "presets": [
        {"name": "MyScript", "cmd": "my-custom-ai", "icon": "X", "inject_init": True},
        {"name": "Claude Silent", "cmd": "claude --quiet", "icon": "X", "inject_init": False},
    ]
}

with patch("main.load_config", return_value=override_config):
    result = api._should_inject_init("my-custom-ai")
    status = "PASS" if result else "FAIL"
    passed += 1 if result else 0
    failed += 0 if result else 1
    print(f"  [{status}] preset override True       cmd='my-custom-ai'         -> {result}")

    result = api._should_inject_init("claude --quiet")
    status = "PASS" if not result else "FAIL"
    passed += 1 if not result else 0
    failed += 0 if not result else 1
    print(f"  [{status}] preset override False      cmd='claude --quiet'       -> {result}")

# ── init 注入時機 gate（v0.23.3：新分頁打 /model 被 inject 的修正）──
import types as _types
print("\n_init_inject_decision（斜線指令不消耗 init、逐鍵 hold）:")
_D = api.__class__._init_inject_decision
def _gate_case(name, seqs, want_last, want_hold=None):
    global passed, failed
    s = _types.SimpleNamespace(_init_pending=True)
    res = [_D(s, c) for c in seqs]
    ok = res[-1] == want_last and (want_hold is None or getattr(s, "_init_hold", False) == want_hold)
    passed += 1 if ok else 0
    failed += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} -> {res[-1]}")
_gate_case("逐鍵 /model+Enter 全程不注入", ["/", "m", "o", "d", "e", "l", "\r"], "pass", want_hold=False)
_gate_case("斜線送出後真訊息才注入", ["/", "m", "\r", "你"], "inject")
_gate_case("貼上 /model\\r 不留 hold", ["/model\r"], "pass", want_hold=False)
_gate_case("新分頁直接打字立即注入", ["你"], "inject")
_gate_case("裸 Enter 不觸發", ["\r"], "pass")
_gate_case("選單方向鍵不觸發", ["/", "m", "\r", "\x1b[B", "\r"], "pass")

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed:
    print("SOME TESTS FAILED!")
    sys.exit(1)
else:
    print("All tests passed!")
    sys.exit(0)
