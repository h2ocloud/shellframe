#!/usr/bin/env python3
"""A paired phone's Settings → 用量與配速: GET /link/usage (link_usage, api_remote `usage_accounts`).

Asked for: see on the phone how much of each signed-in subscription is used, and its pace.
  - each window: used %, its reset, and with the window known: pace (how much of it has gone by),
    ahead (used minus pace) and full_at (when, at the rate so far, it reaches 100 %, only before the reset)
  - an account that cannot be read says why, never a number; a stale reading is marked stale
  - the account furthest ahead of its pace comes first
  - refresh=1 re-reads at most once a minute; the route needs a peer that may see this computer
Demo data only. Run: .venv/bin/python tests_link_usage.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import link_usage  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


NOW = 1_800_000_000.0
WEEK = 7 * 24 * 60
print("a window and its pace")
# 4 days of 7 gone (57.1 %), 68 % used: ahead 10.9; at 68 % / 4 days it is full after 1.88 more days.
w = link_usage.window({"pct": 68, "reset": "週四 04:00", "reset_epoch": NOW + 3 * 86400, "window_minutes": WEEK}, NOW)
check("pace: how much of the window has gone by", w["pace"] == 57.1, w)
check("ahead: used minus pace", w["ahead"] == 10.9, w)
check("full_at: before the reset, at the rate so far", abs(w["full_at"] - (NOW + 32 / 17 * 86400)) < 2, w)
w = link_usage.window({"pct": 20, "reset": "x", "reset_epoch": NOW + 3 * 86400, "window_minutes": WEEK}, NOW)
check("behind the pace: negative ahead, no full_at (it lasts past the reset)", w["ahead"] < 0 and "full_at" not in w, w)
w = link_usage.window({"pct": 74, "reset": "x", "reset_epoch": NOW - 3600, "window_minutes": WEEK}, NOW)
check("the reset has passed: the last window's numbers, marked, no pace (it read '26 % to spare')",
      w.get("reset_passed") and "pace" not in w and "ahead" not in w, w)
w = link_usage.window({"pct": 30, "reset": "x"}, NOW)
check("no window known: the numbers only, no pace guessed", w == {"pct": 30, "reset": "x"}, w)
check("no reading: nothing", link_usage.window({"pct": None}, NOW) is None)

print("every account")
PROVIDERS = {
    "claude": {"a": {"ai": "claude", "account": "demo@example.com", "is_current_login": True,
                     "five_hr": {"pct": 42, "reset": "14:00", "reset_epoch": NOW + 7200, "window_minutes": 300},
                     "week": {"pct": 68, "reset": "週四", "reset_epoch": NOW + 3 * 86400, "window_minutes": WEEK}}},
    "codex": {"b": {"ai": "codex", "account": "demo2@example.com", "error": "auth_required",
                    "error_message": "登入已過期"},
              "c": {"ai": "codex", "account": "demo3@example.com", "stale": True, "checked": "35 分鐘前",
                    "week": {"pct": 31, "reset": "週四", "reset_epoch": NOW + 3 * 86400, "window_minutes": WEEK}}},
    "agy": {"d": {"ai": "agy", "account": "demo4@example.com",
                  "week": {"pct": 20, "reset": "週六"},
                  "groups": [{"name": "Gemini", "pct": 20, "reset": "週六"}, {"name": "Claude/GPT", "pct": 45, "reset": "週六"}]}},
}
acc = link_usage.phone_accounts(PROVIDERS, NOW)
by = {a["ref"]: a for a in acc}
check("one entry per account, with its provider's name", len(acc) == 4 and by["a"]["label"] == "Claude Code", acc)
check("the furthest ahead of its pace first", acc[0]["ref"] == "a", [a["ref"] for a in acc])
check("an account it cannot read says why, with no numbers", by["b"]["error"] == "auth_required"
      and "week" not in by["b"] and "five_hr" not in by["b"], by["b"])
check("a stale reading is marked, with when it was read", by["c"]["stale"] and by["c"]["checked"] == "35 分鐘前")
check("a provider's separate budgets come along", [g["name"] for g in by["d"]["groups"]] == ["Gemini", "Claude/GPT"])
check("the signed-in account is marked", by["a"]["current"] and not by["d"]["current"])

print("refresh")
link_usage._last_refresh["at"] = 0
check("the first refresh goes through", link_usage.refresh_allowed(NOW))
check("another within a minute reads the cache", not link_usage.refresh_allowed(NOW + 30))
check("after a minute it goes through again", link_usage.refresh_allowed(NOW + 61))

print("the command and the route")
import types  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402
sys.modules.setdefault("webview", MagicMock())
import api_remote  # noqa: E402
host = api_remote.RemoteApiMixin.__new__(api_remote.RemoteApiMixin)
asked = []
host.account_usage_all = lambda refresh=False: (asked.append(refresh), json.dumps({"success": True, "providers": PROVIDERS}))[1]
link_usage._last_refresh["at"] = 0
res = api_remote.RemoteApiMixin._execute_sfctl(host, "usage_accounts", {"refresh": True})
check("usage_accounts: the accounts, and the refresh asked of the panel's source",
      res["success"] and len(res["details"]["accounts"]) == 4 and asked == [True] and res["details"]["refreshed"], res)
res = api_remote.RemoteApiMixin._execute_sfctl(host, "usage_accounts", {"refresh": True})
check("…a second refresh at once reads the cache", asked == [True, False] and not res["details"]["refreshed"], asked)
fl = (HERE / "frame_link.py").read_text(encoding="utf-8")
route = fl.split('if path == "/link/usage":', 1)[1].split("if path ==", 1)[0]
check("GET /link/usage needs a peer that may see this computer, and runs usage_accounts",
      "_peer_may_control(peer_id)" in route and '"usage_accounts"' in route, route[:300])

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
