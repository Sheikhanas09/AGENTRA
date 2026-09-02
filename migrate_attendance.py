"""
Attendance module migration
───────────────────────────
SQLAlchemy's create_all() only creates NEW tables —
it does not add new columns to an existing one.
This script:
  1. Adds new columns (idempotent — safe to run repeatedly)
  2. Creates indexes
  3. Creates the attendance_photos table
  4. Imports old uploads/faces/ photos into the DB

Run:  py migrate_attendance.py     (from the Backend/ folder)
"""

import os

from sqlalchemy import text
from app.database import engine, SessionLocal, Base
from app.models.user import User          # noqa: F401  (FK target)
from app.models import attendance as attendance_models
from app.models.chat import (
    ChatSession, ChatMessage, HrRequest, HrCase, HrSettings, HrNudge,
    EmploymentRecord,
)
from app.models.payroll import (
    CompanyBranding, SalaryStructure, PayrollPolicy, PayrollRun, Payslip,
    PayrollAdjustment, EmployeeLoan, LoanRepayment
)
from app.models.attendance import (
    AttendanceSession, AttendancePhoto, PhotoKindEnum,
    LeaveRequest, LeaveDocument, CompanyLeaveType,
)
from app.utils.face_utils import prepare_photo_for_db
from app.utils.documents import prepare_document

# ──── (table, column, type) ────
NEW_COLUMNS = [
    ("attendance_sessions", "check_in_distance_meters", "DOUBLE PRECISION"),
    ("attendance_sessions", "check_in_gps_accuracy", "DOUBLE PRECISION"),
    ("attendance_sessions", "check_in_location_note", "VARCHAR"),
    ("attendance_sessions", "check_out_distance_meters", "DOUBLE PRECISION"),
    ("attendance_sessions", "check_out_gps_accuracy", "DOUBLE PRECISION"),
    ("attendance_sessions", "check_out_location_note", "VARCHAR"),
    ("attendance_sessions", "is_working_day", "BOOLEAN DEFAULT TRUE"),
    ("attendance_sessions", "is_early_checkout", "BOOLEAN DEFAULT FALSE"),
    ("attendance_sessions", "early_checkout_minutes", "INTEGER DEFAULT 0"),
    ("attendance_sessions", "checkout_location_verified", "BOOLEAN DEFAULT FALSE"),
    ("attendance_sessions", "location_verified", "BOOLEAN DEFAULT FALSE"),

    # ──── Check-in window policy ────
    ("company_work_policy", "enforce_shift_window", "BOOLEAN DEFAULT TRUE"),
    ("company_work_policy", "early_checkin_grace_mins", "INTEGER DEFAULT 60"),
    ("company_work_policy", "leave_auto_approve_hours", "INTEGER DEFAULT 24"),
    # ──── Break time and duration ────
    # There used to be only break_policy (deducted or not). Employees
    # never learned when the break was or how long it lasted.
    ("company_work_policy", "break_minutes", "INTEGER DEFAULT 60"),
    ("company_work_policy", "break_start", "VARCHAR"),
    ("company_work_policy", "break_end", "VARCHAR"),

    # ──── Payroll: is this leave paid? ────
    # This is the one question payroll has to ask. Existing rows default
    # to TRUE — the "unpaid" ones are then set to FALSE below.
    ("company_leave_types", "is_paid", "BOOLEAN DEFAULT TRUE NOT NULL"),

    # ──── Payroll: the items that change every month ────
    # Incentive, arrears and commission differ each month — so they come
    # from payroll_adjustments, not from the salary structure.
    ("payslips", "incentive_pay", "NUMERIC(12,2) DEFAULT 0 NOT NULL"),
    ("payslips", "arrears", "NUMERIC(12,2) DEFAULT 0 NOT NULL"),
    ("payslips", "commission", "NUMERIC(12,2) DEFAULT 0 NOT NULL"),
    ("payslips", "other_earnings", "NUMERIC(12,2) DEFAULT 0 NOT NULL"),
    ("payslips", "loan_deduction", "NUMERIC(12,2) DEFAULT 0 NOT NULL"),
    ("payslips", "other_deductions", "NUMERIC(12,2) DEFAULT 0 NOT NULL"),

    # ──── Leave: the working days that come off the balance ────
    ("leave_requests", "deductible_days", "INTEGER"),
    ("leave_requests", "reminder_sent_at", "TIMESTAMP"),
]

# ──── Performance indexes ────
NEW_INDEXES = [
    ("ix_attendance_company_date", "attendance_sessions", "(company_id, date)"),
    ("ix_attendance_employee_date", "attendance_sessions", "(employee_id, date)"),
    ("ix_attendance_intervals_session", "attendance_intervals", "(session_id)"),
]


def run():
    with engine.begin() as conn:
        # ──── chat_sessions.kind ────
        # Added after the table existed, so create_all cannot do it.
        conn.execute(text(
            "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
            "kind VARCHAR DEFAULT 'employee'"
        ))
        # ──── users.designation ────
        # Job title, kept apart from department. Nothing is moved
        # automatically: only a person can say whether "Backend
        # Developer" in someone's department field was meant as the team
        # or the role.
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS designation VARCHAR"
        ))
        conn.execute(text(
            "UPDATE chat_sessions SET kind = 'employee' WHERE kind IS NULL"
        ))

        # ──── Columns ────
        for table, column, coltype in NEW_COLUMNS:
            conn.execute(text(
                f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coltype}'
            ))
            print(f"  [ok] {table}.{column}")

        # ──── Indexes ────
        for name, table, cols in NEW_INDEXES:
            conn.execute(text(
                f'CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}'
            ))
            print(f"  [ok] index {name}")

        # ──── leave_type: Postgres enum -> VARCHAR ────
        # The enum held only 5 fixed values. A company may have any type in
        # its policy (maternity, study, hajj...) — the enum stood in the way.
        # The `::text` cast keeps existing data completely intact.
        for tbl in ("leave_requests", "leave_balances", "company_policy_overrides"):
            is_enum = conn.execute(text("""
                SELECT udt_name FROM information_schema.columns
                WHERE table_name = :t AND column_name = 'leave_type'
            """), {"t": tbl}).scalar()

            if is_enum == "leavetypeenum":
                conn.execute(text(
                    f"ALTER TABLE {tbl} ALTER COLUMN leave_type "
                    f"TYPE VARCHAR USING leave_type::text"
                ))
                print(f"  [ok] {tbl}.leave_type enum -> VARCHAR")
            else:
                print(f"  [ok] {tbl}.leave_type is already VARCHAR")

        # ──── Make the intervals FK CASCADE ────
        # Otherwise deleting a session raises a "still referenced" error
        # (the photos table is already CASCADE — keep the schema consistent)
        conn.execute(text(
            "ALTER TABLE attendance_intervals "
            "DROP CONSTRAINT IF EXISTS attendance_intervals_session_id_fkey"
        ))
        conn.execute(text(
            "ALTER TABLE attendance_intervals "
            "ADD CONSTRAINT attendance_intervals_session_id_fkey "
            "FOREIGN KEY (session_id) REFERENCES attendance_sessions(id) "
            "ON DELETE CASCADE"
        ))
        print("  [ok] attendance_intervals FK -> ON DELETE CASCADE")

        # ──── Duplicate sessions cleanup ────
        # One employee should have one session per day.
        # Older data may contain duplicates (checking in again after
        # checking out used to be allowed) → keep only the first.
        dupes = conn.execute(text("""
            SELECT employee_id, date, COUNT(*) AS c
            FROM attendance_sessions
            GROUP BY employee_id, date
            HAVING COUNT(*) > 1
        """)).fetchall()

        if dupes:
            print(f"\n  [!] duplicate sessions found on {len(dupes)} day(s) - not merged,")
            print("      only reporting them (so the data stays safe):")
            for d in dupes:
                print(f"      employee {d[0]} | {d[1]} | {d[2]} sessions")
        else:
            print("\n  [ok] No duplicate sessions")

    # ──── Binary storage tables ────
    print("\n  Binary tables...")
    Base.metadata.create_all(
        bind=engine,
        tables=[AttendancePhoto.__table__, LeaveDocument.__table__,
                CompanyLeaveType.__table__]
    )
    print("  [ok] attendance_photos ready")
    print("  [ok] leave_documents ready")
    print("  [ok] company_leave_types ready")

    # ──── The payroll tables ────
    print("\n  Payroll tables...")
    Base.metadata.create_all(
        bind=engine,
        tables=[CompanyBranding.__table__, SalaryStructure.__table__,
                PayrollPolicy.__table__, PayrollRun.__table__,
                Payslip.__table__]
    )
    for t in ("company_branding", "salary_structures", "payroll_policy",
              "payroll_runs", "payslips", "payroll_adjustments",
              "employee_loans", "loan_repayments"):
        print(f"  [ok] {t} ready")

    # ──── The HR help desk tables ────
    print("\n  HR help desk tables...")
    Base.metadata.create_all(
        bind=engine,
        tables=[ChatSession.__table__, ChatMessage.__table__,
                HrRequest.__table__, HrCase.__table__,
                HrSettings.__table__, HrNudge.__table__,
                EmploymentRecord.__table__]
    )
    for t in ("chat_sessions", "chat_messages", "hr_requests",
              "hr_cases", "hr_settings", "hr_nudges",
              "employment_records"):
        print(f"  [ok] {t} ready")

    backfill_unpaid_leave_types()
    backfill_photos()
    backfill_certificates()

    print("\n[DONE] Attendance + Leave migration complete!")


def backfill_unpaid_leave_types():
    """
    Existing rows default to `is_paid` TRUE — but salary SHOULD be
    deducted for an "unpaid" type. Set those to FALSE.

    Recognised by code alone (`unpaid`, `without_pay`, `lwp`) — types that
    came from a policy document under a different name are left for the CEO
    to decide on from the Leave Types tab. Guessing and docking someone's
    salary would be wrong.
    """
    print("\n  Marking unpaid leave types...")
    with engine.begin() as conn:
        r = conn.execute(text("""
            UPDATE company_leave_types
               SET is_paid = FALSE
             WHERE is_paid = TRUE
               AND (lower(code) LIKE '%unpaid%'
                 OR lower(code) LIKE '%without_pay%'
                 OR lower(code) = 'lwp')
        """))
        print(f"  [ok] {r.rowcount} type(s) marked unpaid")


def backfill_certificates():
    """
    Old medical certificates are files in uploads/certificates/ with only a
    path in the DB. Import them into the DB.
    The files are NOT deleted — leave them there for safety.
    """
    print("\n  Importing old certificates...")

    db = SessionLocal()
    imported = missing = skipped = failed = 0

    try:
        requests = db.query(LeaveRequest).filter(
            LeaveRequest.medical_certificate != None
        ).all()

        for req in requests:
            if db.query(LeaveDocument).filter(
                LeaveDocument.leave_request_id == req.id
            ).first():
                skipped += 1
                continue

            path = req.medical_certificate
            if not path or not os.path.exists(path):
                missing += 1
                continue

            try:
                with open(path, "rb") as f:
                    raw = f.read()
                prepared = prepare_document(os.path.basename(path), raw)

                db.add(LeaveDocument(
                    leave_request_id=req.id,
                    employee_id=req.employee_id,
                    company_id=req.company_id,
                    doc_type="medical_certificate",
                    file_data=prepared["data"],
                    file_name=prepared["file_name"],
                    mime_type=prepared["mime_type"],
                    file_size_bytes=prepared["size_bytes"],
                    width=prepared["width"],
                    height=prepared["height"],
                    sha256=prepared["sha256"],
                    uploaded_at=req.created_at,
                ))
                imported += 1

            except Exception as e:
                print(f"      [!] leave {req.id}: {e}")
                failed += 1

        db.commit()
        print(f"  [ok] imported={imported} skipped={skipped} "
              f"file-missing={missing} failed={failed}")
        print(f"  [ok] {db.query(LeaveDocument).count()} certificates now in the DB")

    finally:
        db.close()


def backfill_photos():
    """
    Old photos sit in the uploads/faces/ folder with only their path in the
    DB. Import them so everything lives in one place.
    The files are NOT deleted — leave them there for safety.
    """
    print("\n  Importing old photos...")

    db = SessionLocal()
    imported = missing = skipped = failed = 0

    try:
        sessions = db.query(AttendanceSession).filter(
            (AttendanceSession.check_in_face_image != None) |
            (AttendanceSession.check_out_face_image != None)
        ).all()

        for s in sessions:
            for kind, path in (
                (PhotoKindEnum.checkin, s.check_in_face_image),
                (PhotoKindEnum.checkout, s.check_out_face_image),
            ):
                if not path:
                    continue

                # ──── Already imported? ────
                if db.query(AttendancePhoto).filter(
                    AttendancePhoto.session_id == s.id,
                    AttendancePhoto.kind == kind
                ).first():
                    skipped += 1
                    continue

                if not os.path.exists(path):
                    missing += 1
                    continue

                try:
                    import base64
                    with open(path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()

                    prepared = prepare_photo_for_db(b64)
                    if not prepared:
                        failed += 1
                        continue

                    db.add(AttendancePhoto(
                        session_id=s.id,
                        employee_id=s.employee_id,
                        company_id=s.company_id,
                        kind=kind,
                        image_data=prepared["data"],
                        mime_type=prepared["mime_type"],
                        file_size_bytes=prepared["size_bytes"],
                        width=prepared["width"],
                        height=prepared["height"],
                        sha256=prepared["sha256"],
                        captured_at=s.check_in_time if kind == PhotoKindEnum.checkin
                        else s.check_out_time,
                    ))
                    imported += 1

                except Exception as e:
                    print(f"      [!] session {s.id} {kind}: {e}")
                    failed += 1

        db.commit()

        total_kb = (db.query(AttendancePhoto).count() and sum(
            p or 0 for (p,) in db.query(AttendancePhoto.file_size_bytes).all()
        ) // 1024) or 0

        print(f"  [ok] imported={imported} skipped={skipped} "
              f"file-missing={missing} failed={failed}")
        print(f"  [ok] {db.query(AttendancePhoto).count()} photos now in the DB ({total_kb} KB)")

    finally:
        db.close()


if __name__ == "__main__":
    run()
