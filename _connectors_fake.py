"""A made-up service, for the connector core's tests: one description and one fake adapter, nothing else.

It exists to prove the core is not tied to any service: every path (connect, expire, reconnect, break,
disconnect) runs from this description and this adapter. Also the stand-ins a test passes in: a runner
that records the argument lists and runs no program, a finder, a key store in memory.
"""
import copy

from connectors import secrets
from connectors.adapter import Adapter
from connectors.clis import Result


def T(en, zh=None):
    return {"en": en, "zh": zh or en + " (zh)"}


def states(**kw):
    return {code: {"tone": tone, "text": T(f"state {code}")} for code, tone in kw.items()}


DEMO = {
    "schema": 1, "id": "demo", "name": T("Demo"), "icon": "plug", "summary": T("A made-up service"),
    "scopes": [{"id": "read", "label": T("Read"), "required": True}, {"id": "write", "label": T("Write"), "required": False}],
    "faq": [{"id": "slow", "q": T("It is slow"), "a": T("Wait a minute")}],
    "problems": {"demo_down": {"text": T("The demo service is down"), "next": T("Try again later")}},
    "routes": [
        {"id": "connector", "kind": "claude_connector", "name": T("Account connector"), "recommended": True, "minutes": 3,
         "scopes": ["read"],
         "clis": {"claude": {"via": "connector"}, "codex": {"via": "none", "note": T("Not reachable")}},
         "steps": [
             {"id": "open", "title": T("Open the page"), "how": [T("Open it")], "url": "https://example.com/connect",
              "check": {"id": "account_ok", "on": "enter", "gate": False, "pass": ["ok"], "states": states(missing="warn")}},
             {"id": "authorize", "title": T("Authorize"), "how": [T("Press allow")],
              "check": {"id": "authorized", "on": "done", "gate": True, "pass": ["ok"], "states": states(missing="warn")},
              "stuck": [{"id": "nothing", "q": T("Nothing happens"), "a": T("Reload"), "links": [{"label": T("Back"), "step": "open"}]}]},
             {"id": "test", "kind": "test", "title": T("Test"), "intro": T("Test it")}]},
        {"id": "local", "kind": "local_mcp", "name": T("Local server"), "minutes": 10, "scopes": ["read"],
         "needs": [T("A tool")],
         "clis": {c: {"via": "mcp"} for c in ("claude", "codex", "agy", "grok", "opencode", "cursor", "pi")},
         "mcp": {"server": "shellbot-demo", "command": "demo-mcp", "args": ["--serve"], "win_command": ["cmd", "/c", "demo-mcp", "--serve"],
                 "env": {"DEMO_PROFILE": "shellbot"}},
         "steps": [
             {"id": "tool", "title": T("Check the tool"), "check": {"id": "tool_ok", "on": "enter", "gate": True, "pass": ["ok"], "states": states(missing="bad", old="warn")}},
             {"id": "add", "title": T("Add to the AIs"),
              "actions": [{"id": "add_server", "label": T("Add"), "builtin": "cli_add", "results": states(added="good", replaced="good", failed="bad")}],
              "options": [{"id": "readonly", "label": T("Read only"), "default": True}]},
             {"id": "signin", "title": T("Sign in"), "redo_on_reconnect": True,
              "actions": [{"id": "signin", "label": T("Open sign-in"), "after": "check", "results": states(opened="good")}],
              "check": {"id": "signed_in", "on": "done", "gate": True, "pass": ["ok"], "states": states(missing="warn")}},
             {"id": "test", "kind": "test", "title": T("Test"), "intro": T("Test it")}],
         "test": {"via": T("the demo tool"), "verdicts": {"nothing_back": {"text": T("The demo gave nothing back"), "next": T("Sign in again")}}}},
        {"id": "key", "kind": "api_key", "name": T("API key"), "minutes": 4, "scopes": ["read"],
         "clis": {"claude": {"via": "mcp"}, "codex": {"via": "mcp"}},
         "secrets": [{"id": "api_key", "label": T("API key"), "help": T("From the demo site")}],
         "mcp": {"server": "shellbot-demo-key", "command": "demo-mcp", "args": [], "env_from_secret": {"DEMO_API_KEY": "api_key"}},
         "steps": [
             {"id": "paste", "title": T("Paste the key"),
              "actions": [{"id": "save", "label": T("Save"), "builtin": "secret_set", "results": states(saved="good", empty="bad")}]},
             {"id": "add", "title": T("Add to the AIs"),
              "actions": [{"id": "add_server", "label": T("Add"), "builtin": "cli_add", "results": states(added="good", replaced="good", failed="bad")}]},
             {"id": "test", "kind": "test", "title": T("Test"), "intro": T("Test it")}]},
    ],
}


def demo():
    return copy.deepcopy(DEMO)


class FakeAdapter(Adapter):
    """Its state is a dict the test changes: what each check finds, what a test comes to, what health says."""
    id = "demo"

    def __init__(self):
        self.found = {"account_ok": "ok", "authorized": "ok", "tool_ok": "ok", "signed_in": "ok"}
        self.test_script = [{"state": "done", "ok": True}]       # answers to successive test() calls
        self.health_answer = {"state": "ok"}
        self.calls = []
        self.boom = False
        self.checks = {k: self._check(k) for k in self.found}
        self.actions = {"signin": self._signin}

    def _check(self, name):
        def fn(ctx, args):
            self.calls.append(("check", name))
            if self.boom:
                raise RuntimeError("secret-token-sk-abcdefghijklmnop leaked in an exception")
            return {"state": self.found[name]}
        return fn

    def _signin(self, ctx, args):
        self.calls.append(("action", "signin"))
        self.found["signed_in"] = "ok"
        return {"state": "opened"}

    def test(self, ctx, route, handle=None):
        self.calls.append(("test", route, handle, ctx.cancel))
        if ctx.cancel:
            return {"state": "done", "ok": False, "code": "test_cancelled", "failed_step": ""}
        return self.test_script.pop(0) if len(self.test_script) > 1 else dict(self.test_script[0])

    def health(self, ctx, route):
        self.calls.append(("health", route))
        return dict(self.health_answer)

    def disconnect(self, ctx, route):
        self.calls.append(("disconnect", route))
        return {"state": "done"}


class FakeRunner:
    """Stands in for every AI program: records each argument list, answers from a table, runs nothing.
    `entries[cli]` is the set of server names that program 'has'."""

    def __init__(self):
        self.calls = []
        self.entries = {}
        self.fail_add = set()
        self.timeout_get = set()

    def __call__(self, argv, timeout=40, env=None):
        self.calls.append(list(argv))
        binary = str(argv[0]).rsplit("/", 1)[-1]
        cli = binary
        op = argv[2] if len(argv) > 2 and argv[1] == "mcp" else ""
        have = self.entries.setdefault(cli, set())
        name = next((a for a in argv[3:] if str(a).startswith("shellbot-")), "")
        if op == "add":
            if cli in self.fail_add:
                return Result(1, "error")
            have.add(name)
            return Result(0, "added")
        if op == "remove":
            if name in have:
                have.discard(name)
                return Result(0, "removed")
            return Result(1, "not found")
        if op == "get":
            if cli in self.timeout_get:
                return Result(-2, "")
            return Result(0 if name in have else 1, name if name in have else "not found")
        if op == "list":
            return Result(0, "\n".join(sorted(have)))
        return Result(1, "unknown")


def finder(installed=("claude", "codex", "agy", "grok", "opencode", "cursor", "pi")):
    return lambda cli: f"/fake/bin/{cli}" if cli in installed else None


def backend():
    return secrets.MemoryBackend()
