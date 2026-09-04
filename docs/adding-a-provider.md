# Adding support for another AI CLI

ShellFrame wraps AI coding CLIs. Beyond running one in a tab — which needs no
code at all — a *provider* is a CLI that ShellFrame also understands well
enough to report its quota, pace its usage, and treat its tabs as AI tabs.

Supported today: `claude` (Claude Code), `codex`, `agy` (Antigravity CLI),
`pi` (Pi coding agent), `opencode`. The last two report no usage figure —
they run whatever model the user pointed them at, so there is no single
budget to read; see the note on saying why in *Errors* below.

Adding another one is **one registry entry plus two adapter functions** in
`usage_probe.py`. Nothing in `main.py` or `web/index.html` hard-codes provider
names, so you should not need to touch them.

## What you get for free

Once your entry is in the registry:

| Surface | Behaviour |
|---|---|
| Provider detection | `detect_ai()` matches your CLI's command names, including absolute paths and `.exe` |
| Top-bar usage pill | `5h` / `wk` segments, colour thresholds, stale marking, click-to-panel |
| Token pacing | The `pc` segment — as long as you report the window's reset instant and length |
| `/usage` slash command | The in-tab water-level text block |
| AI-tab semantics | Init-prompt injection, mic/STT tagging and other AI-only affordances (`AI_CLI_TOOLS` is derived from the registry) |
| `+` menu preset | Add one line to `_DEFAULT_AI_PRESETS` in `main.py`; existing installs pick it up on next launch |
| Front-end mapping | `web/index.html` reads the registry through `pywebview.api.ai_providers()` |

## The minimum

```python
# usage_probe.py

PROVIDER_SPECS = {
    ...
    "mycli": {"label": "My CLI", "binaries": ("mycli", "my-cli-wrapper")},
}
```

`binaries` are matched against a session command's base name (last path
component, extension stripped). Include any launcher wrapper you ship.

Then two adapters, and register them next to the existing ones:

```python
def _probe_mycli(env):
    """Return the raw reading (see the contract below), or None."""
    return _fetch_mycli()

def _account_mycli(data, env):
    """Human label for the signed-in account, or ''."""
    return _mycli_account()

PROVIDER_SPECS["mycli"].update(probe=_probe_mycli, account=_account_mycli)
```

`env` carries per-account credential overrides when the provider supports
profiles (see *Accounts panel* below); it is `{}` or `None` otherwise.

## The data contract

`probe(env)` returns a plain dict. Percentages are **utilisation** — how much
has been *used*, not how much remains. Invert if your CLI reports the opposite.

```python
{
  "5hr":  (7, "08-20 15:00"),      # (used %, formatted reset time) — optional
  "week": (30, "08-25 04:00"),     # optional; report the windows you have
  "_reset_epoch":    {"week": 1787601600},   # set via _pace_meta(), see below
  "_window_minutes": {"week": 10080},        # ditto
  "_plan":   "team",               # optional, for the account label
  "_ts":     1787600000,           # optional: when a local snapshot was taken
  "_stale":  True,                 # optional: last good reading, refresh failed
  "_groups": [...],                # optional, see Multiple budgets
}
```

Return `None` when there is genuinely nothing (the UI shows "no data"), or an
error marker (below). `_shape()` converts this into what the UI consumes; you
do not build the UI shape yourself.

### Pacing metadata

Pacing answers "should I have used this much by now?", so it needs the window's
reset instant and its length. Record both with the helper rather than by hand:

```python
_pace_meta(out, "week", reset_epoch, window_minutes)   # minutes may be None
```

Use your CLI's own reported window length when it has one — do not assume a
`primary` bucket means five hours. If you omit this metadata the reading still
displays; it simply gets no pace line. Never fabricate a reset time to make the
pace segment appear.

### Errors: say why, never estimate

A user who can't see a number needs to know whether to log in, wait, or ignore
it. Return a marker instead of `None` whenever you know the reason:

```python
{"_error": "auth_required", "_error_message": "登入已過期，請重新登入"}
```

Conventional keys: `auth_required`, `rate_limited`, `no_data`, `not_installed`,
`probe_failed`, plus anything specific you need. Guessing a percentage from
partial data is worse than showing the reason.

### Multiple budgets

Some CLIs meter several model families separately (`agy` has one weekly bucket
for Gemini models and another for Claude/GPT models). Report the primary one as
`5hr`/`week`, and list them all in `_groups`:

```python
out["_groups"] = [{"name": "Gemini Models", "used": 0, "reset": "08-27 16:49",
                   "epoch": 1787820556, "window": "weekly", "key": "week"}]
```

The pill follows the primary bucket; the tooltip and accounts panel show every
group.

## Caching is your job

The pill polls, several callers share a probe, and each surface may refresh on
activity. Budget accordingly:

- **Network quota APIs rate-limit hard.** Cache per account and back off between
  live attempts; serve the last good reading flagged `_stale` rather than
  failing. `_fetch_claude` is the reference implementation.
- **Spawning a CLI is not free** — `_fetch_agy` shells out to a large binary, so
  it caches for `AGY_OK_TTL` and refuses to retry within `AGY_RETRY_MIN`.
- Persist through `_write_cache_file()`, which does a read-modify-write of
  `~/.config/shellframe/usage_cache.json`. Never rewrite that file wholesale:
  other providers keep their sections in it.
- Cached entries outlive upgrades. Adding a field is fine; **changing the shape
  of an existing one is not** — an old entry must still load, just with less
  detail. This is why pacing metadata lives in side-channel dicts instead of
  widening the `(pct, reset)` tuples.

## Optional extras

These are separate systems; a provider works without them.

**Accounts panel** (`account_manager.py`) — per-account profiles, switching, and
one water-level row per account. Requires that the CLI's credentials can be
redirected per process (e.g. a config-dir or token environment variable).
`account_manager.PROVIDERS` is deliberately a *subset* of the usage registry: a
CLI that only supports one signed-in account still reports quota fine.

**Status detection and model badge** (`agent_status.py`) — busy/idle dots and
the per-tab model label. This is provider-specific: Claude and Codex are read
from transcript files, so a CLI storing state elsewhere (a SQLite index, a
protobuf blob, a log file) needs its own reader. Skipping this only costs you
the dot and the badge.

**Startup-trust auto-accept** (`STARTUP_TRUST_AI_TOOLS` in `main.py`) — add your
CLI only if you have verified what its first-run prompt looks like.

## Checklist

1. `PROVIDER_SPECS` entry with `label` + `binaries`.
2. `probe` / `account` adapters registered.
3. A fetcher that caches, and reports reasons instead of guessing.
4. `_pace_meta()` wherever you know a window's reset and length.
5. Optional: `_DEFAULT_AI_PRESETS` entry in `main.py` for the `+` menu.
6. Tests in `tests_usage_probe.py` — mock the network or subprocess, never call
   a real API. Cover: a good reading, an auth failure, and a cached entry
   written by an older build still loading.

Then verify end to end:

```bash
.venv/bin/python tests_usage_probe.py
.venv/bin/python -c "import usage_probe as U; print(U.probe_data('mycli'))"
```

UI changes are verified with Playwright against the real `web/index.html` —
see `docs/` for the existing approach — because a provider that reports numbers
nobody can read is not done.
