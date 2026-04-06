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

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed:
    print("SOME TESTS FAILED!")
    sys.exit(1)
else:
    print("All tests passed!")
    sys.exit(0)
