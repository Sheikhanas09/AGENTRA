import os
from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.utils.tenancy import Tenant, get_tenant, require_ceo
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
# This is only the STARTING set. It is inserted once per company into
# `company_leave_types`, after which the CEO (or information extracted
# from the policy document) can change it — set the entitlement to 0,
# disable it, or add an entirely new type (maternity, hajj...).
DEFAULT_LEAVE_TYPES = [
    # code,        label,             days, unlimited, cert, notice, order, paid
    ("annual",     "Annual Leave",      15, False, False, 1, 1, True),
    ("casual",     "Casual Leave",      10, False, False, 1, 2, True),
    ("sick",       "Sick Leave",        10, False, True,  0, 3, True),
    ("emergency",  "Emergency Leave",    3, False, False, 0, 4, True),
    # Important for payroll — salary is deducted for this one
    ("unpaid",     "Unpaid Leave",       0, True,  False, 1, 5, False),
]


# ══════════════════════════════════════════════
# "Is this leave unpaid?" — inferred from the name
# ══════════════════════════════════════════════
# Payroll's `unpaid_leave_deduction` runs solely off
# `CompanyLeaveType.is_paid`. Types coming from a policy document can be
# named anything ("Leave Without Pay", "LWP") — and the agent does not
# always say whether they are paid. In that case the name is all we have.
#
# Only words that plainly mean "without pay". Types like "sabbatical",
# "study" or "hajj" are paid at some companies and not at others —
# guessing and docking someone's salary would be wrong. When in doubt,
# always PAID (in the employee's favour).
#
# ⚠ The same list also lives in the migration backfill
# (`migrate_attendance.py` → `backfill_unpaid_leave_types`) — that one is SQL,
# it is SQL there, so it is written separately; change both together.
UNPAID_NAME_HINTS = ("unpaid", "without pay", "without_pay",
                     "no pay", "no_pay", "lwp")


def looks_unpaid(*words) -> bool:
    """Does the type's code/label plainly say "without pay"?"""
    text = " ".join(str(w or "").lower() for w in words)
    return any(hint in text for hint in UNPAID_NAME_HINTS)


MIN_ADVANCE_DAYS = 1

# A request longer than this looks like a mistake
MAX_LEAVE_SPAN_DAYS = 365

# Sick/emergency leave may be backdated this far
MAX_BACKDATE_DAYS = 30

# How long before an unanswered request approves itself — from the policy
DEFAULT_AUTO_APPROVE_HOURS = 24

# ──── Reason ────
# Every leave needs a reason. The CEO has to decide on it and the Leave
# Agent reads it alongside the policy — an empty reason leaves both of
# them in the dark.
MIN_REASON_LENGTH = 5
MAX_REASON_LENGTH = 1000

# Requests in these statuses make their dates "occupied"
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
    """The CEO creates a new type or edits an existing one"""
    code: str
    label: Optional[str] = None
    default_entitlement: int = 0
    is_unlimited: bool = False
    requires_certificate: bool = False
    advance_notice_days: int = 1
    is_enabled: bool = True

    # ──── Is this leave paid? ────
    # The whole of payroll's `unpaid_leave_deduction` rests on this one
    # boolean. This field did not exist here before — the column was there
    # but nothing could write to it, so every NEW type silently became
    # `paid` (even when the policy said "Leave Without Pay").
    #
    # Default TRUE — when in doubt, in the employee's favour. A wrong
    # FALSE means someone's salary is docked for no reason.
    is_paid: bool = True

    policy_reference: Optional[str] = None


# ══════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════
def _status_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _is_unlimited(db: Session, company_id: int, leave_type: str) -> bool:
    """Is this type's balance unlimited (e.g. unpaid)?"""
    cfg = leave_type_map(db, company_id).get(_status_value(leave_type))
    return bool(cfg and cfg.is_unlimited)


def get_leave_types(db: Session, company_id: int) -> List[CompanyLeaveType]:
    """
    The company's leave types — seeded from the defaults on first use.

    This is the only place types come from; there is no hardcoded list
    left anywhere in the code.
    """
    types = db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == company_id
    ).order_by(CompanyLeaveType.sort_order, CompanyLeaveType.code).all()

    if types:
        return types

    # ──── First time — seed the defaults ────
    for code, label, days, unlimited, cert, notice, order, paid in DEFAULT_LEAVE_TYPES:
        db.add(CompanyLeaveType(
            company_id=company_id, code=code, label=label,
            default_entitlement=days, is_unlimited=unlimited,
            requires_certificate=cert, advance_notice_days=notice,
            is_enabled=True, source="default", sort_order=order,
            is_paid=paid,
        ))
    db.commit()

    return db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == company_id
    ).order_by(CompanyLeaveType.sort_order, CompanyLeaveType.code).all()


def leave_type_map(db: Session, company_id: int) -> dict:
    """{code: CompanyLeaveType}"""
    return {t.code: t for t in get_leave_types(db, company_id)}


def _validate_leave_type(db: Session, company_id: int, leave_type: str) -> CompanyLeaveType:
    """Is the type in this company's list, and enabled?"""
    lt = (leave_type or "").strip().lower()
    types = leave_type_map(db, company_id)

    config = types.get(lt)
    if not config:
        allowed = ", ".join(sorted(t.code for t in types.values() if t.is_enabled))
        raise HTTPException(
            status_code=400,
            detail=f"'{lt}' does not exist for this company. Available types: {allowed}"
        )

    if not config.is_enabled:
        raise HTTPException(
            status_code=400,
            detail=f"{config.label} is currently disabled — please contact HR"
        )

    return config


def _validate_reason(reason: str) -> str:
    """A reason is mandatory — both the CEO and the agent decide on it"""
    text = (reason or "").strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Please give a reason for your leave"
        )
    if len(text) < MIN_REASON_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Please give a little more detail (at least "
                   f"{MIN_REASON_LENGTH} harf)"
        )
    return text[:MAX_REASON_LENGTH]


def _suggest_type(types: List[CompanyLeaveType], **match) -> Optional[CompanyLeaveType]:
    """
    So the employee can be given a concrete suggestion: find a type in
    the company's list that satisfies the given condition.

    Example: if the advance-notice rule blocks them, suggest "use Sick
    Leave" — but only if that company really has a no-notice type.
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
        raise HTTPException(status_code=400, detail="Date format must be YYYY-MM-DD")

    if end < start:
        raise HTTPException(status_code=400, detail="End date cannot be before the start date")

    span = (end - start).days + 1
    if span > MAX_LEAVE_SPAN_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Leave cannot be longer than {MAX_LEAVE_SPAN_DAYS} days"
        )

    today = get_pkt_today()

    notice = config.advance_notice_days or 0

    if notice > 0:
        # ──── Planned leave — must be applied for this many days ahead ────
        earliest = today + timedelta(days=notice)
        if start < earliest:
            # ──── If a no-notice type exists, name it ────
            alt = _suggest_type(all_types or [], advance_notice_days=0)
            tip = (
                f"If you need leave for today, please use {alt.label}."
                if alt else
                "There is no leave type available for today — please contact HR."
            )
            raise HTTPException(
                status_code=400,
                detail=f"{config.label} must be applied for at least {notice} "
                       f"day(s) in advance — the earliest possible date is "
                       f"{earliest}. {tip}"
            )
    else:
        # ──── Same day, or a few days back ────
        if start < today - timedelta(days=MAX_BACKDATE_DAYS):
            raise HTTPException(
                status_code=400,
                detail=f"Leave older than {MAX_BACKDATE_DAYS} days cannot be applied for"
            )

    return start, end, span


def _auto_approve_hours(policy) -> int:
    """Hours the CEO has to respond — 0 means never auto-approve"""
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
    Fetch the balance row, or create one using the company's configured
    entitlement.

    The entitlement is no longer hardcoded — it comes from
    `company_leave_types`. A type missing from the policy has an
    entitlement of 0, so the balance is 0/0 and cannot be applied for.
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
    When a type's entitlement changes, update employees' EXISTING balance rows too.

    This matters: `get_or_create_balance` only reads the config when the
    row is created for the FIRST time. If the CEO later sets the
    entitlement to 0, the old number stays in existing rows and the
    employee keeps seeing the card as though nothing changed.

    `used_days` is never touched — it is settled history.
    Remaining is recomputed: max(0, entitlement - used).
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
    """
    The CEO of this company, for notifications.

    ═══ THE DOCSTRING USED TO SAY IT OUT LOUD ═══
        "The CEO record for notifications — company_id IS their user id"
        return db.query(User).filter(User.id == company_id).first()

    It was true, and then it was not. A company registered after the
    multi-tenant change has an id from 1000 up while its CEO has an
    ordinary user id, so this returned None — and every leave
    notification to that CEO stopped, silently.
    """
    return db.query(User).filter(
        User.company_id == company_id, User.role == "ceo").first()


def _leave_type_label(db: Session, company_id: int, code: str) -> str:
    """Write 'Annual Leave' in emails instead of 'annual'"""
    cfg = leave_type_map(db, company_id).get(_status_value(code))
    return cfg.label if cfg else _status_value(code).replace("_", " ").title()


def _find_overlap(db: Session, employee_id: int, start: date, end: date,
                  exclude_id: Optional[int] = None) -> Optional[LeaveRequest]:
    """
    Is there already a live request on these dates?
    Overlap rule: (existing.start <= new.end) AND (existing.end >= new.start)
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
    The CEO did not respond in time → approve automatically if the balance allows.

    Every leave request goes to the CEO first (there may be an important
    meeting that day which only they know about). But an employee should
    not be left hanging forever — hence the deadline.

    It does NOT happen if:
      - the CEO put a manual override on that leave type (they explicitly
        said "I will look at these myself")
      - the balance is short
      - the policy has hours = 0 (the CEO switched auto-approve off)

    This runs from READ endpoints (there is no cron). If it fails the
    listing must still work — so the caller wraps it in try/except.
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

    # ──── Types the CEO asked to review personally ────
    overridden = {
        _status_value(o.leave_type)
        for o in db.query(CompanyPolicyOverride).filter(
            CompanyPolicyOverride.company_id == company_id,
            CompanyPolicyOverride.force_manual == True
        ).all()
    }

    ceo = _company_ceo(db, company_id)
    types = leave_type_map(db, company_id)
    notify_queue = []          # sent AFTER the commit

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
                # ──── Balance is short — let the CEO decide ────
                continue
            _deduct(balance, days)

        req.status = LeaveStatusEnum.approved
        req.auto_approved = True
        req.decided_at = now
        req.payroll_notified = True
        req.ceo_note = (
            f"Auto-approved — no response within {hours} hours"
        )
        approved += 1

        # ──── Collect the emails (do not send yet — the commit is pending) ────
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

        # ──── Send now — after the DB is safe ────
        # Otherwise a failed commit with the email already gone would tell
        # the employee about an approval that never happened
        for item in notify_queue:
            emp = item["employee"]
            if emp and emp.email:
                notify.leave_decision_to_employee(
                    company_id=company_id,
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
                    company_id=company_id,
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
    """Settle overdue requests before showing a listing — quietly"""
    if not company_id:
        return
    try:
        _auto_approve_overdue(db, company_id)
    except Exception as e:
        # ──── The listing must work even if the sweep fails ────
        db.rollback()
        print(f"[leave] auto-approve sweep failed: {e}")


def _auto_approve_at(req: LeaveRequest, hours: int) -> Optional[str]:
    """When this request will approve itself (if it is still pending)"""
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
    One shape everywhere — the frontend should never have to guess.

    has_doc: the answer from the documents table. When None we fall back
    to the old file-path column (legacy rows).
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

        # ──── The agent's decision (audit trail) ────
        "agent_decision": log.decision if log else None,
        "agent_reason": log.reason if log else None,
        "policy_reference": log.policy_reference if log else None,
    }


def _logs_for(db: Session, leave_ids: List[int]) -> dict:
    """All decision logs in one query (to avoid N+1)"""
    if not leave_ids:
        return {}
    logs = db.query(PolicyDecisionLog).filter(
        PolicyDecisionLog.leave_request_id.in_(leave_ids)
    ).all()
    return {l.leave_request_id: l for l in logs}


def _docs_for(db: Session, leave_ids: List[int]) -> dict:
    """
    Which requests have a document attached — in one query.
    `file_data` is deliberately NOT loaded, or the listing would drag megabytes.
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
    """Turn a list of requests into rows — logs and docs in one query each"""
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
    # ↑ Deliberately optional at the FastAPI level —
    #   `Form(...)` returns a raw 422 "Field required" on an empty value.
    #   `_validate_reason()` below returns a 400 with a clear message.
    medical_certificate: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    """
    Employee leave request kare → Leave Agent (RAG + LLM) evaluate karega.

    A few deterministic guards run BEFORE the agent — the LLM only gets
    the decisions that genuinely need judgement.
    """
    # ──── Only for yourself (the CEO may file on someone else's behalf) ────
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
            detail=f"A request already exists on these dates "
                   f"({clash.start_date} to {clash.end_date}, status: {_status_value(clash.status)})"
        )

    # ──── Count working days — only these come off the balance ────
    work_policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()
    deductible_days = count_working_days(work_policy, start, end)

    if deductible_days == 0:
        raise HTTPException(
            status_code=400,
            detail="There is no working day in these dates — no leave is needed"
        )

    # ──── Medical certificate — prepare it for the DB ────
    # Only validated and compressed here. It is written to the DB once the
    # leave request has an id (below).
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

    # ──── Balance (for the leave's YEAR, not today's) ────
    balance = get_or_create_balance(db, employee_id, company_id, leave_type, start.year)

    # ═══ Balance completely used up → the request is never created ═══
    # This used to go to the CEO. But when the CEO themselves set this
    # type's balance to 0, there is no point asking them again — stop the
    # employee right here.
    if not type_config.is_unlimited and balance.remaining_days <= 0:
        alt = _suggest_type(all_types, is_unlimited=True)
        tip = (f"Please use {alt.label}, or ask HR to review your balance"
               if alt else "Please ask HR to review your balance")
        raise HTTPException(
            status_code=400,
            detail=f"Your {type_config.label} balance is finished "
                   f"(0/{balance.total_entitlement} days). You cannot apply "
                   f"for this type — {tip}."
        )

    # ═══ Sick leave → a medical certificate is required ═══
    if type_config.requires_certificate and not prepared_cert:
        alt = _suggest_type(all_types, requires_certificate=False,
                            advance_notice_days=type_config.advance_notice_days)
        tip = (f" If you do not have one, please use {alt.label}." if alt else "")
        raise HTTPException(
            status_code=400,
            detail=f"A medical certificate must be attached with "
                   f"{type_config.label} (PDF or image).{tip}"
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
    db.flush()          # leave_req.id is needed for the document

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
        Every request goes to the CEO — nothing is auto-approved on the spot.

        Why: there may be an important meeting on the requested day, and
        only the CEO knows about it. Even when the policy is satisfied,
        the CEO deserves a chance to look. If the balance allows, it
        approves itself once the deadline passes.
        """
        leave_req.status = LeaveStatusEnum.pending
        if agent_result:
            _save_decision_log(db, leave_req, company_id, agent_result)
        db.commit()
        db.refresh(leave_req)

        auto_at = _auto_approve_at(leave_req, auto_hours)

        # ──── Notify the CEO — in the background, the request never waits ────
        ceo = _company_ceo(db, company_id)
        if ceo and ceo.email:
            notify.leave_submitted_to_ceo(
                company_id=company_id,
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
            "message": "Leave request sent — waiting for HR approval",
            "leave_id": leave_req.id,
            "status": "pending",
            "reason": reason_text,
            # ──── What the agent recommended (not a decision) ────
            "agent_recommendation": recommendation,
            "policy_reference": (agent_result or {}).get("policy_reference", ""),
            # ──── When it will approve itself if the CEO stays silent ────
            "auto_approve_at": auto_at,
            "auto_approve_hours": auto_hours if auto_at else None,
            "total_days": total_days,
            "deductible_days": deductible_days,
        }

    # ═══ Guard 1: the CEO set a manual override on this leave type ═══
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

    # ═══ Guard 2: the balance does not fully cover it ═══
    # A balance of exactly zero was already blocked above. This is the case
    # where some days are left but not enough (say 2 left, 4 needed) — the
    # CEO decides whether to treat the rest as unpaid. No need to call the
    # agent; this is arithmetic.
    if not type_config.is_unlimited and balance.remaining_days < deductible_days:
        return to_ceo(
            f"Balance does not cover it — {deductible_days} working days "
            f"needed, only {balance.remaining_days} left",
            recommendation="insufficient_balance",
        )

    # ═══ Guard 3: no policy document has been uploaded ═══
    # Without a policy the agent cannot recommend anything
    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).first()
    if not active_policy:
        return to_ceo(
            "No company policy document has been uploaded — the agent could not advise"
        )

    # ═══ Leave Agent (RAG + LLM) ═══
    try:
        # The import is inside the try too — a missing GROQ_API_KEY or a
        # broken ML package must not turn the whole request into a 500
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
        # ──── Groq down, or anything else breaking — the request must not get stuck ────
        print(f"Leave agent failed: {e}")
        return to_ceo("The agent is unavailable — HR will review this manually")

    # ═══ The agent's verdict is a RECOMMENDATION, not an order ═══
    # Even when the policy is satisfied, the request still goes to the CEO
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
    """The full RAG + LLM audit trail — so later questions have an answer"""
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

    # ──── Settle any requests that have passed their deadline first ────
    _run_auto_approve(db, ceo.company_id)

    pending = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == ceo.company_id,
        LeaveRequest.status == LeaveStatusEnum.pending
    ).order_by(LeaveRequest.created_at.desc()).all()

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == ceo.company_id
    ).first()
    auto_hours = _auto_approve_hours(policy)

    employees = {e.id: e for e in company_employees(db, ceo)}
    result = _leave_rows(db, pending, employees, auto_hours)

    # ──── The CEO should see the balance while deciding ────
    for row, req in zip(result, pending):
        balance = db.query(LeaveBalance).filter(
            LeaveBalance.employee_id == req.employee_id,
            LeaveBalance.leave_type == req.leave_type,
            LeaveBalance.year == req.start_date.year
        ).first()
        row["remaining_balance"] = balance.remaining_days if balance else None
        row["total_entitlement"] = balance.total_entitlement if balance else None

        # ──── The employee may not be in the CEO's list (e.g. the CEO) ────
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
        LeaveRequest.company_id == ceo.company_id,
        LeaveRequest.status == LeaveStatusEnum.pending
    ).first()

    if not leave_req:
        raise HTTPException(status_code=404, detail="Pending request not found")

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

    # ──── The CEO's decision stands even on a short balance, but silently
    #      skipping the deduction would be wrong — report the overrun ────
    over_limit = False
    if not _is_unlimited(db, leave_req.company_id, leave_req.leave_type):
        over_limit = balance.remaining_days < days
        _deduct(balance, days)

    db.commit()

    # ──── Employee ko batao ────
    employee = db.query(User).filter(User.id == leave_req.employee_id).first()
    if employee and employee.email:
        notify.leave_decision_to_employee(
            company_id=company_id,
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
        "note": "Approved beyond the balance — treat the excess as unpaid" if over_limit else None,
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
        LeaveRequest.company_id == ceo.company_id,
        LeaveRequest.status == LeaveStatusEnum.pending
    ).first()

    if not leave_req:
        raise HTTPException(status_code=404, detail="Pending request not found")

    # ──── A rejection reason is mandatory — the employee must know why ────
    note = (data.ceo_note or "").strip()
    if not note:
        raise HTTPException(
            status_code=400,
            detail="A reason is required to reject — this is what the employee will see"
        )

    leave_req.status = LeaveStatusEnum.rejected
    leave_req.decided_by = current_user["user_id"]
    leave_req.decided_at = get_pkt_now()
    leave_req.ceo_note = note

    db.commit()

    # ──── Tell the employee, with the reason ────
    employee = db.query(User).filter(User.id == leave_req.employee_id).first()
    if employee and employee.email:
        notify.leave_decision_to_employee(
            company_id=company_id,
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
    current_user: Tenant = Depends(get_tenant)
):
    """
    A pending request can be cancelled at any time.

    An employee can only cancel approved leave that has not STARTED yet —
    cancelling leave for the 12th on the 12th makes no sense, that day's
    attendance has already been counted differently.

    The CEO can cancel leave that has already started (say the person
    turned up after all) — either way the balance is returned.
    """
    leave_req = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if not leave_req:
        raise HTTPException(status_code=404, detail="Leave request not found")

    assert_can_view(db, current_user, leave_req.employee_id)

    status = _status_value(leave_req.status)
    if status in ("rejected", "cancelled"):
        raise HTTPException(status_code=400, detail=f"This request is already {status}")

    today = get_pkt_today()
    is_ceo = current_user["role"] in ("ceo", "superadmin")

    if status == "approved" and leave_req.start_date <= today and not is_ceo:
        raise HTTPException(
            status_code=400,
            detail=f"This leave started on {leave_req.start_date} — it can no "
                   f"longer be cancelled by the employee. Please contact HR."
        )

    # ──── If it was approved, return the balance ────
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

    # ──── If the employee cancelled, tell the CEO (not if the CEO did) ────
    ceo = _company_ceo(db, leave_req.company_id)
    employee = db.query(User).filter(User.id == leave_req.employee_id).first()
    if ceo and ceo.email and employee:
        notify.leave_cancelled_to_ceo(
            company_id=company_id,
            ceo_email=ceo.email,
            ceo_name=ceo.full_name or "CEO",
            employee_name=employee.full_name or "Employee",
            leave_type=_leave_type_label(db, leave_req.company_id, leave_req.leave_type),
            start=leave_req.start_date, end=leave_req.end_date,
            by_ceo=is_ceo,
            company=ceo.company_name or "",
        )

    return {
        "message": "Leave cancelled",
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
    current_user: Tenant = Depends(get_tenant)
):
    employee = assert_can_view(db, current_user, employee_id)
    year = year or get_pkt_today().year
    company_id = resolve_company_id(db, employee) or employee_id

    # ──── Types come from the company config — no hardcoded list ────
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
            # ──── This type's own rules — the UI is driven by these ────
            "requires_certificate": cfg.requires_certificate,
            "advance_notice_days": cfg.advance_notice_days or 0,
            # The employee must know BEFORE applying that this leave costs
            # salary — finding out later from the payslip feels like a trick
            "is_paid": cfg.is_paid,
            "source": cfg.source,
            "policy_reference": cfg.policy_reference,
        })
    db.commit()

    # ──── Requests still under review (balance not deducted yet) ────
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
    current_user: Tenant = Depends(get_tenant)
):
    employee = assert_can_view(db, current_user, employee_id)
    company_id = resolve_company_id(db, employee)

    # ──── Settle any past-deadline requests first ────
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
    leave_type = _validate_leave_type(db, ceo.company_id, data.leave_type).code
    year = get_pkt_today().year

    balance = get_or_create_balance(db, data.employee_id, ceo.company_id, leave_type, year)

    new_entitlement = (balance.total_entitlement or 0) + data.adjustment
    if new_entitlement < 0:
        raise HTTPException(status_code=400, detail="Entitlement cannot be negative")

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
    _run_auto_approve(db, ceo.company_id)

    query = db.query(LeaveRequest).filter(LeaveRequest.company_id == ceo.company_id)
    if status:
        query = query.filter(LeaveRequest.status == status)
    if employee_id:
        query = query.filter(LeaveRequest.employee_id == employee_id)

    requests = query.order_by(LeaveRequest.created_at.desc()).limit(limit).all()

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == ceo.company_id
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
# Route 10: CEO — Team leave calendar (who is off, and when)
# ══════════════════════════════════════════════
@router.get("/calendar")
def get_leave_calendar(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """List of approved leaves — so the CEO can see who is away on which day"""
    ceo = get_user_or_404(db, current_user["user_id"])
    today = get_pkt_today()

    try:
        start = date.fromisoformat(from_date) if from_date else today
        end = date.fromisoformat(to_date) if to_date else today + timedelta(days=30)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format must be YYYY-MM-DD")

    approved = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == ceo.company_id,
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
    current_user: Tenant = Depends(get_tenant)
):
    """
    The company's leave types. Employees see only enabled ones; the CEO
    sees all of them (including disabled, so they can switch them back on).
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
                "is_paid": t.is_paid,
                "source": t.source,
                "policy_reference": t.policy_reference,
                "sort_order": t.sort_order,
            }
            for t in types
        ]
    }


# ══════════════════════════════════════════════
# Route 14: Leave type — create or update (CEO)
# ══════════════════════════════════════════════
@router.put("/types")
def upsert_leave_type(
    data: LeaveTypeSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Update the type if it exists, otherwise create it.

    Setting the entitlement to 0 means the type is still visible but
    cannot be applied for (the zero-balance rule blocks it). This is what
    happens to a type missing from the policy document.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    code = (data.code or "").strip().lower().replace(" ", "_")

    if not code:
        raise HTTPException(status_code=400, detail="The type needs a code")
    if not code.replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="A code may only contain letters, digits and underscores"
        )
    if data.default_entitlement < 0:
        raise HTTPException(status_code=400, detail="Entitlement cannot be negative")

    get_leave_types(db, ceo.company_id)          # seed the defaults on first use

    existing = db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == ceo.company_id,
        CompanyLeaveType.code == code
    ).first()

    created = existing is None
    if created:
        max_order = db.query(CompanyLeaveType).filter(
            CompanyLeaveType.company_id == ceo.company_id
        ).count()
        existing = CompanyLeaveType(
            company_id=current_user["company_id"], code=code, sort_order=max_order + 1,
            source="manual",
        )
        db.add(existing)

    existing.label = data.label or code.replace("_", " ").title()
    existing.default_entitlement = 0 if data.is_unlimited else data.default_entitlement
    existing.is_unlimited = data.is_unlimited
    existing.requires_certificate = data.requires_certificate
    existing.advance_notice_days = max(0, data.advance_notice_days)
    existing.is_enabled = data.is_enabled
    existing.is_paid = data.is_paid
    if data.policy_reference is not None:
        existing.policy_reference = data.policy_reference
    existing.updated_at = get_pkt_now()

    # ──── Sync employees' existing balance rows too ────
    db.flush()
    synced = sync_balances_to_config(db, ceo.company_id, existing)

    db.commit()
    db.refresh(existing)

    notes = []
    if not existing.is_unlimited and existing.default_entitlement == 0:
        notes.append("Entitlement is 0 — employees will not be able to apply for this type")
    if not existing.is_enabled:
        notes.append("Disabled — employees will not see this type at all")
    if not existing.is_paid:
        # The CEO must clearly see that this toggle affects MONEY
        notes.append(
            "This type is UNPAID — payroll will deduct for each "
            "day of it"
        )
    if synced:
        notes.append(f"{synced} employee balance(s) updated")

    return {
        "message": f"{existing.label} {'created' if created else 'updated'}",
        "created": created,
        "code": existing.code,
        "is_paid": existing.is_paid,
        "balances_synced": synced,
        "note": " · ".join(notes) if notes else None,
    }


# ══════════════════════════════════════════════
# Route 16: Extract leave types from the policy (CEO)
# ══════════════════════════════════════════════
class ApplyTypesSchema(BaseModel):
    """The types the CEO reviewed and confirmed"""
    types: List[LeaveTypeSchema]
    disable_missing: bool = False
    # ↑ True = set the entitlement of types missing from the policy to 0


@router.post("/types/extract")
def extract_types_from_policy(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Read the policy document and SUGGEST leave types.

    This saves NOTHING — it only advises. The CEO reviews and confirms via
    `/types/apply`. An LLM mistake never lands straight in an employee's
    balance.

    Every suggestion carries a `source_quote` — the exact line in the
    document it came from, so the CEO can verify it themselves.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == ceo.company_id,
        CompanyPolicy.is_active == True
    ).first()

    if not active_policy:
        raise HTTPException(
            status_code=400,
            detail="No active policy document — please upload one from Settings first"
        )

    try:
        from app.agents.policy_extraction_agent import extract_leave_types
        result = extract_leave_types(ceo.company_id)
    except Exception as e:
        print(f"Policy extraction failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="The agent is unavailable — please create the types manually"
        )

    existing = leave_type_map(db, ceo.company_id)
    suggested_codes = {t["code"] for t in result["types"]}

    # ──── Match each suggestion against the current state ────
    for t in result["types"]:
        current = existing.get(t["code"])
        t["is_new"] = current is None
        t["current_entitlement"] = current.default_entitlement if current else None
        t["changes_entitlement"] = bool(
            current and current.default_entitlement != t["days_per_year"]
        )

        # ──── The paid/unpaid decision is settled HERE ────
        # The agent may send `None` (the document is silent). The UI does
        # not need to understand three states — it needs the value that
        # will actually be applied, plus whether it came from the document.
        # (The same order `apply_policy_types()` uses internally.)
        t["paid_from_policy"] = t["is_paid"] is not None
        if t["is_paid"] is None:
            t["is_paid"] = current.is_paid if current else True
        t["current_is_paid"] = current.is_paid if current else None
        t["changes_paid"] = bool(current and current.is_paid != t["is_paid"])

    # ──── Existing types NOT found in the policy ────
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
        "note": "These are suggestions only — nothing has been saved. Review, then apply.",
    }


def apply_policy_types(db: Session, company_id: int, extracted: List[dict],
                       disable_missing: bool = True) -> dict:
    """
    Apply the types extracted by the agent to the company config.

    The policy document is the source of truth — a type NOT found in it is
    **disabled** (employees stop seeing it). It is never deleted: old
    requests and history must stay intact, and the CEO can switch it back
    on with one click.

    This function runs both from the upload background task in
    `settings.py` and from the CEO's manual apply — both must behave
    identically.
    """
    existing = {t.code: t for t in get_leave_types(db, company_id)}
    applied, created, disabled, unpaid = [], [], [], []
    synced = 0

    for item in extracted:
        code = (item.get("code") or "").strip().lower().replace(" ", "_")
        if not code or not code.replace("_", "").isalnum():
            continue

        cfg = existing.get(code)
        is_new = cfg is None
        if is_new:
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

        # ══════ Paid ya unpaid ══════
        # For the other fields the rule is "the policy document wins".
        # Here there is one exception, and `None` is the reason:
        #
        #   True/False  → the agent read it plainly from the document → apply
        #   None        → the document is silent (or the agent is)     → ?
        #
        # On `None`, new and existing types are treated differently:
        #   · NEW type → infer from the name (only on plain "unpaid" words),
        #     otherwise paid. Without this, "Leave Without Pay" silently
        #     became paid
        #     and no deduction would ever apply to it — that was the bug.
        #   · EXISTING type → LEAVE IT ALONE. What the CEO set must not be
        #     flipped by the agent's silence, or every policy upload would
        #     wipe out their decision.
        paid = item.get("is_paid")
        if isinstance(paid, bool):
            cfg.is_paid = paid
        elif is_new:
            cfg.is_paid = not looks_unpaid(code, cfg.label)

        if not cfg.is_paid:
            unpaid.append(code)

        cfg.source = "policy"
        cfg.policy_reference = item.get("source_quote") or None
        cfg.updated_at = get_pkt_now()
        applied.append(code)

    db.flush()

    # ──── Anything not found in the policy — disable it ────
    if disable_missing:
        for code, cfg in existing.items():
            if code in applied or not cfg.is_enabled:
                continue
            cfg.is_enabled = False
            cfg.source = "policy"
            cfg.policy_reference = "This type was not mentioned in the policy document"
            cfg.updated_at = get_pkt_now()
            disabled.append(code)

    # ──── Bring employee balances in line with the config too ────
    for cfg in existing.values():
        synced += sync_balances_to_config(db, company_id, cfg)

    db.commit()

    return {
        "applied": applied,
        "created": created,
        "disabled": disabled,
        # The unpaid ones — the CEO should see these clearly after an
        # upload, because these are what payroll will deduct for
        "unpaid": unpaid,
        "balances_synced": synced,
    }


@router.post("/types/apply")
def apply_extracted_types(
    data: ApplyTypesSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Save the types the CEO confirmed.

    `disable_missing` = set the entitlement of types missing from the
    policy to 0 (the card stays visible but cannot be applied for).
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    # ──── The same path the upload takes — behaviour must not diverge ────
    result = apply_policy_types(
        db, ceo.company_id,
        [
            {
                "code": t.code,
                "label": t.label,
                "days_per_year": t.default_entitlement,
                "is_unlimited": t.is_unlimited,
                "requires_certificate": t.requires_certificate,
                "advance_notice_days": t.advance_notice_days,
                "is_paid": t.is_paid,
                "source_quote": t.policy_reference,
            }
            for t in data.types
        ],
        disable_missing=data.disable_missing,
    )

    notes = []
    if result["disabled"]:
        notes.append(
            f"{', '.join(result['disabled'])} disabled — "
            f"not mentioned in the policy"
        )
    if result["unpaid"]:
        notes.append(
            f"{', '.join(result['unpaid'])} is unpaid — payroll will "
            f"deduct for those days"
        )
    if result["balances_synced"]:
        notes.append(f"{result['balances_synced']} employee balance(s) updated")

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
    Remove a type. If old requests exist against it we do not delete it —
    only disable it, otherwise the history becomes meaningless.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    code = (code or "").strip().lower()

    cfg = db.query(CompanyLeaveType).filter(
        CompanyLeaveType.company_id == ceo.company_id,
        CompanyLeaveType.code == code
    ).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Leave type not found")

    used = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == ceo.company_id,
        LeaveRequest.leave_type == code
    ).count()

    if used:
        cfg.is_enabled = False
        cfg.updated_at = get_pkt_now()
        db.commit()
        return {
            "message": f"{cfg.label} has been disabled",
            "disabled": True,
            "deleted": False,
            "note": f"{used} existing request(s) use this type — it was "
                    f"disabled rather than deleted, to preserve history",
        }

    label = cfg.label
    db.query(LeaveBalance).filter(
        LeaveBalance.company_id == ceo.company_id,
        LeaveBalance.leave_type == code
    ).delete(synchronize_session=False)
    db.delete(cfg)
    db.commit()

    return {
        "message": f"{label} deleted",
        "disabled": False,
        "deleted": True,
    }


# ══════════════════════════════════════════════
# Route 12: Auto-approve overdue requests
# ══════════════════════════════════════════════
@router.post("/process-overdue")
def process_overdue(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Settle pending requests that have passed their deadline.

    This normally runs by itself whenever someone opens a leave listing.
    The endpoint exists so a cron/scheduler can drive it too — so nobody
    is left waiting just because no one opened the app.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    approved = _auto_approve_overdue(db, ceo.company_id)

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
    current_user: Tenant = Depends(get_tenant)
):
    """
    Certificates are served from the DB. Files from older requests (still
    sitting in uploads/certificates/) also still work.
    """
    leave_req = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).first()
    if not leave_req:
        raise HTTPException(status_code=404, detail="Leave request not found")

    # ──── Your own certificate, or the CEO's own company's ────
    assert_can_view(db, current_user, leave_req.employee_id)

    # ──── 1. The DB (the real place) ────
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

    raise HTTPException(status_code=404, detail="Certificate is not available")
