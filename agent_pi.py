"""Pi (pi-coding-agent): its session JSONL as conversation turns.

The session file is the one agent_status already finds for a pi tab's model badge
(`_pi_session_file`: the newest file in the tab's cwd slug, created after its process
started). Records are `{"type": "message", "message": {"role", "content", "timestamp"}}`:
a user's text, the assistant's text and toolCall blocks in order, and toolResult
messages. Thinking blocks are left out, as they are for Claude. The turns have the
shape every other CLI's do (agent_status): what sfctl `conversation` returns, so the
phone's chat view and the desktop's read the same thing.
"""

import json
import os
import shlex
import shutil

import agent_status
import launch_cmd

TAIL_BYTES = 4 * 1024 * 1024
LAUNCHER_PEEK = 64 * 1024                           # how much of a launcher script is read for --api-key


def _ts(record, message):
    v = message.get("timestamp")
    if isinstance(v, (int, float)) and v:
        return v / 1000.0 if v > 1e11 else float(v)
    return agent_status._parse_iso(record.get("timestamp"))


def norm(record):
    """One session record → a list of turns (possibly empty)."""
    if not isinstance(record, dict) or record.get("type") != "message":
        return []
    m = record.get("message") if isinstance(record.get("message"), dict) else {}
    role, ts = m.get("role"), _ts(record, m)
    content = m.get("content")
    if role == "user":
        text = agent_status._content_text(content)
        return [{"kind": "user_msg", "ts": ts, "text": text}] if text else []
    if role != "assistant":
        return []                                   # toolResult: the call already shows
    if m.get("errorMessage"):                       # the model call failed; pi retries and writes each
        return [{"kind": "error", "ts": ts, "text": agent_status.redact(m["errorMessage"], 160)}]
    out = []
    for c in content if isinstance(content, list) else []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text" and (c.get("text") or "").strip():
            out.append({"kind": "assistant_text", "ts": ts, "text": c["text"].strip()})
        elif c.get("type") == "toolCall":
            name = str(c.get("name") or "tool")
            out.append({"kind": "tool_call", "ts": ts, "tool": name, "text": "",
                        "target": agent_status._target(name, c.get("arguments"))})
    return out


def turns(worker, limit=120):
    """The last `limit` turns of the tab's pi session, oldest first; [] when there is none."""
    path = agent_status._pi_session_file(worker)
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]                           # the first line was cut by the window
    out = []
    for line in lines:
        try:
            out.extend(norm(json.loads(line)))
        except (ValueError, TypeError):
            continue
    return out[-max(1, int(limit)):]


# ── a relaunched tab keeps its model ────────────────────────────────────────────
# pi writes a `model_change` when a conversation starts (the model it started on) and one each time
# `/model` switches; taking a conversation back up with --session writes none, and a `--model` on the
# command line beats the one the conversation was on. A launcher that pins a model (exec pi --provider
# x --model y "$@") so put every relaunched conversation back on its default. The last model_change is
# the model the conversation was last put on; the first, the one its launcher gave it.

def session_models(path):
    """(first, last) `model_change` of a pi session file, each {"provider", "model"}; (None, None)
    when it has none or cannot be read."""
    first = last = None
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                if '"model_change"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(o, dict) or o.get("type") != "model_change" or not o.get("modelId"):
                    continue
                last = {"provider": str(o.get("provider") or ""), "model": str(o["modelId"])}
                first = first or last
    except (OSError, TypeError):
        return None, None
    return first, last


def _flag(tokens, name):
    return any(t == name or t.startswith(name + "=") for t in tokens)


_SHELLS = {"sh", "bash", "zsh", "fish", "dash"}
_PIN_FLAGS = {"--model", "--provider"}


def _without_pins(tokens):
    """*tokens* less any --model/--provider (and their values): a pin added on an earlier relaunch
    must not outlive a later /model."""
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t.split("=", 1)[0] in _PIN_FLAGS:
            i += 1 if "=" in t else 2
            continue
        out.append(t)
        i += 1
    return out


def _launcher_passes_key(exe):
    """Whether the launcher script *exe* gives pi a key on its command line. A launcher that cannot
    be read, or is not a text script, counts as one that does: the safe side."""
    path = shutil.which(exe) or (exe if os.path.isfile(os.path.expanduser(exe)) else "")
    if not path:
        return True
    try:
        with open(os.path.expanduser(path), "rb") as f:
            data = f.read(LAUNCHER_PEEK)
    except OSError:
        return True
    return b"\0" in data or b"--api-key" in data


def keep_model(cmd, path):
    """(cmd, note): *cmd* — already started on its session file at *path* — put on the model the
    conversation was last on.

    - plain `pi` that pins no model: unchanged, pi puts a conversation back on its model itself
    - a launcher, or a command that pins one: earlier --model/--provider go, and
      - same provider as at the start: `--model <id>` (pi takes the last one given), and
        `--provider <p>` again when the command had one
      - another provider: `--provider <p> --model <id>`, unless the command or its launcher passes
        `--api-key`: pi ties that key to the provider the command line ends on, so the launcher's
        key would go to the other provider. Left as it is, with a note saying why.
    - never switched and nothing pinned on the command: unchanged
    - a shell wrapping it (`zsh -lc '…'`): unchanged, words after the script never reach pi
    """
    first, last = session_models(path) if path else (None, None)
    if not last:
        return cmd, ""
    prefix, rest = launch_cmd.split_prefix(cmd or "")
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return cmd, ""
    if not tokens or os.path.basename(tokens[0]) in _SHELLS:
        return cmd, ""
    plain = os.path.basename(tokens[0]) == "pi"
    pinned = _flag(tokens, "--model") or _flag(tokens, "--provider")
    if plain and not pinned:
        return cmd, ""
    if last == first and not pinned:
        return cmd, ""
    if not last["provider"] or last["provider"] == first["provider"]:
        had = _flag(tokens, "--provider") and last["provider"]
        extra = (["--provider", last["provider"]] if had else []) + ["--model", last["model"]]
    elif _flag(tokens, "--api-key") or (not plain and _launcher_passes_key(tokens[0])):
        return cmd, (f"stays on its launcher's model: last on {last['provider']}/{last['model']}, "
                     "and the launcher's --api-key would go to that provider")
    else:
        extra = ["--provider", last["provider"], "--model", last["model"]]
    new = shlex.join(_without_pins(tokens) + extra)
    note = "keeps " + "/".join(x for x in (last["provider"], last["model"]) if x)
    return (f"{prefix} {new}" if prefix else new), note
