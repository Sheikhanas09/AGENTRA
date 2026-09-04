from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Index
from app.database import Base


class User(Base):

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_company", "company_id", "role", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)

    full_name = Column(String)
    email = Column(String, unique=True)

    password = Column(String)

    role = Column(String)  # superadmin / ceo / employee

    # ══════════════════════════════════════════════
    # Which tenant this person belongs to
    # ══════════════════════════════════════════════
    # THE tenant key. Everything a request is allowed to see is decided
    # from this column and nothing else.
    #
    # It replaces a string match. An employee used to find their company
    # by looking for a CEO whose `company_name` text was equal to their
    # own — so two companies with the same name merged into one, and a
    # CEO renaming their company detached every employee at once.
    #
    # NULL only for the superadmin, who belongs to no tenant. Every CEO
    # and every employee has one, and `utils/tenancy.py` refuses the
    # request if they do not.
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # ──── The company's name, kept only for display ────
    # Left in place because a lot of screens and emails read it, but it
    # is NO LONGER how anything is resolved. `companies.name` is the
    # real one; this is a copy that may lag. Never filter on it.
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