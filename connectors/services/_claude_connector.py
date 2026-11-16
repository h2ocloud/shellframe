"""A service reached through the Claude account's connector (set up once on claude.ai): the adapter several
services share. Each such service keeps a two-line `<id>.py` that builds one `ConnectorAdapter` with its own
names; its steps, words and FAQ stay in its `<id>.json`. The file starts with `_`, so the registry never takes
it for a service (it reads only `*.json`).

A connector route has three steps (ids as the descriptions use them):

  a1  how Claude is signed in (check `claude_account`: the plan ShellBot keeps). When it can tell, the step is
      done by itself and the person never sees it; only when it cannot does the card ask
  a2  connect the service on claude.ai (`in_browser`: the page opens the service's own page on claude.ai at once,
      and when the person comes back to ShellBot the step counts as done and the test runs)
  a3  the connection test (_tab_test.py)

A connector's tool names are `mcp__claude_ai_<Tool>__<what>`; `authenticate` and `complete_authentication` mean
the connector is there but not authorized yet. Whether it is still connected lives on claude.ai and cannot be
asked without a Claude tab, so `health()` answers `unknown` and the hourly check changes nothing.
"""

from connectors.adapter import claude_login
from connectors.services._tab_test import READY_TIMEOUT_S, TEST_TIMEOUT_S, TabTestAdapter  # noqa: F401 (the tests read them here)

FAILED_STEP = {"needs_auth": "a2", "no_tools": "a2", "not_ready": "a1"}     # the step a verdict sends the person back to


def check_claude_account(ctx, args: dict) -> dict:
    """How Claude is signed in, from the account label ShellBot keeps (the core's claude_login): the plan only."""
    return claude_login(ctx.cfg)


class ConnectorAdapter(TabTestAdapter):
    """One service on the claude.ai connector route. `tool` is the connector's part of the tool names
    (`mcp__claude_ai_<tool>__…`), `mark` the word of the test's last line, `name` the service's (en, zh) name."""

    def __init__(self, service_id: str, tool: str, mark: str, name, route: str = "connector"):
        prefix = f"mcp__claude_ai_{tool}__"
        super().__init__(service_id, prefix, mark, name, route, FAILED_STEP,
                         auth_tools=(prefix + "authenticate", prefix + "complete_authentication"))
        self.checks = {"claude_account": check_claude_account}
