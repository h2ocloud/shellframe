#!/usr/bin/env python3
"""路由建議：三檔、額度高就往後、付費模型不入選、hint 不開行程。"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_route as R

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name + (f" | {detail}" if detail else ""))


def hint(task, readings, tier=None):
    return R.hint(task, readings, tier)


def test_tiers_and_quota():
    low = {"opencode": {"week_pct": None, "error": "no_data"},
           "pi": {"week_pct": None, "error": "no_data"},
           "grok": {"week_pct": 10}, "cursor": {"week_pct": 10},
           "claude": {"week_pct": 10}}
    check("light prefers local spark", hint("幫我翻譯這段", low)["suggestion"] == "opencode")
    check("light names spark", "地端 Spark" in hint("翻譯這段", low)["text"])
    code = hint("修這個函式", low)
    check("code prefers cursor composer", code["suggestion"] == "cursor"
          and code["model"] == "composer-2.5" and code["counterpart"] == "claude")
    check("plan prefers opus", hint("規劃這個架構", low)["suggestion"] == "claude")
    review = hint("審查這段實作", low)
    check("review prefers opus and pairs cursor",
          review["tier"] == "review" and review["suggestion"] == "claude"
          and review["counterpart_model"] == "composer-2.5")
    check("tier override beats keywords",
          hint("規劃這個架構", low, tier="light")["suggestion"] == "opencode")
    check("local no_data is still eligible",
          hint("查一下", {"opencode": {"error": "no_data"},
                         "pi": {"error": "not_installed"},
                         "grok": {"error": "not_installed"},
                         "cursor": {"error": "not_installed"}})["suggestion"] == "opencode")
    demoted = hint("修這個函式", {"grok": {"week_pct": 20}, "cursor": {"week_pct": 95}})
    check("a full cursor week moves behind grok", demoted["suggestion"] == "grok", demoted)
    check("cursor with no reading says so",
          hint("修這個函式", {"grok": {"error": "not_installed"},
                             "cursor": {"error": "no_data"}})["note"] == "用量未知")
    skipped = hint("系統設計", {"claude": {"week_pct": 95}, "grok": {"week_pct": 10}})
    check("opus at 90% is skipped", skipped["suggestion"] == "grok", skipped)
    unknown = hint("系統設計", {"claude": {"error": "no_data"}, "grok": {"week_pct": 0}})
    check("unknown usage is not treated as zero and still eligible",
          unknown["suggestion"] == "claude", unknown)
    paid = hint("用 gpt-5 改這個", low)
    check("a named paid model is marked and not selected",
          paid["paid_named"] and paid["model"] != "gpt-5"
          and paid["suggestion"] in ("grok", "cursor", "opencode", "pi", "claude"), paid)
    check("the suggestion never names a cursor third-party model",
          hint("用 claude 寫程式", {"grok": {"error": "auth_required"},
                                  "cursor": {"week_pct": 5}})["model"] == "composer-2.5")
    out = R.annotate({"success": True, "message": "Delegated to Coding (s2)",
                      "details": {"sid": "s2"}}, "修這個函式", low)
    check("annotate keeps the delegate message",
          out["message"] == "Delegated to Coding (s2)"
          and out["details"]["route_hint"]["suggestion"] == "cursor")
    check("annotate does not raise on garbage", R.annotate(None, "x") is None)


def test_hint_spawns_nothing():
    calls = []
    real = subprocess.run
    subprocess.run = lambda *a, **k: calls.append(a)
    try:
        hint("寫一個函式", {"grok": {"week_pct": 12}})
    finally:
        subprocess.run = real
    check("hint does not spawn a process", calls == [])


if __name__ == "__main__":
    test_tiers_and_quota()
    test_hint_spawns_nothing()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        for item in FAILED:
            print(" -", item)
        sys.exit(1)
    print("PASS")
