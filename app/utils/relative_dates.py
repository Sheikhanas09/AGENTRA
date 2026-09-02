"""
Turning "last week" into two dates
──────────────────────────────────
One definition, used by both the CEO console and the employee help desk.

It lived in the console only, so the help desk had none: asked "what was
my attendance last week?", the router turned it into a MONTH, the month
tool returned August, and the reply presented August's totals as a
week's — under a date range the model made up, which reached into
September and had not happened yet.

A date is a calendar fact. Two sides of one system must not each work it
out for themselves, and neither should a model.
"""

from datetime import timedelta


# ══════════════════════════════════════════════
# "this month", "last month", "today"
# ══════════════════════════════════════════════
# The model was left to work these out and got them wrong in the way
# that matters: asked how many joined "this month" in September, it
# reported an August and a June joiner. It had no anchor to resolve
# against, so it reached for whatever the nearest tool returned.
#
# These are calendar facts, not judgement. Python does them.
_REL = (
    ("day before yesterday", "dby"), ("parson", "dby"),
    ("yesterday", "yesterday"), ("kal", None),      # kal = both, ambiguous
    ("today", "today"), ("aaj", "today"),
    ("this month", "this_month"), ("is mahine", "this_month"),
    ("current month", "this_month"), ("is month", "this_month"),
    ("last month", "last_month"), ("pichle mahine", "last_month"),
    ("previous month", "last_month"), ("pichle month", "last_month"),
    ("this year", "this_year"), ("is saal", "this_year"),
    ("last year", "last_year"), ("pichle saal", "last_year"),
    ("this week", "this_week"), ("is hafte", "this_week"),
    ("next week", "next_week"), ("agle hafte", "next_week"),
    ("agley hafte", "next_week"), ("coming week", "next_week"),
    ("last week", "last_week"), ("pichle hafte", "last_week"),
)


# ─────────────────────────────────────────────────────────────────
# A WEEK IS MONDAY TO SUNDAY, AND IT WAS NOT DEFINED ANYWHERE
# ─────────────────────────────────────────────────────────────────
# Asked "what about next week?", the console answered about
# "September 5 to September 11". Today was Wednesday 2 September, so
# that is neither the next Monday-to-Sunday (7–13) nor the next seven
# days (3–9). It was invented, because nothing in this system said what
# a week is:
#
#     "next week" / "last week"   not in the resolver at all
#     "this week"                 resolved to {"on_date": today}
#
# The second one is its own small wrong: a question about a week was
# answered about a day, and nothing said so.
#
# Monday to Sunday, because that is the calendar the work policy already
# runs on (working_days is a set of weekdays), and because a CEO asking
# "next week" on a Wednesday means the week that starts on Monday, not
# the nine days from now.
def week_window(today, offset: int = 0):
    """(monday, sunday) of the week `offset` weeks from today's."""
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    return monday, monday + timedelta(days=6)


def resolve_relative(message: str, today) -> dict:
    """
    Turn a phrase into a period or a date. Empty when nothing is named.

    Returns {"period": "YYYY-MM"} or {"on_date": "YYYY-MM-DD"} or
    {"year": n} — whichever the phrase actually pins down.
    """
    low = (message or "").lower()
    hit = next((tag for phrase, tag in _REL if tag and phrase in low), None)
    if not hit:
        return {}

    if hit == "today":
        return {"on_date": str(today)}
    if hit == "yesterday":
        return {"on_date": str(today - timedelta(days=1))}
    if hit == "dby":
        return {"on_date": str(today - timedelta(days=2))}
    if hit == "this_month":
        return {"period": f"{today.year:04d}-{today.month:02d}"}
    if hit == "last_month":
        y, m = (today.year - 1, 12) if today.month == 1 else (today.year,
                                                              today.month - 1)
        return {"period": f"{y:04d}-{m:02d}"}
    if hit == "this_year":
        return {"year": today.year}
    if hit == "last_year":
        return {"year": today.year - 1}
    # A week is a RANGE. It used to come back as a single date, so
    # "who is on leave this week" was answered about today.
    if hit in ("this_week", "next_week", "last_week"):
        start, end = week_window(today, {"this_week": 0, "next_week": 1,
                                         "last_week": -1}[hit])
        return {"date_from": str(start), "date_to": str(end)}
    return {}
