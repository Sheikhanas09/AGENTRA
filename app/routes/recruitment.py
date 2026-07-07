import os
import uuid
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


# ──── MCP Tools Call (Meet Link + Email) ────
# ──── MCP Tools Call (Meet Link + Email) ────
async def call_mcp_tools(
    candidate_name, candidate_email, job_title, company_name,
    scheduled_date, scheduled_time, interviewer_1_email,
    interviewer_2_email, hr_name, sender_email, sender_password
):
    import sys
    import os
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "..", "mcp_servers", "meeting_email_server.py")],
    )

    meet_link = ""
    email_sent = False

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # ──── Tool 1: Google Meet Link ────
                meet_result = await session.call_tool(
                    "generate_meeting_link",
                    {
                        "title": f"Interview — {job_title} at {company_name}",
                        "date": scheduled_date,
                        "time": scheduled_time,
                        "attendees": [
                            candidate_email,
                            interviewer_1_email,
                            interviewer_2_email or ""
                        ]
                    }
                )
                meet_link = meet_result.content[0].text

                # ──── Tool 2: Email Send ────
                email_result = await session.call_tool(
                    "send_interview_email",
                    {
                        "candidate_name": candidate_name,
                        "candidate_email": candidate_email,
                        "job_title": job_title,
                        "company_name": company_name,
                        "scheduled_date": scheduled_date,
                        "scheduled_time": scheduled_time,
                        "meeting_link": meet_link,
                        "interviewer_1_email": interviewer_1_email,
                        "interviewer_2_email": interviewer_2_email or "",
                        "hr_name": hr_name,
                        "sender_email": sender_email,
                        "sender_password": sender_password
                    }
                )
                email_sent = "successfully sent" in email_result.content[0].text.lower()

    except Exception as e:
        print(f"MCP error: {e}")
        unique_id = str(uuid.uuid4())[:8].upper()
        meet_link = f"https://meet.jit.si/Agentra-{unique_id}"
        email_sent = False

    return meet_link, email_sent


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
        "message": "Job successfully created!",
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
    from app.models.recruitment import Interview, InterviewFeedback, FinalScore

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")

    # ──── Order mein delete karo ────
    interviews = db.query(Interview).filter(Interview.job_id == job_id).all()
    interview_ids = [i.id for i in interviews]

    # 1. InterviewFeedback
    db.query(InterviewFeedback).filter(
        InterviewFeedback.interview_id.in_(interview_ids)
    ).delete(synchronize_session=False)

    # 2. Interviews
    db.query(Interview).filter(
        Interview.job_id == job_id
    ).delete(synchronize_session=False)

    # 3. FinalScores
    db.query(FinalScore).filter(
        FinalScore.job_id == job_id
    ).delete(synchronize_session=False)

    # 4. Applications
    db.query(Application).filter(
        Application.job_id == job_id
    ).delete(synchronize_session=False)

    # 5. Job
    db.delete(job)
    db.commit()

    return {"message": "Job aur sab related data delete ho gaya!"}

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
        raise HTTPException(status_code=404, detail="Job not found")

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
                if existing_app.status in ["hired", "accepted"]:
                    continue
                elif existing_app.status in ["screened", "shortlisted",
                                              "interview_scheduled", "applied"]:
                    # ──── CV update karo + pdf bhi ────
                    existing_candidate.cv_text = app_data['cv_text']
                    existing_candidate.cv_filename = app_data['cv_filename']
                    existing_candidate.cv_pdf = app_data.get('cv_pdf')  # ← add
                    existing_app.status = "applied"
                    existing_app.match_score = None
                    existing_app.skill_gap = None
                    existing_app.summary = None
                    db.flush()

                    if app_data['cv_text']:
                        result = screen_cv(
                            candidate_id=existing_candidate.id,
                            job_id=job.id,
                            candidate_name=existing_candidate.full_name,
                            candidate_email=existing_candidate.email,
                            cv_text=app_data['cv_text'],
                            job_title=job.title,
                            job_description=job.full_description or "",
                            job_keywords=job.keywords or "",
                            job_experience=job.experience or "",
                            job_skills=job.skills or ""
                        )
                        existing_app.match_score = result["match_score"]
                        existing_app.skill_gap = result["skill_gap"]
                        existing_app.summary = result["summary"]

                        if result["match_score"] >= 85:
                            existing_app.status = "shortlisted"
                            shortlisted += 1
                        else:
                            existing_app.status = "screened"
                        screened += 1
                        saved += 1
                    continue
                else:
                    continue

            candidate = existing_candidate
        else:
            # ──── Naya candidate ────
            candidate = Candidate(
                full_name=app_data['name'],
                email=app_data['email'],
                phone="",
                cv_text=app_data['cv_text'],
                cv_pdf=app_data.get('cv_pdf'),      # ← add
                cv_filename=app_data['cv_filename']
            )
            db.add(candidate)
            db.flush()

        # ──── Naya application banao ────
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
        raise HTTPException(status_code=404, detail="Application not found")
    application.status = "shortlisted"
    db.commit()
    return {"message": "Candidate shortlisted successfully!"}


# ──── Employees list ────
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
            {"id": emp.id, "full_name": emp.full_name,
             "email": emp.email, "department": emp.department}
            for emp in employees
        ]
    }


# ──── Interview Schedule karo (MCP se) ────
@router.post("/schedule-interview")
async def schedule_interview(
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

    candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    job = db.query(Job).filter(Job.id == job_id).first()
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    if not candidate or not job:
        raise HTTPException(status_code=404, detail="Candidate or Job not found")

    interview_date = date.fromisoformat(scheduled_date)
    interview_time = time.fromisoformat(scheduled_time)

    # ──── Time AM/PM format ────
    time_obj = datetime.strptime(scheduled_time, "%H:%M")
    formatted_time = time_obj.strftime("%I:%M %p")

    # ──── Date format ────
    date_obj = datetime.strptime(scheduled_date, "%Y-%m-%d")
    formatted_date = date_obj.strftime("%B %d, %Y")

    # ──── MCP se Meet Link + Email ────
    meet_link, email_sent = await call_mcp_tools(
        candidate_name=candidate.full_name,
        candidate_email=candidate.email,
        job_title=job.title,
        company_name=job.company_name,
        scheduled_date=formatted_date,
        scheduled_time=formatted_time,
        interviewer_1_email=interviewer_1_email,
        interviewer_2_email=interviewer_2_email,
        hr_name=ceo.full_name,
        sender_email="nirmal.naik1994@gmail.com",
        sender_password=os.getenv("GMAIL_APP_PASSWORD")
    )

    # ──── Interview save karo ────
    interview = Interview(
        application_id=application_id,
        candidate_id=candidate_id,
        job_id=job_id,
        scheduled_date=interview_date,
        scheduled_time=interview_time,
        meeting_link=meet_link,
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

    return {
        "message": "Interview scheduled successfully via MCP!",
        "interview_id": interview.id,
        "scheduled_date": formatted_date,
        "scheduled_time": formatted_time,
        "meeting_link": meet_link,
        "email_sent": email_sent
    }


# ──── Interviews list ────
@router.get("/interviews")
def get_interviews(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.recruitment import Interview, InterviewFeedback
    from app.models.user import User
    from datetime import date

    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    jobs = db.query(Job).filter(Job.company_name == ceo.company_name).all()
    job_ids = [j.id for j in jobs]

    interviews = db.query(Interview).filter(
        Interview.job_id.in_(job_ids)
    ).all()

    today = date.today()
    result = []

    for interview in interviews:
        candidate = db.query(Candidate).filter(
            Candidate.id == interview.candidate_id
        ).first()
        job = db.query(Job).filter(Job.id == interview.job_id).first()

        # ──── Feedback check karo ────
        feedback = db.query(InterviewFeedback).filter(
            InterviewFeedback.interview_id == interview.id
        ).first()

        # ──── Status determine karo ────
        if interview.status == "completed":
            status = "completed"
        elif interview.scheduled_date < today:
            if feedback:
                status = "completed"
            else:
                status = "pending"
        elif interview.scheduled_date == today:
            status = "today"
        else:
            status = "upcoming"

        result.append({
            "interview_id": interview.id,
            "candidate_name": candidate.full_name if candidate else "—",
            "candidate_email": candidate.email if candidate else "—",
            "job_title": job.title if job else "—",
            "scheduled_date": str(interview.scheduled_date),
            "scheduled_time": str(interview.scheduled_time),
            "meeting_link": interview.meeting_link,
            "interviewer_1": interview.interviewer_1,
            "interviewer_2": interview.interviewer_2,
            "status": status,
            "application_id": interview.application_id,
            "candidate_id": interview.candidate_id,
            "job_id": interview.job_id
        })

    return {"total": len(result), "interviews": result}

# ──── Interview Complete mark karo ────
@router.put("/interviews/{interview_id}/complete")
def mark_interview_complete(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.recruitment import Interview
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview nahi mili")
    interview.status = "completed"
    db.commit()
    return {"message": "Interview has been marked as completed!"}


# ──── Interview Feedback submit karo ────
@router.post("/interviews/{interview_id}/feedback")
def submit_feedback(
    interview_id: int,
    technical_score: float = Form(...),
    communication_score: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user) 
):
    from app.models.recruitment import Interview, InterviewFeedback
    from app.models.user import User

    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="The interview was not received.")

    submitter = db.query(User).filter(User.id == current_user["user_id"]).first()

    feedback = InterviewFeedback(
        interview_id=interview_id,
        candidate_id=interview.candidate_id,
        technical_score=technical_score,
        communication_score=communication_score,
        notes=notes,
        submitted_by=submitter.full_name if submitter else "CEO"
    )
    db.add(feedback)
    interview.status = "completed"
    db.commit()
    db.refresh(feedback)

    # ──── Agent 3: Evaluation trigger karo ────
    from app.agents.evaluation_agent import evaluate_candidate

    application = db.query(Application).filter(
        Application.id == interview.application_id
    ).first()

    eval_result = None
    if application:
        eval_result = evaluate_candidate(
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            resume_score=application.match_score or 0,
            technical_score=technical_score,
            communication_score=communication_score
        )

        from app.models.recruitment import FinalScore
        final = FinalScore(
            candidate_id=interview.candidate_id,
            job_id=interview.job_id,
            resume_score=application.match_score or 0,
            technical_score=technical_score,
            communication_score=communication_score,
            final_score=eval_result["final_score"],
            ranking_category=eval_result["ranking_category"]
        )
        db.add(final)
        db.commit()

    return {
        "message": "Feedback has been submitted and the evaluation is complete!",
        "feedback_id": feedback.id,
        "final_score": eval_result["final_score"] if eval_result else None,
        "ranking_category": eval_result["ranking_category"] if eval_result else None
    }
    # ──── Employee ke apne interviews ────
@router.get("/my-interviews")
def get_my_interviews(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    from app.models.recruitment import Interview, InterviewFeedback
    from app.models.user import User
    from datetime import date

    user = db.query(User).filter(User.id == current_user["user_id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nahi mila")

    interviews = db.query(Interview).filter(
        (Interview.interviewer_1 == user.email) |
        (Interview.interviewer_2 == user.email)
    ).all()

    today = date.today()
    result = []

    for interview in interviews:
        candidate = db.query(Candidate).filter(
            Candidate.id == interview.candidate_id
        ).first()
        job = db.query(Job).filter(Job.id == interview.job_id).first()

        feedback = db.query(InterviewFeedback).filter(
            InterviewFeedback.interview_id == interview.id
        ).first()

        if interview.status == "completed":
            status = "completed"
        elif interview.scheduled_date < today:
            status = "pending"
        elif interview.scheduled_date == today:
            status = "today"
        else:
            status = "upcoming"

        result.append({
            "interview_id": interview.id,
            "candidate_name": candidate.full_name if candidate else "—",
            "candidate_email": candidate.email if candidate else "—",
            "candidate_cv_text": candidate.cv_text if candidate else "",
            "job_title": job.title if job else "—",
            "company_name": job.company_name if job else "—",
            "scheduled_date": str(interview.scheduled_date),
            "scheduled_time": str(interview.scheduled_time),
            "meeting_link": interview.meeting_link,
            "interviewer_1": interview.interviewer_1,
            "interviewer_2": interview.interviewer_2,
            "status": status,
            "feedback_submitted": feedback is not None,
            "application_id": interview.application_id,
            "candidate_id": interview.candidate_id,
            "job_id": interview.job_id
        })

    return {"total": len(result), "interviews": result}
    # ──── Ranked Candidates fetch karo ────
@router.get("/ranked-candidates/{job_id}")
def get_ranked_candidates(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.recruitment import FinalScore, Interview

    final_scores = db.query(FinalScore).filter(
        FinalScore.job_id == job_id
    ).all()

    if not final_scores:
        return {"job_id": job_id, "ranked_list": [], "best_candidate": {}}

    candidates = []
    for fs in final_scores:
        candidate = db.query(Candidate).filter(
            Candidate.id == fs.candidate_id
        ).first()
        application = db.query(Application).filter(
            Application.candidate_id == fs.candidate_id,
            Application.job_id == job_id
        ).first()

        # ──── Interview details ────
        interview = db.query(Interview).filter(
            Interview.application_id == application.id
        ).first() if application else None

        candidates.append({
            "candidate_id": fs.candidate_id,
            "application_id": application.id if application else None,
            "full_name": candidate.full_name if candidate else "—",
            "email": candidate.email if candidate else "—",
            "resume_score": fs.resume_score,
            "technical_score": fs.technical_score,
            "communication_score": fs.communication_score,
            "final_score": fs.final_score,
            "ranking_category": fs.ranking_category,
            "hired": application.status in ["hired", "accepted"] if application else False,
            "rejected": application.status == "rejected" if application else False,  # ← add kiya
            # ──── Interview info ────
            "interview_date": str(interview.scheduled_date) if interview else "—",
            "interview_time": str(interview.scheduled_time) if interview else "—",
            "interviewer_1": interview.interviewer_1 if interview else "—",
            "interviewer_2": interview.interviewer_2 if interview else "",
            "meeting_link": interview.meeting_link if interview else "",
            "evaluated_at": str(fs.created_at) if fs else "—"
        })

    # ──── ranking agent call kro ────
    from app.agents.ranking_agent import rank_candidates
    result = rank_candidates(job_id=job_id, candidates=candidates)

    return {
        "job_id": job_id,
        "ranked_list": result["ranked_list"],
        "best_candidate": result["best_candidate"]
    }

# ──── CEO Candidate Approve karo ────
# ──── CEO Candidate Approve karo ────
@router.post("/hire/{application_id}")
async def hire_candidate(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from app.models.user import User
    from datetime import datetime

    application = db.query(Application).filter(
        Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="The application was not received.")

    # ──── Already hired check ────
    if application.status in ["hired", "accepted"]:
        raise HTTPException(
            status_code=400,
            detail="The candidate has already been hired!"
        )

    candidate = db.query(Candidate).filter(
        Candidate.id == application.candidate_id
    ).first()
    job = db.query(Job).filter(Job.id == application.job_id).first()
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    application.status = "hired"
    db.commit()

    # ──── Accept Link ────
    ngrok_url = os.getenv("NGROK_URL", "http://127.0.0.1:8000")
    accept_link = f"{ngrok_url}/recruitment/accept-offer/{application_id}?ngrok-skip-browser-warning=true"
    today = datetime.now().strftime("%B %d, %Y")

    # ──── MCP se Offer Letter Email ────
    email_sent = False
    try:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[os.path.join(os.path.dirname(__file__), "..", "mcp_servers", "meeting_email_server.py")],
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                result = await session.call_tool(
                    "send_offer_letter",
                    {
                        "candidate_name": candidate.full_name,
                        "candidate_email": candidate.email,
                        "job_title": job.title,
                        "company_name": job.company_name,
                        "salary_range": job.salary_range or "Competitive",
                        "ceo_name": ceo.full_name,
                        "accept_link": accept_link,
                        "offer_date": today,
                        "sender_email": "nirmal.naik1994@gmail.com",
                        "sender_password": os.getenv("GMAIL_APP_PASSWORD")
                    }
                )
                email_sent = "sent" in result.content[0].text.lower()

    except Exception as e:
        print(f"MCP offer error: {e}")
        email_sent = False

    return {
        "message": "The candidate has been hired! The offer letter has been sent!",
        "application_id": application_id,
        "email_sent": email_sent
    }

# ──── Offer Accept karo ────
@router.get("/accept-offer/{application_id}")
async def accept_offer(
    application_id: int,
    db: Session = Depends(get_db)
):
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from datetime import datetime, timedelta
    from fastapi.responses import HTMLResponse

    application = db.query(Application).filter(
        Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application nahi mili")

    if application.status != "hired":
        html = """<!DOCTYPE html><html><head><title>Already Processed</title>
        <style>body{font-family:'Segoe UI',sans-serif;background:#0a0a0a;min-height:100vh;display:flex;align-items:center;justify-content:center;}
        .card{background:#111;border:1px solid rgba(250,204,21,0.4);border-radius:20px;padding:48px;text-align:center;max-width:500px;}
        h1{color:#facc15;font-size:24px;margin-bottom:12px;}p{color:#9ca3af;font-size:16px;}</style></head>
        <body><div class="card"><div style="font-size:64px">⚠️</div>
        <h1>Already Processed</h1><p>This offer has already been accepted or is no longer valid.</p>
        </div></body></html>"""
        response = HTMLResponse(content=html)
        response.headers["ngrok-skip-browser-warning"] = "true"
        return response

    application.status = "accepted"
    db.commit()

    candidate = db.query(Candidate).filter(
        Candidate.id == application.candidate_id
    ).first()
    job = db.query(Job).filter(Job.id == application.job_id).first()

    joining_date = (datetime.now() + timedelta(weeks=2)).strftime("%B %d, %Y")

    # ──── MCP se Onboarding Email ────
    try:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[os.path.join(os.path.dirname(__file__), "..", "mcp_servers", "meeting_email_server.py")],
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool(
                    "send_onboarding_email",
                    {
                        "candidate_name": candidate.full_name,
                        "candidate_email": candidate.email,
                        "job_title": job.title,
                        "company_name": job.company_name,
                        "joining_date": joining_date,
                        "sender_email": "nirmal.naik1994@gmail.com",
                        "sender_password": os.getenv("GMAIL_APP_PASSWORD")
                    }
                )
    except Exception as e:
        print(f"MCP onboarding error: {e}")

    # ──── HTML Page ────
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Offer Accepted!</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', sans-serif;
            background: #0a0a0a;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .card {{
            background: #111;
            border: 1px solid rgba(5, 220, 127, 0.4);
            border-radius: 20px;
            padding: 48px;
            text-align: center;
            max-width: 500px;
            width: 100%;
            box-shadow: 0 0 40px rgba(5, 220, 127, 0.2);
        }}
        .icon {{ font-size: 64px; margin-bottom: 20px; }}
        h1 {{ color: #05DC7F; font-size: 28px; margin-bottom: 12px; }}
        p {{ color: #9ca3af; font-size: 15px; line-height: 1.6; margin-bottom: 8px; }}
        .highlight {{ color: white; font-weight: 600; }}
        .joining {{
            background: rgba(5, 220, 127, 0.1);
            border: 1px solid rgba(5, 220, 127, 0.3);
            border-radius: 12px;
            padding: 20px;
            margin: 24px 0;
        }}
        .joining-label {{ color: #05DC7F; font-weight: 600; font-size: 14px; }}
        .joining-date {{ color: white; font-size: 22px; font-weight: 700; margin-top: 8px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">🎉</div>
        <h1>Offer Accepted!</h1>
        <p>Congratulations <span class="highlight">{candidate.full_name}</span>!</p>
        <p>You have successfully accepted the offer for</p>
        <p><span class="highlight">{job.title}</span> at <span class="highlight">{job.company_name}</span></p>
        <div class="joining">
            <p class="joining-label">📅 Your Joining Date</p>
            <p class="joining-date">{joining_date}</p>
        </div>
        <p>Onboarding details have been sent to</p>
        <p><span class="highlight">{candidate.email}</span></p>
    </div>
</body>
</html>"""

    response = HTMLResponse(content=html_content)
    response.headers["ngrok-skip-browser-warning"] = "true"
    return response

@router.get("/all-employees")
def get_all_employees(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User
    from app.models.recruitment import FinalScore

    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    jobs = db.query(Job).filter(Job.company_name == ceo.company_name).all()
    job_ids = [j.id for j in jobs]

    result = []

    # ──── Type 1: Hired/Accepted candidates ────
    applications = db.query(Application).filter(
        Application.job_id.in_(job_ids),
        Application.status.in_(["hired", "accepted"])
    ).all()

    for app in applications:
        candidate = db.query(Candidate).filter(Candidate.id == app.candidate_id).first()
        job = db.query(Job).filter(Job.id == app.job_id).first()
        result.append({
            "id": f"candidate_{app.id}",
            "application_id": app.id,
            "full_name": candidate.full_name if candidate else "—",
            "email": candidate.email if candidate else "—",
            "department": job.department if job else "—",
            "joining_date": str(app.applied_at)[:10] if app.applied_at else "—",
            "status": app.status,
            "employee_type": "hired",
        })

    # ──── Type 2: Manually created employees ────
    created_employees = db.query(User).filter(
        User.company_name == ceo.company_name,
        User.role == "employee",
        User.status != "fired"
    ).all()

    for emp in created_employees:
        result.append({
            "id": f"user_{emp.id}",
            "application_id": None,
            "user_id": emp.id,
            "full_name": emp.full_name,
            "email": emp.email,
            "department": emp.department or "—",
            "joining_date": str(emp.joining_date) if emp.joining_date else "—",
            "status": emp.status or "active",
            "employee_type": "created",
        })

    return {"total": len(result), "employees": result}

    # ──── Hired Employees Only ────
@router.get("/hired-employees")
def get_hired_employees(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User
    from app.models.recruitment import FinalScore

    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    jobs = db.query(Job).filter(Job.company_name == ceo.company_name).all()
    job_ids = [j.id for j in jobs]

    applications = db.query(Application).filter(
        Application.job_id.in_(job_ids),
        Application.status.in_(["hired", "accepted"])
    ).all()

    result = []
    for app in applications:
        candidate = db.query(Candidate).filter(Candidate.id == app.candidate_id).first()
        job = db.query(Job).filter(Job.id == app.job_id).first()
        final_score = db.query(FinalScore).filter(
            FinalScore.candidate_id == app.candidate_id,
            FinalScore.job_id == app.job_id
        ).first()

        result.append({
            "id": f"candidate_{app.id}",
            "application_id": app.id,
            "full_name": candidate.full_name if candidate else "—",
            "email": candidate.email if candidate else "—",
            "department": job.department if job else "—",
            "job_title": job.title if job else "—",
            "status": app.status,
            "final_score": final_score.final_score if final_score else None,
            "ranking_category": final_score.ranking_category if final_score else "—",
            "employee_type": "hired",
            "hired_at": str(app.applied_at)[:10] if app.applied_at else "—",
        })

    return {"total": len(result), "employees": result}

# ──── Employee Fire/Remove karo ────
@router.put("/fire-employee/{employee_id}")
def fire_employee(
    employee_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User

    # ──── Hired candidate fire karo ────
    if employee_id.startswith("candidate_"):
        app_id = int(employee_id.replace("candidate_", ""))
        application = db.query(Application).filter(
            Application.id == app_id
        ).first()
        if not application:
            raise HTTPException(status_code=404, detail="Application nahi mili")
        if application.status not in ["hired", "accepted"]:
            raise HTTPException(status_code=400, detail="Yeh employee hired nahi hai")
        application.status = "fired"
        db.commit()
        return {"message": "Hired employee remove ho gaya!"}

    # ──── Manually created employee fire karo ────
    elif employee_id.startswith("user_"):
        user_id = int(employee_id.replace("user_", ""))
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User nahi mila")
        user.status = "fired"
        db.commit()
        return {"message": "Created employee remove ho gaya!"}

    else:
        raise HTTPException(status_code=400, detail="Invalid employee ID")
    # ──── Dashboard Stats ────
@router.get("/dashboard-stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User
    from app.models.recruitment import Interview

    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    jobs = db.query(Job).filter(Job.company_name == ceo.company_name).all()
    job_ids = [j.id for j in jobs]

    # ──── Stats ────
    total_jobs = len(jobs)
    active_jobs = len(jobs)

    total_applied = db.query(Application).filter(
        Application.job_id.in_(job_ids)
    ).count()

    total_shortlisted = db.query(Application).filter(
        Application.job_id.in_(job_ids),
        Application.status == "shortlisted"
    ).count()

    total_interviews = db.query(Interview).filter(
        Interview.job_id.in_(job_ids)
    ).count()

    total_hired = db.query(Application).filter(
        Application.job_id.in_(job_ids),
        Application.status.in_(["hired", "accepted"])
    ).count()

    # ──── Unique departments ────
    dept_list = list(set([j.department for j in jobs if j.department]))

    return {
        "total_employees": total_hired,
        "total_departments": len(dept_list),
        "active_openings": active_jobs,
        "pipeline": {
            "applied": total_applied,
            "shortlisted": total_shortlisted,
            "interviews": total_interviews,
            "hired": total_hired
        }
    }
    # ──── CV PDF Download ────
@router.get("/download-cv/{application_id}")
def download_cv(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    from fastapi.responses import Response

    application = db.query(Application).filter(
        Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application nahi mili")

    candidate = db.query(Candidate).filter(
        Candidate.id == application.candidate_id
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate nahi mila")

    # ──── Original PDF hai to wahi bhejo ────
    if candidate.cv_pdf:
        filename = f"{candidate.full_name or 'CV'}_CV.pdf".replace(" ", "_")
        return Response(
            content=candidate.cv_pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
    
    # ──── Fallback: text se PDF banao ────
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(candidate.full_name or "Candidate", styles['Title']))
    story.append(Paragraph(candidate.email or "", styles['Normal']))
    story.append(Spacer(1, 12))

    if candidate.cv_text:
        for line in candidate.cv_text.split('\n'):
            if line.strip():
                try:
                    story.append(Paragraph(line.strip(), styles['Normal']))
                    story.append(Spacer(1, 4))
                except:
                    pass

    doc.build(story)
    buffer.seek(0)

    filename = f"{candidate.full_name or 'CV'}_CV.pdf".replace(" ", "_")
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )
    # ──── Candidate Reject karo ────
@router.post("/reject/{application_id}")
async def reject_candidate(
    application_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from app.models.user import User

    application = db.query(Application).filter(
        Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application nahi mili")

    if application.status in ["hired", "accepted"]:
        raise HTTPException(status_code=400, detail="Already hired candidate reject nahi ho sakta")

    candidate = db.query(Candidate).filter(
        Candidate.id == application.candidate_id
    ).first()
    job = db.query(Job).filter(Job.id == application.job_id).first()
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    # ──── Status update ────
    application.status = "rejected"
    db.commit()

    # ──── MCP se Rejection Email ────
    email_sent = False
    try:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[os.path.join(os.path.dirname(__file__), "..", "mcp_servers", "meeting_email_server.py")],
        )
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "send_rejection_email",
                    {
                        "candidate_name": candidate.full_name,
                        "candidate_email": candidate.email,
                        "job_title": job.title,
                        "company_name": job.company_name,
                        "ceo_name": ceo.full_name,
                        "sender_email": "nirmal.naik1994@gmail.com",
                        "sender_password": os.getenv("GMAIL_APP_PASSWORD")
                    }
                )
                email_sent = "sent" in result.content[0].text.lower()
    except Exception as e:
        print(f"MCP rejection error: {e}")

    return {
        "message": "Candidate rejected! Email bheja gaya!",
        "application_id": application_id,
        "email_sent": email_sent
    }