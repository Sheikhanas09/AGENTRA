from typing import Optional

from pydantic import BaseModel, EmailStr
from datetime import date


# CEO Signup Schema
class CEOSignup(BaseModel):

    full_name: str
    email: EmailStr
    company_name: str
    password: str
    confirm_password: str


# Login Schema
class LoginSchema(BaseModel):

    email: EmailStr
    password: str


# Employee Create Schema
class EmployeeCreate(BaseModel):

    full_name: str
    email: EmailStr
    phone: str

    # ──── Two fields, because they are two things ────
    # `department` is where they sit in the company — Engineering,
    # Finance. `designation` is what they do inside it — Backend
    # Developer, QA Engineer. The column has existed since the console
    # reported "the Backend Developer department has 2 people"; nothing
    # was filling it in, so nothing could tell the two apart.
    department: str
    designation: Optional[str] = None

    joining_date: date
    password: str