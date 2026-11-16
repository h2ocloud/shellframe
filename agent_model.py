"""
agent_model — which model a roster role runs on: what each CLI takes, the pin, the check.

The problem this solves, from a real fan-out: a group send opened a worker tab,
the account had no credits for the model that tab defaulted to, and the CLI
stopped on a chooser. The message sat behind a dialog nobody was watching. Worse
than the stall is the silent version of it — the tab quietly falls back to a
different model and you never learn which one answered you.

The shape that fixes it is also the shape that is useful on purpose: **the tab
that dispatches should be the strong model, and the workers it opens need not
be.** A roster role pins its own model, so the split is a decision recorded in
config rather than an accident of whatever the CLI picked today.

Each provider spells the flag differently, and a provider we do not recognise is
left alone — appending a flag a CLI does not understand turns a working launch
into a startup error, which is a far worse failure than an unpinned model.

The other silent failure was a model typed by hand: a key the CLI does not know
(`sonet`, `Sonnet 4.5`) is stored, pinned on the command line, and the tab runs on
something else, or does not start. So the page offers the table below as a list,
and what is typed instead is checked here (`check`) before it is saved. The check
never starts a CLI: it compares the key with the table and with the shapes of the
ids that table's families come in.

**When a new model ships, this table is the one place to change** (docs/bots.md,
"Updating the model list"): add `("key", "Label")` to the provider's `models`. The
picker, the check, the phone's `choices.models` and the pin all follow from it.
"""

import difflib
import functools
import json
import os
import re
import shlex
import shutil

import launch_cmd

# provider -> what its CLI takes. One table; everything below is derived from it.
#   flag    the command-line flag that pins a model
#   models  (key, label) in the order the picker lists them; the key is what the CLI is given
#   also    keys the CLI takes that the picker does not list
#   suffix  what may follow a key or id (Claude Code's `[1m]` long-context form)
#   ids     shapes of the full ids a family comes in (a regex each, matched whole). In a pattern,
#           "<keys>" stands for the keys of `models` joined with "|": a family named there is a
#           family the check knows, so a misspelt one (`claude-sonet-4`) is not taken for an id.
# The list is what the picker offers, never a restriction on the CLI: a model released tomorrow
# is typed under "Other (custom)" and checked against `also` and `ids`.
_TABLE = {
    "claude": {
        "flag": "--model",
        "models": [("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("fable", "Fable")],
        "also": ("opusplan",),
        "suffix": r"(?:\[1m\])?",
        "ids": (r"claude-(?:<keys>)-\d+(?:-\d+)?(?:-\d{8})?",      # claude-opus-4-8, claude-haiku-4-5-20251001
                r"claude-\d+(?:-\d+)?-(?:<keys>)(?:-\d{8})?"),     # claude-3-5-sonnet-20241022
    },
    "codex": {
        "flag": "-m",
        "models": [("gpt-5.5", "GPT-5.5"), ("gpt-5.4", "GPT-5.4"), ("gpt-5.4-mini", "GPT-5.4 mini"), ("gpt-5.3-codex", "GPT-5.3 Codex"),
                   ("gpt-5.2", "GPT-5.2")],          # Codex 0.142.1's own /model menu, measured
        "ids": (r"gpt-\d+(?:\.\d+)?(?:-[a-z0-9]+(?:\.\d+)?)*",     # gpt-5.1-codex-max
                r"o\d+(?:-[a-z0-9]+)*",                            # o4-mini
                r"codex-[a-z0-9]+(?:[.\-][a-z0-9]+)*"),            # codex-mini-latest
    },
    # opencode names a model `provider/model`; no list of its own, so only the shape is checked.
    "opencode": {
        "flag": "--model",
        "models": [],
        "ids": (r"[a-z0-9][a-z0-9_.\-]*/[A-Za-z0-9][\w.:/\-]*",),
    },
    # 注意：grok 啟動時帶 -m／--effort 會把值寫進 ~/.grok/config.toml 的 default，
    # 等於改了使用者全域的預設，不只這個分頁。roster 角色要 pin 才這樣用。
    "grok": {
        "flag": "-m",
        "models": [("grok-4.7", "Grok 4.7"), ("grok-4.7-build-fast", "Grok 4.7 Build Fast"), ("grok-4.6", "Grok 4.6")],
        "ids": (r"grok-[a-z0-9]+(?:[.\-][a-z0-9]+)*",),
    },
    # Must be listed: its model names carry other vendors' names (claude-…, grok-…),
    # and without this entry those tokens decided the provider, so pinning a model
    # rewrote `cursor-agent --model grok-4` into `… --model grok-4 -m <model>`.
    # Only the subscription model is offered; the others spend prepaid credit.
    # pi: its list is the machine's own (models.json, `pi_models`), not this table's. Only a model of
    # the provider the command starts on is pinned: pi exits on an id it cannot place, and a pin is
    # applied at every wake, so a wrong one would be a bot that dies each time it opens.
    "pi": {
        "flag": "--model",
        "models": [],
        "ids": (r"[A-Za-z0-9][\w.:\-]*",),
    },
    "cursor": {
        "flag": "--model",
        "models": [("composer-2.5", "Composer 2.5")],
        "also": ("auto",),
        "ids": (r"composer-\d+(?:\.\d+)*",
                r"(?:gpt|claude|sonnet|opus|haiku|gemini)(?:-[a-z0-9.]+)+",
                r"(?:grok|o\d+)(?:-[a-z0-9.]+)*"),
    },
}

# provider -> (flag, known model names): the table's older shape, kept for what reads it.
PROVIDERS = {name: (spec["flag"], [k for k, _ in spec["models"]]) for name, spec in _TABLE.items()}

MODEL_MAX = 80                                       # a model is one short word
_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+=,\[\]\-]*")


def _program(cmd: str) -> str:
    """The program a command runs (its basename), past a `cd …&&` / env prefix."""
    try:
        tokens = shlex.split(launch_cmd.split_prefix(cmd or "")[1])
    except ValueError:
        return ""
    return tokens[0].rsplit("/", 1)[-1] if tokens else ""


def provider_of(cmd: str) -> str:
    """Which CLI a launch command runs, by the first recognisable token.

    Wrappers matter here: `sf-codex` and `sf-opencode-home` are shell scripts
    that launch a CLI, so the name is matched as a prefix rather than exactly.
    pi only by the program itself (`pi`, `sf-pi…`): "pi-" is too common a start
    for a folder or file in the rest of a command."""
    prog = _program(cmd)
    if prog == "pi" or prog.startswith("sf-pi"):
        return "pi"
    try:
        tokens = shlex.split(cmd or "")
    except ValueError:
        tokens = (cmd or "").split()
    for tok in tokens:
        base = tok.rsplit("/", 1)[-1]
        for name in PROVIDERS:
            if name == "pi":
                continue
            if base == name or base.startswith(f"sf-{name}") or base.startswith(f"{name}-"):
                return name
    return ""


# ── pi: the machine's own model list ──────────────────────────────────────
LAUNCHER_PEEK = 64 * 1024
_PI_CACHE = {"key": None, "models": []}
_PROVIDER_FLAG = re.compile(r"--provider(?:=|\s+)[\"']?([A-Za-z0-9][\w.\-]*)")


def _pi_dir() -> str:
    return os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")


def pi_models() -> list:
    """[(provider, id)] that pi's models.json defines, in its order; [] when there is none.
    Only provider names and ids are taken: the file also holds each provider's key, and nothing
    else from it is kept, shown or logged. Read again only when the file changes."""
    path = os.path.join(_pi_dir(), "models.json")
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return []
    if _PI_CACHE["key"] == key:
        return _PI_CACHE["models"]
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            providers = (json.load(f) or {}).get("providers") or {}
        for prov, spec in providers.items() if isinstance(providers, dict) else ():
            for m in (spec.get("models") or []) if isinstance(spec, dict) else ():
                mid = m.get("id") if isinstance(m, dict) else None
                if isinstance(mid, str) and len(mid) <= MODEL_MAX and _SHAPE.fullmatch(mid) and (str(prov), mid) not in out:
                    out.append((str(prov), mid))
    except (OSError, ValueError, AttributeError):
        out = []
    _PI_CACHE.update(key=key, models=out)
    return out


def launcher_head(exe: str):
    """The first bytes of a launcher script found on PATH (or at its path), or None when it cannot
    be read. Read for the flags it gives its CLI; never shown."""
    path = shutil.which(exe) or (exe if os.path.isfile(os.path.expanduser(exe)) else "")
    if not path:
        return None
    try:
        with open(os.path.expanduser(path), "rb") as f:
            return f.read(LAUNCHER_PEEK)
    except OSError:
        return None


def pi_provider(cmd: str) -> str:
    """The provider a pi command starts on: its own `--provider`, else its launcher's; "" when
    neither says (pi's default)."""
    try:
        tokens = shlex.split(launch_cmd.split_prefix(cmd or "")[1])
    except ValueError:
        return ""
    for i, t in enumerate(tokens):
        if t.startswith("--provider="):
            return t.split("=", 1)[1]
        if t == "--provider" and i + 1 < len(tokens):
            return tokens[i + 1]
    if not tokens or tokens[0].rsplit("/", 1)[-1] == "pi":
        return ""
    head = launcher_head(tokens[0])
    if not head or b"\0" in head:
        return ""
    m = _PROVIDER_FLAG.search(head.decode("utf-8", "replace"))
    return m.group(1) if m else ""


def _pi_ids(cmd: str) -> list:
    """The ids a pi command may be pinned to: its provider's, or every one when it names none."""
    prov = pi_provider(cmd)
    return [i for p, i in pi_models() if not prov or p == prov]


def flag_for(cmd: str) -> str:
    return (PROVIDERS.get(provider_of(cmd)) or ("", []))[0]


def current_model(cmd: str) -> str:
    """The model already pinned in a command, if any."""
    flag = flag_for(cmd)
    if not flag:
        return ""
    m = re.search(re.escape(flag) + r"[= ]+(\S+)", cmd or "")
    return m.group(1).strip("'\"") if m else ""


def apply(cmd: str, model: str) -> str:
    """Return *cmd* with *model* pinned; unchanged when the provider is unknown.

    Replaces an existing flag rather than appending a second one — two `--model`
    flags is a launch error on some CLIs and silently ambiguous on others."""
    cmd = (cmd or "").strip()
    model = ("" if model is None else str(model)).strip()
    flag = flag_for(cmd)
    if not flag:
        return cmd
    stripped = re.sub(re.escape(flag) + r"[= ]+\S+\s*", "", cmd).strip()
    if not model:
        return stripped
    return f"{stripped} {flag} {shlex.quote(model)}".strip()


def describe(cmd: str) -> str:
    """Short label for the UI: which CLI, on which model."""
    provider = provider_of(cmd) or "?"
    model = current_model(cmd)
    return f"{provider}／{model}" if model else provider


# ── the picker's list ─────────────────────────────────────────────────────
CUSTOM = "__custom__"            # the last item of a picker's list: not a model, "type one yourself"


def label_of(provider: str, key: str) -> str:
    """The name a person reads for a model key; the key itself when the table has no name for it."""
    return next((lab for k, lab in (_TABLE.get(provider) or {}).get("models", []) if k == key), "") or key


def choices(provider: str, zh: bool = False) -> list:
    """What a model picker for *provider* lists, as [{"key", "label"}]: "CLI default" first (key
    ""), then the table's models, and last "Other (custom)" (key CUSTOM, "custom": true: a text
    box, checked by `check`). A CLI that takes no model pin on its command line (Antigravity, a
    command ShellBot does not know) has only "CLI default": grey the field out. pi lists the models
    its models.json defines on this computer ("id · provider")."""
    out = [{"key": "", "label": "CLI 預設" if zh else "CLI default"}]
    spec = _TABLE.get(provider)
    if not spec:
        return out
    if provider == "pi":
        seen = set()
        for prov, mid in pi_models():
            if mid not in seen:
                seen.add(mid)
                out.append({"key": mid, "label": f"{mid} · {prov}"})
    out += [{"key": k, "label": lab} for k, lab in spec["models"]]
    out.append({"key": CUSTOM, "label": "其他（自訂）" if zh else "Other (custom)", "custom": True})
    return out


# ── the check ─────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=None)
def _shapes(provider: str):
    spec = _TABLE[provider]
    fams = "|".join(re.escape(k) for k, _ in spec["models"])
    suffix = spec.get("suffix", "")
    keys = [re.escape(k) for k in [k for k, _ in spec["models"]] + list(spec.get("also", ()))]
    pats = ([f"(?:{'|'.join(keys)}){suffix}"] if keys else []) + [p.replace("<keys>", fams) + suffix for p in spec["ids"]]
    return [re.compile(p) for p in pats]


def _kind(provider: str, model: str) -> str:
    """"listed" (in the picker), "known" (a key or id shape the CLI takes), or ""."""
    if any(k == model for k, _ in _TABLE[provider]["models"]):
        return "listed"
    return "known" if any(p.fullmatch(model) for p in _shapes(provider)) else ""


def suggest(provider: str, model: str) -> str:
    """The listed key a mistyped one most likely meant ("" when none is close)."""
    spec = _TABLE.get(provider) or {}
    keys = [k for k, _ in spec.get("models", [])] + list(spec.get("also", ()))
    low = {k.lower(): k for k in keys}
    want = (model or "").strip().lower().replace(" ", "-")
    if want in low:
        return low[want]
    close = difflib.get_close_matches(want, list(low), n=1, cutoff=0.6)
    return low[close[0]] if close else ""


def check(cmd: str, model: str) -> dict:
    """Whether *model* can be pinned on *cmd*, without starting anything:
    {"ok", "code", "kind", "provider", "suggestion"}.
      ok, kind "default"   no model: the CLI's own
      ok, kind "listed"    one of the table's models
      ok, kind "known"     not in the picker, but a key or id shape this CLI takes
      code "model_fixed"   this command takes no model pin (Antigravity, an unknown command)
      pi: a model of the provider the command starts on, as this computer's models.json defines it
          ("listed"); refused otherwise (`keys`: the ids it may take), since pi exits on one it
          cannot place. A provider models.json does not define (pi's built-in ones) takes any id
          shape ("known").
      code "bad_model"     not one short word of letters, digits and . _ : / - [ ] (`suggestion` as below)
      code "unknown_model" a word the table does not know (`suggestion`: the likely one)
    Plain refusals: a model the check does not know is never saved, because the CLI would
    ignore it or refuse to start with it."""
    model = ("" if model is None else str(model)).strip()      # a hand-edited config may hold a number
    provider = provider_of(cmd)
    out = {"ok": True, "code": "", "kind": "default", "provider": provider, "suggestion": ""}
    if not model:
        return out
    if not provider:
        return dict(out, ok=False, code="model_fixed", kind="")
    if len(model) > MODEL_MAX or not _SHAPE.fullmatch(model):
        return dict(out, ok=False, code="bad_model", kind="", suggestion=suggest(provider, model))
    if provider == "pi":
        prov, ids = pi_provider(cmd), _pi_ids(cmd)
        if model in ids:
            return dict(out, kind="listed")
        if prov and prov not in {p for p, _ in pi_models()} and any(p.fullmatch(model) for p in _shapes("pi")):
            return dict(out, kind="known")
        close = difflib.get_close_matches(model, ids, n=1, cutoff=0.6)
        return dict(out, ok=False, code="unknown_model", kind="", suggestion=close[0] if close else "", keys=ids)
    kind = _kind(provider, model)
    if kind:
        return dict(out, kind=kind)
    return dict(out, ok=False, code="unknown_model", kind="", suggestion=suggest(provider, model))


def pinned(cmd: str, model: str) -> dict:
    """What a role's command will run on: the role's `model`, else the one already pinned in its
    command, and whether the check knows it. {"model", "label", "ok", "code", "kind", "provider", "suggestion"}.
    A stored key that fails the check is what the editor flags as "may not take effect"."""
    model = ("" if model is None else str(model)).strip() or current_model(cmd)
    res = check(cmd, model)
    return dict(res, model=model, label=label_of(res["provider"], model) if model else "")
