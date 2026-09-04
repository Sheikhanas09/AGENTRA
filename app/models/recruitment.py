"""
Recruitment
───────────
═══════════════════════════════════════════════════════════
THIS MODULE HAD NO TENANT COLUMN AT ALL
═══════════════════════════════════════════════════════════
`jobs` knew which CEO created it. The five tables hanging off it —
candidates, applications, interviews, feedback, final scores — knew
nothing, so there was no way to scope a query on them even if a route
had tried, and none of them did:

    GET    /recruitment/applications/{job_id}     any job, any company
    GET    /recruitment/download-cv/{app_id}      any CV, any logged-in user
    DELETE /recruitment/jobs/{job_id}             any job, and its whole tree
    PUT    /recruitment/shortlist/{app_id}
    POST   /recruitment/hire/{app_id}

Every one of them is `company_id` now, which is also what makes the
guard in `utils/tenant_guard.py` cover them: it protects any mapped
class that has the column, so adding it here is what switched these six
tables on.

⚠ THE COLUMN HAS TO BE ON THE MODEL, NOT ONLY IN THE DATABASE.
The migration added it to Postgres first and these classes were left
alone for a while. Everything still ran, the rows had the right values
in them — and the ORM could not see the column, so the guard skipped
all six tables. It looked exactly like it was working. The live probe
in `check_tenancy.py` is what noticed, which is the reason that probe
hits real routes instead of reading the code.
"""

from sqlalchemy import (
    Column, Integer, String, Float, Text, Date, Time, ForeignKey, DateTime,
    LargeBinary, UniqueConstraint, Index,
)
from sqlalchemy.sql import func

from app.database import Base


# The tenant column, written once. Every table below gets the same one.
def _company_fk():
    return Column(
        Integer,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True,          # nullable so the migration could backfill;
        index=True,             # a NULL belongs to nobody and is visible
    )                           # to nobody, which is the safe way to fail


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_company", "company_id", "status"),
                      {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True)
    company_id = _company_fk()
    ceo_id = Column(Integer, ForeignKey("users.id"))
    # Kept for display and for the public job board. Not a link — the
    # company is `company_id`.
    company_name = Column(String)
    title = Column(String, nullable=False)
    department = Column(String)
    employment_type = Column(String)
    experience = Column(String)
    skills = Column(Text)
    salary_range = Column(String)
    full_description = Column(Text)
    keywords = Column(Text)
    status = Column(String, default="published")
    created_at = Column(DateTime, server_default=func.now())


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        # ═══ THE EMAIL USED TO BE UNIQUE ACROSS THE WHOLE TABLE ═══
        # `fetch-and-screen` looks a candidate up by email and REUSES
        # the row it finds — CV text, PDF, filename and all. With one
        # global row per email, the same person applying to a second
        # company would have handed that company the CV and screening
        # history belonging to the first.
        UniqueConstraint("company_id", "email",
                         name="uq_candidate_company_email"),
        Index("ix_candidates_company", "company_id"),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = _company_fk()
    full_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String)
    cv_text = Column(Text)
    cv_pdf = Column(LargeBinary, nullable=True)  # ← original PDF bytes
    cv_filename = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (Index("ix_applications_company", "company_id", "job_id"),
                      Index("ix_applications_offer_token", "offer_token_hash"),
                      {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True)
    company_id = _company_fk()
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    job_id = Column(Integer, ForeignKey("jobs.id"))
    status = Column(String, default="applied")
    match_score = Column(Float)
    skill_gap = Column(Text)
    summary = Column(Text)
    applied_at = Column(DateTime, server_default=func.now())

    # ══════════════════════════════════════════════
    # The CV AS SUBMITTED for this application
    # ══════════════════════════════════════════════
    # These lived only on `Candidate`, and `fetch-and-screen` overwrote
    # them every time that person's address turned up again:
    #
    #     existing_candidate.cv_text     = app_data['cv_text']
    #     existing_candidate.cv_pdf      = app_data.get('cv_pdf')
    #     existing_candidate.cv_filename = app_data['cv_filename']
    #
    # One person applying for two roles sends two different CVs — and
    # both applications then showed whichever arrived last. "View CV"
    # was right or wrong depending on the order the mailbox was read in,
    # which is exactly how it was reported: sometimes the correct CV,
    # sometimes not.
    #
    # A CV is not a property of a person. It is what they sent for THIS
    # role, on that day, and the score beside it was computed from it.
    # The candidate keeps a copy of the most recent one for listings;
    # anything that says "this application's CV" reads these.
    cv_text = Column(Text, nullable=True)
    cv_pdf = Column(LargeBinary, nullable=True)
    cv_filename = Column(String, nullable=True)

    # ══════════════════════════════════════════════
    # The offer link's authority
    # ══════════════════════════════════════════════
    # The candidate opens that link from their inbox with no account, so
    # the link IS the authorisation. It used to be this row's primary
    # key — `/recruitment/accept-offer/34` — which is a counter: anyone
    # could walk it and accept offers belonging to any company.
    #
    # Now the link carries a 256-bit random token and only its SHA-256
    # digest is stored, so a database dump yields digests rather than
    # working links. Single-use and time-bounded; see
    # `utils/offer_token.py` for why plain SHA-256 is right here.
    offer_token_hash = Column(String(64), nullable=True)
    offer_token_expires_at = Column(DateTime, nullable=True)
    offer_token_used_at = Column(DateTime, nullable=True)


class Interview(Base):
    __tablename__ = "interviews"
    __table_args__ = (Index("ix_interviews_company", "company_id"),
                      {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True)
    company_id = _company_fk()
    application_id = Column(Integer, ForeignKey("applications.id"))
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    job_id = Column(Integer, ForeignKey("jobs.id"))
    scheduled_date = Column(Date)
    scheduled_time = Column(Time)
    meeting_link = Column(String)
    interviewer_1 = Column(String)
    interviewer_2 = Column(String)
    status = Column(String, default="scheduled")
    created_at = Column(DateTime, server_default=func.now())


class InterviewFeedback(Base):
    __tablename__ = "interview_feedback"
    __table_args__ = (Index("ix_feedback_company", "company_id"),
                      {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True)
    company_id = _company_fk()
    interview_id = Column(Integer, ForeignKey("interviews.id"))
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    technical_score = Column(Float)
    communication_score = Column(Float)
    notes = Column(Text)
    submitted_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class FinalScore(Base):
    __tablename__ = "final_scores"
    __table_args__ = (Index("ix_final_scores_company", "company_id"),
                      {"extend_existing": True})

    id = Column(Integer, primary_key=True, index=True)
    company_id = _company_fk()
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    job_id = Column(Integer, ForeignKey("jobs.id"))
    resume_score = Column(Float)
    technical_score = Column(Float)
    communication_score = Column(Float)
    final_score = Column(Float)
    ranking_category = Column(String)
    created_at = Column(DateTime, server_default=func.now())
