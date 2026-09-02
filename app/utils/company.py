"""
Company + authorization helpers
───────────────────────────────
`company_id` is not a separate table — it IS the CEO's user id.
Attendance and Leave both use these rules, so authorization can never be
strict in one module and loose in another.
"""

from typing import List, Optional

from fastapi import HTTPException, Depends
from sqlalchemy.orm import Session

from app.models.user import User
from app.utils.security import get_current_user


def require_ceo(current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in ["ceo", "superadmin"]:
        raise HTTPException(status_code=403, detail="Only the CEO can do this")
    return current_user


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def resolve_company_id(db: Session, user: User) -> Optional[int]:
    """The CEO's own id; for an employee, their company's CEO id"""
    if user.role == "ceo":
        return user.id
    if not user.company_name:
        return None
    ceo = db.query(User).filter(
        User.company_name == user.company_name,
        User.role == "ceo"
    ).first()
    return ceo.id if ceo else None


def assert_self(current_user: dict, employee_id: int, action: str = "this"):
    """Only for yourself — no attendance/leave on someone else's behalf"""
    if current_user["user_id"] != employee_id:
        raise HTTPException(
            status_code=403,
            detail=f"You can only do {action} for yourself"
        )


def assert_can_view(db: Session, current_user: dict, employee_id: int) -> User:
    """Your own record, or — if you are the CEO — your company's employee"""
    target = get_user_or_404(db, employee_id)

    if current_user["user_id"] == employee_id:
        return target

    if current_user["role"] == "superadmin":
        return target

    if current_user["role"] == "ceo":
        ceo = get_user_or_404(db, current_user["user_id"])
        if target.company_name and target.company_name == ceo.company_name:
            return target

    raise HTTPException(status_code=403, detail="You are not allowed to view this record")


def company_employees(db: Session, ceo: User) -> List[User]:
    """
    People who work here today. Payroll, emails and leave all use this.

    ═══ IT USED TO SAY `status != "fired"` ═══
    Which is not "employed" — it is "not this one particular word". So
    anybody `inactive`, `approved` or `pending` counted as staff, the
    monthly payroll job ran for them, and the payslip emails followed.
    Any status added later would have joined them silently.

    The whitelist now lives in `utils/workforce.py`, in one place, and
    this delegates to it. Where a CEO screen needs to see people who
    have not started yet, use `company_roster()` below and say so.
    """
    from app.utils.workforce import employed

    if not ceo.company_name:
        return []
    return employed(db, ceo.id)


def company_roster(db: Session, ceo: User) -> List[User]:
    """
    Everyone the CEO manages — working here, or about to.

    For planning screens only: setting up a salary structure before
    somebody's first day is sensible, paying them before it is not. This
    list is never used to send anything.
    """
    from app.utils.workforce import employed, not_yet_started

    if not ceo.company_name:
        return []
    people = employed(db, ceo.id) + not_yet_started(db, ceo.id)
    return sorted(people, key=lambda u: (u.full_name or "").lower())
