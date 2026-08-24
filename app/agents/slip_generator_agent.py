"""
Slip Generator Agent — LangGraph, 3 nodes, ZERO LLM calls
─────────────────────────────────────────────────────────

    branding  →  render  →  store

Like the Payroll Agent, there is no LLM here — building a PDF is entirely
deterministic. The same payslip must always produce the same PDF.

═══════════════════════════════════════════════════════════
PDF SEEDHA, MCP KE BAGAIR
═══════════════════════════════════════════════════════════
The plan called for the PDF to be an MCP tool. But MCP is for things that
are REALLY external — a Google Meet link, an email through Gmail.
Building a PDF is work inside our own process; putting MCP in the middle
would add one more thing that can go down, and make testing harder.

The email does go through MCP in Chunk 5 — Gmail genuinely is external.

═══════════════════════════════════════════════════════════
A FAILED PDF NEVER COSTS THE SALARY
═══════════════════════════════════════════════════════════
The slip's real record is the DB row — the figures, the snapshots, the
calculation steps. The PDF is only its face. So a failed PDF leaves the
payslip `computed` (the money is safe); the reason goes into `error_note`
and the CEO can rebuild it.
"""

import hashlib
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END

from app.models.payroll import CompanyBranding, Payslip
from app.models.user import User
from app.utils.payroll_data import month_label
from app.utils.payslip_pdf import build_payslip_pdf


class SlipState(TypedDict, total=False):
    db: object
    payslip_id: int
    company_id: int

    branding: dict
    pdf_bytes: Optional[bytes]

    status: str        # generated | failed
    error: str


# ══════════════════════════════════════════════
# Node 1: Branding
# ══════════════════════════════════════════════
def branding_node(state: SlipState) -> SlipState:
    """
    The company's logo, colour and address.

    With no branding the slip is still produced — the default colour and
    just the company name. A CEO not supplying a logo is no reason for an
    employee to go without a slip.
    """
    db = state["db"]
    try:
        row = db.query(CompanyBranding).filter(
            CompanyBranding.company_id == state["company_id"]
        ).first()

        ceo = db.query(User).filter(User.id == state["company_id"]).first()

        return {
            **state,
            "branding": {
                "name": (getattr(ceo, "company_name", None)
                         or getattr(ceo, "full_name", None) or "Company"),
                "address": row.company_address if row else None,
                "email": row.contact_email if row else None,
                "phone": row.contact_phone if row else None,
                "footer": row.footer_text if row else None,
                "color": row.primary_color if row else None,
                "logo_bytes": row.logo_data if row else None,
            },
            "error": "",
        }
    except Exception as e:
        return {**state, "status": "failed", "error": f"Branding load: {e}"}


# ══════════════════════════════════════════════
# Node 2: Render
# ══════════════════════════════════════════════
def render_node(state: SlipState) -> SlipState:
    """Payslip row → PDF bytes"""
    if state.get("status") == "failed":
        return state

    db = state["db"]
    try:
        slip = db.query(Payslip).filter(Payslip.id == state["payslip_id"]).first()
        if not slip:
            return {**state, "status": "failed", "error": "Payslip not found"}

        emp = db.query(User).filter(User.id == slip.employee_id).first()
        att = slip.attendance_snapshot or {}
        notes = slip.calculation_notes or {}

        # ──── Only lines that actually have a value ────
        # Showing zero rows makes the slip look crowded and buries what
        # matters. Printing "Overtime 0.00" gains nothing.
        earnings = [("Basic Salary", slip.base_salary)]
        sal = slip.salary_snapshot or {}
        for label, key in (("House Allowance", "house_allowance"),
                           ("Transport Allowance", "transport_allowance"),
                           ("Medical Allowance", "medical_allowance"),
                           ("Other Allowances", "other_allowances")):
            val = sal.get(key)
            if val and float(val) > 0:
                earnings.append((label, val))
        if slip.overtime_pay and slip.overtime_pay > 0:
            ot_h = round((att.get("overtime_minutes") or 0) / 60, 1)
            earnings.append((f"Overtime ({ot_h} h)", slip.overtime_pay))

        # That month's one-off items — only those with a value
        for label, val in (("Incentive Pay", slip.incentive_pay),
                           ("Arrears", slip.arrears),
                           ("Bonus", slip.bonus),
                           ("Commission", slip.commission),
                           ("Other Earnings", slip.other_earnings)):
            if val and val > 0:
                earnings.append((label, val))

        deductions = []
        if slip.late_deduction and slip.late_deduction > 0:
            deductions.append(
                (f"Late Arrival ({att.get('late_count', 0)}x)", slip.late_deduction))
        if slip.undertime_deduction and slip.undertime_deduction > 0:
            ut_h = round((att.get("undertime_minutes") or 0) / 60, 1)
            deductions.append((f"Short Hours ({ut_h} h)", slip.undertime_deduction))
        if slip.unpaid_leave_deduction and slip.unpaid_leave_deduction > 0:
            deductions.append(
                (f"Unpaid Leave ({att.get('unpaid_leave_days', 0)} d)",
                 slip.unpaid_leave_deduction))
        if slip.absent_deduction and slip.absent_deduction > 0:
            deductions.append(
                (f"Absent ({att.get('absent_days', 0)} d)",
                 slip.absent_deduction))
        if slip.tax_deduction and slip.tax_deduction > 0:
            pct = (slip.policy_snapshot or {}).get("tax_percentage", "")
            deductions.append((f"Income Tax ({pct}%)", slip.tax_deduction))
        if slip.provident_fund and slip.provident_fund > 0:
            pct = (slip.policy_snapshot or {}).get("provident_fund_percent", "")
            deductions.append((f"Provident Fund ({pct}%)", slip.provident_fund))

        # ──── Loan — the outstanding amount is printed too ────
        # An employee should not only see how much was taken, but also
        # how much is STILL owed
        if slip.loan_deduction and slip.loan_deduction > 0:
            loans = (slip.attendance_snapshot or {}).get("loans") or []
            if len(loans) == 1:
                label = f"Loan — {loans[0].get('title', 'installment')}"
                rem = loans[0].get("remaining_after")
                if rem is not None:
                    label += f" ({float(rem):,.0f} remaining)"
            else:
                label = f"Loan / Advance ({len(loans)})" if loans else "Loan / Advance"
            deductions.append((label, slip.loan_deduction))

        if slip.other_deductions and slip.other_deductions > 0:
            deductions.append(("Other Deductions", slip.other_deductions))

        if not deductions:
            deductions.append(("No deductions", 0))

        pdf = build_payslip_pdf({
            "company": state["branding"],
            "employee": {
                "name": getattr(emp, "full_name", None) or "Employee",
                "employee_id": slip.employee_id,
                "department": getattr(emp, "department", None),
                "designation": getattr(emp, "designation", None)
                               or getattr(emp, "department", None),
            },
            "period": month_label(slip.period),
            "attendance": {
                "working_days": att.get("working_days_in_month"),
                "present_days": att.get("present_days"),
                "net_hours": att.get("total_net_hours", 0),
                "overtime_hours": round((att.get("overtime_minutes") or 0) / 60, 1),
                "late_count": att.get("late_count", 0),
                "paid_leave_days": att.get("paid_leave_days", 0),
                "unpaid_leave_days": att.get("unpaid_leave_days", 0),
            },
            "earnings": earnings,
            "deductions": deductions,
            "totals": {
                "gross": slip.gross_pay,
                "deductions": slip.total_deductions,
                "net": slip.net_salary,
            },
            "currency": slip.currency or "PKR",
            "generated": str(slip.created_at)[:10] if slip.created_at else None,
            "warnings": notes.get("warnings") or [],
        })

        return {**state, "pdf_bytes": pdf, "error": ""}

    except Exception as e:
        return {**state, "status": "failed", "error": f"While building the PDF: {e}"}


# ══════════════════════════════════════════════
# Node 3: Store
# ══════════════════════════════════════════════
def store_node(state: SlipState) -> SlipState:
    """
    The PDF into the DB (BYTEA) + a sha256.

    The hash exists so it can later be verified that the PDF an employee
    downloaded is the one that was built — that nothing altered it.
    """
    if state.get("status") == "failed":
        return state

    db = state["db"]
    try:
        slip = db.query(Payslip).filter(Payslip.id == state["payslip_id"]).first()
        if not slip:
            return {**state, "status": "failed", "error": "Payslip not found"}

        pdf = state.get("pdf_bytes")
        if not pdf:
            return {**state, "status": "failed", "error": "The PDF is empty"}

        slip.slip_pdf = pdf
        slip.pdf_sha256 = hashlib.sha256(pdf).hexdigest()
        slip.error_note = None
        db.flush()

        return {**state, "status": "generated", "error": ""}

    except Exception as e:
        db.rollback()
        return {**state, "status": "failed", "error": f"While saving the PDF: {e}"}


# ══════════════════════════════════════════════
# Graph
# ══════════════════════════════════════════════
def build_slip_graph():
    graph = StateGraph(SlipState)
    graph.add_node("branding", branding_node)
    graph.add_node("render", render_node)
    graph.add_node("store", store_node)

    graph.set_entry_point("branding")
    graph.add_edge("branding", "render")
    graph.add_edge("render", "store")
    graph.add_edge("store", END)
    return graph.compile()


slip_graph = build_slip_graph()


def generate_slip_pdf(db, payslip_id: int, company_id: int) -> dict:
    """
    Build the PDF for one payslip.

    Never raises — if the PDF fails the payslip's money is still safe,
    only `slip_pdf` stays empty.
    """
    out = slip_graph.invoke({
        "db": db,
        "payslip_id": payslip_id,
        "company_id": company_id,
        "status": "",
        "error": "",
    })

    pdf = out.get("pdf_bytes")
    return {
        "payslip_id": payslip_id,
        "status": out.get("status") or "failed",
        "error": out.get("error", ""),
        "size_kb": round(len(pdf) / 1024, 1) if pdf else 0,
    }
