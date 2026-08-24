"""
Work policy helpers
───────────────────
Attendance and Leave both need this arithmetic — it is kept in one place
so the two modules can never behave differently.
"""

from datetime import date, timedelta
from typing import Optional


def parse_hhmm(value: str) -> Optional[int]:
    """'09:00' or '09:00:00' → minutes since midnight (540)"""
    try:
        parts = str(value).strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError, AttributeError):
        return None


def fmt_hhmm(minutes: int) -> str:
    """540 → '09:00'"""
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def is_overnight_shift(policy) -> bool:
    """
    Does the shift cross midnight? (e.g. 22:00 to 05:00)

    When shift_end < shift_start the shift runs through the night and ends
    on the next calendar day.
    """
    if not policy:
        return False
    start = parse_hhmm(policy.shift_start)
    end = parse_hhmm(policy.shift_end)
    if start is None or end is None:
        return False
    return end < start


def shift_length_minutes(policy) -> Optional[int]:
    """How long the shift is — correct even when it runs overnight"""
    if not policy:
        return None
    start = parse_hhmm(policy.shift_start)
    end = parse_hhmm(policy.shift_end)
    if start is None or end is None:
        return None
    return (end - start) if end >= start else (24 * 60 - start + end)


def work_date_for(policy, now) -> date:
    """
    Which DAY the attendance belongs to — the SHIFT's day, not the calendar's.

    ═══ WHY THIS MATTERS ═══
    On a normal shift (09:00-18:00) the two are the same.

    But on a night shift (22:00-05:00) the calendar date changes the moment
    midnight passes — while the person is still working the same shift.
    That is why an employee used to be able to check in AGAIN after 12
    (the system read it as a new day), even though they were still in
    yesterday's shift.

    Ab:
      22:00 Aug 11 pe check-in  ->  work date = Aug 11
      01:00 Aug 12 (same shift) ->  work date = Aug 11  (NO new check-in)
      06:00 Aug 12 (shift over)   -> work date = Aug 12 (a new day now)
    """
    today = now.date()

    if not is_overnight_shift(policy):
        return today

    end = parse_hhmm(policy.shift_end)
    now_minutes = now.hour * 60 + now.minute

    # After midnight but before the shift ends
    # → this is still the PREVIOUS day's shift
    if now_minutes <= end:
        return today - timedelta(days=1)

    return today


def _allowed_day_names(policy) -> set:
    """Turn the policy's working_days into a set that is easy to match"""
    allowed = {str(d).strip().lower() for d in policy.working_days}
    # Accept both "mon" and "monday"
    allowed |= {d[:3] for d in allowed}
    return allowed


def is_working_day(policy, day: date) -> bool:
    """
    Is this day a working day?

    No policy, or an empty working_days → True.
    An empty list is read as "not configured", not as "no day is a
    working day" — otherwise every day would become an off-day and all
    work would count as overtime.
    """
    if not policy or not policy.working_days:
        return True

    day_name = day.strftime("%A").lower()          # "monday"
    allowed = _allowed_day_names(policy)
    return day_name in allowed or day_name[:3] in allowed


def count_working_days(policy, start: date, end: date) -> int:
    """
    How many working days there are from start to end (both inclusive).

    The leave balance is deducted from this — in leave from Friday to
    Monday, Sat/Sun were off anyway, so they should not cost balance.
    """
    if end < start:
        return 0

    total = (end - start).days + 1
    if not policy or not policy.working_days:
        return total

    return sum(
        1 for i in range(total)
        if is_working_day(policy, start + timedelta(days=i))
    )
