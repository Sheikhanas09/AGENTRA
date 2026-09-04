"""
Cancel payslips for months before somebody joined
─────────────────────────────────────────────────
    py fix_prejoining_payslips.py            show what would change
    py fix_prejoining_payslips.py --apply    actually change it

Payroll used to run over "everyone employed today", with no reference to
the month being paid. So an employee who joined on 17 June had payslips
generated for February through May — four months of salary recorded for
somebody who was not employed. The cause is fixed
(`workforce.employed_during`), but rows already written stay written,
and on the CEO's console they read back as real money.

═══════════════════════════════════════════════════════════
CANCELLED, NOT DELETED
═══════════════════════════════════════════════════════════
`cancelled` is a status payroll already understands — `get_payslips`,
`employee_payslip` and `payroll_overview` all skip it, so the figures
disappear from every view without the row disappearing from the
database. Deleting would destroy the evidence that this happened, which
is exactly what somebody auditing the payroll would want to see.

The reason goes in `error_note`, so a year from now the row explains
itself instead of looking like an unexplained cancellation.

═══════════════════════════════════════════════════════════
WHAT THIS DOES NOT TOUCH
═══════════════════════════════════════════════════════════
`payroll_runs` keeps its own totals, and they are left alone. A run is a
record of what happened on the day it ran — including the mistake. Going
back and quietly rewriting those totals would leave no trace that any of
this occurred.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ──── This script works across companies, and says so ────
# The tenant guard refuses any query on a session that has not declared
# which company it is for. These tools audit or repair the whole
# database, so crossing companies IS the job — the point is that it is
# declared rather than assumed, and appears in the list
# `check_tenancy.py` prints.
from app.utils.tenancy import unscoped_session


def SessionLocal():          # noqa: N802  (same name, declared scope)
    return unscoped_session("fix_prejoining_payslips: repairs slips in every company")
from app.models.payroll import Payslip
from app.models.user import User

NOTE = ("Cancelled: this month is before the employee's joining date, so "
        "there was no employment to pay. Generated before payroll checked "
        "joining dates.")


def main() -> int:
    apply = "--apply" in sys.argv
    db = SessionLocal()

    try:
        users = {u.id: u for u in db.query(User).all()}
        found = []

        for p in db.query(Payslip).order_by(
                Payslip.employee_id, Payslip.period).all():
            u = users.get(p.employee_id)
            if not u or not u.joining_date:
                continue
            joined_period = f"{u.joining_date.year:04d}-{u.joining_date.month:02d}"
            if str(p.period) < joined_period:
                found.append((p, u))

        if not found:
            print("\nNothing to do — no payslip predates a joining date.\n")
            return 0

        to_change = [(p, u) for p, u in found if p.status != "cancelled"]
        already = len(found) - len(to_change)

        print(f"\n{len(found)} payslip(s) predate a joining date"
              f"{f' ({already} already cancelled)' if already else ''}:\n")

        total = 0.0
        for p, u in found:
            mark = "     " if p.status == "cancelled" else "  -> "
            print(f"{mark}#{p.id:<5} {u.full_name:<14} joined {u.joining_date}"
                  f"  {p.period}  {p.status:<10} "
                  f"net={float(p.net_salary or 0):>11,.2f}")
            if p.status != "cancelled":
                total += float(p.net_salary or 0)

        print(f"\n{len(to_change)} to cancel, {total:,.2f} in net pay that was "
              f"never owed.")

        if not to_change:
            print("Nothing left to change.\n")
            return 0

        if not apply:
            print("\nThis was a dry run. Re-run with --apply to change them.\n")
            return 0

        for p, _u in to_change:
            p.status = "cancelled"
            # Keep any existing note rather than overwriting it
            p.error_note = f"{p.error_note}\n{NOTE}".strip() if p.error_note \
                else NOTE
            db.add(p)

        db.commit()
        print(f"\n{len(to_change)} payslip(s) cancelled. The rows are kept; "
              f"they no longer appear in any total.\n")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
