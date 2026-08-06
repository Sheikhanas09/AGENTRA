import os
import json
import math
import shutil
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db
from app.utils.security import get_current_user
from app.models.attendance import (
    CompanyWorkPolicy, CompanyPolicy,
    CompanyPolicyOverride, LeaveBalance, OfficeLocation
)
from app.models.user import User

router = APIRouter(prefix="/settings", tags=["Settings"])


def require_ceo(current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in ["ceo", "superadmin"]:
        raise HTTPException(status_code=403, detail="Sirf CEO yeh kar sakta hai")
    return current_user


# ──── Pydantic Schemas ────
class WorkPolicySchema(BaseModel):
    working_days: List[str]
    shift_start: str
    late_tolerance_mins: int = 15
    shift_end: str
    # ──── Check-in sirf shift ke darmiyan ────
    enforce_shift_window: bool = True
    early_checkin_grace_mins: int = 60
    min_daily_hours: float = 8.0
    overtime_threshold: float = 9.0
    max_overtime_per_day: float = 3.0
    break_policy: str = "excluded"


class OverrideSchema(BaseModel):
    leave_type: str
    force_manual: bool
    reason: Optional[str] = ""


class OfficeLocationSchema(BaseModel):
    office_name: str = "Head Office"
    latitude: float
    longitude: float
    radius_meters: int = 200
    # ↑ Modular — CEO change kar sakta hai


# ──── GPS Helper ────
def calculate_distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Haversine formula
    2 GPS points ke beech distance meters mein
    """
    R = 6371000  # Earth radius meters

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)

    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) *
         math.sin(delta_lng / 2) ** 2)

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ──── Route 1: Working Hours Policy Save ────
@router.post("/work-policy")
def save_work_policy(
    data: WorkPolicySchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = ceo.id

    existing = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()

    if existing:
        existing.working_days = data.working_days
        existing.shift_start = data.shift_start
        existing.late_tolerance_mins = data.late_tolerance_mins
        existing.shift_end = data.shift_end
        existing.enforce_shift_window = data.enforce_shift_window
        existing.early_checkin_grace_mins = data.early_checkin_grace_mins
        existing.min_daily_hours = data.min_daily_hours
        existing.overtime_threshold = data.overtime_threshold
        existing.max_overtime_per_day = data.max_overtime_per_day
        existing.break_policy = data.break_policy
        existing.set_by = current_user["user_id"]
        existing.updated_at = datetime.utcnow()
        db.commit()
        return {"message": "Work policy updated!", "policy_id": existing.id}

    policy = CompanyWorkPolicy(
        company_id=company_id,
        working_days=data.working_days,
        shift_start=data.shift_start,
        late_tolerance_mins=data.late_tolerance_mins,
        shift_end=data.shift_end,
        enforce_shift_window=data.enforce_shift_window,
        early_checkin_grace_mins=data.early_checkin_grace_mins,
        min_daily_hours=data.min_daily_hours,
        overtime_threshold=data.overtime_threshold,
        max_overtime_per_day=data.max_overtime_per_day,
        break_policy=data.break_policy,
        set_by=current_user["user_id"]
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return {"message": "Work policy saved!", "policy_id": policy.id}


# ──── Route 2: Work Policy Get ────
@router.get("/work-policy")
def get_work_policy(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = ceo.id

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()

    if not policy:
        return {"policy": None}

    return {
        "policy": {
            "id": policy.id,
            "working_days": policy.working_days,
            "shift_start": policy.shift_start,
            "late_tolerance_mins": policy.late_tolerance_mins,
            "shift_end": policy.shift_end,
            "enforce_shift_window": policy.enforce_shift_window,
            "early_checkin_grace_mins": policy.early_checkin_grace_mins,
            "min_daily_hours": policy.min_daily_hours,
            "overtime_threshold": policy.overtime_threshold,
            "max_overtime_per_day": policy.max_overtime_per_day,
            "break_policy": policy.break_policy,
            "updated_at": str(policy.updated_at)
        }
    }


# ──── Route 3: Office Location Save ────
@router.post("/office-location")
def save_office_location(
    data: OfficeLocationSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    CEO office ki location set kare
    Modular — CEO settings se change kar sakta hai
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = ceo.id

    existing = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id
    ).first()

    if existing:
        existing.office_name = data.office_name
        existing.latitude = data.latitude
        existing.longitude = data.longitude
        existing.radius_meters = data.radius_meters
        existing.set_by = current_user["user_id"]
        existing.updated_at = datetime.utcnow()
        db.commit()
        return {
            "message": "Office location updated!",
            "office_name": data.office_name,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "radius_meters": data.radius_meters
        }

    office = OfficeLocation(
        company_id=company_id,
        office_name=data.office_name,
        latitude=data.latitude,
        longitude=data.longitude,
        radius_meters=data.radius_meters,
        set_by=current_user["user_id"]
    )
    db.add(office)
    db.commit()
    db.refresh(office)

    return {
        "message": "Office location saved!",
        "office_name": data.office_name,
        "latitude": data.latitude,
        "longitude": data.longitude,
        "radius_meters": data.radius_meters
    }


# ──── Route 4: Office Location Get ────
@router.get("/office-location")
def get_office_location(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Office location lo"""
    from app.models.user import User

    user = db.query(User).filter(User.id == current_user["user_id"]).first()

    # ──── CEO dhundo ────
    if user.role == "ceo":
        company_id = user.id
    else:
        ceo = db.query(User).filter(
            User.company_name == user.company_name,
            User.role == "ceo"
        ).first()
        company_id = ceo.id if ceo else None

    if not company_id:
        return {"office": None}

    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id,
        OfficeLocation.is_active == True
    ).first()

    if not office:
        return {"office": None}

    return {
        "office": {
            "id": office.id,
            "office_name": office.office_name,
            "latitude": office.latitude,
            "longitude": office.longitude,
            "radius_meters": office.radius_meters,
            "updated_at": str(office.updated_at)
        }
    }


# ──── Route 5: GPS Verify (Employee check-in se call hoga) ────
@router.post("/verify-location")
def verify_location(
    employee_lat: float,
    employee_lng: float,
    company_id: int,
    db: Session = Depends(get_db)
):
    """
    Employee ki GPS location verify karo
    Office radius mein hai ya nahi
    """
    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id,
        OfficeLocation.is_active == True
    ).first()

    if not office:
        # ──── Office set nahi → Allow karo ────
        return {
            "verified": True,
            "distance_meters": 0,
            "message": "Office location not configured — allowing"
        }

    distance = calculate_distance_meters(
        employee_lat, employee_lng,
        office.latitude, office.longitude
    )

    verified = distance <= office.radius_meters

    return {
        "verified": verified,
        "distance_meters": round(distance, 1),
        "radius_meters": office.radius_meters,
        "office_name": office.office_name,
        "message": (
            f"Within office range ({distance:.0f}m)" if verified
            else f"Outside office range ({distance:.0f}m > {office.radius_meters}m)"
        )
    }


# ──── Background Task: Policy Index ────
def process_policy_document(
    file_path: str,
    file_type: str,
    policy_id: int,
    company_id: int,
    db_url: str
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import chromadb
    from sentence_transformers import SentenceTransformer
    from app.utils.policy_extractor import extract_policy_text

    engine = create_engine(db_url)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        text = extract_policy_text(file_path, file_type)
        if not text:
            policy = session.query(CompanyPolicy).filter(CompanyPolicy.id == policy_id).first()
            if policy:
                policy.status = "failed"
                session.commit()
            return

        chunk_size = 400
        overlap = 50
        words = text.split()
        chunks = []
        i = 0
        while i < len(words):
            chunk = " ".join(words[i:i+chunk_size])
            chunks.append(chunk)
            i += chunk_size - overlap

        chroma_client = chromadb.PersistentClient(
            path=os.path.join(os.path.dirname(__file__), "..", "chroma_db")
        )
        collection_name = f"company_{company_id}_policies"
        try:
            chroma_client.delete_collection(collection_name)
        except:
            pass

        collection = chroma_client.create_collection(collection_name)
        model = SentenceTransformer("all-MiniLM-L6-v2")

        for idx, chunk in enumerate(chunks):
            embedding = model.encode(chunk).tolist()
            collection.add(
                ids=[f"policy_{policy_id}_chunk_{idx}"],
                embeddings=[embedding],
                documents=[chunk],
                metadatas=[{"policy_id": policy_id, "company_id": company_id, "chunk_index": idx}]
            )

        policy = session.query(CompanyPolicy).filter(CompanyPolicy.id == policy_id).first()
        if policy:
            policy.status = "active"
            policy.chunks_indexed = len(chunks)
            policy.vector_collection = collection_name
            policy.indexed_at = datetime.utcnow()
            policy.is_active = True
            session.commit()

        print(f"Policy {policy_id} indexed: {len(chunks)} chunks")

    except Exception as e:
        print(f"Policy processing error: {e}")
        policy = session.query(CompanyPolicy).filter(CompanyPolicy.id == policy_id).first()
        if policy:
            policy.status = "failed"
            session.commit()
    finally:
        session.close()


# ──── Route 6: Policy Upload ────
@router.post("/policy/upload")
async def upload_policy(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    policy_label: str = Form("Company Policy"),
    effective_from: str = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = ceo.id

    filename = file.filename.lower()
    if filename.endswith(".pdf"):
        file_type = "pdf"
    elif filename.endswith(".docx"):
        file_type = "docx"
    else:
        raise HTTPException(status_code=400, detail="Sirf PDF ya DOCX upload karo!")

    upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads", "policies")
    os.makedirs(upload_dir, exist_ok=True)

    file_path = os.path.join(
        upload_dir,
        f"company_{company_id}_{datetime.now().timestamp()}.{file_type}"
    )

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).update({"is_active": False, "status": "superseded"})

    last_version = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id
    ).count()

    policy = CompanyPolicy(
        company_id=company_id,
        uploaded_by=current_user["user_id"],
        file_name=file.filename,
        file_path=file_path,
        file_type=file_type,
        policy_label=policy_label,
        effective_from=date.fromisoformat(effective_from) if effective_from else date.today(),
        status="processing",
        version=last_version + 1,
        is_active=False
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)

    from app.database import DATABASE_URL
    background_tasks.add_task(
        process_policy_document,
        file_path=file_path,
        file_type=file_type,
        policy_id=policy.id,
        company_id=company_id,
        db_url=DATABASE_URL
    )

    return {
        "message": "Policy upload ho gayi! Processing...",
        "policy_id": policy.id,
        "status": "processing"
    }


# ──── Route 7: Policy Status ────
@router.get("/policy/status/{policy_id}")
def get_policy_status(
    policy_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    policy = db.query(CompanyPolicy).filter(CompanyPolicy.id == policy_id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy nahi mili")

    return {
        "policy_id": policy.id,
        "status": policy.status,
        "chunks_indexed": policy.chunks_indexed,
        "indexed_at": str(policy.indexed_at) if policy.indexed_at else None,
        "is_active": policy.is_active
    }


# ──── Route 8: Active Policy ────
@router.get("/policy/active")
def get_active_policy(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = ceo.id

    policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).first()

    if not policy:
        return {"policy": None}

    return {
        "policy": {
            "id": policy.id,
            "file_name": policy.file_name,
            "policy_label": policy.policy_label,
            "status": policy.status,
            "chunks_indexed": policy.chunks_indexed,
            "version": policy.version,
            "effective_from": str(policy.effective_from),
            "created_at": str(policy.created_at)
        }
    }


# ──── Route 9: Manual Override ────
@router.patch("/policy/override")
def set_override(
    data: OverrideSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = ceo.id

    existing = db.query(CompanyPolicyOverride).filter(
        CompanyPolicyOverride.company_id == company_id,
        CompanyPolicyOverride.leave_type == data.leave_type
    ).first()

    if existing:
        existing.force_manual = data.force_manual
        existing.reason = data.reason
        existing.set_by = current_user["user_id"]
        if not data.force_manual:
            existing.cleared_at = datetime.utcnow()
        db.commit()
        return {"message": "Override updated!"}

    override = CompanyPolicyOverride(
        company_id=company_id,
        leave_type=data.leave_type,
        force_manual=data.force_manual,
        reason=data.reason,
        set_by=current_user["user_id"]
    )
    db.add(override)
    db.commit()
    return {"message": "Override set!"}