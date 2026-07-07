from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.user import CEOSignup, LoginSchema
from app.crud.user import create_ceo, get_user_by_email
from app.utils.security import verify_password, create_access_token  

router = APIRouter(
    prefix="/auth",
    tags=["Auth"]
)


# CEO Signup
@router.post("/ceo-signup")
def ceo_signup(data: CEOSignup, db: Session = Depends(get_db)):

    user = get_user_by_email(db, data.email)

    if user:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    if data.password != data.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )

    new_user = create_ceo(db, data)

    return {
        "message": "Signup request sent to admin",
        "user_id": new_user.id
    }


@router.post("/login")
def login(data: LoginSchema, db: Session = Depends(get_db)):

    user = get_user_by_email(db, data.email)

    if not user:
        raise HTTPException(status_code=400, detail="The email is incorrect.")

    if not verify_password(data.password, user.password):
        raise HTTPException(status_code=400, detail="The password is incorrect.")

    if user.role == "ceo" and user.status != "approved":
        raise HTTPException(status_code=403, detail="The account has not been approved yet.")

    # ──── Fired/Deactivated account check ────
    if user.status == "fired":
        raise HTTPException(
            status_code=403,
            detail="Your account has been deactivate"
        )

    token = create_access_token({
        "user_id": user.id,
        "role": user.role,
        "email": user.email
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name
    }