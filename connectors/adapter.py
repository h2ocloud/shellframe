"""What one service's adapter implements, and what the core hands it.

An adapter is the small piece of code a service needs and a description cannot hold: a check ("is the
file there?"), an action ("open the sign-in"), a connection test, a health check. The description names
them; the core calls them by name and never looks at which service they belong to.

Every answer is a plain dict that can be turned into JSON: a `state` that is a short code, and whatever
else is safe to show. Never a sentence (the words are in the description), never a command's output,
never a secret. An exception inside an adapter is caught by the engine and shown as `adapter_error`.

    class MyAdapter(Adapter):
        id = "my-service"
        checks = {"tool_ok": check_tool}              # fn(ctx, args) -> {"state": "ok" | "missing" | …}
        actions = {"signin": action_signin}           # fn(ctx, args) -> {"state": "opened" | …}
        def test(self, ctx, route, handle=None): ...  # see below
        def health(self, ctx, route): ...             # {"state": "ok" | "expired" | "revoked" | "unreachable" | "unknown"}

    ADAPTER = MyAdapter()                             # a module-level name, in services/<id>.py

`test()` says "it works" or which step did not, and never looks at the content (a mail's subject, a
file's name): it is only about whether the way through is open.

    {"state": "done", "ok": True}
    {"state": "done", "ok": False, "code": "<a test verdict>", "failed_step": "<step id, or ''>"}
    {"state": "running", "handle": "<opaque>", "phase": "<code>"}        call again with the handle
    {"state": "needs_user", "handle": "<opaque>", "code": "<code>"}      waiting for the person
    ctx.cancel is true: stop and answer {"state": "done", "ok": False, "code": "test_cancelled", "failed_step": ""}
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

HEALTH_STATES = ("ok", "expired", "revoked", "unreachable", "unknown")


@dataclass
class Ctx:
    """What the core gives an adapter for one call."""
    service: str
    route: str = ""
    cfg: dict = field(default_factory=dict)          # a copy of ShellBot's config; an adapter changes nothing in it
    env: dict = field(default_factory=dict)          # the environment for a child program: no key, no login token
    win: bool = False
    lang: str = "en"
    run: Optional[Callable[..., Any]] = None         # run(argv, timeout=40, env=None) -> Result(rc, out): replaceable, tests use a stand-in
    find: Optional[Callable[[str], Any]] = None      # find(binary) -> path | None, as a tab would find it
    open_tab: Optional[Callable[[str, str], str]] = None     # open_tab(cmd, title) -> sid: a tab of ShellBot's own, not a bot
    tabs: Any = None                                 # a temporary AI tab for a connection test: open / ask / phase / read / close (connect_tabs.TabKit)
    open_path: Optional[Callable[[str], None]] = None        # show a folder in Finder / Explorer
    secrets: Any = None                              # bound to this service: get / set / delete / has (name)
    cancel: bool = False
    now: Callable[[], float] = time.time


class Adapter:
    """Base class. A service overrides what it needs; the rest answers "not implemented" in the way the
    engine understands (no health check = nothing to refresh, no test = the route cannot be tested)."""
    id = ""
    checks: dict = {}
    actions: dict = {}

    def test(self, ctx: Ctx, route: str, handle: Optional[str] = None) -> dict:
        return {"state": "done", "ok": False, "code": "no_adapter", "failed_step": ""}

    def health(self, ctx: Ctx, route: str) -> dict:
        return {"state": "unknown"}

    def disconnect(self, ctx: Ctx, route: str) -> dict:
        """The service's own clean-up on disconnect (the core already removes the CLI entries and the
        secrets it stored). Return {"state": "done"}."""
        return {"state": "done"}
