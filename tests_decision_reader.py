#!/usr/bin/env python3
"""The shared "waiting on you" reader and answer (the phone's /link/bots and /link/answer,
Telegram's choice buttons, the desktop's chat view): three faults measured on real tabs.

  1. An option wrapped onto a second line dropped every option above it (Claude Code
     2.1.322, an 80-column tab, a long folder in "always allow access to …"): the
     question came out as the end of the folder name, with options 3 and 4 only.
  2. The reader took 40 lines of scrollback and the first menu in them: Codex's start-up
     update menu, answered and scrolled up, was offered over the approval on screen
     (and a numbered list in the agent's own answer, over the dialog under it).
  3. An answer was the number and Enter. Codex takes the number alone and moves on, so
     the Enter answered the next prompt (measured: "Skip" on the update menu, then the
     folder-trust prompt accepted with its default "Yes, continue").
  4. What a question was about came from the tool call, its first line cut at 40
     characters: the dialog on screen now gives its own words (`detail`).

Each case is written against the reader's real entry points: menu_options, the
`decision` the phone gets, and /link/answer's _bots_link_answer.

Run: .venv/bin/python tests_decision_reader.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-decision-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import bridge_telegram as bt  # noqa: E402
bt._LOG_FILE = str(TMP / "bridge.log")
import bridge_home  # noqa: E402
import main  # noqa: E402
import tab_delivery  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {}}), encoding="utf-8")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


W = 80
# 1. Claude Code 2.1.322's permission dialog on an 80-column tab, option 2 wrapped (the
#    measured layout; the folder is a placeholder of the same length).
WRAPPED = ["─" * W, " Bash command", ' Tip: auto mode handles these prompts for you — choose "switch to auto mode"',
           " below", " Create probe.txt in the working directory and confirm it exists", "╌" * W,
           " touch probe.txt && ls -l probe.txt", "╌" * W, " Do you want to proceed?", " ❯ 1. Yes",
           "   2. Yes, and always allow access to /work/a-demo-project-with-a-rather-l/name-",
           "      and-more from this project",
           "   3. Yes, and switch to auto mode · auto mode handles these prompts for you",
           "   4. No", "", " Esc to cancel · Tab to amend"]
assert len(WRAPPED[10]) == W
# 2. Codex: the start-up update menu, answered, still above the approval on screen.
UPDATE = ["  ✨ Update available! 0.142.1 -> 0.167.0", "", "  Release notes: https://example.com/releases", "",
          "› 1. Update now (runs `npm install -g @openai/codex`)", "  2. Skip", "  3. Skip until next version", "",
          "  Press enter to continue"]
APPROVAL = ["  Would you like to run the following command?", "", "  $ touch codex-probe.txt", "",
            "› 1. Yes, proceed (y)", "  2. Yes, and don't ask again for this command in this session (a)",
            "  3. No, and tell Codex what to do differently (esc)", "", "  Press enter to confirm or esc to cancel"]
CONVO_LIST = ["⏺ 下週要做的：", "", "  1. 首頁載入速度調校", "  2. 補齊匯出功能的測試", ""]
TRUST = ["> You are in /work/x", "", "  Do you trust the contents of this directory?", "",
         "› 1. Yes, continue", "  2. No, quit", "", "  Press enter to continue"]


class Tab:
    def __init__(self, sid, cmd):
        self.sid, self.cmd, self.cwd, self.alive = sid, cmd, "~", True
        self._tmux_name, self._custom_label = f"sf_{sid}", None
        self.written = []
        self.on_write = None

    def write(self, data):
        self.written.append(data)
        if self.on_write:
            self.on_write(data)


SCROLL, VISIBLE = {}, {}       # what tmux has with 40 lines of history / what is on screen
api = Api.__new__(Api)
api.sessions = {sid: Tab(sid, cmd) for sid, cmd in (("c1", "claude"), ("x1", "codex"), ("x2", "codex"),
                                                   ("c2", "claude"), ("x3", "codex"), ("r1", "claude"))}
api.bridge = api.line_bridge = None
api._window = None
api._agent_status_snapshot = lambda sid: ({"state": "decision", "prompt_at": 0.0}, 0)
api._agent_state_for_list = lambda sid: "decision"
tab_delivery.screen_text = lambda api_, sid, lines=60: "\n".join(SCROLL.get(sid, VISIBLE.get(sid, [])))
_real_visible = getattr(tab_delivery, "visible_text", None)
tab_delivery.visible_text = lambda api_, sid: "\n".join(VISIBLE.get(sid, []))
tab_delivery.ANSWER_SETTLE_S = 0.3


def decision(sid):
    return api._link_decision(sid, "decision", {"state": "decision"}) or {}


print("1. an option wrapped onto a second line")
VISIBLE["c1"] = WRAPPED
d = decision("c1")
check("all four options, with their own numbers", [o["n"] for o in d.get("options", [])] == [1, 2, 3, 4], d.get("options"))
check("…the wrapped one whole (a cut at the screen's edge joins without a space)",
      (d.get("options") or [{}, {}])[1].get("text") == "Yes, and always allow access to "
      "/work/a-demo-project-with-a-rather-l/name-and-more from this project", d.get("options"))
check("…and the question is the question", d.get("reason") == "Do you want to proceed?", d.get("reason"))
lines = [ln for ln in WRAPPED]
opts = bt.menu_options(lines)
check("Telegram's menu reader agrees", [o["num"] for o in opts] == ["1", "2", "3", "4"], opts)
word = bt.menu_options([" ❯ 1. Yes, and always allow access to the very long folder name that",
                        "      wraps here", "   2. No", ""])
check("a wrap between words keeps its space", word and word[0]["text"].endswith("name that wraps here"), word)

print("2. an older menu, or a numbered list, above the one on screen")
SCROLL["x1"] = UPDATE + ["", "› count to 3", "", "• 1 2 3", ""] + APPROVAL
VISIBLE["x1"] = ["• 1 2 3", ""] + APPROVAL
d = decision("x1")
check("the approval on screen, not the update menu answered before it",
      [o["text"] for o in d.get("options", [])] == ["Yes, proceed (y)",
                                                     "Yes, and don't ask again for this command in this session (a)",
                                                     "No, and tell Codex what to do differently (esc)"], d.get("options"))
VISIBLE["x2"] = CONVO_LIST + ["─" * W] + WRAPPED[8:]
d = decision("x2")
check("the dialog, not the numbered list in the answer above it", [o["text"] for o in d.get("options", [])][:1] == ["Yes"]
      and len(d.get("options", [])) == 4, d.get("options"))
check("Telegram's reader takes the last menu too",
      [o["text"] for o in bt.menu_options(CONVO_LIST + WRAPPED[8:])][:1] == ["Yes"])
VISIBLE["c2"] = ["   3. Yes, and switch to auto mode · auto mode handles these prompts for you", "   4. No", "",
                 " Esc to cancel · Tab to amend"]
check("a list that does not start at 1 is not a menu (its top is off the screen)", decision("c2") == {}, decision("c2"))

print("3. the number, and Enter only while the same menu is still up")
bridge_home._answered_at.clear()
cx = api.sessions["x3"]
VISIBLE["x3"] = UPDATE
cx.on_write = lambda data: VISIBLE.__setitem__("x3", TRUST) if data.startswith("2") else None
q = decision("x3")
r = api._bots_link_answer({"sid": "x3", "n": 2, "id": q.get("id")})
check("Codex takes the number and moves on: no Enter follows it", r.get("success") and cx.written == ["2"], (r, cx.written))
check("…so the prompt it moved on to is still waiting", VISIBLE["x3"] == TRUST)
bridge_home._answered_at.clear()
cc = api.sessions["c1"]
VISIBLE["c1"] = WRAPPED
q = decision("c1")
r = api._bots_link_answer({"sid": "c1", "n": 1, "id": q.get("id")})
check("a menu still up after the number gets its Enter", r.get("success") and cc.written == ["1", "\r"], (r, cc.written))
bridge_home._answered_at.clear()
red = api.sessions["r1"]
VISIBLE["r1"] = ["⏺ 要先修哪個？", "", "────", "❯ ", "────"]
RED = [{"kind": "user_msg", "text": "修哪個"}, {"kind": "assistant_text", "text": "要先修哪個？\n[[SF:RED]]\n1. 按鈕\n2. 通知"}]
bridge_home.on_turn_end(api, "r1", read_events=lambda sid: RED)
api._agent_status_snapshot = lambda sid: ({"state": "done" if sid == "r1" else "decision", "prompt_at": 0.0}, 0)
q = api._link_decision("r1", "done", {"state": "done"}) or {}
r = api._bots_link_answer({"sid": "r1", "n": 2, "id": q.get("id")})
check("a [[SF:RED]] question is answered as a message: the number and Enter", r.get("success") and red.written == ["2\r"],
      (r, red.written))

print("4. what the question is about: the dialog's own words, whole")
# Found collecting the evidence above: the desktop's card showed the call the question is
# about as agent_status keeps it, its first line cut at 40 characters with no sign of it
# ("touch chat-shell-probe.txt && ls -l chat" for a command that went on).
for sid in ("d1", "d2", "d3"):
    api.sessions[sid] = Tab(sid, "claude")
VISIBLE["d1"] = WRAPPED
det = decision("d1").get("detail") or ""
check("the command as the dialog shows it, not cut short", "touch probe.txt && ls -l probe.txt" in det.splitlines(), det)
check("…with the dialog's title and description, in screen order",
      det.splitlines()[:1] == ["Bash command"] and 0 <= det.find("Create probe.txt") < det.find("touch probe.txt"), det)
check("…and not its rules, or the question (that is `reason`)",
      not set("─╌") & set(det) and "Do you want to proceed?" not in det, det)
pw = "x" * 12
VISIBLE["d2"] = ["─" * W, " Bash command", "╌" * W, f" curl -s https://deploy:{pw}@example.com/hook && rm -rf ./build",
                 "╌" * W, " Do you want to proceed?", " ❯ 1. Yes", "   2. No", ""]
det = decision("d2").get("detail") or ""
check("a credential in it is masked, the rest of the command kept", pw not in det and "rm -rf ./build" in det, det)
VISIBLE["d3"] = (["─" * W, " Bash command", "╌" * W] + [f" echo step {i} " + "and more " * 7 for i in range(60)]
                 + ["╌" * W, " Do you want to proceed?", " ❯ 1. Yes", "   2. No", ""])
det = decision("d3").get("detail") or ""
check("a dialog too long to send whole says it was cut", det.endswith("…") and "echo step 0 " in det and len(det) <= 2001,
      (len(det), det[-40:]))
check("no rule opening a dialog above the menu (Codex's): no words guessed for it",
      not decision("x1").get("detail"), decision("x1"))

print("9. a long option: cut with …")
long_dir = "/work/" + "/".join(["deeply-nested-folder"] * 8)
VISIBLE["L1"] = ["─" * W, " Bash command", "╌" * W, " ls " + long_dir, "╌" * W, " Do you want to proceed?",
                 " ❯ 1. Yes", f"   2. Yes, and always allow access to {long_dir} from this project", "   3. No", ""]
opts = {o["n"]: o["text"] for o in decision("L1").get("options", [])}
check("a long option is cut at 120 characters and ends with …", len(opts.get(2, "")) == 120 and opts[2].endswith("…"), opts)
check("…a short one is left whole", opts.get(1) == "Yes" and opts.get(3) == "No", opts)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
