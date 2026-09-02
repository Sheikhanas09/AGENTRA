from sqlalchemy import Column, Integer, String, Date, DateTime
from app.database import Base


class User(Base):

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    full_name = Column(String)
    email = Column(String, unique=True)

    password = Column(String)

    role = Column(String)  # superadmin / ceo / employee

    company_name = Column(String)

    phone = Column(String)

    # ──── Department and job title are different things ────
    # `department` alone held both — "Finance" for one person and
    # "Backend Developer" for another. Counting employees by department
    # then produced "the Backend Developer department has 2 people",
    # which is not a department and not a useful answer.
    department = Column(String)
    designation = Column(String)

    joining_date = Column(Date)

    status = Column(String)  # pending / approved / active / inactive

    # ──── Approval tracking ────
    approved_at = Column(DateTime, nullable=True)   # when approval happened
    expires_at = Column(DateTime, nullable=True)    # expires after 30 days