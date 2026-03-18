from sqlalchemy import Column, Integer, String, Float, Text, Date, Time, ForeignKey, DateTime
from sqlalchemy.sql import func
from app.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    ceo_id = Column(Integer, ForeignKey("users.id"))
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

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    cv_text = Column(Text)
    cv_filename = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    job_id = Column(Integer, ForeignKey("jobs.id"))
    status = Column(String, default="applied")
    match_score = Column(Float)
    skill_gap = Column(Text)
    summary = Column(Text)
    applied_at = Column(DateTime, server_default=func.now())


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)
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

    id = Column(Integer, primary_key=True, index=True)
    interview_id = Column(Integer, ForeignKey("interviews.id"))
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    technical_score = Column(Float)
    communication_score = Column(Float)
    notes = Column(Text)
    submitted_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class FinalScore(Base):
    __tablename__ = "final_scores"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    job_id = Column(Integer, ForeignKey("jobs.id"))
    resume_score = Column(Float)
    technical_score = Column(Float)
    communication_score = Column(Float)
    final_score = Column(Float)
    ranking_category = Column(String)
    created_at = Column(DateTime, server_default=func.now())