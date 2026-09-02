"""
The half of HR that nobody asked for
────────────────────────────────────
Until now the help desk only ever spoke when spoken to. A real HR does
not work that way: most of what makes someone feel looked after arrives
before they thought to ask. "Your probation ends on Friday." "You have
twelve days that lapse this month." "You have been late six times —
is everything all right?"

═══════════════════════════════════════════════════════════
NOTHING HERE DECIDES BY A NUMBER IN THIS FILE
═══════════════════════════════════════════════════════════
Every threshold — how long probation lasts, how many late arrivals are
worth a word, how long a request may sit — comes from `hr_settings` for
that company. A call centre and an architecture studio do not agree on
any of them, and neither should have to edit Python to say so.

Two switches turn the whole thing off: `proactive_to_employee` and
`proactive_to_ceo`. Unsolicited messages are the most intrusive thing in
this system and a company that wants none of them keeps everything else.

═══════════════════════════════════════════════════════════
SAID ONCE, NOT ON A LOOP
═══════════════════════════════════════════════════════════
These jobs run every half hour. Every send is written to `hr_nudges`
first and checked against it before sending, because "your probation
ends in 7 days" arriving forty-eight times a day is not a reminder, it
is a fault — and after the second one nobody reads any of them.

═══════════════════════════════════════════════════════════
THE ATTENDANCE ONE IS A QUESTION, NOT A WARNING
═══════════════════════════════════════════════════════════
Being late a lot usually means something — a bus route, a sick parent, a
shift that no longer fits. HR asks. A system that issues warnings by
email teaches people to say nothing, and then the reason never surfaces
at all. The wording matters more here than anywhere else in this file.
"""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.attendance import (
    AttendanceSession, CompanyLeaveType, LeaveBalance,
)
from app.models.chat import (
    ChatMessage, ChatSession, HrNudge, HrRequest,
)
from app.models.user import User
from app.utils.chat_cases import get_settings, stale_cases
from app.utils.pkt import get_pkt_today


# ══════════════════════════════════════════════
# Saying it once
# ══════════════════════════════════════════════
def _already_sent(db: Session, company_id: int, kind: str, ref: str,
                  employee_id: int = None) -> bool:
    q = db.query(HrNudge).filter(
        HrNudge.company_id == company_id,
        HrNudge.kind == kind,
        HrNudge.ref == str(ref),
    )
    q = q.filter(HrNudge.employee_id == employee_id) if employee_id \
        else q.filter(HrNudge.employee_id.is_(None))
    return db.query(q.exists()).scalar()


def _mark_sent(db: Session, company_id: int, kind: str, ref: str,
               employee_id: int = None) -> None:
    db.add(HrNudge(company_id=company_id, employee_id=employee_id,
                   kind=kind, ref=str(ref)))


# ══════════════════════════════════════════════
# Reaching the employee
# ══════════════════════════════════════════════
def _tell_employee(db: Session, employee: User, company_id: int,
                   text: str) -> None:
    """
    Put a message in their own help desk thread, and email them.

    It goes in the thread rather than only in an email so that it is
    there when they next open the chat — and so their reply lands in the
    same conversation, where HR can pick it up.
    """
    # ──── The last check before anything is sent ────
    # `_staff()` already returns only active people, but this is the
    # single point every proactive message passes through, and a leaver
    # receiving "your probation ends on Friday" is not a cosmetic bug —
    # it tells someone the company still thinks they work there. So the
    # guard is here too, where no future caller can route around it.
    from app.utils.workforce import may_receive_mail

    if not may_receive_mail(employee):
        print(f"[proactive] not sending to {employee.id} "
              f"({employee.status}) — not employed")
        return

    session = db.query(ChatSession).filter(
        ChatSession.employee_id == employee.id
    ).order_by(ChatSession.last_active_at.desc()).first()

    if not session:
        session = ChatSession(employee_id=employee.id, company_id=company_id,
                              title="From HR")
        db.add(session)
        db.flush()

    db.add(ChatMessage(session_id=session.id, role="hr", text=text,
                       intent="proactive"))
    session.last_active_at = datetime.utcnow()

    try:
        from app.utils import notify
        if employee.email:
            notify.send_email(to=employee.email, subject="A note from HR",
                              body=f"Dear {employee.full_name},\n\n{text}\n")
    except Exception as e:                              # noqa: BLE001
        print(f"[proactive] could not email {employee.id}: {e}")


def _tell_ceo(db: Session, company_id: int, subject: str, body: str) -> None:
    ceo = db.query(User).filter(User.id == company_id).first()
    if not ceo or not ceo.email:
        return
    try:
        from app.utils import notify
        notify.send_email(to=ceo.email, subject=subject,
                          body=f"Dear {ceo.full_name},\n\n{body}\n")
    except Exception as e:                              # noqa: BLE001
        print(f"[proactive] could not email the CEO: {e}")


def _companies(db: Session) -> list:
    return db.query(User).filter(User.role == "ceo").all()


def _staff(db: Session, ceo: User) -> list:
    if not ceo.company_name:
        return []
    return db.query(User).filter(
        User.company_name == ceo.company_name,
        User.role == "employee",
        User.status == "active",
    ).all()


# ══════════════════════════════════════════════
# 1. Probation is ending
# ══════════════════════════════════════════════
def check_probation(db: Session, ceo: User) -> int:
    s = get_settings(db, ceo.id)
    length, notice = s.probation_days or 0, s.probation_notice_days or 0
    if not (length and notice):
        return 0

    today = get_pkt_today()
    sent = 0

    for u in _staff(db, ceo):
        if not u.joining_date:
            continue
        ends = u.joining_date + timedelta(days=length)
        days_left = (ends - today).days
        if not (0 <= days_left <= notice):
            continue

        ref = str(ends)
        if s.proactive_to_employee and not _already_sent(
                db, ceo.id, "probation_employee", ref, u.id):
            _tell_employee(db, u, ceo.id,
                           f"Your probation period ends on {ends}. "
                           f"Nothing is needed from you — I will confirm "
                           f"once it has been reviewed.")
            _mark_sent(db, ceo.id, "probation_employee", ref, u.id)
            sent += 1

        if s.proactive_to_ceo and not _already_sent(
                db, ceo.id, "probation_ceo", ref, u.id):
            _tell_ceo(db, ceo.id,
                      f"Probation ending — {u.full_name}",
                      f"{u.full_name} ({u.department or 'no department'}) "
                      f"joined on {u.joining_date} and their {length}-day "
                      f"probation ends on {ends}.\n\n"
                      f"This needs a decision to confirm or extend.")
            _mark_sent(db, ceo.id, "probation_ceo", ref, u.id)
            sent += 1

    return sent


# ══════════════════════════════════════════════
# 2. Leave days about to lapse
# ══════════════════════════════════════════════
def check_leave_expiry(db: Session, ceo: User) -> int:
    s = get_settings(db, ceo.id)
    notice = s.leave_expiry_notice_days or 0
    if not (notice and s.proactive_to_employee):
        return 0

    today = get_pkt_today()
    year_end = date(today.year, 12, 31)
    if (year_end - today).days > notice:
        return 0

    # ──── Unlimited types are not "days you might lose" ────
    # Unpaid leave is stored with remaining_days = 999, so summing the
    # raw balances told a real employee they had 1,035 days about to
    # lapse. Only types with a real entitlement can expire.
    limited = {
        t.code for t in db.query(CompanyLeaveType).filter(
            CompanyLeaveType.company_id == ceo.id,
            CompanyLeaveType.is_enabled == True,        # noqa: E712
            CompanyLeaveType.is_unlimited == False,     # noqa: E712
        ).all()
    }
    if not limited:
        return 0

    sent = 0
    for u in _staff(db, ceo):
        balances = db.query(LeaveBalance).filter(
            LeaveBalance.employee_id == u.id,
            LeaveBalance.year == today.year,
        ).all()
        total = sum(b.remaining_days or 0 for b in balances
                    if b.leave_type in limited)
        if total <= 0:
            continue

        # Once per employee per year — the ref is the year itself.
        if _already_sent(db, ceo.id, "leave_expiry", today.year, u.id):
            continue

        _tell_employee(db, u, ceo.id,
                       f"You have {total} leave "
                       f"{'day' if total == 1 else 'days'} left for "
                       f"{today.year}, "
                       f"and the year ends on {year_end}. If you would like "
                       f"to use any of them, tell me the dates and I will "
                       f"put the request in for you.")
        _mark_sent(db, ceo.id, "leave_expiry", today.year, u.id)
        sent += 1

    return sent


# ══════════════════════════════════════════════
# 3. Someone has been late a lot
# ══════════════════════════════════════════════
def check_attendance_concern(db: Session, ceo: User) -> int:
    """
    A quiet word, once a month, when lateness passes this company's line.

    Deliberately NOT a warning, and deliberately not copied to the CEO.
    Someone late six times usually has a reason, and the only way to hear
    it is to ask in a way that is safe to answer.
    """
    s = get_settings(db, ceo.id)
    limit, window = s.late_pattern_count or 0, s.late_pattern_window_days or 0
    if not (limit and window and s.proactive_to_employee):
        return 0

    today = get_pkt_today()
    since = today - timedelta(days=window)
    sent = 0

    for u in _staff(db, ceo):
        late = db.query(AttendanceSession).filter(
            AttendanceSession.employee_id == u.id,
            AttendanceSession.date >= since,
            AttendanceSession.date <= today,
            AttendanceSession.is_late == True,          # noqa: E712
        ).count()
        if late < limit:
            continue

        ref = f"{today.year}-{today.month:02d}"
        if _already_sent(db, ceo.id, "attendance_concern", ref, u.id):
            continue

        _tell_employee(db, u, ceo.id,
                       f"I noticed you have arrived late {late} "
                       f"{'time' if late == 1 else 'times'} in the last "
                       f"{window} days. This is not a warning — I only "
                       f"wanted to check whether something is making the "
                       f"mornings difficult. If a different shift or a "
                       f"change of hours would help, tell me and I will see "
                       f"what can be done.")
        _mark_sent(db, ceo.id, "attendance_concern", ref, u.id)
        sent += 1

    return sent


# ══════════════════════════════════════════════
# 4. Days missed without leave
# ══════════════════════════════════════════════
def check_absence_concern(db: Session, ceo: User) -> int:
    """
    Working days with no attendance and no approved leave.

    The sibling of the lateness check, and treated the same way: HR asks
    rather than warns. Somebody missing three days without applying is
    far more often ill, stuck, or in trouble than they are careless, and
    a warning is the surest way never to find out which.
    """
    from app.models.attendance import CompanyWorkPolicy, LeaveRequest
    from app.utils.workpolicy import count_working_days

    s = get_settings(db, ceo.id)
    limit = s.absence_pattern_count or 0
    window = s.absence_pattern_window_days or 0
    if not (limit and window and s.proactive_to_employee):
        return 0

    today = get_pkt_today()
    since = today - timedelta(days=window)
    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == ceo.id).first()

    expected = count_working_days(policy, since, today)
    if expected <= 0:
        return 0

    sent = 0
    for u in _staff(db, ceo):
        present = db.query(AttendanceSession).filter(
            AttendanceSession.employee_id == u.id,
            AttendanceSession.date >= since,
            AttendanceSession.date <= today,
        ).count()

        # Days they had approved leave for are not absences — that is the
        # whole difference between "off" and "missing".
        on_leave = 0
        for r in db.query(LeaveRequest).filter(
                LeaveRequest.employee_id == u.id,
                LeaveRequest.status == "approved",
                LeaveRequest.end_date >= since,
                LeaveRequest.start_date <= today,
        ).all():
            on_leave += count_working_days(
                policy, max(r.start_date, since), min(r.end_date, today))

        missed = expected - present - on_leave
        if missed < limit:
            continue

        ref = f"{today.year}-{today.month:02d}"
        if _already_sent(db, ceo.id, "absence_concern", ref, u.id):
            continue

        _tell_employee(db, u, ceo.id,
                       f"I can see {missed} working "
                       f"{'day' if missed == 1 else 'days'} in the last "
                       f"{window} days with no attendance and no leave "
                       f"applied. I wanted to check you are all right "
                       f"before this affects your pay — if you were unwell "
                       f"or something came up, tell me and I will sort the "
                       f"records out.")
        _mark_sent(db, ceo.id, "absence_concern", ref, u.id)
        sent += 1

    return sent


# ══════════════════════════════════════════════
# 5. Things sitting on the CEO
# ══════════════════════════════════════════════
def check_ceo_backlog(db: Session, ceo: User) -> int:
    s = get_settings(db, ceo.id)
    if not s.proactive_to_ceo:
        return 0

    sla = s.request_sla_days or 0
    now = datetime.utcnow()
    sent = 0

    if sla:
        overdue = [
            r for r in db.query(HrRequest).filter(
                HrRequest.company_id == ceo.id,
                HrRequest.status == "open",
            ).all()
            if r.created_at and (now - r.created_at).days >= sla
        ]
        # Once a day at most — the ref is today's date.
        ref = str(get_pkt_today())
        if overdue and not _already_sent(db, ceo.id, "ceo_overdue", ref):
            lines = "\n".join(
                f"  · {r.subject} ({(now - r.created_at).days} days)"
                for r in overdue)
            _tell_ceo(db, ceo.id,
                      f"{len(overdue)} request(s) waiting on you",
                      f"These have been open longer than {sla} day(s):\n\n"
                      f"{lines}\n\nOpen Requests on the dashboard to respond.")
            _mark_sent(db, ceo.id, "ceo_overdue", ref)
            sent += 1

    stale = stale_cases(db, ceo.id)
    ref = f"stale-{get_pkt_today()}"
    if stale and not _already_sent(db, ceo.id, "ceo_stale_cases", ref):
        # Concern and age only. A stale grievance is still a grievance,
        # and the CEO knowing one exists is a different thing from the
        # CEO reading what is in it.
        counts = {}
        for c in stale:
            counts[c.concern] = counts.get(c.concern, 0) + 1
        lines = "\n".join(f"  · {k}: {v}" for k, v in counts.items())
        _tell_ceo(db, ceo.id,
                  f"{len(stale)} HR case(s) have gone quiet",
                  f"These have had no movement for more than "
                  f"{s.case_stale_days} day(s):\n\n{lines}\n\n"
                  f"Counts only — the details of a confidential case are "
                  f"not shared.")
        _mark_sent(db, ceo.id, "ceo_stale_cases", ref)
        sent += 1

    return sent


# ══════════════════════════════════════════════
# The job the scheduler calls
# ══════════════════════════════════════════════
CHECKS = (
    ("probation", check_probation),
    ("leave expiry", check_leave_expiry),
    ("attendance", check_attendance_concern),
    ("absence", check_absence_concern),
    ("CEO backlog", check_ceo_backlog),
)


def job_hr_proactive():
    """
    Every company, every check.

    One company failing must not stop the others, and one check failing
    must not stop the rest for that company — a broken probation date
    should not silence the leave-expiry warning.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        total = 0
        for ceo in _companies(db):
            for label, fn in CHECKS:
                try:
                    total += fn(db, ceo)
                    db.commit()
                except Exception as e:                  # noqa: BLE001
                    db.rollback()
                    print(f"[proactive] {label} failed for company "
                          f"{ceo.id}: {e}")
        return f"{total} nudge(s) sent" if total else None
    finally:
        db.close()
