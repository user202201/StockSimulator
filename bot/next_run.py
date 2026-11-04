#!/usr/bin/env python3
"""Scheduler for the self-sustaining run chain. Prints '<seconds> <trade|tick>'.

  * trading day (weekday, 09:30:45 .. 16:20 ET): sleep to the next quarter-hour (+45 s), then run the bot  -> 'trade'
  * otherwise (evening, night, weekend): sleep towards the next weekday 09:30:45 ET in chunks of at most 5 hours
    ('tick' = just keep the chain alive with another sleep), the last chunk ends with 'trade'.
The chain therefore never has to be restarted by hand and never depends on GitHub's unreliable cron.
"""
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CHUNK = 5 * 3600            # a hosted job may run at most 6 h


def decide(now):
    """now: aware datetime in New York time. Returns (seconds, kind)."""
    open_t = now.replace(hour=9, minute=30, second=45, microsecond=0)
    close = now.replace(hour=16, minute=20, second=0, microsecond=0)
    if now.weekday() < 5 and open_t <= now < close:
        nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=(now.minute // 15 + 1) * 15, seconds=45)
        if nxt <= close:
            return max(int((nxt.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()), 1), "trade"
    target = open_t
    while target <= now or target.weekday() >= 5:      # wall-clock arithmetic keeps 09:30:45 across DST changes
        target = target + timedelta(days=1)
    secs = max(int((target.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()), 1)
    return (CHUNK, "tick") if secs > CHUNK else (secs, "trade")


if __name__ == "__main__":
    s, k = decide(datetime.now(NY))
    print(s, k)
