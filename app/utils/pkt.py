"""
Pakistan Standard Time (UTC+5) helpers
──────────────────────────────────────
Poora system PKT pe chalta hai. `date.today()` ya `datetime.utcnow()`
kabhi use mat karo — server ka timezone kuch bhi ho sakta hai, aur raat
12 se 5 baje ke darmiyan date ek din peeche aa jaati hai.
"""

from datetime import datetime, date, timedelta, timezone

PKT = timezone(timedelta(hours=5))


def get_pkt_now() -> datetime:
    """Abhi ka waqt PKT mein — naive datetime (DB mein isi tarah store hota hai)"""
    return datetime.now(PKT).replace(tzinfo=None)


def get_pkt_today() -> date:
    """Aaj ki date PKT ke hisaab se"""
    return get_pkt_now().date()
