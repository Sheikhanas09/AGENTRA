"""
Pakistan Standard Time (UTC+5) helpers
──────────────────────────────────────
The whole system runs on PKT. Never use `date.today()` or
`datetime.utcnow()` — the server's timezone could be anything, and
between midnight and 5am the date ends up a day behind.
"""

from datetime import datetime, date, timedelta, timezone

PKT = timezone(timedelta(hours=5))


def get_pkt_now() -> datetime:
    """The current time in PKT — a naive datetime (that is how the DB stores it)"""
    return datetime.now(PKT).replace(tzinfo=None)


def get_pkt_today() -> date:
    """Today's date according to PKT"""
    return get_pkt_now().date()
