import os
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.utils.security import get_current_user
from app.utils.pkt import get_pkt_now, get_pkt_today
from app.utils.workpolicy import count_working_days
from app.utils.documents import prepare_document, DocumentError
from app.utils import notify
from app.utils.company import (
    require_ceo, get_user_or_404, resolve_company_id,
    assert_self, assert_can_view, company_employees,
)
from app.models.attendance import (
    LeaveRequest, LeaveBalance, CompanyPolicyOverride,
    CompanyPolicy, PolicyDecisionLog, CompanyWorkPolicy,
    LeaveDocument, CompanyLeaveType, LeaveStatusEnum,
)
from app.models.user import User

router = APIRouter(prefix="/leave", tags=["Leave"])


# ══════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════
# ══════════════════════════════════════════════
# Default leave types
# ══════════════════════════════════════════════
# Yeh sirf SHURUATI set hai. Har company ke liye ek dafa
# `company_leave_types` mein daal diya jata hai, phir CEO (ya policy
# document se nikli maloomat) inhein badal sakti hai — entitlement 0 kar
# dena, band kar dena, ya bilkul nayi type add kar dena (maternity, hajj...).
DEFAULT_LEAVE_TYPES = [
    # code,        label,             days, unlimited, cert, notice_days, order
    ("annual",     "Annual Leave",      15, False, False, 1, 1),
    ("casual",     "Casual Leave",      10, False, False, 1, 2),
    ("sick",       "Sick Leave",        10, False, True,  0, 3),
    ("emergency",  "Emergency Leave",    3, False, False, 0, 4),
    ("unpaid",     "Unpaid Leave",       0, True,  False, 1, 5),
]

MIN_ADVANCE_DAYS = 1

# Itni lambi request ghalti lagti hai
MAX_LEAVE_SPAN_DAYS = 365

# Sick/emergency itni purani tak backdate ho sakti hai
MAX_BACKDATE_DAYS = 30

# CEO jawab na de to kitni der baad khud approve — policy se aata hai
DEFAULT_AUTO_APPROVE_HOURS = 24

# ──── Reason ────
# Har leave par wajah lazmi hai. CEO ko faisla karna hota hai aur Leave Agent
# bhi reason ko policy ke saath parhta hai — khali reason dono ko andhera
# rakhta hai.
MIN_REASON_LENGTH = 5
MAX_REASON_LENGTH = 1000

# In statuses wali request dates "ghair-khali" samjhi jati hain
ACTIVE_STATUSES = [
    LeaveStatusEnum.evaluating,
    LeaveStatusEnum.pending,
    LeaveStatusEnum.approved,
]


# ══════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════
class CEODecisionSchema(BaseModel):
    ceo_note: Optional[str] = ""


class BalanceAdjustSchema(BaseModel):
    employee_id: int
    leave_type: str
    adjustment: int        # +5 ya -2
    reason: str


class CancelSchema(BaseModel):
    reason: Optional[str] = ""


class LeaveTypeSchema(BaseModel):
    """CEO nayi type banaye ya maujooda badle"""
    code: str
    label: Optional[str] = None
    default_entitlement: int = 0
    is_unlimited: bool = False
    requires_certificate: bool = False
    advance_notice_days: int = 1
    is_enabled: bool = True
    policy_reference: Optional[str] = None


# ══════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════
def _status_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _is_unlimited(db: Session, company_id: int, leave_type: str) -> bool:
    """Is type ka balance mehdood nahi (misal unpaid)?"""
    cfg = leave_type_map(db, company_id).get(_status_value(leave_type))
    return bool(cfg and cfg.is_unlimited)


def get_leave_types(db: Session, company_id: int) -> List[CompanyLeaveType]:
    """
    Company ki leave types — pehli baar defaults se seed kar deti hain.

    Yahi ek jagah hai jahan se types aati hain; code mein kahin hardcoded
    list nahi rahi.
    """
    types = db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == company_id
    ).order_by(CompanyLeaveType.sort_order, CompanyLeaveType.code).all()

    if types:
        return types

    # ──── Pehli dafa — defaults daal do ────
    for code, label, days, unlimited, cert, notice, order in DEFAULT_LEAVE_TYPES:
        db.add(CompanyLeaveType(
            company_id=company_id, code=code, label=label,
            default_entitlement=days, is_unlimited=unlimited,
            requires_certificate=cert, advance_notice_days=notice,
            is_enabled=True, source="default", sort_order=order,
        ))
    db.commit()

    return db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == company_id
    ).order_by(CompanyLeaveType.sort_order, CompanyLeaveType.code).all()


def leave_type_map(db: Session, company_id: int) -> dict:
    """{code: CompanyLeaveType}"""
    return {t.code: t for t in get_leave_types(db, company_id)}


def _validate_leave_type(db: Session, company_id: int, leave_type: str) -> CompanyLeaveType:
    """Type company ki list mein hai aur chalu hai?"""
    lt = (leave_type or "").strip().lower()
    types = leave_type_map(db, company_id)

    config = types.get(lt)
    if not config:
        allowed = ", ".join(sorted(t.code for t in types.values() if t.is_enabled))
        raise HTTPException(
            status_code=400,
            detail=f"'{lt}' is company mein maujood nahi. Chalne wali types: {allowed}"
        )

    if not config.is_enabled:
        raise HTTPException(
            status_code=400,
            detail=f"{config.label} is waqt band hai — CEO se raabta karein"
        )

    return config


def _validate_reason(reason: str) -> str:
    """Leave ki wajah lazmi hai — CEO aur agent dono isi par faisla karte hain"""
    text = (reason or "").strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Leave ki wajah likhna zaroori hai"
        )
    if len(text) < MIN_REASON_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Wajah thori tafseel se likhein (kam se kam "
                   f"{MIN_REASON_LENGTH} harf)"
        )
    return text[:MAX_REASON_LENGTH]


def _suggest_type(types: List[CompanyLeaveType], **match) -> Optional[CompanyLeaveType]:
    """
    Employee ko concrete mashwara dene ke liye: company ki types mein se
    aisi type dhundo jo di hui shart par poori utarti ho.

    Misal: advance notice waali rok lagi to batao ke "Sick Leave use karein" —
    magar sirf tab jab us company mein waqai koi bina-notice type maujood ho.
    """
    for t in types:
        if not t.is_enabled:
            continue
        if all(getattr(t, k, None) == v for k, v in match.items()):
            return t
    return None


def _parse_dates(start_date: str, end_date: str, config: CompanyLeaveType,
                 all_types: Optional[List[CompanyLeaveType]] = None):
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format YYYY-MM-DD hona chahiye")

    if end < start:
        raise HTTPException(status_code=400, detail="End date start date se pehle nahi ho sakti")

    span = (end - start).days + 1
    if span > MAX_LEAVE_SPAN_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Leave {MAX_LEAVE_SPAN_DAYS} din se zyada nahi ho sakti"
        )

    today = get_pkt_today()

    notice = config.advance_notice_days or 0

    if notice > 0:
        # ──── Planned leave — itne din pehle apply karni hai ────
        earliest = today + timedelta(days=notice)
        if start < earliest:
            # ──── Bina notice wali koi type ho to naam le kar batao ────
            alt = _suggest_type(all_types or [], advance_notice_days=0)
            tip = (
                f"Aaj ki chhutti chahiye to {alt.label} use karein."
                if alt else
                "Aaj ki chhutti ke liye koi type maujood nahi — CEO se raabta karein."
            )
            raise HTTPException(
                status_code=400,
                detail=f"{config.label} kam se kam {notice} din pehle apply "
                       f"karni hoti hai — sab se pehli mumkin tareekh {earliest} "
                       f"hai. {tip}"
            )
    else:
        # ──── Usi din ki, ya kuch din purani bhi ────
        if start < today - timedelta(days=MAX_BACKDATE_DAYS):
            raise HTTPException(
                status_code=400,
                detail=f"{MAX_BACKDATE_DAYS} din se purani leave apply nahi ho sakti"
            )

    return start, end, span


def _auto_approve_hours(policy) -> int:
    """CEO ko jawab dene ke liye kitne ghante — 0 matlab kabhi auto nahi"""
    if not policy or policy.leave_auto_approve_hours is None:
        return DEFAULT_AUTO_APPROVE_HOURS
    return max(0, policy.leave_auto_approve_hours)


def get_or_create_balance(
    db: Session,
    employee_id: int,
    company_id: int,
    leave_type: str,
    year: int,
    config: Optional[CompanyLeaveType] = None,
) -> LeaveBalance:
    """
    Balance row lo, na ho to company ki configured entitlement ke saath bana do.

    Entitlement ab hardcoded nahi — `company_leave_types` se aati hai.
    Jo type policy mein na ho uski entitlement 0 hoti hai, to balance bhi
    0/0 banta hai aur employee us par apply nahi kar sakta.
    """
    balance = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.leave_type == leave_type,
        LeaveBalance.year == year
    ).first()

    if not balance:
        if config is None:
            config = leave_type_map(db, company_id).get(leave_type)

        if config is None:
            entitlement = 0
        elif config.is_unlimited:
            entitlement = 999
        else:
            entitlement = config.default_entitlement or 0

        balance = LeaveBalance(
            employee_id=employee_id,
            company_id=company_id,
            year=year,
            leave_type=leave_type,
            total_entitlement=entitlement,
            used_days=0,
            remaining_days=entitlement,
            last_updated=get_pkt_now(),
        )
        db.add(balance)
        db.flush()

    return balance


def sync_balances_to_config(db: Session, company_id: int, cfg: CompanyLeaveType,
                            year: Optional[int] = None) -> int:
    """
    Type ki entitlement badle to employees ki MAUJOODA balance rows bhi badlo.

    Yeh zaroori hai: `get_or_create_balance` config sirf tab parhta hai jab
    row PEHLI DAFA banti hai. Us ke baad CEO entitlement 0 kar de to purani
    rows mein purana adad para rehta hai aur employee ko card dikhta rehta
    hai — jaise kuch badla hi na ho.

    `used_days` ko haath nahi lagate — wo guzri hui haqeeqat hai.
    Remaining dobara hisaab hota hai: max(0, entitlement - used).
    """
    year = year or get_pkt_today().year
    new_entitlement = 999 if cfg.is_unlimited else (cfg.default_entitlement or 0)

    rows = db.query(LeaveBalance).filter(
        LeaveBalance.company_id == company_id,
        LeaveBalance.leave_type == cfg.code,
        LeaveBalance.year == year,
    ).all()

    changed = 0
    for b in rows:
        if b.total_entitlement == new_entitlement:
            continue
        b.total_entitlement = new_entitlement
        b.remaining_days = max(0, new_entitlement - (b.used_days or 0))
        b.last_updated = get_pkt_now()
        changed += 1

    return changed


def _company_ceo(db: Session, company_id: int) -> Optional[User]:
    """Notification bhejne ke liye CEO ka record — company_id hi uski id hai"""
    return db.query(User).filter(User.id == company_id).first()


def _leave_type_label(db: Session, company_id: int, code: str) -> str:
    """Email mein 'annual' ke bajaye 'Annual Leave' likha jaye"""
    cfg = leave_type_map(db, company_id).get(_status_value(code))
    return cfg.label if cfg else _status_value(code).replace("_", " ").title()


def _find_overlap(db: Session, employee_id: int, start: date, end: date,
                  exclude_id: Optional[int] = None) -> Optional[LeaveRequest]:
    """
    In dates pe pehle se koi zinda request to nahi.
    Overlap ka rule: (existing.start <= new.end) AND (existing.end >= new.start)
    """
    query = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_(ACTIVE_STATUSES),
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    )
    if exclude_id:
        query = query.filter(LeaveRequest.id != exclude_id)
    return query.first()


def _deduct(balance: LeaveBalance, days: int):
    balance.used_days = (balance.used_days or 0) + days
    balance.remaining_days = max(0, (balance.total_entitlement or 0) - balance.used_days)
    balance.last_updated = get_pkt_now()


def _restore(balance: LeaveBalance, days: int):
    balance.used_days = max(0, (balance.used_days or 0) - days)
    balance.remaining_days = max(0, (balance.total_entitlement or 0) - balance.used_days)
    balance.last_updated = get_pkt_now()


# ══════════════════════════════════════════════
# Deferred auto-approve
# ══════════════════════════════════════════════
def _auto_approve_overdue(db: Session, company_id: int) -> int:
    """
    CEO ne muqarrara waqt mein jawab nahi diya → balance ho to khud approve.

    Har leave request pehle CEO ke paas jati hai (us din koi zaroori meeting
    ho sakti hai jo sirf CEO ko pata ho). Magar employee hamesha ke liye
    latka nahi rehna chahiye — isliye deadline ke baad khud approve.

    NAHI hoti agar:
      - CEO ne us leave type pe manual override lagaya ho (usne saaf kaha
        hai "main khud dekhunga")
      - balance kam ho
      - policy mein hours = 0 ho (CEO ne auto-approve band kar diya)

    Yeh function READ endpoints se chalta hai (koi cron nahi). Fail ho jaye
    to listing nahi rukni chahiye — isliye caller try/except mein chalata hai.
    """
    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()

    hours = _auto_approve_hours(policy)
    if hours == 0:
        return 0

    now = get_pkt_now()
    cutoff = now - timedelta(hours=hours)

    stale = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.status == LeaveStatusEnum.pending,
        LeaveRequest.created_at <= cutoff,
    ).all()

    if not stale:
        return 0

    # ──── Jin types pe CEO ne khud dekhne ka kaha hai ────
    overridden = {
        _status_value(o.leave_type)
        for o in db.query(CompanyPolicyOverride).filter(
            CompanyPolicyOverride.company_id == company_id,
            CompanyPolicyOverride.force_manual == True
        ).all()
    }

    ceo = _company_ceo(db, company_id)
    types = leave_type_map(db, company_id)
    notify_queue = []          # commit ke BAAD bhejenge

    approved = 0
    for req in stale:
        leave_type = _status_value(req.leave_type)
        if leave_type in overridden:
            continue

        days = req.deductible_days or req.total_days
        balance = get_or_create_balance(
            db, req.employee_id, company_id, leave_type, req.start_date.year
        )

        if not _is_unlimited(db, company_id, leave_type):
            if balance.remaining_days < days:
                # ──── Balance kam — CEO hi faisla kare ────
                continue
            _deduct(balance, days)

        req.status = LeaveStatusEnum.approved
        req.auto_approved = True
        req.decided_at = now
        req.payroll_notified = True
        req.ceo_note = (
            f"Auto-approved — CEO ne {hours} ghante mein jawab nahi diya"
        )
        approved += 1

        # ──── Email ka saman jama karo (abhi mat bhejo — commit baqi hai) ────
        employee = db.query(User).filter(User.id == req.employee_id).first()
        cfg = types.get(leave_type)
        notify_queue.append({
            "employee": employee,
            "label": cfg.label if cfg else leave_type,
            "start": req.start_date, "end": req.end_date, "days": days,
            "remaining": balance.remaining_days,
        })

    if approved:
        db.commit()
        print(f"[leave] {approved} request(s) auto-approved (company {company_id})")

        # ──── Ab bhejo — DB mehfooz hone ke baad ────
        # Warna commit fail ho jaye aur email ja chuki ho, to employee ko
        # aisi approval ki khabar milti jo hui hi nahi
        for item in notify_queue:
            emp = item["employee"]
            if emp and emp.email:
                notify.leave_decision_to_employee(
                    employee_email=emp.email,
                    employee_name=emp.full_name or "Employee",
                    decision="approved",
                    leave_type=item["label"],
                    start=item["start"], end=item["end"], days=item["days"],
                    remaining=item["remaining"],
                    auto=True,
                    company=emp.company_name or "",
                )
            if ceo and ceo.email:
                notify.leave_auto_approved_to_ceo(
                    ceo_email=ceo.email,
                    ceo_name=ceo.full_name or "CEO",
                    employee_name=emp.full_name if emp else "Employee",
                    leave_type=item["label"],
                    start=item["start"], end=item["end"], days=item["days"],
                    hours=hours,
                    company=ceo.company_name or "",
                )

    return approved


def _run_auto_approve(db: Session, company_id: Optional[int]):
    """Listing dikhane se pehle overdue requests nipta do — chup chaap"""
    if not company_id:
        return
    try:
        _auto_approve_overdue(db, company_id)
    except Exception as e:
        # ──── Sweep fail ho to bhi listing chalni chahiye ────
        db.rollback()
        print(f"[leave] auto-approve sweep failed: {e}")


def _auto_approve_at(req: LeaveRequest, hours: int) -> Optional[str]:
    """Yeh request kab khud approve ho jayegi (pending ho to)"""
    if hours == 0 or not req.created_at:
        return None
    if _status_value(req.status) != "pending":
        return None
    return str(req.created_at + timedelta(hours=hours))


def _leave_out(req: LeaveRequest, employee: Optional[User] = None,
               log: Optional[PolicyDecisionLog] = None,
               doc: Optional["LeaveDocument"] = None,
               has_doc: Optional[bool] = None) -> dict:
    """
    Har jagah ek hi shape — frontend ko andaza na lagana pade.

    has_doc: documents table se aaya hua jawab. None ho to purane
    file-path column pe fall back karte hain (legacy rows).
    """
    if has_doc is None:
        has_doc = doc is not None or req.medical_certificate is not None

    return {
        "leave_id": req.id,
        "employee_id": req.employee_id,
        "employee_name": employee.full_name if employee else None,
        "department": employee.department if employee else None,

        "leave_type": _status_value(req.leave_type),
        "start_date": str(req.start_date),
        "end_date": str(req.end_date),
        "total_days": req.total_days,
        "deductible_days": req.deductible_days if req.deductible_days is not None else req.total_days,

        "reason": req.reason,
        "status": _status_value(req.status),
        "auto_approved": req.auto_approved,
        "has_medical_cert": has_doc,
        "certificate_name": doc.file_name if doc else None,

        "decided_by": req.decided_by,
        "decided_at": str(req.decided_at) if req.decided_at else None,
        "ceo_note": req.ceo_note,
        "created_at": str(req.created_at) if req.created_at else None,

        # ──── Agent ka faisla (audit trail) ────
        "agent_decision": log.decision if log else None,
        "agent_reason": log.reason if log else None,
        "policy_reference": log.policy_reference if log else None,
    }


def _logs_for(db: Session, leave_ids: List[int]) -> dict:
    """Sab decision logs ek query mein (N+1 se bachne ke liye)"""
    if not leave_ids:
        return {}
    logs = db.query(PolicyDecisionLog).filter(
        PolicyDecisionLog.leave_request_id.in_(leave_ids)
    ).all()
    return {l.leave_request_id: l for l in logs}


def _docs_for(db: Session, leave_ids: List[int]) -> dict:
    """
    Kis request ke saath document laga hai — ek query mein.
    `file_data` deliberately load NAHI karte, warna listing megabytes uthati.
    """
    if not leave_ids:
        return {}
    rows = db.query(
        LeaveDocument.leave_request_id,
        LeaveDocument.file_name,
        LeaveDocument.mime_type,
    ).filter(LeaveDocument.leave_request_id.in_(leave_ids)).all()

    return {
        r[0]: {"file_name": r[1], "mime_type": r[2]}
        for r in rows
    }


def _leave_rows(db: Session, requests: List[LeaveRequest],
                employees: dict = None, auto_hours: int = 0) -> List[dict]:
    """Requests ki list ko rows mein badlo — logs aur docs ek-ek query mein"""
    ids = [r.id for r in requests]
    logs = _logs_for(db, ids)
    docs = _docs_for(db, ids)
    employees = employees or {}

    rows = []
    for r in requests:
        doc_meta = docs.get(r.id)
        row = _leave_out(
            r,
            employees.get(r.employee_id),
            logs.get(r.id),
            has_doc=doc_meta is not None or r.medical_certificate is not None,
        )
        row["certificate_name"] = doc_meta["file_name"] if doc_meta else None
        row["auto_approve_at"] = _auto_approve_at(r, auto_hours)
        rows.append(row)
    return rows


# ══════════════════════════════════════════════
# Route 1: Leave Request Submit
# ══════════════════════════════════════════════
@router.post("/request")
async def submit_leave_request(
    employee_id: int = Form(...),
    leave_type: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    reason: str = Form(""),
    # ↑ FastAPI ke level par optional rakha hai jaan bujh kar —
    #   `Form(...)` khali value par raw 422 "Field required" deta hai.
    #   `_validate_reason()` neeche saaf Hinglish message ke saath 400 deta hai.
    medical_certificate: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Employee leave request kare → Leave Agent (RAG + LLM) evaluate karega.

    Agent se PEHLE kuch deterministic guards chalte hain — LLM ko wahi
    faisle karne dete hain jo waqai judgment maangte hain.
    """
    # ──── Apne liye hi (CEO kisi aur ke naam pe bhi laga sakta hai) ────
    if current_user["role"] not in ("ceo", "superadmin"):
        assert_self(current_user, employee_id, "leave apply")

    employee = get_user_or_404(db, employee_id)
    company_id = resolve_company_id(db, employee) or employee_id

    reason = _validate_reason(reason)
    all_types = get_leave_types(db, company_id)
    type_config = _validate_leave_type(db, company_id, leave_type)
    leave_type = type_config.code
    start, end, total_days = _parse_dates(
        start_date, end_date, type_config, all_types)

    # ──── Overlap check ────
    clash = _find_overlap(db, employee_id, start, end)
    if clash:
        raise HTTPException(
            status_code=400,
            detail=f"In dates pe pehle se ek request maujood hai "
                   f"({clash.start_date} se {clash.end_date}, status: {_status_value(clash.status)})"
        )

    # ──── Working days ginno — balance sirf inhi ka katega ────
    work_policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()
    deductible_days = count_working_days(work_policy, start, end)

    if deductible_days == 0:
        raise HTTPException(
            status_code=400,
            detail="In dates mein koi working day nahi hai — leave ki zarurat nahi"
        )

    # ──── Medical certificate — DB ke liye tayyar karo ────
    # Yahan sirf validate + compress karte hain. DB mein tab daalte hain
    # jab leave request ki id mil jaye (neeche).
    prepared_cert = None
    if medical_certificate and medical_certificate.filename:
        try:
            prepared_cert = prepare_document(
                medical_certificate.filename,
                await medical_certificate.read()
            )
        except DocumentError as e:
            raise HTTPException(status_code=400, detail=str(e))

    has_medical_cert = prepared_cert is not None

    # ──── Balance (leave ke SAAL ka, aaj ka nahi) ────
    balance = get_or_create_balance(db, employee_id, company_id, leave_type, start.year)

    # ═══ Balance bilkul khatam → request banti hi nahi ═══
    # Pehle yeh request CEO ke paas chali jati thi. Magar jab CEO ne khud
    # is type ka balance 0 kiya hai to us se dobara poochne ka koi matlab
    # nahi — employee ko wahin rok do.
    if not type_config.is_unlimited and balance.remaining_days <= 0:
        alt = _suggest_type(all_types, is_unlimited=True)
        tip = (f"{alt.label} use karein ya CEO se balance barhwayein"
               if alt else "CEO se balance barhwayein")
        raise HTTPException(
            status_code=400,
            detail=f"{type_config.label} ka balance khatam hai "
                   f"(0/{balance.total_entitlement} din). Is type par apply "
                   f"nahi ho sakti — {tip}."
        )

    # ═══ Sick leave → medical certificate lazmi ═══
    if type_config.requires_certificate and not prepared_cert:
        alt = _suggest_type(all_types, requires_certificate=False,
                            advance_notice_days=type_config.advance_notice_days)
        tip = (f" Certificate na ho to {alt.label} use karein." if alt else "")
        raise HTTPException(
            status_code=400,
            detail=f"{type_config.label} ke saath medical certificate lagana "
                   f"zaroori hai (PDF ya image).{tip}"
        )

    # ──── Request save ────
    leave_req = LeaveRequest(
        employee_id=employee_id,
        company_id=company_id,
        leave_type=leave_type,
        start_date=start,
        end_date=end,
        total_days=total_days,
        deductible_days=deductible_days,
        reason=reason,
        status=LeaveStatusEnum.evaluating,
        created_at=get_pkt_now(),
    )
    db.add(leave_req)
    db.flush()          # leave_req.id chahiye document ke liye

    if prepared_cert:
        db.add(LeaveDocument(
            leave_request_id=leave_req.id,
            employee_id=employee_id,
            company_id=company_id,
            doc_type="medical_certificate",
            file_data=prepared_cert["data"],
            file_name=prepared_cert["file_name"],
            mime_type=prepared_cert["mime_type"],
            file_size_bytes=prepared_cert["size_bytes"],
            width=prepared_cert["width"],
            height=prepared_cert["height"],
            sha256=prepared_cert["sha256"],
            uploaded_at=get_pkt_now(),
        ))

    db.commit()
    db.refresh(leave_req)

    auto_hours = _auto_approve_hours(work_policy)

    def to_ceo(reason_text: str, agent_result: Optional[dict] = None,
               recommendation: Optional[str] = None):
        """
        Har request CEO ke paas jati hai — koi foran auto-approve nahi.

        Wajah: jis din employee chhutti maang raha hai us din koi zaroori
        meeting ho sakti hai, aur wo sirf CEO ko pata hoti hai. Policy
        theek bhi ho to bhi CEO ko dekhne ka mauqa milna chahiye.
        Balance maujood ho to deadline ke baad khud approve ho jayegi.
        """
        leave_req.status = LeaveStatusEnum.pending
        if agent_result:
            _save_decision_log(db, leave_req, company_id, agent_result)
        db.commit()
        db.refresh(leave_req)

        auto_at = _auto_approve_at(leave_req, auto_hours)

        # ──── CEO ko ittila — background mein, request nahi rukti ────
        ceo = _company_ceo(db, company_id)
        if ceo and ceo.email:
            notify.leave_submitted_to_ceo(
                ceo_email=ceo.email,
                ceo_name=ceo.full_name or "CEO",
                employee_name=employee.full_name or "Employee",
                leave_type=type_config.label,
                start=start, end=end, days=deductible_days,
                reason=reason,
                auto_approve_at=auto_at,
                agent_note=(agent_result or {}).get("reason", "") if agent_result else "",
                company=employee.company_name or "",
            )

        return {
            "message": "Leave request bhej di gayi — CEO approval ka intezar",
            "leave_id": leave_req.id,
            "status": "pending",
            "reason": reason_text,
            # ──── Agent ne kya mashwara diya (faisla nahi) ────
            "agent_recommendation": recommendation,
            "policy_reference": (agent_result or {}).get("policy_reference", ""),
            # ──── CEO chup raha to kab khud approve hogi ────
            "auto_approve_at": auto_at,
            "auto_approve_hours": auto_hours if auto_at else None,
            "total_days": total_days,
            "deductible_days": deductible_days,
        }

    # ═══ Guard 1: CEO ne is leave type pe manual override lagaya hai ═══
    override = db.query(CompanyPolicyOverride).filter(
        CompanyPolicyOverride.company_id == company_id,
        CompanyPolicyOverride.leave_type == leave_type,
        CompanyPolicyOverride.force_manual == True
    ).first()
    if override:
        return to_ceo(
            f"Manual override active: {override.reason or 'CEO review required'}",
            recommendation="manual_only",
        )

    # ═══ Guard 2: Balance poora nahi par ra ═══
    # Bilkul zero upar hi block ho chuka. Yahan wo surat hai jahan kuch din
    # bache hain magar poore nahi (misal 2 bache, 4 chahiye) — CEO faisla kare
    # ke baqi din unpaid treat karne hain ya nahi. Agent ko bulane ki zarurat
    # nahi, yeh arithmetic hai.
    if not type_config.is_unlimited and balance.remaining_days < deductible_days:
        return to_ceo(
            f"Balance poora nahi — {deductible_days} working days chahiye, "
            f"sirf {balance.remaining_days} bache hain",
            recommendation="insufficient_balance",
        )

    # ═══ Guard 3: Policy document hi upload nahi hui ═══
    # Bina policy ke agent koi mashwara nahi de sakta
    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).first()
    if not active_policy:
        return to_ceo(
            "Company policy document upload nahi hui — agent mashwara nahi de saka"
        )

    # ═══ Leave Agent (RAG + LLM) ═══
    try:
        # Import bhi try ke andar — GROQ_API_KEY missing ho ya koi ML
        # package toota ho to poori request 500 na ho jaye
        from app.agents.leave_agent import evaluate_leave_request

        agent_result = evaluate_leave_request(
            employee_id=employee_id,
            company_id=company_id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            total_days=deductible_days,
            reason=reason,
            has_medical_cert=has_medical_cert,
            leave_balance=balance.remaining_days,
        )
    except Exception as e:
        # ──── Groq down ho ya kuch bhi phate — request phansi na rahe ────
        print(f"Leave agent failed: {e}")
        return to_ceo("Agent abhi dastyab nahi — CEO manually review karega")

    # ═══ Agent ka faisla ab MASHWARA hai, hukm nahi ═══
    # Policy theek bhi ho to bhi request CEO ke paas jayegi
    recommendation = (
        "approve" if agent_result.get("decision") == "auto_approve" else "review"
    )
    return to_ceo(
        agent_result.get("reason", "CEO review required"),
        agent_result,
        recommendation,
    )


def _save_decision_log(db: Session, leave_req: LeaveRequest,
                       company_id: int, agent_result: dict):
    """RAG + LLM ka poora audit trail — baad mein sawal ho to jawab mile"""
    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).first()

    db.add(PolicyDecisionLog(
        leave_request_id=leave_req.id,
        policy_id=active_policy.id if active_policy else None,
        retrieval_query=agent_result.get("retrieval_query", ""),
        retrieved_chunks=agent_result.get("retrieved_chunks", []),
        llm_response={
            "decision": agent_result.get("decision"),
            "reason": agent_result.get("reason"),
            "policy_reference": agent_result.get("policy_reference"),
            "error": agent_result.get("error", ""),
        },
        decision=agent_result.get("decision"),
        reason=agent_result.get("reason"),
        policy_reference=agent_result.get("policy_reference"),
        decided_at=get_pkt_now(),
    ))


# ══════════════════════════════════════════════
# Route 2: CEO — Pending Requests
# ══════════════════════════════════════════════
@router.get("/pending")
def get_pending_leaves(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = get_user_or_404(db, current_user["user_id"])

    # ──── Jo requests deadline paar kar chuki hain wo pehle nipta do ────
    _run_auto_approve(db, ceo.id)

    pending = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == ceo.id,
        LeaveRequest.status == LeaveStatusEnum.pending
    ).order_by(LeaveRequest.created_at.desc()).all()

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == ceo.id
    ).first()
    auto_hours = _auto_approve_hours(policy)

    employees = {e.id: e for e in company_employees(db, ceo)}
    result = _leave_rows(db, pending, employees, auto_hours)

    # ──── CEO ko balance bhi dikhe faisla lete waqt ────
    for row, req in zip(result, pending):
        balance = db.query(LeaveBalance).filter(
            LeaveBalance.employee_id == req.employee_id,
            LeaveBalance.leave_type == req.leave_type,
            LeaveBalance.year == req.start_date.year
        ).first()
        row["remaining_balance"] = balance.remaining_days if balance else None
        row["total_entitlement"] = balance.total_entitlement if balance else None

        # ──── employee CEO ki list mein na ho (misal khud CEO) ────
        if not row["employee_name"]:
            emp = db.query(User).filter(User.id == req.employee_id).first()
            row["employee_name"] = emp.full_name if emp else "—"

    return {
        "total": len(result),
        "auto_approve_hours": auto_hours,
        "pending_requests": result,
    }


# ══════════════════════════════════════════════
# Route 3: CEO — Approve
# ══════════════════════════════════════════════
@router.post("/approve/{leave_id}")
def approve_leave(
    leave_id: int,
    data: CEODecisionSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = get_user_or_404(db, current_user["user_id"])

    leave_req = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.company_id == ceo.id,
        LeaveRequest.status == LeaveStatusEnum.pending
    ).first()

    if not leave_req:
        raise HTTPException(status_code=404, detail="Pending request nahi mili")

    days = leave_req.deductible_days or leave_req.total_days
    balance = get_or_create_balance(
        db, leave_req.employee_id, leave_req.company_id,
        leave_req.leave_type, leave_req.start_date.year
    )

    leave_req.status = LeaveStatusEnum.approved
    leave_req.decided_by = current_user["user_id"]
    leave_req.decided_at = get_pkt_now()
    leave_req.ceo_note = data.ceo_note
    leave_req.payroll_notified = True

    # ──── Balance kam ho to bhi CEO ka faisla chalega, magar chupke se
    #      deduct chhodna galat hai — batao kitna over ho gaya ────
    over_limit = False
    if not _is_unlimited(db, leave_req.company_id, leave_req.leave_type):
        over_limit = balance.remaining_days < days
        _deduct(balance, days)

    db.commit()

    # ──── Employee ko batao ────
    employee = db.query(User).filter(User.id == leave_req.employee_id).first()
    if employee and employee.email:
        notify.leave_decision_to_employee(
            employee_email=employee.email,
            employee_name=employee.full_name or "Employee",
            decision="approved",
            leave_type=_leave_type_label(db, leave_req.company_id, leave_req.leave_type),
            start=leave_req.start_date, end=leave_req.end_date, days=days,
            note=data.ceo_note or "",
            remaining=balance.remaining_days,
            company=employee.company_name or "",
        )

    return {
        "message": "Leave approved!",
        "leave_id": leave_id,
        "status": "approved",
        "days_deducted": days,
        "remaining_balance": balance.remaining_days,
        "over_entitlement": over_limit,
        "note": "Balance se zyada approve hui — unpaid treat karein" if over_limit else None,
    }


# ══════════════════════════════════════════════
# Route 4: CEO — Reject
# ══════════════════════════════════════════════
@router.post("/reject/{leave_id}")
def reject_leave(
    leave_id: int,
    data: CEODecisionSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = get_user_or_404(db, current_user["user_id"])

    leave_req = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.company_id == ceo.id,
        LeaveRequest.status == LeaveStatusEnum.pending
    ).first()

    if not leave_req:
        raise HTTPException(status_code=404, detail="Pending request nahi mili")

    # ──── Reject ki wajah lazmi — employee ko pata to chale kyun hui ────
    note = (data.ceo_note or "").strip()
    if not note:
        raise HTTPException(
            status_code=400,
            detail="Reject karne ki wajah likhna zaroori hai — employee ko yahi dikhegi"
        )

    leave_req.status = LeaveStatusEnum.rejected
    leave_req.decided_by = current_user["user_id"]
    leave_req.decided_at = get_pkt_now()
    leave_req.ceo_note = note

    db.commit()

    # ──── Employee ko wajah ke saath batao ────
    employee = db.query(User).filter(User.id == leave_req.employee_id).first()
    if employee and employee.email:
        notify.leave_decision_to_employee(
            employee_email=employee.email,
            employee_name=employee.full_name or "Employee",
            decision="rejected",
            leave_type=_leave_type_label(db, leave_req.company_id, leave_req.leave_type),
            start=leave_req.start_date, end=leave_req.end_date,
            days=leave_req.deductible_days or leave_req.total_days,
            note=note,
            company=employee.company_name or "",
        )

    return {"message": "Leave rejected!", "leave_id": leave_id, "status": "rejected"}


# ══════════════════════════════════════════════
# Route 5: Cancel (employee ya CEO)
# ══════════════════════════════════════════════
@router.post("/cancel/{leave_id}")
def cancel_leave(
    leave_id: int,
    data: CancelSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Pending request kabhi bhi cancel ho sakti hai.

    Approved leave employee sirf tab cancel kar sakta hai jab wo abhi SHURU
    na hui ho — 12 tareekh ki chhutti 12 ko cancel karne ka matlab nahi,
    us din ki attendance ka hisaab pehle hi badal chuka hota hai.

    CEO shuru ho chuki leave bhi cancel kar sakta hai (misal banda asal mein
    aa gaya) — dono surat mein balance wapis mil jata hai.
    """
    leave_req = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if not leave_req:
        raise HTTPException(status_code=404, detail="Leave request nahi mili")

    assert_can_view(db, current_user, leave_req.employee_id)

    status = _status_value(leave_req.status)
    if status in ("rejected", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Yeh request pehle se {status} hai")

    today = get_pkt_today()
    is_ceo = current_user["role"] in ("ceo", "superadmin")

    if status == "approved" and leave_req.start_date <= today and not is_ceo:
        raise HTTPException(
            status_code=400,
            detail=f"Leave {leave_req.start_date} ko shuru ho chuki hai — "
                   f"ab employee khud cancel nahi kar sakta. CEO cancel kar sakta hai."
        )

    # ──── Approved thi to balance wapis karo ────
    restored = 0
    if status == "approved" and not _is_unlimited(
            db, leave_req.company_id, leave_req.leave_type):
        balance = get_or_create_balance(
            db, leave_req.employee_id, leave_req.company_id,
            leave_req.leave_type, leave_req.start_date.year
        )
        restored = leave_req.deductible_days or leave_req.total_days
        _restore(balance, restored)

    leave_req.status = LeaveStatusEnum.cancelled
    leave_req.decided_at = get_pkt_now()
    leave_req.ceo_note = (data.reason or "").strip() or leave_req.ceo_note
    db.commit()

    # ──── Employee ne cancel ki to CEO ko batao (CEO ne ki to nahi) ────
    ceo = _company_ceo(db, leave_req.company_id)
    employee = db.query(User).filter(User.id == leave_req.employee_id).first()
    if ceo and ceo.email and employee:
        notify.leave_cancelled_to_ceo(
            ceo_email=ceo.email,
            ceo_name=ceo.full_name or "CEO",
            employee_name=employee.full_name or "Employee",
            leave_type=_leave_type_label(db, leave_req.company_id, leave_req.leave_type),
            start=leave_req.start_date, end=leave_req.end_date,
            by_ceo=is_ceo,
            company=ceo.company_name or "",
        )

    return {
        "message": "Leave cancel ho gayi",
        "leave_id": leave_id,
        "status": "cancelled",
        "days_restored": restored,
    }


# ══════════════════════════════════════════════
# Route 6: Leave Balance
# ══════════════════════════════════════════════
@router.get("/balance/{employee_id}")
def get_leave_balance(
    employee_id: int,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    employee = assert_can_view(db, current_user, employee_id)
    year = year or get_pkt_today().year
    company_id = resolve_company_id(db, employee) or employee_id

    # ──── Types company ki config se — hardcoded list nahi ────
    balances = []
    for cfg in get_leave_types(db, company_id):
        if not cfg.is_enabled:
            continue

        b = get_or_create_balance(db, employee_id, company_id, cfg.code, year, cfg)
        balances.append({
            "leave_type": cfg.code,
            "label": cfg.label,
            "total_entitlement": b.total_entitlement,
            "used_days": b.used_days,
            "remaining_days": b.remaining_days,
            "unlimited": cfg.is_unlimited,
            # ──── Is type ke apne rules — UI inhi se guide karta hai ────
            "requires_certificate": cfg.requires_certificate,
            "advance_notice_days": cfg.advance_notice_days or 0,
            "source": cfg.source,
            "policy_reference": cfg.policy_reference,
        })
    db.commit()

    # ──── Abhi zer-e-ghaur requests (balance abhi kata nahi) ────
    pending_days = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_([LeaveStatusEnum.pending, LeaveStatusEnum.evaluating]),
    ).count()

    return {
        "employee_id": employee_id,
        "employee_name": employee.full_name,
        "year": year,
        "balances": balances,
        "pending_requests": pending_days,
    }


# ══════════════════════════════════════════════
# Route 7: Leave History
# ══════════════════════════════════════════════
@router.get("/history/{employee_id}")
def get_leave_history(
    employee_id: int,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    employee = assert_can_view(db, current_user, employee_id)
    company_id = resolve_company_id(db, employee)

    # ──── Deadline paar kar chuki requests pehle nipta do ────
    _run_auto_approve(db, company_id)

    query = db.query(LeaveRequest).filter(LeaveRequest.employee_id == employee_id)
    if status:
        query = query.filter(LeaveRequest.status == status)

    requests = query.order_by(LeaveRequest.created_at.desc()).limit(limit).all()

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first() if company_id else None

    return {
        "employee_id": employee_id,
        "employee_name": employee.full_name,
        "total": len(requests),
        "auto_approve_hours": _auto_approve_hours(policy),
        "history": _leave_rows(
            db, requests, {employee.id: employee}, _auto_approve_hours(policy)
        ),
    }


# ══════════════════════════════════════════════
# Route 8: CEO — Balance Adjust
# ══════════════════════════════════════════════
@router.patch("/balance/adjust")
def adjust_balance(
    data: BalanceAdjustSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    assert_can_view(db, current_user, data.employee_id)
    ceo = get_user_or_404(db, current_user["user_id"])
    leave_type = _validate_leave_type(db, ceo.id, data.leave_type).code
    year = get_pkt_today().year

    balance = get_or_create_balance(db, data.employee_id, ceo.id, leave_type, year)

    new_entitlement = (balance.total_entitlement or 0) + data.adjustment
    if new_entitlement < 0:
        raise HTTPException(status_code=400, detail="Entitlement manfi nahi ho sakti")

    balance.total_entitlement = new_entitlement
    balance.remaining_days = max(0, new_entitlement - (balance.used_days or 0))
    balance.last_updated = get_pkt_now()
    db.commit()

    return {
        "message": "Balance adjusted!",
        "employee_id": data.employee_id,
        "leave_type": leave_type,
        "new_entitlement": balance.total_entitlement,
        "used_days": balance.used_days,
        "remaining_days": balance.remaining_days,
    }


# ══════════════════════════════════════════════
# Route 9: CEO — All Leave Requests
# ══════════════════════════════════════════════
@router.get("/all")
def get_all_leaves(
    status: Optional[str] = None,
    employee_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    ceo = get_user_or_404(db, current_user["user_id"])
    _run_auto_approve(db, ceo.id)

    query = db.query(LeaveRequest).filter(LeaveRequest.company_id == ceo.id)
    if status:
        query = query.filter(LeaveRequest.status == status)
    if employee_id:
        query = query.filter(LeaveRequest.employee_id == employee_id)

    requests = query.order_by(LeaveRequest.created_at.desc()).limit(limit).all()

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == ceo.id
    ).first()

    employees = {e.id: e for e in company_employees(db, ceo)}
    rows = _leave_rows(db, requests, employees, _auto_approve_hours(policy))

    counts = {s: 0 for s in ["evaluating", "pending", "approved", "rejected", "cancelled"]}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    return {
        "total": len(rows),
        "summary": counts,
        "requests": rows,
    }


# ══════════════════════════════════════════════
# Route 10: CEO — Team leave calendar (kaun kab chhutti pe hai)
# ══════════════════════════════════════════════
@router.get("/calendar")
def get_leave_calendar(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """Approved leaves ki list — CEO dekh sake kis din kitne log ghair-hazir hain"""
    ceo = get_user_or_404(db, current_user["user_id"])
    today = get_pkt_today()

    try:
        start = date.fromisoformat(from_date) if from_date else today
        end = date.fromisoformat(to_date) if to_date else today + timedelta(days=30)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format YYYY-MM-DD hona chahiye")

    approved = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == ceo.id,
        LeaveRequest.status == LeaveStatusEnum.approved,
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    ).order_by(LeaveRequest.start_date).all()

    employees = {e.id: e for e in company_employees(db, ceo)}

    return {
        "from": str(start),
        "to": str(end),
        "total": len(approved),
        "leaves": _leave_rows(db, approved, employees),
    }


# ══════════════════════════════════════════════
# Route 13: Leave Types — list
# ══════════════════════════════════════════════
@router.get("/types")
def list_leave_types(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Company ki leave types. Employee ko sirf chalu types dikhti hain,
    CEO ko sab (band ki hui bhi, taake wapas on kar sake).
    """
    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user) or user.id
    is_ceo = current_user["role"] in ("ceo", "superadmin")

    types = get_leave_types(db, company_id)
    if not is_ceo:
        types = [t for t in types if t.is_enabled]

    return {
        "total": len(types),
        "types": [
            {
                "id": t.id,
                "code": t.code,
                "label": t.label,
                "default_entitlement": t.default_entitlement,
                "is_unlimited": t.is_unlimited,
                "requires_certificate": t.requires_certificate,
                "advance_notice_days": t.advance_notice_days or 0,
                "is_enabled": t.is_enabled,
                "source": t.source,
                "policy_reference": t.policy_reference,
                "sort_order": t.sort_order,
            }
            for t in types
        ]
    }


# ══════════════════════════════════════════════
# Route 14: Leave Type — banao ya badlo (CEO)
# ══════════════════════════════════════════════
@router.put("/types")
def upsert_leave_type(
    data: LeaveTypeSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Type maujood ho to update, warna nayi bana do.

    Entitlement 0 kar dena = wo type dikhti to hai magar us par apply nahi
    ho sakti (zero-balance rule khud rok deta hai). Policy document mein
    koi type na ho to yahi karte hain.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    code = (data.code or "").strip().lower().replace(" ", "_")

    if not code:
        raise HTTPException(status_code=400, detail="Type ka code chahiye")
    if not code.replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="Code mein sirf harf, adad aur underscore chalte hain"
        )
    if data.default_entitlement < 0:
        raise HTTPException(status_code=400, detail="Entitlement manfi nahi ho sakti")

    get_leave_types(db, ceo.id)          # pehli dafa ho to defaults seed ho jayein

    existing = db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == ceo.id,
        CompanyLeaveType.code == code
    ).first()

    created = existing is None
    if created:
        max_order = db.query(CompanyLeaveType).filter(
            CompanyLeaveType.company_id == ceo.id
        ).count()
        existing = CompanyLeaveType(
            company_id=ceo.id, code=code, sort_order=max_order + 1,
            source="manual",
        )
        db.add(existing)

    existing.label = data.label or code.replace("_", " ").title()
    existing.default_entitlement = 0 if data.is_unlimited else data.default_entitlement
    existing.is_unlimited = data.is_unlimited
    existing.requires_certificate = data.requires_certificate
    existing.advance_notice_days = max(0, data.advance_notice_days)
    existing.is_enabled = data.is_enabled
    if data.policy_reference is not None:
        existing.policy_reference = data.policy_reference
    existing.updated_at = get_pkt_now()

    # ──── Employees ki maujooda balance rows bhi sync karo ────
    db.flush()
    synced = sync_balances_to_config(db, ceo.id, existing)

    db.commit()
    db.refresh(existing)

    notes = []
    if not existing.is_unlimited and existing.default_entitlement == 0:
        notes.append("Entitlement 0 hai — employee is type par apply nahi kar sakega")
    if not existing.is_enabled:
        notes.append("Band hai — employee ko yeh type dikhti hi nahi")
    if synced:
        notes.append(f"{synced} employee(s) ka balance update ho gaya")

    return {
        "message": f"{existing.label} {'bana di gayi' if created else 'update ho gayi'}",
        "created": created,
        "code": existing.code,
        "balances_synced": synced,
        "note": " · ".join(notes) if notes else None,
    }


# ══════════════════════════════════════════════
# Route 16: Policy se leave types nikalo (CEO)
# ══════════════════════════════════════════════
class ApplyTypesSchema(BaseModel):
    """CEO ne review kar ke jo types confirm kin"""
    types: List[LeaveTypeSchema]
    disable_missing: bool = False
    # ↑ True = jo types policy mein nahi mili unki entitlement 0 kar do


@router.post("/types/extract")
def extract_types_from_policy(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Policy document parh kar leave types TAJWEEZ karo.

    Yeh kuch save NAHI karta — sirf mashwara deta hai. CEO review kar ke
    `/types/apply` par confirm karta hai. LLM ki ghalti seedha employees
    ke balance mein nahi jaati.

    Har tajweez ke saath `source_quote` aata hai — document ki wo asal line
    jis se value nikli, taake CEO khud tasdeeq kar sake.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == ceo.id,
        CompanyPolicy.is_active == True
    ).first()

    if not active_policy:
        raise HTTPException(
            status_code=400,
            detail="Koi active policy document nahi — pehle Settings se upload karein"
        )

    try:
        from app.agents.policy_extraction_agent import extract_leave_types
        result = extract_leave_types(ceo.id)
    except Exception as e:
        print(f"Policy extraction failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="Agent abhi dastyab nahi — types manually banayein"
        )

    existing = leave_type_map(db, ceo.id)
    suggested_codes = {t["code"] for t in result["types"]}

    # ──── Har tajweez ko maujooda haalat ke saath milao ────
    for t in result["types"]:
        current = existing.get(t["code"])
        t["is_new"] = current is None
        t["current_entitlement"] = current.default_entitlement if current else None
        t["changes_entitlement"] = bool(
            current and current.default_entitlement != t["days_per_year"]
        )

    # ──── Jo maujooda types policy mein NAHI mili ────
    missing = [
        {
            "code": c.code,
            "label": c.label,
            "current_entitlement": c.default_entitlement,
            "is_unlimited": c.is_unlimited,
        }
        for c in existing.values()
        if c.code not in suggested_codes and c.is_enabled
        and not (c.is_unlimited or c.default_entitlement == 0)
    ]

    return {
        "policy_document": active_policy.file_name,
        "suggested": result["types"],
        "missing_from_policy": missing,
        "warnings": result["warnings"],
        "chunks_used": result["chunks_used"],
        "note": "Yeh sirf tajweez hai — kuch save nahi hua. Review kar ke apply karein.",
    }


def apply_policy_types(db: Session, company_id: int, extracted: List[dict],
                       disable_missing: bool = True) -> dict:
    """
    Agent ki nikali hui types ko company ki config par chaspa karo.

    Policy document hi asal sach hai — jo type document mein NAHI mili wo
    **band** kar di jati hai (employee ko dikhti hi nahi). Delete nahi karte:
    purani requests aur history saabit rehni chahiye, aur CEO ek click mein
    wapas on kar sakta hai.

    Yeh function `settings.py` ke upload background task se bhi chalta hai
    aur CEO ke manual apply se bhi — dono ka behaviour ek hi rahe.
    """
    existing = {t.code: t for t in get_leave_types(db, company_id)}
    applied, created, disabled = [], [], []
    synced = 0

    for item in extracted:
        code = (item.get("code") or "").strip().lower().replace(" ", "_")
        if not code or not code.replace("_", "").isalnum():
            continue

        cfg = existing.get(code)
        if not cfg:
            cfg = CompanyLeaveType(
                company_id=company_id, code=code,
                sort_order=len(existing) + len(created) + 1,
            )
            db.add(cfg)
            existing[code] = cfg
            created.append(code)

        cfg.label = item.get("label") or code.replace("_", " ").title()
        cfg.is_unlimited = bool(item.get("is_unlimited"))
        cfg.default_entitlement = (
            0 if cfg.is_unlimited else max(0, int(item.get("days_per_year") or 0))
        )
        cfg.requires_certificate = bool(item.get("requires_certificate"))
        cfg.advance_notice_days = max(0, int(item.get("advance_notice_days") or 0))
        cfg.is_enabled = True
        cfg.source = "policy"
        cfg.policy_reference = item.get("source_quote") or None
        cfg.updated_at = get_pkt_now()
        applied.append(code)

    db.flush()

    # ──── Jo policy mein nahi mili — band kar do ────
    if disable_missing:
        for code, cfg in existing.items():
            if code in applied or not cfg.is_enabled:
                continue
            cfg.is_enabled = False
            cfg.source = "policy"
            cfg.policy_reference = "Policy document mein is type ka zikr nahi mila"
            cfg.updated_at = get_pkt_now()
            disabled.append(code)

    # ──── Employees ke balance bhi config se milao ────
    for cfg in existing.values():
        synced += sync_balances_to_config(db, company_id, cfg)

    db.commit()

    return {
        "applied": applied,
        "created": created,
        "disabled": disabled,
        "balances_synced": synced,
    }


@router.post("/types/apply")
def apply_extracted_types(
    data: ApplyTypesSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    CEO ne jo types confirm kin unhein save karo.

    `disable_missing` = policy mein jo types nahi mili unki entitlement 0
    kar do (card dikhta rahega magar apply nahi ho sakegi).
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    # ──── Wahi raasta jo upload ke waqt chalta hai — behaviour ek hi rahe ────
    result = apply_policy_types(
        db, ceo.id,
        [
            {
                "code": t.code,
                "label": t.label,
                "days_per_year": t.default_entitlement,
                "is_unlimited": t.is_unlimited,
                "requires_certificate": t.requires_certificate,
                "advance_notice_days": t.advance_notice_days,
                "source_quote": t.policy_reference,
            }
            for t in data.types
        ],
        disable_missing=data.disable_missing,
    )

    notes = []
    if result["disabled"]:
        notes.append(
            f"{', '.join(result['disabled'])} band kar di gayin — "
            f"policy mein zikr nahi mila"
        )
    if result["balances_synced"]:
        notes.append(f"{result['balances_synced']} employee balance update hue")

    return {
        "message": f"{len(result['applied'])} type(s) save ho gayin",
        **result,
        "note": " · ".join(notes) if notes else None,
    }


# ══════════════════════════════════════════════
# Route 15: Leave Type delete (CEO)
# ══════════════════════════════════════════════
@router.delete("/types/{code}")
def delete_leave_type(
    code: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Type hatao. Agar us par purani requests maujood hain to delete nahi
    karte — sirf band kar dete hain, warna history ka matlab hi khatam.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    code = (code or "").strip().lower()

    cfg = db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == ceo.id,
        CompanyLeaveType.code == code
    ).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Leave type nahi mili")

    used = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == ceo.id,
        LeaveRequest.leave_type == code
    ).count()

    if used:
        cfg.is_enabled = False
        cfg.updated_at = get_pkt_now()
        db.commit()
        return {
            "message": f"{cfg.label} band kar di gayi",
            "disabled": True,
            "deleted": False,
            "note": f"{used} purani requests is type par hain — "
                    f"history bachane ke liye delete nahi ki, sirf band ki hai",
        }

    label = cfg.label
    db.query(LeaveBalance).filter(
        LeaveBalance.company_id == ceo.id,
        LeaveBalance.leave_type == code
    ).delete(synchronize_session=False)
    db.delete(cfg)
    db.commit()

    return {
        "message": f"{label} delete ho gayi",
        "disabled": False,
        "deleted": True,
    }


# ══════════════════════════════════════════════
# Route 12: Overdue requests khud approve karo
# ══════════════════════════════════════════════
@router.post("/process-overdue")
def process_overdue(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Deadline paar kar chuki pending requests nipta do.

    Waise yeh khud chalta hai jab bhi koi leave listing kholta hai.
    Yeh endpoint isliye hai ke cron/scheduler se bhi chala sakein —
    ta ke koi app na khole tab bhi employee latka na rahe.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    approved = _auto_approve_overdue(db, ceo.id)

    return {
        "message": f"{approved} request(s) auto-approve ho gayin",
        "auto_approved": approved,
    }


# ══════════════════════════════════════════════
# Route 11: Medical Certificate dekho
# ══════════════════════════════════════════════
@router.get("/certificate/{leave_id}")
def get_leave_certificate(
    leave_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Certificate DB se serve hota hai. Purani requests ki file (jo
    uploads/certificates/ mein padi hai) bhi abhi chal jayegi.
    """
    leave_req = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if not leave_req:
        raise HTTPException(status_code=404, detail="Leave request nahi mili")

    # ──── Apna certificate, ya CEO ho to apni company ka ────
    assert_can_view(db, current_user, leave_req.employee_id)

    # ──── 1. DB (asal jagah) ────
    doc = db.query(LeaveDocument).filter(
        LeaveDocument.leave_request_id == leave_id
    ).first()

    if doc:
        return Response(
            content=doc.file_data,
            media_type=doc.mime_type or "application/pdf",
            headers={
                "Cache-Control": "private, max-age=86400",
                "Content-Disposition": f'inline; filename="{doc.file_name or "certificate"}"',
            },
        )

    # ──── 2. Legacy file fallback ────
    path = leave_req.medical_certificate
    if path and os.path.exists(path):
        return FileResponse(path, filename=os.path.basename(path))

    raise HTTPException(status_code=404, detail="Certificate available nahi hai")
