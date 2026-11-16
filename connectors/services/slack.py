"""Slack through the Claude account's connector: the shared adapter (_claude_connector.py) with this service's
names. The steps, the words and the FAQ are in slack.json."""
from connectors.services._claude_connector import ConnectorAdapter

ADAPTER = ConnectorAdapter("slack", tool="Slack", mark="SLACK-TEST", name=("Slack", "Slack"))
