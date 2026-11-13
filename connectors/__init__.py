"""The connector core: one way to connect a service (a mailbox, a file store, a chat) to the AI tabs.

A service is a JSON description (services/<id>.json) plus, when it needs code, one small adapter
(services/<id>.py). Everything else is here and knows no service by name:

  registry   read and check the descriptions
  problems   plain-language errors, each with a next step
  secrets    where a key or token lives (the system's key store) and how text is masked
  clis       add, remove and look up an MCP server in each AI CLI, only ShellBot's own `shellbot-*`
  adapter    what an adapter implements (checks, actions, a test, a health check)
  engine     the steps: state kept in state_dir/connectors.json, resumable after a restart
  health     the one status every surface shows (settings, sidebar, sfctl, phone, Telegram)

Rules (docs/connectors.md): nothing secret in config.json, a log, a status or the screen; a test says
"it works" or which step did not, and never touches the content; no service name in this package's
core files (tests_connectors_hygiene.py).

Leaf package: stdlib plus sf_config / sf_lang / sf_log, no `import main`.
"""

SCHEMA = 1
