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
    CompanyWorkPolicy, CompanyPolicy, PolicyDecisionLog,
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
    # ──── CEO kitne ghante mein leave ka jawab de (0 = kabhi auto nahi) ────
    leave_auto_approve_hours: int = 24
    min_daily_hours: float = 8.0
    overtime_threshold: float = 9.0
    max_overtime_per_day: float = 3.0
    break_policy: str = "excluded"
    # ──── Break kab aur kitni der ────
    # start/end khali = "itne minute, jab chahein" (flexible)
    break_minutes: int = 60
    break_start: Optional[str] = None
    break_end: Optional[str] = None


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


def _clean_time(value):
    """
    Khali string ko None banao.

    HTML ka <input type="time"> khali hone par "" bhejta hai. Wo seedha
    save ho jata to `break_start` khali string ban jati — na None, na
    waqt — aur "break muqarrar hai ya nahi" ka check ghalat ho jata.
    """
    value = (value or "").strip()
    return value or None


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
        existing.leave_auto_approve_hours = data.leave_auto_approve_hours
        existing.min_daily_hours = data.min_daily_hours
        existing.overtime_threshold = data.overtime_threshold
        existing.max_overtime_per_day = data.max_overtime_per_day
        existing.break_policy = data.break_policy
        existing.break_minutes = data.break_minutes
        existing.break_start = _clean_time(data.break_start)
        existing.break_end = _clean_time(data.break_end)
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
        leave_auto_approve_hours=data.leave_auto_approve_hours,
        min_daily_hours=data.min_daily_hours,
        overtime_threshold=data.overtime_threshold,
        max_overtime_per_day=data.max_overtime_per_day,
        break_policy=data.break_policy,
        break_minutes=data.break_minutes,
        break_start=_clean_time(data.break_start),
        break_end=_clean_time(data.break_end),
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
            "leave_auto_approve_hours": policy.leave_auto_approve_hours,
            "min_daily_hours": policy.min_daily_hours,
            "overtime_threshold": policy.overtime_threshold,
            "max_overtime_per_day": policy.max_overtime_per_day,
            "break_policy": policy.break_policy,
            "break_minutes": policy.break_minutes,
            "break_start": policy.break_start,
            "break_end": policy.break_end,
            "updated_at": str(policy.updated_at)
        }
    }


# ──── Route 2b: Policy se Working Hours ki tajweez ────
@router.post("/work-policy/extract")
def extract_work_policy_suggestion(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Policy document parh kar working hours TAJWEEZ karo.

    Upload par yeh khud chal jata hai (`extract_and_apply_work_policy`).
    Yeh route dobara chalane ke liye hai — aur yeh kuch SAVE NAHI karta.
    Values form mein bhar jati hain, CEO dekhta hai, badal sakta hai,
    phir khud Save dabata hai.

    Har field ke saath `source_quote` aata hai — document ki wo asal line
    jis se value nikli, taake CEO khud tasdeeq kar sake.
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == ceo.id,
        CompanyPolicy.is_active == True
    ).first()

    if not active_policy:
        raise HTTPException(
            status_code=400,
            detail="Koi active policy document nahi — pehle Policy Document tab se upload karein"
        )

    try:
        from app.agents.work_policy_extraction_agent import extract_work_policy
        result = extract_work_policy(ceo.id)
    except Exception as e:
        print(f"Work policy extraction failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="Agent abhi dastyab nahi — working hours manually set karein"
        )

    # ──── Har tajweez ko maujooda value ke saath milao ────
    # CEO ko yeh dikhna chahiye ke kya BADAL raha hai, sirf nayi value nahi
    current = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == ceo.id
    ).first()

    for name, item in result["fields"].items():
        old = getattr(current, name, None) if current else None
        if hasattr(old, "value"):          # Enum (break_policy)
            old = old.value
        item["current_value"] = old
        item["changes"] = old != item["value"]

    return {
        "ran": True,
        "saved": False,          # sirf tajweez — CEO Save dabayega
        "fields": result["fields"],
        "warnings": result["warnings"],
        "chunks_used": result["chunks_used"],
        "policy_label": active_policy.policy_label or active_policy.file_name,
        "found_count": len(result["fields"]),
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


def extract_and_apply_leave_types(session, company_id: int, policy_id: int,
                                  policy_text: str = ""):
    """
    Policy index hone ke fauran baad leave types nikal kar laga do.

    CEO ko button dabane ki zarurat nahi — document hi asal sach hai.
    Jo type policy mein na ho wo band ho jati hai, jo nayi ho wo ban jati hai,
    aur employees ke maujooda balance bhi config se mil jate hain.

    ═══ AHEM GUARD ═══
    Agar agent ko koi type MILI HI NAHI (ya wo fail ho gaya) to KUCH NAHI
    badalte. Warna Groq down hone par ya kharab PDF par saari types ek saath
    band ho jatin aur poori company ki leave ruk jati.

    Natija `company_policies.policy_preview` mein save hota hai taake CEO
    dekh sake ke kya hua — chup chaap kuch nahi hota.
    """
    summary = {"ran": False, "reason": "", "applied": [], "created": [], "disabled": []}

    try:
        from app.agents.policy_extraction_agent import extract_leave_types
        from app.routes.leave import apply_policy_types

        result = extract_leave_types(company_id, policy_text)
        extracted = result.get("types", [])

        if not extracted:
            summary["reason"] = (
                result.get("error")
                or "Policy document mein koi leave type nahi mili — "
                   "purani types waise hi rehne di gayin"
            )
            print(f"[policy] extraction ne kuch nahi diya — kuch nahi badla")
        else:
            applied = apply_policy_types(
                session, company_id, extracted, disable_missing=True
            )
            summary.update(applied)
            summary["ran"] = True
            summary["warnings"] = result.get("warnings", [])
            summary["reason"] = f"{len(applied['applied'])} type(s) policy se laagu"
            print(
                f"[policy] types auto-applied: {applied['applied']} "
                f"| band: {applied['disabled']} "
                f"| balances synced: {applied['balances_synced']}"
            )

    except Exception as e:
        session.rollback()
        summary["reason"] = f"Types nikalte waqt masla: {e}"
        print(f"[policy] leave type extraction failed: {e}")

    _save_policy_preview(session, policy_id, "leave_types", summary)
    return summary


def _save_policy_preview(session, policy_id: int, key: str, summary: dict):
    """
    Extraction ka natija `policy_preview` mein rakho — CEO ko dikhane ke liye.

    Ek document se do cheezein nikalti hain (leave types + working hours),
    is liye dono alag key mein rehti hain aur ek dusre ko mitati nahi.
    """
    try:
        policy = session.query(CompanyPolicy).filter(
            CompanyPolicy.id == policy_id
        ).first()
        if not policy:
            return

        preview = policy.policy_preview
        # Purana format seedha leave summary rakhta tha — usay nayi
        # shape mein le aao warna wo natija gum ho jayega
        if not isinstance(preview, dict) or "leave_types" not in preview:
            preview = {"leave_types": preview} if preview else {}

        policy.policy_preview = {**preview, key: summary}
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[policy] {key} summary save failed: {e}")


def extract_and_apply_work_policy(session, company_id: int, policy_text: str = ""):
    """
    Policy index hone ke baad **working hours** khud bhar do.

    Leave types wala hi usool, magar ek bara farq:
    types mein "policy mein nahi hai" ka matlab "band kar do" hai, magar
    yahan uska matlab **"CEO ne jo set kiya wo waise hi rehne do"** hai.
    Shift timings zaroori cheez hai — document khamosh ho to purani value
    mitana galat hoga.

    Yani: jo field MILI wo lag jati hai, jo NAHI mili wo CHHOTI NAHI JATI.

    ═══ GUARD ═══
    Agent ko kuch bhi na mile (ya wo fail ho jaye) to kuch nahi badalta.
    Warna Groq down hone par ya kharab PDF par poori company ki shift
    default 09:00-18:00 par chali jati.

    Return: summary dict — CEO ko dikhane ke liye
    """
    # Shakl bilkul wahi jo `/work-policy/extract` deta hai — UI ka ek hi
    # panel dono ko dikhata hai
    summary = {
        "ran": False, "reason": "",
        "fields": {},         # {name: {value, source_quote, confidence, ...}}
        "skipped": [],        # jo document mein tha hi nahi
        "warnings": [],
    }

    # Wahi fields jo Working Hours tab mein hain
    FIELDS = [
        "shift_start", "shift_end", "working_days",
        "late_tolerance_mins", "early_checkin_grace_mins",
        "enforce_shift_window", "leave_auto_approve_hours",
        "min_daily_hours", "overtime_threshold", "max_overtime_per_day",
        "break_policy", "break_minutes", "break_start", "break_end",
    ]

    try:
        from app.agents.work_policy_extraction_agent import extract_work_policy

        result = extract_work_policy(company_id, policy_text)
        found = result.get("fields", {})
        summary["warnings"] = result.get("warnings", [])

        if not found:
            summary["reason"] = (
                result.get("error")
                or "Policy document mein working hours nahi mile — "
                   "maujooda settings waise hi rehne di gayin"
            )
            print("[policy] work policy: kuch nahi mila — kuch nahi badla")
            return summary

        policy = session.query(CompanyWorkPolicy).filter(
            CompanyWorkPolicy.company_id == company_id
        ).first()

        # ──── Policy hi na ho to nayi banao ────
        # Shift timings nullable nahi hain, is liye default rakhna parta
        # hai — magar sirf tab jab document ne wo field di hi na ho.
        if not policy:
            policy = CompanyWorkPolicy(
                company_id=company_id,
                working_days=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                shift_start="09:00",
                shift_end="18:00",
            )
            session.add(policy)

        for name in FIELDS:
            if name not in found:
                summary["skipped"].append(name)
                continue

            item = found[name]
            previous = getattr(policy, name, None)
            if hasattr(previous, "value"):        # Enum (break_policy)
                previous = previous.value

            setattr(policy, name, item["value"])
            summary["fields"][name] = {
                "value": item["value"],
                "source_quote": item.get("source_quote", ""),
                "confidence": item.get("confidence", "low"),
                "current_value": previous,
                "changes": previous != item["value"],
            }

        policy.updated_at = datetime.utcnow()
        session.commit()

        summary["ran"] = True
        summary["saved"] = True          # yeh khud lag chuka hai
        summary["found_count"] = len(summary["fields"])
        summary["reason"] = (
            f"{len(summary['fields'])} field policy se laagu, "
            f"{len(summary['skipped'])} manual"
        )
        print(f"[policy] work policy auto-applied: {list(summary['fields'])}")

    except Exception as e:
        session.rollback()
        summary["reason"] = f"Working hours nikalte waqt masla: {e}"
        print(f"[policy] work policy extraction failed: {e}")

    return summary


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

        # ──── Cosine space zaroori hai ────
        # ChromaDB by default squared-L2 use karta hai. Unit vectors pe
        # L2² = 2 - 2*cos, to `1 - distance` cosine similarity NAHI hoti
        # (cos=0.5 pe 0 aata hai, cos=0 pe -1). Leave agent ka
        # similarity threshold isi hisaab se ghalat lagta tha.
        collection = chroma_client.create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        model = SentenceTransformer("all-MiniLM-L6-v2")

        for idx, chunk in enumerate(chunks):
            embedding = model.encode(chunk).tolist()
            collection.add(
                ids=[f"policy_{policy_id}_chunk_{idx}"],
                embeddings=[embedding],
                documents=[chunk],
                metadatas=[{"policy_id": policy_id, "company_id": company_id, "chunk_index": idx}]
            )

        # ──── Chunks tayyar — policy ab kaam ki hai ────
        # `is_active` yahin lag jata hai kyunki Leave Agent isi par chalta
        # hai. Magar `status` abhi "processing" hi rehta hai — dekhein neeche.
        policy = session.query(CompanyPolicy).filter(CompanyPolicy.id == policy_id).first()
        if policy:
            policy.chunks_indexed = len(chunks)
            policy.vector_collection = collection_name
            policy.indexed_at = datetime.utcnow()
            policy.is_active = True
            session.commit()

        print(f"Policy {policy_id} indexed: {len(chunks)} chunks")

        # ──── Ab policy se settings khud nikal kar laga do ────
        # Do alag agent — ek dusre se mustaqil. Ek fail ho jaye to
        # doosra phir bhi chalta hai (dono apni ghalti khud sambhalte hain).
        extract_and_apply_leave_types(session, company_id, policy_id, text)

        wp_summary = extract_and_apply_work_policy(session, company_id, text)
        _save_policy_preview(session, policy_id, "work_policy", wp_summary)

        # ══════════════════════════════════════════
        # SAB SE AAKHIR MEIN status = "active"
        # ══════════════════════════════════════════
        # Pehle yeh chunks bante hi lag jata tha — indexing ke fauran baad,
        # magar agents chalne se PEHLE. UI isi par intezar khatam karti hai,
        # is liye "Policy indexed!" us waqt aa jata jab leave types aur
        # working hours abhi lage hi nahi the. CEO ko purani values dikhtin
        # (ya panel aata hi nahi) aur page reload karna parta.
        #
        # Ab `status == "active"` ka matlab hai: **sab kuch mukammal**.
        policy = session.query(CompanyPolicy).filter(CompanyPolicy.id == policy_id).first()
        if policy:
            policy.status = "active"
            session.commit()

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

    # Purane documents ka preview seedha leave summary tha (bina key ke)
    preview = policy.policy_preview
    if isinstance(preview, dict) and "leave_types" in preview:
        leave_summary = preview.get("leave_types")
        work_summary = preview.get("work_policy")
    else:
        leave_summary, work_summary = preview, None

    return {
        "policy_id": policy.id,
        "status": policy.status,
        "chunks_indexed": policy.chunks_indexed,
        "indexed_at": str(policy.indexed_at) if policy.indexed_at else None,
        "is_active": policy.is_active,
        # ──── Document se khud lagne ka natija ────
        "leave_types": leave_summary,
        "work_policy": work_summary,
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

    preview = policy.policy_preview
    if isinstance(preview, dict) and "leave_types" in preview:
        leave_summary = preview.get("leave_types")
        work_summary = preview.get("work_policy")
    else:
        leave_summary, work_summary = preview, None

    return {
        "policy": {
            "id": policy.id,
            "file_name": policy.file_name,
            "policy_label": policy.policy_label,
            "status": policy.status,
            "chunks_indexed": policy.chunks_indexed,
            "version": policy.version,
            "effective_from": str(policy.effective_from),
            "created_at": str(policy.created_at),
            "leave_types": leave_summary,
            # Settings kholte hi Working Hours tab par dikh jata hai ke
            # kaunsi field document se aayi thi — page reload ke baad bhi
            "work_policy": work_summary,
        }
    }


# ──── Route 8b: Sab Policies ki List ────
@router.get("/policy/list")
def list_policies(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """CEO ki company ki sari uploaded policies — nayi pehle"""
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    policies = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == ceo.id
    ).order_by(CompanyPolicy.created_at.desc()).all()

    return {
        "total": len(policies),
        "policies": [
            {
                "id": p.id,
                "file_name": p.file_name,
                "policy_label": p.policy_label,
                "file_type": p.file_type,
                "status": p.status.value if hasattr(p.status, "value") else p.status,
                "chunks_indexed": p.chunks_indexed,
                "version": p.version,
                "is_active": p.is_active,
                "effective_from": str(p.effective_from) if p.effective_from else None,
                "created_at": str(p.created_at) if p.created_at else None,
                "indexed_at": str(p.indexed_at) if p.indexed_at else None,
                "file_exists": bool(p.file_path and os.path.exists(p.file_path)),
            }
            for p in policies
        ]
    }


# ──── Route 8b-2: Purani Policy dobara Activate ────
@router.post("/policy/{policy_id}/activate")
def activate_policy(
    policy_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Pehle se uploaded policy ko dobara active karo.

    ═══ MASLA KYA THA ═══
    Nayi policy upload karte hi purani "superseded" ho jati thi. Wapas usi
    par jane ka koi tareeqa nahi tha — CEO ko wohi file DOBARA upload karni
    parti thi (nayi version ban jati, indexing phir se hoti) aur purani
    delete karni parti thi. File to disk par mehfooz padi hai — usay
    istemal na karna bemaani hai.

    ═══ AB ═══
    File wahi hai, is liye wohi rasta dobara chalta hai jo upload par chalta
    hai — `process_policy_document`:
        chunks banao → ChromaDB dobara index karo (purana collection hata kar)
        → leave types laagu → working hours laagu

    Yani natija bilkul waisa hi hota hai jaisa us document ko upload karne
    par hota. Alag rasta banata to dono kabhi na kabhi alag chalne lagte.

    Purani policies delete NAHI hoti — CEO jab chahe ek se doosri par
    aa ja sakta hai.
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = ceo.id

    policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.id == policy_id,
        CompanyPolicy.company_id == company_id
    ).first()

    if not policy:
        raise HTTPException(status_code=404, detail="Policy nahi mili")

    if policy.is_active:
        raise HTTPException(status_code=400, detail="Yeh policy pehle se active hai")

    # ──── File disk par honi chahiye ────
    # Bina file ke na chunks ban sakte hain na agent kuch parh sakta hai.
    # Yahan rok dena behtar hai — warna active to ho jati magar khali.
    if not policy.file_path or not os.path.exists(policy.file_path):
        raise HTTPException(
            status_code=400,
            detail=(
                "Is policy ki file disk par nahi mili — activate nahi ho sakti. "
                "Dobara upload karein."
            )
        )

    label = policy.policy_label or policy.file_name

    # ──── Baqi sab ko utaar do ────
    db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).update({"is_active": False, "status": "superseded"})

    # Purana natija saaf — warna nayi indexing chalte waqt UI purana
    # natija dikhata rahega
    policy.status = "processing"
    policy.policy_preview = None
    db.commit()

    from app.database import DATABASE_URL
    background_tasks.add_task(
        process_policy_document,
        file_path=policy.file_path,
        file_type=policy.file_type,
        policy_id=policy.id,
        company_id=company_id,
        db_url=DATABASE_URL
    )

    return {
        "message": f"'{label}' activate ho rahi hai — dobara index ho rahi hai",
        "policy_id": policy.id,
        "status": "processing",
    }


# ──── Route 8c: Policy Delete ────
@router.delete("/policy/{policy_id}")
def delete_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Policy document delete karo — DB row, file, aur ChromaDB ke chunks.

    Chunks hatana SAB SE ZAROORI hai: agar sirf DB row delete karein aur
    vector chunks pade rahein to Leave Agent us hataye hue document se
    jawab uthata rahega.
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.id == policy_id,
        CompanyPolicy.company_id == ceo.id
    ).first()

    if not policy:
        raise HTTPException(status_code=404, detail="Policy nahi mili")

    was_active = policy.is_active
    file_path = policy.file_path
    label = policy.policy_label or policy.file_name

    # ──── 1. Audit trail bacha lo ────
    # Purane leave faisle delete NAHI karne — sirf policy ka link hata do,
    # warna "yeh leave kis policy pe reject hui thi" ka record gum ho jayega
    db.query(PolicyDecisionLog).filter(
        PolicyDecisionLog.policy_id == policy_id
    ).update({"policy_id": None}, synchronize_session=False)

    # ──── 2. Vector chunks ────
    chunks_removed = False
    if was_active:
        try:
            import chromadb
            chroma_client = chromadb.PersistentClient(
                path=os.path.join(os.path.dirname(__file__), "..", "chroma_db")
            )
            chroma_client.delete_collection(f"company_{ceo.id}_policies")
            chunks_removed = True
        except Exception as e:
            # Collection maujood hi na ho to koi baat nahi
            print(f"Chroma collection delete: {e}")

    # ──── 3. File ────
    file_removed = False
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            file_removed = True
    except Exception as e:
        print(f"Policy file delete failed: {e}")

    # ──── 4. DB row ────
    db.delete(policy)
    db.commit()

    return {
        "message": f"'{label}' delete ho gayi",
        "policy_id": policy_id,
        "was_active": was_active,
        "chunks_removed": chunks_removed,
        "file_removed": file_removed,
        "note": (
            "Ab koi active policy nahi — sari leave requests CEO ke paas jayengi"
            if was_active else None
        ),
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