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
from app.utils.tenancy import Tenant, get_tenant, require_ceo, public_scope
from app.utils.google_auth import GoogleNotConnected, credentials_for
from app.utils.offer_token import (
    build_link as build_offer_link, issue as issue_offer_token,
    redeem as redeem_offer_token,
)
from app.agents.jd_generator import generate_job_description

router = APIRouter(prefix="/recruitment", tags=["Recruitment"])

# ══════════════════════════════════════════════
# `require_ceo` is imported — this file's own was the widest hole
# ══════════════════════════════════════════════
# It read, in full:
#
#     def require_ceo(current_user = Depends(get_current_user)):
#         if current_user["role"] != "ceo":
#             raise HTTPException(403, "Only the CEO can do this")
#         return current_user
#
# "Is a CEO" — of ANY company. Every route below then took an id
# straight out of the URL and acted on it, so one company's CEO could
# read another's applications and CVs, shortlist their candidates,
# send offers, and DELETE a job together with all of its interviews,
# feedback and scores.
#
# The shared version scopes the session to the caller's own company, and
# the guard in `utils/tenant_guard.py` then adds `company_id = <theirs>`
# to every query in this file. Another company's job id now simply is
# not found — a 404, which is also the right answer, because a 403
# would confirm the id exists.

def _offer_page(title: str, body: str, good: bool) -> str:
    """
    What a candidate sees. One renderer for both offer routes, so a
    refusal and a success cannot drift into looking different in ways
    that leak which one happened.
    """
    colour = "#05DC7F" if good else "#facc15"
    return f"""<!DOCTYPE html><html><head><title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0a0a;
 min-height:100vh;display:flex;align-items:center;justify-content:center;
 margin:0;padding:24px}}
.card{{background:#111;border:1px solid {colour}66;border-radius:20px;
 padding:44px;text-align:center;max-width:520px}}
h1{{color:{colour};font-size:23px;margin:0 0 12px}}
p{{color:#9ca3af;font-size:15px;line-height:1.6;margin:0}}</style></head>
<body><div class="card"><div style="font-size:56px">{'&#10003;' if good else '&#9888;'}</div>
<h1>{title}</h1><p>{body}</p></div></body></html>"""


def _google_token(db, company_id: int) -> str:
    """
    This company's Google credentials as JSON, for the MCP subprocess.

    Loaded and refreshed HERE rather than inside the MCP server: that
    process has no database, and giving it one would create a second
    place that decides which company a call belongs to.
    """
    return credentials_for(db, company_id).to_json()


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
    interviewer_2_email, hr_name, google_token
):
    """
    `sender_email` and `sender_password` used to be the same hard-coded
    Gmail address and the one shared app password on every call. It is
    now the calling company's own OAuth credentials, so the Meet link is
    made on THEIR calendar and the invitation arrives from THEIR address.
    """
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
                        ],
                        "google_token": google_token
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
                        "google_token": google_token
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
        raise HTTPException(status_code=404, detail="CEO not found")

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
    jobs = db.query(Job).filter(Job.status == "published").all()
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
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    """
    Had no authentication at all — not even a login. Any id, from
    anywhere, including unpublished roles. The public portal has its own
    route above; this one is for the company that owns the job.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
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
        raise HTTPException(status_code=404, detail="Job not found")

    # ──── Delete in order ────
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

    return {"message": "The job and all related data have been deleted"}

# ──── Public Jobs ────
@router.get("/public/jobs")
def get_public_jobs(
    db: Session = Depends(get_db),
    _: None = Depends(public_scope),
):
    """
    The job board. Genuinely across companies — that is what a job board
    is — and only ever `status == "published"`.
    """
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
def get_public_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(public_scope),
):
    from app.models.user import User
    # Published only. Without this a draft or closed role — salary band
    # and all — was readable by anybody who guessed the id.
    job = db.query(Job).filter(
        Job.id == job_id, Job.status == "published").first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    ceo = db.query(User).filter(User.id == job.ceo_id).first()
    return {"id": job.id, "title": job.title, "department": job.department,
            "employment_type": job.employment_type, "experience": job.experience,
            "skills": job.skills, "salary_range": job.salary_range,
            "company_name": job.company_name, "full_description": job.full_description,
            "ceo_email": ceo.email if ceo else "", "created_at": job.created_at}


# ──── Applications list ────
@router.get("/applications/{job_id}")
def get_applications(job_id: int, db: Session = Depends(get_db),
                     current_user: Tenant = Depends(require_ceo)):
    # The guard already keeps another company's applications out of the
    # list, so this leaked nothing — it answered `200 {"total": 0}`,
    # which says "that job exists and has no applicants". Confirming a
    # job id and then describing it is still telling somebody something.
    if not db.query(Job).filter(Job.id == job_id).first():
        raise HTTPException(status_code=404, detail="Job not found")

    applications = db.query(Application).filter(Application.job_id == job_id).all()
    result = []
    for app in applications:
        candidate = db.query(Candidate).filter(Candidate.id == app.candidate_id).first()
        result.append({
            "application_id": app.id, "candidate_id": app.candidate_id,
            "full_name": candidate.full_name if candidate else "—",
            "email": candidate.email if candidate else "—",
            "phone": candidate.phone if candidate else "—",
            # The application's own CV — see `/download-cv` for why
            # the candidate's copy is only a fallback.
            "cv_filename": (app.cv_filename
                            or (candidate.cv_filename if candidate else None)
                            or "—"),
            "cv_text": (app.cv_text
                        or (candidate.cv_text if candidate else "")
                        or ""),
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
    from app.agents.gmail_agent import (
        fetch_job_application_emails, _looks_like_a_person)
    from app.agents.cv_screening_agent import screen_cv

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        email_applications = fetch_job_application_emails(
            db, current_user["company_id"], job_title=job.title)
    except GoogleNotConnected as e:
        # Not a server error — an unconnected company, and the CEO can
        # fix it themselves. It used to fall through to a shared inbox.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail fetch error: {str(e)}")

    # ══════════════════════════════════════════════
    # ONE EMAIL PER PERSON
    # ══════════════════════════════════════════════
    # A candidate who sends a reminder, or replies on the thread, or
    # simply applies twice, is several messages and one applicant. Every
    # one of them used to be screened separately — the same person, the
    # same job, re-scored against an LLM once per message. A real fetch
    # reported "Fetched: 11, Screened: 11" and then listed two people,
    # which reads like nine applicants went missing. Nine did not exist.
    #
    # Gmail returns newest first, so the first message from an address
    # is their most recent CV, and that is the one kept.
    emails_found = len(email_applications)
    seen = set()
    deduped = []
    for app_data in email_applications:
        key = (app_data.get('email') or '').strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(app_data)
    email_applications = deduped
    duplicates = emails_found - len(email_applications)

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
                    # ──── Update the CV and the PDF ────
                    # On the application, so re-fetching for a second
                    # role stops overwriting the first role's CV.
                    existing_app.cv_text = app_data['cv_text']
                    existing_app.cv_filename = app_data['cv_filename']
                    existing_app.cv_pdf = app_data.get('cv_pdf')
                    # The candidate keeps the most recent one for the
                    # places that show a person rather than an
                    # application.
                    existing_candidate.cv_text = app_data['cv_text']
                    existing_candidate.cv_filename = app_data['cv_filename']
                    existing_candidate.cv_pdf = app_data.get('cv_pdf')

                    # ──── And the name ────
                    # This branch never touched `full_name`, so a
                    # candidate filed under a bad name stayed under it
                    # no matter how many times the CV was re-read. Two
                    # people sat in the dashboard as "Wise Tech" — the
                    # employer named at the top of their CV — while the
                    # extractor, by then fixed, returned MUHAMMAD ANAS
                    # for the very same PDF on every run.
                    #
                    # Only accept a name-shaped replacement: when the
                    # PDF cannot be read the extractor falls back to the
                    # part of the address before the @, and overwriting
                    # a real name with "bunnyhazel9001" would be worse
                    # than leaving it alone.
                    fresh_name = (app_data.get('name') or '').strip()
                    if fresh_name and fresh_name != existing_candidate.full_name:
                        if (_looks_like_a_person(fresh_name)
                                or not _looks_like_a_person(
                                    existing_candidate.full_name or '')):
                            existing_candidate.full_name = fresh_name

                    existing_app.status = "applied"
                    existing_app.match_score = None
                    existing_app.skill_gap = None
                    existing_app.summary = None
                    db.flush()

                    if app_data['cv_text']:
                        result = screen_cv(
                            company_id=current_user["company_id"],
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
            # ──── A new candidate ────
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

        # ──── Create a new application ────
        application = Application(
            candidate_id=candidate.id, job_id=job_id, status="applied",
            # The CV AS SUBMITTED for this role. `match_score` below is
            # computed from this text, so the two have to stay together
            # — a score whose CV has since been replaced refers to a
            # document nobody can open any more.
            cv_text=app_data['cv_text'],
            cv_pdf=app_data.get('cv_pdf'),
            cv_filename=app_data['cv_filename'],
        )
        db.add(application)
        db.flush()
        saved += 1

        if app_data['cv_text']:
            result = screen_cv(
                company_id=current_user["company_id"],
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
        # `total_fetched` is APPLICANTS, which is what the dashboard
        # label means and what the list underneath it shows. The raw
        # message count is reported separately rather than dropped —
        # the two differing is normal, not an error.
        "total_fetched": len(email_applications),
        "emails_found": emails_found,
        "duplicates_skipped": duplicates,
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


# ──── Schedule an interview (via MCP) ────
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

    # ──── Meet link + email via MCP ────
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
        google_token=_google_token(db, current_user["company_id"]),
    )

    # ──── Save the interview ────
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
    jobs = db.query(Job).all()
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

        # ──── Check the feedback ────
        feedback = db.query(InterviewFeedback).filter(
            InterviewFeedback.interview_id == interview.id
        ).first()

        # ──── Determine the status ────
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

# ──── Mark an interview complete ────
@router.put("/interviews/{interview_id}/complete")
def mark_interview_complete(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.recruitment import Interview
    interview = db.query(Interview).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    interview.status = "completed"
    db.commit()
    return {"message": "Interview has been marked as completed!"}


# ──── Submit interview feedback ────
@router.post("/interviews/{interview_id}/feedback")
def submit_feedback(
    interview_id: int,
    technical_score: float = Form(...),
    communication_score: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    """
    An interviewer scores a candidate.

    `get_current_user` proved WHO was asking and nothing about which
    company, so an employee could post feedback — and a final score —
    onto another company's interview by its id. The scoped session now
    means that interview simply is not there.
    """
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

    # ──── Agent 3: trigger the evaluation ────
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
    # ──── The employee's own interviews ────
@router.get("/my-interviews")
def get_my_interviews(
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    """
    The panels this employee sits on.

    Interviews are matched to an interviewer BY EMAIL, and email is
    unique across the whole system, so nothing crossed here in practice.
    It is scoped anyway: "no known way in" is a statement about the
    columns as they are today, and this route reads candidate names, CVs
    and job titles.
    """
    from app.models.recruitment import Interview, InterviewFeedback
    from app.models.user import User
    from datetime import date

    user = db.query(User).filter(User.id == current_user["user_id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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

        # The CV this interview is actually about. An interviewer
        # reading the candidate's latest CV instead of the one submitted
        # for THIS role is the same bug as "View CV" showing the wrong
        # document — with worse consequences, because they are about to
        # score somebody on it.
        interview_app = db.query(Application).filter(
            Application.id == interview.application_id
        ).first()

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
            "candidate_cv_text": (
                (interview_app.cv_text if interview_app else None)
                or (candidate.cv_text if candidate else "")
                or ""),
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
    # ──── Fetch the ranked candidates ────
@router.get("/ranked-candidates/{job_id}")
def get_ranked_candidates(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.recruitment import FinalScore, Interview

    # Same reason as `/applications/{job_id}`: the guard kept another
    # company's scores out, but answering 200 for their job id still
    # confirmed it exists.
    if not db.query(Job).filter(Job.id == job_id).first():
        raise HTTPException(status_code=404, detail="Job not found")

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

# ──── CEO approves a candidate ────
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

    # ══════════════════════════════════════════════
    # The accept link
    # ══════════════════════════════════════════════
    # This was `f".../accept-offer/{application_id}"` — the row's primary
    # key, in a public URL, as the only thing authorising acceptance.
    # Anybody could count upwards and accept offers belonging to any
    # company. The link now carries a 256-bit single-use token and the
    # database keeps only its hash; see `utils/offer_token.py`.
    ngrok_url = os.getenv("NGROK_URL", "http://127.0.0.1:8000")
    offer_token = issue_offer_token(db, application)
    accept_link = build_offer_link(ngrok_url, offer_token)
    today = datetime.now().strftime("%B %d, %Y")

    # ──── Offer letter email via MCP ────
    email_sent = False
    try:
        google_token = _google_token(db, current_user["company_id"])
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
                        "google_token": google_token
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

# ──── Accept the offer ────
@router.get("/accept-offer/{application_id}")
async def accept_offer_retired(
    application_id: int,
    _: None = Depends(public_scope),
):
    """
    The old link shape. It does nothing now, deliberately.

    ═══ WHAT IT USED TO DO ═══
    Read the application by the id in the URL and, if its status was
    `hired`, set it to `accepted` and send the onboarding email. Public,
    unauthenticated, and keyed on a counter — so walking 1, 2, 3…
    accepted offers belonging to any company in the system.

    It is kept rather than deleted so that an offer email already in
    somebody's inbox gets an explanation instead of a 404, and it is
    inert rather than redirected: honouring the old id even once would
    leave the hole open.
    """
    from fastapi.responses import HTMLResponse

    return HTMLResponse(
        content=_offer_page(
            "This link has expired",
            "Offer links were replaced with secure, single-use ones. "
            "Please ask your contact at the company to send you a new "
            "offer email.", False),
        headers={"ngrok-skip-browser-warning": "true"})


# ──── Accept the offer (the real one) ────
@router.get("/offer/{token}")
async def accept_offer(
    token: str,
    db: Session = Depends(get_db),
    _: None = Depends(public_scope),
):
    """
    Opened by the candidate from their offer email.

    There is no session and no company to scope to — the candidate has
    no account — so the LINK is the authorisation. `utils/offer_token.py`
    holds the reasoning; in short: 256 random bits, stored only as a
    SHA-256 digest, usable once, and expiring.

    Every failure returns the same page. Telling an unauthenticated
    caller whether a token was unknown, expired or already used would
    confirm which tokens exist.
    """
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from datetime import datetime, timedelta
    from fastapi.responses import HTMLResponse

    application, reason = redeem_offer_token(db, token)

    # ═══ A SWITCHED-OFF COMPANY CANNOT ONBOARD ANYBODY ═══
    # The tenant guard has nothing to say on this route — it is public,
    # so there is no session to scope. But accepting an offer starts an
    # employment, and a suspended company is one that has been stopped.
    # An outstanding link would otherwise quietly create a new employee
    # inside a tenant that nobody can sign in to.
    if application is not None:
        from app.models.company import Company, LIVE_STATUSES
        company = db.query(Company).filter(
            Company.id == application.company_id).first()
        if not company or company.status not in LIVE_STATUSES:
            application, reason = None, "company_not_live"

    if not application:
        # Logged with the reason; the caller is told nothing.
        print(f"[offer] refused a token: {reason}")
        return HTMLResponse(
            content=_offer_page(
                "This link is no longer valid",
                "It may have already been used, or it may have expired. "
                "Please ask your contact at the company for a new offer "
                "email.", False),
            headers={"ngrok-skip-browser-warning": "true"})

    application_id = application.id

    application.status = "accepted"
    db.commit()

    # ═══ NO TENANT ON THIS REQUEST, SO THE ROW DECIDES ═══
    # The candidate opens this from their offer email; there is no login
    # and no company on the session. The company is the one that OWNS
    # THE APPLICATION, read from the row rather than from anything the
    # caller supplied — so the onboarding email goes out through that
    # company's Google account and no other.
    google_token = None
    try:
        google_token = _google_token(db, application.company_id)
    except GoogleNotConnected:
        # They accepted; that has been recorded. The onboarding email
        # simply will not go until the company connects Google, and the
        # acceptance page below still shows.
        pass

    candidate = db.query(Candidate).filter(
        Candidate.id == application.candidate_id
    ).first()
    job = db.query(Job).filter(Job.id == application.job_id).first()

    joining_date = (datetime.now() + timedelta(weeks=2)).strftime("%B %d, %Y")

    # ──── Onboarding email via MCP ────
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
                        "google_token": google_token
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
    jobs = db.query(Job).all()
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
    # Whitelist, not "!= fired" — see utils/workforce.py
    from app.utils.workforce import EMPLOYED, NOT_YET
    created_employees = db.query(User).filter(
        # company scoping is applied by the tenant guard
        User.role == "employee",
        User.status.in_(tuple(EMPLOYED) + tuple(NOT_YET)),
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
    jobs = db.query(Job).all()
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

# ──── Fire / remove an employee ────
@router.put("/fire-employee/{employee_id}")
def fire_employee(
    employee_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User

    # ──── Fire a hired candidate ────
    if employee_id.startswith("candidate_"):
        app_id = int(employee_id.replace("candidate_", ""))
        application = db.query(Application).filter(
            Application.id == app_id
        ).first()
        if not application:
            raise HTTPException(status_code=404, detail="Application not found")
        if application.status not in ["hired", "accepted"]:
            raise HTTPException(status_code=400, detail="This employee was not hired")
        application.status = "fired"
        db.commit()
        return {"message": "Hired employee removed"}

    # ──── Fire a manually created employee ────
    elif employee_id.startswith("user_"):
        user_id = int(employee_id.replace("user_", ""))
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.status = "fired"
        db.commit()
        return {"message": "Created employee removed"}

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
    jobs = db.query(Job).all()
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
    tenant: Tenant = Depends(get_tenant),
):
    """
    A candidate's CV — name, phone, email, work history.

    This asked only for a valid login. ANY employee of ANY company could
    walk the application ids and download every CV in the database.

    `get_tenant` scopes the session, so the lookup below cannot see
    another company's application at all.
    """
    from fastapi.responses import Response

    application = db.query(Application).filter(
        Application.id == application_id
    ).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    candidate = db.query(Candidate).filter(
        Candidate.id == application.candidate_id
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # ══════════════════════════════════════════════
    # THIS APPLICATION'S CV, not the candidate's latest
    # ══════════════════════════════════════════════
    # This used to read `candidate.cv_pdf` — one document per person.
    # `fetch-and-screen` overwrote it whenever that address turned up
    # again, so somebody who applied for two roles had both applications
    # showing whichever CV was read last. Right or wrong depending on
    # the order the mailbox came back in.
    #
    # The application carries its own copy now. The candidate's is the
    # fallback for rows written before that change and never re-fetched.
    cv_pdf = application.cv_pdf or candidate.cv_pdf
    cv_text = application.cv_text or candidate.cv_text

    # ──── Send the original PDF if there is one ────
    if cv_pdf:
        filename = f"{candidate.full_name or 'CV'}_CV.pdf".replace(" ", "_")
        return Response(
            content=cv_pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )
    
    # ──── Fallback: build a PDF from the text ────
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

    if cv_text:
        for line in cv_text.split('\n'):
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
    # ──── Reject the candidate ────
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
        raise HTTPException(status_code=404, detail="Application not found")

    if application.status in ["hired", "accepted"]:
        raise HTTPException(status_code=400, detail="A candidate who is already hired cannot be rejected")

    candidate = db.query(Candidate).filter(
        Candidate.id == application.candidate_id
    ).first()
    job = db.query(Job).filter(Job.id == application.job_id).first()
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    # ──── Status update ────
    application.status = "rejected"
    db.commit()

    google_token = _google_token(db, current_user["company_id"])

    # ──── Rejection email via MCP ────
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
                        "google_token": google_token
                    }
                )
                email_sent = "sent" in result.content[0].text.lower()
    except Exception as e:
        print(f"MCP rejection error: {e}")

    return {
        "message": "Candidate rejected — email sent",
        "application_id": application_id,
        "email_sent": email_sent
    }