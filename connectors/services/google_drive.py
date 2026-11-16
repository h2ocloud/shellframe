"""Google Drive through the Claude account's connector: the shared adapter (_claude_connector.py) with this service's
names. The steps, the words and the FAQ are in google_drive.json."""
from connectors.services._claude_connector import ConnectorAdapter

ADAPTER = ConnectorAdapter("google_drive", tool="Google_Drive", mark="DRIVE-TEST", name=("Google Drive", "Google 雲端硬碟"))
