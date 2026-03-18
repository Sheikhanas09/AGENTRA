from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.user import EmployeeCreate
from app.crud.user import create_employee, get_employees_by_company
from app.utils.security import get_current_user, hash_password

router = APIRouter(
    prefix="/ceo",
    tags=["CEO"]
)


# ──── Role check helper ────
def require_ceo(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "ceo":
        raise HTTPException(status_code=403, detail="Sirf CEO yeh kaam kar sakta hai")
    return current_user


# ──── Employee banao ────
@router.post("/create-employee")
def create_employee_route(
    data: EmployeeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.crud.user import get_user_by_email
    existing = get_user_by_email(db, data.email)
    if existing:
        raise HTTPException(status_code=400, detail="Yeh email pehle se registered hai")

    employee, plain_password = create_employee(db, data, current_user["user_id"])

    return {
        "message": "Employee successfully create ho gaya",
        "employee_id": employee.id,
        "full_name": employee.full_name,
        "email": employee.email,
        "password": plain_password
    }


# ──── Saare employees dekho ────
@router.get("/employees")
def get_employees(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User

    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    if not ceo or not ceo.company_name:
        raise HTTPException(status_code=404, detail="CEO ka record nahi mila")

    employees = get_employees_by_company(db, ceo.company_name)

    return {
        "company": ceo.company_name,
        "total_employees": len(employees),
        "employees": [
            {
                "id": emp.id,
                "full_name": emp.full_name,
                "email": emp.email,
                "phone": emp.phone,
                "department": emp.department,
                "joining_date": emp.joining_date,
                "status": emp.status
            }
            for emp in employees
        ]
    }


# ──── Yeh naye add hue ────

# CEO apna profile dekhe
@router.get("/profile")
def get_profile(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="CEO nahi mila")
    return {
        "full_name": ceo.full_name,
        "email": ceo.email,
        "company_name": ceo.company_name,
    }


# CEO apna profile update kare
@router.put("/profile")
def update_profile(
    data: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="CEO nahi mila")

    if "full_name" in data and data["full_name"]:
        ceo.full_name = data["full_name"]
    if "company_name" in data and data["company_name"]:
        ceo.company_name = data["company_name"]
    if "password" in data and data["password"]:
        ceo.password = hash_password(data["password"])

    db.commit()

    return {
        "message": "Profile update ho gaya",
        "full_name": ceo.full_name,
        "company_name": ceo.company_name,
    }