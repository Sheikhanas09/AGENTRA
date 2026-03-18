from sqlalchemy.orm import Session
from app.models.user import User
from app.utils.security import hash_password
import secrets
import string


# CEO create karna (mat chhuo)
def create_ceo(db: Session, data):

    hashed = hash_password(data.password)

    new_user = User(
        full_name=data.full_name,
        email=data.email,
        password=hashed,
        role="ceo",
        company_name=data.company_name,
        status="pending"
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


# email se user find karna (mat chhuo)
def get_user_by_email(db: Session, email: str):

    return db.query(User).filter(User.email == email).first()


# Auto password generate karna (mat chhuo)
def generate_password(length: int = 10) -> str:
    characters = string.ascii_letters + string.digits
    return ''.join(secrets.choice(characters) for _ in range(length))


# Employee create karna ──── fix hua ────
def create_employee(db: Session, data, ceo_id: int):

    # ── CEO dhundo taake uski company mile ──
    ceo = db.query(User).filter(User.id == ceo_id).first()

    if not data.password or data.password.strip() == "":
        plain_password = generate_password()
    else:
        plain_password = data.password

    hashed = hash_password(plain_password)

    new_employee = User(
        full_name=data.full_name,
        email=data.email,
        password=hashed,
        phone=data.phone,
        department=data.department,
        joining_date=data.joining_date,
        role="employee",
        status="active",
        company_name=ceo.company_name if ceo else None  # ← CEO ki company ab save hogi
    )

    db.add(new_employee)
    db.commit()
    db.refresh(new_employee)

    return new_employee, plain_password


# CEO ke saare employees lana (mat chhuo)
def get_employees_by_company(db: Session, company_name: str):

    return db.query(User).filter(
        User.role == "employee",
        User.company_name == company_name
    ).all()