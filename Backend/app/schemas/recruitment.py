from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import date, time


# ──── Job Schemas ────
class JobCreate(BaseModel):
    title: str
    department: str
    employment_type: str
    experience: str
    skills: str
    salary_range: str
    additional_info: str = ""  # ← add hua


class JobResponse(BaseModel):
    id: int
    title: str
    department: str
    employment_type: str
    experience: str
    skills: str
    salary_range: str
    full_description: Optional[str] = None
    keywords: Optional[str] = None
    status: str
    company_name: Optional[str] = None

    class Config:
        from_attributes = True


# ──── Candidate Schemas ────
class CandidateCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    job_id: int


# ──── Application Schemas ────
class ApplicationResponse(BaseModel):
    id: int
    candidate_id: int
    job_id: int
    status: str
    match_score: Optional[float] = None
    skill_gap: Optional[str] = None
    summary: Optional[str] = None

    class Config:
        from_attributes = True


# ──── Interview Schemas ────
class InterviewSchedule(BaseModel):
    application_id: int
    candidate_id: int
    job_id: int
    scheduled_date: date
    scheduled_time: time
    interviewer_1: Optional[str] = None
    interviewer_2: Optional[str] = None


class InterviewResponse(BaseModel):
    id: int
    candidate_id: int
    job_id: int
    scheduled_date: date
    scheduled_time: time
    meeting_link: Optional[str] = None
    interviewer_1: Optional[str] = None
    interviewer_2: Optional[str] = None
    status: str

    class Config:
        from_attributes = True


# ──── Feedback Schemas ────
class FeedbackCreate(BaseModel):
    interview_id: int
    candidate_id: int
    technical_score: float
    communication_score: float
    notes: Optional[str] = None