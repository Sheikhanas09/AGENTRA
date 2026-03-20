import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
import json
import fitz
from app.database import get_db
from app.models.recruitment import Job, Candidate, Application
from app.schemas.recruitment import JobCreate, JobResponse, CandidateCreate
from app.utils.security import get_current_user
from app.agents.jd_generator import generate_job_description

router = APIRouter(prefix="/recruitment", tags=["Recruitment"])

def require_ceo(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "ceo":
        raise HTTPException(status_code=403, detail="Sirf CEO yeh kaam kar sakta hai")
    return current_user

def to_string(value) -> str:
    if isinstance(value, str): return value
    elif isinstance(value, dict):
        parts = []
        for v in value.values():
            if isinstance(v, str): parts.append(v)
            elif isinstance(v, list): parts.extend([str(i) for i in v])
        return " ".join(parts)
    elif isinstance(value, list): return " ".join([str(i) for i in value])
    return str(value) if value else ""


# ──── CEO Job Create ────
@router.post("/jobs/create")
def create_job(data: JobCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_ceo)):
    from app.models.user import User
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="CEO nahi mila")

    jd_result = generate_job_description(
        title=data.title, department=data.department,
        employment_type=data.employment_type, experience=data.experience,
        skills=data.skills, salary_range=data.salary_range,
        company_name=ceo.company_name or "Company",
        additional_info=data.additional_info, ceo_email=ceo.email
    )

    full_description = to_string(jd_result.get("full_description", ""))
    keywords = to_string(jd_result.get("keywords", ""))

    new_job = Job(
        ceo_id=current_user["user_id"], company_name=ceo.company_name,
        title=data.title, department=data.department,
        employment_type=data.employment_type, experience=data.experience,
        skills=data.skills, salary_range=data.salary_range,
        full_description=full_description, keywords=keywords, status="published"
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    return {
        "message": "Job successfully create ho gayi!",
        "job_id": new_job.id, "title": new_job.title,
        "full_description": new_job.full_description, "keywords": new_job.keywords
    }


# ──── Jobs list ────
@router.get("/jobs")
def get_jobs(db: Session = Depends(get_db), current_user: dict = Depends(require_ceo)):
    from app.models.user import User
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    jobs = db.query(Job).filter(Job.company_name == ceo.company_name, Job.status == "published").all()
    return {
        "total": len(jobs),
        "jobs": [{"id": j.id, "title": j.title, "department": j.department,
                  "employment_type": j.employment_type, "experience": j.experience,
                  "skills": j.skills, "salary_range": j.salary_range,
                  "full_description": j.full_description, "status": j.status,
                  "created_at": j.created_at} for j in jobs]
    }


# ──── Single job ────
@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")
    return {"id": job.id, "title": job.title, "department": job.department,
            "employment_type": job.employment_type, "experience": job.experience,
            "skills": job.skills, "salary_range": job.salary_range,
            "full_description": job.full_description, "company_name": job.company_name,
            "status": job.status, "created_at": job.created_at}


# ──── Job delete ────
@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_ceo)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")
    db.query(Application).filter(Application.job_id == job_id).delete()
    db.delete(job)
    db.commit()
    return {"message": "Job delete ho gayi"}


# ──── Public Jobs ────
@router.get("/public/jobs")
def get_public_jobs(db: Session = Depends(get_db)):
    from app.models.user import User
    jobs = db.query(Job).filter(Job.status == "published").all()
    result = []
    for job in jobs:
        ceo = db.query(User).filter(User.id == job.ceo_id).first()
        result.append({
            "id": job.id, "title": job.title, "department": job.department,
            "employment_type": job.employment_type, "experience": job.experience,
            "skills": job.skills, "salary_range": job.salary_range,
            "company_name": job.company_name, "full_description": job.full_description,
            "ceo_email": ceo.email if ceo else "", "created_at": job.created_at
        })
    return {"total": len(result), "jobs": result}


# ──── Single Public Job ────
@router.get("/public/jobs/{job_id}")
def get_public_job(job_id: int, db: Session = Depends(get_db)):
    from app.models.user import User
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")
    ceo = db.query(User).filter(User.id == job.ceo_id).first()
    return {"id": job.id, "title": job.title, "department": job.department,
            "employment_type": job.employment_type, "experience": job.experience,
            "skills": job.skills, "salary_range": job.salary_range,
            "company_name": job.company_name, "full_description": job.full_description,
            "ceo_email": ceo.email if ceo else "", "created_at": job.created_at}


# ──── Applications list ────
@router.get("/applications/{job_id}")
def get_applications(job_id: int, db: Session = Depends(get_db), current_user: dict = Depends(require_ceo)):
    applications = db.query(Application).filter(Application.job_id == job_id).all()
    result = []
    for app in applications:
        candidate = db.query(Candidate).filter(Candidate.id == app.candidate_id).first()
        result.append({
            "application_id": app.id, "candidate_id": app.candidate_id,
            "full_name": candidate.full_name if candidate else "—",
            "email": candidate.email if candidate else "—",
            "phone": candidate.phone if candidate else "—",
            "cv_filename": candidate.cv_filename if candidate else "—",
            "cv_text": candidate.cv_text if candidate else "",
            "status": app.status, "match_score": app.match_score,
            "skill_gap": app.skill_gap, "summary": app.summary,
            "applied_at": app.applied_at
        })
    return {"job_id": job_id, "total": len(result), "applications": result}


# ──── Gmail fetch + Auto Screen + Auto Shortlist ────
@router.post("/fetch-and-screen/{job_id}")
async def fetch_and_screen(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.agents.gmail_agent import fetch_job_application_emails
    from app.agents.cv_screening_agent import screen_cv

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")

    try:
        email_applications = fetch_job_application_emails(job_title=job.title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail fetch error: {str(e)}")

    saved = 0
    screened = 0
    shortlisted = 0

    for app_data in email_applications:
        existing_candidate = db.query(Candidate).filter(
            Candidate.email == app_data['email']
        ).first()

        if existing_candidate:
            existing_app = db.query(Application).filter(
                Application.candidate_id == existing_candidate.id,
                Application.job_id == job_id
            ).first()
            if existing_app:
                continue
            candidate = existing_candidate
        else:
            candidate = Candidate(
                full_name=app_data['name'], email=app_data['email'],
                phone="", cv_text=app_data['cv_text'],
                cv_filename=app_data['cv_filename']
            )
            db.add(candidate)
            db.flush()

        application = Application(
            candidate_id=candidate.id, job_id=job_id, status="applied"
        )
        db.add(application)
        db.flush()
        saved += 1

        if app_data['cv_text']:
            result = screen_cv(
                candidate_id=candidate.id, job_id=job.id,
                candidate_name=candidate.full_name, candidate_email=candidate.email,
                cv_text=app_data['cv_text'], job_title=job.title,
                job_description=job.full_description or "",
                job_keywords=job.keywords or "",
                job_experience=job.experience or "",
                job_skills=job.skills or ""
            )

            application.match_score = result["match_score"]
            application.skill_gap = result["skill_gap"]
            application.summary = result["summary"]

            if result["match_score"] >= 85:
                application.status = "shortlisted"
                shortlisted += 1
            else:
                application.status = "screened"

            screened += 1

    db.commit()

    return {
        "message": "Gmail fetch + AI screening complete!",
        "total_fetched": len(email_applications),
        "saved": saved,
        "screened": screened,
        "shortlisted": shortlisted
    }


# ──── Manual Shortlist ────
@router.put("/shortlist/{application_id}")
def manual_shortlist(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    application = db.query(Application).filter(
        Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application nahi mili")
    application.status = "shortlisted"
    db.commit()
    return {"message": "Candidate shortlisted ho gaya!"}


# ──── Employees list (interviewers ke liye) ────
@router.get("/employees")
def get_employees_for_interview(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    employees = db.query(User).filter(
        User.company_name == ceo.company_name,
        User.role == "employee",
        User.status == "active"
    ).all()
    return {
        "employees": [
            {
                "id": emp.id,
                "full_name": emp.full_name,
                "email": emp.email,
                "department": emp.department
            }
            for emp in employees
        ]
    }


# ──── Interview Schedule karo ────
@router.post("/schedule-interview")
def schedule_interview(
    application_id: int = Form(...),
    candidate_id: int = Form(...),
    job_id: int = Form(...),
    scheduled_date: str = Form(...),
    scheduled_time: str = Form(...),
    interviewer_1_email: str = Form(...),
    interviewer_2_email: str = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.recruitment import Interview
    from app.models.user import User
    from datetime import date, time, datetime
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    job = db.query(Job).filter(Job.id == job_id).first()
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    if not candidate or not job:
        raise HTTPException(status_code=404, detail="Candidate ya Job nahi mili")

    interview_date = date.fromisoformat(scheduled_date)
    interview_time = time.fromisoformat(scheduled_time)

    # ──── Time AM/PM format ────
    time_obj = datetime.strptime(scheduled_time, "%H:%M")
    formatted_time = time_obj.strftime("%I:%M %p")

    # ──── Date format ────
    date_obj = datetime.strptime(scheduled_date, "%Y-%m-%d")
    formatted_date = date_obj.strftime("%B %d, %Y")

    interview = Interview(
        application_id=application_id,
        candidate_id=candidate_id,
        job_id=job_id,
        scheduled_date=interview_date,
        scheduled_time=interview_time,
        interviewer_1=interviewer_1_email,
        interviewer_2=interviewer_2_email or "",
        status="scheduled"
    )
    db.add(interview)

    application = db.query(Application).filter(
        Application.id == application_id
    ).first()
    if application:
        application.status = "interview_scheduled"

    db.commit()
    db.refresh(interview)

    # ──── Email bhejo ────
    email_sent = False
    try:
        sender_email = "nirmal.naik1994@gmail.com"
        sender_password = os.getenv("GMAIL_APP_PASSWORD")

        recipients = [candidate.email, interviewer_1_email]
        if interviewer_2_email:
            recipients.append(interviewer_2_email)

        subject = f"Interview Scheduled — {job.title} at {job.company_name}"

        body = f"""Dear {candidate.full_name},

We are pleased to inform you that your interview has been scheduled for the position of {job.title} at {job.company_name}.

Interview Details:
━━━━━━━━━━━━━━━━━━━━━━
📅 Date:        {formatted_date}
⏰ Time:        {formatted_time}
💼 Position:    {job.title}
🏢 Company:     {job.company_name}
👥 Interviewers: {interviewer_1_email}{f', {interviewer_2_email}' if interviewer_2_email else ''}
━━━━━━━━━━━━━━━━━━━━━━

Please be prepared and join on time.

Best regards,
HR: {ceo.full_name}
Company: {job.company_name}"""

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = ", ".join(recipients)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, recipients, msg.as_string())
        server.quit()
        email_sent = True

    except Exception as e:
        print(f"Email error: {e}")
        email_sent = False

    return {
        "message": "Interview scheduled successfully!",
        "interview_id": interview.id,
        "scheduled_date": formatted_date,
        "scheduled_time": formatted_time,
        "email_sent": email_sent
    }