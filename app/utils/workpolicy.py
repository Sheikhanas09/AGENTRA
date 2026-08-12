"""
Work policy helpers
───────────────────
Attendance aur Leave dono ko yehi hisaab chahiye — ek hi jagah rakha hai
taake dono modules ka behaviour kabhi alag na ho jaye.
"""

from datetime import date, timedelta
from typing import Optional


def parse_hhmm(value: str) -> Optional[int]:
    """'09:00' ya '09:00:00' → aadhi raat se minutes (540)"""
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
    Shift aadhi raat paar karti hai? (misal 22:00 se 05:00)

    shift_end < shift_start ho to shift raat bhar chalti hai aur agle
    calendar din par khatam hoti hai.
    """
    if not policy:
        return False
    start = parse_hhmm(policy.shift_start)
    end = parse_hhmm(policy.shift_end)
    if start is None or end is None:
        return False
    return end < start


def shift_length_minutes(policy) -> Optional[int]:
    """Shift kitni lambi hai — overnight ho to bhi sahi hisaab"""
    if not policy:
        return None
    start = parse_hhmm(policy.shift_start)
    end = parse_hhmm(policy.shift_end)
    if start is None or end is None:
        return None
    return (end - start) if end >= start else (24 * 60 - start + end)


def work_date_for(policy, now) -> date:
    """
    Attendance kis DIN ki hai — calendar date nahi, SHIFT ka din.

    ═══ YEH KYUN ZAROORI HAI ═══
    Normal shift (09:00-18:00) mein dono ek hi hain.

    Magar raat ki shift (22:00-05:00) mein aadhi raat guzarte hi calendar
    date badal jati hai — jabke banda usi shift mein kaam kar raha hota hai.
    Pehle isi wajah se employee 12 baje ke baad DOBARA check-in kar leta tha
    (system usay naya din samajh leta tha), halanke wo abhi tak kal wali
    shift mein tha.

    Ab:
      22:00 Aug 11 pe check-in  ->  work date = Aug 11
      01:00 Aug 12 (usi shift)  ->  work date = Aug 11  (naya check-in NAHI)
      06:00 Aug 12 (shift khatam) -> work date = Aug 12 (ab naya din)
    """
    today = now.date()

    if not is_overnight_shift(policy):
        return today

    end = parse_hhmm(policy.shift_end)
    now_minutes = now.hour * 60 + now.minute

    # Aadhi raat ke baad magar shift khatam hone se pehle
    # → yeh abhi bhi PICHHLE din ki shift hai
    if now_minutes <= end:
        return today - timedelta(days=1)

    return today


def _allowed_day_names(policy) -> set:
    """Policy ke working_days ko match-karne layak set mein badlo"""
    allowed = {str(d).strip().lower() for d in policy.working_days}
    # "mon" likha ho ya "monday" — dono chalein
    allowed |= {d[:3] for d in allowed}
    return allowed


def is_working_day(policy, day: date) -> bool:
    """
    Yeh din working day hai?

    Policy na ho ya working_days khali ho → True.
    Khali list ka matlab "configure nahi kiya" liya jata hai, na ke
    "koi din working nahi" — warna har din off-day ban jata aur har
    kaam overtime count hota.
    """
    if not policy or not policy.working_days:
        return True

    day_name = day.strftime("%A").lower()          # "monday"
    allowed = _allowed_day_names(policy)
    return day_name in allowed or day_name[:3] in allowed


def count_working_days(policy, start: date, end: date) -> int:
    """
    start se end tak (dono shamil) kitne working days hain.

    Leave balance isi se katta hai — Friday se Monday ki chhutti mein
    Sat/Sun waise hi off hote hain, unka balance kyun kate.
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
