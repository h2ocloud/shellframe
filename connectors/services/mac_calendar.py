"""Calendar on this Mac through the local apple-events server: the shared adapter (_apple_events.py) with this
service's names. The steps, the words and the FAQ are in mac_calendar.json."""
from connectors.services._apple_events import AppleEventsAdapter

ADAPTER = AppleEventsAdapter("mac_calendar", domain="calendar", mark="CALENDAR-TEST", name=("Calendar", "行事曆"),
                             lookup=("list your calendars", "列出行事曆"))
