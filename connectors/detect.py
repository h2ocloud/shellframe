"""What already works on this computer: connections a person set up without ShellBot's wizard, per route.

A route is a protocol (the Claude account's connector, a local MCP server, …). For every route of every
service this looks, read-only, in what the AI programs report about their own MCP servers:

  claude  `claude mcp list`, run with the account folder the Claude tabs use (the caller's `Clis.env`). Each
          server's line ends in what Claude found when it checked it:
            claude.ai <Name>: <url> - ✔ Connected          a Claude account connector
            claude.ai <Name>: <url> - ! Needs authentication
            <name>: <command and arguments> - ✔ Connected  a local server
            <name>: <url> (HTTP) - ✘ Failed to connect — …
            <name>: … - ⏸ Pending approval
          It starts every server to answer (10–20 s), so the caller runs `scan` on a thread of its own and keeps
          the answer; nothing here starts a thread.
  codex   `codex mcp list` (its tables; env values show as *****, and `--json` is not used because it carries
          them): each server's name, command and arguments, whether it is turned on and signed in. Codex does
          not check them: an entry there is only found.

How a route is recognized comes from its description, never from a service's name here:

  claude_connector  route.detect.connector (default: the service's English name): the line "claude.ai <that>",
                    the whole name, case and spacing aside
  local_mcp         ShellBot's own entry, route.mcp.server; or an entry a person made (any name that is not
                    shellbot-…) whose command runs one of route.detect.packages, version left off (default: the
                    route's command when it is not a launcher such as npx, and the packages pinned in its args)

A finding is one of
  checked  its program checked it and it works (✔ Connected)
  found    the entry is there and nothing has checked it (Codex lists without checking; pending approval)
  broken   found, and its check failed (needs authentication, failed to connect, turned off, not signed in)
and a route nothing matches has none. An entry a person made is "adopted": ShellBot uses it as it is and never
changes or removes it (clis.own refuses such a name before any program runs).

What is kept is only: which service and route, the entry's name, the finding, why (a code), and whether it is
ShellBot's own or adopted. A command line, an argument, an environment value or a URL is looked at while matching
and dropped: Codex's JSON carries environment values, and a command line can carry a key.
"""

import re
import time

from . import registry as reg_mod, secrets
from .clis import OWN_PREFIX

LIST_TIMEOUT_S = 90.0              # `claude mcp list` checks every server before it answers
STATES = ("checked", "found", "broken")
RANK = {"checked": 3, "broken": 2, "found": 1}
READS = ("claude", "codex")        # the programs whose list is read; the others are not looked at yet
_LAUNCHERS = {"npx", "npm", "node", "bun", "bunx", "pnpm", "pnpx", "yarn", "uv", "uvx", "pipx", "python", "python3",
              "deno", "docker", "cmd", "sh", "bash", "powershell", "pwsh"}
_PINNED = re.compile(r"^(@[a-z0-9._-]+/[a-z0-9._-]+(@[^/\s]+)?|[a-z0-9._-]+@[0-9][^/\s]*)$")
_CONNECTOR = "claude.ai "


# ── reading what a program says ────────────────────────────────────────────
def _claude_state(tail: str):
    """(finding, why) for the end of one `claude mcp list` line."""
    s = tail.strip().lower()
    if re.search(r"needs\s+auth", s):
        return "broken", "auth"
    if re.search(r"(✘|✗|\bfail|\berror\b|disconnected|not connected)", s):
        return "broken", "failed"
    if "pending" in s or "⏸" in s or "disabled" in s:
        return "found", "pending"
    if re.match(r"^[^a-z0-9]*connected\b", s):          # ✔, ✓, √ or another mark (a console without the glyph)
        return "checked", ""
    return "found", "unknown"


def parse_claude(out: str) -> list:
    """The servers in `claude mcp list`'s answer: [{"name", "kind": connector | local | remote, "label", "words",
    "state", "why"}]. `label` is a connector's name ("<Name>" of "claude.ai <Name>"); `words` the command a local one
    runs (kept in memory while matching only). A line that is not a server's is left out."""
    entries = []
    for line in str(out or "").splitlines():
        line = line.strip()
        i, j = line.find(": "), line.rfind(" - ")
        if i <= 0 or j <= i:
            continue
        name, middle, tail = line[:i].strip(), line[i + 2:j].strip(), line[j + 3:]
        state, why = _claude_state(tail)
        if name.lower().startswith(_CONNECTOR):
            entries.append({"name": name, "kind": "connector", "label": name[len(_CONNECTOR):], "words": [], "state": state, "why": why})
        elif re.match(r"^[a-z]+://", middle, re.I):
            entries.append({"name": name, "kind": "remote", "label": "", "words": [], "state": state, "why": why})
        else:
            entries.append({"name": name, "kind": "local", "label": "", "words": middle.split(), "state": state, "why": why})
    return entries


def _cols(header: str) -> list:
    """[(start, name)] of a table's header: names are separated by two spaces or more (one may have spaces)."""
    return [(m.start(), m.group(0).strip().lower()) for m in re.finditer(r"\S+(?: \S+)*", header)]


def parse_codex(out: str):
    """The servers in `codex mcp list` (its tables, one for local servers and one for remote ones), or None when
    there is no table. The plain listing shows env values as `*****`; `--json` would carry them, so it is not
    used. Each row is cut by its header's columns; only Name, Command and Args (a local server), Status and Auth
    are kept, the Env column is never read."""
    entries, cols, seen = [], None, False
    for line in str(out or "").splitlines():
        if re.match(r"^\s*name\s{2,}", line, re.I):
            cols, seen = _cols(line), True
            continue
        if not cols or not line.strip():
            continue
        cell = {}
        for i, (start, head) in enumerate(cols):
            stop = cols[i + 1][0] if i + 1 < len(cols) else None
            cell[head] = line[start:stop].strip() if len(line) > start else ""
        name = cell.get("name", "")
        if not name:
            continue
        local = "command" in cell
        words = ([cell.get("command", "")] + cell.get("args", "").split()) if local else []
        status, auth = cell.get("status", "").lower(), cell.get("auth", "").lower()
        if status == "disabled":
            state, why = "broken", "off"
        elif "not logged in" in auth:
            state, why = "broken", "auth"
        else:
            state, why = "found", ""
        entries.append({"name": name, "kind": "local" if local else "remote", "label": "", "words": [w for w in words if w],
                        "state": state, "why": why})
    if not seen and re.search(r"no mcp servers configured", str(out or ""), re.I):
        return []                                   # its words for an empty list
    return entries if seen else None


PARSERS = {"claude": (["mcp", "list"], parse_claude), "codex": (["mcp", "list"], parse_codex)}


# ── what a route is recognized by (its description) ────────────────────────
def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def _bare(word: str) -> str:
    """A package as it is named, its version left off: @scope/name@1.2 -> @scope/name, name@1 -> name."""
    w = str(word or "").strip().strip("'\"").lower()
    if w.startswith("@"):
        parts = w.split("@")
        return "@" + parts[1] if len(parts) > 2 else w
    return w.split("@", 1)[0]


def connector_name(desc: dict, route: dict) -> str:
    """The name Claude lists the connector under, after "claude.ai ": route.detect.connector, else the service's
    English name."""
    named = ((route or {}).get("detect") or {}).get("connector")
    return str(named or reg_mod.pick((desc or {}).get("name") or {}, "en"))


def packages(route: dict) -> list:
    """The packages or programs an entry that serves this route runs: route.detect.packages, else the route's
    command when it is not a launcher, and the packages its args pin (`@scope/name`, `name@version`)."""
    named = ((route or {}).get("detect") or {}).get("packages")
    if named:
        return sorted({_bare(p) for p in named if isinstance(p, str) and p.strip()})
    mcp = (route or {}).get("mcp") or {}
    out = set()
    cmd = _bare(str(mcp.get("command") or "").replace("\\", "/").split("/")[-1])
    if cmd and re.sub(r"\.(exe|cmd)$", "", cmd) not in _LAUNCHERS:
        out.add(cmd)
    for a in mcp.get("args") or []:
        if isinstance(a, str) and _PINNED.match(a.strip().lower()):
            out.add(_bare(a))
    return sorted(out)


def _runs(words, wanted) -> bool:
    """Does a command run one of `wanted`: a whole word (version left off), or a path through its folder."""
    if not wanted:
        return False
    for w in words or []:
        if _bare(w) in wanted:
            return True
        path = "/" + str(w).lower().replace("\\", "/").strip("/") + "/"
        if any("/" + p + "/" in path for p in wanted):
            return True
    return False


def _shown(name: str) -> str:
    """An entry's name as it may be kept and shown: one line, masked."""
    return secrets.mask(reg_mod.clean_line(name, 60))


def match(entries, cli: str, registry) -> list:
    """The findings in one program's servers: [{"service", "route", "entry", "state", "why", "own", "adopted"}].
    A route is looked for only in a program its description says it reaches (`via` connector or mcp)."""
    hits = []
    for sid in registry.ids():
        desc = registry.get(sid) or {}
        for route in desc.get("routes") or []:
            via = (((route.get("clis") or {}).get(cli)) or {}).get("via")
            if route.get("kind") == "claude_connector" and via == "connector":
                want = _norm(connector_name(desc, route))
                for e in entries:
                    if e["kind"] == "connector" and want and _norm(e["label"]) == want:
                        hits.append({"service": sid, "route": route["id"], "entry": "", "state": e["state"], "why": e["why"],
                                     "own": False, "adopted": False})
            elif via == "mcp" and route.get("mcp"):
                server, wanted = str(route["mcp"].get("server") or ""), set(packages(route))
                for e in entries:
                    own = e["name"] == server
                    adopted = (not own and e["kind"] == "local" and not e["name"].lower().startswith(OWN_PREFIX)
                               and _runs(e["words"], wanted))
                    if own or adopted:
                        hits.append({"service": sid, "route": route["id"], "entry": _shown(e["name"]), "state": e["state"],
                                     "why": e["why"], "own": own, "adopted": adopted})
    return hits[:400]


# ── one look ───────────────────────────────────────────────────────────────
def scan(clis, registry, prev=None, now=time.time) -> dict:
    """Ask each program that can be read for its servers, once, and match them: {"at", "clis": {cli: {"read":
    ok | missing | unknown, "at", "hits": […]}}}. Synchronous (seconds): the caller decides on which thread. A
    program that did not answer (timeout, an error, output that cannot be read) keeps what the last look found:
    not knowing is not the same as gone."""
    at = int(now())
    out = {"at": at, "clis": {}}
    for cli in READS:
        args, parse = PARSERS[cli]
        before = ((prev or {}).get("clis") or {}).get(cli)
        try:
            binary = clis.finder(cli)
        except Exception:
            binary = None
        if not binary:
            out["clis"][cli] = {"read": "missing", "at": at, "hits": []}
            continue
        entries = None
        try:
            env = clis.env(cli) if callable(clis.env) else clis.env
            r = clis.runner([binary, *args], LIST_TIMEOUT_S, env)
            if r.rc == 0:
                entries = parse(r.out)
        except Exception:
            entries = None
        if entries is None:
            out["clis"][cli] = dict(before, read="unknown") if before else {"read": "unknown", "at": 0, "hits": []}
            continue
        out["clis"][cli] = {"read": "ok", "at": at, "hits": match(entries, cli, registry)}
    return out


def findings(snap, service: str) -> dict:
    """What the last look found for one service, per route: {route: {"state", "own", "adopted", "cli", "entry",
    "why", "places": [{cli, entry, state, why, own, adopted}]}}. A route's state is its best place's (checked, then
    broken, then found); on a tie a ShellBot entry or a connector comes before an adopted one."""
    out = {}
    for cli, part in sorted(((snap or {}).get("clis") or {}).items()):
        for h in (part or {}).get("hits") or []:
            if h.get("service") != service or h.get("state") not in RANK:
                continue
            f = out.setdefault(h["route"], {"places": []})
            f["places"].append({"cli": cli, "entry": h.get("entry", ""), "state": h["state"], "why": h.get("why", ""),
                                "own": bool(h.get("own")), "adopted": bool(h.get("adopted"))})
    for f in out.values():
        best(f)
    return out


def best(f: dict) -> dict:
    """Put a route's best place on the route itself (its places may have changed)."""
    top = max(f["places"], key=lambda p: (RANK[p["state"]], not p["adopted"])) if f.get("places") else None
    f.update(state=top["state"] if top else "none", own=bool(top and top["own"]), adopted=bool(top and top["adopted"]),
             cli=top["cli"] if top else "", entry=top["entry"] if top else "", why=top["why"] if top else "")
    return f
