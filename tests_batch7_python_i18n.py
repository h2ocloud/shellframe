#!/usr/bin/env python3
"""Batch 7: Python replies shown by the main window select the prompt language."""

import importlib
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"

import agent_cursor  # noqa: E402
import agent_grok  # noqa: E402
import usage_probe  # noqa: E402


FAILURES = []


def check(name, condition, detail=""):
    if not condition:
        FAILURES.append(f"{name}: {detail}" if detail else name)


def test_cursor_replies_are_english():
    check("cursor subscription label", agent_cursor._billing_label("subscription") == "Subscription")
    check("cursor paid label", agent_cursor._billing_label("paid") == "Paid")
    old_quota, old_model = agent_cursor.quota, agent_cursor.model_info
    try:
        agent_cursor.quota = lambda: {"_error_message": "Usage lookup failed"}
        agent_cursor.model_info = lambda worker: {
            "name": "composer-2.5", "effort": "high", "billing": "subscription"
        }
        report = agent_cursor.usage_report({})
        check("cursor usage heading", report.startswith("AI usage Cursor Agent"), report)
        check("cursor usage error", "Usage lookup failed" in report, report)
        check("cursor usage model", "Model composer-2.5 (Subscription) effort high" in report, report)
    finally:
        agent_cursor.quota, agent_cursor.model_info = old_quota, old_model


def test_grok_replies_are_english():
    old_quota, old_account, old_text = agent_grok.quota, agent_grok.account_label, agent_grok.usage_text
    try:
        agent_grok.quota = lambda: {
            "week": (14, "10-12 08:35"), "_reset_epoch": {}, "_window_minutes": {},
            "_stale": True,
        }
        agent_grok.account_label = lambda: "user@example.com"
        agent_grok.usage_text = lambda worker: "Grok Build this session: 2 turns"
        report = agent_grok.usage_report({})
        check("grok usage heading", report.startswith("AI usage Grok Build"), report)
        check("grok account", "Account user@example.com" in report, report)
        check("grok stale warning", "This update failed; showing the last known data" in report, report)
        check("grok fallback wording", agent_grok._pace_clause({"week": (14, "10-12 08:35")})
              == "Weekly quota used 14% | reset 10-12 08:35", agent_grok._pace_clause({"week": (14, "10-12 08:35")}))
    finally:
        agent_grok.quota, agent_grok.account_label, agent_grok.usage_text = old_quota, old_account, old_text


def test_usage_provider_labels_are_localized():
    check("enterprise plan label", usage_probe._plan_label("team", "Acme") == "Team (Enterprise·Acme)")
    check("personal plan label", usage_probe._plan_label("pro") == "Pro (Personal)")
    notes = [
        usage_probe.PROVIDER_SPECS[name]["install"].get("note", "")
        for name in ("pi", "opencode", "agy", "grok", "cursor")
    ]
    check("provider install notes are English", all(not any(c >= "\u3400" and c <= "\u9fff" for c in note) for note in notes), notes)


def test_chinese_halves_stay_unchanged():
    os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
    check("cursor Chinese label", agent_cursor._billing_label("subscription") == "訂閱")
    check("cursor Chinese heading", agent_cursor.usage_report({}).startswith("AI 水位 Cursor Agent"))
    check("grok Chinese pace", agent_grok._pace_clause({"week": (14, "10-12 08:35")})
          == "週配額已用 14%｜重置 10-12 08:35")
    check("usage Chinese enterprise label", usage_probe._plan_label("team", "Acme") == "Team（企業·Acme）")


def test_source_uses_language_helper_for_batch7_apis():
    for filename in ("api_accounts.py", "api_voice.py", "api_extensions.py"):
        source = (ROOT / filename).read_text(encoding="utf-8")
        check(f"{filename} imports language helper", "import sf_lang" in source)
        check(f"{filename} calls language helper", "sf_lang.pick(" in source)
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for phrase in ("缺 sid 或內容", "啟動信任對話框", "需要使用麥克風進行語音輸入"):
        check(f"main source keeps Chinese half for {phrase}", phrase in source)
    check("main source localizes batch 7 replies", source.count("sf_lang.pick(") >= 9)


if __name__ == "__main__":
    test_cursor_replies_are_english()
    test_grok_replies_are_english()
    test_usage_provider_labels_are_localized()
    test_chinese_halves_stay_unchanged()
    test_source_uses_language_helper_for_batch7_apis()
    if FAILURES:
        print("FAIL")
        print("\n".join(FAILURES))
        raise SystemExit(1)
    print("PASS: batch 7 Python replies use language helpers")
