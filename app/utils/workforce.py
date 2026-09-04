"""
Who works here — decided in ONE place
─────────────────────────────────────
This file exists because the system had four different answers to that
question and nobody had noticed:

    crud/user.py            no status filter at all — fired included
    utils/company.py        status != "fired"
    routes/recruitment.py   status == "active"  (one route)
    routes/recruitment.py   status != "fired"   (another route)

The monthly payroll job and the leave reminders both went through
`company_employees()`, which is the `!= "fired"` one. So anybody
`inactive`, `approved` or `pending` was treated as staff: payroll ran for
them, and the emails followed.

═══════════════════════════════════════════════════════════
A BLACKLIST OF ONE VALUE IS NOT A RULE
═══════════════════════════════════════════════════════════
`status != "fired"` does not mean "employed". It means "not this one
particular word", and every status anyone adds later — resigned,
suspended, on-notice, retired — quietly becomes employed the day it is
introduced. Nobody writing that migration would think to check.

So the lists below are WHITELISTS. A status nobody has classified is not
employed, is not a leaver, and shows up in `unclassified()` so it gets
noticed instead of guessed at.

═══════════════════════════════════════════════════════════
WHY LEAVERS KEEP THEIR USER ROW
═══════════════════════════════════════════════════════════
Their payslips, attendance and leave all point at `users.id`. Moving or
deleting the row would orphan exactly the history a company is obliged
to keep — the year of payroll that proves what someone was paid.

So the row stays as the person's identity, and `employment_records`
carries what happened: when they joined, when they left, why, and
whether the final settlement was done. That is the separate record, and
it is a record with facts in it rather than a status word.
"""

from datetime import timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.user import User

# ══════════════════════════════════════════════
# The three groups, by whitelist
# ══════════════════════════════════════════════
# On the payroll today. Gets paid, gets emailed, counts as headcount.
EMPLOYED = ("active",)

# Was employed and is not any more. Keeps every record; receives nothing.
FORMER = ("fired", "resigned", "retired", "contract_ended")

# On the books but not yet working — an account made before the start
# date, or an invitation not yet accepted. Neither paid nor written to.
NOT_YET = ("pending", "approved", "inactive")


def _base(db: Session, company_id: int):
    """
    Every employee of one company.

    ═══ THIS USED TO GO THROUGH THE COMPANY'S NAME ═══
        ceo  = db.query(User).filter(User.id == company_id).first()
        name = ceo.company_name
        return db.query(User).filter(User.company_name == name, ...)

    Two hops and a string in the middle. It read the CEO's row to get a
    name and then matched other rows on that text, so the whole payroll
    and every automated email depended on those strings agreeing. A CEO
    renaming their company changed one of them and not the others, and
    from that moment this returned nothing: no employees, no payroll, no
    reminders, headcount zero — with nothing raised anywhere.

    One hop and a foreign key now.
    """
    if not company_id:
        return None
    return db.query(User).filter(
        User.company_id == company_id,
        User.role == "employee",
    )


# ══════════════════════════════════════════════
# The one function almost everything should call
# ══════════════════════════════════════════════
def employed(db: Session, company_id: int) -> List[User]:
    """
    People who work here TODAY.

    This is the list for payroll, for any automatic email, for the
    proactive checks, and for headcount. If you are about to send
    somebody something, this is the list you want.
    """
    q = _base(db, company_id)
    if q is None:
        return []
    return q.filter(User.status.in_(EMPLOYED)).order_by(User.full_name).all()


def employed_during(db: Session, company_id: int, period: str) -> List[User]:
    """
    Who was on the payroll for a given month. `period` is "YYYY-MM".

    ═══ SOMEBODY WHO HAD NOT JOINED YET IS NOT UNPAID, THEY ARE ABSENT ═══
    Payroll ran over `employed()`, which asks only "do they work here
    today". So an employee who joined on 17 June had payslips generated
    for February, March, April and May — four months of salary recorded
    for a person who was not employed. On the CEO's console those months
    then read back as real figures, because by then they were rows in the
    payslips table like any other.

    A joining date is not a formality. Until it, there is nothing to pay
    and nothing to pro-rate.

    ═══ WHAT THIS DOES NOT YET HANDLE ═══
    Somebody who has since LEFT is excluded by `employed()` entirely, so
    re-running an old month they worked would quietly skip them. That is
    a separate gap and it needs the employment record's `ended_on`, not
    just a status word.
    """
    from datetime import date

    people = employed(db, company_id)

    try:
        year, month = (int(x) for x in str(period).split("-")[:2])
        # The last day of the period — joining on the 30th still counts
        last_day = date(year + (month == 12), (month % 12) + 1, 1) - \
            timedelta(days=1)
    except (ValueError, TypeError):
        # An unreadable period is not a reason to pay the wrong people
        return people

    return [
        u for u in people
        if u.joining_date is None or u.joining_date <= last_day
    ]


def former(db: Session, company_id: int) -> List[User]:
    """People who used to work here. They receive nothing, ever."""
    q = _base(db, company_id)
    if q is None:
        return []
    return q.filter(User.status.in_(FORMER)).order_by(User.full_name).all()


def not_yet_started(db: Session, company_id: int) -> List[User]:
    """Accounts that exist but are not employment yet."""
    q = _base(db, company_id)
    if q is None:
        return []
    return q.filter(User.status.in_(NOT_YET)).order_by(User.full_name).all()


def everyone_ever(db: Session, company_id: int) -> List[User]:
    """Every person on the books — for records and reporting only."""
    q = _base(db, company_id)
    return q.order_by(User.full_name).all() if q is not None else []


def unclassified(db: Session, company_id: int) -> List[User]:
    """
    Anyone whose status matches none of the three lists.

    Never empty by design — it is a tripwire. A new status word that
    nobody classified turns up here rather than silently landing in
    whichever group a `!=` comparison happened to put it.
    """
    known = set(EMPLOYED) | set(FORMER) | set(NOT_YET)
    return [u for u in everyone_ever(db, company_id)
            if (u.status or "") not in known]


def is_employed(user: Optional[User]) -> bool:
    """One person — safe to pay, safe to write to."""
    return bool(user and user.role == "employee" and user.status in EMPLOYED)


def may_receive_mail(user: Optional[User]) -> bool:
    """
    Whether this person should be sent anything at all.

    The last check before an automated email goes out. A leaver getting
    "your probation ends on Friday" is not a cosmetic bug — it tells
    someone the company still thinks they work there.
    """
    return bool(user and user.email and user.status in EMPLOYED)
