import os
import json
from decimal import Decimal, InvalidOperation
import math
import shutil
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db
from app.utils.security import get_current_user
from app.utils.tenancy import Tenant, get_tenant, require_ceo, bind_tenant
from app.models.attendance import (
    CompanyWorkPolicy, CompanyPolicy, PolicyDecisionLog,
    CompanyPolicyOverride, LeaveBalance, OfficeLocation
)
from app.models.user import User

router = APIRouter(prefix="/settings", tags=["Settings"])


# ──── `require_ceo` is imported, not defined here ────
# This file had its own, and so did `routes/ceo.py`, `routes/recruitment.py`
# and `utils/company.py`. All four asked only "is this user a CEO?" — none
# asked "of THIS company?". The shared one in `utils/tenancy.py` answers
# both and scopes the session while it is at it.


# ──── Pydantic Schemas ────
class WorkPolicySchema(BaseModel):
    working_days: List[str]
    shift_start: str
    late_tolerance_mins: int = 15
    shift_end: str
    # ──── Check in only during the shift ────
    enforce_shift_window: bool = True
    early_checkin_grace_mins: int = 60
    # ──── Hours the CEO has to answer a leave request (0 = never auto) ────
    leave_auto_approve_hours: int = 24
    min_daily_hours: float = 8.0
    overtime_threshold: float = 9.0
    max_overtime_per_day: float = 3.0
    break_policy: str = "excluded"
    # ──── When the break is, and how long ────
    # empty start/end = "this many minutes, whenever you like" (flexible)
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
    # ↑ Modular — the CEO can change it


def _clean_time(value):
    """
    Turn an empty string into None.

    An HTML <input type="time"> sends "" when empty. Saved as-is that
    would make `break_start` an empty string — neither None nor a time —
    and the "is the break fixed" check would go wrong.
    """
    value = (value or "").strip()
    return value or None


# ──── GPS Helper ────
def calculate_distance_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Haversine formula
    The distance between two GPS points, in metres
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
    company_id = current_user["company_id"]

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
    company_id = current_user["company_id"]

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


# ──── Route 2b: Suggest working hours from the policy ────
@router.post("/work-policy/extract")
def extract_work_policy_suggestion(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Read the policy document and SUGGEST working hours.

    This runs by itself on upload (`extract_and_apply_work_policy`).
    This route exists for a retry — and it SAVES NOTHING. The values fill
    the form, the CEO looks at them, can change them, then presses Save.

    Every field carries a `source_quote` — the exact line in the document
    the value came from, so the CEO can verify it.
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == current_user["company_id"],
        CompanyPolicy.is_active == True
    ).first()

    if not active_policy:
        raise HTTPException(
            status_code=400,
            detail="No active policy document — please upload one from the Policy Document tab first"
        )

    try:
        from app.agents.work_policy_extraction_agent import extract_work_policy
        result = extract_work_policy(current_user["company_id"])
    except Exception as e:
        print(f"Work policy extraction failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="The agent is unavailable — please set the working hours manually"
        )

    # ──── Match each suggestion against the current value ────
    # The CEO should see what is CHANGING, not just the new value
    current = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == current_user["company_id"]
    ).first()

    for name, item in result["fields"].items():
        old = getattr(current, name, None) if current else None
        if hasattr(old, "value"):          # Enum (break_policy)
            old = old.value
        item["current_value"] = old
        item["changes"] = old != item["value"]

    return {
        "ran": True,
        "saved": False,          # suggestions only — the CEO presses Save
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
    The CEO sets the office location.
    Modular — it can be changed from Settings.
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = current_user["company_id"]

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
    tenant: Tenant = Depends(get_tenant),
):
    """
    The office, for whoever is asking.

    ═══ THIS WAS THE STRING MATCH, IN FULL ═══
        if user.role == "ceo":
            company_id = user.id                    # a user id as a company
        else:
            ceo = db.query(User).filter(
                User.company_name == user.company_name,   # text
                User.role == "ceo",
            ).first()                                     # ...and the first
            company_id = ceo.id if ceo else None

    An employee of a company sharing its name with another would have
    been handed the OTHER company's office coordinates, and then marked
    out of range every day at their own desk.

    One dependency now, and the same answer everywhere.
    """
    company_id = tenant.company_id

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


# ──── Route 5: GPS verify (called from the employee check-in) ────
@router.post("/verify-location")
def verify_location(
    employee_lat: float,
    employee_lng: float,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    """
    Whether the employee's GPS reading is inside their office radius.

    ═══ THE COMPANY USED TO BE AN ARGUMENT ═══
        def verify_location(employee_lat, employee_lng,
                            company_id: int, ...)

    It was a query parameter with no authentication on the route at all,
    so the caller chose which company to be measured against. Anyone
    could sweep company ids and read back every office's name, radius and
    — from the distances — roughly where each one is.

    The caller no longer says which company. It is the one they are in.
    """
    company_id = tenant.company_id

    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id,
        OfficeLocation.is_active == True
    ).first()

    if not office:
        # ──── No office set → allow it ────
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
    As soon as the policy is indexed, extract and apply the leave types.

    The CEO does not have to press a button — the document is the truth.
    A type missing from the policy is disabled, a new one is created, and
    employees' existing balances are brought in line with the config.

    ═══ THE IMPORTANT GUARD ═══
    If the agent finds NO type at all (or fails outright) then NOTHING is
    changed. Otherwise Groq being down, or one bad PDF, would disable every
    type at once and stop leave for the whole company.

    The outcome is saved into `company_policies.policy_preview` so the CEO
    can see what happened — nothing occurs silently.
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
                or "No leave type was found in the policy document — "
                   "the existing types were left unchanged"
            )
            print(f"[policy] extraction returned nothing — nothing was changed")
        else:
            applied = apply_policy_types(
                session, company_id, extracted, disable_missing=True
            )
            summary.update(applied)
            summary["ran"] = True
            summary["warnings"] = result.get("warnings", [])
            summary["reason"] = f"{len(applied['applied'])} type(s) applied from the policy"
            print(
                f"[policy] types auto-applied: {applied['applied']} "
                f"| band: {applied['disabled']} "
                f"| balances synced: {applied['balances_synced']}"
            )

    except Exception as e:
        session.rollback()
        summary["reason"] = f"Problem while extracting the types: {e}"
        print(f"[policy] leave type extraction failed: {e}")

    _save_policy_preview(session, policy_id, "leave_types", summary)
    return summary


def _save_policy_preview(session, policy_id: int, key: str, summary: dict):
    """
    Store the extraction outcome in `policy_preview` — to show the CEO.

    Two things are extracted from one document (leave types + working
    hours), so each lives under its own key and neither overwrites the other.
    """
    try:
        policy = session.query(CompanyPolicy).filter(
            CompanyPolicy.id == policy_id
        ).first()
        if not policy:
            return

        preview = policy.policy_preview
        # The old format stored the leave summary directly — migrate it to
        # the new shape or that result would be lost
        if not isinstance(preview, dict) or "leave_types" not in preview:
            preview = {"leave_types": preview} if preview else {}

        policy.policy_preview = {**preview, key: summary}
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[policy] {key} summary save failed: {e}")


def extract_and_apply_work_policy(session, company_id: int, policy_text: str = ""):
    """
    Once the policy is indexed, fill in the **working hours** automatically.

    The same rule as leave types, with one big difference:
    for types, "not in the policy" means "disable it", but here it means
    **"leave whatever the CEO set exactly as it is"**.
    Shift timings are essential — if the document is silent, the old value
    mitana galat hoga.

    So: a field that was FOUND is applied; one that was NOT is LEFT ALONE.

    ═══ GUARD ═══
    If the agent finds nothing (or fails), nothing changes. Otherwise Groq
    being down, or one bad PDF, would push the whole company's shift back
    to the 09:00-18:00 default.

    Return: a summary dict — for showing to the CEO
    """
    # Exactly the shape `/work-policy/extract` returns — one UI panel
    # displays both
    summary = {
        "ran": False, "reason": "",
        "fields": {},         # {name: {value, source_quote, confidence, ...}}
        "skipped": [],        # fields that were not in the document at all
        "warnings": [],
    }

    # The same fields that appear on the Working Hours tab
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
        # The payroll rules come from this same result — there is no need
        # to call the LLM again (and calling it again could give a slightly
        # different answer)
        summary["_all_fields"] = found

        if not found:
            summary["reason"] = (
                result.get("error")
                or "No working hours were found in the policy document — "
                   "maujooda settings waise hi rehne di gayin"
            )
            print("[policy] work policy: nothing found — nothing changed")
            return summary

        policy = session.query(CompanyWorkPolicy).filter(
            CompanyWorkPolicy.company_id == company_id
        ).first()

        # ──── If there is no policy at all, create one ────
        # Shift timings are not nullable, so a default has to be set — but
        # only when the document did not supply that field.
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
        summary["saved"] = True          # this has already been applied
        summary["found_count"] = len(summary["fields"])
        summary["reason"] = (
            f"{len(summary['fields'])} field(s) applied from the policy, "
            f"{len(summary['skipped'])} manual"
        )
        print(f"[policy] work policy auto-applied: {list(summary['fields'])}")

    except Exception as e:
        session.rollback()
        summary["reason"] = f"Problem while extracting the working hours: {e}"
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

    # ══════════════════════════════════════════════
    # A background task has no request to inherit from
    # ══════════════════════════════════════════════
    # This runs after the upload response has been sent, on a session it
    # builds itself. Nothing has told that session which company it is
    # working for, so the tenant guard would refuse every query in here —
    # which is the guard doing its job: this function indexes a document
    # and then writes leave types, shift timings and payroll rules, and
    # it must not be able to write them into the wrong company.
    #
    # `company_id` is a parameter this task already received from the
    # route that scheduled it, so the scope is stamped from that.
    bind_tenant(session, company_id)

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

        # ──── Cosine space is required ────
        # ChromaDB defaults to squared L2. On unit vectors L2² = 2 - 2*cos,
        # so `1 - distance` is NOT cosine similarity (cos=0.5 gives 0,
        # cos=0 gives -1). The leave agent's
        # similarity threshold was wrong for exactly that reason.
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

        # ──── The chunks are ready — the policy is now usable ────
        # `is_active` is set here because the Leave Agent runs off it. But
        # `status` stays "processing" for now — see below.
        policy = session.query(CompanyPolicy).filter(CompanyPolicy.id == policy_id).first()
        if policy:
            policy.chunks_indexed = len(chunks)
            policy.vector_collection = collection_name
            policy.indexed_at = datetime.utcnow()
            policy.is_active = True
            session.commit()

        print(f"Policy {policy_id} indexed: {len(chunks)} chunks")

        # ──── Now extract and apply the settings from the policy ────
        # Two separate agents — independent of each other. If one fails the
        # other still runs (each handles its own errors).
        extract_and_apply_leave_types(session, company_id, policy_id, text)

        wp_summary = extract_and_apply_work_policy(session, company_id, text)

        # ──── The payroll rules from the SAME result ────
        # One document, one LLM call — it just lands in two different tables
        pr_summary = extract_and_apply_payroll_rules(
            session, company_id, wp_summary.pop("_all_fields", None)
        )
        _save_policy_preview(session, policy_id, "work_policy", wp_summary)
        _save_policy_preview(session, policy_id, "payroll_rules", pr_summary)

        # ══════════════════════════════════════════
        # status = "active" comes LAST of all
        # ══════════════════════════════════════════
        # This used to be set as soon as the chunks existed — right after
        # indexing, but BEFORE the agents ran. The UI stops waiting on it,
        # so "Policy indexed!" appeared while the leave types and working
        # hours had not been applied yet. The CEO saw the old values (or no
        # panel at all) and had to reload the page.
        #
        # Now `status == "active"` means: **everything is complete**.
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
    company_id = current_user["company_id"]

    filename = file.filename.lower()
    if filename.endswith(".pdf"):
        file_type = "pdf"
    elif filename.endswith(".docx"):
        file_type = "docx"
    else:
        raise HTTPException(status_code=400, detail="Please upload a PDF or DOCX only")

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
        "message": "Policy uploaded — processing...",
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
        raise HTTPException(status_code=404, detail="Policy not found")

    # Older documents stored the leave summary directly (with no key)
    preview = policy.policy_preview
    if isinstance(preview, dict) and "leave_types" in preview:
        leave_summary = preview.get("leave_types")
        work_summary = preview.get("work_policy")
        payroll_summary = preview.get("payroll_rules")
    else:
        leave_summary, work_summary, payroll_summary = preview, None, None

    return {
        "policy_id": policy.id,
        "status": policy.status,
        "chunks_indexed": policy.chunks_indexed,
        "indexed_at": str(policy.indexed_at) if policy.indexed_at else None,
        "is_active": policy.is_active,
        # ──── What was applied automatically from the document ────
        "leave_types": leave_summary,
        "work_policy": work_summary,
        "payroll_rules": payroll_summary,
    }


# ──── Route 8: Active Policy ────
@router.get("/policy/active")
def get_active_policy(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = current_user["company_id"]

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
        payroll_summary = preview.get("payroll_rules")
    else:
        leave_summary = preview
        work_summary = payroll_summary = None

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
            # On opening Settings, the Working Hours tab immediately shows
            # which field came from the document — even after a reload
            "work_policy": work_summary,
            # The same applies to the Payroll → Rules tab. Without this key
            # its "from policy" markers never appeared at all.
            "payroll_rules": payroll_summary,
        }
    }


# ──── Route 8b: List all policies ────
@router.get("/policy/list")
def list_policies(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """Every policy uploaded for the CEO's company — newest first"""
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    policies = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == current_user["company_id"]
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


# ──── Route 8b-2: Reactivate an earlier policy ────
@router.post("/policy/{policy_id}/activate")
def activate_policy(
    policy_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Reactivate a policy that was uploaded earlier.

    ═══ WHAT THE PROBLEM WAS ═══
    Uploading a new policy marked the old one "superseded", with no way to
    go back — the CEO had to upload the SAME file again (creating a new
    version and reindexing) and delete the old one. The file is sitting
    safely on disk — not using it made no sense.

    ═══ AB ═══
    The file is the same, so the same path that runs on upload runs again —
    `process_policy_document`:
        build chunks → reindex ChromaDB (dropping the old collection)
        → leave types laagu → working hours laagu

    So the outcome is exactly what uploading that document would produce.
    A separate path would eventually start behaving differently.

    Old policies are NOT deleted — the CEO can move between them freely.
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()
    company_id = current_user["company_id"]

    policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.id == policy_id,
        CompanyPolicy.company_id == company_id
    ).first()

    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    if policy.is_active:
        raise HTTPException(status_code=400, detail="This policy is already active")

    # ──── The file must exist on disk ────
    # Without it neither the chunks can be built nor can the agent read
    # anything. Better to stop here — otherwise it goes active but empty.
    if not policy.file_path or not os.path.exists(policy.file_path):
        raise HTTPException(
            status_code=400,
            detail=(
                "This policy's file was not found on disk — it cannot be "
                "activated. Please upload it again."
            )
        )

    label = policy.policy_label or policy.file_name

    # ──── Stand every other one down ────
    db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).update({"is_active": False, "status": "superseded"})

    # Clear the old result — otherwise, while reindexing, the UI shows the old
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
        "message": f"'{label}' is being activated — reindexing now",
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
    Delete a policy document — the DB row, the file, and the ChromaDB chunks.

    Removing the chunks is the MOST important part: deleting only the DB
    row while the vector chunks remain would let the Leave Agent keep
    jawab uthata rahega.
    """
    ceo = db.query(User).filter(User.id == current_user["user_id"]).first()

    policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.id == policy_id,
        CompanyPolicy.company_id == current_user["company_id"]
    ).first()

    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    was_active = policy.is_active
    file_path = policy.file_path
    label = policy.policy_label or policy.file_name

    # ──── 1. Preserve the audit trail ────
    # Do NOT delete old leave decisions — just unlink the policy, or the
    # record of "which policy this leave was rejected under" is lost
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
            chroma_client.delete_collection(
                f"company_{current_user['company_id']}_policies")
            chunks_removed = True
        except Exception as e:
            # If the collection does not exist, that is fine
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
        "message": f"'{label}' deleted",
        "policy_id": policy_id,
        "was_active": was_active,
        "chunks_removed": chunks_removed,
        "file_removed": file_removed,
        "note": (
            "There is no active policy now — every leave request will go to the CEO"
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
    company_id = current_user["company_id"]

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

# ══════════════════════════════════════════════
# Payroll rules — straight from the policy document
# ══════════════════════════════════════════════
PAYROLL_RULE_FIELDS = [
    "overtime_multiplier",
    "late_deduction_policy", "late_deduction_amount",
    "undertime_deduction", "unpaid_leave_deduction", "absent_deduction",
    "tax_percentage", "tax_threshold", "provident_fund_percent",
]


def _value_changed(previous, new) -> bool:
    """
    Kya value waqai badli?

    A plain str() comparison will not do: the DB gives `Decimal("10.00")`
    and the agent gives `10.0` — the same meaning, different text. That
    would show the CEO "this changed" every time, even when nothing did.
    Compare numbers as numbers, everything else as text.
    """
    if previous is None:
        return new is not None
    try:
        return Decimal(str(previous)) != Decimal(str(new))
    except (InvalidOperation, TypeError, ValueError):
        return str(previous) != str(new)


def extract_and_apply_payroll_rules(session, company_id: int, found: dict):
    """
    Apply the payroll rules straight from the policy document.

    Exactly the same rule as the working hours:
      · a field FOUND in the document is applied
      · one that is NOT found is left alone (the CEO's value stands)
      · if nothing is found, nothing changes

    ═══ THE LLM IS NOT CALLED AGAIN ═══
    `found` holds the same fields the working-hours agent extracted.
    One document, one LLM call — it simply lands in two different tables.
    Running it again could give a slightly different answer, and
    two places would end up saying two things.
    """
    from app.models.payroll import PayrollPolicy

    summary = {
        "ran": False, "reason": "", "fields": {}, "skipped": [], "warnings": [],
    }

    try:
        mine = {k: v for k, v in (found or {}).items()
                if k in PAYROLL_RULE_FIELDS}

        if not mine:
            summary["reason"] = (
                "No payroll rules were found in the policy document — "
                "maujooda settings waise hi rehne di gayin"
            )
            summary["skipped"] = list(PAYROLL_RULE_FIELDS)
            print("[policy] payroll rules: nothing found")
            return summary

        row = session.query(PayrollPolicy).filter(
            PayrollPolicy.company_id == company_id
        ).first()
        if not row:
            row = PayrollPolicy(company_id=company_id)
            session.add(row)

        for name in PAYROLL_RULE_FIELDS:
            if name not in mine:
                summary["skipped"].append(name)
                continue

            item = mine[name]
            previous = getattr(row, name, None)
            if hasattr(previous, "value"):
                previous = previous.value

            setattr(row, name, item["value"])
            summary["fields"][name] = {
                "value": item["value"],
                "source_quote": item.get("source_quote", ""),
                "confidence": item.get("confidence", "low"),
                "current_value": (float(previous)
                                  if isinstance(previous, Decimal) else previous),
                "changes": _value_changed(previous, item["value"]),
            }

        row.updated_at = datetime.utcnow()
        session.commit()

        summary["ran"] = True
        summary["saved"] = True
        summary["found_count"] = len(summary["fields"])
        summary["reason"] = (
            f"{len(summary['fields'])} payroll rule(s) applied from the policy, "
            f"{len(summary['skipped'])} manual"
        )
        print(f"[policy] payroll rules auto-applied: {list(summary['fields'])}")

    except Exception as e:
        session.rollback()
        summary["reason"] = f"Problem while extracting the payroll rules: {e}"
        print(f"[policy] payroll rules failed: {e}")

    return summary
