import secrets
import string

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.company import (
    Company, existing_name_for, name_is_taken, normalise_name,
    STATUS_PENDING,
)
from app.models.user import User
from app.utils.security import hash_password


# ══════════════════════════════════════════════
# A CEO and their company, created together
# ══════════════════════════════════════════════
def create_ceo(db: Session, data):
    """
    Signing up creates the tenant.

    Both rows go in one transaction on purpose. A CEO whose company row
    failed to write cannot sign in (no `company_id`), and a company with
    no CEO can never be reached — either half alone is a stuck account
    that somebody has to fix by hand in the database.
    """
    slug = normalise_name(data.company_name)
    if not slug:
        raise HTTPException(
            status_code=400, detail="Please give your company a name.")

    # ═══ THE NAME IS CLAIMED, ONCE ═══
    # Two companies called "TechTribe" used to be possible, and they did
    # not merely look alike: an employee found their company by matching
    # the name and taking `.first()`, so every employee of the second
    # company resolved to the first one's CEO and began reading their
    # attendance, leave and payroll. Nothing errored.
    #
    # Matched on the normalised form, so "TechTribe", "techtribe" and
    # " Tech  Tribe " are the same claim.
    # Through the boolean function, not a query — `companies` has a
    # row-level-security policy and a direct read cannot see names it is
    # not entitled to. See `models/company.name_is_taken`.
    if name_is_taken(db, slug):
        shown = existing_name_for(db, slug) or data.company_name.strip()
        raise HTTPException(
            status_code=400,
            detail=f"A company called {shown!r} is already registered. "
                   f"Please use a different name.",
        )

    company = Company(
        name=data.company_name.strip(),
        slug=slug,
        status=STATUS_PENDING,
    )
    db.add(company)
    db.flush()          # the id, without committing half of it

    new_user = User(
        full_name=data.full_name,
        email=data.email,
        password=hash_password(data.password),
        role="ceo",
        company_id=company.id,
        company_name=company.name,
        status="pending",
    )
    db.add(new_user)
    db.flush()

    company.created_by = new_user.id

    db.commit()
    db.refresh(new_user)
    db.refresh(company)

    return new_user, company


# Find a user by email
def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()


# Generate a password automatically
def generate_password(length: int = 10) -> str:
    characters = string.ascii_letters + string.digits
    return ''.join(secrets.choice(characters) for _ in range(length))


# ══════════════════════════════════════════════
# An employee belongs to a company id, not a name
# ══════════════════════════════════════════════
def create_employee(db: Session, data, company_id: int):
    """
    This took a `ceo_id`, looked up that CEO, and copied their
    `company_name` string onto the new employee. That string was then the
    only thing tying them to their company — so the day the CEO edited
    the company's name, this employee stopped belonging anywhere.

    It now takes the company id, which the caller already holds from
    `Depends(require_ceo)`, and the name is copied only for display.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    plain_password = (data.password or "").strip() or generate_password()

    new_employee = User(
        full_name=data.full_name,
        email=data.email,
        password=hash_password(plain_password),
        phone=data.phone,
        department=data.department,
        designation=getattr(data, "designation", None) or None,
        joining_date=data.joining_date,
        role="employee",
        status="active",
        company_id=company.id,
        company_name=company.name,   # display copy — never matched on
    )

    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)

    return new_employee, plain_password


# ══════════════════════════════════════════════
# A CEO's people
# ══════════════════════════════════════════════
def get_employees_by_company(db: Session, company_id: int):
    """
    Everyone working here, or about to.

    Two things changed. It matched on `company_name` — see
    `create_employee` for why that was never a link. And it had no status
    filter at all, so a CEO's own employee list included the people they
    had let go; leavers are reached through the employment records, not
    through a list captioned "your employees".
    """
    from app.utils.workforce import EMPLOYED, NOT_YET

    return db.query(User).filter(
        User.role == "employee",
        User.company_id == company_id,
        User.status.in_(tuple(EMPLOYED) + tuple(NOT_YET)),
    ).order_by(User.full_name).all()
