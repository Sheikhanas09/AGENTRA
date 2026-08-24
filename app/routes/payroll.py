"""
Payroll — setup routes
──────────────────────
This is Chunk 1: only the things the CEO sets up ONCE.
The calculation and the slip come in later chunks.

    Branding          → the company's face on the slip (logo, colour, address)
    Salary Structure  → each employee's salary structure
    Payroll Policy    → deduction and overtime rules (company-wide)

═══════════════════════════════════════════════════════════
SECURITY — THIS IS THE MOST SENSITIVE MODULE IN THE SYSTEM
═══════════════════════════════════════════════════════════
In attendance the worst case was someone seeing another person's check-in.
Here the worst case is **one employee seeing another's salary** — and that
poisons relationships in an office.

So three rules, on every route:

  1. Only the CEO writes — `Depends(require_ceo)`
  2. Reading: the CEO sees their whole company, an employee ONLY their own
  3. The company boundary in every query — the `company_id` filter is never
     optional. Trusting `employee_id` alone is not enough, because an id
     can simply be guessed.

`assert_can_view()` (utils/company.py) handles all three of these
in one place — attendance and leave use the same helper, so the three
modules can never behave differently.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from datetime import datetime

from app.database import get_db
from app.models.payroll import (
    CompanyBranding, SalaryStructure, PayrollPolicy, PayrollRun, Payslip
)
from app.models.user import User
from app.utils.company import (
    require_ceo, get_user_or_404, resolve_company_id, assert_can_view,
    company_employees,
)
from app.utils.documents import prepare_document, DocumentError
from app.utils.payroll_data import month_label
from app.utils.security import get_current_user

router = APIRouter(prefix="/payroll", tags=["Payroll"])


# ══════════════════════════════════════════════
# The money ceiling
# ══════════════════════════════════════════════
# Numeric(12,2) leaves 10 digits for the integer part. A larger amount
# would error inside the DB — so we stop it up front, and the CEO gets a
# clear message instead of a 500.
MAX_MONEY = Decimal("9999999999.99")

LATE_POLICIES = ("pro_rata", "per_occurrence", "per_minute", "none")
DEDUCTION_MODES = ("pro_rata", "none")
ABSENT_MODES = ("per_day", "none")


def money(value, field: str) -> Decimal:
    """
    Turn any incoming value into a trustworthy Decimal.

    Building a Decimal straight from a float is wrong:
        Decimal(0.1) → 0.1000000000000000055511151231257827
    So `str()` first — the Decimal matches exactly what the user typed.
    """
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field}: '{value}' is not a valid number")

    if d < 0:
        raise HTTPException(400, f"{field} cannot be negative")
    if d > MAX_MONEY:
        raise HTTPException(400, f"{field} exceeds the limit (max {MAX_MONEY})")
    return d


def ratio(value, field: str, lo=Decimal("0"), hi=Decimal("10")) -> Decimal:
    """A multiplier such as the overtime one — 1.5x, 2x and so on"""
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field}: '{value}' is not a valid number")

    if not (lo <= d <= hi):
        raise HTTPException(400, f"{field} must be between {lo} and {hi}")
    return d


def percent(value, field: str) -> Decimal:
    """Any percentage between 0 and 100"""
    try:
        d = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field}: '{value}' is not a valid number")

    if not (Decimal("0") <= d <= Decimal("100")):
        raise HTTPException(400, f"{field} must be between 0 and 100")
    return d


def as_float(d) -> Optional[float]:
    """
    Prepare a Decimal for JSON.

    JSON cannot carry a Decimal directly. Converting to float here loses
    no money because the arithmetic is already done and the value is fixed
    at two decimals — this is purely for display.
    """
    return None if d is None else float(d)


# ══════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════
class SalaryStructureIn(BaseModel):
    employee_id: int
    base_salary: float = Field(ge=0)
    house_allowance: float = Field(default=0, ge=0)
    transport_allowance: float = Field(default=0, ge=0)
    medical_allowance: float = Field(default=0, ge=0)
    other_allowances: float = Field(default=0, ge=0)
    currency: str = "PKR"
    effective_from: Optional[str] = None


class PayrollPolicyIn(BaseModel):
    overtime_multiplier: float = Field(default=1.5, ge=0, le=10)
    late_deduction_policy: str = "pro_rata"
    late_deduction_amount: float = Field(default=0, ge=0)
    undertime_deduction: str = "none"
    unpaid_leave_deduction: str = "pro_rata"
    absent_deduction: str = "per_day"
    tax_percentage: float = Field(default=0, ge=0, le=100)
    tax_threshold: float = Field(default=0, ge=0)
    provident_fund_percent: float = Field(default=0, ge=0, le=100)


class BrandingIn(BaseModel):
    primary_color: str = "#05DC7F"
    company_address: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    footer_text: Optional[str] = None


# ══════════════════════════════════════════════
# Route 1: Branding — save
# ══════════════════════════════════════════════
@router.post("/branding")
def save_branding(
    data: BrandingIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """The company's face on the slip. The logo has its own route (file upload)."""
    ceo = get_user_or_404(db, current_user["user_id"])

    color = (data.primary_color or "").strip()
    if not color.startswith("#") or len(color) not in (4, 7):
        raise HTTPException(400, "Colour must look like '#05DC7F'")

    row = db.query(CompanyBranding).filter(
        CompanyBranding.company_id == ceo.id
    ).first()

    if not row:
        row = CompanyBranding(company_id=ceo.id)
        db.add(row)

    row.primary_color = color
    row.company_address = data.company_address
    row.contact_email = data.contact_email
    row.contact_phone = data.contact_phone
    row.footer_text = data.footer_text
    row.set_by = ceo.id
    db.commit()

    return {"message": "Branding saved", "company_id": ceo.id}


# ══════════════════════════════════════════════
# Route 2: Logo upload
# ══════════════════════════════════════════════
@router.post("/branding/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    The logo lives in the DB (BYTEA), not on disk.

    Same reason as the attendance photos: keep the backup in one place, and
    a file lost from disk must not leave the slip half-built.

    `prepare_document()` does the validation and compression — the same
    function written for leave certificates. Writing a new one would mean two
    two different limits would drift apart.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    raw = await file.read()
    try:
        prepared = prepare_document(file.filename, raw)
    except DocumentError as e:
        # Show the reason the helper gave — "file rejected" tells nobody
        # anything; the CEO needs to know why
        raise HTTPException(400, str(e))

    if prepared["mime_type"] == "application/pdf":
        raise HTTPException(400, "The logo must be an image (PNG/JPG), not a PDF")

    row = db.query(CompanyBranding).filter(
        CompanyBranding.company_id == ceo.id
    ).first()
    if not row:
        row = CompanyBranding(company_id=ceo.id)
        db.add(row)

    row.logo_data = prepared["data"]
    row.logo_mime = prepared["mime_type"]
    row.logo_filename = file.filename
    row.set_by = ceo.id
    db.commit()

    return {
        "message": "Logo saved",
        "size_kb": round(len(prepared["data"]) / 1024, 1),
        "mime": prepared["mime_type"],
    }


# ══════════════════════════════════════════════
# Route 3: Branding — read
# ══════════════════════════════════════════════
@router.get("/branding")
def get_branding(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Employees can read this too — the logo and address are not secrets, and
    they are printed on their own slip. No salary figure appears here.
    """
    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user)

    row = db.query(CompanyBranding).filter(
        CompanyBranding.company_id == company_id
    ).first()

    if not row:
        return {"branding": None}

    return {
        "branding": {
            "primary_color": row.primary_color,
            "company_address": row.company_address,
            "contact_email": row.contact_email,
            "contact_phone": row.contact_phone,
            "footer_text": row.footer_text,
            "has_logo": row.logo_data is not None,
            "logo_filename": row.logo_filename,
            "updated_at": str(row.updated_at) if row.updated_at else None,
        }
    }


# ══════════════════════════════════════════════
# Route 4: Serve the logo
# ══════════════════════════════════════════════
@router.get("/branding/logo")
def get_logo(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Your own company's logo — company_id comes from the token, not the URL"""
    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user)

    row = db.query(CompanyBranding).filter(
        CompanyBranding.company_id == company_id
    ).first()

    if not row or not row.logo_data:
        raise HTTPException(404, "No logo has been set")

    return Response(
        content=row.logo_data,
        media_type=row.logo_mime or "image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


# ══════════════════════════════════════════════
# Route 5: Salary structure — save (CEO)
# ══════════════════════════════════════════════
@router.post("/salary-structure")
def save_salary_structure(
    data: SalaryStructureIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    Set one employee's salary.

    ═══ SECURITY ═══
    `assert_can_view` confirms this employee REALLY belongs to this CEO's
    company. Without it a CEO could send another company's employee id and
    set their salary.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    employee = assert_can_view(db, current_user, data.employee_id)

    if employee.id == ceo.id:
        raise HTTPException(400, "A CEO's own salary structure is not set here")

    base = money(data.base_salary, "Base salary")
    if base <= 0:
        raise HTTPException(400, "Base salary must be greater than zero")

    row = db.query(SalaryStructure).filter(
        SalaryStructure.employee_id == employee.id
    ).first()

    if not row:
        row = SalaryStructure(employee_id=employee.id, company_id=ceo.id)
        db.add(row)
    else:
        # If the existing row belongs to another company, do not touch it
        if row.company_id != ceo.id:
            raise HTTPException(403, "This record does not belong to your company")

    row.base_salary = base
    row.house_allowance = money(data.house_allowance, "House allowance")
    row.transport_allowance = money(data.transport_allowance, "Transport allowance")
    row.medical_allowance = money(data.medical_allowance, "Medical allowance")
    row.other_allowances = money(data.other_allowances, "Other allowances")
    row.currency = (data.currency or "PKR").strip().upper()[:8]
    row.set_by = ceo.id

    if data.effective_from:
        from datetime import date as _date
        try:
            row.effective_from = _date.fromisoformat(data.effective_from)
        except ValueError:
            raise HTTPException(400, "effective_from must be YYYY-MM-DD")

    db.commit()
    db.refresh(row)

    gross = (row.base_salary + row.house_allowance + row.transport_allowance
             + row.medical_allowance + row.other_allowances)

    return {
        "message": f"Salary structure saved for {employee.full_name}",
        "employee_id": employee.id,
        "gross_fixed": as_float(gross),
        "currency": row.currency,
    }


def _structure_out(row: SalaryStructure, employee: User = None) -> dict:
    gross = (row.base_salary + row.house_allowance + row.transport_allowance
             + row.medical_allowance + row.other_allowances)
    out = {
        "employee_id": row.employee_id,
        "base_salary": as_float(row.base_salary),
        "house_allowance": as_float(row.house_allowance),
        "transport_allowance": as_float(row.transport_allowance),
        "medical_allowance": as_float(row.medical_allowance),
        "other_allowances": as_float(row.other_allowances),
        "gross_fixed": as_float(gross),
        "currency": row.currency,
        "effective_from": str(row.effective_from) if row.effective_from else None,
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }
    if employee is not None:
        out["employee_name"] = employee.full_name
        out["department"] = employee.department
    return out


# ══════════════════════════════════════════════
# Route 6: Saare structures (CEO)
# ══════════════════════════════════════════════
@router.get("/salary-structures")
def list_salary_structures(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    The whole team's structure — including those not yet set up, otherwise
    the CEO would never notice whose salary is missing (and
    the payroll run skips them).
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    employees = company_employees(db, ceo)

    rows = {
        r.employee_id: r
        for r in db.query(SalaryStructure).filter(
            SalaryStructure.company_id == ceo.id
        ).all()
    }

    out, missing = [], 0
    for emp in employees:
        row = rows.get(emp.id)
        if row:
            out.append({**_structure_out(row, emp), "is_set": True})
        else:
            missing += 1
            out.append({
                "employee_id": emp.id,
                "employee_name": emp.full_name,
                "department": emp.department,
                "is_set": False,
                "base_salary": None,
                "gross_fixed": None,
                "currency": "PKR",
            })

    return {
        "total": len(out),
        "missing": missing,
        "structures": out,
    }


# ══════════════════════════════════════════════
# Route 7: Meri salary (employee)
# ══════════════════════════════════════════════
@router.get("/salary-structure/{employee_id}")
def get_salary_structure(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    An employee sees only THEIR OWN; the CEO sees anyone in their company.

    `assert_can_view` makes that decision and raises 403 — so there is no
    way to see someone else's salary by passing their id.
    """
    employee = assert_can_view(db, current_user, employee_id)

    row = db.query(SalaryStructure).filter(
        SalaryStructure.employee_id == employee_id
    ).first()

    if not row:
        return {"structure": None, "message": "No salary structure has been set yet"}

    return {"structure": _structure_out(row, employee)}


# ══════════════════════════════════════════════
# Route 8: Payroll policy — save (CEO)
# ══════════════════════════════════════════════
@router.post("/policy")
def save_payroll_policy(
    data: PayrollPolicyIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    ceo = get_user_or_404(db, current_user["user_id"])

    if data.late_deduction_policy not in LATE_POLICIES:
        raise HTTPException(400, f"late_deduction_policy must be one of: {LATE_POLICIES}")
    if data.undertime_deduction not in DEDUCTION_MODES:
        raise HTTPException(400, f"undertime_deduction must be one of: {DEDUCTION_MODES}")
    if data.unpaid_leave_deduction not in DEDUCTION_MODES:
        raise HTTPException(400, f"unpaid_leave_deduction must be one of: {DEDUCTION_MODES}")
    if data.absent_deduction not in ABSENT_MODES:
        raise HTTPException(400, f"absent_deduction must be one of: {ABSENT_MODES}")

    row = db.query(PayrollPolicy).filter(
        PayrollPolicy.company_id == ceo.id
    ).first()
    if not row:
        row = PayrollPolicy(company_id=ceo.id)
        db.add(row)

    row.overtime_multiplier = ratio(data.overtime_multiplier, "Overtime multiplier")
    row.late_deduction_policy = data.late_deduction_policy
    row.late_deduction_amount = money(data.late_deduction_amount, "Late deduction")
    row.undertime_deduction = data.undertime_deduction
    row.unpaid_leave_deduction = data.unpaid_leave_deduction
    row.absent_deduction = data.absent_deduction
    row.tax_percentage = percent(data.tax_percentage, "Tax %")
    row.tax_threshold = money(data.tax_threshold, "Tax threshold")
    row.provident_fund_percent = percent(data.provident_fund_percent, "Provident fund %")
    row.set_by = ceo.id

    db.commit()
    return {"message": "Payroll policy saved"}


def _policy_out(row: PayrollPolicy) -> dict:
    return {
        "overtime_multiplier": as_float(row.overtime_multiplier),
        "late_deduction_policy": row.late_deduction_policy,
        "late_deduction_amount": as_float(row.late_deduction_amount),
        "undertime_deduction": row.undertime_deduction,
        "unpaid_leave_deduction": row.unpaid_leave_deduction,
        "absent_deduction": row.absent_deduction,
        "tax_percentage": as_float(row.tax_percentage),
        "tax_threshold": as_float(row.tax_threshold),
        "provident_fund_percent": as_float(row.provident_fund_percent),
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }


# ══════════════════════════════════════════════
# Route 9: Payroll policy — read
# ══════════════════════════════════════════════
@router.get("/policy")
def get_payroll_policy(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Employees can see this too — these are the company's RULES, not
    anyone's salary. An employee should know what arriving late costs,
    exactly as they should know their working hours
    chahiyein.
    """
    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user)

    row = db.query(PayrollPolicy).filter(
        PayrollPolicy.company_id == company_id
    ).first()

    return {"policy": _policy_out(row) if row else None}


# ══════════════════════════════════════════════
# Route 10: Run the payroll
# ══════════════════════════════════════════════
class RunIn(BaseModel):
    period: str                       # "2026-05"
    force: bool = False               # cancel the previous run and redo it


@router.post("/run")
def run_payroll(
    data: RunIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    One month's payroll — for the whole team.

    ═══ ONE MONTH, ONE RUN ═══
    Running the same period again returns a 409. Otherwise two sets of
    slips would exist and "which one is real" becomes an open question.

    To rerun it (say the CEO fixed a salary structure) use `force: true` —
    the old run is **cancelled**, never deleted. Its slips are cancelled
    too but the record remains. Nothing in payroll should ever vanish.

    ═══ ONE PERSON'S ERROR DOES NOT STOP EVERYONE'S PAYROLL ═══
    An employee without a salary structure gets a "failed" slip while
    everyone else's is produced. The list shows the CEO exactly whose was
    left out.
    """
    from app.agents.payroll_agent import run_for_employee
    from app.utils.payroll_data import parse_period
    from app.utils.pkt import get_pkt_now

    ceo = get_user_or_404(db, current_user["user_id"])

    try:
        start, _end = parse_period(data.period)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # ──── A future month cannot be run ────
    # That month's attendance has not happened yet — the slip would be a lie
    if start > get_pkt_now().date():
        raise HTTPException(
            400,
            f"{data.period} has not arrived yet — there is no attendance "
            f"data for that month"
        )

    # ──── Is there already a run? ────
    existing = db.query(PayrollRun).filter(
        PayrollRun.company_id == ceo.id,
        PayrollRun.period == data.period,
        PayrollRun.status != "cancelled",
    ).order_by(PayrollRun.attempt.desc()).first()

    attempt = 1
    if existing:
        if not data.force:
            raise HTTPException(
                409,
                f"Payroll for {data.period} has already run "
                f"(status: {existing.status}). To run it again send force - "
                f"the previous run will be cancelled."
            )
        existing.status = "cancelled"
        existing.error_note = f"Re-run by the CEO ({datetime.utcnow()})"
        db.query(Payslip).filter(Payslip.run_id == existing.id).update(
            {"status": "cancelled"}, synchronize_session=False
        )

        # ═══ A CANCELLED RUN'S INSTALMENT COMES BACK TOO ═══
        # Without this line the loan is deducted TWICE: the old run's
        # instalment row stays behind and the new run creates its own
        # (`(loan_id, run_id)` is unique, and the run id is new).
        #
        # The outstanding balance is the sum of these rows — so removing
        # the row restores it automatically. The slip's own record is still
        # safe, because it keeps the instalment inside its snapshot.
        from app.models.payroll import LoanRepayment, EmployeeLoan

        # Only the ids of loans whose instalment was taken in this run —
        # do not touch any other loan
        touched = [
            r.loan_id for r in db.query(LoanRepayment).filter(
                LoanRepayment.run_id == existing.id
            ).all()
        ]

        db.query(LoanRepayment).filter(
            LoanRepayment.run_id == existing.id
        ).delete(synchronize_session=False)

        # A loan marked `cleared` because of this run now has a balance
        # again — put it back to `active`
        if touched:
            db.query(EmployeeLoan).filter(
                EmployeeLoan.id.in_(touched),
                EmployeeLoan.status == "cleared",
            ).update({"status": "active"}, synchronize_session=False)

        attempt = (existing.attempt or 1) + 1
        db.flush()

    employees = company_employees(db, ceo)
    if not employees:
        raise HTTPException(400, "This company has no employees")

    run = PayrollRun(
        company_id=ceo.id,
        period=data.period,
        attempt=attempt,
        triggered_by="ceo",
        triggered_by_user=ceo.id,
        status="processing",
        employees_total=len(employees),
    )
    db.add(run)
    db.flush()

    # ──── Run the agent for each employee ────
    done, failed = 0, 0
    total_gross = Decimal("0.00")
    total_ded = Decimal("0.00")
    total_net = Decimal("0.00")
    results = []

    for emp in employees:
        out = run_for_employee(db, emp.id, ceo.id, data.period, run.id)

        if out["status"] == "computed":
            done += 1
            total_gross += out["gross_pay"]
            total_ded += out["total_deductions"]
            total_net += out["net_salary"]
        else:
            failed += 1

        results.append({
            "employee_id": out["employee_id"],
            "employee_name": emp.full_name,
            "status": out["status"],
            "error": out["error"],
            "net_salary": as_float(out["net_salary"]),
            "warnings": out["warnings"],
        })

    run.employees_done = done
    run.employees_failed = failed
    run.total_gross = total_gross
    run.total_deductions = total_ded
    run.total_payroll_cost = total_net
    # The slips exist - the CEO reviews and approves. Emails go only then.
    run.status = "pending_approval" if done else "failed"
    run.completed_at = datetime.utcnow()
    if not done:
        run.error_note = "No slip could be produced for any employee"

    db.commit()

    return {
        "message": f"Payroll for {data.period} has run",
        "run_id": run.id,
        "period": data.period,
        "attempt": attempt,
        "status": run.status,
        "employees_total": len(employees),
        "employees_done": done,
        "employees_failed": failed,
        "total_gross": as_float(total_gross),
        "total_deductions": as_float(total_ded),
        "total_payroll_cost": as_float(total_net),
        "results": results,
    }


# ══════════════════════════════════════════════
# Route 11: List of runs (CEO)
# ══════════════════════════════════════════════
@router.get("/runs")
def list_runs(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    ceo = get_user_or_404(db, current_user["user_id"])
    runs = db.query(PayrollRun).filter(
        PayrollRun.company_id == ceo.id
    ).order_by(PayrollRun.period.desc(), PayrollRun.attempt.desc()).all()

    return {
        "total": len(runs),
        "runs": [{
            "run_id": r.id,
            "period": r.period,
            "attempt": r.attempt,
            "status": r.status,
            "triggered_by": r.triggered_by,
            "employees_total": r.employees_total,
            "employees_done": r.employees_done,
            "employees_failed": r.employees_failed,
            "total_payroll_cost": as_float(r.total_payroll_cost),
            "total_gross": as_float(r.total_gross),
            "run_at": str(r.run_at) if r.run_at else None,
            "completed_at": str(r.completed_at) if r.completed_at else None,
            "approved_at": str(r.approved_at) if r.approved_at else None,
            "error_note": r.error_note,
        } for r in runs]
    }


# ══════════════════════════════════════════════
# Route 12: One run in detail (CEO)
# ══════════════════════════════════════════════
@router.get("/run/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    The full detail of a run - a breakdown per employee.

    The `company_id` filter is mandatory: trusting `run_id` alone would let
    a CEO pass another company's run id and read that whole team's salaries
    dekh leta.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    run = db.query(PayrollRun).filter(
        PayrollRun.id == run_id,
        PayrollRun.company_id == ceo.id,
    ).first()
    if not run:
        raise HTTPException(404, "Run not found")

    slips = db.query(Payslip).filter(Payslip.run_id == run.id).all()
    names = {u.id: u for u in company_employees(db, ceo)}

    return {
        "run": {
            "run_id": run.id,
            "period": run.period,
            "attempt": run.attempt,
            "status": run.status,
            "employees_total": run.employees_total,
            "employees_done": run.employees_done,
            "employees_failed": run.employees_failed,
            "total_gross": as_float(run.total_gross),
            "total_deductions": as_float(run.total_deductions),
            "total_payroll_cost": as_float(run.total_payroll_cost),
            "run_at": str(run.run_at) if run.run_at else None,
            "approved_at": str(run.approved_at) if run.approved_at else None,
        },
        "payslips": [{
            "payslip_id": s.id,
            "employee_id": s.employee_id,
            "employee_name": getattr(names.get(s.employee_id), "full_name", None),
            "department": getattr(names.get(s.employee_id), "department", None),
            "gross_pay": as_float(s.gross_pay),
            "total_deductions": as_float(s.total_deductions),
            "net_salary": as_float(s.net_salary),
            "currency": s.currency,
            "status": s.status,
            "has_pdf": s.slip_pdf is not None,
            "warnings": (s.calculation_notes or {}).get("warnings", []),
        } for s in slips]
    }


# ══════════════════════════════════════════════
# Route 13: My slips (employee) / anyone's (CEO)
# ══════════════════════════════════════════════
@router.get("/slips")
def my_slips(
    employee_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    An employee sees all of their own slips.

    The CEO can pass `?employee_id=` to see any of their own employees' —
    and `assert_can_view` is what decides that the employee really belongs
    to their company.

    ═══ CANCELLED SLIPS ARE NEVER SHOWN ═══
    When the CEO reruns payroll the old run is cancelled. Its slips stay in
    the DB for the record, but the employee should only see the real one —
    otherwise two different salaries appear and
    confusion ho.
    """
    target = employee_id if employee_id is not None else current_user["user_id"]
    employee = assert_can_view(db, current_user, target)

    slips = db.query(Payslip).filter(
        Payslip.employee_id == employee.id,
        Payslip.status != "cancelled",
    ).order_by(Payslip.period.desc(), Payslip.id.desc()).all()

    return {
        "employee_id": employee.id,
        "employee_name": employee.full_name,
        "total": len(slips),
        "slips": [{
            "payslip_id": s.id,
            "period": s.period,
            "period_label": month_label(s.period),
            "gross_pay": as_float(s.gross_pay),
            "total_deductions": as_float(s.total_deductions),
            "net_salary": as_float(s.net_salary),
            "currency": s.currency,
            "status": s.status,
            "has_pdf": s.slip_pdf is not None,
            "created_at": str(s.created_at) if s.created_at else None,
            "email_sent_at": str(s.email_sent_at) if s.email_sent_at else None,
        } for s in slips]
    }


# ══════════════════════════════════════════════
# Route 14: One slip in full detail
# ══════════════════════════════════════════════
@router.get("/slip/{payslip_id}")
def get_slip(
    payslip_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    The slip's full breakdown — with the snapshots and calculation steps.

    An employee should see HOW their salary was built, not just the net
    figure. Every step is written into `calculation_notes.steps`.
    """
    slip = db.query(Payslip).filter(Payslip.id == payslip_id).first()
    if not slip:
        raise HTTPException(404, "Slip not found")

    # ═══ Ownership — the single most important line ═══
    # Trusting payslip_id alone would let any employee try 1, 2, 3... and
    # read the whole company's salaries.
    assert_can_view(db, current_user, slip.employee_id)

    notes = slip.calculation_notes or {}
    return {
        "slip": {
            "payslip_id": slip.id,
            "employee_id": slip.employee_id,
            "period": slip.period,
            "period_label": month_label(slip.period),
            "currency": slip.currency,
            "status": slip.status,
            "has_pdf": slip.slip_pdf is not None,

            "earnings": {
                "base_salary": as_float(slip.base_salary),
                "allowances_total": as_float(slip.allowances_total),
                "overtime_pay": as_float(slip.overtime_pay),
                "incentive_pay": as_float(slip.incentive_pay),
                "arrears": as_float(slip.arrears),
                "bonus": as_float(slip.bonus),
                "commission": as_float(slip.commission),
                "other_earnings": as_float(slip.other_earnings),
                "gross_pay": as_float(slip.gross_pay),
            },
            "deductions": {
                "late_deduction": as_float(slip.late_deduction),
                "undertime_deduction": as_float(slip.undertime_deduction),
                "unpaid_leave_deduction": as_float(slip.unpaid_leave_deduction),
                "absent_deduction": as_float(slip.absent_deduction),
                "tax_deduction": as_float(slip.tax_deduction),
                "provident_fund": as_float(slip.provident_fund),
                "loan_deduction": as_float(slip.loan_deduction),
                "other_deductions": as_float(slip.other_deductions),
                "total_deductions": as_float(slip.total_deductions),
            },
            "net_salary": as_float(slip.net_salary),

            "attendance": slip.attendance_snapshot,
            "salary_structure": slip.salary_snapshot,
            "policy": slip.policy_snapshot,
            "calculation_steps": notes.get("steps", []),
            "warnings": notes.get("warnings", []),
            "rates": notes.get("rates", {}),

            "created_at": str(slip.created_at) if slip.created_at else None,
        }
    }


# ══════════════════════════════════════════════
# Route 15: PDF download
# ══════════════════════════════════════════════
@router.get("/slip/{payslip_id}/download")
def download_slip(
    payslip_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    The slip as a PDF.

    ═══ THE MOST SENSITIVE ENDPOINT IN THE SYSTEM ═══
    This hands out a file containing somebody's entire salary. Three
    safeguards:

      1. Login required (`get_current_user`)
      2. `assert_can_view` — your own slip, or the CEO's own company's
      3. Cache `private` — no proxy or CDN may store it

    This also defeats id guessing: an employee trying 1, 2, 3... gets a 403
    every time.
    """
    slip = db.query(Payslip).filter(Payslip.id == payslip_id).first()
    if not slip:
        raise HTTPException(404, "Slip not found")

    employee = assert_can_view(db, current_user, slip.employee_id)

    if not slip.slip_pdf:
        raise HTTPException(
            404,
            "The PDF for this slip has not been produced yet — ask HR to "
            "karne ko kahein"
        )

    safe_name = "".join(
        ch for ch in (employee.full_name or "employee")
        if ch.isalnum() or ch in (" ", "-", "_")
    ).strip().replace(" ", "_") or "employee"

    return Response(
        content=slip.slip_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="salary-slip-{safe_name}-{slip.period}.pdf"',
            # A salary must never be cached
            "Cache-Control": "private, no-store",
        },
    )


# ══════════════════════════════════════════════
# Route 16: Rebuild the PDFs (CEO)
# ══════════════════════════════════════════════
@router.post("/run/{run_id}/regenerate-pdfs")
def regenerate_pdfs(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    Rebuild PDFs that failed, or where the CEO changed the branding.

    The salary arithmetic is NOT touched at all — only the PDF is rebuilt.
    That makes this operation safe: every money figure stays exactly as it
    was.
    """
    from app.agents.slip_generator_agent import generate_slip_pdf

    ceo = get_user_or_404(db, current_user["user_id"])
    run = db.query(PayrollRun).filter(
        PayrollRun.id == run_id, PayrollRun.company_id == ceo.id
    ).first()
    if not run:
        raise HTTPException(404, "Run not found")

    slips = db.query(Payslip).filter(
        Payslip.run_id == run.id, Payslip.status != "cancelled"
    ).all()

    done, failed = 0, 0
    for s in slips:
        out = generate_slip_pdf(db, s.id, ceo.id)
        if out["status"] == "generated":
            done += 1
        else:
            failed += 1
            s.error_note = f"PDF could not be produced: {out.get('error')}"

    db.commit()
    return {
        "message": f"{done} PDF(s) rebuilt",
        "generated": done,
        "failed": failed,
        "total": len(slips),
    }


# ══════════════════════════════════════════════
# Payslip email — via MCP
# ══════════════════════════════════════════════
async def _email_slips_via_mcp(company_name: str, slips: list) -> dict:
    """
    Send the slips through MCP's `send_payroll_email` tool.

    ═══ WHY THE EMAIL GOES VIA MCP AND THE PDF DOES NOT ═══
    Building a PDF is work for our own process — putting a protocol in the
    middle would gain nothing. But Gmail really IS external: a separate
    service, separate credentials, failing in its own way. That is exactly
    what MCP is for — outside things, wrapped as a tool.

    That is why all the recruitment emails go through it too, and the
    payroll email is Tool 6 in the same server.

    ═══ ONE FAILED EMAIL DOES NOT STOP THE REST ═══
    Each slip has its own outcome. One wrong address only marks that slip
    "failed" — the other 49 still go out.
    """
    import base64
    import os

    # The same sender the whole system uses — `notify.py` uses it too.
    # A new env var would mean two addresses in two places, and employees
    # would sometimes get mail from a different one.
    from app.utils.notify import SENDER_EMAIL, SENDER_PASSWORD

    sender = SENDER_EMAIL or ""
    password = SENDER_PASSWORD or ""

    if not sender or not password:
        return {
            "ok": False,
            "reason": "Gmail credentials are not set (GMAIL_APP_PASSWORD) - "
                      "the slips were generated but not emailed",
            "results": {},
        }

    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # ═══ THE SAME PYTHON THE APP IS RUNNING ON ═══
    # Writing `command="python"` is dangerous: on Windows `python` picks the
    # first interpreter on PATH. On this machine that was 3.11, while the app
    # runs on 3.14 — and `mcp` is installed only in 3.14. The result: the MCP
    # server died silently with "ModuleNotFoundError" and the email never went.
    #
    # `sys.executable` always gives the interpreter this code is running on —
    # so both sides share one environment.
    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "..",
                           "mcp_servers", "meeting_email_server.py")],
    )

    results = {}
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                for item in slips:
                    try:
                        out = await session.call_tool("send_payroll_email", {
                            "employee_name": item["name"],
                            "employee_email": item["email"],
                            "period_label": item["period_label"],
                            "net_salary": item["net_salary"],
                            "currency": item["currency"],
                            "company_name": company_name,
                            "pdf_base64": base64.b64encode(item["pdf"]).decode(),
                            "sender_email": sender,
                            "sender_password": password,
                        })
                        text = ""
                        for c in (out.content or []):
                            text += getattr(c, "text", "")
                        results[item["payslip_id"]] = {
                            "sent": "emailed" in text.lower(),
                            "detail": text[:200],
                        }
                    except Exception as e:
                        results[item["payslip_id"]] = {
                            "sent": False, "detail": f"MCP call: {e}"[:200]
                        }

        return {"ok": True, "reason": "", "results": results}

    except Exception as e:
        # The MCP server may not even start — the slips are still safe
        return {"ok": False, "reason": f"MCP server: {e}", "results": {}}


# ══════════════════════════════════════════════
# Route 17: Approve & Disburse (CEO)
# ══════════════════════════════════════════════
@router.post("/run/{run_id}/approve")
async def approve_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    The CEO approves the payroll — only then are the slips emailed.

    ═══ WHY THERE IS AN APPROVAL GATE ═══
    After payroll runs the status stays `pending_approval`. The CEO can
    look at the real PDFs first, read the warnings, and if necessary fix a
    salary structure and run it again.

    An email goes out ONCE — it cannot be recalled. So it only goes after a
    human has confirmed, exactly as the CEO decides on leave.

    ═══ THE APPROVAL STANDS EVEN IF THE EMAIL FAILS ═══
    The slips are in the DB and the employee can download them from the
    portal. The email is only a convenience. So if it fails the run still
    ends up `completed` and the reason is written into `email_note` - the
    CEO can resend later.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    run = db.query(PayrollRun).filter(
        PayrollRun.id == run_id, PayrollRun.company_id == ceo.id
    ).first()
    if not run:
        raise HTTPException(404, "Run not found")

    if run.status == "cancelled":
        raise HTTPException(400, "A cancelled run cannot be approved")
    if run.status == "completed":
        raise HTTPException(400, "This run has already been approved")
    if not run.employees_done:
        raise HTTPException(400, "No slip was produced in this run")

    slips = db.query(Payslip).filter(
        Payslip.run_id == run.id, Payslip.status == "computed"
    ).all()

    employees = {u.id: u for u in company_employees(db, ceo)}

    # ──── Slips that are ready to email ────
    to_send, skipped = [], []
    for s in slips:
        emp = employees.get(s.employee_id)
        if not emp or not getattr(emp, "email", None):
            skipped.append({"payslip_id": s.id, "reason": "no email address"})
            continue
        if not s.slip_pdf:
            skipped.append({"payslip_id": s.id, "reason": "no PDF was produced"})
            continue
        to_send.append({
            "payslip_id": s.id,
            "name": emp.full_name or "Employee",
            "email": emp.email,
            "period_label": month_label(s.period),
            "net_salary": f"{s.net_salary:,.2f}",
            "currency": s.currency or "PKR",
            "pdf": s.slip_pdf,
        })

    # ──── Emails are switched off during testing ────
    import os
    enabled = os.getenv("NOTIFICATIONS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off"
    )

    if not enabled:
        outcome = {"ok": False, "reason": "NOTIFICATIONS_ENABLED=false",
                   "results": {}}
    elif to_send:
        company_name = getattr(ceo, "company_name", None) or "Company"
        outcome = await _email_slips_via_mcp(company_name, to_send)
    else:
        outcome = {"ok": True, "reason": "", "results": {}}

    # ──── Write the outcome back onto the slips ────
    sent = 0
    for s in slips:
        res = (outcome.get("results") or {}).get(s.id)
        if res and res.get("sent"):
            s.status = "sent"
            s.email_sent_at = datetime.utcnow()
            sent += 1
        elif res:
            s.error_note = f"Email was not sent: {res.get('detail')}"

    # Whether or not the email goes — the approval has happened
    run.status = "completed"
    run.approved_at = datetime.utcnow()
    run.approved_by = ceo.id
    if not outcome.get("ok"):
        run.error_note = f"Slips are ready, email was not sent: {outcome.get('reason')}"

    db.commit()

    return {
        "message": f"Payroll for {run.period} has been approved",
        "run_id": run.id,
        "status": run.status,
        "slips_total": len(slips),
        "emails_sent": sent,
        "emails_skipped": skipped,
        "email_ok": outcome.get("ok"),
        "email_note": outcome.get("reason") or None,
    }


# ══════════════════════════════════════════════
# Route 18: Resend the email (CEO)
# ══════════════════════════════════════════════
@router.post("/slip/{payslip_id}/resend")
async def resend_slip_email(
    payslip_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    Resend one slip's email — if it failed the first time, or the employee
    says it never arrived.

    Neither the salary nor the PDF changes; the same stored PDF is resent.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    slip = db.query(Payslip).filter(
        Payslip.id == payslip_id, Payslip.company_id == ceo.id
    ).first()
    if not slip:
        raise HTTPException(404, "Slip not found")
    if not slip.slip_pdf:
        raise HTTPException(400, "No PDF was produced for this slip")
    if slip.status == "cancelled":
        raise HTTPException(400, "A cancelled slip is not emailed")

    emp = assert_can_view(db, current_user, slip.employee_id)
    if not getattr(emp, "email", None):
        raise HTTPException(400, "The employee has no email address")

    import os
    enabled = os.getenv("NOTIFICATIONS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off"
    )
    if not enabled:
        return {"message": "Notifications are disabled (NOTIFICATIONS_ENABLED=false)",
                "sent": False}

    outcome = await _email_slips_via_mcp(
        getattr(ceo, "company_name", None) or "Company",
        [{
            "payslip_id": slip.id,
            "name": emp.full_name or "Employee",
            "email": emp.email,
            "period_label": month_label(slip.period),
            "net_salary": f"{slip.net_salary:,.2f}",
            "currency": slip.currency or "PKR",
            "pdf": slip.slip_pdf,
        }])

    res = (outcome.get("results") or {}).get(slip.id) or {}
    if res.get("sent"):
        slip.status = "sent"
        slip.email_sent_at = datetime.utcnow()
        slip.error_note = None
        db.commit()
        return {"message": f"Slip emailed to {emp.full_name}", "sent": True}

    slip.error_note = f"Email was not sent: {res.get('detail') or outcome.get('reason')}"
    db.commit()
    return {
        "message": "The email could not be sent",
        "sent": False,
        "reason": res.get("detail") or outcome.get("reason"),
    }


# ══════════════════════════════════════════════
# Adjustments — the one-off items each month
# ══════════════════════════════════════════════
class AdjustmentIn(BaseModel):
    employee_id: int
    period: str
    kind: str
    amount: float = Field(gt=0)
    note: Optional[str] = None


@router.post("/adjustment")
def add_adjustment(
    data: AdjustmentIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    One item for one month — incentive, arrears, bonus, commission, or a
    one-off deduction.

    ═══ THE AMOUNT IS ALWAYS POSITIVE ═══
    Whether it is an earning or a deduction is decided by `kind`, never by
    the sign of the number. Negatives are simply rejected — otherwise a
    "deduction of -5000" would invert and
    would quietly inflate the salary.

    These must be added BEFORE payroll runs — a completed run is unaffected
    (its slip is frozen with its snapshots). To change it, add the
    adjustment and run payroll again.
    """
    from app.models.payroll import PayrollAdjustment
    from app.utils.payroll_data import ALL_KINDS, KIND_LABELS, parse_period

    ceo = get_user_or_404(db, current_user["user_id"])
    employee = assert_can_view(db, current_user, data.employee_id)

    if data.kind not in ALL_KINDS:
        raise HTTPException(400, f"kind must be one of: {ALL_KINDS}")

    try:
        parse_period(data.period)
    except ValueError as e:
        raise HTTPException(400, str(e))

    amount = money(data.amount, KIND_LABELS.get(data.kind, data.kind))
    if amount <= 0:
        raise HTTPException(400, "The amount must be greater than zero")

    row = PayrollAdjustment(
        company_id=ceo.id,
        employee_id=employee.id,
        period=data.period,
        kind=data.kind,
        amount=amount,
        note=(data.note or "").strip() or None,
        created_by=ceo.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "message": f"{KIND_LABELS.get(data.kind)} {amount} — {employee.full_name}",
        "adjustment_id": row.id,
    }


@router.get("/adjustments")
def list_adjustments(
    period: str,
    employee_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    That month's adjustments.

    An employee only sees their own — omitting `employee_id` returns theirs,
    and asking for someone else's is stopped by `assert_can_view`.
    """
    from app.models.payroll import PayrollAdjustment
    from app.utils.payroll_data import EARNING_KINDS, KIND_LABELS

    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user)

    q = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.company_id == company_id,
        PayrollAdjustment.period == period,
    )

    if employee_id is not None:
        assert_can_view(db, current_user, employee_id)
        q = q.filter(PayrollAdjustment.employee_id == employee_id)
    elif current_user["role"] not in ("ceo", "superadmin"):
        q = q.filter(PayrollAdjustment.employee_id == user.id)

    rows = q.order_by(PayrollAdjustment.id.desc()).all()
    names = {}
    if current_user["role"] in ("ceo", "superadmin"):
        names = {u.id: u.full_name for u in company_employees(db, user)}

    return {
        "period": period,
        "total": len(rows),
        "adjustments": [{
            "adjustment_id": r.id,
            "employee_id": r.employee_id,
            "employee_name": names.get(r.employee_id),
            "kind": r.kind,
            "label": KIND_LABELS.get(r.kind, r.kind),
            "is_earning": r.kind in EARNING_KINDS,
            "amount": as_float(r.amount),
            "note": r.note,
        } for r in rows]
    }


@router.delete("/adjustment/{adjustment_id}")
def delete_adjustment(
    adjustment_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """Remove something added by mistake — before payroll runs"""
    from app.models.payroll import PayrollAdjustment

    ceo = get_user_or_404(db, current_user["user_id"])
    row = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.id == adjustment_id,
        PayrollAdjustment.company_id == ceo.id,
    ).first()
    if not row:
        raise HTTPException(404, "Adjustment not found")

    db.delete(row)
    db.commit()
    return {"message": "Removed"}


# ══════════════════════════════════════════════
# Loans
# ══════════════════════════════════════════════
class LoanIn(BaseModel):
    employee_id: int
    title: str
    principal: float = Field(gt=0)
    installment: float = Field(gt=0)
    start_period: str
    note: Optional[str] = None


@router.post("/loan")
def create_loan(
    data: LoanIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    A new loan/advance — the instalment is deducted automatically each month.

    The CEO enters it once; the loan closes itself as soon as the balance
    reaches zero. Nothing to remember each month.
    """
    from app.models.payroll import EmployeeLoan
    from app.utils.payroll_data import parse_period

    ceo = get_user_or_404(db, current_user["user_id"])
    employee = assert_can_view(db, current_user, data.employee_id)

    try:
        parse_period(data.start_period)
    except ValueError as e:
        raise HTTPException(400, str(e))

    principal = money(data.principal, "Loan")
    installment = money(data.installment, "Instalment")

    if installment > principal:
        raise HTTPException(400, "The instalment cannot exceed the whole loan")

    title = (data.title or "").strip()
    if not title:
        raise HTTPException(400, "Give the loan a title (e.g. 'Bike advance')")

    row = EmployeeLoan(
        company_id=ceo.id,
        employee_id=employee.id,
        title=title[:120],
        principal=principal,
        installment=installment,
        start_period=data.start_period,
        status="active",
        note=(data.note or "").strip() or None,
        created_by=ceo.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    months = int((principal / installment).to_integral_value(rounding="ROUND_CEILING"))
    return {
        "message": f"Loan of {principal} for {employee.full_name} — {months} month(s)",
        "loan_id": row.id,
        "months": months,
    }


def _loan_out(db, loan, employee_name=None) -> dict:
    from app.utils.payroll_data import loan_remaining

    remaining = loan_remaining(db, loan)
    paid = loan.principal - remaining
    return {
        "loan_id": loan.id,
        "employee_id": loan.employee_id,
        "employee_name": employee_name,
        "title": loan.title,
        "principal": as_float(loan.principal),
        "installment": as_float(loan.installment),
        "paid": as_float(paid),
        "remaining": as_float(remaining),
        "progress_pct": (
            float(paid / loan.principal * 100) if loan.principal else 0
        ),
        "start_period": loan.start_period,
        "status": loan.status,
        "note": loan.note,
    }


@router.get("/loans")
def list_loans(
    employee_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    The list of loans — the outstanding amount is DERIVED, never counted.

    An employee only sees their own loans (and should see them — how much
    they still owe is their right to know).
    """
    from app.models.payroll import EmployeeLoan

    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user)

    q = db.query(EmployeeLoan).filter(EmployeeLoan.company_id == company_id)

    if employee_id is not None:
        assert_can_view(db, current_user, employee_id)
        q = q.filter(EmployeeLoan.employee_id == employee_id)
    elif current_user["role"] not in ("ceo", "superadmin"):
        q = q.filter(EmployeeLoan.employee_id == user.id)

    loans = q.order_by(EmployeeLoan.id.desc()).all()
    names = {}
    if current_user["role"] in ("ceo", "superadmin"):
        names = {u.id: u.full_name for u in company_employees(db, user)}

    return {
        "total": len(loans),
        "loans": [_loan_out(db, l, names.get(l.employee_id)) for l in loans],
    }


@router.post("/loan/{loan_id}/cancel")
def cancel_loan(
    loan_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    Close a loan — no further instalments are deducted.

    It is NOT deleted: the instalments already taken must stay on record,
    or the figures on old slips could never be explained.
    """
    from app.models.payroll import EmployeeLoan

    ceo = get_user_or_404(db, current_user["user_id"])
    loan = db.query(EmployeeLoan).filter(
        EmployeeLoan.id == loan_id, EmployeeLoan.company_id == ceo.id
    ).first()
    if not loan:
        raise HTTPException(404, "Loan not found")

    loan.status = "cancelled"
    db.commit()
    return {"message": f"'{loan.title}' closed — no further instalments will be deducted"}
