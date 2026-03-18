from sqlalchemy import Column, Integer, String, Date, DateTime  # ← DateTime add karo
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

    department = Column(String)

    joining_date = Column(Date)

    status = Column(String)  # pending / approved / active / inactive

    # ──── Yeh naye add karo ────
    approved_at = Column(DateTime, nullable=True)   # approve hone ka time
    expires_at = Column(DateTime, nullable=True)    # 30 days baad expire