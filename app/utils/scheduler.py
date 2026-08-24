"""
Background Scheduler
────────────────────
Until now everything only happened when a user clicked something:
auto-approve ran only when someone opened a leave listing. If nobody
opened the app for a week, an employee's request just hung there.

This scheduler runs the jobs that should not have to wait for anyone to
show up.

No new library (no APScheduler, no Celery) — a daemon thread and some
timestamps are enough. At this scale that is the right call, and it is
easy to explain: one tick every 60 seconds, and any job whose time has
come runs.

RULES:
· Each job takes its OWN DB session and closes it
· A job that blows up is only logged — the scheduler never stops
· Everything is idempotent (running twice does no harm)
"""

import threading
import traceback
from datetime import datetime, timedelta
from typing import Callable, List

from app.utils.pkt import get_pkt_now

# How often to check which job is due
TICK_SECONDS = 60


class _Job:
    def __init__(self, func: Callable, minutes: int, name: str, run_at_start: bool):
        self.func = func
        self.interval = timedelta(minutes=minutes)
        self.name = name
        # Run as soon as the server starts, or after the first interval
        self.next_run = datetime.now() if run_at_start else datetime.now() + self.interval
        self.runs = 0
        self.failures = 0
        self.last_result = None


class Scheduler:
    def __init__(self):
        self._jobs: List[_Job] = []
        self._thread = None
        self._stop = threading.Event()

    def every(self, minutes: int, func: Callable, name: str = "",
              run_at_start: bool = False):
        """Register a job — it does not run yet, it only joins the list"""
        self._jobs.append(
            _Job(func, minutes, name or func.__name__, run_at_start)
        )
        return self

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="agentra-scheduler", daemon=True
        )
        self._thread.start()
        names = ", ".join(f"{j.name}({j.interval.seconds // 60}m)" for j in self._jobs)
        print(f"[scheduler] started — jobs: {names}")

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            now = datetime.now()
            for job in self._jobs:
                if now < job.next_run:
                    continue
                job.next_run = now + job.interval
                self._run(job)

            # wait() beats sleep — stop() breaks out of it immediately
            self._stop.wait(TICK_SECONDS)

    @staticmethod
    def _run(job: _Job):
        try:
            result = job.func()
            job.runs += 1
            job.last_result = result
            if result:
                print(f"[scheduler] {job.name}: {result}")
        except Exception as e:
            job.failures += 1
            print(f"[scheduler] {job.name} FAILED: {e}")
            traceback.print_exc()

    def status(self) -> list:
        return [
            {
                "name": j.name,
                "every_minutes": j.interval.seconds // 60,
                "next_run": j.next_run.isoformat(timespec="seconds"),
                "runs": j.runs,
                "failures": j.failures,
                "last_result": j.last_result,
            }
            for j in self._jobs
        ]


scheduler = Scheduler()


# ══════════════════════════════════════════════
# Jobs
# ══════════════════════════════════════════════
def job_process_overdue_leaves():
    """
    The CEO did not respond in time → approve automatically if the balance allows.

    This used to run only when someone opened a leave listing.
    Now it happens whether or not anyone opens the app.
    """
    from app.database import SessionLocal
    from app.models.attendance import LeaveRequest, LeaveStatusEnum
    from app.routes.leave import _auto_approve_overdue

    db = SessionLocal()
    try:
        companies = [
            row[0] for row in db.query(LeaveRequest.company_id)
            .filter(LeaveRequest.status == LeaveStatusEnum.pending)
            .distinct().all()
        ]

        total = 0
        for company_id in companies:
            try:
                total += _auto_approve_overdue(db, company_id)
            except Exception as e:
                db.rollback()
                print(f"[scheduler] company {company_id} auto-approve failed: {e}")

        return f"{total} auto-approved" if total else None
    finally:
        db.close()


# Remind the CEO this many hours before the deadline
REMINDER_BEFORE_HOURS = 4


def job_leave_reminders():
    """
    The deadline is close and the CEO has not responded → send an email.

    `reminder_sent_at` deduplicates it — otherwise the same reminder would
    go out again every 30 minutes.
    """
    from app.database import SessionLocal
    from app.models.attendance import LeaveRequest, LeaveStatusEnum, CompanyWorkPolicy
    from app.models.user import User
    from app.routes.leave import _auto_approve_hours, _leave_type_label
    from app.utils import notify

    db = SessionLocal()
    sent = 0

    try:
        now = get_pkt_now()

        companies = [
            row[0] for row in db.query(LeaveRequest.company_id)
            .filter(
                LeaveRequest.status == LeaveStatusEnum.pending,
                LeaveRequest.reminder_sent_at == None,
            ).distinct().all()
        ]

        for company_id in companies:
            policy = db.query(CompanyWorkPolicy).filter(
                CompanyWorkPolicy.company_id == company_id
            ).first()
            hours = _auto_approve_hours(policy)
            if hours == 0:
                continue          # auto-approve is off, so there is no deadline

            # ──── Those whose deadline falls within REMINDER_BEFORE_HOURS ────
            window_start = now - timedelta(hours=hours)
            window_end = window_start + timedelta(hours=REMINDER_BEFORE_HOURS)

            due = db.query(LeaveRequest).filter(
                LeaveRequest.company_id == company_id,
                LeaveRequest.status == LeaveStatusEnum.pending,
                LeaveRequest.reminder_sent_at == None,
                LeaveRequest.created_at <= window_end,
            ).all()

            if not due:
                continue

            ceo = db.query(User).filter(User.id == company_id).first()
            if not ceo or not ceo.email:
                continue

            employees = {
                e.id: e for e in db.query(User).filter(
                    User.id.in_([r.employee_id for r in due])
                ).all()
            }

            rows = []
            for r in due:
                emp = employees.get(r.employee_id)
                rows.append({
                    "employee_name": emp.full_name if emp else "Employee",
                    "leave_type": _leave_type_label(db, company_id, r.leave_type),
                    "start_date": r.start_date,
                    "end_date": r.end_date,
                    "deductible_days": r.deductible_days or r.total_days,
                })

            if notify.leave_reminder_to_ceo(
                ceo_email=ceo.email,
                ceo_name=ceo.full_name or "CEO",
                pending=rows,
                hours_left=REMINDER_BEFORE_HOURS,
                company=ceo.company_name or "",
            ):
                for r in due:
                    r.reminder_sent_at = now
                sent += len(due)

        if sent:
            db.commit()
        return f"{sent} reminder(s)" if sent else None

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def register_jobs():
    """Called from main.py"""
    scheduler.every(
        15, job_process_overdue_leaves, "overdue-leaves", run_at_start=True
    )
    scheduler.every(30, job_leave_reminders, "leave-reminders")

    # ──── The monthly payroll ────
    # It runs every 6 hours but does its work ONCE a month: it first
    # checks whether a run for that period already exists. It runs this
    # often so that a server outage does not make it miss the 1st.
    scheduler.every(6 * 60, job_monthly_payroll, "monthly-payroll",
                    run_at_start=True)
    return scheduler


# ══════════════════════════════════════════════
# Job 3: Run the monthly payroll automatically
# ══════════════════════════════════════════════
# So the CEO does not have to remember every month. But this is about
# money, so "run it automatically" does not mean "send it blindly".
#
# The rule: normally the CEO does nothing. But if anything looks
# SUSPICIOUS the run stops at `pending_approval` and the CEO is emailed.
# So they are involved only when there is a real problem.

# This much change from last month is suspicious (double or half)
SALARY_JUMP_RATIO = 2.0

# Payroll does not run before the 1st — that month's attendance is not
# complete yet
AUTO_RUN_FROM_DAY = 1


def _previous_period(today) -> str:
    """The previous month relative to today — "2026-05" """
    year, month = today.year, today.month
    month -= 1
    if month == 0:
        month, year = 12, year - 1
    return f"{year}-{month:02d}"


def _suspicious(db, company_id: int, period: str, run) -> list:
    """
    Is there anything in this run that needs a human eye?

    These are the three questions an HR manager would ask themselves:
      1. Did anyone's slip fail to build?
      2. Did anyone's net come out as zero?
      3. Did anyone's salary double or halve since last month?

    If any of these is true no email goes out — the CEO looks first.
    """
    from app.models.payroll import Payslip
    from app.models.user import User

    reasons = []

    if run.employees_failed:
        reasons.append(
            f"{run.employees_failed} employee slip(s) could not be produced "
            f"(is the salary structure set?)"
        )

    slips = db.query(Payslip).filter(
        Payslip.run_id == run.id, Payslip.status == "computed"
    ).all()

    names = {}
    if slips:
        names = {u.id: u.full_name for u in db.query(User).filter(
            User.id.in_([s.employee_id for s in slips])).all()}

    for s in slips:
        who = names.get(s.employee_id, f"#{s.employee_id}")

        if s.net_salary is None or s.net_salary <= 0:
            reasons.append(f"{who}'s net salary came out as zero")

        warns = (s.calculation_notes or {}).get("warnings") or []
        if warns:
            reasons.append(f"{who}: {warns[0]}")

        # Compare against the previous slip
        prev = db.query(Payslip).filter(
            Payslip.employee_id == s.employee_id,
            Payslip.period < period,
            Payslip.status != "cancelled",
        ).order_by(Payslip.period.desc()).first()

        if prev and prev.net_salary and prev.net_salary > 0 and s.net_salary:
            ratio = float(s.net_salary) / float(prev.net_salary)
            if ratio >= SALARY_JUMP_RATIO or ratio <= (1 / SALARY_JUMP_RATIO):
                reasons.append(
                    f"{who}'s salary changed by "
                    f"{ratio:.1f}x "
                    f"({prev.net_salary} to {s.net_salary})"
                )

    return reasons


def job_monthly_payroll():
    """
    Last month's payroll for each company — if it has not run yet.

    This job runs often but does its work ONCE a month: it first checks
    whether a run for that period already exists. It runs this often so
    that a server outage does not make it miss the 1st.
    """
    import asyncio

    from app.database import SessionLocal
    from app.models.user import User
    from app.models.payroll import PayrollRun, SalaryStructure
    from app.utils.pkt import get_pkt_now

    today = get_pkt_now().date()
    if today.day < AUTO_RUN_FROM_DAY:
        return

    period = _previous_period(today)
    db = SessionLocal()

    try:
        # Only companies that have actually set payroll up
        company_ids = [
            row[0] for row in db.query(SalaryStructure.company_id).distinct().all()
        ]

        for company_id in company_ids:
            existing = db.query(PayrollRun).filter(
                PayrollRun.company_id == company_id,
                PayrollRun.period == period,
                PayrollRun.status != "cancelled",
            ).first()
            if existing:
                continue          # this month's work is already done

            ceo = db.query(User).filter(User.id == company_id).first()
            if not ceo:
                continue

            print(f"[payroll] {period} — running payroll automatically for "
                  f"company {company_id}")

            try:
                run = _run_payroll_for(db, ceo, period)
            except Exception as e:
                print(f"[payroll] company {company_id} fail: {e}")
                db.rollback()
                continue

            if not run or not run.employees_done:
                print(f"[payroll] company {company_id}: no slip was produced")
                continue

            reasons = _suspicious(db, company_id, period, run)

            if reasons:
                # Hold — let the CEO look
                run.error_note = "Held automatically: " + " | ".join(reasons[:4])
                db.commit()
                print(f"[payroll] {period} HELD ({len(reasons)} reason(s)) — "
                      f"notified the CEO")
                _notify_hold(ceo, period, run, reasons)
            else:
                # All good — send the slips
                try:
                    asyncio.run(_approve_and_email(db, ceo, run))
                    print(f"[payroll] {period} auto-approved and emailed")
                except Exception as e:
                    run.error_note = f"Slips were produced, email failed: {e}"
                    db.commit()
                    print(f"[payroll] email fail: {e}")

    finally:
        db.close()


def _run_payroll_for(db, ceo, period: str):
    """
    Run payroll — the same logic that runs behind the CEO's button.

    The route's code is not duplicated: the same agent, the same
    aggregation. Only the HTTP layer is missing. Two places must never
    hone chahiyein.
    """
    from decimal import Decimal

    from app.agents.payroll_agent import run_for_employee
    from app.models.payroll import PayrollRun
    from app.utils.company import company_employees

    employees = company_employees(db, ceo)
    if not employees:
        return None

    run = PayrollRun(
        company_id=ceo.id, period=period, attempt=1,
        triggered_by="scheduler", status="processing",
        employees_total=len(employees),
    )
    db.add(run)
    db.flush()

    done = failed = 0
    gross = ded = net = Decimal("0.00")

    for emp in employees:
        out = run_for_employee(db, emp.id, ceo.id, period, run.id)
        if out["status"] == "computed":
            done += 1
            gross += out["gross_pay"]
            ded += out["total_deductions"]
            net += out["net_salary"]
        else:
            failed += 1

    run.employees_done = done
    run.employees_failed = failed
    run.total_gross = gross
    run.total_deductions = ded
    run.total_payroll_cost = net
    run.status = "pending_approval" if done else "failed"
    run.completed_at = datetime.now()
    db.commit()
    return run


async def _approve_and_email(db, ceo, run):
    """Email the slips and mark the run completed"""
    from app.models.payroll import Payslip
    from app.routes.payroll import _email_slips_via_mcp
    from app.utils import notify
    from app.utils.company import company_employees
    from app.utils.payroll_data import month_label

    slips = db.query(Payslip).filter(
        Payslip.run_id == run.id, Payslip.status == "computed"
    ).all()
    employees = {u.id: u for u in company_employees(db, ceo)}

    to_send = []
    for s in slips:
        emp = employees.get(s.employee_id)
        if not emp or not getattr(emp, "email", None) or not s.slip_pdf:
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

    outcome = {"ok": True, "results": {}}
    if to_send and notify.ENABLED:
        outcome = await _email_slips_via_mcp(
            getattr(ceo, "company_name", None) or "Company", to_send)

    for s in slips:
        res = (outcome.get("results") or {}).get(s.id)
        if res and res.get("sent"):
            s.status = "sent"
            s.email_sent_at = datetime.now()

    run.status = "completed"
    run.approved_at = datetime.now()
    run.error_note = None if outcome.get("ok") else (
        f"Slips are ready, email was not sent: {outcome.get('reason')}")
    db.commit()


def _notify_hold(ceo, period, run, reasons):
    """Tell the CEO the payroll is on hold, and WHY"""
    from app.utils import notify

    if not getattr(ceo, "email", None):
        return
    try:
        notify.send_email(
            to=ceo.email,
            subject=f"Payroll {period} - your response is needed",
            body=(
                f"Assalam-o-alaikum {ceo.full_name or 'CEO'},\n\n"
                f"Payroll for {period} ran automatically, but the slips were "
                f"NOT sent - a few things need your attention:\n\n"
                + "\n".join(f"  - {r}" for r in reasons[:6])
                + f"\n\n{run.employees_done} slips are ready. Open Payroll > Runs "
                f"on the dashboard, review them, and if they look right press Approve "
                f"dabayein.\n\n"
                f"If something is wrong, fix the salary structure and run payroll "
                f"again.\n\nAgentra"
            ),
        )
    except Exception as e:
        print(f"[payroll] could not notify the CEO: {e}")
