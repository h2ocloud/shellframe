#!/usr/bin/env python3
"""Signing a tab in again, step by step, from the chat view (tab_login.py; api_chat.chat_login; /link/login).

Asked for: "Login expired · Please run /login" in the chat view should lead someone who knows neither
the product nor the CLI through signing in, without typing commands.
  - which step /login is at, read from the screen in Claude Code's own words (from its 2.1.325 binary):
    the menu, the browser (with its URL, whole even when the terminal wrapped it), the code to paste,
    done; nothing else is taken for a sign-in
  - the card shows only when the last turn says the sign-in expired, or while a sign-in runs
  - each button presses what its step needs: /login, the menu's first or second choice, the code and
    Enter, the Enter after "Login successful"
Demo data only. Run: .venv/bin/python tests_tab_login.py
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.modules.setdefault("webview", MagicMock())
sys.path.insert(0, str(HERE))
import tab_login  # noqa: E402

tab_login.time.sleep = lambda s: None
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


MENU = """ Select login method:

 ❯ 1. Claude account with subscription · Pro, Max, Team, or Enterprise
   2. Anthropic Console account · API usage billing
"""
URL = "https://claude.ai/oauth/authorize?code=true&client_id=demo&state=demo-state-0123456789"
BROWSER = f""" Opening browser to authorize Claude Code…

 Browser didn't open? Use the url below to sign in (c to copy)

{URL}
"""
CODE = BROWSER + "\n Paste code here if prompted > "
DONE = " Login successful. Press Enter to continue…\n"

print("which step")
check("the menu", tab_login.step_of(MENU) == {"step": "menu"})
check("the browser, with its URL whole", tab_login.step_of(BROWSER) == {"step": "browser", "url": URL}, tab_login.step_of(BROWSER))
check("the code to paste back", tab_login.step_of(CODE) == {"step": "code", "url": URL})
check("done", tab_login.step_of(DONE) == {"step": "done"})
check("an ordinary screen is no sign-in", tab_login.step_of("❯ \n  ⏵⏵ bypass permissions on") == {"step": ""})
check("words about a subscription alone are not the menu", tab_login.step_of("pick Claude account with subscription") == {"step": ""})

print("when the card shows")
EXPIRED = [{"kind": "user_msg", "text": "請整理一下"}, {"kind": "assistant_text", "text": "Login expired · Please run /login"}]
check("the last turn says it expired", tab_login.expired_in(EXPIRED))
check("…not when the person wrote it (quoting it)", not tab_login.expired_in([{"kind": "user_msg", "text": "Please run /login 是什麼"}]))
check("…not when it was earlier and the tab went on", not tab_login.expired_in(EXPIRED + [{"kind": "assistant_text", "text": "好了"}]))
screens = {"s1": ""}
tab_login.screen = lambda api, sid: screens.get(sid, "")
api = types.SimpleNamespace(sessions={})
tab_login._active.clear()
check("nothing expired, nothing running: no card, the screen not even read", tab_login.view(api, "s1", [{"kind": "assistant_text", "text": "好"}]) == {})
check("expired: the card offers to sign in", tab_login.view(api, "s1", EXPIRED) == {"step": "expired"})
screens["s1"] = MENU
check("a sign-in under way: the step on the screen", tab_login.view(api, "s1", EXPIRED) == {"step": "menu"})

print("each button")
pressed = []


class Tab:
    sid = "s1"

    def write(self, data):
        pressed.append(("key", data))


api = types.SimpleNamespace(sessions={"s1": Tab()},
                            _send_text_to_session=lambda s, text, submit=False: pressed.append(("text", text, submit)))
screens["s1"] = ""
r = tab_login.act(api, "s1", "start")
check("start: types /login with its Enter, and the card follows it", pressed == [("text", "/login", True)]
      and r["login"] == {"step": "starting"}, (pressed, r))
pressed.clear(); tab_login.act(api, "s1", "subscription")
check("subscription: the highlighted first choice, Enter", pressed == [("key", "\r")], pressed)
pressed.clear(); tab_login.act(api, "s1", "console")
check("console: down, then Enter", pressed == [("key", "\x1b[B"), ("key", "\r")], pressed)
pressed.clear()
check("a code: pasted with its Enter; an empty one is refused",
      tab_login.act(api, "s1", "code", "  ")["code"] == "empty" and not pressed
      and tab_login.act(api, "s1", "code", "demo-code-123")["success"] and pressed == [("text", "demo-code-123", True)], pressed)
pressed.clear(); tab_login.act(api, "s1", "finish")
check("finish: the Enter after 'Login successful', and the sign-in is over", pressed == [("key", "\r")] and "s1" not in tab_login._active)
check("an unknown step does nothing", tab_login.act(api, "s1", "rm -rf")["code"] == "unknown")

print("the routes")
src = (HERE / "frame_link.py").read_text(encoding="utf-8")
route = src.split('if path == "/link/login":', 1)[1].split("if path", 1)[0]
check("POST /link/login needs a peer that may control this computer, and runs tab_login",
      "_peer_may_control(peer_id)" in route and '"tab_login"' in route)
chat = (HERE / "api_chat.py").read_text(encoding="utf-8")
check("chat_view says the step (login) and chat_login presses it", 'body["login"] = tab_login.view' in chat and "def chat_login" in chat)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
