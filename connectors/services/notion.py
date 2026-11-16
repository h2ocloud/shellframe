"""Notion through the Claude account's connector: the shared adapter (_claude_connector.py) with this service's
names. The steps, the words and the FAQ are in notion.json."""
from connectors.services._claude_connector import ConnectorAdapter

ADAPTER = ConnectorAdapter("notion", tool="Notion", mark="NOTION-TEST", name=("Notion", "Notion"))
