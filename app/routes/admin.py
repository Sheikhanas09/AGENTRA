from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.database import get_db
from app.models.company import (
    Company, STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_PENDING,
)
from app.models.user import User
from app.utils.tenancy import require_superadmin

router = APIRouter(
    prefix="/admin",
    tags=["Admin"]
)

# ══════════════════════════════════════════════
# The one role that is not inside a company
# ══════════════════════════════════════════════
# `require_admin` used to read the role straight out of the JWT. The
# shared `require_superadmin` re-reads it from the database — this is
# the identity for which a forged claim would open every tenant at once,
# so it is not taken on the token's word — and it marks the session as
# allowed to read across companies, in writing.
require_admin = require_superadmin


def _company_of(db, ceo: User):
    return db.query(Company).filter(Company.id == ceo.company_id).first()


def _set_company_status(db, ceo: User, status: str, reason: str = None):
    """
    Approving or suspending a CEO does the same to their company.

    ═══ THESE USED TO BE TWO SEPARATE FACTS ═══
    Suspension was a status on the CEO's user row and nothing else, so
    switching a company off locked its CEO out and left every employee
    working normally — marking attendance, applying for leave, drawing
    payroll. That is a company with no owner, not a suspended one.

    The company row is what `get_tenant` checks on every single request,
    so moving it here is what makes suspension reach everybody in it,
    including the sessions already open.
    """
    from datetime import datetime

    company = _company_of(db, ceo)
    if not company:
        return None
    company.status = status
    if status == STATUS_ACTIVE:
        company.activated_at = datetime.utcnow()
        company.suspended_at = None
        company.suspended_reason = None
    elif status == STATUS_SUSPENDED:
        company.suspended_at = datetime.utcnow()
        company.suspended_reason = reason
    return company


# Pending CEOs list
@router.get("/pending-ceos")
def pending_ceos(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceos = db.query(User).filter(
        User.role == "ceo",
        User.status == "pending"
    ).all()

    return [
        {
            "id": ceo.id,
            "full_name": ceo.full_name,
            "email": ceo.email,
            "company_name": ceo.company_name,
            "status": ceo.status
        }
        for ceo in ceos
    ]


# Approve CEO
@router.put("/approve-ceo/{ceo_id}")
def approve_ceo(
    ceo_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceo = db.query(User).filter(User.id == ceo_id).first()

    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")

    if ceo.status == "approved":
        raise HTTPException(status_code=400, detail="The CEO is already approved.")

    ceo.status = "approved"
    ceo.approved_at = datetime.utcnow()
    ceo.expires_at = datetime.utcnow() + timedelta(days=30)
    company = _set_company_status(db, ceo, STATUS_ACTIVE)
    db.commit()

    return {
        "message": "The CEO has been approved.",
        "ceo_id": ceo.id,
        "company_id": company.id if company else None,
        "company_name": company.name if company else None,
    }


# Approved CEOs list
@router.get("/approved-ceos")
def approved_ceos(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceos = db.query(User).filter(
        User.role == "ceo",
        User.status == "approved"
    ).all()

    now = datetime.utcnow()
    result = []

    for ceo in ceos:
        if ceo.expires_at and now > ceo.expires_at:
            ceo.status = "inactive"
            db.commit()
            continue

        days_left = None
        if ceo.expires_at:
            days_left = (ceo.expires_at - now).days

        result.append({
            "id": ceo.id,
            "full_name": ceo.full_name,
            "email": ceo.email,
            "company_name": ceo.company_name,
            "status": ceo.status,
            "approved_at": ceo.approved_at.isoformat() if ceo.approved_at else None,
            "expires_at": ceo.expires_at.isoformat() if ceo.expires_at else None,
            "days_left": days_left
        })

    return result


# Inactive CEOs list
@router.get("/inactive-ceos")
def inactive_ceos(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceos = db.query(User).filter(
        User.role == "ceo",
        User.status == "inactive"
    ).all()

    return [
        {
            "id": ceo.id,
            "full_name": ceo.full_name,
            "email": ceo.email,
            "company_name": ceo.company_name,
            "status": ceo.status,
        }
        for ceo in ceos
    ]


# Rejected CEOs list
@router.get("/rejected-ceos")
def rejected_ceos(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceos = db.query(User).filter(
        User.role == "ceo",
        User.status == "rejected"
    ).all()

    return [
        {
            "id": ceo.id,
            "full_name": ceo.full_name,
            "email": ceo.email,
            "company_name": ceo.company_name,
            "status": ceo.status
        }
        for ceo in ceos
    ]


# Reject a CEO
@router.put("/reject-ceo/{ceo_id}")
def reject_ceo(
    ceo_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceo = db.query(User).filter(User.id == ceo_id).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")
    ceo.status = "rejected"
    _set_company_status(db, ceo, STATUS_SUSPENDED, "The registration was declined.")
    db.commit()
    return {"message": "The CEO has been rejected."}


# Manually deactivate a CEO
@router.put("/deactivate-ceo/{ceo_id}")
def deactivate_ceo(
    ceo_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceo = db.query(User).filter(User.id == ceo_id).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")
    ceo.status = "inactive"
    # Everybody in the company, not just the CEO — see `_set_company_status`.
    _set_company_status(db, ceo, STATUS_SUSPENDED,
                        "The company has been deactivated by the administrator.")
    db.commit()
    return {
        "message": "The company has been deactivated. Nobody in it can sign "
                   "in until it is activated again."
    }


# Reactivate a CEO
@router.put("/activate-ceo/{ceo_id}")
def activate_ceo(
    ceo_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceo = db.query(User).filter(User.id == ceo_id).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")
    ceo.status = "approved"
    ceo.approved_at = datetime.utcnow()
    ceo.expires_at = datetime.utcnow() + timedelta(days=30)
    _set_company_status(db, ceo, STATUS_ACTIVE)
    db.commit()
    return {"message": "The company has been activated."}


# ──── Newly added ────

# Delete a CEO
@router.delete("/delete-ceo/{ceo_id}")
def delete_ceo(
    ceo_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """
    ═══════════════════════════════════════════════════════════
    THIS USED TO BE `db.delete(ceo)` AND NOTHING ELSE
    ═══════════════════════════════════════════════════════════
    One statement, no cascade, no check. The company's employees, jobs,
    candidates, attendance, leave, payslips and chat history all stayed
    exactly where they were, now belonging to a company whose owner did
    not exist — and every screen that starts from the CEO went blank
    while a year of payroll sat there unreachable.

    A company is not deleted, it is switched off. The records are what a
    company is legally obliged to keep, and `ON DELETE RESTRICT` on
    `users.company_id` now refuses this at the database as well, so
    there is no way to do it by accident from anywhere.
    """
    ceo = db.query(User).filter(User.id == ceo_id).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")

    _set_company_status(db, ceo, STATUS_SUSPENDED,
                        "The company was closed by the administrator.")
    ceo.status = "inactive"
    db.commit()

    return {
        "message": "The company has been suspended and nobody in it can sign "
                   "in. Its records are kept — payroll and attendance history "
                   "cannot be deleted with one click.",
        "company_id": ceo.company_id,
    }