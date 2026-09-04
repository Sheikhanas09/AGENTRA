"""
Company + authorization helpers
───────────────────────────────
`company_id` USED TO MEAN "the CEO's user id", and an employee reached it
by matching their `company_name` text against a CEO's. Both are gone:
the tenant is a row in `companies`, and `users.company_id` points at it.

⚠ `company_id` AND A CEO'S USER ID ARE NO LONGER THE SAME NUMBER.
For the two companies that existed before this change they still are —
the migration kept their ids so nothing had to be renumbered — but a
company registered afterwards has an id from 1000 up while its CEO has
whatever user id came next. So `company_id = ceo.id` is now a bug, and
`Depends(require_ceo)` hands the route the real one.
"""

from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User

# ══════════════════════════════════════════════
# One definition, re-exported
# ══════════════════════════════════════════════
# There were FOUR `require_ceo` functions — here, in `routes/ceo.py`, in
# `routes/recruitment.py` and in `routes/settings.py` — and every one of
# them only asked "is this user a CEO?". None asked "of THIS company?",
# which is the question that matters once there is more than one.
#
# This project has already paid for four separate definitions of
# "employee" (see `utils/workforce.py`). So there is now one, in
# `utils/tenancy.py`, and this name re-exports it so that every route
# already importing it from here was upgraded by that single line.
from app.utils.tenancy import (  # noqa: F401  (re-exported deliberately)
    Tenant, get_tenant, require_ceo, require_employee, require_superadmin,
)


def get_user_or_404(db: Session, user_id: int) -> User:
    """
    Fetch a user.

    Reads through the tenant guard, so on a scoped session this can only
    ever return somebody from the caller's own company — another
    company's user id is simply not there, and comes back 404 rather
    than 403. That is the intended answer: a 403 would confirm the id
    exists.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def resolve_company_id(db: Session, user: User) -> Optional[int]:
    """
    Which company this person belongs to.

    ═══ WHAT THIS USED TO DO ═══
        if user.role == "ceo":
            return user.id                      # id of a USER as a COMPANY
        ceo = db.query(User).filter(
            User.company_name == user.company_name,   # a STRING match
            User.role == "ceo",
        ).first()                                     # and .first() of them
        return ceo.id if ceo else None

    Three failures in six lines: two companies sharing a name merged
    into whichever the database returned first; renaming a company
    detached every employee at once; and a company was identified by a
    user id.

    It is now a column read. It stays a function because twenty-three
    call sites use it, and they were all fixed by fixing this.
    """
    return user.company_id


def assert_self(current_user, employee_id: int, action: str = "this"):
    """Only for yourself — no attendance/leave on someone else's behalf"""
    if _user_id(current_user) != employee_id:
        raise HTTPException(
            status_code=403,
            detail=f"You can only do {action} for yourself"
        )


def assert_can_view(db: Session, current_user, employee_id: int) -> User:
    """
    Your own record, or — if you are the CEO — your company's employee.

    Two walls, and the order matters:

      1. the tenant guard means `get_user_or_404` cannot see outside the
         caller's company at all, so a foreign employee id is a 404
      2. the company ids are compared here as well

    The second looks redundant while the first holds. It is here because
    this function is what stands between an employee and every payslip
    in the database, and one wall is a wall that has never been tested
    with the other one missing.
    """
    target = get_user_or_404(db, employee_id)

    if _user_id(current_user) == employee_id:
        return target

    if _role(current_user) == "superadmin":
        return target

    if _role(current_user) == "ceo":
        ceo = get_user_or_404(db, _user_id(current_user))
        if (target.company_id is not None
                and target.company_id == ceo.company_id):
            return target

    raise HTTPException(
        status_code=403, detail="You are not allowed to view this record")


def _user_id(current_user):
    """Works with both the old dict and a `Tenant` (see tenancy.py)."""
    return current_user["user_id"] if not isinstance(current_user, Tenant) \
        else current_user.user_id


def _role(current_user):
    return current_user["role"] if not isinstance(current_user, Tenant) \
        else current_user.role


def company_employees(db: Session, ceo: User) -> List[User]:
    """
    People who work here today. Payroll, emails and leave all use this.

    ═══ IT USED TO SAY `status != "fired"` ═══
    Which is not "employed" — it is "not this one particular word". So
    anybody `inactive`, `approved` or `pending` counted as staff, the
    monthly payroll job ran for them, and the payslip emails followed.
    Any status added later would have joined them silently.

    The whitelist lives in `utils/workforce.py`, in one place, and this
    delegates to it.
    """
    from app.utils.workforce import employed

    if not ceo.company_id:
        return []
    return employed(db, ceo.company_id)


def company_roster(db: Session, ceo: User) -> List[User]:
    """
    Everyone the CEO manages — working here, or about to.

    For planning screens only: setting up a salary structure before
    somebody's first day is sensible, paying them before it is not. This
    list is never used to send anything.
    """
    from app.utils.workforce import employed, not_yet_started

    if not ceo.company_id:
        return []
    people = employed(db, ceo.company_id) + not_yet_started(db, ceo.company_id)
    return sorted(people, key=lambda u: (u.full_name or "").lower())
