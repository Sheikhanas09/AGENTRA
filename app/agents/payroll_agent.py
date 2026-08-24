"""
Payroll Agent — LangGraph, 4 nodes, ZERO LLM calls
──────────────────────────────────────────────────

    aggregate  →  compute  →  save  →  event

═══════════════════════════════════════════════════════════
WHY THERE IS NO LLM HERE
═══════════════════════════════════════════════════════════
The other agents (Leave, Policy Extraction) use an LLM because their
questions can only be answered from written words — "what does the policy
say", "when is the shift in this document". A little variation is
acceptable there, and every decision still goes to a human.

Salary is not that kind of question. If the same employee's payroll for
the same month runs twice and produces two different figures, trust in
the system is gone. So every node here does nothing but arithmetic:

    aggregate → counts from attendance/leave (SQL)
    compute   → Decimal arithmetic (payroll_calc.py)
    save      → DB row + teen snapshot
    event     → the run counters, and the signal to build the slip

═══════════════════════════════════════════════════════════
SO WHY LANGGRAPH?
═══════════════════════════════════════════════════════════
Two reasons:

1. **Each node is testable on its own.** State in, state out — every
   intermediate step can be inspected. That is hard inside one long
   function.

2. **One employee's error does not bring down the run.** Each node puts
   its error into the state's `error`, the graph moves on, and that one
   employee's slip is marked "failed" while the other 50 carry on.

So LangGraph is here for orchestration, not for AI — and that should be
stated plainly.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END

from app.models.payroll import Payslip
from app.utils.payroll_calc import compute_payroll
from app.utils.payroll_data import gather_inputs, MissingSetup


class PayrollState(TypedDict, total=False):
    # ──── Inputs ────
    db: object            # SQLAlchemy Session
    employee_id: int
    company_id: int
    period: str
    run_id: int

    # ──── Passed between nodes ────
    salary: object
    policy: object
    work: object
    snapshots: dict
    loan_plan: list
    result: object
    payslip_id: Optional[int]
    pdf_status: str
    pdf_error: str

    # ──── Natija ────
    status: str           # computed | failed
    error: str


# ══════════════════════════════════════════════
# Node 1: Aggregate
# ══════════════════════════════════════════════
def aggregate_node(state: PayrollState) -> PayrollState:
    """
    Attendance + leave + salary + policy — gather it all.

    This is the node where the rule "payroll invents no data" is enforced:
    every figure comes from another module.
    """
    try:
        salary, policy, work, snapshots, loan_plan = gather_inputs(
            state["db"], state["employee_id"], state["company_id"],
            state["period"], state["run_id"],
        )
        return {
            **state,
            "salary": salary,
            "policy": policy,
            "work": work,
            "snapshots": snapshots,
            "loan_plan": loan_plan,
            "error": "",
        }

    except MissingSetup as e:
        # The CEO did not finish the setup — this employee is skipped but
        # the rest of the payroll carries on
        return {**state, "status": "failed", "error": str(e)}

    except Exception as e:
        return {**state, "status": "failed", "error": f"While gathering data: {e}"}


# ══════════════════════════════════════════════
# Node 2: Compute
# ══════════════════════════════════════════════
def compute_node(state: PayrollState) -> PayrollState:
    """Arithmetic only — `payroll_calc.py`, which knows nothing about the DB"""
    if state.get("status") == "failed":
        return state

    try:
        result = compute_payroll(state["salary"], state["policy"], state["work"])
        return {**state, "result": result, "error": ""}
    except Exception as e:
        return {**state, "status": "failed", "error": f"During the calculation: {e}"}


# ══════════════════════════════════════════════
# Node 3: Save
# ══════════════════════════════════════════════
def save_node(state: PayrollState) -> PayrollState:
    """
    Create the payslip row — with its three snapshots.

    A rerun does not create a NEW row: the existing row for that run is
    updated. The unique constraint on `(run_id, employee_id)` enforces the
    same thing at the DB level.
    """
    if state.get("status") == "failed":
        return state

    db = state["db"]
    r = state["result"]

    try:
        slip = db.query(Payslip).filter(
            Payslip.run_id == state["run_id"],
            Payslip.employee_id == state["employee_id"],
        ).first()

        if not slip:
            slip = Payslip(
                run_id=state["run_id"],
                employee_id=state["employee_id"],
                company_id=state["company_id"],
                period=state["period"],
            )
            db.add(slip)

        slip.base_salary = r.base_salary
        slip.allowances_total = r.allowances_total
        slip.overtime_pay = r.overtime_pay
        slip.bonus = r.bonus
        slip.incentive_pay = r.incentive_pay
        slip.arrears = r.arrears
        slip.commission = r.commission
        slip.other_earnings = r.other_earnings
        slip.gross_pay = r.gross_pay

        slip.late_deduction = r.late_deduction
        slip.undertime_deduction = r.undertime_deduction
        slip.unpaid_leave_deduction = r.unpaid_leave_deduction
        slip.absent_deduction = r.absent_deduction
        slip.tax_deduction = r.tax_deduction
        slip.provident_fund = r.provident_fund
        slip.loan_deduction = r.loan_deduction
        slip.other_deductions = r.other_deductions
        slip.total_deductions = r.total_deductions
        slip.net_salary = r.net_salary

        snaps = state.get("snapshots") or {}
        # Adjustments and loans go into the attendance snapshot too — all
        # the evidence stays in one place inside the slip
        attendance_snap = dict(snaps.get("attendance") or {})
        attendance_snap["adjustments"] = snaps.get("adjustments") or []
        attendance_snap["loans"] = snaps.get("loans") or []
        slip.attendance_snapshot = attendance_snap
        slip.salary_snapshot = snaps.get("salary")
        slip.policy_snapshot = snaps.get("policy")
        slip.currency = (snaps.get("salary") or {}).get("currency", "PKR")

        # Every step of the arithmetic — the answer to "where did this come from"
        slip.calculation_notes = {
            "rates": {
                "working_days": r.working_days,
                "daily_rate": str(r.daily_rate),
                "hourly_rate": str(r.hourly_rate),
            },
            "steps": r.notes,
            "warnings": r.warnings,
        }

        slip.status = "computed"
        slip.error_note = None
        db.flush()

        _record_loan_repayments(db, state, slip)

        return {**state, "payslip_id": slip.id, "status": "computed", "error": ""}

    except Exception as e:
        db.rollback()
        return {**state, "status": "failed", "error": f"While saving: {e}"}


# ══════════════════════════════════════════════
# Node 4: Event
# ══════════════════════════════════════════════
def event_node(state: PayrollState) -> PayrollState:
    """
    Salary ban chuki — ab Slip Generator ko PDF banane do.

    ═══ A FAILED PDF NEVER COSTS THE SALARY ═══
    The slip's real record is the DB row (figures + snapshots + the
    calculation steps). The PDF is only its face. So if the PDF fails the
    status stays `computed` — the money is safe — and the reason is
    written into `error_note`. The CEO can rebuild it later.
    """
    if state.get("status") == "failed" or not state.get("payslip_id"):
        return state

    try:
        from app.agents.slip_generator_agent import generate_slip_pdf

        out = generate_slip_pdf(
            state["db"], state["payslip_id"], state["company_id"]
        )

        if out["status"] != "generated":
            slip = state["db"].query(Payslip).filter(
                Payslip.id == state["payslip_id"]
            ).first()
            if slip:
                slip.error_note = f"PDF could not be produced: {out.get('error')}"

        return {**state, "pdf_status": out["status"], "status": "computed"}

    except Exception as e:
        # A PDF failure never brings the salary down
        return {**state, "pdf_status": "failed", "pdf_error": str(e),
                "status": "computed"}


# ══════════════════════════════════════════════
# Graph
# ══════════════════════════════════════════════
def build_payroll_graph():
    graph = StateGraph(PayrollState)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("compute", compute_node)
    graph.add_node("save", save_node)
    graph.add_node("event", event_node)

    graph.set_entry_point("aggregate")
    graph.add_edge("aggregate", "compute")
    graph.add_edge("compute", "save")
    graph.add_edge("save", "event")
    graph.add_edge("event", END)
    return graph.compile()


payroll_graph = build_payroll_graph()


def run_for_employee(db, employee_id: int, company_id: int,
                     period: str, run_id: int) -> dict:
    """
    One employee's payroll for one month.

    Never raises — an error comes back as `status: failed`, so one
    employee can never stop the whole run.
    """
    out = payroll_graph.invoke({
        "db": db,
        "employee_id": employee_id,
        "company_id": company_id,
        "period": period,
        "run_id": run_id,
        "status": "",
        "error": "",
    })

    result = out.get("result")
    return {
        "employee_id": employee_id,
        "status": out.get("status") or "failed",
        "error": out.get("error", ""),
        "payslip_id": out.get("payslip_id"),
        "net_salary": result.net_salary if result else Decimal("0.00"),
        "gross_pay": result.gross_pay if result else Decimal("0.00"),
        "total_deductions": result.total_deductions if result else Decimal("0.00"),
        "warnings": result.warnings if result else [],
        "pdf_status": out.get("pdf_status", ""),
    }


# ══════════════════════════════════════════════
# Recording the loan instalment
# ══════════════════════════════════════════════
def _record_loan_repayments(db, state, slip):
    """
    The instalment taken on this slip is recorded in `loan_repayments`.

    ═══ A RERUN NEVER DEDUCTS TWICE ═══
    There is a unique constraint on `(loan_id, run_id)`. A forced rerun
    keeps the same run id, so the existing row is UPDATED rather than
    added. That is why the outstanding amount always stays correct.

    And the balance is stored nowhere — it is the sum of these rows.
    Cancel a run and the rows disappear, so the balance restores itself.
    """
    from app.models.payroll import LoanRepayment

    for item in (state.get("loan_plan") or []):
        loan = item["loan"]
        amount = item["amount"]

        row = db.query(LoanRepayment).filter(
            LoanRepayment.loan_id == loan.id,
            LoanRepayment.run_id == state["run_id"],
        ).first()

        if row:
            row.amount = amount
            row.payslip_id = slip.id
            row.period = state["period"]
        else:
            db.add(LoanRepayment(
                loan_id=loan.id,
                run_id=state["run_id"],
                payslip_id=slip.id,
                period=state["period"],
                amount=amount,
            ))

        # Balance cleared? Then close the loan — nothing is taken next time
        if item["remaining_after"] <= 0:
            loan.status = "cleared"

    db.flush()
