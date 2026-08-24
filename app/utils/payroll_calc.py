"""
Payroll arithmetic — nothing else
─────────────────────────────────
This file is deliberately EMPTY of everything else: no DB, no HTTP, no
agents. Numbers go in, numbers come out.

Two reasons:

  1. **Testing** — every case (zero working days, a whole month of unpaid
     leave, sitting exactly on the tax threshold) can be tested without
     starting a server.
  2. **Trust** — code that depends only on its inputs gives the same
     answer every time. For salary that is the single most important
     property.

═══════════════════════════════════════════════════════════
DECIMAL, EVERYWHERE
═══════════════════════════════════════════════════════════
Not one float gets in here. `0.1 + 0.2 != 0.3` — and a slip contains ten
such additions and subtractions. By the end "earnings − deductions ≠ net"
appears, and it becomes impossible to explain to an employee.

═══════════════════════════════════════════════════════════
ROUND EACH LINE FIRST, THEN ADD
═══════════════════════════════════════════════════════════
Every line on the slip prints to two decimals. If we added unrounded
values and only rounded the total, the printed lines would not add up to
their own total:

    10.004 + 10.004 = 20.008 → printed: 10.00 + 10.00 = 20.01   ✗

So EVERY line is rounded first, then added. The slip always adds up.

═══════════════════════════════════════════════════════════
`notes` ARE READ BY THE EMPLOYEE
═══════════════════════════════════════════════════════════
Every string appended to `notes` ends up on the payslip, under "How were
these figures calculated?". They are not debug output — they are the
answer to "where did this number come from", so they are written for a
person, not for a developer.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import List

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def q(value) -> Decimal:
    """
    Round money to two decimals — ROUND_HALF_UP.

    Python's default is ROUND_HALF_EVEN ("banker's rounding"): 0.5 goes up
    sometimes and down other times. Fine for statistics, not for wages —
    it is hard to explain to an employee why 2.5 became 2 once and 3
    another time. HALF_UP is what everyone was taught at school.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def d(value) -> Decimal:
    """Turn anything into a Decimal — floats go via str()"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value if value is not None else 0))


# ══════════════════════════════════════════════
# Inputs
# ══════════════════════════════════════════════
@dataclass
class SalaryInputs:
    """An employee's fixed salary structure"""
    base_salary: Decimal = ZERO
    house_allowance: Decimal = ZERO
    transport_allowance: Decimal = ZERO
    medical_allowance: Decimal = ZERO
    other_allowances: Decimal = ZERO

    @property
    def allowances_total(self) -> Decimal:
        return q(self.house_allowance + self.transport_allowance
                 + self.medical_allowance + self.other_allowances)

    @property
    def gross_fixed(self) -> Decimal:
        return q(self.base_salary + self.allowances_total)


@dataclass
class PolicyInputs:
    """The company's overtime and deduction rules"""
    overtime_multiplier: Decimal = Decimal("1.5")

    # per_occurrence = a fixed amount every time
    # per_minute     = a fixed amount per late minute
    # pro_rata       = the minutes lost, paid at the employee's own hourly
    #                  rate (no amount has to be configured)
    # none           = nothing is deducted
    late_deduction_policy: str = "none"
    late_deduction_amount: Decimal = ZERO

    undertime_deduction: str = "none"        # pro_rata | none
    unpaid_leave_deduction: str = "pro_rata" # pro_rata | none

    # Absence without notice — did not come in, did not apply for leave.
    # per_day = a full day's pay per day | none = nothing deducted
    absent_deduction: str = "per_day"

    tax_percentage: Decimal = ZERO
    tax_threshold: Decimal = ZERO
    provident_fund_percent: Decimal = ZERO


@dataclass
class WorkInputs:
    """
    The real figures for that month — all of them come from the
    attendance and leave modules.

    Nothing here is an estimate: every field has a source.
    """
    working_days_in_month: int = 0    # counted from the policy's working_days
    min_daily_hours: Decimal = Decimal("8")

    present_days: int = 0
    overtime_minutes: int = 0
    undertime_minutes: int = 0
    late_count: int = 0
    late_minutes: int = 0          # minutes AFTER grace — the deduction uses this
    late_grace_mins: int = 0       # only so it can be explained on the slip

    paid_leave_days: int = 0          # salary continues
    unpaid_leave_days: int = 0        # salary is deducted
    absent_days: int = 0              # neither present nor on leave

    # ──── One-off items for that month (from payroll_adjustments) ────
    # These change every month, which is why they are not in the salary structure
    bonus: Decimal = ZERO
    incentive_pay: Decimal = ZERO
    arrears: Decimal = ZERO
    commission: Decimal = ZERO
    other_earnings: Decimal = ZERO
    other_deductions: Decimal = ZERO

    # ──── This month's loan instalment ────
    # Derived from the outstanding balance — the final instalment may be smaller
    loan_installment: Decimal = ZERO


# ══════════════════════════════════════════════
# Output
# ══════════════════════════════════════════════
@dataclass
class PayrollResult:
    # ──── Rates ────
    working_days: int = 0
    daily_rate: Decimal = ZERO
    hourly_rate: Decimal = ZERO

    # ──── Earnings ────
    base_salary: Decimal = ZERO
    allowances_total: Decimal = ZERO
    overtime_pay: Decimal = ZERO
    bonus: Decimal = ZERO
    incentive_pay: Decimal = ZERO
    arrears: Decimal = ZERO
    commission: Decimal = ZERO
    other_earnings: Decimal = ZERO
    gross_pay: Decimal = ZERO

    # ──── Deductions ────
    late_deduction: Decimal = ZERO
    undertime_deduction: Decimal = ZERO
    unpaid_leave_deduction: Decimal = ZERO
    absent_deduction: Decimal = ZERO
    tax_deduction: Decimal = ZERO
    provident_fund: Decimal = ZERO
    loan_deduction: Decimal = ZERO
    other_deductions: Decimal = ZERO
    total_deductions: Decimal = ZERO

    net_salary: Decimal = ZERO

    # ──── Every step of the calculation — "where did this come from" ────
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """For JSON — Decimals are kept as strings so no money is lost"""
        return {
            "working_days": self.working_days,
            "daily_rate": str(self.daily_rate),
            "hourly_rate": str(self.hourly_rate),
            "base_salary": str(self.base_salary),
            "allowances_total": str(self.allowances_total),
            "overtime_pay": str(self.overtime_pay),
            "bonus": str(self.bonus),
            "incentive_pay": str(self.incentive_pay),
            "arrears": str(self.arrears),
            "commission": str(self.commission),
            "other_earnings": str(self.other_earnings),
            "gross_pay": str(self.gross_pay),
            "late_deduction": str(self.late_deduction),
            "undertime_deduction": str(self.undertime_deduction),
            "unpaid_leave_deduction": str(self.unpaid_leave_deduction),
            "absent_deduction": str(self.absent_deduction),
            "tax_deduction": str(self.tax_deduction),
            "provident_fund": str(self.provident_fund),
            "loan_deduction": str(self.loan_deduction),
            "other_deductions": str(self.other_deductions),
            "total_deductions": str(self.total_deductions),
            "net_salary": str(self.net_salary),
            "notes": self.notes,
            "warnings": self.warnings,
        }


# ══════════════════════════════════════════════
# The calculation itself
# ══════════════════════════════════════════════
def compute_payroll(salary: SalaryInputs, policy: PolicyInputs,
                    work: WorkInputs) -> PayrollResult:
    """
    One employee's salary for one month.

    Order: rates → earnings → deductions → net.
    Every step is written into `notes`, so that if anyone later asks
    "where did this 10,909 come from", the answer is inside the slip.
    """
    r = PayrollResult()
    base = q(d(salary.base_salary))

    r.base_salary = base
    r.allowances_total = salary.allowances_total
    r.bonus = q(d(work.bonus))
    r.working_days = int(work.working_days_in_month or 0)

    # ══════════════════════════════════════════
    # 1. Rates
    # ══════════════════════════════════════════
    # The rate is derived from that month's ACTUAL working days. So the
    # hourly rate in February (20 days) is higher than in March (23 days).
    min_hours = d(work.min_daily_hours)

    if r.working_days <= 0:
        # A whole month with no working day? That is a policy mistake, not
        # the employee's. We carry on with a rate of 0 — the fixed salary
        # is still paid, but overtime and unpaid leave cannot be worked out.
        r.warnings.append(
            "No working day was found for this month, so an hourly rate could "
            "not be derived. Please check the working-days policy."
        )
    else:
        r.daily_rate = q(base / Decimal(r.working_days))
        if min_hours > 0:
            r.hourly_rate = q(base / (Decimal(r.working_days) * min_hours))
            r.notes.append(
                f"hourly rate = {base} / ({r.working_days} days × {min_hours} hours)"
                f" = {r.hourly_rate}"
            )
        else:
            r.warnings.append(
                "min_daily_hours is zero — overtime and short hours cannot "
                "be calculated"
            )
        r.notes.append(
            f"daily rate = {base} / {r.working_days} days = {r.daily_rate}"
        )

    # ══════════════════════════════════════════
    # 2. Earnings
    # ══════════════════════════════════════════
    ot_minutes = max(0, int(work.overtime_minutes or 0))
    if ot_minutes and r.hourly_rate > 0:
        ot_hours = d(ot_minutes) / Decimal(60)
        mult = d(policy.overtime_multiplier)
        r.overtime_pay = q(ot_hours * r.hourly_rate * mult)
        r.notes.append(
            f"overtime = ({ot_minutes}/60 hours) × {r.hourly_rate} × {mult}x"
            f" = {r.overtime_pay}"
        )

    # ──── One-off items for the month ────
    # All POSITIVE — whether something is an earning or a deduction is
    # decided by its field, never by the sign of the number
    r.incentive_pay = q(d(work.incentive_pay))
    r.arrears = q(d(work.arrears))
    r.commission = q(d(work.commission))
    r.other_earnings = q(d(work.other_earnings))

    extras = r.bonus + r.incentive_pay + r.arrears + r.commission + r.other_earnings

    r.gross_pay = q(base + r.allowances_total + r.overtime_pay + extras)
    r.notes.append(
        f"gross = base {base} + allowances {r.allowances_total}"
        + (f" + overtime {r.overtime_pay}" if r.overtime_pay else "")
        + (f" + bonus {r.bonus}" if r.bonus else "")
        + (f" + incentive {r.incentive_pay}" if r.incentive_pay else "")
        + (f" + arrears {r.arrears}" if r.arrears else "")
        + (f" + commission {r.commission}" if r.commission else "")
        + (f" + other {r.other_earnings}" if r.other_earnings else "")
        + f" = {r.gross_pay}"
    )

    # ══════════════════════════════════════════
    # 3. Deductions
    # ══════════════════════════════════════════

    # ──── Late arrival ────
    late_count = max(0, int(work.late_count or 0))
    late_mins = max(0, int(work.late_minutes or 0))
    late_amt = q(d(policy.late_deduction_amount))

    if policy.late_deduction_policy == "per_occurrence" and late_count:
        r.late_deduction = q(Decimal(late_count) * late_amt)
        r.notes.append(
            f"late = {late_count} occurrence(s) × {late_amt} = {r.late_deduction}"
        )
    elif policy.late_deduction_policy == "per_minute" and late_mins:
        r.late_deduction = q(Decimal(late_mins) * late_amt)
        r.notes.append(
            f"late = {late_mins} minutes × {late_amt} = {r.late_deduction}"
        )
    elif policy.late_deduction_policy == "pro_rata" and late_mins:
        # ──── Time lost, pay lost ────
        # Under `per_occurrence`, being one minute late and being two hours
        # late both cost the same 500. That was not fair.
        #
        # Here nothing has to be configured: the deduction comes from the
        # employee's own salary and that month's working hours. The hourly
        # rate is the same one overtime and short hours use — an hour lost
        # should cost the same in all three places.
        if r.hourly_rate > 0:
            r.late_deduction = q((d(late_mins) / Decimal(60)) * r.hourly_rate)
            grace = max(0, int(work.late_grace_mins or 0))
            r.notes.append(
                f"late = ({late_mins}/60 hours) × {r.hourly_rate}"
                f" = {r.late_deduction}"
                # Without this, an employee who was 20 minutes late cannot
                # see why only 5 minutes were charged
                + (f"  (late on {late_count} day(s); the first {grace} minutes "
                   f"each day are grace and are not charged)"
                   if grace else "")
            )
        else:
            r.warnings.append(
                "The late deduction could not be calculated — the hourly rate "
                "is zero (are min_daily_hours or working days set?)"
            )

    # ──── Short hours ────
    ut_minutes = max(0, int(work.undertime_minutes or 0))
    if policy.undertime_deduction == "pro_rata" and ut_minutes and r.hourly_rate > 0:
        r.undertime_deduction = q((d(ut_minutes) / Decimal(60)) * r.hourly_rate)
        r.notes.append(
            f"short hours = ({ut_minutes}/60 hours) × {r.hourly_rate}"
            f" = {r.undertime_deduction}"
        )

    # ──── Unpaid leave ────
    unpaid_days = max(0, int(work.unpaid_leave_days or 0))
    if policy.unpaid_leave_deduction == "pro_rata" and unpaid_days and r.daily_rate > 0:
        r.unpaid_leave_deduction = q(Decimal(unpaid_days) * r.daily_rate)
        r.notes.append(
            f"unpaid leave = {unpaid_days} day(s) × {r.daily_rate}"
            f" = {r.unpaid_leave_deduction}"
        )
        if unpaid_days > r.working_days:
            r.warnings.append(
                f"{unpaid_days} days of unpaid leave were recorded, but the "
                f"month only has {r.working_days} working days — please check "
                f"the data"
            )

    # ──── Absence without notice ────
    # Did not come in and did not apply for leave. This is a different
    # thing from unpaid leave: there the employee gave notice and it was
    # approved; here they were simply absent.
    #
    # One day absent = one full day's pay. There is no grace — that is a
    # company decision, and softening it would make the whole leave system
    # pointless: if disappearing without notice costs nothing, why would
    # anyone file a leave request at all?
    ab_days = max(0, int(work.absent_days or 0))
    if policy.absent_deduction == "per_day" and ab_days and r.daily_rate > 0:
        r.absent_deduction = q(Decimal(ab_days) * r.daily_rate)
        r.notes.append(
            f"absence = {ab_days} day(s) × {r.daily_rate}"
            f" = {r.absent_deduction}  (without notice; no grace applies)"
        )
        if ab_days + unpaid_days > r.working_days:
            r.warnings.append(
                f"{ab_days} days absent + {unpaid_days} days unpaid leave "
                f"= {ab_days + unpaid_days}, but the month only has "
                f"{r.working_days} working days — please check the data"
            )

    # ──── Tax ────
    # Tax applies ONLY to the amount ABOVE the threshold. Charging it on
    # the whole gross would mean that earning two rupees more near the
    # threshold costs thousands — which makes no sense.
    tax_pct = d(policy.tax_percentage)
    threshold = q(d(policy.tax_threshold))

    if tax_pct > 0:
        taxable = r.gross_pay - threshold
        if taxable > 0:
            r.tax_deduction = q(taxable * tax_pct / Decimal(100))
            r.notes.append(
                f"tax = (gross {r.gross_pay} − threshold {threshold})"
                f" × {tax_pct}% = {r.tax_deduction}"
            )
        else:
            r.notes.append(
                f"tax = 0 (gross {r.gross_pay} is below the threshold {threshold})"
            )

    # ──── Provident fund ────
    # Charged on BASE, not on gross — that is the usual practice, and it
    # means working overtime does not increase the PF (which is correct).
    pf_pct = d(policy.provident_fund_percent)
    if pf_pct > 0 and base > 0:
        r.provident_fund = q(base * pf_pct / Decimal(100))
        r.notes.append(
            f"provident fund = base {base} × {pf_pct}% = {r.provident_fund}"
        )

    # ──── Loan instalment ────
    # ═══ WHY THE LOAN COMES AFTER TAX ═══
    # A loan repayment is a deduction, not an expense — the employee is
    # paying back their own debt. It should not reduce their tax, or
    # taking a loan would become a way to avoid tax. So tax is charged on
    # the full gross and the loan is taken off afterwards.
    r.loan_deduction = q(d(work.loan_installment))
    if r.loan_deduction > 0:
        r.notes.append(f"loan instalment = {r.loan_deduction}")

    # ──── One-off deductions (advance, penalty, etc.) ────
    r.other_deductions = q(d(work.other_deductions))
    if r.other_deductions > 0:
        r.notes.append(f"other deductions = {r.other_deductions}")

    # ──── Total ────
    # Every line is already rounded, so the slip adds up exactly
    r.total_deductions = q(
        r.late_deduction + r.undertime_deduction + r.unpaid_leave_deduction
        + r.absent_deduction
        + r.tax_deduction + r.provident_fund
        + r.loan_deduction + r.other_deductions
    )

    # ══════════════════════════════════════════
    # 4. Net
    # ══════════════════════════════════════════
    net = r.gross_pay - r.total_deductions

    if net < 0:
        # This can happen — a whole month of unpaid leave, for instance.
        # But money cannot be taken back from an employee, so net is zero.
        # Not silently: it goes into both the warnings and the notes.
        r.warnings.append(
            f"Deductions ({r.total_deductions}) came to more than gross "
            f"({r.gross_pay}) — net worked out to {q(net)} and was set to "
            f"zero. This needs to be reviewed."
        )
        r.notes.append(f"net = {q(net)} → 0.00 (a negative salary is not possible)")
        r.net_salary = ZERO
    else:
        r.net_salary = q(net)
        r.notes.append(
            f"net = gross {r.gross_pay} − deductions {r.total_deductions}"
            f" = {r.net_salary}"
        )

    return r
