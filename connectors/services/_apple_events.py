"""Reminders and Calendar on this Mac through one local MCP server: the adapter both services share. Each keeps
a two-line `<id>.py` that builds one `AppleEventsAdapter` with its own names; the steps and words are in its
`<id>.json`. The file starts with `_`, so the registry never takes it for a service.

The server is mcp-server-apple-events (npm, MIT), pinned to one version and started with
`--require-write-confirmation`: every change it would make (a new reminder, a moved event) is put to the person
first through the AI program's confirmation form (MCP elicitation), and a program that cannot show that form can
only read. One server serves both apps, so both services name the same entry, `shellbot-apple-events`; the core
keeps an entry two services share until the last of them is disconnected.

macOS decides who may read Reminders and Calendar, per app that starts the server: inside ShellBot that is
ShellBot. The first read asks; a "no" there, or a question that never comes, is what the test's `failed`
words explain (System Settings → Privacy & Security). This route is marked beta: nobody has yet clicked through
macOS's question on a real Mac with ShellBot.

Like every adapter: no file is opened, nothing secret is touched, and the test judges from the names of the tools
a temporary Claude tab called and its last line, never from what they returned.
"""

import re

from connectors.services._tab_test import TabTestAdapter

SERVER = "shellbot-apple-events"
PACKAGE = "mcp-server-apple-events"
VERSION = "1.6.2"
NODE_MIN = 20                                        # the package's own engines field: node >= 20
FAILED_STEP = {"no_tools": "b2", "not_ready": ""}    # no tools: the server is not in Claude's settings (the add step)


def check_node(ctx, args: dict) -> dict:
    """The version of the node a tab would find. {"state": "ok" | "old" | "missing", "version"}."""
    node = ctx.find("node")
    if not node:
        return {"state": "missing"}
    r = ctx.run([node, "--version"], 10)
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", r.out or "") if r.rc == 0 else None
    if not m:
        return {"state": "missing"}
    return {"state": "ok" if int(m.group(1)) >= NODE_MIN else "old", "version": ".".join(m.groups())}


class AppleEventsAdapter(TabTestAdapter):
    """One of the two apps. `domain` starts its tools' names on the server (`reminders_…`, `calendar_…`), so a
    Reminders test that only touched Calendar tools does not count."""

    def __init__(self, service_id: str, domain: str, mark: str, name, lookup, route: str = "local"):
        super().__init__(service_id, f"mcp__{SERVER}__{domain}_", mark, name, route, FAILED_STEP, lookup=lookup)
        self.checks = {"node": check_node}
