#!/usr/bin/env python3
"""Account/usage UI and usage probe responses use the language helpers."""

from pathlib import Path
import os
from unittest.mock import patch

import usage_probe


ROOT = Path(__file__).resolve().parent
index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
probe = (ROOT / "usage_probe.py").read_text(encoding="utf-8")
failures = []

for old in (
    "<h2>AI 帳號</h2>", "切換「目前 Session」", "切目前", "設下一個",
    "登入新帳號", "重新整理已登入", "重新查用量", "用量查詢中…",
    "用量已更新", "處理中…", "操作失敗", "本機找不到執行檔",
    "尚未發現登入帳號", "顯示上次資料", "重置 ${esc(",
):
    if old in index:
        failures.append(f"hard-coded account/usage text remains: {old}")

if "usageLoading" not in index or "aiAccountsTitle" not in index:
    failures.append("account panel does not use locale keys")
if "import sf_lang" not in probe or "sf_lang.pick(" not in probe:
    failures.append("usage_probe responses are missing sf_lang.pick")

sample = {
    "ai": "claude", "account": "demo@example.test", "stale": True,
    "five_hr": {"pct": 18, "reset": "10-12 18:00"},
}
with patch.dict(os.environ, {"SHELLFRAME_PROMPT_LANG": "en"}):
    if "AI usage claude" not in usage_probe.probe_text(sample) or "reset" not in usage_probe.probe_text(sample):
        failures.append("usage_probe English output is not selected")
with patch.dict(os.environ, {"SHELLFRAME_PROMPT_LANG": "zh"}):
    if "AI 水位 claude" not in usage_probe.probe_text(sample) or "重置" not in usage_probe.probe_text(sample):
        failures.append("usage_probe Traditional Chinese output is not selected")

if failures:
    print("FAIL")
    print("\n".join(failures))
    raise SystemExit(1)
print("PASS: account and usage UI/probe use i18n")
