"""
One person's attendance for a date range — the only calculation
────────────────────────────────────────────────────────────────
This file exists so that BOTH sides of the system count an absence the
same way, without either being able to reach the other's data.

    the CEO console  ->  hr_company_data.py  ->  here
    the help desk    ->  chat_data.py        ->  here

They may not import each other: `chat_data` holding a company-wide tool
would end the "an employee can only ever see their own row" guarantee,
and `check_scope.py` fails the build if that import ever appears. But
the arithmetic below is about ONE employee over ONE range, so it is safe
for both — and having it in two places is exactly how the console and
the payslip came to disagree in public.

    console  : "you missed all 21 working days"
    the slip : "Absent Days: 11"

Everything here delegates the actual absence count to
`payroll_data.absent_days()`, which examines each day on its own. There
is one definition of an absence in this system, and it is that one.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.models.attendance import (
    AttendanceSession, CompanyWorkPolicy, LeaveRequest,
)
from app.utils.pkt import get_pkt_today


# ══════════════════════════════════════════════
# One attendance model, for every tool here
# ══════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────
# WHY THIS EXISTS
# ─────────────────────────────────────────────────────────────────
# The console had its own arithmetic — working_days − present − leave —
# while payroll used `payroll_data.absent_days()`, which examines each
# day on its own. The two disagreed, in public:
#
#   attendance answer : "present 0 days, missed all 21 working days"
#   the payslip       : "Present Days: 0, Absent Days: 11"
#
# And the console's own answers contradicted themselves — "has attended
# for 1 day... has not been present on that day".
#
# `absent_days()` already explains why subtraction is wrong: a Sunday
# check-in hides an absence, and a day with both a session AND leave gets
# subtracted twice. Its docstring warned against exactly what this file
# was doing.
#
# So there is one calculation now, and it is that one.
#
# ─────────────────────────────────────────────────────────────────
# WHY A PAYSLIP CAN STILL SHOW A DIFFERENT NUMBER
# ─────────────────────────────────────────────────────────────────
# `absent_days()` stops counting at `today` — days that have not happened
# are not absences. A payslip computed on the 12th therefore froze the
# count at 11, and the same month read today gives 21. Neither is wrong;
# they are answers to different questions, and the payslip is a record of
# what was decided on the day it ran.
#
# `attendance_for` returns `as_of` so a reply can say which it is,
# instead of asserting one and being contradicted by the other.
#
# ─────────────────────────────────────────────────────────────────
# NOBODY IS ABSENT BEFORE THEY WERE HIRED
# ─────────────────────────────────────────────────────────────────
# Sheikh Anas joined on 14 August. Asked about August, this counted 21
# absences and listed 3 August among them — eleven of those days were
# before he worked here. It is the same mistake the payroll job made
# when it generated payslips for months before somebody joined: the
# window was taken from the question and not from the employment.
#
# So the window starts at the joining date when that is later. A person
# with no joining date recorded is counted from the start of the period,
# which is the old behaviour and the only safe default.
def attendance_for(db: Session, employee_id: int, company_id: int,
                   start: date, end: date) -> dict:
    """One person, one date range, counted day by day."""
    from app.models.user import User
    from app.utils.payroll_data import absent_days
    from app.utils.workpolicy import count_working_days, is_working_day

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id).first()

    person = db.query(User).filter(User.id == employee_id).first()
    joined = person.joining_date if person else None
    start_of_period = start
    counted_from = max(start, joined) if joined else start

    today = get_pkt_today()
    upto = min(end, today)

    # Hired after the period ended — nothing of theirs falls inside it.
    if counted_from > upto:
        return {
            "period_start": str(start),
            "period_end": str(end),
            "counted_from": str(counted_from),
            "as_of": str(upto),
            "not_employed_in_this_period": True,
            "joined_on": str(joined) if joined else None,
            "working_days": 0, "present_days": 0, "leave_days": 0,
            "absent_days": 0, "absent_dates": [], "late_days": 0,
            "late_minutes": 0, "overtime_minutes": 0,
            "sessions_on_non_working_days": 0,
        }

    start = counted_from

    sessions = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.date >= start,
        AttendanceSession.date <= end,
    ).order_by(AttendanceSession.date).all()

    # Sessions on a working day are attendance; a Sunday check-in is not
    # absence-relevant and must not be netted off anything.
    on_working = [s for s in sessions if is_working_day(policy, s.date)]

    leave_days = set()
    for lv in db.query(LeaveRequest).filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.company_id == company_id,
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= upto,
            LeaveRequest.end_date >= start,
    ).all():
        cur = max(lv.start_date, start)
        stop = min(lv.end_date, upto)
        while cur <= stop:
            if is_working_day(policy, cur):
                leave_days.add(cur)
            cur = date.fromordinal(cur.toordinal() + 1)

    absent = absent_days(db, employee_id, company_id, policy, start, end,
                         today=today)
    late = [s for s in on_working if s.is_late]

    return {
        "period_start": str(start),
        "period_end": str(end),
        # Says so out loud when the window was cut short by a joining
        # date, so "13 working days" in a 21-day month is explainable
        # rather than looking like a miscount.
        "counted_from": str(counted_from),
        "joined_on": str(joined) if joined else None,
        "joined_during_this_period": bool(joined and joined > start_of_period),
        # Days after this have not happened, so nothing is counted for
        # them. A reply that says "21 working days" about a month still
        # running is claiming the future.
        "as_of": absent.get("counted_until") or str(upto),
        "working_days": count_working_days(policy, start, upto),
        "present_days": len(on_working),
        "leave_days": len(leave_days),
        "absent_days": absent["absent_days"],
        "absent_dates": absent["dates"][:15],
        "late_days": len(late),
        "late_minutes": sum(s.late_by_minutes or 0 for s in late),
        "overtime_minutes": sum(s.overtime_minutes or 0 for s in on_working),
        "sessions_on_non_working_days": len(sessions) - len(on_working),
        **_absence_kinds(db, employee_id, company_id, absent["dates"]),
    }


# ─────────────────────────────────────────────────────────────────
# "UNAUTHORISED" IS A WORD THIS SYSTEM CANNOT SAY
# ─────────────────────────────────────────────────────────────────
# Asked who had the most unauthorised absences, the console answered
# "Sheikh Wasi, with 18 absent days". Nothing in the database supports
# the word:
#
#   · `AttendanceSession.status` only ever holds `checked_out`. An
#     absence is not a row — it is the absence of one.
#   · No field anywhere records an authorisation decision about an
#     absence. There is no "excused", no "approved after the fact".
#
# `absent_days` means one thing: a working day with no session and no
# APPROVED leave. Three quite different situations are inside it, and
# they ARE distinguishable, because the leave request survives whatever
# was decided about it:
#
#   no request at all   they did not turn up and did not ask
#   asked, refused      they asked, it was refused, they stayed away
#   asked, undecided    still pending — nobody has ruled on it yet
#
# The first is the closest thing to "unauthorised" that exists here, and
# even that is an inference. So all three are reported and the word is
# left to whoever has the authority to use it.
def _absence_kinds(db: Session, employee_id: int, company_id: int,
                   absent_dates) -> dict:
    """What is known about WHY each absent day is absent."""
    if not absent_dates:
        return {"absence_kinds": {}, "absence_kinds_note": None}

    days = [date.fromisoformat(str(d)[:10]) for d in absent_dates]

    requests = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.company_id == company_id,
        LeaveRequest.start_date <= max(days),
        LeaveRequest.end_date >= min(days),
    ).all()

    kinds = {"no_request_at_all": 0, "request_refused": 0,
             "request_undecided": 0, "request_withdrawn": 0}

    for day in days:
        states = {(r.status.value if hasattr(r.status, "value")
                   else str(r.status)).lower()
                  for r in requests if r.start_date <= day <= r.end_date}
        if not states:
            kinds["no_request_at_all"] += 1
        elif "rejected" in states:
            kinds["request_refused"] += 1
        elif states & {"pending", "evaluating"}:
            kinds["request_undecided"] += 1
        else:                                   # cancelled, withdrawn
            kinds["request_withdrawn"] += 1

    return {
        "absence_kinds": kinds,
        "absence_kinds_note":
            "These are the ONLY distinctions this system holds. It does "
            "not record whether an absence was authorised, excused or "
            "unauthorised — no such field exists. `no_request_at_all` is "
            "the closest thing to unauthorised and is still an inference, "
            "not a decision anybody recorded.",
    }
