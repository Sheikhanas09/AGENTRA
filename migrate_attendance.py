"""
Attendance module migration
───────────────────────────
SQLAlchemy ka create_all() sirf NAYI tables banata hai —
purani table mein naye columns add nahi karta.
Yeh script:
  1. Naye columns add karta hai (idempotent — baar baar chala sakte ho)
  2. Indexes banata hai
  3. attendance_photos table banata hai
  4. Purani uploads/faces/ ki photos DB mein import karta hai

Run:  py migrate_attendance.py     (Backend/ folder se)
"""

import os

from sqlalchemy import text
from app.database import engine, SessionLocal, Base
from app.models.user import User          # noqa: F401  (FK target)
from app.models import attendance as attendance_models
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
    # ──── Break ka waqt aur muddat ────
    # Pehle sirf break_policy tha (katega/nahi). Employee ko yeh nahi
    # pata chalta tha ke break kab hai aur kitni der ki hai.
    ("company_work_policy", "break_minutes", "INTEGER DEFAULT 60"),
    ("company_work_policy", "break_start", "VARCHAR"),
    ("company_work_policy", "break_end", "VARCHAR"),

    # ──── Leave: working days jo balance se katenge ────
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
        # Enum mein sirf 5 fixed values thin. Company apni policy mein koi bhi
        # type rakh sakti hai (maternity, study, hajj...) — enum us raah mein
        # rukawat tha. `::text` cast se maujooda data bilkul mehfooz rehta hai.
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
                print(f"  [ok] {tbl}.leave_type pehle se VARCHAR")

        # ──── Intervals ka FK CASCADE karo ────
        # Warna session delete karte waqt "still referenced" error aata hai
        # (photos table already CASCADE hai — schema consistent rakho)
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
        # Ek employee ka ek din mein ek hi session hona chahiye.
        # Purana data mein duplicates ho sakte hain (check-out ke baad
        # dobara check-in allow tha) → sirf pehla wala rakho.
        dupes = conn.execute(text("""
            SELECT employee_id, date, COUNT(*) AS c
            FROM attendance_sessions
            GROUP BY employee_id, date
            HAVING COUNT(*) > 1
        """)).fetchall()

        if dupes:
            print(f"\n  [!] {len(dupes)} din mein duplicate sessions mile - merge nahi kiya,")
            print("      sirf report kar raha hoon (data safe rahe):")
            for d in dupes:
                print(f"      employee {d[0]} | {d[1]} | {d[2]} sessions")
        else:
            print("\n  [ok] Koi duplicate session nahi")

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

    backfill_photos()
    backfill_certificates()

    print("\n[DONE] Attendance + Leave migration complete!")


def backfill_certificates():
    """
    Purane medical certificates uploads/certificates/ mein padi files hain
    aur DB mein sirf path. Unhein DB mein import karo.
    Files delete NAHI karte — safety ke liye pade rehne do.
    """
    print("\n  Purane certificates import kar raha hoon...")

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
        print(f"  [ok] DB mein total {db.query(LeaveDocument).count()} certificates")

    finally:
        db.close()


def backfill_photos():
    """
    Purani photos uploads/faces/ folder mein padi hain aur DB mein sirf
    unka path hai. Unhein DB mein import karo taake sab ek jagah ho.
    Files delete NAHI karte — safety ke liye pade rehne do.
    """
    print("\n  Purani photos import kar raha hoon...")

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

                # ──── Pehle se import ho chuki? ────
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
        print(f"  [ok] DB mein total {db.query(AttendancePhoto).count()} photos ({total_kb} KB)")

    finally:
        db.close()


if __name__ == "__main__":
    run()
