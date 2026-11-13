#!/usr/bin/env python3
"""Switching a running tab's model: what each CLI's screen says, the keys pressed, and what the tab does
around it (tab_model.py, api_tab_model.py; the table of measurements is docs/tab-model.md).

The screens below are written down from the real CLIs (Claude Code 2.1.323, Codex 0.142.1, OpenCode 1.18.30,
Pi 0.74.2); models and paths are demo ones.
  - Claude Code's menu: rows, the ❯ row, the level under the list, the `s` for this tab only; a key picks its
    row (an alias is the first row of its family, a full id is a row behind "More models")
  - the keys: Up/Down to the row, Left/Right for the level, then `s`. Never Enter or a number (those save the
    pick as the default of every new session), never `/model <name>`
  - Codex: `/model` alone (a name after it is a prompt), the number of the model, then the level's number; a
    confirmation is only the one that is new
  - OpenCode and Pi: type to filter, no match closes the list; Pi's level by Shift+Tab until the footer says it
  - a menu that did not open, a key the screen does not offer, a model the list does not hold: closed with Esc and
    reported, nothing pressed blind
  - Grok and Cursor: one typed line; the answer is read from the CLI's files and reported as unconfirmed when
    nothing shows it
  - the tab: a busy tab waits (the job says so), a draft is asked about and never overwritten, a box that cannot
    be read is asked about first, a failure says why, a CLI with no way inside says it must be restarted
  - the chip shows what the switch reported until the monitor reads something else; "also keep it for the bot"
    only when asked

Run: .venv/bin/python tests_tab_model.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-tabmodel-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")

import agent_status  # noqa: E402
import api_tab_model as atm  # noqa: E402
import main  # noqa: E402
import tab_delivery as td  # noqa: E402
import tab_model as tm  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "agent_roster": {}}), encoding="utf-8")
td.POLL, td.TAKEN_WAIT = 0.05, 0.2

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ── screens (written down from the real CLIs) ─────────────────────────────
CLAUDE_MENU = """\
❯ /model
──────────────────────────────────────────────────
  Select model
  Switch between Claude models. Your pick becomes the default for new sessions.
    1.  Default (recommended)  Sonnet 5.5 · Efficient for routine tasks
    2.  Opus 5.5               For complex work and everyday tasks
    3.  Fable 5.2              For your toughest challenges
  ❯ 4.  Sonnet 5.5 ✔           Most efficient for simpler tasks
    5.  Haiku 5.5              Fastest for quick answers
    ▸   More models
  ● High effort ←/→ to adjust
  Enter to set as default · s to use this session only · Esc to cancel"""
CLAUDE_MENU_OLD = CLAUDE_MENU.replace("Switch between Claude models. Your pick becomes the default for new sessions.", "") \
    .replace(" · s to use this session only", "")
CODEX_MODELS = """\
  Select Model and Effort
  Access legacy models by running codex -m <model_name> or in your config.toml
› 1. gpt-5.5 (default)  Frontier model for complex coding, research, and real-world work.
  2. gpt-5.4            Strong model for everyday coding.
  3. gpt-5.4-mini       Small, fast, and cost-efficient model for simpler coding tasks.
  4. gpt-5.3-codex      Coding-optimized model.
  5. gpt-5.2            Optimized for professional work and long-running agents.
  Press enter to confirm or esc to go back"""
CODEX_LEVELS = """\
  Select Reasoning Level for gpt-5.4
  1. Low               Fast responses with lighter reasoning
› 2. Medium (default)  Balances speed and reasoning depth for everyday tasks
  3. High              Greater reasoning depth for complex problems
  4. Extra high        Extra high reasoning depth for complex problems
  Press enter to confirm or esc to go back"""
PI_SELECTOR = """\
──────────────────────────────────────────
Scope: all | scoped
tab scope (all/scoped)
> fast
→ model-fast [demo]
  Model Name: Demo fast model
──────────────────────────────────────────
/work
0.0%/131k (auto)                                                       model-think • medium"""
OC_DIALOG = """\
                                                 Select model                                     esc
                                                 pickle
                                                 Big Pickle                                  OpenCode
                                                 Connect provider ctrl+a  Favorite ctrl+f
                                      ┃  Build · Flash 125B"""

print("reading the screens")
m = tm.claude_menu(CLAUDE_MENU.splitlines())
check("Claude: rows, the ❯ row, the level, `s`", [r["n"] for r in m["rows"]] == [1, 2, 3, 4, 5] and m["cursor"] == 4
      and m["effort"] == "high" and m["session_key"] and m["more"] and not m["expanded"]
      and next(r for r in m["rows"] if r["n"] == 4) == {"n": 4, "name": "Sonnet 5.5", "now": True}, m)
check("Claude: an older menu has no `s` for this tab", tm.claude_menu(CLAUDE_MENU_OLD.splitlines())["session_key"] is False)
check("Claude: no menu, no answer", tm.claude_menu(["❯ hello", "  ⎿  done"]) is None)
rows = lambda key: (tm.claude_row(m, key) or {}).get("n")
check("Claude: an alias is the first row of its family, not the Default row that mentions Sonnet",
      rows("opus") == 2 and rows("sonnet") == 4 and rows("haiku") == 5 and rows("fable") == 3, [rows(k) for k in ("opus", "sonnet")])
check("Claude: \"\" is the Default row; a model the menu lacks is None", rows("") == 1 and rows("opusplan") is None and rows("claude-opus-4-8") is None)
check("Claude: a full id is the row named after it", tm._claude_want("claude-opus-4-8") == "opus 4.8" and tm._claude_want("claude-sonnet-5") == "sonnet 5")
check("Claude: the confirmation under the echo of /model",
      tm.claude_confirm(["❯ /model", "  ⎿  Set model to Opus 5.5 for this session only with max effort"]) ==
      {"name": "Opus 5.5", "effort": "max", "session_only": True})
check("Claude: an older confirmation above the echo is not this one",
      tm.claude_confirm(["❯ /model", "  ⎿  Set model to Opus 5.5 for this session only", "❯ /model"]) is None)
check("Claude: 'saved as your default' is told apart from 'this session only'",
      tm.claude_confirm(["❯ /model", "  ⎿  Set model to Fable 5.2 and saved as your default for new sessions"])["session_only"] is False)
check("Claude: (default) is not part of the name",
      tm.claude_confirm(["❯ /model", "  ⎿  Set model to Fable 5.2 (default) for this session only"])["name"] == "Fable 5.2")
cm = tm.codex_menu(CODEX_MODELS.splitlines())
check("Codex: the model menu", cm["kind"] == "model" and [r["id"] for r in cm["rows"]] ==
      ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"] and cm["cursor"] == 1 and cm["rows"][0]["default"], cm)
lm = tm.codex_menu(CODEX_LEVELS.splitlines())
check("Codex: the level menu, labels as keys", lm["kind"] == "effort" and lm["model"] == "gpt-5.4"
      and [r["id"] for r in lm["rows"]] == ["low", "medium", "high", "xhigh"] and lm["cursor"] == 2, lm)
old = ["• Model changed to gpt-5.4 low", "› Explain this codebase"]
check("Codex: a confirmation that was there before is not a new one", tm.codex_new_confirm(old, tm.codex_confirms(old)) is None)
check("Codex: one more line, or a changed last one, is new",
      tm.codex_new_confirm(old + ["• Model changed to gpt-5.2 xhigh"], tm.codex_confirms(old)) == {"name": "gpt-5.2", "effort": "xhigh"}
      and tm.codex_new_confirm(["• Model changed to gpt-5.2 high"], tm.codex_confirms(old))["name"] == "gpt-5.2")
check("OpenCode: the dialog, its matches, the prompt line", tm.opencode_open(OC_DIALOG.splitlines()) and tm.opencode_rows(OC_DIALOG.splitlines()) == 1
      and tm.opencode_current(["x", "┃  Build · Big Pickle OpenCode", "tab agents"]) == "Big Pickle OpenCode")
check("OpenCode: nothing typed matches", tm.opencode_rows(OC_DIALOG.splitlines()[:2] + [OC_DIALOG.splitlines()[3]]) == 0)
check("Pi: the selector's rows and its footer", tm.pi_selector(PI_SELECTOR.splitlines())["rows"] ==
      [{"id": "model-fast", "provider": "demo", "now": False, "cursor": True}] and tm.pi_footer(PI_SELECTOR.splitlines()) == {"model": "model-think", "level": "medium"})
check("Pi: 'thinking off' and a model with no level", tm.pi_footer(["0.0%/131k (auto)      m • thinking off"])["level"] == "off"
      and tm.pi_footer(["0.0%/131k (auto)              model-fast"]) == {"model": "model-fast", "level": None})

print("what a command can do")
check("restart-only: Antigravity and a command ShellBot does not know", tm.plan("agy", "x", None)["code"] == "restart"
      and tm.plan("my-wrapper --x", "x", None)["code"] == "restart" and tm.link_fields("agy")["in_session"] is False)
check("Codex is only ever opened with /model: a name after it would be a prompt",
      tm.plan("codex", "gpt-5.4", "high")["opener"] == "/model" and tm.plan("codex", "gpt-5.4", None)["opener"] == "/model")
check("Claude is opened with /model alone too (its argument form saves the default)", tm.plan("claude", "opus", None)["opener"] == "/model")
check("OpenCode opens /models; Grok and Cursor type the line; Pi's level alone types nothing",
      tm.plan("opencode", "x", None)["opener"] == "/models" and tm.plan("grok", "grok-4.6", "high")["opener"] == "/model grok-4.6 high"
      and tm.plan("grok", None, "low")["opener"] == "/effort low" and tm.plan("cursor-agent", "composer-2.5", None)["opener"] == "/model composer-2.5"
      and tm.plan("pi", None, "high")["opener"] == "")
check("a level the CLI does not have, a model that is a typo, nothing at all",
      tm.plan("claude", None, "off")["code"] == "bad_effort" and tm.plan("opencode", "x", "high")["code"] == "bad_effort"
      and tm.plan("claude", "sonet", None, custom=True)["code"] == "unknown_model" and tm.plan("claude", None, None)["code"] == "nothing")
check("a typed name is checked by the table of the CLI; the list's keys need no check",
      tm.plan("codex", "gpt-5.4", None, custom=True)["ok"] and tm.plan("claude", "opus", None)["ok"])
check("the list is agent_model's, with no default row unless the CLI has one",
      [c["key"] for c in tm.models_for("claude")] == ["", "opus", "sonnet", "haiku", "fable"]
      and [c["key"] for c in tm.models_for("codex")][0] == "gpt-5.5" and "" not in [c["key"] for c in tm.models_for("codex")]
      and tm.models_for("pi") == [] and tm.models_for("opencode") == [])
check("the phone's fields", tm.link_fields("claude") == {"in_session": True, "efforts": ["low", "medium", "high", "xhigh", "max"], "unverified": False,
                                                          "persists": False}
      and tm.link_fields("grok")["unverified"] is True and tm.link_fields("opencode")["efforts"] == [])


# ── CLIs that answer the keys ─────────────────────────────────────────────
class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class SimIO(Clock):
    """A screen and a keyboard: `screen()` is what the tab shows now, `key()` what a key does."""

    def __init__(self, screen, key, probe=None):
        super().__init__()
        self.screen, self.key, self.pressed, self._probe = screen, key, [], probe

    def lines(self):
        return self.screen().splitlines()

    def send(self, *keys):
        for k in keys:
            self.pressed.append(k)
            self.key(k)

    def probe(self):
        return self._probe() if self._probe else None


class Claude:
    """Claude Code's /model menu as measured: rows 1-5, ❯ moves, the level is per row (reset to the row's own),
    `s` is this tab only, Enter or a number saves the default, Esc keeps the model."""
    LEVELS = ["low", "medium", "high", "xhigh", "max"]

    def __init__(self, session_key=True, opened=True, more=True):
        self.open, self.cur, self.eff, self.log, self.session_key, self.more = opened, 4, 2, [], session_key, more
        self.model = "Sonnet 5.5"

    def names(self):
        return ["Default (recommended)", "Opus 5.5", "Fable 5.2", "Sonnet 5.5", "Haiku 5.5"]

    def screen(self):
        if not self.open:
            return "\n".join(["❯ /model"] + self.log + ["──", "❯ ", "──"])
        rows = [f"  {'❯' if i + 1 == self.cur else ' '} {i + 1}.  {n}{' ✔' if n == self.model else ''}    desc" for i, n in enumerate(self.names())]
        text = ["  Select model"] + rows + (["    ▸   More models"] if self.more else []) + [
            f"  ● {self.LEVELS[self.eff].capitalize()} effort ←/→ to adjust",
            "  Enter to set as default" + (" · s to use this session only" if self.session_key else "") + " · Esc to cancel"]
        return "\n".join(["❯ /model", "──"] + text)

    def key(self, k):
        if not self.open:
            return
        if k == "Down" and self.cur < 5:
            self.cur += 1
            self.eff = 1
        elif k == "Up" and self.cur > 1:
            self.cur -= 1
            self.eff = 1
        elif k == "Right":
            self.eff = min(4, self.eff + 1)
        elif k == "Left":
            self.eff = max(0, self.eff - 1)
        elif k == "Esc":
            self.open = False
            self.log = [f"  ⎿  Kept model as {self.model}"]
        elif k == {"text": "s"} and self.session_key:
            self.model = self.names()[self.cur - 1].split(" (")[0]
            self.open = False
            self.log = [f"  ⎿  Set model to {self.model} for this session only with {self.LEVELS[self.eff]} effort"]
        elif k == "Enter" or (isinstance(k, dict) and k["text"].isdigit()):
            self.model = self.names()[self.cur - 1].split(" (")[0]
            self.open = False
            self.log = [f"  ⎿  Set model to {self.model} and saved as your default for new sessions"]


print("Claude Code's menu, driven")
c = Claude()
io = SimIO(c.screen, c.key)
r = tm.drive("claude", io, "opus", "max")
check("opus + max: Up to the row, Right to max, then s", r["ok"] and r["name"] == "Opus 5.5" and r["effort"] == "max"
      and io.pressed == ["Up", "Up", "Right", "Right", "Right", {"text": "s"}], (r, io.pressed))
check("…and never Enter or a number, which would save the default", "Enter" not in io.pressed and not any(isinstance(k, dict) and k["text"].isdigit() for k in io.pressed))
c = Claude()
io = SimIO(c.screen, c.key)
r = tm.drive("claude", io, "haiku", "low")
check("haiku + low: down one, left to the lowest", r["ok"] and r["name"] == "Haiku 5.5" and r["effort"] == "low" and io.pressed[0] == "Down", (r, io.pressed))
c = Claude()
io = SimIO(c.screen, c.key)
r = tm.drive("claude", io, None, "xhigh")
check("only the level: the row it opened on stays", r["ok"] and r["name"] == "Sonnet 5.5" and r["effort"] == "xhigh" and "Up" not in io.pressed and "Down" not in io.pressed, (r, io.pressed))
c = Claude()
io = SimIO(c.screen, c.key)
r = tm.drive("claude", io, "claude-opus-4-8", None)
check("a model the list does not hold: Esc, said so with what it has", r["ok"] is False and r["code"] == "not_listed" and "Opus 5.5" in r["message"]
      and c.open is False and io.pressed[-1] == "Esc", (r, io.pressed))
c = Claude(session_key=False)
io = SimIO(c.screen, c.key)
r = tm.drive("claude", io, "opus", None)
check("a menu with no `s` for this tab: nothing is changed, and it says why",
      r["ok"] is False and r["code"] == "no_session_only" and c.model == "Sonnet 5.5" and "default" in r["message"] and io.pressed == ["Esc"], (r, io.pressed))
c = Claude(opened=False)
io = SimIO(c.screen, c.key)
r = tm.drive("claude", io, "opus", None)
check("a menu that never opened: reported, no key pressed", r["ok"] is False and r["code"] == "no_menu" and io.pressed == [] and io.now() >= tm.WAIT_OPEN, r)


class Deaf(Claude):
    def key(self, k):
        if k == {"text": "s"}:
            return
        super().key(k)


c = Deaf()
io = SimIO(c.screen, c.key)
r = tm.drive("claude", io, "opus", None)
check("s pressed but nothing said: not a success, the menu is closed", r["ok"] is False and r["code"] == "no_confirm" and c.open is False, (r, c.open))


class Codex:
    def __init__(self):
        self.stage, self.log, self.model, self.lvl = "model", [], "gpt-5.6", "high"

    def screen(self):
        body = {"model": CODEX_MODELS, "effort": CODEX_LEVELS, "": ""}[self.stage]
        return "\n".join(self.log + [body, "› Explain this codebase", f"  {self.model} {self.lvl} · /work"])

    def key(self, k):
        t = k["text"] if isinstance(k, dict) else k
        if self.stage == "model" and t in "12345":
            self.model, self.stage = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"][int(t) - 1], "effort"
        elif self.stage == "effort" and (t in "1234" or t == "Enter"):
            self.lvl = {"1": "low", "2": "medium", "3": "high", "4": "xhigh", "Enter": "medium"}[t]
            self.stage = ""
            self.log = self.log + [f"• Model changed to {self.model} {self.lvl}"]
        elif t == "Esc":
            self.stage = {"effort": "model"}.get(self.stage, "")


print("Codex's two menus, driven")
c = Codex()
io = SimIO(c.screen, c.key)
r = tm.drive("codex", io, "gpt-5.4", "low")
check("gpt-5.4 + low: its number, then the level's number", r["ok"] and r["name"] == "gpt-5.4" and r["effort"] == "low" and io.pressed == [{"text": "2"}, {"text": "1"}], (r, io.pressed))
c = Codex()
c.log = ["• Model changed to gpt-5.2 high"]            # an older switch still on the screen
io = SimIO(c.screen, c.key)
r = tm.drive("codex", io, "gpt-5.2", "high")
check("the same switch again is a new confirmation, not the one on the screen", r["ok"] and len(c.log) == 2 and r["name"] == "gpt-5.2", (r, c.log))
c = Codex()
io = SimIO(c.screen, c.key)
r = tm.drive("codex", io, "gpt-5-codex", "high")
check("a model the picker does not list (the table has it): not listed, closed, what it lists is said",
      r["ok"] is False and r["code"] == "not_listed" and "gpt-5.2" in r["message"] and c.stage == "" and io.pressed[0] == "Esc", (r, io.pressed, c.stage))
c = Codex()
io = SimIO(c.screen, c.key)
r = tm.drive("codex", io, None, "high")
check("only a level: Codex needs the model with it", r["ok"] is False and r["code"] == "not_listed" and c.stage == "", r)

print("pickers, driven")


class Picker:
    def __init__(self, rows):
        self.rows, self.typed, self.open, self.line = rows, "", True, "Build · Flash 125B"

    def screen(self):
        if self.open:
            hit = [r for r in self.rows if self.typed.lower() in r.lower()]
            return "\n".join(["                       Select model                      esc", f"                       {self.typed or 'Search'}"] +
                             [f"                       {h}                OpenCode" for h in hit] + ["                       Connect provider ctrl+a"])
        return f"┃  {self.line}\n tab agents"

    def key(self, k):
        if isinstance(k, dict):
            self.typed += k["text"]
        elif k == "Enter" and self.open:
            hit = [r for r in self.rows if self.typed.lower() in r.lower()]
            if hit:
                self.line, self.open = f"Build · {hit[0]} OpenCode", False
        elif k == "Esc":
            self.open = False


p = Picker(["Big Pickle", "Other Model"])
io = SimIO(p.screen, p.key)
r = tm.drive("opencode", io, "big-pickle", None)
check("OpenCode: typed, Enter on the first match, the prompt line read back", r["ok"] and "Big Pickle" in r["name"] and io.pressed == [{"text": "big pickle"}, "Enter"], (r, io.pressed))
p = Picker(["Big Pickle"])
io = SimIO(p.screen, p.key)
r = tm.drive("opencode", io, "zzz", None)
check("OpenCode: no match closes the list and says so", r["ok"] is False and r["code"] == "no_match" and p.open is False and io.pressed[-1] == "Esc", (r, io.pressed))


class Pi:
    LEVELS = ["off", "minimal", "low", "medium", "high"]

    def __init__(self, models=("model-fast", "model-think", "model-thinker")):
        self.models, self.model, self.lvl, self.open, self.typed, self.msg = list(models), "model-think", 3, False, "", ""
        self.open = True

    def screen(self):
        foot = "0.0%/131k (auto)                    " + self.model + (f" • {self.LEVELS[self.lvl] if self.lvl else 'thinking off'}" if self.model != "model-fast" else "")
        if self.open:
            hit = [m for m in self.models if self.typed in m]
            return "\n".join(["Scope: all | scoped", "tab scope (all/scoped)", f"> {self.typed}"] +
                             [f"{'→' if i == 0 else ' '} {m} [demo]" for i, m in enumerate(hit)] + ["──", "/work", foot])
        return "\n".join([self.msg, "──", "/work", foot])

    def key(self, k):
        if isinstance(k, dict):
            self.typed += k["text"]
        elif k == "Enter" and self.open:
            hit = [m for m in self.models if self.typed in m]
            if hit:
                self.model, self.open, self.msg = hit[0], False, f" Model: {hit[0]}"
        elif k == "Esc":
            self.open = False
        elif k == "BTab":
            self.lvl = (self.lvl + 1) % 5
            self.msg = f" Thinking level: {self.LEVELS[self.lvl]}"


p = Pi()
io = SimIO(p.screen, p.key)
r = tm.drive("pi", io, "model-fast", None)
check("Pi: filter, Enter, the footer read back", r["ok"] and r["name"] == "model-fast" and p.model == "model-fast", (r, io.pressed))
p = Pi()
p.open = False
io = SimIO(p.screen, p.key)
r = tm.drive("pi", io, None, "high")
check("Pi: Shift+Tab until the footer says the level (it wraps: medium to high is one press)", r["ok"] and r["effort"] == "high" and io.pressed == ["BTab"], (r, io.pressed))
p = Pi()
p.open, p.lvl = False, 4
io = SimIO(p.screen, p.key)
r = tm.drive("pi", io, None, "low")
check("Pi: from the top it goes round to low", r["ok"] and r["effort"] == "low" and io.pressed == ["BTab"] * 3, (r, io.pressed))
p = Pi()
io = SimIO(p.screen, p.key)
r = tm.drive("pi", io, "model-th", None)
check("Pi: two models match 'model-th': it asks for more of the name, nothing chosen", r["ok"] is False and r["code"] == "ambiguous" and p.model == "model-think", r)
p = Pi()
io = SimIO(p.screen, p.key)
r = tm.drive("pi", io, "nothing-like", None)
check("Pi: no match closes the selector", r["ok"] is False and r["code"] == "no_match" and p.open is False, r)

print("Grok and Cursor: one line, read from the CLI's files")
state = {"info": {"name": "Grok 4.7", "effort": "low"}}
io = SimIO(lambda: "", lambda k: None, probe=lambda: state["info"])
r = tm.drive("grok", io, "grok-4.6", "high")
check("nothing shows the new model: sent, not confirmed (never 'done')", r["ok"] is True and r.get("unconfirmed") and r["code"] == "unconfirmed", r)
state["info"] = {"name": "Grok 4.6", "effort": "high"}
io = SimIO(lambda: "", lambda k: None, probe=lambda: state["info"])
r = tm.drive("grok", io, "grok-4.6", "high")
check("its own file says so: confirmed", r["ok"] and not r.get("unconfirmed") and r["name"] == "Grok 4.6" and r["effort"] == "high", r)
io = SimIO(lambda: "", lambda k: None)
r = tm.drive("agy", io, "x", None)
check("a CLI with no way in drives nothing", r["ok"] is False and r["code"] == "restart" and io.pressed == [])


# ── the tab around it ─────────────────────────────────────────────────────
RULE = "─" * 40
box = lambda *rows: "\n".join(["some conversation", RULE, *rows, RULE, "  footer"])


class Tab:
    def __init__(self, sid, cmd):
        self.sid, self._custom_label, self.cmd = sid, sid, cmd
        self.alive, self.lock, self._recent = True, threading.Lock(), bytearray()
        self._tmux_name = None
        self._startup_trust_pending = True
        self._last_output_activity_time = time.time()
        self.written = []

    def write(self, data, user_activity=True):
        self.written.append(data)


def make_api(sid="s1", cmd="claude"):
    api = Api.__new__(Api)
    api.sessions = {sid: Tab(sid, cmd)}
    api.bridge = api.line_bridge = None
    api._window = None
    api.state, api.screen, api.sent, api.model = "", box("\x1b[39m❯\xa0"), [], None
    api.roles = {}

    def send(s, text, submit=False):
        api.sent.append((s.sid, text, submit))
    api._send_text_to_session = send
    api._agent_state_for_list = lambda x: api.state
    api._agent_status_snapshot = lambda x: ({"model": api.model}, 0)
    api._bridge_session_busy = lambda x: False
    api._bots_role_of = lambda x, cfg=None: api.roles.get(x, "")
    api._worker_ctx = lambda x, s: {"cmd": s.cmd}
    return api


td.capture = lambda api, sid: api.screen
td.visible_text = lambda api, sid: api.visible if hasattr(api, "visible") else ""
real_drive = tm.drive
calls = []


def fake_drive(result):
    def drive(kind, io, model, effort):
        calls.append((kind, model, effort, td.HOLD.get(io.sid)))
        return dict(result)
    return drive


def job(api, sid="s1"):
    return json.loads(api.tab_model_info(sid))["job"]


def reset():
    wait_for(lambda: not td._draining, 3)            # a drain thread of the test before must be done with its queue
    atm._JOBS.clear()
    atm._SHOWN.clear()
    td.HOLD.clear()
    td._queues.clear()
    del calls[:]


print("the tab around it")
reset()
api = make_api()
tm.drive = fake_drive({"ok": True, "code": "", "message": "Switched to Opus 5.5 · high", "name": "Opus 5.5", "effort": "high"})
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "opus", "effort": "high"})))
check("an idle tab: /model typed at once, then the menu is driven with the tab held", r["success"] and r["state"] == "switching"
      and api.sent == [("s1", "/model", True)] and wait_for(lambda: job(api)["state"] == "ok") and calls == [("claude", "opus", "high", "model")], (r, api.sent, calls))
check("…and the job tells what the CLI reported", job(api)["name"] == "Opus 5.5" and job(api)["effort_now"] == "high" and td.HOLD == {})
info = json.loads(api.tab_model_info("s1"))
check("the chip shows the switch's own answer until the monitor reads something else (the transcript has nothing new)",
      info["current"] == {"name": "Opus 5.5", "effort": "high", "from": "switch"} and info["current_key"] == "opus", info["current"])
api.model = {"name": "Sonnet 5.5", "effort": "low"}
check("…then the monitor is believed again", json.loads(api.tab_model_info("s1"))["current"] == {"name": "Sonnet 5.5", "effort": "low", "from": "status"})

reset()
api = make_api()
api.state = "working"
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "haiku"})))
j = job(api)
check("a busy tab: queued, the job says it is waiting and why; nothing typed", r["state"] == "waiting" and j["state"] == "waiting" and j["reason"] == "working" and api.sent == [], (r, j))
api.state = ""
check("…it goes in when the tab is free", wait_for(lambda: api.sent == [("s1", "/model", True)]) and wait_for(lambda: job(api)["state"] == "ok"), (api.sent, job(api)))

reset()
api = make_api()
api.state = "working"
json.loads(api.tab_model_set("s1", json.dumps({"model": "haiku"})))
c = json.loads(api.tab_model_cancel("s1"))
api.state = ""
time.sleep(0.4)
check("a waiting switch can be cancelled: it never types", c["success"] and api.sent == [] and job(api)["state"] == "failed" and job(api)["code"] == "cancelled", (c, api.sent, job(api)))

reset()
api = make_api()
api.screen = box("\x1b[39m❯\xa0half a sentence")
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "opus"})))
check("text in the box: asked first, shown, nothing typed", r["success"] is False and r["code"] == "draft" and r["draft"] == "half a sentence" and api.sent == [] and job(api) is None, r)
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "opus", "draft": "wait"})))
check("…'wait for it to be sent' queues behind the draft and leaves it alone", r["state"] == "waiting" and api.sent == [] and job(api)["reason"] == "draft", r)
api.screen = box("\x1b[39m❯\xa0")
check("…and goes in once the box is empty", wait_for(lambda: api.sent == [("s1", "/model", True)]), api.sent)

reset()
api = make_api(cmd="codex")
api.screen = "› Explain this codebase\n  gpt-5.4 high · /work"
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "gpt-5.4"})))
check("a box that cannot be read (Codex has none to read): the person is asked first, nothing typed", r["success"] is False and r["code"] == "unreadable" and api.sent == [], r)
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "gpt-5.4", "unreadable_ok": True})))
check("…and once they say so, it is typed (the opener never carries the name)", r["success"] and api.sent == [("s1", "/model", True)], (r, api.sent))
wait_for(lambda: job(api)["state"] == "ok")

reset()
api = make_api()
tm.drive = fake_drive({"ok": False, "code": "not_listed", "message": "Claude Code's menu does not list \"x\"", "name": "", "effort": None})
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "opus"})))
wait_for(lambda: job(api)["state"] == "failed")
check("a failure is shown as it is, with its reason; the chip does not change", job(api)["state"] == "failed" and job(api)["code"] == "not_listed"
      and "does not list" in job(api)["message"] and json.loads(api.tab_model_info("s1"))["current"] is None)
check("…and the tab is let go", td.HOLD == {})
tm.drive = fake_drive({"ok": True, "code": "unconfirmed", "message": "Sent, but nothing shows it", "name": "", "effort": None, "unconfirmed": True})
reset()
api = make_api(cmd="grok")
json.loads(api.tab_model_set("s1", json.dumps({"model": "grok-4.6", "unreadable_ok": True})))
wait_for(lambda: job(api) and job(api)["state"] != "waiting" and job(api)["state"] != "switching")
check("an unconfirmed switch is neither ok nor a failure, and the chip is not changed", job(api)["state"] == "unconfirmed" and json.loads(api.tab_model_info("s1"))["current"] is None
      and api.sent == [("s1", "/model grok-4.6", True)], (job(api), api.sent))

print("while the menu is driven, nothing else is typed")
reset()
api = make_api()
gate = threading.Event()


def slow_drive(kind, io, model, effort):
    gate.wait(3)
    return {"ok": True, "code": "", "message": "ok", "name": "Opus 5.5", "effort": None}


tm.drive = slow_drive
json.loads(api.tab_model_set("s1", json.dumps({"model": "opus"})))
wait_for(lambda: td.HOLD.get("s1") == "model")
r2 = td.deliver(api, "s1", "a chat message")
check("a message sent meanwhile waits ('it is switching its model')", td.HOLD.get("s1") == "model" and r2["queued"] and r2["reason"] == "model"
      and "switching its model" in r2["message"] and ("s1", "a chat message", True) not in api.sent, r2)
gate.set()
check("…and goes in after", wait_for(lambda: ("s1", "a chat message", True) in api.sent), (api.sent, td._draining, dict(td._queues), td.HOLD))
r3 = json.loads(api.tab_model_set("s1", json.dumps({"model": "haiku"})))
check("a second switch while one is under way is refused", r3["success"] is False or r3["state"] in ("waiting", "switching"), r3)

print("a CLI that has to be started again")
reset()
tm.drive = real_drive
api = make_api(cmd="agy")
info = json.loads(api.tab_model_info("s1"))
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "x"})))
check("Antigravity: says it needs a restart, types nothing", info["in_session"] is False and "started again" in info["restart_note"]
      and r["success"] is False and r["code"] == "restart" and api.sent == [], (info, r))
orig = dict(tm.SWITCH["cursor"])
tm.SWITCH["cursor"] = dict(orig, how="restart")
api = make_api(cmd="cursor-agent")
r = json.loads(api.tab_model_set("s1", json.dumps({"model": "composer-2.5"})))
info = json.loads(api.tab_model_info("s1"))
check("a CLI whose own list has the models but no way inside: restart, and the list is still offered for 'save and switch now'",
      r["code"] == "restart" and api.sent == [] and info["in_session"] is False and [c["key"] for c in info["models"]] == ["composer-2.5"], (r, info["models"]))
tm.SWITCH["cursor"] = orig
api = make_api(cmd="bash")
check("a terminal tab is not an AI tab", json.loads(api.tab_model_info("s1")) == {"success": True, "ai": False})

print("the page's questions")
reset()
api = make_api()
info = json.loads(api.tab_model_info("s1"))
check("what the page needs: CLI, in-session, the table's list, the levels, nothing unknown guessed",
      info["cli"] == "claude" and info["in_session"] and info["current"] is None and info["efforts"][0] == {"key": "low", "label": "Low"}
      and [c["key"] for c in info["models"]] == ["", "opus", "sonnet", "haiku", "fable"] and info["custom"] is True and info["bot"] is None
      and info["persists"] is False and info["unverified"] is False, info)
api.roles["s1"] = "助理"
main.CONFIG_FILE.write_text(json.dumps({"settings": {}, "agent_roster": {"助理": {"label": "助理", "cmd": "claude"}}}), encoding="utf-8")
info = json.loads(api.tab_model_info("s1"))
check("a remembered bot's tab says so, and whether its model can be saved", info["bot"] == {"role": "助理", "label": "助理", "can_save": True}, info["bot"])
saved = []
api.bots_switch = lambda role, cli, model, apply: json.dumps(saved.append((role, cli, model, apply)) or {"success": True})
r = json.loads(api.tab_model_save_bot("s1", "opus"))
check("'also keep it for the bot' takes it from its next wake on, and nothing saved before the person says yes", r["success"] and saved == [("助理", "", "opus", "next_wake")])
check("Pi has no pin on its command, so there is nothing to save for its bot",
      json.loads(make_api(cmd="pi").tab_model_info("s1"))["models"] == [] and tm.link_fields("pi")["in_session"] is True)

print("a switch does not outlive the app")
saved_state = td.STATE_FILE
td.STATE_FILE = str(TMP / "queue.json")
td._saved = None
td._resumed_at = 0.0
Path(td.STATE_FILE).write_text(json.dumps([{"sid": "s1", "text": "/model", "what": "model:abc", "t0": time.time()},
                                          {"sid": "s1", "text": "hello", "what": "", "t0": time.time()}]))
check("a queued /model is not carried over a restart (nothing would drive its menu); a message is", [r["text"] for r in td._read_saved()] == ["hello"])
td.STATE_FILE, td._saved = saved_state, None

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
