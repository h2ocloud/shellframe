#!/usr/bin/env python3
"""A pi bot's model, set in its bot settings like any other CLI's (agent_model, pi rows).

Asked for: the model field of a bot should work for pi too (it was greyed: "pi picks its model inside it").
pi has no fixed list; the models are what the computer's pi defines (models.json), and pi exits on an id
it cannot place, while a bot's pin is applied at every wake. So:
  - the list is models.json's ids, labelled "id · provider"; nothing else from that file is read (it also
    holds each provider's key)
  - a model is kept only when the provider the command starts on (its own --provider, else its launcher's)
    has it; another provider's id, or an unknown one, is refused at save with the ones it may take.
    A provider models.json does not define (pi's built-in ones) takes any id shape
  - the pin is `--model <id>` only, never --provider: it stays on the command's provider, so a launcher
    that passes its own --api-key keeps its key on its own provider
  - pi is recognised by the program the command runs (`pi`, `sf-pi…`), not by "pi-" anywhere in it
  - a relaunched conversation stays on its last /model (agent_pi.keep_model); a new one starts on the pin
Demo data only. Run: .venv/bin/python tests_pi_roster_model.py
"""
import json
import os
import shlex
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-pi-model-"))
os.environ["PI_CODING_AGENT_DIR"] = str(TMP / "agent")
os.environ["CODEX_HOME"] = tempfile.mkdtemp()                # no models_cache.json: Codex lists the table here
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"                  # the wording below is the OS language's: pin it, as the other suites do
sys.path.insert(0, str(HERE))
sys.modules.setdefault("webview", MagicMock())
import agent_model as M  # noqa: E402
import agent_pi  # noqa: E402
import tab_model  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


SECRET = "demo-secret-value-0000"
(TMP / "agent").mkdir()
MODELS = TMP / "agent" / "models.json"


def write_models(providers):
    MODELS.write_text(json.dumps({"providers": providers}))
    t = time.time() + len(M._PI_CACHE.get("models") or []) + 1
    os.utime(MODELS, (t, t))                          # a new mtime: the cache reads it again


write_models({
    "local": {"baseUrl": "http://127.0.0.1:1/v1", "apiKey": SECRET, "api": "openai-completions",
              "models": [{"id": "model-a", "name": "A"}, {"id": "model-b"}]},
    "other": {"baseUrl": "http://127.0.0.1:2/v1", "apiKey": SECRET, "models": [{"id": "model-x"}]},
})

bindir = TMP / "bin"
bindir.mkdir()
os.environ["PATH"] = f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"


def launcher(name, body):
    p = bindir / name
    p.write_text(body)
    p.chmod(0o755)


launcher("sf-pi-local", '#!/bin/sh\nexec pi --provider local --model model-a --api-key "$KEY" "$@"\n')
launcher("sf-pi-plain", '#!/bin/sh\nexec pi "$@"\n')
launcher("sf-pi-builtin", '#!/bin/sh\nexec pi --provider=cloudco "$@"\n')

print("models.json: ids and provider names only")
check("the computer's models, in order", M.pi_models() == [("local", "model-a"), ("local", "model-b"), ("other", "model-x")],
      M.pi_models())
got = M.choices("pi", zh=True)
check("the picker: CLI default, then 'id · provider', then Other",
      [c["key"] for c in got] == ["", "model-a", "model-b", "model-x", M.CUSTOM]
      and got[1]["label"] == "model-a · local" and got[3]["label"] == "model-x · other", got)
check("nothing else from the file is kept: no key, no address",
      SECRET not in json.dumps(got) + json.dumps(M.pi_models()) + json.dumps(M._PI_CACHE)
      and "127.0.0.1" not in json.dumps(M._PI_CACHE), M._PI_CACHE)
write_models({"local": {"apiKey": SECRET, "models": [{"id": "model-a"}, {"id": "model-c"}]}})
check("read again when the file changes", M.pi_models() == [("local", "model-a"), ("local", "model-c")], M.pi_models())
MODELS.write_text("{ not json")
os.utime(MODELS, (time.time() + 99, time.time() + 99))
check("a broken file: nothing listed, no error", M.pi_models() == [] and len(M.choices("pi")) == 2)
write_models({"local": {"apiKey": SECRET, "models": [{"id": "model-a"}, {"id": "model-b"}, {"id": "bad id; x"}]},
              "other": {"apiKey": SECRET, "models": [{"id": "model-x"}]}})
check("an id that is not one word is left out", ("local", "bad id; x") not in M.pi_models(), M.pi_models())

print("which commands are pi")
for cmd in ("pi", "pi --session /x.jsonl", "sf-pi-local", "/opt/bin/sf-pi-local --x", "cd ~/w && sf-pi-local"):
    check(f"{cmd!r} → pi", M.provider_of(cmd) == "pi", M.provider_of(cmd))
check("a 'pi-' folder in a Claude command is not pi", M.provider_of("cd ~/pi-work && claude --x") == "claude")
check("…nor a pi- file it is given", M.provider_of("claude --append-system-prompt-file ~/pi-notes.md") == "claude")
check("a shell wrapping pi is not taken for it (nothing on its line would reach pi)", M.provider_of("zsh -lc 'pi'") == "")

print("the provider a command starts on")
check("its own --provider", M.pi_provider("pi --provider other --session x") == "other")
check("…or --provider=<p>", M.pi_provider("pi --provider=other") == "other")
check("its launcher's", M.pi_provider("sf-pi-local --session x") == "local")
check("…--provider=<p> in a launcher", M.pi_provider("sf-pi-builtin") == "cloudco")
check("none said: pi's default", M.pi_provider("pi") == "" and M.pi_provider("sf-pi-plain") == "")
check("a launcher that cannot be found: none", M.pi_provider("sf-pi-nowhere") == "")

print("the check")
ok = lambda cmd, m: M.check(cmd, m)
check("an id of the launcher's provider: listed", ok("sf-pi-local", "model-b")["ok"] and ok("sf-pi-local", "model-b")["kind"] == "listed")
r = ok("sf-pi-local", "model-x")
check("another provider's id is refused (pi would look for it under the launcher's)",
      r["ok"] is False and r["code"] == "unknown_model" and r["keys"] == ["model-a", "model-b"], r)
r = ok("sf-pi-local", "model-bb")
check("an unknown id is refused, the close one offered", r["ok"] is False and r["suggestion"] == "model-b", r)
check("no provider named: any id models.json defines", ok("pi", "model-x")["ok"] and ok("sf-pi-plain", "model-a")["ok"])
check("…and not one it does not", ok("pi", "gpt-5")["ok"] is False)
check("a provider models.json does not define (pi's built-in): any id shape", ok("sf-pi-builtin", "big-model-1")["kind"] == "known"
      and ok("pi --provider cloudco", "m:7b")["ok"])
check("…but still one word", ok("sf-pi-builtin", "a b")["code"] == "bad_model")
check("empty: the CLI's default", ok("sf-pi-local", "")["kind"] == "default")

print("the pin on the command")
cmd = M.apply("sf-pi-local --append-system-prompt /p.md", "model-b")
t = shlex.split(cmd)
check("--model <id>, and no --provider", t[-2:] == ["--model", "model-b"] and "--provider" not in t, cmd)
again = M.apply(M.apply(cmd, "model-a"), "model-a")
check("pinned again: still one --model", shlex.split(again).count("--model") == 1 and again.endswith("--model model-a"), again)
check("cleared: no pin left", "--model" not in M.apply(cmd, ""), M.apply(cmd, ""))
check("current_model reads it", M.current_model(cmd) == "model-b")
check("the pin the editor shows", M.pinned("sf-pi-local", "model-b")["ok"] and M.pinned("sf-pi-local", "model-b")["label"] == "model-b")

print("a relaunched conversation and the pin")


def session(*models, name):
    p = TMP / name
    lines = [{"type": "session"}] + [{"type": "model_change", "provider": pr, "modelId": m} for pr, m in models]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


pinned = M.apply("sf-pi-local", "model-a")
f = session(("local", "model-a"), ("local", "model-b"), name="switched.jsonl")
out, _ = agent_pi.keep_model(f"{pinned} --session {f}", f)
check("switched with /model since: the conversation stays on it, one --model",
      shlex.split(out).count("--model") == 1 and out.endswith("--model model-b"), out)
f = session(("local", "model-a"), name="same.jsonl")
out, _ = agent_pi.keep_model(f"{pinned} --session {f}", f)
check("never switched: the pin it started on", out.endswith("--model model-a") and shlex.split(out).count("--model") == 1, out)

print("the editor's words and the in-tab switch")
import api_bots  # noqa: E402
words = api_bots.BotsApiMixin._model_words("sf-pi-local", "model-x", M.check("sf-pi-local", "model-x"))
check("a refusal lists the ids this command may take", "model-a" in words and "model-b" in words and "model-x」" in words
      and "model-x、" not in words, words)
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
words_en = api_bots.BotsApiMixin._model_words("sf-pi-local", "model-x", M.check("sf-pi-local", "model-x"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
check("…and in English, the same ids", 'Unknown model "model-x"' in words_en and "(model-a, model-b)" in words_en
      and 'Did you mean "model-b"?' in words_en, words_en)
check("the in-tab switch lists the same ids, by id (what pi's picker matches)",
      [c["key"] for c in tab_model.models_for("sf-pi-local")] == ["model-a", "model-b", "model-x"],
      tab_model.models_for("sf-pi-local"))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
