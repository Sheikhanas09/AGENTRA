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
    """Active employees in the CEO's company (excluding fired ones)"""
    if not ceo.company_name:
        return []
    return db.query(User).filter(
        User.company_name == ceo.company_name,
        User.role == "employee",
        User.status != "fired"
    ).order_by(User.full_name).all()
