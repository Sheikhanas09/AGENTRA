"""
Remove the companies the probes created.

`_e2e_newcompany.py` registers a real company every time it runs, because
that is the only way to get a company whose id is not its CEO's user id.
This takes them back out.

Only test companies are touched — those named "Probe ..." and the
"Zeta Labs" fixture — and it prints what it will do before doing
anything.

    py tests/_cleanup_probe.py            list them
    py tests/_cleanup_probe.py --apply    remove them

⚠ `Zeta Labs` is a REAL SECOND COMPANY, created so that multi-tenancy
can be seen working in the browser: `company_id = 1000` while its CEO is
user 46, which is the combination that exposes every place still using a
user id as a company. Keep it for the demo, or remove it here.

Its accounts use `@zeta.test`, which does not exist, so the proactive HR
job's emails to them bounce. That is the only reason to remove it if you
are not using it.

    ceo@zeta.test / zeta12345      emp@zeta.test / zeta12345
"""
import sys
import warnings

warnings.filterwarnings("ignore")

from sqlalchemy import text                                    # noqa: E402

# ──── Backend/ ko raaste par lao ────
# Yeh script Backend/ ke andar ek folder mein hai. `py tests/x.py`
# chalane par Python sirf us folder ko sys.path par rakhta hai, cwd ko
# nahi — to `import app` nakaam ho jata. Aur kuch checks source tree ko
# `Path("app")` se scan karte hain, jo cwd par munhasir hai.
import os as _os
import sys as _sys

_BACKEND = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)
_os.chdir(_BACKEND)

from app.database import admin_engine                          # noqa: E402
from app.models.company import Company                         # noqa: E402
from app.models.user import User                               # noqa: E402
from app.utils.tenancy import open_unscoped_session            # noqa: E402

APPLY = "--apply" in sys.argv

# Every table that points at a company, deepest first, so nothing is
# removed while something still refers to it. `ON DELETE RESTRICT` is
# what makes that order matter — and is also why a company cannot be
# deleted by accident from the application.
ORDER = [
    "loan_repayments", "payroll_adjustments", "payslips", "payroll_runs",
    "employee_loans", "salary_structures", "payroll_policy",
    "company_branding",
    "policy_decisions_log", "leave_documents", "leave_requests",
    "leave_balances", "company_leave_types",
    "attendance_intervals", "attendance_photos", "attendance_sessions",
    "face_enrollment", "office_locations", "company_policy_overrides",
    "company_policies", "company_work_policy",
    "chat_messages", "chat_sessions", "hr_nudges", "hr_cases",
    "hr_requests", "hr_settings", "employment_records",
    "interview_feedback", "final_scores", "interviews", "applications",
    "candidates", "jobs",
    "users",
]


def main():
    with open_unscoped_session("cleanup: finding probe companies") as db:
        from sqlalchemy import or_
        rows = db.query(Company).filter(
            or_(Company.name.like("Probe %"),
                Company.slug == "zeta labs")).order_by(Company.id).all()
        targets = []
        for c in rows:
            emails = [u.email for u in db.query(User).filter(
                User.company_id == c.id).all()]
            if emails and not all(
                    (e or "").endswith(("probetest.example", "zeta.test"))
                    for e in emails):
                print(f"  skipping {c.id} {c.name!r} — it has accounts that "
                      f"are not probe accounts: {emails}")
                continue
            targets.append((c.id, c.name, len(emails)))

    if not targets:
        print("  no probe companies to remove")
        return

    print(f"  {len(targets)} probe compan{'y' if len(targets) == 1 else 'ies'}:")
    for cid, name, n in targets:
        print(f"     {cid:5}  {name:<18} {n} users")

    if not APPLY:
        print("\n  dry run — re-run with --apply")
        return

    engine = admin_engine()
    with engine.begin() as conn:
        for cid, name, _ in targets:
            for t in ORDER:
                conn.execute(text(f"DELETE FROM {t} WHERE company_id = :c"),
                             {"c": cid})
            conn.execute(text("DELETE FROM companies WHERE id = :c"),
                         {"c": cid})
            print(f"  removed {cid} {name!r}")


if __name__ == "__main__":
    main()
