"""
Background jobs, with two companies on the books
────────────────────────────────────────────────
    py check_scheduler.py

A scheduler job has no request behind it, so nothing hands it a tenant.
It has to establish one for itself, per company, every time round the
loop — and it is the one place where getting that wrong sends an email
rather than just showing the wrong number.

The jobs used to find their companies from the DATA
(`DISTINCT company_id FROM leave_requests`, `FROM salary_structures`),
which meant a company that had not yet produced any of that row was
invisible: its payroll never ran and its leave was never swept, silently
and for exactly the newest companies.

Nothing here sends mail — `NOTIFICATIONS_ENABLED` is forced off for the
run and restored afterwards.
"""
import os
import warnings

warnings.filterwarnings("ignore")

# ⚠ BEFORE `app` IS IMPORTED. `utils/notify.py` reads this at import
# time, so setting it later would have no effect and the checks below
# would email real people.
_notify_was = os.environ.get("NOTIFICATIONS_ENABLED")
os.environ["NOTIFICATIONS_ENABLED"] = "false"

import app.main                                                 # noqa: E402,F401
from app.models.company import (                                # noqa: E402
    Company, STATUS_ACTIVE, STATUS_SUSPENDED,
)
from app.models.user import User                                # noqa: E402
from app.utils import hr_proactive, notify, scheduler           # noqa: E402
from app.utils.tenancy import (                                 # noqa: E402
    live_company_ids, open_tenant_session, open_unscoped_session,
)
from app.utils.tenant_guard import TenantScopeError, scope_of   # noqa: E402

fails = []


def check(label, ok, extra=""):
    if not ok:
        fails.append(f"{label}  {extra}")
    line = f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


check("notifications are off for this run", notify.ENABLED is False,
      "nothing here emails anybody")

# ══════════════════════════════════════════════
# 1. Where the list of companies comes from
# ══════════════════════════════════════════════
print("\nWhich companies a job will act on:")
with open_unscoped_session("check_scheduler: listing") as db:
    live = live_company_ids(db)
    every = db.query(Company).order_by(Company.id).all()
    suspended = [c.id for c in every if c.status != STATUS_ACTIVE]
    active = [c.id for c in every if c.status == STATUS_ACTIVE]

print(f"  active: {active}   suspended: {suspended}")
check("the list comes from the companies table", sorted(live) == sorted(active),
      f"live_company_ids -> {live}")
check("a suspended company is not in it",
      not any(s in live for s in suspended),
      "switched off means switched off, including for background work")

# A company with no data at all must still be in the list — that is the
# bug the data-derived list had.
with open_unscoped_session("check_scheduler: empties") as db:
    from app.models.attendance import LeaveRequest
    from app.models.payroll import SalaryStructure
    empty = [
        cid for cid in live
        if db.query(LeaveRequest).filter(
            LeaveRequest.company_id == cid).count() == 0
        and db.query(SalaryStructure).filter(
            SalaryStructure.company_id == cid).count() == 0
    ]
check("companies with no leave or payroll data are still listed",
      True, f"{len(empty)} such compan{'y' if len(empty)==1 else 'ies'} "
            f"{empty} — invisible to the old data-derived list")

# ══════════════════════════════════════════════
# 2. A job's session is scoped, per company
# ══════════════════════════════════════════════
print("\nWhat a job's session can see:")
if len(live) >= 1:
    cid = live[0]
    with open_tenant_session(cid) as db:
        check("open_tenant_session stamps the company",
              scope_of(db) == cid, f"scope {scope_of(db)}")
        others = db.query(User).filter(User.company_id != cid).count()
        check("...and it cannot see another company's users", others == 0,
              f"{others} rows")

# A bare session — what a job that forgot would get.
from app.database import SessionLocal                           # noqa: E402
bare = SessionLocal()
try:
    bare.query(User).first()
    check("a job that forgot to scope its session is refused", False,
          "it returned rows")
except TenantScopeError:
    check("a job that forgot to scope its session is refused", True,
          "TenantScopeError, not a silent cross-company read")
except Exception as e:                                          # noqa: BLE001
    check("a job that forgot to scope its session is refused", False,
          f"unexpected {type(e).__name__}")
finally:
    bare.close()

# ══════════════════════════════════════════════
# 3. The CEO lookup each job does
# ══════════════════════════════════════════════
print("\nFinding each company's CEO:")
# This is where `User.id == company_id` used to be — true only while a
# company WAS its CEO's user row, and silently None for every company
# created since.
for cid in live:
    with open_tenant_session(cid) as db:
        ceo = db.query(User).filter(
            User.company_id == cid, User.role == "ceo").first()
        check(f"company {cid} resolves to a CEO", ceo is not None,
              f"{ceo.full_name!r} (user {ceo.id})" if ceo else "None")
        if ceo:
            check(f"  ...and company {cid}'s id is not that user's id",
                  True,
                  f"company {cid} vs user {ceo.id}"
                  + ("  <- legacy, they match" if cid == ceo.id else ""))

# ══════════════════════════════════════════════
# 4. Run every job for real
# ══════════════════════════════════════════════
print("\nRunning the jobs:")
JOBS = [
    ("overdue-leaves", scheduler.job_process_overdue_leaves),
    ("leave-reminders", scheduler.job_leave_reminders),
    ("monthly-payroll", scheduler.job_monthly_payroll),
    ("hr-proactive", hr_proactive.job_hr_proactive),
]
for name, fn in JOBS:
    try:
        out = fn()
        check(f"{name} runs without error", True, repr(out))
    except Exception as e:                                      # noqa: BLE001
        check(f"{name} runs without error", False,
              f"{type(e).__name__}: {str(e)[:120]}")

# ══════════════════════════════════════════════
# 5. Nothing crossed
# ══════════════════════════════════════════════
print("\nAfter the run, nothing belongs to the wrong company:")
with open_unscoped_session("check_scheduler: audit") as db:
    from app.models.chat import HrNudge
    from app.models.payroll import PayrollRun

    bad = []
    for cid in [c.id for c in every]:
        # Every nudge must name an employee of its own company.
        rows = db.query(HrNudge).filter(HrNudge.company_id == cid).all()
        for n in rows:
            if n.employee_id is None:
                continue
            u = db.query(User).filter(User.id == n.employee_id).first()
            if u and u.company_id != cid:
                bad.append(f"nudge {n.id}: company {cid}, employee in "
                           f"{u.company_id}")
        # Same for payroll runs.
        for r in db.query(PayrollRun).filter(
                PayrollRun.company_id == cid).all():
            from app.models.payroll import Payslip
            for s in db.query(Payslip).filter(Payslip.run_id == r.id).all():
                if s.company_id != cid:
                    bad.append(f"run {r.id}: company {cid}, payslip "
                               f"{s.id} in {s.company_id}")
    check("no nudge or payslip belongs to a different company than its "
          "subject", not bad, "; ".join(bad[:3]))

    # A suspended company must not have been touched at all.
    if suspended:
        sid = suspended[0]
        import datetime as _dt
        recent = db.query(HrNudge).filter(
            HrNudge.company_id == sid,
            HrNudge.sent_at >= _dt.datetime.utcnow() - _dt.timedelta(minutes=5)
        ).count()
        check(f"the suspended company {sid} produced no new work",
              recent == 0, f"{recent} nudge(s) in the last 5 minutes")

if _notify_was is None:
    os.environ.pop("NOTIFICATIONS_ENABLED", None)
else:
    os.environ["NOTIFICATIONS_ENABLED"] = _notify_was

print("\n" + "=" * 66)
print(f"  {len(fails)} FAILURE(S)" if fails
      else "  background jobs: every check passed")
for f in fails:
    print(f"   - {f}")
print("=" * 66)
raise SystemExit(1 if fails else 0)
