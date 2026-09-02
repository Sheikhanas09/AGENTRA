"""
Gathering the data payroll needs
───────────────────────────────
This is where the rule "payroll INVENTS no figure of its own" is actually
enforced. Every number has a source:

    present_days, overtime, undertime, late  →  attendance_sessions
    paid / unpaid leave days                 →  leave_requests + type config
    working_days_in_month                    →  company_work_policy
    base salary, allowances                  →  salary_structures
    deduction rules                          →  payroll_policy

No arithmetic happens here — only counting. The arithmetic is done by
`payroll_calc.py`, which knows nothing about the DB. Two jobs, two files.
"""

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.attendance import (
    AttendanceSession, CompanyWorkPolicy, CompanyLeaveType,
    LeaveRequest, LeaveStatusEnum,
)
from app.models.payroll import SalaryStructure, PayrollPolicy
from app.models.user import User
from app.utils.payroll_calc import (
    SalaryInputs, PolicyInputs, WorkInputs, d,
)
from app.utils.pkt import get_pkt_now
from app.utils.workpolicy import count_working_days, is_working_day


def parse_period(period: str) -> Tuple[date, date]:
    """
    "2026-05" → (2026-05-01, 2026-05-31)

    A clear error on a bad format — the period decides the whole month,
    and a wrong one could quietly produce a wrong salary.
    """
    try:
        year_s, month_s = str(period).strip().split("-")
        year, month = int(year_s), int(month_s)
    except (ValueError, AttributeError):
        raise ValueError("Period must look like 'YYYY-MM' (e.g. 2026-05)")

    if not (2000 <= year <= 2100) or not (1 <= month <= 12):
        raise ValueError(f"Period '{period}' does not look valid")

    last_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def month_label(period: str) -> str:
    """"2026-05" → "May 2026" — for printing on the slip"""
    start, _ = parse_period(period)
    return start.strftime("%B %Y")


# ══════════════════════════════════════════════
# Counting attendance
# ══════════════════════════════════════════════
def attendance_totals(db: Session, employee_id: int, start: date, end: date,
                      policy=None) -> dict:
    """
    A straight count from that month's attendance sessions.

    ═══ TWO SEPARATE LATE FIGURES ═══
    Attendance counts `late_by_minutes` from the shift START — the grace
    minutes are included. That is right for attendance
    ("they arrived at 9:20, so 20 minutes after").

    But MONEY cannot be charged on it. A 15-minute grace means precisely
    that the company FORGAVE those 15 minutes — charging for them
    would make the grace period meaningless.

      late_minutes           = minutes AFTER grace  → the deduction uses this
      late_minutes_from_shift = full, from shift start → display only

    The subtraction happens PER SESSION, never after summing — otherwise
    one day's spare time merges with another and the figures
    come out wrong.
    """
    sessions = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.date >= start,
        AttendanceSession.date <= end,
    ).all()

    completed = [s for s in sessions if s.check_out_time is not None]
    late = [s for s in sessions if s.is_late]

    grace = int(getattr(policy, "late_tolerance_mins", 0) or 0)

    return {
        "present_days": len(sessions),
        "completed_days": len(completed),
        "total_net_hours": round(sum(s.net_hours or 0 for s in sessions), 2),
        "overtime_minutes": sum(s.overtime_minutes or 0 for s in sessions),
        "undertime_minutes": sum(s.undertime_minutes or 0 for s in sessions),
        "late_count": len(late),
        "late_minutes": sum(
            max(0, (s.late_by_minutes or 0) - grace) for s in late
        ),
        "late_minutes_from_shift": sum(s.late_by_minutes or 0 for s in late),
        "late_grace_mins": grace,
        "early_checkout_count": len([s for s in sessions if s.is_early_checkout]),
    }


# ══════════════════════════════════════════════
# Counting leave
# ══════════════════════════════════════════════
def leave_totals(db: Session, employee_id: int, company_id: int,
                 policy, start: date, end: date) -> dict:
    """
    Days of approved leave — paid and unpaid counted separately.

    ═══ DO NAZUK BAATEIN ═══

    1. **Only this month's portion counts.** A request may run from 28
       April to 3 May. `deductible_days` is the whole request's total —
       May's payroll only needs 1–3 May. So each day is counted separately.

    2. **Working days only.** If Sat/Sun fall inside the leave they cost
       no balance either, and should cost no salary — they were off anyway.
    """
    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.company_id == company_id,
        LeaveRequest.status == LeaveStatusEnum.approved,
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    ).all()

    # Which types are paid — the company's own decision
    paid_map = {
        t.code: bool(t.is_paid)
        for t in db.query(CompanyLeaveType).filter(
            CompanyLeaveType.company_id == company_id
        ).all()
    }

    paid_days = 0
    unpaid_days = 0
    breakdown = {}

    for lv in leaves:
        # This month's portion
        first = max(lv.start_date, start)
        last = min(lv.end_date, end)

        days = 0
        cur = first
        while cur <= last:
            if is_working_day(policy, cur):
                days += 1
            cur = date.fromordinal(cur.toordinal() + 1)

        if not days:
            continue

        code = str(getattr(lv.leave_type, "value", lv.leave_type))
        # With no type config we assume PAID — docking someone's salary
        # merely because the config is missing would be wrong
        is_paid = paid_map.get(code, True)

        if is_paid:
            paid_days += days
        else:
            unpaid_days += days

        entry = breakdown.setdefault(code, {"days": 0, "is_paid": is_paid})
        entry["days"] += days

    return {
        "paid_leave_days": paid_days,
        "unpaid_leave_days": unpaid_days,
        "by_type": breakdown,
    }


# ══════════════════════════════════════════════
# Absence
# ══════════════════════════════════════════════
def absent_days(db: Session, employee_id: int, company_id: int, policy,
                start: date, end: date, today: date = None) -> dict:
    """
    Working days on which the person neither turned up NOR had approved
    leave — that is, absent without notice.

    ═══ WHY IT IS NOT COUNTED BY SUBTRACTION ═══
    It looks easy: working_days − present − leave. But it is wrong:

      · `present_days` counts ALL sessions, not only those on working
        days. One Sunday check-in and the subtraction hides an absence —
        or turns the number negative.
      · A day with BOTH a session AND leave (leave applied, then they came
        in anyway) gets subtracted twice.

    So each day is examined on its own: is it a working day? is there a
    session on it? is there leave on it? All three, day by day.

    ═══ FUTURE DAYS ARE NOT COUNTED AS ABSENT ═══
    If payroll runs mid-month, the rest of the month has not happened yet.
    Counting those days as absent would be unfair. So the count stops at
    `today`. A month already past is unaffected — every one of its days is
    before `today` anyway.
    """
    if today is None:
        today = get_pkt_now().date()

    last = min(end, today)

    if last < start:
        return {"absent_days": 0, "counted_until": None, "dates": []}

    # Which days have attendance
    present = {
        row[0] for row in db.query(AttendanceSession.date).filter(
            AttendanceSession.employee_id == employee_id,
            AttendanceSession.date >= start,
            AttendanceSession.date <= last,
        ).all()
    }

    # Which days had approved leave (paid or unpaid — either way the
    # person was not "missing", they gave notice)
    on_leave = set()
    for lv in db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.company_id == company_id,
        LeaveRequest.status == LeaveStatusEnum.approved,
        LeaveRequest.start_date <= last,
        LeaveRequest.end_date >= start,
    ).all():
        cur = max(lv.start_date, start)
        stop = min(lv.end_date, last)
        while cur <= stop:
            on_leave.add(cur)
            cur = date.fromordinal(cur.toordinal() + 1)

    missing = []
    cur = start
    while cur <= last:
        if is_working_day(policy, cur) and cur not in present and cur not in on_leave:
            missing.append(cur)
        cur = date.fromordinal(cur.toordinal() + 1)

    return {
        "absent_days": len(missing),
        "counted_until": str(last),
        "dates": [str(d) for d in missing],
    }


# ══════════════════════════════════════════════
# Everything in one place
# ══════════════════════════════════════════════
class MissingSetup(Exception):
    """No salary structure or policy is set — payroll cannot be produced"""


def gather_inputs(db: Session, employee_id: int, company_id: int, period: str,
                  run_id: int = None):
    """
    The complete inputs for one employee.

    Return: (SalaryInputs, PolicyInputs, WorkInputs, snapshots: dict)

    `snapshots` are the three JSON blobs stored with the payslip — so the
    slip can still tell its own story later.
    """
    start, end = parse_period(period)

    # ──── Salary structure ────
    structure = db.query(SalaryStructure).filter(
        SalaryStructure.employee_id == employee_id,
        SalaryStructure.company_id == company_id,
    ).first()

    if not structure:
        raise MissingSetup("No salary structure has been set for this employee")

    if not structure.base_salary or structure.base_salary <= 0:
        raise MissingSetup("Base salary is zero — set the salary structure first")

    salary = SalaryInputs(
        base_salary=d(structure.base_salary),
        house_allowance=d(structure.house_allowance),
        transport_allowance=d(structure.transport_allowance),
        medical_allowance=d(structure.medical_allowance),
        other_allowances=d(structure.other_allowances),
    )

    # ──── Payroll policy ────
    # If there is none, everything is zero — only the fixed salary is paid,
    # with no deductions. That is the safe default: when in doubt, decide
    # in the employee's favour.
    pp = db.query(PayrollPolicy).filter(
        PayrollPolicy.company_id == company_id
    ).first()

    policy_in = PolicyInputs(
        overtime_multiplier=d(pp.overtime_multiplier) if pp else Decimal("1.5"),
        late_deduction_policy=pp.late_deduction_policy if pp else "none",
        late_deduction_amount=d(pp.late_deduction_amount) if pp else Decimal("0"),
        undertime_deduction=pp.undertime_deduction if pp else "none",
        unpaid_leave_deduction=pp.unpaid_leave_deduction if pp else "pro_rata",
        absent_deduction=pp.absent_deduction if pp else "per_day",
        tax_percentage=d(pp.tax_percentage) if pp else Decimal("0"),
        tax_threshold=d(pp.tax_threshold) if pp else Decimal("0"),
        provident_fund_percent=d(pp.provident_fund_percent) if pp else Decimal("0"),
    )

    # ──── Work policy — working days and minimum hours ────
    wpol = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()

    working_days = count_working_days(wpol, start, end)
    min_hours = d(wpol.min_daily_hours) if wpol and wpol.min_daily_hours else Decimal("8")

    # ──────────────────────────────────────────────────────────────
    # THE MONTH IS THE MONTH; THEIR MONTH STARTS WHEN THEY DID
    # ──────────────────────────────────────────────────────────────
    # Every count below took the period's first day as its start, so a
    # person hired on 14 August was measured from 1 August. On Sheikh
    # Anas's August slip that became 11 absences and a 26,190.45
    # deduction for days he did not work here.
    #
    # `employed_during()` already keeps payroll from running for months
    # BEFORE somebody joined. This is the same rule inside the month they
    # joined in, which that check cannot see.
    #
    # Only the start moves. `working_days` stays the whole month's,
    # because the daily rate is a property of the month, not of the
    # person — otherwise a mid-month joiner would have a higher daily
    # rate than the colleague sitting next to them.
    person = db.query(User).filter(User.id == employee_id).first()
    joined = person.joining_date if person else None
    paid_from = max(start, joined) if joined else start
    # 0 here is a real answer, not a missing one: a joining date after
    # this month means nothing was earned in it.
    employed_days = (count_working_days(wpol, paid_from, end)
                     if paid_from <= end else 0)

    # ──── The actual counts, from the day they joined ────
    att = attendance_totals(db, employee_id, paid_from, end, wpol)
    lv = leave_totals(db, employee_id, company_id, wpol, paid_from, end)
    ab = absent_days(db, employee_id, company_id, wpol, paid_from, end)

    # ──── That month's one-off items + the loan instalment ────
    adj = adjustment_totals(db, employee_id, company_id, period)
    at = adj["totals"]
    loan_due, loan_plan = due_loan_installment(
        db, employee_id, company_id, period, run_id
    )

    work = WorkInputs(
        working_days_in_month=working_days,
        employed_days_in_month=employed_days,
        employed_from=str(paid_from) if paid_from > start else None,
        min_daily_hours=min_hours,
        present_days=att["present_days"],
        overtime_minutes=att["overtime_minutes"],
        undertime_minutes=att["undertime_minutes"],
        late_count=att["late_count"],
        late_minutes=att["late_minutes"],
        late_grace_mins=att["late_grace_mins"],
        paid_leave_days=lv["paid_leave_days"],
        unpaid_leave_days=lv["unpaid_leave_days"],
        absent_days=ab["absent_days"],

        bonus=at.get("bonus", Decimal("0")),
        incentive_pay=at.get("incentive_pay", Decimal("0")),
        arrears=at.get("arrears", Decimal("0")),
        commission=at.get("commission", Decimal("0")),
        other_earnings=at.get("other_earnings", Decimal("0")),
        other_deductions=at.get("other_deductions", Decimal("0")),
        loan_installment=loan_due,
    )

    snapshots = {
        "attendance": {
            **att,
            "working_days_in_month": working_days,
            # What the slip needs in order to explain itself later: the
            # month's days, and how many of them were theirs.
            "employed_days_in_month": employed_days,
            "counted_from": str(paid_from),
            "joined_on": str(joined) if joined else None,
            "joined_during_this_month": bool(joined and joined > start),
            "min_daily_hours": float(min_hours),
            "period_start": str(start),
            "period_end": str(end),
            **lv,
            # The actual dates of absence too — if the CEO or employee
            # asks "which days?", the answer is inside the slip
            **ab,
        },
        "salary": {
            "base_salary": str(structure.base_salary),
            "house_allowance": str(structure.house_allowance),
            "transport_allowance": str(structure.transport_allowance),
            "medical_allowance": str(structure.medical_allowance),
            "other_allowances": str(structure.other_allowances),
            "currency": structure.currency,
        },
        "adjustments": adj["detail"],
        "loans": [
            {
                "loan_id": item["loan"].id,
                "title": item["loan"].title,
                "installment": str(item["amount"]),
                "principal": str(item["loan"].principal),
                "remaining_after": str(item["remaining_after"]),
            }
            for item in loan_plan
        ],
        "policy": {
            "overtime_multiplier": str(policy_in.overtime_multiplier),
            "late_deduction_policy": policy_in.late_deduction_policy,
            "late_deduction_amount": str(policy_in.late_deduction_amount),
            "undertime_deduction": policy_in.undertime_deduction,
            "unpaid_leave_deduction": policy_in.unpaid_leave_deduction,
            "tax_percentage": str(policy_in.tax_percentage),
            "tax_threshold": str(policy_in.tax_threshold),
            "provident_fund_percent": str(policy_in.provident_fund_percent),
            "policy_configured": pp is not None,
        },
    }

    return salary, policy_in, work, snapshots, loan_plan


# ══════════════════════════════════════════════
# That month's one-off items
# ══════════════════════════════════════════════
# Which item is an earning and which a deduction is decided in one place.
# The route, the arithmetic and the UI all ask here, so they can never
# disagree.
EARNING_KINDS = {
    "incentive": "incentive_pay",
    "arrears": "arrears",
    "bonus": "bonus",
    "commission": "commission",
    "other_earning": "other_earnings",
}
DEDUCTION_KINDS = {
    "advance": "other_deductions",
    "penalty": "other_deductions",
    "other_deduction": "other_deductions",
}
ALL_KINDS = tuple(EARNING_KINDS) + tuple(DEDUCTION_KINDS)

KIND_LABELS = {
    "incentive": "Incentive Pay",
    "arrears": "Arrears",
    "bonus": "Bonus",
    "commission": "Commission",
    "other_earning": "Other Earning",
    "advance": "Advance",
    "penalty": "Penalty",
    "other_deduction": "Other Deduction",
}


def adjustment_totals(db: Session, employee_id: int, company_id: int,
                      period: str) -> dict:
    """
    That month's incentive/arrears/bonus/commission and any one-off
    katautiyan.

    Every amount is POSITIVE — whether it is an earning or a deduction is
    decided by `kind`. Negatives are not allowed at all; added by mistake,
    one would quietly inflate the salary.
    """
    from app.models.payroll import PayrollAdjustment

    rows = db.query(PayrollAdjustment).filter(
        PayrollAdjustment.employee_id == employee_id,
        PayrollAdjustment.company_id == company_id,
        PayrollAdjustment.period == period,
    ).all()

    totals = {v: Decimal("0.00") for v in
              set(EARNING_KINDS.values()) | set(DEDUCTION_KINDS.values())}
    detail = []

    for row in rows:
        field = EARNING_KINDS.get(row.kind) or DEDUCTION_KINDS.get(row.kind)
        if not field:
            continue
        amount = d(row.amount)
        if amount <= 0:
            continue
        totals[field] += amount
        detail.append({
            "kind": row.kind,
            "label": KIND_LABELS.get(row.kind, row.kind),
            "amount": str(amount),
            "is_earning": row.kind in EARNING_KINDS,
            "note": row.note,
        })

    return {"totals": totals, "detail": detail}


# ══════════════════════════════════════════════
# The loan instalment
# ══════════════════════════════════════════════
def loan_remaining(db: Session, loan) -> Decimal:
    """
    How much is outstanding — DERIVED, never from a counter.

    A `remaining` column would be decremented by every run, and a rerun
    would decrement twice. Here it is the sum of the instalments taken —
    cancel a run and its instalment row disappears, so the balance corrects
    itself. No drift is possible.
    """
    from sqlalchemy import func
    from app.models.payroll import LoanRepayment

    paid = db.query(func.coalesce(func.sum(LoanRepayment.amount), 0)).filter(
        LoanRepayment.loan_id == loan.id
    ).scalar()
    return max(Decimal("0.00"), d(loan.principal) - d(paid))


def due_loan_installment(db: Session, employee_id: int, company_id: int,
                         period: str, run_id: int = None):
    """
    Which loan's instalment, and how much, is due this month.

    Teen ehtiyat:
      1. Nothing is deducted on payrolls before `start_period`
      2. The final instalment equals the BALANCE — on a 10,000 instalment
         with only 3,000 left, 3,000 is taken, not 10,000
      3. If this same run already took an instalment (a forced rerun) the
         row is updated rather than added — so nothing is taken twice

    Return: (kul_qist, [{loan, amount}, ...])
    """
    from app.models.payroll import EmployeeLoan, LoanRepayment

    loans = db.query(EmployeeLoan).filter(
        EmployeeLoan.employee_id == employee_id,
        EmployeeLoan.company_id == company_id,
        EmployeeLoan.status == "active",
    ).all()

    total = Decimal("0.00")
    plan = []

    for loan in loans:
        # Nothing is deducted before it starts
        if str(loan.start_period) > str(period):
            continue

        remaining = loan_remaining(db, loan)

        # If this run already took an instalment, add it back to the
        # balance — otherwise a rerun sees too small a balance and the
        # instalment comes out wrong
        # nikalti
        if run_id:
            mine = db.query(LoanRepayment).filter(
                LoanRepayment.loan_id == loan.id,
                LoanRepayment.run_id == run_id,
            ).first()
            if mine:
                remaining += d(mine.amount)

        if remaining <= 0:
            continue

        amount = min(d(loan.installment), remaining)
        if amount <= 0:
            continue

        total += amount
        plan.append({"loan": loan, "amount": amount,
                     "remaining_after": remaining - amount})

    return total, plan
