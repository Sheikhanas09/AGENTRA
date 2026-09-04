"""
Payroll tables
─────────────────
Payroll consumes the OUTPUT of the attendance and leave modules and
produces the net salary. No new "truth" is created here — every figure
comes from upstream.

═══════════════════════════════════════════════════════════
MONEY IS NEVER A Float
═══════════════════════════════════════════════════════════
The rest of the system uses `Float` (min_daily_hours = 8.0 and so on)
because a few paisa either way mean nothing there. Not in payroll:

    >>> 0.1 + 0.2
    0.30000000000000004

A slip has 8-10 additions and subtractions. On floats each one drifts a
little, and by month end "earnings − deductions ≠ net" shows up — which
is impossible to explain to anyone.

So every money column is `Numeric(12, 2)` (Postgres DECIMAL) and arrives
in Python as a `Decimal`. Twelve digits, two decimals — up to
9,999,999,999.99, far more than PKR ever needs.

═══════════════════════════════════════════════════════════
A SLIP NEVER CHANGES ONCE IT IS BUILT
═══════════════════════════════════════════════════════════
The salary structure and payroll policy change over time. If a slip only
pointed at `salary_structures`, then six months later an old slip would
be rebuilt from NEW values — and would no longer match the original.

So every payslip carries three snapshots with it (JSON):
    attendance_snapshot  — that month's real figures
    salary_snapshot      — the salary structure at the time
    policy_snapshot      — the deduction rules at the time

The slip is therefore complete evidence in itself — not only the result,
but the reason for it.
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, JSON, Numeric,
    ForeignKey, Text, LargeBinary, UniqueConstraint, Index
)
from app.database import Base
from datetime import datetime


# One shape for every money column
MONEY = Numeric(12, 2)


# ══════════════════════════════════════════════
# Table 1: Company Branding
# ══════════════════════════════════════════════
class CompanyBranding(Base):
    """
    The company's face on a salary slip — logo, colour, address.

    A single PDF template fits every company; only these values differ.
    In a multi-company system each CEO supplies their own branding and the
    slip adapts to it on its own.
    """
    __tablename__ = "company_branding"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, unique=True)

    # ──── The logo lives in the DB, not on disk ────
    # Same reason as the attendance photos: keep the backup in one place,
    # and a file lost from disk must not leave the slip half-built.
    logo_data = Column(LargeBinary, nullable=True)
    logo_mime = Column(String, nullable=True)
    logo_filename = Column(String, nullable=True)

    primary_color = Column(String, default="#05DC7F")
    company_address = Column(Text, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    footer_text = Column(Text, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ══════════════════════════════════════════════
# Table 2: Salary Structure (per employee)
# ══════════════════════════════════════════════
class SalaryStructure(Base):
    """
    One employee's salary structure.

    `employee_id` is unique — one active structure per employee.
    The old values stay on record inside each payslip's `salary_snapshot`,
    so there is no need to keep history here.
    """
    __tablename__ = "salary_structures"
    __table_args__ = (
        UniqueConstraint("employee_id", name="uq_salary_employee"),
        Index("ix_salary_company", "company_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    company_id = Column(Integer, nullable=False)

    base_salary = Column(MONEY, nullable=False, default=0)
    house_allowance = Column(MONEY, nullable=False, default=0)
    transport_allowance = Column(MONEY, nullable=False, default=0)
    medical_allowance = Column(MONEY, nullable=False, default=0)
    other_allowances = Column(MONEY, nullable=False, default=0)

    currency = Column(String, default="PKR")
    effective_from = Column(Date, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ══════════════════════════════════════════════
# Table 3: Payroll Policy (company-wide)
# ══════════════════════════════════════════════
class PayrollPolicy(Base):
    """
    Deduction and overtime rules — one set for the whole company.

    Related to the attendance policy but deliberately kept separate:
      attendance.overtime_threshold  → HOW MANY minutes count as OT
      payroll.overtime_multiplier    → what those minutes are WORTH
    """
    __tablename__ = "payroll_policy"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, unique=True)

    # ──── Overtime ────
    overtime_multiplier = Column(Numeric(5, 2), nullable=False, default=1.5)

    # ──── Late arrival ────
    # per_occurrence = a fixed amount every time (one minute late and two
    #                  hours late both cost the same)
    # per_minute     = a fixed amount per late minute
    # pro_rata       = the minutes lost, paid at the employee's own hourly
    #                  rate — no amount has to be configured
    # none           = nothing is deducted
    late_deduction_policy = Column(String, default="pro_rata")
    late_deduction_amount = Column(MONEY, nullable=False, default=0)

    # ──── Short hours / unpaid leave ────
    # pro_rata = deduct in proportion to the hours missed | none = nothing
    undertime_deduction = Column(String, default="none")

    # ──── Absence without notice ────
    # Did not come in and did not apply for leave. DIFFERENT from unpaid
    # leave, where there was both notice and approval.
    # per_day = one day absent = one full day's pay
    # none    = nothing is deducted
    absent_deduction = Column(String, default="per_day")
    unpaid_leave_deduction = Column(String, default="pro_rata")

    # ──── Tax ────
    # Tax applies ONLY to the amount ABOVE the threshold — that is how
    # real income tax works. Charging it on the whole gross would mean
    # earning two rupees more near the threshold cost thousands.
    tax_percentage = Column(Numeric(5, 2), nullable=False, default=0)
    tax_threshold = Column(MONEY, nullable=False, default=0)

    # ──── Provident fund ────
    # Charged on the base salary, not on gross (the usual practice)
    provident_fund_percent = Column(Numeric(5, 2), nullable=False, default=0)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ══════════════════════════════════════════════
# Table 4: Payroll Run (the monthly batch)
# ══════════════════════════════════════════════
class PayrollRun(Base):
    """
    One month's payroll batch.

    UNIQUE on `(company_id, period)` — a month's payroll cannot run twice.
    Otherwise two slips would exist and the question of which one to trust
    would be open.

    To run it again the previous run must be marked `cancelled` — annulled,
    not erased. The record still remains.
    """
    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("company_id", "period", "attempt",
                         name="uq_run_company_period_attempt"),
        Index("ix_run_company_period", "company_id", "period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)
    period = Column(String, nullable=False)          # "2026-05"

    # A retry of the same period — the attempt increases once the old one is cancelled
    attempt = Column(Integer, nullable=False, default=1)

    triggered_by = Column(String, default="ceo")     # ceo | scheduler
    triggered_by_user = Column(Integer, ForeignKey("users.id"), nullable=True)

    # processing → pending_approval → completed
    #            ↘ failed / cancelled
    status = Column(String, default="processing")

    employees_total = Column(Integer, default=0)
    employees_done = Column(Integer, default=0)
    employees_failed = Column(Integer, default=0)

    total_gross = Column(MONEY, nullable=False, default=0)
    total_deductions = Column(MONEY, nullable=False, default=0)
    total_payroll_cost = Column(MONEY, nullable=False, default=0)

    error_note = Column(Text, nullable=True)

    run_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ══════════════════════════════════════════════
# Table 5: Payslip (per employee, per month)
# ══════════════════════════════════════════════
class Payslip(Base):
    """
    One employee's slip for one month.

    This is the most SENSITIVE record in the system — employee A must
    never see B's slip. An ownership check is mandatory on every route
    (a `company_id` filter is NOT enough — it exposes the whole company).
    """
    __tablename__ = "payslips"
    __table_args__ = (
        UniqueConstraint("run_id", "employee_id", name="uq_slip_run_employee"),
        Index("ix_slip_employee_period", "employee_id", "period"),
        Index("ix_slip_company_period", "company_id", "period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("payroll_runs.id", ondelete="CASCADE"),
                    nullable=False)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    company_id = Column(Integer, nullable=False)
    period = Column(String, nullable=False)

    # ──── Earnings ────
    base_salary = Column(MONEY, nullable=False, default=0)
    allowances_total = Column(MONEY, nullable=False, default=0)
    overtime_pay = Column(MONEY, nullable=False, default=0)
    bonus = Column(MONEY, nullable=False, default=0)
    # Items that change every month — the CEO enters them before the run
    incentive_pay = Column(MONEY, nullable=False, default=0)
    arrears = Column(MONEY, nullable=False, default=0)
    commission = Column(MONEY, nullable=False, default=0)
    other_earnings = Column(MONEY, nullable=False, default=0)
    gross_pay = Column(MONEY, nullable=False, default=0)

    # ──── Deductions ────
    late_deduction = Column(MONEY, nullable=False, default=0)
    undertime_deduction = Column(MONEY, nullable=False, default=0)
    unpaid_leave_deduction = Column(MONEY, nullable=False, default=0)
    absent_deduction = Column(MONEY, nullable=False, default=0)
    tax_deduction = Column(MONEY, nullable=False, default=0)
    provident_fund = Column(MONEY, nullable=False, default=0)
    loan_deduction = Column(MONEY, nullable=False, default=0)
    other_deductions = Column(MONEY, nullable=False, default=0)
    total_deductions = Column(MONEY, nullable=False, default=0)

    net_salary = Column(MONEY, nullable=False, default=0)
    currency = Column(String, default="PKR")

    # ──── Three snapshots — the slip is complete evidence ────
    # Not only the result but the REASON for it. Even if the salary
    # structure or policy changes later, an old slip still tells its own story.
    attendance_snapshot = Column(JSON, nullable=True)
    salary_snapshot = Column(JSON, nullable=True)
    policy_snapshot = Column(JSON, nullable=True)

    # Every step of the arithmetic — the answer to "where did this come from"
    calculation_notes = Column(JSON, nullable=True)

    # ──── PDF ────
    slip_pdf = Column(LargeBinary, nullable=True)
    pdf_sha256 = Column(String, nullable=True)

    # computed → sent | failed
    status = Column(String, default="computed")
    email_sent_at = Column(DateTime, nullable=True)
    error_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════
# Table 6: Payroll Adjustments (the one-off items each month)
# ══════════════════════════════════════════════
class PayrollAdjustment(Base):
    """
    One employee's one-off items for a single MONTH — incentive, arrears,
    bonus, commission, or a one-time deduction.

    ═══ WHY THIS IS NOT IN THE SALARY STRUCTURE ═══
    The salary structure is FIXED: base and allowances, the same every
    month. An incentive changes monthly, arrears come once, a bonus
    arrives at Eid.

    Putting all of that in the structure would force the CEO to edit it
    every month — and would change the figures on old slips too (the
    structure is a single row and keeps no history).

    Hence a separate table: each row is one item for one month. The
    structure stays fixed, and one month's bonus never affects another.
    """
    __tablename__ = "payroll_adjustments"
    __table_args__ = (
        Index("ix_adj_employee_period", "employee_id", "period"),
        Index("ix_adj_company_period", "company_id", "period"),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    period = Column(String, nullable=False)          # "2026-05"

    # incentive | arrears | bonus | commission | other_earning
    # advance   | penalty | other_deduction
    kind = Column(String, nullable=False)

    # Always POSITIVE. Whether it is an earning or a deduction is decided
    # by `kind`, never by a negative number. Otherwise "a deduction of
    # -5000" inverts and could be added by mistake.
    amount = Column(MONEY, nullable=False, default=0)

    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ══════════════════════════════════════════════
# Table 7: Employee Loans
# ══════════════════════════════════════════════
class EmployeeLoan(Base):
    """
    A loan or advance deducted as a monthly instalment.

    ═══ THE BALANCE IS NOT STORED HERE ═══
    A `remaining` column looks easy — but payroll can be rerun (the CEO
    fixed a salary and forced a run). Decrementing a counter on every run
    would deduct twice on a rerun and leave the balance permanently wrong.

    So the outstanding amount is DERIVED:
        remaining = principal − (the instalments actually taken)

    And those instalments live in `loan_repayments`. Cancel a run and its
    instalment row disappears — so the balance corrects itself. No counter
    can drift.
    """
    __tablename__ = "employee_loans"
    __table_args__ = (
        Index("ix_loan_employee", "employee_id"),
        Index("ix_loan_company", "company_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)
    employee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)

    title = Column(String, nullable=False)           # "Bike advance"
    principal = Column(MONEY, nullable=False, default=0)
    installment = Column(MONEY, nullable=False, default=0)

    # Deductions start from this month — earlier payrolls are unaffected
    start_period = Column(String, nullable=False)    # "2026-05"

    # active | cleared | cancelled
    # `cleared` is applied automatically once the balance reaches zero
    status = Column(String, nullable=False, default="active")

    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ══════════════════════════════════════════════
# Table 8: Loan Repayments
# ══════════════════════════════════════════════
class LoanRepayment(Base):
    """
    One month's instalment on one loan that was ACTUALLY taken.

    `(loan_id, run_id)` is unique — one run can take only one instalment
    per loan. And cancelling a run removes this row, so the outstanding
    amount corrects itself immediately.
    """
    __tablename__ = "loan_repayments"
    __table_args__ = (
        UniqueConstraint("loan_id", "run_id", name="uq_repay_loan_run"),
        Index("ix_repay_loan", "loan_id"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # ══════════════════════════════════════════════
    # The tenant column
    # ══════════════════════════════════════════════
    # This table reached its company only through its parent row. The
    # routes do look the parent up first and that lookup IS scoped, so
    # there was no known way in — but that is a fact about today's
    # routes. A table without `company_id` is one NEITHER wall can
    # protect: the ORM guard skips it, and no row-level-security policy
    # can be written for it.
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )

    loan_id = Column(Integer, ForeignKey("employee_loans.id", ondelete="CASCADE"),
                     nullable=False)
    run_id = Column(Integer, ForeignKey("payroll_runs.id", ondelete="CASCADE"),
                    nullable=False)
    payslip_id = Column(Integer, ForeignKey("payslips.id", ondelete="CASCADE"),
                        nullable=True)
    period = Column(String, nullable=False)
    amount = Column(MONEY, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
