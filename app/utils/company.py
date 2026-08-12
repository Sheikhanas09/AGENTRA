"""
Company + authorization helpers
───────────────────────────────
`company_id` koi alag table nahi — wo CEO ki user id hai.
Attendance aur Leave dono yehi rules use karte hain taake authorization
kabhi ek module mein sakht aur doosre mein narm na ho jaye.
"""

from typing import List, Optional

from fastapi import HTTPException, Depends
from sqlalchemy.orm import Session

from app.models.user import User
from app.utils.security import get_current_user


def require_ceo(current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in ["ceo", "superadmin"]:
        raise HTTPException(status_code=403, detail="Sirf CEO yeh kar sakta hai")
    return current_user


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nahi mila")
    return user


def resolve_company_id(db: Session, user: User) -> Optional[int]:
    """CEO ka apna id; employee ke liye us ki company ke CEO ka id"""
    if user.role == "ceo":
        return user.id
    if not user.company_name:
        return None
    ceo = db.query(User).filter(
        User.company_name == user.company_name,
        User.role == "ceo"
    ).first()
    return ceo.id if ceo else None


def assert_self(current_user: dict, employee_id: int, action: str = "yeh kaam"):
    """Apne liye hi — doosre ke naam pe attendance/leave nahi"""
    if current_user["user_id"] != employee_id:
        raise HTTPException(
            status_code=403,
            detail=f"Aap sirf apne liye {action} kar sakte hain"
        )


def assert_can_view(db: Session, current_user: dict, employee_id: int) -> User:
    """Apna record, ya CEO ho to apni company ke employee ka record"""
    target = get_user_or_404(db, employee_id)

    if current_user["user_id"] == employee_id:
        return target

    if current_user["role"] == "superadmin":
        return target

    if current_user["role"] == "ceo":
        ceo = get_user_or_404(db, current_user["user_id"])
        if target.company_name and target.company_name == ceo.company_name:
            return target

    raise HTTPException(status_code=403, detail="Yeh record dekhne ki ijazat nahi")


def company_employees(db: Session, ceo: User) -> List[User]:
    """CEO ki company ke active employees (fired ko chhod kar)"""
    if not ceo.company_name:
        return []
    return db.query(User).filter(
        User.company_name == ceo.company_name,
        User.role == "employee",
        User.status != "fired"
    ).order_by(User.full_name).all()
