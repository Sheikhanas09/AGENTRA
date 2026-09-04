from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company, LIVE_STATUSES
from app.schemas.user import CEOSignup, LoginSchema
from app.crud.user import create_ceo, get_user_by_email
from app.utils.security import verify_password, create_access_token
from app.utils.tenancy import auth_scope

router = APIRouter(
    prefix="/auth",
    tags=["Auth"]
)


# ══════════════════════════════════════════════
# Signing up IS creating a company
# ══════════════════════════════════════════════
@router.post("/ceo-signup")
def ceo_signup(
    data: CEOSignup,
    db: Session = Depends(get_db),
    _: None = Depends(auth_scope),
):
    """
    A new tenant.

    Two rows are written together and neither is any use alone: the
    company, and the CEO who owns it. `crud.create_ceo` does both in one
    transaction — a CEO with no company cannot sign in, and a company
    with no CEO can never be reached.

    Both start `pending`. The superadmin approving the CEO is what makes
    the company live, which is the flow that already existed; this only
    gives it something real to switch on.
    """
    if get_user_by_email(db, data.email):
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    if data.password != data.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )

    new_user, company = create_ceo(db, data)

    return {
        "message": "Signup request sent to admin",
        "user_id": new_user.id,
        "company_id": company.id,
        "company_name": company.name,
    }


@router.post("/login")
def login(
    data: LoginSchema,
    db: Session = Depends(get_db),
    _: None = Depends(auth_scope),
):
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

    # ══════════════════════════════════════════════
    # The company has to be live — for everybody in it
    # ══════════════════════════════════════════════
    # Suspension used to be a CEO-account status, so switching a company
    # off locked the CEO out and left every employee working normally:
    # marking attendance, applying for leave, drawing payroll. That is
    # not a suspended tenant, it is a tenant with no owner.
    company = None
    if user.role != "superadmin":
        if not user.company_id:
            raise HTTPException(
                status_code=403,
                detail="This account is not linked to a company. Please "
                       "contact your administrator.",
            )
        company = db.query(Company).filter(
            Company.id == user.company_id).first()
        if not company:
            raise HTTPException(
                status_code=403,
                detail="This account is not linked to a company. Please "
                       "contact your administrator.",
            )
        if company.status not in LIVE_STATUSES:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{company.name} is not active"
                    + (f" — {company.suspended_reason}"
                       if company.suspended_reason else "")
                    + ". Please contact your administrator."
                ),
            )

    # ══════════════════════════════════════════════
    # The token says which company, but nothing trusts it
    # ══════════════════════════════════════════════
    # Carrying it makes the common path cheap and makes a mismatch
    # detectable. `get_tenant` re-reads the user on every request and
    # compares — so a token minted before somebody moved, or one edited
    # by hand, is refused instead of being used to reach the old company.
    token = create_access_token({
        "user_id": user.id,
        "role": user.role,
        "email": user.email,
        "company_id": user.company_id,
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role,
        "full_name": user.full_name,
        "user_id": user.id,
        "department": user.department,
        "company_id": user.company_id,
        # The company's own name, not the copy on the user row — after a
        # rename those differ, and this one is right.
        "company_name": company.name if company else user.company_name,
    }
