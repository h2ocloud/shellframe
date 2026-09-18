"""
agent_model — pin which model a roster role runs on.

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
"""

import re
import shlex

# provider -> (flag, known model names for the picker). The list is a
# convenience for the UI, never a restriction: the field takes free text so a
# model released tomorrow works today.
PROVIDERS = {
    "claude": ("--model", ["opus", "sonnet", "haiku"]),
    "codex":  ("-m", ["gpt-5-codex", "gpt-5", "o3"]),
    "opencode": ("--model", []),
}


def provider_of(cmd: str) -> str:
    """Which CLI a launch command runs, by the first recognisable token.

    Wrappers matter here: `sf-codex` and `sf-opencode-home` are shell scripts
    that launch a CLI, so the name is matched as a prefix rather than exactly."""
    try:
        tokens = shlex.split(cmd or "")
    except ValueError:
        tokens = (cmd or "").split()
    for tok in tokens:
        base = tok.rsplit("/", 1)[-1]
        for name in PROVIDERS:
            if base == name or base.startswith(f"sf-{name}") or base.startswith(f"{name}-"):
                return name
    return ""


def flag_for(cmd: str) -> str:
    return (PROVIDERS.get(provider_of(cmd)) or ("", []))[0]


def current_model(cmd: str) -> str:
    """The model already pinned in a command, if any."""
    flag = flag_for(cmd)
    if not flag:
        return ""
    m = re.search(re.escape(flag) + r"[= ]+(\S+)", cmd or "")
    return m.group(1) if m else ""


def apply(cmd: str, model: str) -> str:
    """Return *cmd* with *model* pinned; unchanged when the provider is unknown.

    Replaces an existing flag rather than appending a second one — two `--model`
    flags is a launch error on some CLIs and silently ambiguous on others."""
    cmd = (cmd or "").strip()
    model = (model or "").strip()
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
