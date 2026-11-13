"""The services the core knows: each one a JSON description, read and checked here.

A description (`services/<id>.json`, schema 1) says what a service is called, which ways there are to
connect it (routes: the Claude account's connector, a local MCP server, an API key, OAuth), which
permissions each way asks for (the least), how each AI program gets it, and the steps and common
problems the wizard shows. Everything a person reads is `{"en": …, "zh": …}`.
Anything that needs code (a check, a test) is a small adapter beside the description; the core calls
it by name and never looks at which service it is.

`validate()` returns the list of what is wrong; a description is usable only when it is `[]`. The
registry skips a broken description (and says which, in `Registry.errors`) rather than refusing to start.
"""

import importlib
import json
import os
import re
import threading
from pathlib import Path

from . import SCHEMA, secrets
from .clis import CLI_IDS

SERVICE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
SERVER_NAME = re.compile(r"^shellbot-[a-z0-9][a-z0-9_-]{0,40}$")
CONNECT_TAG = re.compile(r"^\s*connect\s*:\s*([a-z][a-z0-9_-]{0,30})\s*$", re.I)    # [[SF:RED:connect:<service>]]
KINDS = ("claude_connector", "local_mcp", "api_key", "oauth")
VIAS = ("connector", "mcp", "none")
TONES = ("good", "warn", "bad", "info")
BUILTINS = ("cli_add", "cli_remove", "cli_check", "secret_set")
CHECK_ON = ("enter", "done")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f  ]")
_MARK = re.compile(r"\[\[|\]\]")
LINE_MAX = 120


def clean_line(text, limit: int = LINE_MAX) -> str:
    """One line for a bot's input: no control characters, no [[ ]] (ShellBot's own markers live in
    those), bounded."""
    s = _MARK.sub("", _CTRL.sub(" ", str(text if text is not None else "")))
    return " ".join(s.split())[:limit]


def pick(t, lang: str = None) -> str:
    """The string of a {"en","zh"} text for `lang` (default: the OS's, as every agent-facing text)."""
    if isinstance(t, str):
        return t
    if not isinstance(t, dict):
        return ""
    if lang is None:
        import sf_lang
        lang = sf_lang.prompt_lang()
    return str(t.get(lang) or t.get("en") or t.get("zh") or "")


def is_text(t) -> bool:
    return isinstance(t, dict) and all(isinstance(t.get(k), str) and t.get(k).strip() for k in ("en", "zh"))


def parse_connect_tag(reason, known=None):
    """The service id in a `[[SF:RED:connect:<service>]]` marker's reason ("connect:<service>"), else None.
    `known` (ids) narrows it to a service that is registered; without it any well-formed id is returned.
    The one parser: the bridge, the phone's view and the page all go through it."""
    m = CONNECT_TAG.match(str(reason or ""))
    if not m:
        return None
    sid = m.group(1).lower()
    return sid if (known is None or sid in known) else None


# ── validation ─────────────────────────────────────────────────────────────
def _check_text(errs, where, t, optional=False):
    if t is None and optional:
        return
    if not is_text(t):
        errs.append(f"{where}: needs both en and zh text")
        return
    for lang in ("en", "zh"):
        s = t[lang]
        if _MARK.search(s) or _CTRL.search(s):
            errs.append(f"{where}.{lang}: no [[ ]] and no control characters")
        if secrets.looks_secret(s):
            errs.append(f"{where}.{lang}: looks like a secret")


def _check_url(errs, where, url):
    if not (isinstance(url, str) and url.startswith("https://") and " " not in url and not secrets.looks_secret(url)):
        errs.append(f"{where}: an https link with nothing private in it")


def _check_states(errs, where, states):
    if not isinstance(states, dict):
        errs.append(f"{where}: an object of state codes")
        return
    for code, s in states.items():
        if not isinstance(s, dict) or s.get("tone") not in TONES:
            errs.append(f"{where}.{code}: tone must be one of {TONES}")
            continue
        _check_text(errs, f"{where}.{code}.text", s.get("text"))
        _check_text(errs, f"{where}.{code}.hint", s.get("hint"), optional=True)


def _validate_step(errs, w, st, route, ids, is_last):
    if not isinstance(st, dict) or not isinstance(st.get("id"), str) or not st["id"]:
        errs.append(f"{w}: needs an id")
        return
    w = f"{w}[{st['id']}]"
    _check_text(errs, f"{w}.title", st.get("title"))
    if st.get("kind") == "test":
        _check_text(errs, f"{w}.intro", st.get("intro"), optional=True)
        if not is_last:
            errs.append(f"{w}: the test step must be the last one")
        return
    if is_last:
        errs.append(f"{w}: a route ends with a step of kind 'test'")
    for i, h in enumerate(st.get("how") or []):
        _check_text(errs, f"{w}.how[{i}]", h)
    _check_text(errs, f"{w}.see", st.get("see"), optional=True)
    _check_text(errs, f"{w}.note", st.get("note"), optional=True)
    if st.get("url") is not None:
        _check_url(errs, f"{w}.url", st["url"])
    chk = st.get("check")
    if chk is not None:
        if not isinstance(chk, dict) or not isinstance(chk.get("id"), str) or not chk["id"]:
            errs.append(f"{w}.check: needs an id")
        else:
            if chk.get("on") not in CHECK_ON:
                errs.append(f"{w}.check.on: one of {CHECK_ON}")
            if not (isinstance(chk.get("pass"), list) and chk["pass"] and all(isinstance(x, str) for x in chk["pass"])):
                errs.append(f"{w}.check.pass: a list of the state codes that mean ok")
            _check_states(errs, f"{w}.check.states", chk.get("states") or {})
    seen = set()
    for a in st.get("actions") or []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"]:
            errs.append(f"{w}.actions: each needs an id")
            continue
        aw = f"{w}.actions[{a['id']}]"
        if a["id"] in seen:
            errs.append(f"{aw}: id used twice")
        seen.add(a["id"])
        _check_text(errs, f"{aw}.label", a.get("label"))
        b = a.get("builtin")
        if b is not None and b not in BUILTINS:
            errs.append(f"{aw}.builtin: one of {BUILTINS}")
        if b in ("cli_add", "cli_remove", "cli_check") and not route.get("mcp"):
            errs.append(f"{aw}: {b} needs the route's mcp")
        if b == "secret_set" and not route.get("secrets"):
            errs.append(f"{aw}: secret_set needs the route's secrets")
        if a.get("after") not in (None, "check"):
            errs.append(f"{aw}.after: 'check' or nothing")
        _check_states(errs, f"{aw}.results", a.get("results") or {})
    oids = set()
    for o in st.get("options") or []:
        if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not o["id"] or o["id"] in oids:
            errs.append(f"{w}.options: each needs its own id")
            continue
        oids.add(o["id"])
        _check_text(errs, f"{w}.options[{o['id']}].label", o.get("label"))
    qids = set()
    for q in st.get("stuck") or []:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not q["id"] or q["id"] in qids:
            errs.append(f"{w}.stuck: each needs its own id")
            continue
        qids.add(q["id"])
        _check_text(errs, f"{w}.stuck[{q['id']}].q", q.get("q"))
        _check_text(errs, f"{w}.stuck[{q['id']}].a", q.get("a"))
        for ln in q.get("links") or []:
            _check_text(errs, f"{w}.stuck[{q['id']}].links.label", (ln or {}).get("label"))
            if (ln or {}).get("url") is not None:
                _check_url(errs, f"{w}.stuck[{q['id']}].links.url", ln["url"])
            elif (ln or {}).get("step") not in ids:
                errs.append(f"{w}.stuck[{q['id']}].links: a url, or a step of this route")
    if "redo_on_reconnect" in st and not isinstance(st["redo_on_reconnect"], bool):
        errs.append(f"{w}.redo_on_reconnect: true or false")


def _validate_mcp(errs, w, mcp, route):
    if not isinstance(mcp, dict):
        errs.append(f"{w}: an object")
        return
    if not SERVER_NAME.match(str(mcp.get("server") or "")):
        errs.append(f"{w}.server: must be shellbot-<name>: ShellBot only touches its own entries")
    if not (isinstance(mcp.get("command"), str) and mcp["command"]):
        errs.append(f"{w}.command: a program name")
    if not all(isinstance(a, str) for a in mcp.get("args") or []):
        errs.append(f"{w}.args: a list of strings")
    if not all(isinstance(a, str) for a in mcp.get("win_command") or []):
        errs.append(f"{w}.win_command: a list of strings")
    env = mcp.get("env") or {}
    if not (isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())):
        errs.append(f"{w}.env: plain text values")
    else:
        for k, v in env.items():
            if secrets.looks_secret(v) or secrets.looks_secret(f"{k}={v}"):
                errs.append(f"{w}.env.{k}: looks like a secret; use env_from_secret")
    sec_ids = {s.get("id") for s in route.get("secrets") or [] if isinstance(s, dict)}
    for k, sid in (mcp.get("env_from_secret") or {}).items():
        if sid not in sec_ids:
            errs.append(f"{w}.env_from_secret.{k}: no secret '{sid}' on this route")


def validate(desc) -> list:
    """What is wrong with a description, one line each; `[]` when it is fine."""
    errs = []
    if not isinstance(desc, dict):
        return ["a description is a JSON object"]
    if desc.get("schema") != SCHEMA:
        errs.append(f"schema: must be {SCHEMA}")
    if not SERVICE_ID.match(str(desc.get("id") or "")):
        errs.append("id: lowercase letters, digits, - and _, starting with a letter")
    _check_text(errs, "name", desc.get("name"))
    _check_text(errs, "summary", desc.get("summary"), optional=True)
    if not isinstance(desc.get("icon"), str) or not desc.get("icon"):
        errs.append("icon: an icon key")
    scope_ids, required = set(), set()
    for s in desc.get("scopes") or []:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"] or s["id"] in scope_ids:
            errs.append("scopes: each needs its own id")
            continue
        scope_ids.add(s["id"])
        _check_text(errs, f"scopes[{s['id']}].label", s.get("label"))
        if s.get("required"):
            required.add(s["id"])
    routes = desc.get("routes")
    if not isinstance(routes, list) or not routes:
        errs.append("routes: at least one")
        routes = []
    rids = set()
    for r in routes:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not r["id"] or r["id"] in rids:
            errs.append("routes: each needs its own id")
            continue
        rids.add(r["id"])
        w = f"routes[{r['id']}]"
        if r.get("kind") not in KINDS:
            errs.append(f"{w}.kind: one of {KINDS}")
        _check_text(errs, f"{w}.name", r.get("name"))
        _check_text(errs, f"{w}.blurb", r.get("blurb"), optional=True)
        for i, n in enumerate(r.get("needs") or []):
            _check_text(errs, f"{w}.needs[{i}]", n)
        rs = r.get("scopes") or []
        bad = [x for x in rs if x not in scope_ids]
        if bad:
            errs.append(f"{w}.scopes: {bad} are not in the service's scopes")
        if required - set(rs):
            errs.append(f"{w}.scopes: must include the required {sorted(required - set(rs))}")
        for cli, spec in (r.get("clis") or {}).items():
            if cli not in CLI_IDS:
                errs.append(f"{w}.clis.{cli}: not an AI program ShellBot supports")
                continue
            via = (spec or {}).get("via")
            if via not in VIAS:
                errs.append(f"{w}.clis.{cli}.via: one of {VIAS}")
            elif via == "connector" and (r.get("kind") != "claude_connector" or cli != "claude"):
                errs.append(f"{w}.clis.{cli}: 'connector' is the Claude account's connector, for claude only")
            elif via == "mcp" and not r.get("mcp"):
                errs.append(f"{w}.clis.{cli}: 'mcp' needs the route's mcp")
            _check_text(errs, f"{w}.clis.{cli}.note", (spec or {}).get("note"), optional=True)
        if r.get("mcp") is not None:
            _validate_mcp(errs, f"{w}.mcp", r["mcp"], r)
        for s in r.get("secrets") or []:
            if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"]:
                errs.append(f"{w}.secrets: each needs an id")
                continue
            _check_text(errs, f"{w}.secrets[{s['id']}].label", s.get("label"))
        steps = r.get("steps")
        if not isinstance(steps, list) or not steps:
            errs.append(f"{w}.steps: at least the test step")
            steps = []
        sids = [st.get("id") for st in steps if isinstance(st, dict)]
        if len(sids) != len(set(sids)):
            errs.append(f"{w}.steps: an id is used twice")
        for i, st in enumerate(steps):
            _validate_step(errs, f"{w}.steps", st, r, set(sids), i == len(steps) - 1)
        for code, v in ((r.get("test") or {}).get("verdicts") or {}).items():
            _check_text(errs, f"{w}.test.verdicts.{code}.text", (v or {}).get("text"))
            _check_text(errs, f"{w}.test.verdicts.{code}.next", (v or {}).get("next"), optional=True)
        _check_text(errs, f"{w}.test.via", (r.get("test") or {}).get("via"), optional=True)
    for q in desc.get("faq") or []:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str):
            errs.append("faq: each needs an id")
            continue
        _check_text(errs, f"faq[{q['id']}].q", q.get("q"))
        _check_text(errs, f"faq[{q['id']}].a", q.get("a"))
    for code, v in (desc.get("problems") or {}).items():
        _check_text(errs, f"problems.{code}.text", (v or {}).get("text"))
        _check_text(errs, f"problems.{code}.next", (v or {}).get("next"), optional=True)
    return errs


def validate_adapter(desc: dict, adapter) -> list:
    """What a description asks of its adapter that the adapter does not have: every check id and every
    action that is not a built-in. A service's own test runs this on its description and its adapter."""
    errs = []
    checks, actions = getattr(adapter, "checks", {}) or {}, getattr(adapter, "actions", {}) or {}
    for r in desc.get("routes") or []:
        for st in r.get("steps") or []:
            chk = st.get("check")
            if chk and chk.get("id") not in checks:
                errs.append(f"routes[{r['id']}].steps[{st['id']}].check: the adapter has no check '{chk.get('id')}'")
            for a in st.get("actions") or []:
                if not a.get("builtin") and a.get("id") not in actions:
                    errs.append(f"routes[{r['id']}].steps[{st['id']}].actions: the adapter has no action '{a.get('id')}'")
    return errs


# ── the registry ───────────────────────────────────────────────────────────
def builtin_dir() -> Path:
    return Path(__file__).resolve().parent / "services"


class Registry:
    """Descriptions by id, and their adapters. `dirs` are read once, in order; a later one wins an id."""

    def __init__(self, dirs=None):
        self._lock = threading.RLock()
        self._desc = {}
        self._adapters = {}
        self.errors = {}               # id or file name -> what is wrong with it (the service is skipped)
        for d in (dirs if dirs is not None else [builtin_dir()]):
            self.load_dir(d)

    def load_dir(self, folder) -> None:
        for p in sorted(Path(folder).glob("*.json")):
            try:
                desc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.errors[p.name] = ["not readable JSON"]
                continue
            self.register(desc, source=p.name)

    def register(self, desc: dict, adapter=None, source: str = "") -> bool:
        """Add a description (and, when given, its adapter). False, with `errors`, when it is not valid."""
        errs = validate(desc)
        key = (desc or {}).get("id") or source or "?"
        if errs:
            self.errors[key] = errs
            return False
        with self._lock:
            self._desc[desc["id"]] = desc
            self.errors.pop(key, None)
            if adapter is not None:
                self._adapters[desc["id"]] = adapter
            else:
                self._adapters.pop(desc["id"], None)
        return True

    def ids(self) -> list:
        with self._lock:
            return sorted(self._desc)

    def get(self, service: str):
        with self._lock:
            return self._desc.get(str(service))

    def route(self, service: str, route_id: str = None):
        """The route `route_id` of a service; without an id, the recommended one (else the first)."""
        d = self.get(service)
        if not d:
            return None
        routes = d["routes"]
        if route_id:
            return next((r for r in routes if r["id"] == route_id), None)
        return next((r for r in routes if r.get("recommended")), routes[0])

    def adapter(self, service: str):
        """The service's adapter, or None: an injected one, else the module its description names
        (default `connectors.services.<id>`) and that module's `ADAPTER`. A missing module is None;
        a module that fails inside is reported as None too and noted in `errors`."""
        d = self.get(service)
        if not d:
            return None
        with self._lock:
            if service in self._adapters:
                return self._adapters[service]
        modname = d.get("adapter") or f"connectors.services.{service}"
        try:
            ad = getattr(importlib.import_module(modname), "ADAPTER", None)
        except ModuleNotFoundError as e:
            if e.name and modname.startswith(e.name):      # the adapter module itself is not there
                ad = None
            else:
                self.errors[f"{service}.py"] = ["the adapter could not be loaded"]
                ad = None
        except Exception:
            self.errors[f"{service}.py"] = ["the adapter could not be loaded"]
            ad = None
        with self._lock:
            self._adapters[service] = ad
        return ad


_DEFAULT = None
_DEFAULT_LOCK = threading.Lock()


def default() -> Registry:
    """The registry of the services that ship with ShellBot."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = Registry()
        return _DEFAULT
