"""
The tenant itself
─────────────────
Until now there was no such thing as a company in this system. There was
only a CEO, and `company_id` meant "the CEO's user id". Everything else —
attendance, leave, payroll, chat, recruitment — hung off that number, and
an employee found their way to it by matching a *string*:

    ceo = db.query(User).filter(
        User.company_name == user.company_name,
        User.role == "ceo",
    ).first()

Three things break the moment a second company exists:

  1. TWO COMPANIES WITH THE SAME NAME MERGE.
     `.first()` picks one CEO. Every employee of the other company then
     resolves to that CEO's id, and from that instant they are reading
     and writing the first company's attendance, leave and payroll.
     Nothing errors. Nothing looks wrong.

  2. RENAMING A COMPANY ORPHANS EVERY EMPLOYEE.
     `PUT /ceo/profile` let the CEO edit `company_name`. The employees'
     `company_name` did not change with it, so the match returned None:
     no payroll, no leave types, no attendance, headcount zero. A
     cosmetic edit silently deleted a company's working system.

  3. NOTHING POINTED AT ANYTHING.
     `company_id` was a bare Integer on twenty tables with no foreign
     key. Any number could be written into it and the database would
     agree.

So the tenant becomes a row with its own identity, and every other table
points at that row. A name is then only a label: renaming a company is
an UPDATE of one string and nothing detaches, because nothing was ever
keyed on the string.

═══════════════════════════════════════════════════════════
WHY THE EXISTING IDS WERE KEPT
═══════════════════════════════════════════════════════════
The migration seeds this table with ids EQUAL TO the existing CEO user
ids (16, 19), so every `company_id` already stored in the other twenty
tables stays correct and not one row has to be rewritten. A rewrite of
twenty tables is the kind of migration that half-succeeds.

New companies come from a sequence starting at 1000. That is deliberate:
`company_id = 19` is legacy and happens to equal a user id, `company_id
= 1001` obviously is not a user id. During a change this size, being
able to tell the two apart by looking is worth more than tidiness.
"""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Index, CheckConstraint,
)

from app.database import Base

# ══════════════════════════════════════════════
# Tenant lifecycle
# ══════════════════════════════════════════════
# A whitelist, for the reason `utils/workforce.py` gives at length: a
# status test written as `!= "suspended"` silently promotes every status
# anybody adds later. Code asks `is_active()`, never compares strings.
STATUS_PENDING = "pending"      # signed up, waiting for the superadmin
STATUS_ACTIVE = "active"        # approved and running
STATUS_SUSPENDED = "suspended"  # switched off — nobody in it can log in
STATUS_CLOSED = "closed"        # gone for good; records kept

COMPANY_STATUSES = (
    STATUS_PENDING, STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_CLOSED,
)

# Only this one lets anybody through the door.
LIVE_STATUSES = (STATUS_ACTIVE,)


def name_is_taken(db, slug: str, exclude_company_id: int = None) -> bool:
    """
    Is this company name already registered?

    ═══ WHY THIS IS NOT A QUERY ═══
    The question has to see names the asker is not allowed to read, and
    `companies` now has a row-level-security policy. A plain
    `db.query(Company).filter(Company.slug == slug)` from a CEO's scoped
    session answers "no" every single time — so a duplicate slug reaches
    the unique index and surfaces as a raw IntegrityError instead of
    "that name is already registered".

    `agentra_company_name_taken` is a SECURITY DEFINER function created
    by `migrate_companies_rls.py`. It returns ONE BOOLEAN and never a
    row: somebody can learn that a name is taken — which is inherent to
    any name-availability check and is exactly what was asked — and can
    learn nothing else about that company.

    Called through SQLAlchemy's `func`, not `text()`, so it is a
    parameterised expression and the codebase keeps its "no raw SQL"
    property, which is what makes the ORM guard's coverage complete.
    """
    from sqlalchemy import func

    try:
        return bool(db.query(
            func.agentra_company_name_taken(slug, exclude_company_id)
        ).scalar())
    except Exception:                                           # noqa: BLE001
        # The function is missing (the migration has not been run yet).
        # Fall back to the direct query, which is correct whenever
        # `companies` has no policy — i.e. exactly the situation in which
        # the function does not exist.
        db.rollback()
        q = db.query(Company).filter(Company.slug == slug)
        if exclude_company_id is not None:
            q = q.filter(Company.id != exclude_company_id)
        return q.first() is not None


def existing_name_for(db, slug: str) -> str:
    """
    The display name behind a taken slug, when it can be read.

    Only used to make the error message friendlier. Under the policy a
    scoped session cannot see the row, so this returns None and the
    caller falls back to the name the user typed — the message is
    slightly less specific and nothing leaks.
    """
    row = db.query(Company).filter(Company.slug == slug).first()
    return row.name if row else None


def normalise_name(name: str) -> str:
    """
    The comparable form of a company name.

    "TechTribe", "techtribe", "  Tech  Tribe " must not become three
    tenants that a human reads as one. The unique index is on this
    value; the display name stays exactly as it was typed.
    """
    if not name:
        return ""
    return " ".join(str(name).strip().lower().split())


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','suspended','closed')",
            name="ck_company_status",
        ),
        Index("ix_company_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # What the company calls itself — free to change, never matched on.
    name = Column(String(200), nullable=False)

    # What the database matches on. Unique, so a name cannot be claimed
    # twice in any casing. See `normalise_name`.
    slug = Column(String(200), nullable=False, unique=True, index=True)

    status = Column(String(20), nullable=False, default=STATUS_PENDING)

    # ──── Who opened it ────
    # No ForeignKey: the CEO's user row carries `company_id` pointing
    # here, and a FK in the other direction as well would make the two
    # inserts circular. This is a record of who signed up, not a link
    # anything is resolved through.
    created_by = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    activated_at = Column(DateTime, nullable=True)
    suspended_at = Column(DateTime, nullable=True)

    # Shown to the CEO when their company is switched off, so the answer
    # to "why can nobody log in?" is in the system and not in an email.
    suspended_reason = Column(Text, nullable=True)

    def is_live(self) -> bool:
        """Whether anybody in this company may use the system at all."""
        return self.status in LIVE_STATUSES

    def __repr__(self) -> str:  # pragma: no cover - debugging only
        return f"<Company {self.id} {self.name!r} {self.status}>"
