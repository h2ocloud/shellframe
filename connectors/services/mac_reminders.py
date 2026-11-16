"""Reminders on this Mac through the local apple-events server: the shared adapter (_apple_events.py) with this
service's names. The steps, the words and the FAQ are in mac_reminders.json."""
from connectors.services._apple_events import AppleEventsAdapter

ADAPTER = AppleEventsAdapter("mac_reminders", domain="reminders", mark="REMINDERS-TEST", name=("Reminders", "提醒事項"),
                             lookup=("list your reminder lists", "列出提醒事項的清單"))
