from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
import json
import fitz  # pymupdf
from app.database import get_db
from app.models.recruitment import Job, Candidate, Application
from app.schemas.recruitment import JobCreate, JobResponse, CandidateCreate
from app.utils.security import get_current_user
from app.agents.jd_generator import generate_job_description

router = APIRouter(
    prefix="/recruitment",
    tags=["Recruitment"]
)


# ──── Role check ────
def require_ceo(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "ceo":
        raise HTTPException(status_code=403, detail="Sirf CEO yeh kaam kar sakta hai")
    return current_user


# ──── Helper: dict/list ko string banao ────
def to_string(value) -> str:
    if isinstance(value, str):
        return value
    elif isinstance(value, dict):
        parts = []
        for v in value.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend([str(i) for i in v])
        return " ".join(parts)
    elif isinstance(value, list):
        return " ".join([str(i) for i in value])
    return str(value) if value else ""


# ──── CEO Job Create ────
@router.post("/jobs/create")
def create_job(
    data: JobCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User

    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    if not ceo:
        raise HTTPException(status_code=404, detail="CEO nahi mila")

    jd_result = generate_job_description(
        title=data.title,
        department=data.department,
        employment_type=data.employment_type,
        experience=data.experience,
        skills=data.skills,
        salary_range=data.salary_range,
        company_name=ceo.company_name or "Company",
        additional_info=data.additional_info,
        ceo_email=ceo.email
    )

    full_description = to_string(jd_result.get("full_description", ""))
    keywords = to_string(jd_result.get("keywords", ""))

    new_job = Job(
        ceo_id=current_user["user_id"],
        company_name=ceo.company_name,
        title=data.title,
        department=data.department,
        employment_type=data.employment_type,
        experience=data.experience,
        skills=data.skills,
        salary_range=data.salary_range,
        full_description=full_description,
        keywords=keywords,
        status="published"
    )

    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    return {
        "message": "Job successfully create ho gayi!",
        "job_id": new_job.id,
        "title": new_job.title,
        "full_description": new_job.full_description,
        "keywords": new_job.keywords
    }


# ──── Jobs list dekho ────
@router.get("/jobs")
def get_jobs(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.models.user import User
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    jobs = db.query(Job).filter(
        Job.company_name == ceo.company_name,
        Job.status == "published"
    ).all()

    return {
        "total": len(jobs),
        "jobs": [
            {
                "id": job.id,
                "title": job.title,
                "department": job.department,
                "employment_type": job.employment_type,
                "experience": job.experience,
                "skills": job.skills,
                "salary_range": job.salary_range,
                "full_description": job.full_description,
                "status": job.status,
                "created_at": job.created_at
            }
            for job in jobs
        ]
    }


# ──── Single job dekho ────
@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")

    return {
        "id": job.id,
        "title": job.title,
        "department": job.department,
        "employment_type": job.employment_type,
        "experience": job.experience,
        "skills": job.skills,
        "salary_range": job.salary_range,
        "full_description": job.full_description,
        "company_name": job.company_name,
        "status": job.status,
        "created_at": job.created_at
    }


# ──── Job delete karo ────
@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")

    db.delete(job)
    db.commit()

    return {"message": "Job delete ho gayi"}


# ──── Public Jobs List ──── (ceo_email add hua)
@router.get("/public/jobs")
def get_public_jobs(db: Session = Depends(get_db)):
    from app.models.user import User

    jobs = db.query(Job).filter(Job.status == "published").all()

    result = []
    for job in jobs:
        ceo = db.query(User).filter(User.id == job.ceo_id).first()
        ceo_email = ceo.email if ceo else ""

        result.append({
            "id": job.id,
            "title": job.title,
            "department": job.department,
            "employment_type": job.employment_type,
            "experience": job.experience,
            "skills": job.skills,
            "salary_range": job.salary_range,
            "company_name": job.company_name,
            "full_description": job.full_description,
            "ceo_email": ceo_email,  # ← naya
            "created_at": job.created_at
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
    ceo_email = ceo.email if ceo else ""

    return {
        "id": job.id,
        "title": job.title,
        "department": job.department,
        "employment_type": job.employment_type,
        "experience": job.experience,
        "skills": job.skills,
        "salary_range": job.salary_range,
        "company_name": job.company_name,
        "full_description": job.full_description,
        "ceo_email": ceo_email,  # ← naya
        "created_at": job.created_at
    }


# ──── Candidate Apply ────
@router.post("/apply")
async def apply_job(
    job_id: int = Form(...),
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    cv_file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")

    existing_candidate = db.query(Candidate).filter(
        Candidate.email == email
    ).first()

    if existing_candidate:
        existing_app = db.query(Application).filter(
            Application.candidate_id == existing_candidate.id,
            Application.job_id == job_id
        ).first()
        if existing_app:
            raise HTTPException(
                status_code=400,
                detail="Aap pehle se is job ke liye apply kar chuke hain"
            )

    cv_text = ""
    cv_filename = cv_file.filename

    try:
        pdf_bytes = await cv_file.read()
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in pdf_doc:
            cv_text += page.get_text()
        pdf_doc.close()
    except Exception as e:
        raise HTTPException(status_code=400, detail="PDF read nahi ho saka")

    if not cv_text.strip():
        raise HTTPException(status_code=400, detail="CV mein text nahi mila")

    if existing_candidate:
        candidate = existing_candidate
    else:
        candidate = Candidate(
            full_name=full_name,
            email=email,
            phone=phone,
            cv_text=cv_text,
            cv_filename=cv_filename
        )
        db.add(candidate)
        db.flush()

    application = Application(
        candidate_id=candidate.id,
        job_id=job_id,
        status="applied"
    )
    db.add(application)
    db.commit()
    db.refresh(application)

    return {
        "message": "Application successfully submit ho gayi!",
        "application_id": application.id,
        "candidate_id": candidate.id,
        "job_title": job.title
    }


# ──── CEO: Applications list dekho ────
@router.get("/applications/{job_id}")
def get_applications(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    applications = db.query(Application).filter(
        Application.job_id == job_id
    ).all()

    result = []
    for app in applications:
        candidate = db.query(Candidate).filter(
            Candidate.id == app.candidate_id
        ).first()
        result.append({
            "application_id": app.id,
            "candidate_id": app.candidate_id,
            "full_name": candidate.full_name if candidate else "—",
            "email": candidate.email if candidate else "—",
            "phone": candidate.phone if candidate else "—",
            "cv_filename": candidate.cv_filename if candidate else "—",
            "status": app.status,
            "match_score": app.match_score,
            "skill_gap": app.skill_gap,
            "summary": app.summary,
            "applied_at": app.applied_at
        })

    return {
        "job_id": job_id,
        "total": len(result),
        "applications": result
    }
    # ──── Gmail se CVs fetch karo ────
@router.post("/fetch-gmail-cvs/{job_id}")
async def fetch_gmail_cvs(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    from app.agents.gmail_agent import fetch_job_application_emails
    from app.models.user import User

    # Job dhundo
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili")

    # CEO check
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    try:
        # Gmail se emails fetch karo
        applications = fetch_job_application_emails(job_title=job.title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail fetch error: {str(e)}")

    saved_count = 0
    for app_data in applications:
        # Duplicate check
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
                full_name=app_data['name'],
                email=app_data['email'],
                phone="",
                cv_text=app_data['cv_text'],
                cv_filename=app_data['cv_filename']
            )
            db.add(candidate)
            db.flush()

        application = Application(
            candidate_id=candidate.id,
            job_id=job_id,
            status="applied"
        )
        db.add(application)
        saved_count += 1

    db.commit()

    return {
        "message": f"{saved_count} new applications fetched from Gmail",
        "total_found": len(applications),
        "saved": saved_count
    }