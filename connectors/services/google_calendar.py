"""Google Calendar through the Claude account's connector: the shared adapter (_claude_connector.py) with this service's
names. The steps, the words and the FAQ are in google_calendar.json."""
from connectors.services._claude_connector import ConnectorAdapter

ADAPTER = ConnectorAdapter("google_calendar", tool="Google_Calendar", mark="GCAL-TEST", name=("Google Calendar", "Google 日曆"))
