#!/usr/bin/env python3
"""The model table: what each CLI's picker lists, what a pin does to a launch command, and what the
check lets through. Pure Python; nothing is started.

Asked for: a bot's model is chosen from a list built from ShellBot's own table instead of typed
(a typed key that was not exactly the CLI's key was stored, pinned, and silently did nothing).
  - one table: the picker's list, the old PROVIDERS shape, the check and the pin all come from it
  - every CLI's list: "CLI default" first (key ""), its models, "Other (custom)" last; a CLI that
    takes no pin on its command line (Pi, Antigravity, a command ShellBot does not know) has only the default
  - choosing a model puts exactly that key on the launch command, once, for every listed model
  - a custom key is checked against the table and the ids its families come in, never by starting
    a CLI; one it does not know is refused, with the likely one offered
Run: .venv/bin/python tests_agent_model.py
"""
import pathlib
import sys

import agent_model as M

HERE = pathlib.Path(__file__).resolve().parent
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


CMD = {"claude": "claude --dangerously-skip-permissions", "codex": "sf-codex --search", "opencode": "opencode",
       "grok": "grok --no-alt-screen", "cursor": "cursor-agent --trust --force"}
FLAG = {"claude": "--model", "codex": "-m", "opencode": "--model", "grok": "-m", "cursor": "--model"}

print("1. one table")
check("the old shape is derived from it: provider -> (flag, keys)",
      all(M.PROVIDERS[p] == (FLAG[p], [c["key"] for c in M.choices(p)[1:-1]]) for p in CMD), M.PROVIDERS)
check("…the providers are the five that pin a model", sorted(M.PROVIDERS) == sorted(CMD), sorted(M.PROVIDERS))
check("Cursor still offers only its subscription model (the others spend prepaid credit)",
      M.PROVIDERS["cursor"][1] == ["composer-2.5"])
check("every listed model has a name a person reads, apart from its key",
      all(c["label"] and c["label"] != "" for p in CMD for c in M.choices(p)[1:-1]))
check("provider_of finds each CLI from its launch command", all(M.provider_of(CMD[p]) == p for p in CMD))

print("2. what each CLI's picker lists")
claude = M.choices("claude")
check("Claude Code: CLI default, Opus, Sonnet, Haiku, Fable, then Other",
      [c["key"] for c in claude] == ["", "opus", "sonnet", "haiku", "fable", M.CUSTOM]
      and [c["label"] for c in claude][:5] == ["CLI default", "Opus", "Sonnet", "Haiku", "Fable"], claude)
check("Codex", [c["key"] for c in M.choices("codex")] == ["", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2", M.CUSTOM])
check("Grok Build", [c["key"] for c in M.choices("grok")] == ["", "grok-4.7", "grok-4.7-build-fast", "grok-4.6", M.CUSTOM])
check("Cursor Agent", [c["key"] for c in M.choices("cursor")] == ["", "composer-2.5", M.CUSTOM])
check("OpenCode names a model provider/model and has no list of its own: the default and Other",
      [c["key"] for c in M.choices("opencode")] == ["", M.CUSTOM])
for cli in CMD:
    got = M.choices(cli)
    check(f"{cli}: the first is the CLI's default (key \"\"), the last is Other, flagged custom, and no other is",
          got[0] == {"key": "", "label": "CLI default"} and got[-1].get("custom") is True and got[-1]["key"] == M.CUSTOM
          and not any(c.get("custom") for c in got[:-1]), got)
check("a CLI that picks its model inside it (Pi, Antigravity): only the default, so the field is greyed",
      M.choices("pi") == [{"key": "", "label": "CLI default"}] and M.choices("agy") == M.choices("pi"))
check("a command ShellBot does not know: the same", M.choices("") == M.choices("pi") and M.choices("my-wrapper") == M.choices("pi"))
check("the two words that are not models read in Chinese on request",
      M.choices("claude", zh=True)[0]["label"] == "CLI 預設" and M.choices("claude", zh=True)[-1]["label"] == "其他（自訂）"
      and M.choices("claude", zh=True)[1:-1] == claude[1:-1])
check("label_of: the table's name, else the key", M.label_of("claude", "sonnet") == "Sonnet"
      and M.label_of("claude", "claude-opus-4-8") == "claude-opus-4-8" and M.label_of("", "x") == "x")

print("3. choosing a model puts it on the launch command")
for cli, cmd in CMD.items():
    for key in [c["key"] for c in M.choices(cli)[1:-1]]:
        got = M.apply(cmd, key)
        check(f"{cli} {key}: the flag and the key, once, on the same command",
              got.count(FLAG[cli] + " ") == 1 and f"{FLAG[cli]} {key}" in got and got.startswith(cmd.split()[0])
              and M.current_model(got) == key and M.provider_of(got) == cli, got)
check("another choice replaces the first, never a second flag",
      M.apply(M.apply("claude --x", "opus"), "haiku") == "claude --x --model haiku")
check("the default takes the pin off: the CLI's own model again",
      M.apply("claude --model opus --x", "") == "claude --x" and M.apply("grok -m grok-4.7 --effort high", "") == "grok --effort high")
check("a key with brackets is quoted for the shell and read back whole",
      M.apply("claude", "sonnet[1m]") == "claude --model 'sonnet[1m]'" and M.current_model(M.apply("claude", "sonnet[1m]")) == "sonnet[1m]")
check("a command that takes no pin is left alone", M.apply("pi", "x") == "pi" and M.apply("agy --foo", "gpt-5") == "agy --foo")
check("pinned: the role's model, else what its command already pins",
      M.pinned("claude --model haiku", "")["model"] == "haiku" and M.pinned("claude --model haiku", "opus")["model"] == "opus"
      and M.pinned("claude", "")["model"] == "" and M.pinned("claude", "")["ok"] is True)

print("4. a typed key is checked, never by starting a CLI")
ACCEPT = [("claude", "opus", "listed"), ("claude", "fable", "listed"), ("claude", "opusplan", "known"),
          ("claude", "sonnet[1m]", "known"), ("claude", "opus[1m]", "known"),
          ("claude", "claude-opus-4-8", "known"), ("claude", "claude-haiku-4-5-20251001", "known"),
          ("claude", "claude-3-5-sonnet-20241022", "known"), ("claude", "claude-fable-5", "known"),
          ("codex", "gpt-5.5", "listed"), ("codex", "gpt-5.1-codex-max", "known"), ("codex", "o4-mini", "known"),
          ("codex", "codex-mini-latest", "known"),
          ("grok", "grok-4.6", "listed"), ("grok", "grok-code-fast-1", "known"),
          ("cursor-agent", "composer-2.5", "listed"), ("cursor-agent", "composer-3", "known"),
          ("cursor-agent", "claude-4.5-sonnet", "known"), ("cursor-agent", "gpt-5", "known"), ("cursor-agent", "auto", "known"),
          ("opencode", "anthropic/claude-sonnet-4", "known"), ("opencode", "local/qwen3-coder:30b", "known"),
          ("claude", "", "default"), ("pi", "", "default"), ("my-wrapper --x", "", "default")]
for cmd, key, kind in ACCEPT:
    r = M.check(cmd, key)
    check(f"{cmd} {key or '(empty)'}: ok, {kind}", r["ok"] and r["kind"] == kind and r["code"] == "", r)
REFUSE = [("claude", "sonet", "unknown_model", "sonnet"), ("claude", "Sonnet", "unknown_model", "sonnet"),
          ("claude", "OPUS", "unknown_model", "opus"), ("claude", "claude-sonet-4", "unknown_model", ""),
          ("claude", "gpt-5", "unknown_model", ""), ("claude", "claude-sonnet", "unknown_model", "sonnet"),
          ("claude", "sonnet[2m]", "unknown_model", "sonnet"),
          ("codex", "opus", "unknown_model", ""), ("codex", "gpt5", "unknown_model", "gpt-5.5"),
          ("grok", "grok4.7", "unknown_model", "grok-4.7"), ("grok", "sonnet", "unknown_model", ""),
          ("cursor-agent", "banana", "unknown_model", ""), ("cursor-agent", "opus", "unknown_model", ""),
          ("opencode", "sonnet", "unknown_model", ""),
          ("claude", "Sonnet 4.5", "bad_model", "sonnet"), ("claude", "opus; rm -rf /", "bad_model", ""),
          ("claude", "$(id)", "bad_model", ""), ("claude", "opus\nsonnet", "bad_model", "sonnet"), ("claude", "a" * 81, "bad_model", ""),
          ("claude", "'opus'", "bad_model", "opus"),
          ("pi", "gpt-5", "model_fixed", ""), ("agy", "gemini", "model_fixed", ""), ("my-wrapper --x", "opus", "model_fixed", ""),
          ("", "opus", "model_fixed", "")]
for cmd, key, code, hint in REFUSE:
    r = M.check(cmd, key)
    check(f"{cmd or '(no command)'} {key[:24]!r}: refused, {code}" + (f", offers {hint}" if hint else ""),
          r["ok"] is False and r["code"] == code and r["suggestion"] == hint, r)
check("a key from another CLI is refused for this one, both ways",
      not M.check("claude", "gpt-5")["ok"] and not M.check("codex", "sonnet")["ok"] and not M.check("grok", "gpt-5.5")["ok"])
check("what is checked is the command's CLI: Opus is fine on Claude Code and not on its Codex wrapper",
      M.check("claude --x", "opus")["ok"] and not M.check("sf-codex", "opus")["ok"])
check("the check starts nothing: it is regexes over the table",
      "subprocess" not in (HERE / "agent_model.py").read_text(encoding="utf-8")
      and "Popen" not in (HERE / "agent_model.py").read_text(encoding="utf-8"))

print("5. a key that is already stored")
bad = M.pinned("claude", "sonet")
check("an unknown stored key is not ok, and says which one was meant",
      bad["ok"] is False and bad["code"] == "unknown_model" and bad["suggestion"] == "sonnet" and bad["model"] == "sonet", bad)
bad = M.pinned("claude --model Opus", "")
check("…also when it is only in the command", bad["ok"] is False and bad["model"] == "Opus", bad)
bad = M.pinned("pi", "gpt-5")
check("a model kept on a CLI that takes none is flagged: it does nothing", bad["ok"] is False and bad["code"] == "model_fixed", bad)
check("a good stored key is ok", M.pinned("codex", "gpt-5.4")["ok"] is True and M.pinned("codex", "gpt-5.4")["kind"] == "listed")

check("a model that is not text (a hand-edited config) is read as text, never an error",
      M.apply("claude", 5) == "claude --model 5" and M.check("claude", 5)["code"] == "unknown_model"
      and M.pinned("claude", None)["model"] == "" and M.check("claude", None)["kind"] == "default")

print("6. one place to change")
src = (HERE / "agent_model.py").read_text(encoding="utf-8")
check("the module says where a new model goes", "this table is the one place to change" in src and '("key", "Label")' in src)

print(f"\nResults: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
