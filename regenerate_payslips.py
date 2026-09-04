"""
Recompute payslips that the joining-date bug got wrong
──────────────────────────────────────────────────────
    py regenerate_payslips.py              show what would change
    py regenerate_payslips.py --apply      rewrite the affected slips
    py regenerate_payslips.py --apply --include-policy-drift
    py regenerate_payslips.py --apply --include-sent

WHAT WENT WRONG
───────────────
Payroll counted every month from its 1st, whoever it was for. Somebody
hired on 14 August was therefore measured from 1 August: the fortnight
before they had a job here came back as absence, and each of those days
was deducted at a full day's pay.

WHY A RERUN IS NOT AUTOMATICALLY SAFE
─────────────────────────────────────
A payslip is a record of a decision, and recomputing it today can change
it for reasons that have nothing to do with this bug:

  policy_drift    The payroll policy has been edited since. June and
                  July 2026 were computed when tax was 0% and absence
                  carried no deduction. Rerunning them now applies
                  today's policy to a month that was already paid —
                  a decision for the CEO, not a correction.

  month_not_over  A slip run on the 17th counted absences to the 17th.
                  That was right on the day. Rerunning it now counts the
                  whole month, which is a different (and larger) figure.

So each slip is classified, and only the ones whose difference is THIS
bug are rewritten by default. Everything else is printed and left alone.

The rerun goes through `run_for_employee`, the same path the CEO's own
payroll run uses — same snapshots, same calculation notes, same PDF.
Nothing here reimplements payroll arithmetic.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agents.payroll_agent import run_for_employee
# ──── This script works across companies, and says so ────
# The tenant guard refuses any query on a session that has not declared
# which company it is for. These tools audit or repair the whole
# database, so crossing companies IS the job — the point is that it is
# declared rather than assumed, and appears in the list
# `check_tenancy.py` prints.
from app.utils.tenancy import unscoped_session


def SessionLocal():          # noqa: N802  (same name, declared scope)
    return unscoped_session("regenerate_payslips: rebuilds slips in every company")
from app.models.payroll import Payslip
from app.models.user import User
from app.utils.payroll_calc import compute_payroll
from app.utils.payroll_data import gather_inputs, parse_period

APPLY = "--apply" in sys.argv
WITH_DRIFT = "--include-policy-drift" in sys.argv
WITH_SENT = "--include-sent" in sys.argv

# --period=2026-07, repeatable. Naming a month is an instruction about
# THAT month, so the "is it this bug" gate does not apply to it — the
# CEO has already decided. The `sent` guard still stands, because
# rewriting a slip somebody has been emailed deserves its own word.
ONLY = {a.split("=", 1)[1] for a in sys.argv
        if a.startswith("--period=") and "=" in a}

MONEY = ("gross_pay", "total_deductions", "net_salary")


def policy_now(db, company_id, period, employee_id) -> dict:
    """Today's policy, in the same shape the slip stored."""
    _, _, _, snaps, _ = gather_inputs(db, employee_id, company_id, period)
    return snaps.get("policy") or {}


def classify(db, slip) -> dict:
    """What the slip would say if it ran now, and why it differs."""
    start, end = parse_period(slip.period)

    salary, policy, work, snaps, _ = gather_inputs(
        db, slip.employee_id, slip.company_id, slip.period)
    fresh = compute_payroll(salary, policy, work)

    stored_policy = slip.policy_snapshot or {}
    fresh_policy = snaps.get("policy") or {}
    stored_att = slip.attendance_snapshot or {}

    reasons = []
    if work.employed_days_in_month < work.working_days_in_month:
        reasons.append("joining")

    # Compared key by key: a value that only exists on one side counts
    if any(str(stored_policy.get(k)) != str(v)
           for k, v in fresh_policy.items() if k != "policy_configured"):
        reasons.append("policy_drift")

    counted_until = stored_att.get("counted_until")
    if counted_until and str(counted_until) < str(end):
        reasons.append("month_not_over")

    changed = (str(slip.gross_pay) != str(fresh.gross_pay)
               or str(slip.net_salary) != str(fresh.net_salary))

    return {
        "slip": slip,
        "fresh": fresh,
        "work": work,
        "reasons": reasons,
        "changed": changed,
    }


def main() -> int:
    db = SessionLocal()
    slips = db.query(Payslip).filter(
        Payslip.status != "cancelled"
    ).order_by(Payslip.period, Payslip.employee_id).all()

    if not slips:
        print("No payslips on record.")
        return 0

    names = {u.id: u for u in db.query(User).all()}
    rows = [classify(db, s) for s in slips]

    print(f"{'period':<9} {'employee':<14} {'status':<9} "
          f"{'stored net':>12} {'would be':>12}  why")
    print("─" * 78)
    for r in rows:
        s, f = r["slip"], r["fresh"]
        u = names.get(s.employee_id)
        why = ", ".join(r["reasons"]) if r["changed"] else "no change"
        print(f"{s.period:<9} {(u.full_name if u else s.employee_id)!s:<14} "
              f"{s.status:<9} {float(s.net_salary or 0):>12,.2f} "
              f"{float(f.net_salary):>12,.2f}  {why}")

    # ──── What gets rewritten ────
    def wanted(r) -> bool:
        if not r["changed"]:
            return False
        if r["slip"].status == "sent" and not WITH_SENT:
            return False
        if ONLY:
            return r["slip"].period in ONLY
        if "joining" not in r["reasons"]:
            return False
        if "policy_drift" in r["reasons"] and not WITH_DRIFT:
            return False
        return True

    todo = [r for r in rows if wanted(r)]
    held = [r for r in rows
            if r["changed"] and r not in todo]

    print()
    for r in held:
        s = r["slip"]
        u = names.get(s.employee_id)
        blockers = []
        if ONLY and s.period not in ONLY:
            blockers.append(f"not in --period={sorted(ONLY)}")
        elif "joining" not in r["reasons"]:
            blockers.append("not this bug (--period=" + s.period + ")")
        if "policy_drift" in r["reasons"] and not WITH_DRIFT:
            blockers.append("policy changed since (--include-policy-drift)")
        if s.status == "sent" and not WITH_SENT:
            blockers.append("already sent to the employee (--include-sent)")
        print(f"HELD  {s.period} {(u.full_name if u else s.employee_id)}: "
              f"{'; '.join(blockers)}")

    if not todo:
        print("\nNothing to rewrite.")
        db.close()
        return 0

    print(f"\n{len(todo)} slip(s) to rewrite"
          + ("" if APPLY else " — dry run, pass --apply to do it"))

    if not APPLY:
        db.close()
        return 0

    for r in todo:
        s = r["slip"]
        u = names.get(s.employee_id)
        # ──── Read the old figures BEFORE the rerun, not after ────
        # `run_for_employee` writes through the same identity map, so
        # `s` is the row it just overwrote. Reading s.gross_pay after it
        # returns records the NEW value as the old one — an audit trail
        # that quietly says nothing changed.
        was = {
            "gross": str(s.gross_pay),
            "deductions": str(s.total_deductions),
            "net": str(s.net_salary),
            "status": s.status,
            "emailed": str(s.email_sent_at) if s.email_sent_at else None,
        }
        before = float(s.net_salary or 0)
        out = run_for_employee(db, s.employee_id, s.company_id,
                               s.period, s.run_id)
        if out["status"] != "computed":
            print(f"  FAILED {s.period} {u.full_name if u else s.employee_id}: "
                  f"{out['error']}")
            continue

        # ──── A slip that changed after it was issued says so ────
        # The recompute overwrites the row, so without this the old
        # figures leave no trace and nobody can answer "why is my June
        # different from the one I was emailed". `was_emailed` is kept
        # separately because the rerun resets the status to `computed`
        # while the employee still holds the earlier version.
        fresh = db.query(Payslip).filter(Payslip.id == s.id).first()
        if fresh:
            notes = dict(fresh.calculation_notes or {})
            history = list(notes.get("reissues") or [])
            history.append({
                "replaced": {"gross": was["gross"],
                             "deductions": was["deductions"],
                             "net": was["net"]},
                "reasons": r["reasons"],
                "was_emailed": was["emailed"],
                "previous_status": was["status"],
            })
            notes["reissues"] = history
            fresh.calculation_notes = notes
        db.commit()
        print(f"  done   {s.period} {(u.full_name if u else s.employee_id)!s:<14} "
              f"{before:,.2f} -> {float(out['net_salary']):,.2f} "
              f"(pdf: {out.get('pdf_status') or 'n/a'})")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
