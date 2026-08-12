"""
Background Scheduler
────────────────────
Abhi tak sab kuch tab hota tha jab koi user kuch dabata tha:
auto-approve sirf us waqt chalta tha jab koi leave listing kholta ho.
Agar hafte bhar koi app na khole to employee ki request latki rehti thi.

Yeh scheduler un kaamon ko chalata hai jinhein kisi ke aane ka intezar
nahi karna chahiye.

Koi nayi library nahi (APScheduler/Celery nahi) — ek daemon thread aur
timestamps kaafi hain. FYP scale par yehi sahi hai, aur viva mein samjhana
bhi asaan: har 60 second ek tick, jis job ka waqt aa gaya wo chal jata hai.

USOOL:
· Har job apni ALAG DB session leta hai aur band karta hai
· Job phate to sirf log — scheduler kabhi nahi rukta
· Sab kuch idempotent hai (dobara chale to nuqsan na ho)
"""

import threading
import traceback
from datetime import datetime, timedelta
from typing import Callable, List

from app.utils.pkt import get_pkt_now

# Har kitni der mein dekha jaye ke kis job ka waqt hua
TICK_SECONDS = 60


class _Job:
    def __init__(self, func: Callable, minutes: int, name: str, run_at_start: bool):
        self.func = func
        self.interval = timedelta(minutes=minutes)
        self.name = name
        # Server start hote hi chalana hai ya pehla interval guzarne ke baad
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
        """Job register karo — abhi chalta nahi, sirf list mein aata hai"""
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
        print(f"[scheduler] chal para — jobs: {names}")

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

            # wait() sleep se behtar — stop() par foran nikal jata hai
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
    CEO ne muqarrara waqt mein jawab nahi diya → balance ho to khud approve.

    Pehle yeh sirf tab chalta tha jab koi leave listing kholta tha.
    Ab koi app khole ya na khole, kaam ho jata hai.
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


# Deadline se itne ghante pehle CEO ko yaad dihani
REMINDER_BEFORE_HOURS = 4


def job_leave_reminders():
    """
    Deadline qareeb hai aur CEO ne abhi tak jawab nahi diya → email.

    `reminder_sent_at` se dedup hota hai — warna har 30 minute par wahi
    reminder dobara chala jata.
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
                continue          # auto-approve band hai, deadline hi nahi

            # ──── Jin ki deadline agle REMINDER_BEFORE_HOURS mein hai ────
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
    """main.py se bulaya jata hai"""
    scheduler.every(
        15, job_process_overdue_leaves, "overdue-leaves", run_at_start=True
    )
    scheduler.every(30, job_leave_reminders, "leave-reminders")
    return scheduler
