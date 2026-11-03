#!/usr/bin/env python3
"""Chain helper. Prints the seconds to sleep until the next quarter-hour run, or 'stop' when the trading day is over."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("America/New_York"))
if now.weekday() >= 5:
    print("stop"); raise SystemExit
close = now.replace(hour=16, minute=5, second=0, microsecond=0)
nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=(now.minute // 15 + 1) * 15, seconds=45)
print("stop" if nxt > close else int((nxt - now).total_seconds()))
