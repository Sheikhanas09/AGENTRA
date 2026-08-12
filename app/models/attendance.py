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
    # Check-in sirf shift ke darmiyan ho sakta hai.
    # Window: [shift_start - early_checkin_grace_mins  ...  shift_end]
    # Shift end ke baad check-in block → banda absent rehta hai.
    # (Checkout pe koi pabandi nahi — der tak kaam kar sakta hai)
    enforce_shift_window = Column(Boolean, default=True)
    early_checkin_grace_mins = Column(Integer, default=60)

    # ──── Leave: CEO kitne ghante mein jawab de ────
    # Har leave request CEO ke paas jati hai (us din koi zaroori meeting
    # ho sakti hai). Itne ghante tak CEO jawab na de aur balance maujood
    # ho to request khud approve ho jati hai — employee latka na rahe.
    # 0 = kabhi auto-approve mat karo (hamesha CEO ka intezar)
    leave_auto_approve_hours = Column(Integer, default=24)

    min_daily_hours = Column(Float, default=8.0)
    overtime_threshold = Column(Float, default=9.0)
    max_overtime_per_day = Column(Float, default=3.0)

    # ──── Break ────
    # `break_policy` sirf yeh batata tha ke break net hours se katega ya
    # nahi — magar employee ko yeh maloom hi nahi hota tha ke break KAB
    # hai aur KITNI der ki hai. Ab teenon baatein alag alag rakhi hain:
    #
    #   break_policy   → katega ya nahi (excluded / included)
    #   break_minutes  → kitni der ki ijazat hai
    #   break_start/end→ agar waqt muqarrar hai (misal 01:00 PM – 02:00 PM)
    #
    # Start/end khali chhor dein to matlab: "itne minute, jab chahein"
    # (flexible break). Dono soorton mein `break_minutes` hi hadd hai.
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
    # ↑ GPS office range mein hai?

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

    # ──── Shift end se pehle nikal gaya? (policy.shift_end) ────
    is_early_checkout = Column(Boolean, default=False)
    early_checkout_minutes = Column(Integer, default=0)

    # ──── Policy Snapshot ────
    policy_snapshot = Column(JSON, nullable=True)

    # ──── Working day? (policy ke working_days se) ────
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
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Float, nullable=True)

    session = relationship("AttendanceSession", back_populates="intervals")


# ──── Table 5b: Attendance Photos (DB mein binary) ────
# Pehle photos sirf uploads/faces/ folder mein padi rehti thin aur DB mein
# sirf path save hota tha — file delete/move ho jaye to record khali.
# Ab actual bytes DB mein hain (backup ke saath chali jaati hain).
#
# ALAG table kyun (attendance_sessions mein column q nahi)?
# Dashboard har baar poori team ki sessions SELECT karta hai — agar image
# bytes usi row mein hote to har listing ke saath megabytes uthte.
# Alag table = listing fast, photo sirf tab load hoti hai jab maangi jaye.
class AttendancePhoto(Base):
    __tablename__ = "attendance_photos"

    id = Column(Integer, primary_key=True, index=True)

    # ──── Enrollment photo ka koi session nahi hota → nullable ────
    session_id = Column(
        Integer,
        ForeignKey("attendance_sessions.id", ondelete="CASCADE"),
        nullable=True
    )
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    company_id = Column(Integer, nullable=False)

    kind = Column(Enum(PhotoKindEnum), nullable=False)

    # ──── Asal image (compressed JPEG) ────
    image_data = Column(LargeBinary, nullable=False)
    mime_type = Column(String, default="image/jpeg")

    # ──── Metadata — audit + integrity ────
    file_size_bytes = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)

    captured_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # ──── Ek session ki ek hi check-in aur ek hi check-out photo ────
        UniqueConstraint("session_id", "kind", name="uq_photo_session_kind"),
        Index("ix_photo_session_kind", "session_id", "kind"),
        Index("ix_photo_employee_kind", "employee_id", "kind"),
    )


# ──── Table 5c: Company Leave Types ────
# Har company ki apni leave types hoti hain. Pehle 5 types code mein
# hardcoded thin (annual/casual/sick/unpaid/emergency) — magar kisi company
# ki policy mein "casual" hai hi nahi, aur kisi ke paas "maternity" ya
# "study leave" bhi hai. Ab types DB se aati hain.
#
# Jo type policy document mein na ho uska entitlement 0 rakha jata hai —
# card phir bhi dikhta hai (0/0) taake employee ko pata chale ke maujood
# to hai magar allowed nahi.
class CompanyLeaveType(Base):
    __tablename__ = "company_leave_types"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False)

    code = Column(String, nullable=False)          # "annual", "maternity"
    label = Column(String, nullable=False)         # "Annual Leave"

    default_entitlement = Column(Integer, default=0)
    is_unlimited = Column(Boolean, default=False)  # unpaid jaisi

    # ──── Is type ke apne rules ────
    requires_certificate = Column(Boolean, default=False)
    advance_notice_days = Column(Integer, default=1)
    # ↑ 0 = usi din apply ho sakti hai (sick/emergency)

    is_enabled = Column(Boolean, default=True)
    # ↑ False = employee ko dikhti hi nahi

    source = Column(String, default="default")
    # ↑ default = system ne banayi
    #   policy  = policy document se nikli
    #   manual  = CEO ne khud banayi

    policy_reference = Column(Text, nullable=True)
    # ↑ Document ki kaunsi line se aayi — CEO verify kar sake

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
    # ↑ Enum se String kiya — company apni policy mein koi bhi leave type
    #   rakh sakti hai (maternity, study, hajj...). Fixed enum us raah
    #   mein rukawat tha. Chalne wale types company_leave_types mein hain.
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    total_days = Column(Integer, nullable=False)
    # ↑ Calendar days (start se end tak, dono shamil) — sirf dikhane ke liye

    deductible_days = Column(Integer, nullable=True)
    # ↑ In mein se kitne WORKING days hain — balance sirf yeh katta hai.
    #   Friday se Monday ki chhutti mein Sat/Sun waise hi off hote hain,
    #   unka balance kaatna galat hai.

    reason = Column(Text, nullable=True)
    medical_certificate = Column(String, nullable=True)
    status = Column(Enum(LeaveStatusEnum), default=LeaveStatusEnum.evaluating)
    auto_approved = Column(Boolean, default=False)
    decided_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    ceo_note = Column(Text, nullable=True)
    payroll_notified = Column(Boolean, default=False)

    reminder_sent_at = Column(DateTime, nullable=True)
    # ↑ CEO ko deadline ki yaad dihani kab bheji — warna scheduler har
    #   15 minute par wahi reminder dobara bhejta rehta

    created_at = Column(DateTime, default=datetime.utcnow)


# ──── Table 6b: Leave Documents (DB mein binary) ────
# Medical certificate pehle uploads/certificates/ mein file bana kar
# path DB mein rakhta tha — aur CEO usay kahin se dekh hi nahi sakta tha.
# Ab bytes DB mein hain (attendance_photos jaisa hi tareeqa).
#
# Alag table isliye ke leave list har baar poori requests SELECT karti hai —
# agar bytes usi row mein hote to har listing megabytes uthati.
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

    # ──── Asal file (image compressed, PDF jaisi ki taisi) ────
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
    # ↑ Enum se String kiya — company apni policy mein koi bhi leave type
    #   rakh sakti hai (maternity, study, hajj...). Fixed enum us raah
    #   mein rukawat tha. Chalne wale types company_leave_types mein hain.
    total_entitlement = Column(Integer, default=0)
    used_days = Column(Integer, default=0)
    remaining_days = Column(Integer, default=0)
    last_updated = Column(DateTime, default=datetime.utcnow)


# ──── Table 8: Policy Decisions Log ────
class PolicyDecisionLog(Base):
    __tablename__ = "policy_decisions_log"

    id = Column(Integer, primary_key=True, index=True)
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
    # ↑ Enum se String kiya — company apni policy mein koi bhi leave type
    #   rakh sakti hai (maternity, study, hajj...). Fixed enum us raah
    #   mein rukawat tha. Chalne wale types company_leave_types mein hain.
    force_manual = Column(Boolean, default=False)
    reason = Column(Text, nullable=True)
    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    set_at = Column(DateTime, default=datetime.utcnow)
    cleared_at = Column(DateTime, nullable=True)


# ──── Table 10: Office Location ────
# ↑ Modular — CEO change kar sakta hai
# Ek company ki ek office location
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
    # ↑ Kitne meters andar valid
    # 200m = default
    # CEO change kar sakta hai settings se

    is_active = Column(Boolean, default=True)

    set_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)