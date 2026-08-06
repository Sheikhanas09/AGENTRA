import os
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, Form, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.utils.security import get_current_user
from app.models.attendance import (
    LeaveRequest, LeaveBalance, CompanyPolicyOverride,
    CompanyPolicy, PolicyDecisionLog, LeaveTypeEnum
)
from app.models.user import User

router = APIRouter(prefix="/leave", tags=["Leave"])


def require_ceo(current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in ["ceo", "superadmin"]:
        raise HTTPException(status_code=403, detail="Sirf CEO yeh kar sakta hai")
    return current_user


# ──── Schemas ────
class LeaveRequestSchema(BaseModel):
    employee_id: int
    leave_type: str        # "sick", "annual", "casual", "unpaid", "emergency"
    start_date: str        # "2026-05-10"
    end_date: str          # "2026-05-13"
    reason: Optional[str] = ""


class CEODecisionSchema(BaseModel):
    ceo_note: Optional[str] = ""


class BalanceAdjustSchema(BaseModel):
    employee_id: int
    leave_type: str
    adjustment: int        # +5 ya -2
    reason: str


# ──── Helper: Leave Balance lo ya banao ────
def get_or_create_balance(
    db: Session,
    employee_id: int,
    company_id: int,
    leave_type: str,
    year: int
) -> LeaveBalance:

    balance = db.query(LeaveBalance).filter(
        LeaveBalance.employee_id == employee_id,
        LeaveBalance.leave_type == leave_type,
        LeaveBalance.year == year
    ).first()

    if not balance:
        # ──── Default entitlements ────
        defaults = {
            "annual": 15,
            "casual": 10,
            "sick": 10,
            "unpaid": 999,  # Unlimited
            "emergency": 3
        }

        entitlement = defaults.get(leave_type, 10)

        balance = LeaveBalance(
            employee_id=employee_id,
            company_id=company_id,
            year=year,
            leave_type=leave_type,
            total_entitlement=entitlement,
            used_days=0,
            remaining_days=entitlement
        )
        db.add(balance)
        db.flush()

    return balance


# ──────────────────────────────────────────
# Route 1: Leave Request Submit
# ──────────────────────────────────────────
@router.post("/request")
async def submit_leave_request(
    employee_id: int = Form(...),
    leave_type: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    reason: str = Form(""),
    medical_certificate: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Employee leave request kare
    Leave Agent automatically evaluate karega
    """

    from app.agents.leave_agent import evaluate_leave_request

    # ──── Dates parse karo ────
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    total_days = (end - start).days + 1

    if total_days <= 0:
        raise HTTPException(
            status_code=400,
            detail="End date start date se baad honi chahiye"
        )

    # ──── Employee info ────
    employee = db.query(User).filter(
        User.id == employee_id
    ).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee nahi mila")

    # ──── CEO dhundo ────
    ceo = db.query(User).filter(
        User.company_name == employee.company_name,
        User.role == "ceo"
    ).first()
    company_id = ceo.id if ceo else employee_id

    # ──── Medical certificate save karo ────
    cert_path = None
    has_medical_cert = False

    if medical_certificate and medical_certificate.filename:
        upload_dir = os.path.join(
            os.path.dirname(__file__), "..", "uploads", "certificates"
        )
        os.makedirs(upload_dir, exist_ok=True)
        cert_path = os.path.join(
            upload_dir,
            f"cert_{employee_id}_{datetime.now().timestamp()}.pdf"
        )
        content = await medical_certificate.read()
        with open(cert_path, "wb") as f:
            f.write(content)
        has_medical_cert = True

    # ──── Leave balance check karo ────
    balance = get_or_create_balance(
        db, employee_id, company_id,
        leave_type, date.today().year
    )
    db.commit()

    # ──── Leave request DB mein save karo ────
    leave_req = LeaveRequest(
        employee_id=employee_id,
        company_id=company_id,
        leave_type=leave_type,
        start_date=start,
        end_date=end,
        total_days=total_days,
        reason=reason,
        medical_certificate=cert_path,
        status="evaluating"
    )
    db.add(leave_req)
    db.commit()
    db.refresh(leave_req)

    # ──── Manual Override check karo ────
    override = db.query(CompanyPolicyOverride).filter(
        CompanyPolicyOverride.company_id == company_id,
        CompanyPolicyOverride.leave_type == leave_type,
        CompanyPolicyOverride.force_manual == True
    ).first()

    if override:
        # ──── Skip RAG → CEO ko bhejo ────
        leave_req.status = "pending"
        db.commit()

        return {
            "message": "Leave request submitted! CEO approval required (manual override active).",
            "leave_id": leave_req.id,
            "decision": "escalate_to_ceo",
            "reason": f"Manual override active: {override.reason}",
            "status": "pending"
        }

    # ──── Leave Agent call karo ────
    agent_result = evaluate_leave_request(
        employee_id=employee_id,
        company_id=company_id,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        total_days=total_days,
        reason=reason,
        has_medical_cert=has_medical_cert,
        leave_balance=balance.remaining_days
    )

    # ──── Decision log save karo ────
    active_policy = db.query(CompanyPolicy).filter(
        CompanyPolicy.company_id == company_id,
        CompanyPolicy.is_active == True
    ).first()

    decision_log = PolicyDecisionLog(
        leave_request_id=leave_req.id,
        policy_id=active_policy.id if active_policy else None,
        retrieval_query=agent_result.get("retrieval_query", ""),
        retrieved_chunks=agent_result.get("retrieved_chunks", []),
        llm_response={
            "decision": agent_result["decision"],
            "reason": agent_result["reason"],
            "policy_reference": agent_result["policy_reference"]
        },
        decision=agent_result["decision"],
        reason=agent_result["reason"],
        policy_reference=agent_result["policy_reference"]
    )
    db.add(decision_log)

    # ──── Decision apply karo ────
    if agent_result["decision"] == "auto_approve":

        # ──── Balance check ────
        if balance.remaining_days < total_days:
            leave_req.status = "pending"
            db.commit()
            return {
                "message": "Insufficient balance — escalated to CEO",
                "leave_id": leave_req.id,
                "decision": "escalate_to_ceo",
                "reason": f"Only {balance.remaining_days} days remaining",
                "status": "pending"
            }

        # ──── Auto Approve ────
        leave_req.status = "approved"
        leave_req.auto_approved = True
        leave_req.decided_at = datetime.utcnow()

        # ──── Balance deduct karo ────
        balance.used_days += total_days
        balance.remaining_days -= total_days
        balance.last_updated = datetime.utcnow()

        leave_req.payroll_notified = True
        db.commit()

        return {
            "message": "Leave auto-approved!",
            "leave_id": leave_req.id,
            "decision": "auto_approve",
            "reason": agent_result["reason"],
            "policy_reference": agent_result["policy_reference"],
            "status": "approved",
            "remaining_balance": balance.remaining_days
        }

    else:
        # ──── Escalate to CEO ────
        leave_req.status = "pending"
        db.commit()

        return {
            "message": "Leave request submitted! CEO approval required.",
            "leave_id": leave_req.id,
            "decision": "escalate_to_ceo",
            "reason": agent_result["reason"],
            "policy_reference": agent_result["policy_reference"],
            "status": "pending"
        }


# ──────────────────────────────────────────
# Route 2: CEO — Pending Requests
# ──────────────────────────────────────────
@router.get("/pending")
def get_pending_leaves(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """CEO ke liye pending leave requests"""

    ceo = db.query(User).filter(
        User.id == current_user["user_id"]
    ).first()
    company_id = ceo.id

    pending = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.status == "pending"
    ).order_by(LeaveRequest.created_at.desc()).all()

    result = []
    for req in pending:
        employee = db.query(User).filter(
            User.id == req.employee_id
        ).first()

        # ──── RAG decision log nikalo ────
        log = db.query(PolicyDecisionLog).filter(
            PolicyDecisionLog.leave_request_id == req.id
        ).first()

        result.append({
            "leave_id": req.id,
            "employee_id": req.employee_id,
            "employee_name": employee.full_name if employee else "—",
            "leave_type": req.leave_type,
            "start_date": str(req.start_date),
            "end_date": str(req.end_date),
            "total_days": req.total_days,
            "reason": req.reason,
            "has_medical_cert": req.medical_certificate is not None,
            "created_at": str(req.created_at),
            # ──── RAG info ────
            "agent_reason": log.reason if log else None,
            "policy_reference": log.policy_reference if log else None
        })

    return {
        "total": len(result),
        "pending_requests": result
    }


# ──────────────────────────────────────────
# Route 3: CEO — Approve
# ──────────────────────────────────────────
@router.post("/approve/{leave_id}")
def approve_leave(
    leave_id: int,
    data: CEODecisionSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """CEO leave approve kare"""

    leave_req = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.status == "pending"
    ).first()

    if not leave_req:
        raise HTTPException(
            status_code=404,
            detail="Pending request nahi mili"
        )

    # ──── Balance check karo ────
    balance = get_or_create_balance(
        db,
        leave_req.employee_id,
        leave_req.company_id,
        leave_req.leave_type,
        leave_req.start_date.year
    )

    # ──── Approve karo ────
    leave_req.status = "approved"
    leave_req.decided_by = current_user["user_id"]
    leave_req.decided_at = datetime.utcnow()
    leave_req.ceo_note = data.ceo_note
    leave_req.payroll_notified = True

    # ──── Balance deduct karo ────
    if balance.remaining_days >= leave_req.total_days:
        balance.used_days += leave_req.total_days
        balance.remaining_days -= leave_req.total_days
        balance.last_updated = datetime.utcnow()

    db.commit()

    return {
        "message": "Leave approved!",
        "leave_id": leave_id,
        "status": "approved"
    }


# ──────────────────────────────────────────
# Route 4: CEO — Reject
# ──────────────────────────────────────────
@router.post("/reject/{leave_id}")
def reject_leave(
    leave_id: int,
    data: CEODecisionSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """CEO leave reject kare"""

    leave_req = db.query(LeaveRequest).filter(
        LeaveRequest.id == leave_id,
        LeaveRequest.status == "pending"
    ).first()

    if not leave_req:
        raise HTTPException(
            status_code=404,
            detail="Pending request nahi mili"
        )

    leave_req.status = "rejected"
    leave_req.decided_by = current_user["user_id"]
    leave_req.decided_at = datetime.utcnow()
    leave_req.ceo_note = data.ceo_note

    db.commit()

    return {
        "message": "Leave rejected!",
        "leave_id": leave_id,
        "status": "rejected"
    }


# ──────────────────────────────────────────
# Route 5: Leave Balance
# ──────────────────────────────────────────
@router.get("/balance/{employee_id}")
def get_leave_balance(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Employee ka leave balance"""

    year = date.today().year
    employee = db.query(User).filter(
        User.id == employee_id
    ).first()

    ceo = db.query(User).filter(
        User.company_name == employee.company_name,
        User.role == "ceo"
    ).first() if employee else None

    company_id = ceo.id if ceo else employee_id

    # ──── Sab leave types ka balance ────
    leave_types = ["annual", "casual", "sick", "unpaid", "emergency"]
    balances = []

    for lt in leave_types:
        balance = get_or_create_balance(
            db, employee_id, company_id, lt, year
        )
        balances.append({
            "leave_type": lt,
            "total_entitlement": balance.total_entitlement,
            "used_days": balance.used_days,
            "remaining_days": balance.remaining_days
        })

    db.commit()

    return {
        "employee_id": employee_id,
        "year": year,
        "balances": balances
    }


# ──────────────────────────────────────────
# Route 6: Leave History
# ──────────────────────────────────────────
@router.get("/history/{employee_id}")
def get_leave_history(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Employee ki leave history"""

    requests = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id
    ).order_by(LeaveRequest.created_at.desc()).all()

    return {
        "employee_id": employee_id,
        "total": len(requests),
        "history": [
            {
                "leave_id": r.id,
                "leave_type": r.leave_type,
                "start_date": str(r.start_date),
                "end_date": str(r.end_date),
                "total_days": r.total_days,
                "status": r.status,
                "auto_approved": r.auto_approved,
                "reason": r.reason,
                "created_at": str(r.created_at)
            }
            for r in requests
        ]
    }


# ──────────────────────────────────────────
# Route 7: CEO — Balance Adjust
# ──────────────────────────────────────────
@router.patch("/balance/adjust")
def adjust_balance(
    data: BalanceAdjustSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """CEO manually balance adjust kare"""

    ceo = db.query(User).filter(
        User.id == current_user["user_id"]
    ).first()
    company_id = ceo.id
    year = date.today().year

    balance = get_or_create_balance(
        db, data.employee_id, company_id,
        data.leave_type, year
    )

    balance.total_entitlement += data.adjustment
    balance.remaining_days = max(
        0,
        balance.total_entitlement - balance.used_days
    )
    balance.last_updated = datetime.utcnow()
    db.commit()

    return {
        "message": "Balance adjusted!",
        "employee_id": data.employee_id,
        "leave_type": data.leave_type,
        "new_entitlement": balance.total_entitlement,
        "remaining_days": balance.remaining_days
    }


# ──────────────────────────────────────────
# Route 8: CEO — All Leave Requests
# ──────────────────────────────────────────
@router.get("/all")
def get_all_leaves(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """CEO — sari leave requests dekhe"""

    ceo = db.query(User).filter(
        User.id == current_user["user_id"]
    ).first()
    company_id = ceo.id

    requests = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id
    ).order_by(LeaveRequest.created_at.desc()).all()

    result = []
    for req in requests:
        employee = db.query(User).filter(
            User.id == req.employee_id
        ).first()
        result.append({
            "leave_id": req.id,
            "employee_name": employee.full_name if employee else "—",
            "leave_type": req.leave_type,
            "start_date": str(req.start_date),
            "end_date": str(req.end_date),
            "total_days": req.total_days,
            "status": req.status,
            "auto_approved": req.auto_approved,
            "created_at": str(req.created_at)
        })

    return {"total": len(result), "requests": result}