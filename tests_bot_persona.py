#!/usr/bin/env python3
"""A bot's persona in its CLI's system prompt (bots.with_persona; api_bots._bots_launch_cmd).

Reported: a bot asked "what are your settings" described its CLI and nothing of its name or 執掌, and
colleagues found bots no different from a plain tab. The brief arrives as a first message (weaker than the
system prompt, gone after a compaction) and a bot without memory got nothing at all.
  - Claude Code gets --append-system-prompt-file <its persona.md>, pi --append-system-prompt <file>; an
    sf-<cli>-… launcher counts as that CLI; a `cd …&&` / env prefix is looked past
  - a CLI that takes no added system prompt keeps its command, and the persona goes in the first message
  - the file says its name, its 執掌 (or that none is written), its folder; quoted in the command
  - waking a bot and a delegate that opens one both launch with it; a bot without memory whose CLI takes no
    persona gets it as its first message, one whose CLI does gets nothing extra
  - a relaunch (--resume) keeps the flag and its file
  - measured once on Claude Code 2.1.325 with Haiku: asked who it is, it answered with the persona's name
    and 執掌 (not repeated here: it costs a model call)
Demo data only. Run: .venv/bin/python tests_bot_persona.py
"""
import shlex
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.modules.setdefault("webview", MagicMock())
import bots  # noqa: E402
import launch_cmd  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


ROOT = Path(tempfile.mkdtemp(prefix="sf-persona-"))
bots.bot_dir = lambda role: ROOT / str(role)
ENTRY = {"label": "示範秘書", "responsibility": "替使用者整理每週筆記、追蹤待辦", "bot": {"memory": True}}

print("which CLIs take it")
for cmd, flag in (("claude", "--append-system-prompt-file"),
                  ("claude --permission-mode bypassPermissions --dangerously-skip-permissions", "--append-system-prompt-file"),
                  ("pi", "--append-system-prompt"), ("sf-pi-spark", "--append-system-prompt"),
                  ("sf-claude-home", "--append-system-prompt-file"),
                  ("cd ~/work && FOO=1 claude --model haiku", "--append-system-prompt-file"),
                  ("codex", ""), ("sf-codex --search", ""), ("grok --permission-mode bypassPermissions", ""),
                  ("opencode", ""), ("cursor-agent --model composer-2.5", ""), ("bash", ""), ("", "")):
    check(f"{cmd or '(empty)'!r} → {flag or 'none'}", bots.persona_flag(cmd) == flag, bots.persona_flag(cmd))

print("the command and the file")
role = "示範 秘書"                       # a name with a space: the path must be quoted
out = bots.with_persona("claude --model haiku", role, ENTRY, lang="zh")
toks = shlex.split(out)
i = toks.index("--append-system-prompt-file")
path = Path(toks[i + 1])
text = path.read_text(encoding="utf-8")
check("the flag and its file are added, quoted", toks[:3] == ["claude", "--model", "haiku"] and path == ROOT / role / "persona.md", out)
check("the file says its name, its 執掌, its folder, and to read its memory and tasks",
      "「示範秘書」" in text and "替使用者整理每週筆記、追蹤待辦" in text and str(ROOT / role) in text
      and "MEMORY.md" in text and "sfctl task mine" in text, text)
check("…and to answer 'who are you' with that first", "先講上面的名字和執掌" in text)
check("no 執掌 written: it says so", "還沒寫" in bots.persona_text("x", {"label": "x"}, lang="zh"))
check("in English when the prompts are", 'You are "示範秘書"' in bots.persona_text(role, ENTRY, lang="en"))
check("a command that takes none is left as it was", bots.with_persona("codex --search", role, ENTRY) == "codex --search")
check("…and so is one that already has the flag",
      bots.with_persona(out, role, ENTRY) == out)
pi = shlex.split(bots.with_persona("sf-pi-spark", "示範工程師", ENTRY, lang="zh"))
check("pi: --append-system-prompt with the file's path (pi reads a path's contents)",
      pi[:2] == ["sf-pi-spark", "--append-system-prompt"] and pi[2].endswith("persona.md") and Path(pi[2]).exists(), pi)

print("a relaunch keeps it")
again = launch_cmd.claude_resume(shlex.split(out), "11111111-2222-3333-4444-555555555555")
check("--resume keeps --append-system-prompt-file and its path",
      again and "--append-system-prompt-file" in again and str(path) in again, again)

print("waking, delegating")
import api_bots  # noqa: E402

sent, opened = [], []


class App(api_bots.BotsApiMixin):
    def __init__(self, roster):
        self.sessions = {}
        self.roster = roster

    def _bots_enabled(self):
        return True


roster = {"示範秘書": dict(ENTRY, cmd="claude"),
          "示範助理": {"label": "示範助理", "responsibility": "回答問題", "bot": {"memory": False}, "cmd": "codex"},
          "示範工程師": {"label": "示範工程師", "responsibility": "寫程式", "bot": {"memory": False}, "cmd": "pi"}}
app = App(roster)
cmd = app._bots_launch_cmd("示範秘書", roster["示範秘書"], "claude")
check("a bot's launch command carries its persona", "--append-system-prompt-file" in cmd, cmd)
check("an external bot's does not (it has no tab)",
      app._bots_launch_cmd("外部", {"label": "外部", "bot": {"external": {"kind": "webhook"}}}, "claude") == "claude")
check("a plain role (not a bot) is left alone", app._bots_launch_cmd("角色", {"label": "角色"}, "claude") == "claude")
api_bots._cfg = lambda: {"agent_roster": roster}
api_bots.bots.ensure_dirs = lambda role: None
app._bots_bind = lambda sid, role: None
app.sessions = {"s1": types.SimpleNamespace(_init_pending=True), "s2": types.SimpleNamespace(_init_pending=True)}
first = app._bots_first_prompt("s1", "示範助理", "幫我查一下")
check("a delegate to a bot without memory whose CLI takes no persona: its persona before the task",
      first.startswith(bots.persona_text("示範助理", roster["示範助理"])) and first.endswith("幫我查一下"), first[:80])
check("…one whose CLI takes it: the task as it was (the persona is in its system prompt)",
      app._bots_first_prompt("s2", "示範工程師", "寫一支腳本") == "寫一支腳本")

src = (HERE / "api_bots.py").read_text(encoding="utf-8")
opened_src = src.split("def bots_open", 1)[1].split("def _bots_wake", 1)[0]
check("waking a bot opens its tab with the persona command, and wakes it when it has memory or the CLI took none",
      "self._bots_launch_cmd(role, entry, cmd)" in opened_src and "new_session(launch" in opened_src
      and "is_memory_bot(entry) or launch == cmd" in opened_src)
main_src = (HERE / "main.py").read_text(encoding="utf-8")
check("a delegate that opens a bot's tab launches it with the persona", "self._bots_launch_cmd(resolved_role, entry" in main_src)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
