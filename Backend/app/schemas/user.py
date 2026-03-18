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
    department: str
    joining_date: date
    password: str