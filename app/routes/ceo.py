from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import (
    Company, existing_name_for, name_is_taken, normalise_name,
)
from app.models.user import User
from app.schemas.user import EmployeeCreate
from app.crud.user import (
    create_employee, get_employees_by_company, get_user_by_email,
)
from app.utils.security import hash_password
from app.utils.tenancy import Tenant, require_ceo

router = APIRouter(
    prefix="/ceo",
    tags=["CEO"]
)

# ──── There is no local `require_ceo` any more ────
# This file used to define its own, and so did `recruitment.py` and
# `settings.py` and `utils/company.py` — four functions, all of which
# only asked "is this user a CEO?" and none of which asked "of which
# company?". The one in `utils/tenancy.py` answers both and scopes the
# session at the same time.


# ──── Create an employee ────
@router.post("/create-employee")
def create_employee_route(
    data: EmployeeCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_ceo),
):
    if get_user_by_email(db, data.email):
        raise HTTPException(
            status_code=400, detail="This email is already registered.")

    # The company comes from the tenant, not from the CEO's user id.
    # Those were the same number before this system had more than one
    # company and are not any more.
    employee, plain_password = create_employee(db, data, tenant.company_id)

    return {
        "message": "The employee has been created successfully.",
        "employee_id": employee.id,
        "full_name": employee.full_name,
        "email": employee.email,
        "password": plain_password
    }


# ──── All the employees ────
@router.get("/employees")
def get_employees(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_ceo),
):
    employees = get_employees_by_company(db, tenant.company_id)

    return {
        "company": tenant.company_name,
        "total_employees": len(employees),
        "employees": [
            {
                "id": emp.id,
                "full_name": emp.full_name,
                "email": emp.email,
                "phone": emp.phone,
                "department": emp.department,
                # The role inside the department. The create-user form
                # offers both from what the company already uses, so a
                # job title stops landing in the department column.
                "designation": emp.designation,
                "joining_date": emp.joining_date,
                "status": emp.status
            }
            for emp in employees
        ]
    }


# The CEO views their own profile
@router.get("/profile")
def get_profile(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_ceo),
):
    ceo = db.query(User).filter(User.id == tenant.user_id).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")
    return {
        "full_name": ceo.full_name,
        "email": ceo.email,
        "company_id": tenant.company_id,
        "company_name": tenant.company_name,
    }


# The CEO updates their own profile
@router.put("/profile")
def update_profile(
    data: dict,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_ceo),
):
    """
    ═══════════════════════════════════════════════════════════
    RENAMING THE COMPANY USED TO DELETE IT
    ═══════════════════════════════════════════════════════════
    This route wrote the new name onto the CEO's row and stopped there.
    Every employee still carried the OLD text, and the only thing tying
    them to their company was that those two strings matched. So the
    instant a CEO corrected a typo in their company name:

        employees        -> belonged to nothing
        payroll          -> ran for nobody
        leave types      -> gone
        attendance       -> no policy, no office, no shift
        headcount        -> zero

    with no error anywhere, because from the code's point of view the
    company had simply never had any employees.

    The rename is now an UPDATE of one row in `companies`. Nothing is
    keyed on the text, so nothing can come loose. The copies on the user
    rows are refreshed too, but only because screens and emails read
    them — no lookup uses them.
    """
    ceo = db.query(User).filter(User.id == tenant.user_id).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="The CEO was not found.")

    if data.get("full_name"):
        ceo.full_name = data["full_name"]

    if data.get("password"):
        ceo.password = hash_password(data["password"])

    renamed = None
    if data.get("company_name"):
        new_name = str(data["company_name"]).strip()
        slug = normalise_name(new_name)
        if not slug:
            raise HTTPException(
                status_code=400, detail="Please give your company a name.")

        company = db.query(Company).filter(
            Company.id == tenant.company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found.")

        if slug != company.slug:
            # A name is claimed once, or two tenants become
            # indistinguishable to everybody reading a screen.
            # ⚠ THIS IS THE ONE CROSS-COMPANY QUESTION A TENANT ASKS.
            # It ran as a query, which under the `companies` policy sees
            # nothing outside this company and therefore always answered
            # "free" — letting a duplicate slug through to the unique
            # index as a raw IntegrityError. It is a boolean function
            # now; see `models/company.name_is_taken`.
            if name_is_taken(db, slug, exclude_company_id=company.id):
                shown = existing_name_for(db, slug) or new_name
                raise HTTPException(
                    status_code=400,
                    detail=f"A company called {shown!r} is already "
                           f"registered. Please use a different name.",
                )
            company.slug = slug

        company.name = new_name
        renamed = new_name

        # Display copies, refreshed together. `company_id` is what binds
        # these people to the company; this is the label on the screen.
        db.query(User).filter(User.company_id == company.id).update(
            {User.company_name: new_name}, synchronize_session=False)

    db.commit()

    return {
        "message": "The profile has been updated successfully.",
        "full_name": ceo.full_name,
        "company_name": renamed or tenant.company_name,
    }
