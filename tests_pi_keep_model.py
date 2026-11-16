#!/usr/bin/env python3
"""A pi tab started again on its conversation keeps the model it was last on (agent_pi.keep_model).

Reported: a pi bot relaunched went on with its conversation but back on its launcher's default model;
the `/model` switch made in the conversation was gone, and the default pointed at a server that was down,
so its first reply was an HTTP 500. The launcher runs `exec pi --provider x --model y --api-key … "$@"`,
and pi takes a --model given on the command line over the one a conversation was on. pi writes a
`model_change` when a conversation starts and on each /model, none when it is taken back up.
  - same provider as at the start: `--model <last>` added after the launcher's (pi takes the last one)
  - plain `pi` pinning nothing: unchanged (pi puts a conversation back on its model itself)
  - never switched and nothing pinned: unchanged
  - a pin from an earlier relaunch goes, so a later /model (even back to the first) wins
  - another provider: `--provider <p> --model <m>`, unless the command or its launcher passes --api-key
    (pi ties that key to the provider the command line ends on): then unchanged, with a note
  - a launcher that cannot be read counts as one that passes a key; a shell wrapper is left alone
  - the relaunch path runs it on the session file it resumes
Demo data only. Run: .venv/bin/python tests_pi_keep_model.py
"""
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agent_pi  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")


tmp = Path(tempfile.mkdtemp())


def session(*models, name="s.jsonl"):
    """A session file: a header, then a model_change per (provider, model), with messages between."""
    p = tmp / name
    lines = [{"type": "session", "id": "x"}]
    for prov, model in models:
        lines.append({"type": "model_change", "provider": prov, "modelId": model})
        lines.append({"type": "message", "message": {"role": "user", "content": "hi"}})
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


def launcher(name, body):
    p = tmp / "bin" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(body)
    p.chmod(0o755)
    return p


os.environ["PATH"] = f"{tmp / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
KEYED = launcher("pi-keyed", '#!/bin/sh\nexec pi --provider local --model model-a --api-key "$KEY" "$@"\n')
BARE = launcher("pi-bare", '#!/bin/sh\nexec pi --provider local --model model-a "$@"\n')


def args(cmd):
    return shlex.split(cmd)


def last_of(tokens, flag):
    vals = [tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t == flag]
    return vals[-1] if vals else None


print("session_models")
f = session(("local", "model-a"), ("local", "model-b"), ("local", "model-c"))
first, last = agent_pi.session_models(f)
check("first is the one it started on", first == {"provider": "local", "model": "model-a"})
check("last is the last /model", last == {"provider": "local", "model": "model-c"})
check("no model_change → (None, None)", agent_pi.session_models(session(name="empty.jsonl")) == (None, None))
check("missing file → (None, None)", agent_pi.session_models(str(tmp / "nope.jsonl")) == (None, None))
bad = tmp / "bad.jsonl"
bad.write_text('{"type": "model_change", broken\n' + json.dumps(
    {"type": "model_change", "provider": "local", "modelId": "model-a"}) + "\n")
check("a broken line is skipped", agent_pi.session_models(str(bad))[1] == {"provider": "local", "model": "model-a"})

print("the reported case: a keyed launcher, /model on the same provider")
f = session(("local", "model-a"), ("local", "model-b"), name="same.jsonl")
cmd = f"pi-keyed --session {f} --append-system-prompt /bots/x/persona.md"
out, note = agent_pi.keep_model(cmd, f)
t = args(out)
check("--model of the last switch added", last_of(t, "--model") == "model-b")
check("no --provider added (the launcher's stays, and its key with it)", "--provider" not in t)
check("--session and the persona stay", f in t and "/bots/x/persona.md" in t)
check("the note names the model", "model-b" in note)

print("unchanged")
check("never switched, nothing pinned", agent_pi.keep_model(
    f"pi-keyed --session {session(('local', 'model-a'), name='one.jsonl')}",
    str(tmp / "one.jsonl"))[0] == f"pi-keyed --session {tmp / 'one.jsonl'}")
check("plain pi pinning nothing", agent_pi.keep_model(f"pi --session {f}", f) == (f"pi --session {f}", ""))
check("plain pi with only --models (scoped models do not apply to a resumed conversation)",
      agent_pi.keep_model(f"pi --models a,b --session {f}", f)[0] == f"pi --models a,b --session {f}")
check("a session without model_change", agent_pi.keep_model("pi-keyed --session x", str(tmp / "empty.jsonl"))[0]
      == "pi-keyed --session x")
check("no session file", agent_pi.keep_model("pi-keyed", "")[0] == "pi-keyed")
wrapped = f"zsh -lc 'pi --model model-a --session {f}'"
check("a shell wrapper (words after the script never reach pi)", agent_pi.keep_model(wrapped, f)[0] == wrapped)

print("pins on the command")
out, _ = agent_pi.keep_model(f"pi --provider local --model model-a --session {f}", f)
t = args(out)
check("plain pi pinning one: the old --model goes, the last is pinned", t.count("--model") == 1
      and last_of(t, "--model") == "model-b")
check("…and its --provider is given again", t.count("--provider") == 1 and last_of(t, "--provider") == "local")
back = session(("local", "model-a"), ("local", "model-b"), ("local", "model-a"), name="back.jsonl")
out, _ = agent_pi.keep_model(f"pi-keyed --session {back} --model model-b", back)
t = args(out)
check("a pin from an earlier relaunch does not outlive /model back to the first",
      t.count("--model") == 1 and last_of(t, "--model") == "model-a")
out, _ = agent_pi.keep_model(f"pi-keyed --session {f} --model=model-z", f)
check("--model=<x> form is dropped too", "--model=model-z" not in out and last_of(args(out), "--model") == "model-b")
out, _ = agent_pi.keep_model(f"cd ~/w && pi-keyed --session {f}", f)
check("a cd prefix stays", out.startswith("cd ~/w && pi-keyed ") and last_of(args(out.split("&& ", 1)[1]), "--model")
      == "model-b")

print("another provider")
other = session(("local", "model-a"), ("cloud", "model-x"), name="other.jsonl")
out, note = agent_pi.keep_model(f"pi-keyed --session {other}", other)
check("a launcher passing --api-key: unchanged", out == f"pi-keyed --session {other}")
check("…with a note saying why", "api-key" in note and "cloud" in note)
out, note = agent_pi.keep_model(f"pi-bare --session {other}", other)
t = args(out)
check("a launcher without a key: --provider and --model of the last", last_of(t, "--provider") == "cloud"
      and last_of(t, "--model") == "model-x")
out, _ = agent_pi.keep_model(f"pi --provider local --model model-a --api-key k --session {other}", other)
check("plain pi passing --api-key: unchanged", "cloud" not in out)
out, _ = agent_pi.keep_model(f"pi --provider local --model model-a --session {other}", other)
t = args(out)
check("plain pi without a key: switched, one pin each", t.count("--provider") == 1 and t.count("--model") == 1
      and last_of(t, "--provider") == "cloud" and last_of(t, "--model") == "model-x")
out, _ = agent_pi.keep_model(f"pi-nowhere --session {other}", other)
check("a launcher that cannot be found counts as keyed: unchanged", out == f"pi-nowhere --session {other}")
binlauncher = launcher("pi-bin", "\x7fELF\0\0binary")
out, _ = agent_pi.keep_model(f"pi-bin --session {other}", other)
check("a launcher that is not a text script counts as keyed: unchanged", out == f"pi-bin --session {other}")

print("wiring")
src = (HERE / "api_accounts.py").read_text()
i = src.index("launch_cmd.pi_with_session(cmd, pfile)")
check("the relaunch keeps the model right after resuming the session file",
      "agent_pi.keep_model(cmd, pfile)" in src[i:i + 400])

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
