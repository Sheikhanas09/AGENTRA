from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date, Time, JSON,
    Enum, ForeignKey, Text, LargeBinary, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime
import enum

# ──── Enums ────
class BreakPolicyEnum(str, enum.Enum):
    excluded = "excluded"
    included = "included"

class AttendanceStatusEnum(str, enum.Enum):
    checked_in = "checked_in"
    paused = "paused"
    checked_out = "checked_out"
    missed = "missed"

class LeaveTypeEnum(str, enum.Enum):
    annual = "annual"
    casual = "casual"
    sick = "sick"
    unpaid = "unpaid"
    emergency = "emergency"

class LeaveStatusEnum(str, enum.Enum):
    evaluating = "evaluating"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"

class PolicyStatusEnum(str, enum.Enum):
    processing = "processing"
    active = "active"
    failed = "failed"
    superseded = "superseded"

class IntervalTypeEnum(str, enum.Enum):
    pause = "pause"
    resume = "resume"

class FaceEnrollmentStatusEnum(str, enum.Enum):
    active = "active"
    revoked = "revoked"

class PhotoKindEnum(str, enum.Enum):
    checkin = "checkin"
    checkout = "checkout"
    enrollment = "enrollment"


# ──── Table 1: Company Work Policy ────
class CompanyWorkPolicy(Base):
    __tablename__ = "company_work_policy"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, unique=True)
    working_days = Column(JSON, nullable=False)
    shift_start = Column(String, nullable=False)
    late_tolerance_mins = Column(Integer, default=15)
    shift_end = Column(String, nullable=False)

    # ──── Check-in window ────
    # Check-in is only possible during the shift.
    # Window: [shift_start - early_checkin_grace_mins  ...  shift_end]
    # Check-in is blocked after the shift ends → that person stays absent.
    # (No restriction on checkout — they may work as late as they like)
    enforce_shift_window = Column(Boolean, default=True)
    early_checkin_grace_mins = Column(Integer, default=60)

    # ──── Leave: how many hours the CEO has to respond ────
    # Every leave request goes to the CEO (there may be an important
    # meeting that day). If they do not answer within this many hours and
    # the balance allows, the request approves itself — nobody is left
    # hanging. 0 = never auto-approve (always wait for the CEO)
    leave_auto_approve_hours = Column(Integer, default=24)

    min_daily_hours = Column(Float, default=8.0)
    overtime_threshold = Column(Float, default=9.0)
    max_overtime_per_day = Column(Float, default=3.0)

    # ──── Break ────
    # `break_policy` only said whether the break comes off the net hours —
    # but the employee never learned WHEN the break is or HOW LONG it
    # lasts. All three facts are now kept separately:
    #
    #   break_policy   → deducted or not (excluded / included)
    #   break_minutes  → how long is allowed
    #   break_start/end→ if the time is fixed (e.g. 01:00 PM – 02:00 PM)
    #
    # Leaving start/end empty means: "this many minutes, whenever you
    # like" (a flexible break). Either way `break_minutes` is the limit.
    break_policy = Column(Enum(BreakPolicyEnum), default=BreakPolicyEnum.excluded)
    break_minutes = Column(Integer, default=60)
    break_start = Column(String, nullable=True)
    break_end = Column(String, nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ──── Table 2: Company Policies (Document) ────
class CompanyPolicy(Base):
    __tablename__ = "company_policies"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    policy_label = Column(String, nullable=True)
    effective_from = Column(Date, nullable=True)
    status = Column(Enum(PolicyStatusEnum), default=PolicyStatusEnum.processing)
    chunks_indexed = Column(Integer, default=0)
    policy_preview = Column(JSON, nullable=True)
    vector_collection = Column(String, nullable=True)
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    indexed_at = Column(DateTime, nullable=True)


# ──── Table 3: Face Enrollment ────
class FaceEnrollment(Base):
    __tablename__ = "face_enrollment"

    id = Column(Integer, primary_key=True, index=True)

    # ══════════════════════════════════════════════
    # The tenant column
    # ══════════════════════════════════════════════
    # This table reached its company only through its parent row. The
    # routes do look the parent up first and that lookup IS scoped, so
    # there was no known way in — but that is a fact about today's
    # routes. A table without `company_id` is one NEITHER wall can
    # protect: the ORM guard skips it, and no row-level-security policy
    # can be written for it.
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )

    employee_id = Column(Integer, ForeignKey("users.id"), unique=True)
    embedding = Column(JSON, nullable=False)
    enrolled_at = Column(DateTime, default=datetime.utcnow)
    enrolled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(Enum(FaceEnrollmentStatusEnum), default=FaceEnrollmentStatusEnum.active)


# ──── Table 4: Attendance Sessions ────
class AttendanceSession(Base):
    __tablename__ = "attendance_sessions"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)

    # ──── Check-in ────
    check_in_time = Column(DateTime, nullable=True)
    check_in_face_image = Column(String, nullable=True)
    check_in_lat = Column(Float, nullable=True)
    check_in_lng = Column(Float, nullable=True)
    check_in_verified = Column(Boolean, default=False)
    is_late = Column(Boolean, default=False)
    late_by_minutes = Column(Integer, default=0)

    # ──── Location Verified ────
    location_verified = Column(Boolean, default=False)
    checkout_location_verified = Column(Boolean, default=False)
    # ↑ Is the GPS reading inside the office range?

    # ──── GPS Audit (distance + browser accuracy) ────
    check_in_distance_meters = Column(Float, nullable=True)
    check_in_gps_accuracy = Column(Float, nullable=True)
    check_in_location_note = Column(String, nullable=True)
    check_out_distance_meters = Column(Float, nullable=True)
    check_out_gps_accuracy = Column(Float, nullable=True)
    check_out_location_note = Column(String, nullable=True)

    # ──── Check-out ────
    check_out_time = Column(DateTime, nullable=True)
    check_out_face_image = Column(String, nullable=True)
    check_out_lat = Column(Float, nullable=True)
    check_out_lng = Column(Float, nullable=True)
    check_out_verified = Column(Boolean, default=False)

    # ──── Hours ────
    gross_hours = Column(Float, nullable=True)
    total_pause_minutes = Column(Integer, default=0)
    net_hours = Column(Float, nullable=True)

    # ──── Flags ────
    is_undertime = Column(Boolean, default=False)
    undertime_minutes = Column(Integer, default=0)
    is_overtime = Column(Boolean, default=False)
    overtime_minutes = Column(Integer, default=0)

    # ──── Did they leave before the shift ended? (policy.shift_end) ────
    is_early_checkout = Column(Boolean, default=False)
    early_checkout_minutes = Column(Integer, default=0)

    # ──── Policy Snapshot ────
    policy_snapshot = Column(JSON, nullable=True)

    # ──── A working day? (from the policy's working_days) ────
    is_working_day = Column(Boolean, default=True)

    status = Column(Enum(AttendanceStatusEnum), default=AttendanceStatusEnum.checked_in)
    created_at = Column(DateTime, default=datetime.utcnow)

    intervals = relationship("AttendanceInterval", back_populates="session")


# ──── Table 5: Attendance Intervals ────
class AttendanceInterval(Base):
    __tablename__ = "attendance_intervals"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        Integer,
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"),
        nullable=False
    )
    employee_id = Column(Integer, nullable=False)
    type = Column(Enum(IntervalTypeEnum), nullable=False)
    # ══════════════════════════════════════════════
    # The tenant column
    # ══════════════════════════════════════════════
    # This table reached its company only through its parent row. In
    # practice the routes look the parent up first and that lookup is
    # scoped, so there was no known way in — but "no known way in" is a
    # fact about today's routes. A table without `company_id` is one
    # NEITHER wall can protect: the ORM guard skips it, and no
    # row-level-security policy can be written for it.
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Float, nullable=True)

    session = relationship("AttendanceSession", back_populates="intervals")


# ──── Table 5b: Attendance Photos (binary, in the DB) ────
# Photos used to sit only in the uploads/faces/ folder with just a path in
# the DB — delete or move the file and the record was empty.
# The actual bytes now live in the DB (and travel with the backup).
#
# Why a SEPARATE table (and not a column on attendance_sessions)?
# The dashboard SELECTs the whole team's sessions on every refresh — with
# the image bytes in that row, every listing would drag megabytes along.
# A separate table = fast listings, and the photo loads only when asked for.
class AttendancePhoto(Base):
    __tablename__ = "attendance_photos"

    id = Column(Integer, primary_key=True, index=True)

    # ──── An enrolment photo has no session → nullable ────
    session_id = Column(
        Integer,
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"),
        nullable=True
    )
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, nullable=False)

    kind = Column(Enum(PhotoKindEnum), nullable=False)

    # ──── The actual image (compressed JPEG) ────
    image_data = Column(LargeBinary, nullable=False)
    mime_type = Column(String, default="image/jpeg")

    # ──── Metadata — audit + integrity ────
    file_size_bytes = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)

    captured_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # ──── One check-in and one check-out photo per session ────
        UniqueConstraint("session_id", "kind", name="uq_photo_session_kind"),
        Index("ix_photo_session_kind", "session_id", "kind"),
        Index("ix_photo_employee_kind", "employee_id", "kind"),
    )


# ──── Table 5c: Company Leave Types ────
# Every company has its own leave types. Five types used to be hardcoded
# (annual/casual/sick/unpaid/emergency) — but some companies have no
# "casual" at all, and others also have "maternity" or "study leave".
# The types now come from the DB.
#
# A type missing from the policy document gets an entitlement of 0 — the
# card still shows (0/0) so the employee can see it exists but is not
# allowed.
class CompanyLeaveType(Base):
    __tablename__ = "company_leave_types"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)

    code = Column(String, nullable=False)          # "annual", "maternity"
    label = Column(String, nullable=False)         # "Annual Leave"

    default_entitlement = Column(Integer, default=0)
    is_unlimited = Column(Boolean, default=False)  # like unpaid leave

    # ──── Is this leave paid? ────
    # This is the one question payroll has to ask. The system used to guess
    # from the CODE alone (anything named "unpaid" is unpaid) — but a type
    # coming from a policy document can be named anything
    # ("leave without pay", "sabbatical").
    #
    # Stored on the TYPE, not on the request — because this is a company
    # rule, not a per-request decision. And if the CEO changes the rule
    # later, old slips are unaffected: each payslip keeps its own
    # `attendance_snapshot`.
    is_paid = Column(Boolean, default=True, nullable=False)

    # ──── This type's own rules ────
    requires_certificate = Column(Boolean, default=False)
    advance_notice_days = Column(Integer, default=1)
    # ↑ 0 = can be applied for the same day (sick/emergency)

    is_enabled = Column(Boolean, default=True)
    # ↑ False = the employee never sees it

    source = Column(String, default="default")
    # ↑ default = system ne banayi
    #   policy  = extracted from the policy document
    #   manual  = created by the CEO

    policy_reference = Column(Text, nullable=True)
    # ↑ Which line of the document it came from — so the CEO can verify

    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_company_leave_type"),
        Index("ix_company_leave_type", "company_id", "is_enabled"),
    )


# ──── Table 6: Leave Requests ────
class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, nullable=False)
    leave_type = Column(String, nullable=False)
    # ↑ Changed from Enum to String — a company may have any leave type in
    #   its policy (maternity, study, hajj...). A fixed enum stood in the
    #   way. The valid types live in company_leave_types.
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    total_days = Column(Integer, nullable=False)
    # ↑ Calendar days (start to end, both inclusive) — for display only

    deductible_days = Column(Integer, nullable=True)
    # ↑ How many of those are WORKING days — only these come off the
    #   balance. In leave from Friday to Monday, Sat/Sun were off anyway,
    #   so charging balance for them would be wrong.

    reason = Column(Text, nullable=True)
    medical_certificate = Column(String, nullable=True)
    status = Column(Enum(LeaveStatusEnum), default=LeaveStatusEnum.evaluating)
    auto_approved = Column(Boolean, default=False)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    ceo_note = Column(Text, nullable=True)
    payroll_notified = Column(Boolean, default=False)

    reminder_sent_at = Column(DateTime, nullable=True)
    # ↑ When the deadline reminder was sent to the CEO — otherwise the
    #   scheduler would resend the same reminder every 15 minutes

    created_at = Column(DateTime, default=datetime.utcnow)


# ──── Table 6b: Leave Documents (binary, in the DB) ────
# A medical certificate used to be written into uploads/certificates/ with
# only the path in the DB — and the CEO had no way to view it at all.
# The bytes now live in the DB (the same approach as attendance_photos).
#
# A separate table because the leave list SELECTs every request each time —
# with the bytes in that row, every listing would drag megabytes along.
class LeaveDocument(Base):
    __tablename__ = "leave_documents"

    id = Column(Integer, primary_key=True, index=True)

    leave_request_id = Column(
        Integer,
        ForeignKey("leave_requests.id", ondelete="CASCADE"),
        nullable=False
    )
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, nullable=False)

    doc_type = Column(String, default="medical_certificate")

    # ──── The actual file (images compressed, PDFs untouched) ────
    file_data = Column(LargeBinary, nullable=False)
    file_name = Column(String, nullable=True)
    mime_type = Column(String, default="application/pdf")

    file_size_bytes = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)

    uploaded_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_leave_doc_request", "leave_request_id"),
        Index("ix_leave_doc_employee", "employee_id"),
    )


# ──── Table 7: Leave Balances ────
class LeaveBalance(Base):
    __tablename__ = "leave_balances"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    leave_type = Column(String, nullable=False)
    # ↑ Changed from Enum to String — a company may have any leave type in
    #   its policy (maternity, study, hajj...). A fixed enum stood in the
    #   way. The valid types live in company_leave_types.
    total_entitlement = Column(Integer, default=0)
    used_days = Column(Integer, default=0)
    remaining_days = Column(Integer, default=0)
    last_updated = Column(DateTime, default=datetime.utcnow)


# ──── Table 8: Policy Decisions Log ────
class PolicyDecisionLog(Base):
    __tablename__ = "policy_decisions_log"

    id = Column(Integer, primary_key=True, index=True)

    # ══════════════════════════════════════════════
    # The tenant column
    # ══════════════════════════════════════════════
    # The audit trail of leave decisions — it holds the retrieved policy
    # text and the prompt sent to the model, which is company material.
    # It reached its company only through the leave request, so neither
    # the ORM guard nor a row-level-security policy could cover it.
    company_id = Column(
        Integer, ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )

    leave_request_id = Column(Integer, ForeignKey("leave_requests.id"), nullable=False)
    policy_id = Column(Integer, ForeignKey("company_policies.id"), nullable=True)
    retrieval_query = Column(Text, nullable=True)
    retrieved_chunks = Column(JSON, nullable=True)
    llm_prompt = Column(Text, nullable=True)
    llm_response = Column(JSON, nullable=True)
    decision = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    policy_reference = Column(Text, nullable=True)
    decided_at = Column(DateTime, default=datetime.utcnow)


# ──── Table 9: Policy Overrides ────
class CompanyPolicyOverride(Base):
    __tablename__ = "company_policy_overrides"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)
    leave_type = Column(String, nullable=False)
    # ↑ Changed from Enum to String — a company may have any leave type in
    #   its policy (maternity, study, hajj...). A fixed enum stood in the
    #   way. The valid types live in company_leave_types.
    force_manual = Column(Boolean, default=False)
    reason = Column(Text, nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    set_at = Column(DateTime, default=datetime.utcnow)
    cleared_at = Column(DateTime, nullable=True)


# ──── Table 10: Office Location ────
# ↑ Modular — the CEO can change it
# One office location per company
class OfficeLocation(Base):
    __tablename__ = "office_locations"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, unique=True)

    office_name = Column(String, default="Head Office")
    # ↑ "Head Office", "Branch 1", etc.

    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    # ↑ GPS coordinates

    radius_meters = Column(Integer, default=200)
    # ↑ Valid within this many metres
    # 200m = default
    # The CEO can change this from Settings

    is_active = Column(Boolean, default=True)

    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)