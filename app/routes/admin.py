from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.database import get_db
from app.models.user import User
from app.utils.security import get_current_user

router = APIRouter(
    prefix="/admin",
    tags=["Admin"]
)


# ──── Role check helper ────
def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Only the Super Admin can perform this action.")
    return current_user


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
    db.commit()

    return {"message": "The CEO has been approved.", "ceo_id": ceo.id}


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


# CEO ko reject karo
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
    db.commit()
    return {"message": "The CEO has been rejected."}


# CEO manually inactive karo
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
    db.commit()
    return {"message": "The CEO has been deactivated."}


# CEO dobara active karo
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
    db.commit()
    return {"message": "The CEO has been activated."}


# ──── Yeh naya add hua ────

# CEO delete karo
@router.delete("/delete-ceo/{ceo_id}")
def delete_ceo(
    ceo_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    ceo = db.query(User).filter(User.id == ceo_id).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")
    db.delete(ceo)
    db.commit()
    return {"message": "The CEO has been deleted."}