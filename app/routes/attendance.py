import os
import math
from datetime import datetime, date, timedelta, timezone
from calendar import monthrange
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.utils.tenancy import Tenant, get_tenant, require_ceo
from app.utils.security import get_current_user
from app.models.attendance import (
    FaceEnrollment, AttendanceSession, AttendanceInterval,
    CompanyWorkPolicy, AttendanceStatusEnum, IntervalTypeEnum,
    OfficeLocation, LeaveRequest, LeaveStatusEnum,
    AttendancePhoto, PhotoKindEnum
)
from app.models.user import User
from app.utils.face_utils import enroll_face_from_images, prepare_photo_for_db
from app.utils.pkt import get_pkt_now, get_pkt_today
from app.utils.workpolicy import (
    parse_hhmm as _parse_hhmm,
    fmt_hhmm as _fmt_hhmm,
    is_working_day as _is_working_day,
    is_overnight_shift,
    shift_length_minutes,
    work_date_for,
)
from app.utils.company import (
    require_ceo,
    get_user_or_404 as _get_user_or_404,
    resolve_company_id as _resolve_company_id,
    assert_can_view as _assert_can_view,
    company_employees as _company_employees,
    assert_self as _assert_self_base,
)


router = APIRouter(prefix="/attendance", tags=["Attendance"])


def _work_policy(db: Session, company_id: Optional[int]):
    """The company work policy — None when there is no company_id"""
    if not company_id:
        return None
    return db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()


def _work_date(db: Session, company_id: Optional[int], now: Optional[datetime] = None):
    """
    This company's current ATTENDANCE DAY.

    On a night shift the day stays the one the shift STARTED on, even
    after midnight — otherwise an employee could check in again past 12
    could check in again.
    """
    now = now or get_pkt_now()
    return work_date_for(_work_policy(db, company_id), now)


def _employee_work_date(db: Session, employee_id: int):
    """The current attendance day for this employee's company"""
    employee = db.query(User).filter(User.id == employee_id).first()
    company_id = _resolve_company_id(db, employee) if employee else None
    return _work_date(db, company_id or employee_id)


def _assert_self(current_user: dict, employee_id: int):
    """Check-in/out/pause/resume — only for yourself"""
    _assert_self_base(current_user, employee_id, "attendance mark")


# ══════════════════════════════════════════════
# GPS helpers
# ══════════════════════════════════════════════

# The maximum browser GPS accuracy we are willing to add to the radius
MAX_ACCURACY_ALLOWANCE_M = 250

# An accuracy value worse than this means the reading cannot be trusted
UNRELIABLE_ACCURACY_M = 2000


def _calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine formula — distance between two GPS points, in metres"""
    R = 6371000
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) *
         math.sin(d_lng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _verify_location(office, lat, lng, accuracy) -> dict:
    """
    Accuracy-weighted GPS verification.

    The problem: from the same spot, check-in showed 15m and check-out
    20098m. The cause — the browser sometimes uses the GPS chip (accuracy
    ~10m) and sometimes estimates from WiFi/IP (accuracy thousands of m).

    Fix: add the reading's own accuracy to the radius, and if a reading is
    so poor that nothing can be said, skip verification — an employee
    should never get a wrong "outside office" flag.
    """
    result = {
        "verified": False,
        "distance": None,
        "accuracy": accuracy,
        "note": None,
        "message": "",
    }

    # ──── No office is set → skip verification ────
    if not office:
        result["verified"] = True
        result["note"] = "office_not_set"
        result["message"] = "No office location is set — verification skipped"
        return result

    # ──── No GPS at all (permission denied / timeout) ────
    if lat is None or lng is None:
        result["verified"] = False
        result["note"] = "gps_unavailable"
        result["message"] = "No GPS location was available"
        return result

    distance = _calculate_distance(lat, lng, office.latitude, office.longitude)
    result["distance"] = round(distance, 1)

    # ──── The reading is too poor to judge ────
    if accuracy and accuracy > UNRELIABLE_ACCURACY_M:
        result["verified"] = True
        result["note"] = "gps_unreliable"
        result["message"] = (
            f"GPS accuracy is too poor ({int(accuracy)}m) — verification skipped"
        )
        return result

    # ──── Add the accuracy to the radius as an allowance ────
    allowance = min(accuracy or 0, MAX_ACCURACY_ALLOWANCE_M)
    tolerance = office.radius_meters + allowance

    result["verified"] = distance <= tolerance
    result["note"] = "in_range" if result["verified"] else "out_of_range"
    result["message"] = (
        f"{distance:.0f}m from the office (allowed {tolerance:.0f}m)"
        if result["verified"] else
        f"Outside the office — {distance:.0f}m (allowed {tolerance:.0f}m)"
    )
    return result


# ══════════════════════════════════════════════
# Check-in window helpers
# ══════════════════════════════════════════════
def _in_time_window(now_m: int, start_m: int, end_m: int) -> bool:
    """
    Is now_m inside the window? Windows that wrap past midnight are
    handled too (e.g. a 22:00 to 06:00 night shift).
    """
    if start_m <= end_m:
        return start_m <= now_m <= end_m
    return now_m >= start_m or now_m <= end_m


# The grace must not be so large that the window loses all meaning
MAX_EARLY_GRACE_MINS = 12 * 60


def _checkin_window(policy, day: date, now: datetime) -> dict:
    """
    Whether checking in is allowed.

    Rule: check in only during the shift.
      window = [shift_start - grace  ...  shift_end]
    After the shift ends there is NO check-in → that person is absent for
    the day. (There is no restriction on check-out — work as long as you like.)

    A non-working day has no shift at all, so the window does not apply
    (so weekend overtime stays possible).
    """
    result = {
        "enforced": False,
        "open": True,
        "opens_at": None,
        "closes_at": None,
        "reason": None,
        "message": "",
    }

    if not policy or not policy.enforce_shift_window:
        result["reason"] = "not_enforced"
        return result

    if not _is_working_day(policy, day):
        result["reason"] = "non_working_day"
        result["message"] = "Today is not a working day — the check-in window does not apply"
        return result

    start_m = _parse_hhmm(policy.shift_start)
    end_m = _parse_hhmm(policy.shift_end)
    if start_m is None or end_m is None:
        result["reason"] = "policy_incomplete"
        return result

    grace = min(max(policy.early_checkin_grace_mins or 0, 0), MAX_EARLY_GRACE_MINS)
    opens_m = (start_m - grace) % (24 * 60)
    now_m = now.hour * 60 + now.minute

    result["enforced"] = True
    result["opens_at"] = _fmt_hhmm(opens_m)
    result["closes_at"] = _fmt_hhmm(end_m)
    result["open"] = _in_time_window(now_m, opens_m, end_m)

    if result["open"]:
        result["reason"] = "open"
        result["message"] = f"Check-in window {result['opens_at']} – {result['closes_at']}"

    elif opens_m <= end_m and now_m > end_m:
        # ──── A straightforward window, and the shift is over ────
        result["reason"] = "shift_ended"
        result["message"] = (
            f"The shift was {policy.shift_start} – {policy.shift_end}. "
            f"It is now {_fmt_hhmm(now_m)} — the check-in window has closed. "
            f"Aaj absent mark hoga."
        )

    else:
        # ──── The window has not opened yet (or we are in a night-shift gap) ────
        result["reason"] = "too_early"
        result["message"] = (
            f"Check-in opens at {result['opens_at']} "
            f"(shift {policy.shift_start} – {policy.shift_end})."
        )

    return result


def _store_photo(
    db: Session,
    base64_image: Optional[str],
    kind: PhotoKindEnum,
    employee_id: int,
    company_id: int,
    session_id: Optional[int] = None,
    captured_at: Optional[datetime] = None,
) -> bool:
    """
    Save the photo in the DB (compressed).

    A failed photo save must NOT stop attendance — the real attendance
    record is the time plus GPS; the photo is only evidence.
    """
    if not base64_image:
        return False

    prepared = prepare_photo_for_db(base64_image)
    if not prepared:
        print(f"Photo prepare failed (kind={kind}, employee={employee_id})")
        return False

    try:
        # ──── Replace it if one already exists (unique session_id+kind) ────
        existing = None
        if session_id:
            existing = db.query(AttendancePhoto).filter(
                AttendancePhoto.session_id == session_id,
                AttendancePhoto.kind == kind
            ).first()
        elif kind == PhotoKindEnum.enrollment:
            existing = db.query(AttendancePhoto).filter(
                AttendancePhoto.employee_id == employee_id,
                AttendancePhoto.kind == PhotoKindEnum.enrollment
            ).first()

        target = existing or AttendancePhoto(
            session_id=session_id,
            employee_id=employee_id,
            company_id=company_id,
            kind=kind,
        )

        target.image_data = prepared["data"]
        target.mime_type = prepared["mime_type"]
        target.file_size_bytes = prepared["size_bytes"]
        target.width = prepared["width"]
        target.height = prepared["height"]
        target.sha256 = prepared["sha256"]
        target.captured_at = captured_at or get_pkt_now()

        if not existing:
            db.add(target)

        print(f"Photo stored: {kind} employee={employee_id} "
              f"{prepared['size_bytes'] // 1024}KB {prepared['width']}x{prepared['height']}")
        return True

    except Exception as e:
        print(f"Photo DB save failed: {e}")
        return False


def _photo_kinds_for(db: Session, session_ids: List[int]) -> dict:
    """
    Which sessions have which photos — in one query (to avoid N+1).
    The image_data column is deliberately NOT loaded, only the kind.
    Return: {session_id: {"checkin", "checkout"}}
    """
    if not session_ids:
        return {}

    rows = db.query(AttendancePhoto.session_id, AttendancePhoto.kind).filter(
        AttendancePhoto.session_id.in_(session_ids)
    ).all()

    result = {}
    for sid, kind in rows:
        result.setdefault(sid, set()).add(
            kind.value if hasattr(kind, "value") else kind
        )
    return result


def _open_session(
    db: Session,
    employee_id: int,
    today: date,
    status: Optional[AttendanceStatusEnum] = None,
) -> Optional[AttendanceSession]:
    """
    The employee's currently open session.

    We do not look at today's date alone — someone working through the
    night has a session dated YESTERDAY. For example: check in at 4pm,
    check out at 1am (by which point the PKT date has changed). Such a
    person previously could not check out at all and the session stayed
    open forever.
    """
    query = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.date.in_([today, today - timedelta(days=1)]),
        AttendanceSession.status != AttendanceStatusEnum.checked_out,
    )
    if status is not None:
        query = query.filter(AttendanceSession.status == status)

    return query.order_by(AttendanceSession.date.desc()).first()


def _on_approved_leave(db: Session, employee_id: int, day: date) -> Optional[LeaveRequest]:
    """Does the employee have approved leave on this day?"""
    return db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == LeaveStatusEnum.approved,
        LeaveRequest.start_date <= day,
        LeaveRequest.end_date >= day
    ).first()


# ══════════════════════════════════════════════
# Pydantic Schemas
# ══════════════════════════════════════════════
class EnrollFaceSchema(BaseModel):
    employee_id: int
    face_images: List[str]


class CheckInSchema(BaseModel):
    employee_id: int
    face_image: Optional[str] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_accuracy_meters: Optional[float] = None


class PauseSchema(BaseModel):
    session_id: Optional[int] = None
    employee_id: int


class ResumeSchema(BaseModel):
    session_id: Optional[int] = None
    employee_id: int


class CheckOutSchema(BaseModel):
    session_id: Optional[int] = None
    employee_id: int
    face_image: Optional[str] = None
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_accuracy_meters: Optional[float] = None


# ══════════════════════════════════════════════
# Serializers
# ══════════════════════════════════════════════
def _interval_out(i: AttendanceInterval) -> dict:
    return {
        "start": str(i.start_time),
        "end": str(i.end_time) if i.end_time else None,
        "duration_minutes": i.duration_minutes,
    }


def _session_out(
    s: AttendanceSession,
    employee: Optional[User] = None,
    photo_kinds: Optional[set] = None,
) -> dict:
    """
    The same shape everywhere — the frontend should never have to guess.

    photo_kinds = the photos available for this session ({"checkin","checkout"}).
    When None we fall back to the old file-path columns (legacy rows).
    """
    if photo_kinds is None:
        has_in = bool(s.check_in_face_image)
        has_out = bool(s.check_out_face_image)
    else:
        has_in = "checkin" in photo_kinds
        has_out = "checkout" in photo_kinds

    return {
        "session_id": s.id,
        "employee_id": s.employee_id,
        "employee_name": employee.full_name if employee else None,
        "department": employee.department if employee else None,
        "date": str(s.date),
        "status": s.status.value if hasattr(s.status, "value") else s.status,

        "check_in_time": str(s.check_in_time) if s.check_in_time else None,
        "check_out_time": str(s.check_out_time) if s.check_out_time else None,

        "gross_hours": s.gross_hours,
        "net_hours": s.net_hours,
        "total_pause_minutes": s.total_pause_minutes,

        "is_late": s.is_late,
        "late_by_minutes": s.late_by_minutes,
        "is_overtime": s.is_overtime,
        "overtime_minutes": s.overtime_minutes,
        "is_undertime": s.is_undertime,
        "undertime_minutes": s.undertime_minutes,
        "is_early_checkout": s.is_early_checkout,
        "early_checkout_minutes": s.early_checkout_minutes,
        "is_working_day": s.is_working_day,

        "check_in_verified": s.check_in_verified,
        "check_out_verified": s.check_out_verified,

        # ──── Check-in location ────
        "check_in_lat": s.check_in_lat,
        "check_in_lng": s.check_in_lng,
        "location_verified": s.location_verified,
        "check_in_distance_meters": s.check_in_distance_meters,
        "check_in_gps_accuracy": s.check_in_gps_accuracy,
        "check_in_location_note": s.check_in_location_note,

        # ──── Check-out location ────
        "check_out_lat": s.check_out_lat,
        "check_out_lng": s.check_out_lng,
        "checkout_location_verified": s.checkout_location_verified,
        "check_out_distance_meters": s.check_out_distance_meters,
        "check_out_gps_accuracy": s.check_out_gps_accuracy,
        "check_out_location_note": s.check_out_location_note,

        "has_checkin_photo": has_in,
        "has_checkout_photo": has_out,
        "policy_snapshot": s.policy_snapshot,
    }


def _policy_view(policy, window: dict) -> Optional[dict]:
    """
    The policy as shown to the employee — complete and ready to display.

    ═══ WHY THIS IS A SEPARATE FUNCTION ═══
    An employee should know the whole rulebook, not just the shift times:
    when the break is and how long, how early they can check in, after how
    long they count as late, when overtime starts. All of this used to live
    only in the CEO's Settings — the employee was never told.

    The arithmetic happens here, not in the UI. The UI must not work out
    something like `late_after` (shift start + tolerance) for itself — that
    would repeat the old mistake where the UI computed its own figures and
    drifted away from the server.
    """
    if not policy:
        return None

    start_m = _parse_hhmm(policy.shift_start)
    tolerance = policy.late_tolerance_mins or 0

    # ──── Is the break at a fixed time, or whenever you like? ────
    b_start = _parse_hhmm(policy.break_start) if policy.break_start else None
    b_end = _parse_hhmm(policy.break_end) if policy.break_end else None
    fixed_break = b_start is not None and b_end is not None

    if fixed_break:
        # If times are given, the duration is derived from them — a
        # duration stored in two places will drift apart eventually
        span = (b_end - b_start) if b_end >= b_start else (24 * 60 - b_start + b_end)
    else:
        span = policy.break_minutes

    return {
        # ──── Shift ────
        "shift_start": policy.shift_start,
        "shift_end": policy.shift_end,
        "working_days": policy.working_days,
        "is_overnight": is_overnight_shift(policy),
        "shift_length_minutes": shift_length_minutes(policy),

        # ──── The check-in limits ────
        "enforce_shift_window": policy.enforce_shift_window,
        "early_checkin_grace_mins": policy.early_checkin_grace_mins,
        "checkin_opens_at": window.get("opens_at"),
        "checkin_closes_at": window.get("closes_at"),

        # ──── Late ────
        "late_tolerance_mins": tolerance,
        # Arriving AFTER this is late — the employee should not have to work it out
        "late_after": _fmt_hhmm(start_m + tolerance) if start_m is not None else None,

        # ──── Break ────
        "break_policy": getattr(policy.break_policy, "value", policy.break_policy),
        "break_minutes": span,
        "break_start": policy.break_start if fixed_break else None,
        "break_end": policy.break_end if fixed_break else None,
        "break_is_fixed": fixed_break,

        # ──── Hours ────
        "min_daily_hours": policy.min_daily_hours,
        "overtime_threshold": policy.overtime_threshold,
        "max_overtime_per_day": policy.max_overtime_per_day,
    }


def _no_session_status(policy, day: date, now: datetime, window: dict,
                       current_work_date: date) -> tuple:
    """
    An employee with no session — are they ABSENT, or still due to arrive?

    ═══ WHAT THE PROBLEM WAS ═══
    A missing session used to be written straight down as "Absent". Which
    meant the whole team showed as absent before the 9am shift had even
    started — and on a night shift, hours before it started.

    ═══ THE CORRECT RULE ═══
    Absent means someone who has not arrived even after their CHANCE to
    check in has passed — that is, once the check-in window has closed (the
    same moment the employee's check-in button disables). Before that they
    are simply "not in yet".

    The same reasoning covers both day and night shifts, because the window
    is itself derived from the shift.

    Return: (status, note)
    """
    # ──── A past day — the chance is definitely gone ────
    if day < current_work_date:
        return "Absent", None

    # ──── A future day ────
    if day > current_work_date:
        return "Upcoming", "This day has not arrived yet"

    # ═══ Today ═══
    if window.get("enforced"):
        reason = window.get("reason")
        if reason == "too_early":
            return "Upcoming", window.get("message")
        if window.get("open"):
            return "Not Checked In", "The check-in window is still open"
        # shift_ended — the chance has passed
        return "Absent", "Check-in window band ho chuka"

    # ──── The window is not enforced — the shift end is the limit ────
    end_m = _parse_hhmm(policy.shift_end) if policy else None
    if end_m is None:
        # There is no policy — calling anyone absent would be premature
        return "Not Checked In", None

    now_m = now.hour * 60 + now.minute
    start_m = _parse_hhmm(policy.shift_start)

    if start_m is not None and end_m < start_m:
        # ──── Night shift ────
        # This work date's shift can never "pass": the moment it ends, the
        # work date moves on to the next day by itself. So the shift is
        # either still to come (the gap between two shifts), or running.
        if end_m < now_m < start_m:
            return "Upcoming", "The shift starts tonight"
        return "Not Checked In", None

    if now_m > end_m:
        return "Absent", "The shift is over"
    return "Not Checked In", None


def _absent_row(employee: User, day: date, working_day: bool) -> dict:
    """An employee who never checked in — same shape, all values empty"""
    return {
        "session_id": None,
        "employee_id": employee.id,
        "employee_name": employee.full_name,
        "department": employee.department,
        "date": str(day),
        "status": "missed",
        "status_note": None,

        "check_in_time": None,
        "check_out_time": None,

        "gross_hours": None,
        "net_hours": None,
        "total_pause_minutes": 0,

        "is_late": False,
        "late_by_minutes": 0,
        "is_overtime": False,
        "overtime_minutes": 0,
        "is_undertime": False,
        "undertime_minutes": 0,
        "is_early_checkout": False,
        "early_checkout_minutes": 0,
        "is_working_day": working_day,

        "check_in_verified": False,
        "check_out_verified": False,

        "check_in_lat": None,
        "check_in_lng": None,
        "location_verified": False,
        "check_in_distance_meters": None,
        "check_in_gps_accuracy": None,
        "check_in_location_note": None,

        "check_out_lat": None,
        "check_out_lng": None,
        "checkout_location_verified": False,
        "check_out_distance_meters": None,
        "check_out_gps_accuracy": None,
        "check_out_location_note": None,

        "has_checkin_photo": False,
        "has_checkout_photo": False,
        "policy_snapshot": None,
    }


# ══════════════════════════════════════════════
# Route 1: Face Enrollment (CEO)
# ══════════════════════════════════════════════
@router.post("/enroll-face")
def enroll_face(
    data: EnrollFaceSchema,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    _assert_can_view(db, current_user, data.employee_id)

    if not data.face_images:
        raise HTTPException(status_code=400, detail="At least 1 image is required")

    embedding = enroll_face_from_images(data.face_images)
    if not embedding:
        raise HTTPException(status_code=400, detail="No face was detected in any of the images")

    existing = db.query(FaceEnrollment).filter(
        FaceEnrollment.employee_id == data.employee_id
    ).first()

    if existing:
        existing.embedding = embedding
        existing.enrolled_by = current_user["user_id"]
        existing.enrolled_at = get_pkt_now()
        existing.status = "active"
        db.commit()
        return {"message": "Face updated!", "employee_id": data.employee_id}

    db.add(FaceEnrollment(
        employee_id=data.employee_id,
        embedding=embedding,
        enrolled_by=current_user["user_id"],
        enrolled_at=get_pkt_now()
    ))
    db.commit()

    return {
        "message": "Face enrolled successfully!",
        "employee_id": data.employee_id,
        "embedding_size": len(embedding)
    }


# ══════════════════════════════════════════════
# Route 2: Check-In
# ══════════════════════════════════════════════
@router.post("/check-in")
def check_in(
    data: CheckInSchema,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    _assert_self(current_user, data.employee_id)

    now = get_pkt_now()

    employee = _get_user_or_404(db, data.employee_id)
    company_id = _resolve_company_id(db, employee) or data.employee_id
    policy = _work_policy(db, company_id)

    # ──── The attendance DAY — the shift's day, not the calendar date ────
    # On a night shift (22:00-05:00) the day stays the same past midnight
    today = work_date_for(policy, now)

    # ──── Is there already a session for this day? (checked-out counts too) ────
    existing = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == data.employee_id,
        AttendanceSession.date == today
    ).first()

    if existing:
        if existing.status == AttendanceStatusEnum.checked_out:
            raise HTTPException(
                status_code=400,
                detail="Today is already complete — you cannot check in again"
            )
        raise HTTPException(status_code=400, detail="You are already checked in")

    # ──── If yesterday's session is still open, close that first ────
    # Otherwise a dangling session stays open forever
    dangling = _open_session(db, data.employee_id, today)
    if dangling:
        raise HTTPException(
            status_code=400,
            detail=f"Your session from {dangling.date} is still open — "
                   f"please check out of it first"
        )

    working_day = _is_working_day(policy, today)

    # ──── Check-in window — no checking in outside the shift ────
    window = _checkin_window(policy, today, now)
    if not window["open"]:
        raise HTTPException(status_code=400, detail=window["message"])

    # ──── Late check (working days only) ────
    is_late = False
    late_by_minutes = 0
    if policy and working_day:
        shift_start_minutes = _parse_hhmm(policy.shift_start)
        if shift_start_minutes is not None:
            check_in_minutes = now.hour * 60 + now.minute
            allowed = shift_start_minutes + (policy.late_tolerance_mins or 0)
            if check_in_minutes > allowed:
                is_late = True
                late_by_minutes = check_in_minutes - shift_start_minutes

    # ──── GPS verify ────
    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id,
        OfficeLocation.is_active == True
    ).first()

    loc = _verify_location(
        office, data.gps_latitude, data.gps_longitude, data.gps_accuracy_meters
    )
    print(f"[check-in] employee={data.employee_id} {loc['message']}")

    session = AttendanceSession(
        employee_id=data.employee_id,
        company_id=company_id,
        date=today,
        check_in_time=now,
        check_in_lat=data.gps_latitude,
        check_in_lng=data.gps_longitude,
        check_in_verified=False,
        location_verified=loc["verified"],
        check_in_distance_meters=loc["distance"],
        check_in_gps_accuracy=data.gps_accuracy_meters,
        check_in_location_note=loc["note"],
        is_late=is_late,
        late_by_minutes=late_by_minutes,
        is_working_day=working_day,
        status=AttendanceStatusEnum.checked_in,
        created_at=now
    )
    db.add(session)
    db.flush()          # session.id is needed for the photo

    # ──── Photo into the DB (for the record only — not verification) ────
    photo_saved = _store_photo(
        db, data.face_image, PhotoKindEnum.checkin,
        employee_id=data.employee_id, company_id=company_id,
        session_id=session.id, captured_at=now,
    )
    session.check_in_verified = photo_saved

    db.commit()
    db.refresh(session)

    return {
        "message": "Check-in successful!",
        "session_id": session.id,
        "photo_saved": photo_saved,
        "check_in_time": str(session.check_in_time),
        "is_late": is_late,
        "late_by_minutes": late_by_minutes,
        "is_working_day": working_day,
        "face_verified": session.check_in_verified,
        "location_verified": loc["verified"],
        "distance_meters": loc["distance"],
        "gps_accuracy_meters": data.gps_accuracy_meters,
        "location_note": loc["note"],
        "location_message": loc["message"],
    }


# ══════════════════════════════════════════════
# Route 3: Pause (Break start)
# ══════════════════════════════════════════════
@router.post("/pause")
def pause_session(
    data: PauseSchema,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    _assert_self(current_user, data.employee_id)

    session = _open_session(
        db, data.employee_id, _employee_work_date(db, data.employee_id),
        AttendanceStatusEnum.checked_in
    )

    if not session:
        raise HTTPException(status_code=404, detail="No active session found")

    if data.session_id and data.session_id != session.id:
        raise HTTPException(status_code=400, detail="Session id does not match")

    now = get_pkt_now()

    # ──── Is there already an open break? ────
    open_interval = db.query(AttendanceInterval).filter(
        AttendanceInterval.session_id == session.id,
        AttendanceInterval.end_time == None
    ).first()

    if open_interval:
        raise HTTPException(status_code=400, detail="You are already on a break")

    db.add(AttendanceInterval(
        session_id=session.id,
        employee_id=data.employee_id,
        type=IntervalTypeEnum.pause,
        start_time=now
    ))
    session.status = AttendanceStatusEnum.paused
    db.commit()

    return {"message": "Paused!", "session_id": session.id, "pause_time": str(now)}


# ══════════════════════════════════════════════
# Route 4: Resume
# ══════════════════════════════════════════════
@router.post("/resume")
def resume_session(
    data: ResumeSchema,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    _assert_self(current_user, data.employee_id)

    session = _open_session(
        db, data.employee_id, _employee_work_date(db, data.employee_id),
        AttendanceStatusEnum.paused
    )

    if not session:
        raise HTTPException(status_code=404, detail="No paused session found")

    if data.session_id and data.session_id != session.id:
        raise HTTPException(status_code=400, detail="Session id does not match")

    now = get_pkt_now()
    pause_duration = 0.0

    open_interval = db.query(AttendanceInterval).filter(
        AttendanceInterval.session_id == session.id,
        AttendanceInterval.end_time == None
    ).first()

    if open_interval:
        open_interval.end_time = now
        open_interval.duration_minutes = round(
            (now - open_interval.start_time).total_seconds() / 60, 2
        )
        pause_duration = open_interval.duration_minutes

    session.status = AttendanceStatusEnum.checked_in
    db.commit()

    return {
        "message": "Resumed!",
        "session_id": session.id,
        "resume_time": str(now),
        "pause_duration_minutes": pause_duration
    }


# ══════════════════════════════════════════════
# Route 5: Check-Out
# ══════════════════════════════════════════════
@router.post("/check-out")
def check_out(
    data: CheckOutSchema,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    _assert_self(current_user, data.employee_id)

    today = _employee_work_date(db, data.employee_id)

    # ──── No time limit on check-out — it can happen late at night ────
    session = _open_session(db, data.employee_id, today)

    if not session:
        raise HTTPException(status_code=404, detail="No active session found")

    if data.session_id and data.session_id != session.id:
        raise HTTPException(status_code=400, detail="Session id does not match")

    if not session.check_in_time:
        raise HTTPException(status_code=400, detail="Check-in time is missing — cannot check out")

    now = get_pkt_now()

    # ──── Close any open breaks ────
    open_intervals = db.query(AttendanceInterval).filter(
        AttendanceInterval.session_id == session.id,
        AttendanceInterval.end_time == None
    ).all()

    for interval in open_intervals:
        interval.end_time = now
        interval.duration_minutes = round(
            (now - interval.start_time).total_seconds() / 60, 2
        )
    db.flush()

    all_intervals = db.query(AttendanceInterval).filter(
        AttendanceInterval.session_id == session.id
    ).all()
    total_pause_minutes = sum(i.duration_minutes or 0 for i in all_intervals)

    # ──── Hours ────
    gross_hours = max(0.0, (now - session.check_in_time).total_seconds() / 3600)

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == session.company_id
    ).first()

    break_policy = policy.break_policy if policy else None
    break_excluded = str(getattr(break_policy, "value", break_policy)) == "excluded"

    net_hours = gross_hours - (total_pause_minutes / 60) if break_excluded else gross_hours
    net_hours = max(0.0, net_hours)

    # ──── Under / Overtime (working days only) ────
    is_undertime = False
    undertime_minutes = 0
    is_overtime = False
    overtime_minutes = 0

    if policy and session.is_working_day:
        if net_hours < policy.min_daily_hours:
            is_undertime = True
            undertime_minutes = int(round((policy.min_daily_hours - net_hours) * 60))
        if net_hours > policy.overtime_threshold:
            is_overtime = True
            raw_overtime = (net_hours - policy.overtime_threshold) * 60
            overtime_minutes = int(min(raw_overtime, (policy.max_overtime_per_day or 0) * 60))
    elif policy and not session.is_working_day:
        # ──── Work on an off-day = all overtime ────
        overtime_minutes = int(min(net_hours * 60, (policy.max_overtime_per_day or 0) * 60))

    # ──── Did they leave before the shift ended? ────
    is_early_checkout = False
    early_checkout_minutes = 0

    if policy and session.is_working_day and now.date() == session.date:
        shift_start_m = _parse_hhmm(policy.shift_start)
        shift_end_m = _parse_hhmm(policy.shift_end)
        # ──── This calculation is not valid on an overnight shift (end < start) ────
        if (shift_end_m is not None and shift_start_m is not None
                and shift_end_m > shift_start_m):
            checkout_m = now.hour * 60 + now.minute
            if checkout_m < shift_end_m:
                is_early_checkout = True
                early_checkout_minutes = shift_end_m - checkout_m

    # ──── The flag and the minutes must always agree ────
    is_overtime = overtime_minutes > 0
    is_undertime = undertime_minutes > 0

    policy_snapshot = None
    if policy:
        policy_snapshot = {
            "shift_start": policy.shift_start,
            "shift_end": policy.shift_end,
            "min_daily_hours": policy.min_daily_hours,
            "overtime_threshold": policy.overtime_threshold,
            "late_tolerance_mins": policy.late_tolerance_mins,
            "break_policy": getattr(policy.break_policy, "value", policy.break_policy),
        }

    # ──── GPS verify ────
    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == session.company_id,
        OfficeLocation.is_active == True
    ).first()

    loc = _verify_location(
        office, data.gps_latitude, data.gps_longitude, data.gps_accuracy_meters
    )
    print(f"[check-out] employee={data.employee_id} {loc['message']}")

    photo_saved = _store_photo(
        db, data.face_image, PhotoKindEnum.checkout,
        employee_id=data.employee_id, company_id=session.company_id,
        session_id=session.id, captured_at=now,
    )

    session.check_out_time = now
    session.check_out_lat = data.gps_latitude
    session.check_out_lng = data.gps_longitude
    session.check_out_verified = photo_saved
    session.checkout_location_verified = loc["verified"]
    session.check_out_distance_meters = loc["distance"]
    session.check_out_gps_accuracy = data.gps_accuracy_meters
    session.check_out_location_note = loc["note"]
    session.gross_hours = round(gross_hours, 2)
    session.total_pause_minutes = int(round(total_pause_minutes))
    session.net_hours = round(net_hours, 2)
    session.is_undertime = is_undertime
    session.undertime_minutes = undertime_minutes
    session.is_overtime = is_overtime
    session.overtime_minutes = overtime_minutes
    session.is_early_checkout = is_early_checkout
    session.early_checkout_minutes = early_checkout_minutes
    session.policy_snapshot = policy_snapshot
    session.status = AttendanceStatusEnum.checked_out

    db.commit()
    db.refresh(session)

    return {
        "message": "Check-out successful!",
        "session_id": session.id,
        "photo_saved": photo_saved,
        "check_out_time": str(now),
        "gross_hours": session.gross_hours,
        "total_pause_minutes": session.total_pause_minutes,
        "net_hours": session.net_hours,
        "is_late": session.is_late,
        "late_by_minutes": session.late_by_minutes,
        "is_undertime": is_undertime,
        "undertime_minutes": undertime_minutes,
        "is_overtime": is_overtime,
        "overtime_minutes": overtime_minutes,
        "is_early_checkout": is_early_checkout,
        "early_checkout_minutes": early_checkout_minutes,
        "face_verified": session.check_out_verified,
        "location_verified": loc["verified"],
        "checkout_distance_meters": loc["distance"],
        "gps_accuracy_meters": data.gps_accuracy_meters,
        "location_note": loc["note"],
        "location_message": loc["message"],
    }


# ══════════════════════════════════════════════
# Route 6: Daily Report
# ══════════════════════════════════════════════
@router.get("/report/{employee_id}/{report_date}")
def get_daily_report(
    employee_id: int,
    report_date: str,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    employee = _assert_can_view(db, current_user, employee_id)

    try:
        target_date = date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format must be YYYY-MM-DD")

    session = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.date == target_date
    ).first()

    if not session:
        leave = _on_approved_leave(db, employee_id, target_date)
        return {
            "report": None,
            "on_leave": leave is not None,
            "leave_type": getattr(leave.leave_type, "value", None) if leave else None,
            "message": "No record found for this day"
        }

    intervals = db.query(AttendanceInterval).filter(
        AttendanceInterval.session_id == session.id
    ).order_by(AttendanceInterval.start_time).all()

    photos = _photo_kinds_for(db, [session.id])
    report = _session_out(session, employee, photos.get(session.id, set()))
    report["pauses"] = [_interval_out(i) for i in intervals]

    return {"report": report}


# ══════════════════════════════════════════════
# Route 7: Attendance History
# ══════════════════════════════════════════════
@router.get("/history/{employee_id}")
def get_attendance_history(
    employee_id: int,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = Query(90, ge=1, le=366),
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    employee = _assert_can_view(db, current_user, employee_id)

    query = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id
    )

    try:
        if from_date:
            query = query.filter(AttendanceSession.date >= date.fromisoformat(from_date))
        if to_date:
            query = query.filter(AttendanceSession.date <= date.fromisoformat(to_date))
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format must be YYYY-MM-DD")

    sessions = query.order_by(AttendanceSession.date.desc()).limit(limit).all()
    photos = _photo_kinds_for(db, [s.id for s in sessions])

    return {
        "employee_id": employee_id,
        "employee_name": employee.full_name,
        "total": len(sessions),
        "history": [
            _session_out(s, employee, photos.get(s.id, set())) for s in sessions
        ]
    }


# ══════════════════════════════════════════════
# Route 8: Today's Status
# ══════════════════════════════════════════════
@router.get("/today/{employee_id}")
def get_today_status(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    employee = _assert_can_view(db, current_user, employee_id)
    now = get_pkt_now()

    company_id = _resolve_company_id(db, employee)
    policy = _work_policy(db, company_id)

    # ──── The shift's day, not the calendar's (for night shifts) ────
    today = work_date_for(policy, now)
    window = _checkin_window(policy, today, now)

    session = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.date == today
    ).first()

    # ──── Yesterday's still-open session (worked through the night) ────
    if not session:
        session = _open_session(db, employee_id, today)

    if not session:
        leave = _on_approved_leave(db, employee_id, today)
        return {
            "status": "on_leave" if leave else "not_checked_in",
            "session_id": None,
            "date": str(today),
            "work_date": str(today),
            "is_overnight_shift": is_overnight_shift(policy),
            "server_date": str(now.date()),
            "server_time": str(now),
            "on_leave": leave is not None,
            "checkin_window": window,
            "message": (
                "On approved leave today" if leave
                else (window["message"] if not window["open"] else "Not checked in today")
            )
        }

    # ──── Breaks so far ────
    intervals = db.query(AttendanceInterval).filter(
        AttendanceInterval.session_id == session.id
    ).all()
    pause_so_far = sum(
        i.duration_minutes if i.duration_minutes is not None
        else (now - i.start_time).total_seconds() / 60
        for i in intervals
    )

    photos = _photo_kinds_for(db, [session.id])
    out = _session_out(session, employee, photos.get(session.id, set()))
    out["work_date"] = str(today)
    out["is_overnight_shift"] = is_overnight_shift(policy)
    out["server_date"] = str(now.date())
    out["server_time"] = str(now)
    out["on_leave"] = False
    out["checkin_window"] = window
    out["pause_minutes_so_far"] = round(pause_so_far, 2)

    # ──── Is the session from yesterday? (an overnight shift) ────
    out["is_previous_day_session"] = session.date != today
    if out["is_previous_day_session"]:
        out["message"] = f"Your session from {session.date} is still open — please check out"

    # ──── Live elapsed seconds — the timer must not reset on refresh ────
    if session.check_in_time and session.status != AttendanceStatusEnum.checked_out:
        elapsed = (now - session.check_in_time).total_seconds()
        out["elapsed_seconds"] = int(max(0, elapsed - pause_so_far * 60))
    else:
        out["elapsed_seconds"] = int((session.net_hours or 0) * 3600)

    return out


# ══════════════════════════════════════════════
# Route 9: Enrollment Status
# ══════════════════════════════════════════════
@router.get("/enrollment-status/{employee_id}")
def check_enrollment_status(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    _assert_can_view(db, current_user, employee_id)

    enrollment = db.query(FaceEnrollment).filter(
        FaceEnrollment.employee_id == employee_id,
        FaceEnrollment.status == "active"
    ).first()

    return {
        "enrolled": enrollment is not None,
        "employee_id": employee_id,
        "enrolled_at": str(enrollment.enrolled_at) if enrollment else None
    }


# ══════════════════════════════════════════════
# Route 10: Self Enroll
# ══════════════════════════════════════════════
@router.post("/self-enroll")
def self_enroll_face(
    data: EnrollFaceSchema,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    # ──── You may only enrol your own face ────
    employee_id = current_user["user_id"]

    if not data.face_images:
        raise HTTPException(status_code=400, detail="At least 1 image is required")

    embedding = enroll_face_from_images(data.face_images)
    if not embedding:
        raise HTTPException(status_code=400, detail="Enrollment failed")

    user = _get_user_or_404(db, employee_id)
    company_id = _resolve_company_id(db, user) or employee_id
    now = get_pkt_now()

    existing = db.query(FaceEnrollment).filter(
        FaceEnrollment.employee_id == employee_id
    ).first()

    if existing:
        existing.embedding = embedding
        existing.enrolled_at = now
        existing.status = "active"
        message = "Face updated!"
    else:
        db.add(FaceEnrollment(
            employee_id=employee_id,
            embedding=embedding,
            enrolled_by=employee_id,
            enrolled_at=now
        ))
        message = "Face enrolled!"

    # ──── Keep the reference photo in the DB too (it used to be saved nowhere) ────
    photo_saved = _store_photo(
        db, data.face_images[0], PhotoKindEnum.enrollment,
        employee_id=employee_id, company_id=company_id, captured_at=now,
    )

    db.commit()

    return {"message": message, "enrolled": True, "photo_saved": photo_saved}


# ══════════════════════════════════════════════
# Route 11: CEO — Team Attendance (for any day)
# ══════════════════════════════════════════════
@router.get("/flags/today")
def get_team_attendance(
    report_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    The whole team's attendance — including employees who never checked
    in (Absent / On Leave). Previously only checked-in people appeared
    so the 'Absent' count always stayed at 0.
    """
    ceo = _get_user_or_404(db, current_user["user_id"])
    company_id = current_user["company_id"]
    now_pkt = get_pkt_now()

    # Today's SHIFT day — this tells us whether the requested day has
    # passed, is running, or has not arrived yet
    current_work_date = _work_date(db, company_id, now_pkt)

    try:
        day = (date.fromisoformat(report_date) if report_date
               else current_work_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format must be YYYY-MM-DD")

    employees = _company_employees(db, ceo)
    employee_ids = [e.id for e in employees]

    # ──── All sessions in one query (no N+1) ────
    sessions = db.query(AttendanceSession).filter(
        AttendanceSession.company_id == company_id,
        AttendanceSession.date == day
    ).all()
    session_map = {s.employee_id: s for s in sessions}

    # ──── Approved leaves in one query ────
    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.status == LeaveStatusEnum.approved,
        LeaveRequest.start_date <= day,
        LeaveRequest.end_date >= day
    ).all() if employee_ids else []
    leave_map = {l.employee_id: l for l in leaves}

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()
    working_day = _is_working_day(policy, day)
    window = _checkin_window(policy, day, now_pkt)

    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id,
        OfficeLocation.is_active == True
    ).first()

    photos = _photo_kinds_for(db, [s.id for s in sessions])

    rows = []
    for emp in employees:
        session = session_map.get(emp.id)
        leave = leave_map.get(emp.id)

        if session:
            row = _session_out(session, emp, photos.get(session.id, set()))
            row["attendance_status"] = (
                "Late" if session.is_late else "Present"
            )
        else:
            row = _absent_row(emp, day, working_day)
            if leave:
                row["attendance_status"] = "On Leave"
            elif not working_day:
                row["attendance_status"] = "Off Day"
            else:
                # Calling someone absent is premature while they can still
                # check in — Absent only once the window has closed
                status, note = _no_session_status(
                    policy, day, now_pkt, window, current_work_date
                )
                row["attendance_status"] = status
                row["status_note"] = note

        row["on_leave"] = leave is not None
        row["leave_type"] = getattr(leave.leave_type, "value", None) if leave else None
        rows.append(row)

    def _count(*statuses):
        return len([r for r in rows if r["attendance_status"] in statuses])

    present = _count("Present", "Late")
    late = _count("Late")
    on_leave = _count("On Leave")
    absent = _count("Absent")
    pending = _count("Not Checked In")     # can still arrive
    upcoming = _count("Upcoming")          # the shift has not started

    return {
        "date": str(day),
        "server_time": str(now_pkt),
        "is_working_day": working_day,
        "total": len(rows),

        # ──── Shift state — the UI uses this to say whether Absent is
        #      final or the count is still running ────
        "shift_state": {
            "is_today": day == current_work_date,
            "is_past": day < current_work_date,
            "checkin_open": bool(window.get("open")) if window.get("enforced") else None,
            "window_reason": window.get("reason"),
            "window_message": window.get("message"),
            "opens_at": window.get("opens_at"),
            "closes_at": window.get("closes_at"),
            # ══════════════════════════════════════════
            # Has the Absent decision been settled?
            # ══════════════════════════════════════════
            # This condition used to be written out separately:
            #     window.enforced AND reason == "shift_ended"
            # But `_no_session_status()` also marks Absent without enforce
            # once the shift is over. The result: with enforce off, rows
            # showed "Absent" while this flag stayed False, so the UI showed
            # a "Not in yet" card above — two places saying two things.
            #
            # The flag now comes from the SAME helper that builds the row
            # statuses. One rule, one place — they can never disagree.
            "attendance_final": _no_session_status(
                policy, day, now_pkt, window, current_work_date
            )[0] == "Absent",
        },

        # ──── The active policy — for display on the dashboard ────
        "policy": {
            "shift_start": policy.shift_start,
            "shift_end": policy.shift_end,
            "late_tolerance_mins": policy.late_tolerance_mins,
            "min_daily_hours": policy.min_daily_hours,
            "overtime_threshold": policy.overtime_threshold,
            "working_days": policy.working_days,
            "break_policy": getattr(policy.break_policy, "value", policy.break_policy),
            "enforce_shift_window": policy.enforce_shift_window,
            "early_checkin_grace_mins": policy.early_checkin_grace_mins,
            "checkin_window_opens": window["opens_at"],
            "is_overnight": is_overnight_shift(policy),
            "shift_length_minutes": shift_length_minutes(policy),
        } if policy else None,
        "office": {
            "office_name": office.office_name,
            "latitude": office.latitude,
            "longitude": office.longitude,
            "radius_meters": office.radius_meters,
        } if office else None,

        "summary": {
            "total_employees": len(rows),
            "present": present,
            "late": late,
            "absent": absent,
            "pending": pending,
            "upcoming": upcoming,
            "on_leave": on_leave,
            "checked_out": len([r for r in rows if r["status"] == "checked_out"]),
            "on_break": len([r for r in rows if r["status"] == "paused"]),
        },
        "employees": rows
    }


# ══════════════════════════════════════════════
# Route 12: Monthly Summary (ek employee)
# ══════════════════════════════════════════════
@router.get("/summary/{employee_id}/{year}/{month}")
def get_monthly_summary(
    employee_id: int,
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    _assert_can_view(db, current_user, employee_id)

    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12")

    _, days_in_month = monthrange(year, month)
    start = date(year, month, 1)
    end = date(year, month, days_in_month)

    sessions = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.date >= start,
        AttendanceSession.date <= end
    ).all()

    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == LeaveStatusEnum.approved,
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start
    ).all()

    leave_days = 0
    for l in leaves:
        first = max(l.start_date, start)
        last = min(l.end_date, end)
        leave_days += (last - first).days + 1

    completed = [s for s in sessions if s.status == AttendanceStatusEnum.checked_out]

    return {
        "employee_id": employee_id,
        "year": year,
        "month": month,
        "present_days": len(sessions),
        "completed_days": len(completed),
        "leave_days": leave_days,
        "total_net_hours": round(sum(s.net_hours or 0 for s in sessions), 2),
        "avg_net_hours": round(
            sum(s.net_hours or 0 for s in completed) / len(completed), 2
        ) if completed else 0,
        "late_count": len([s for s in sessions if s.is_late]),
        "overtime_total_minutes": sum(s.overtime_minutes or 0 for s in sessions),
        "undertime_total_minutes": sum(s.undertime_minutes or 0 for s in sessions),
    }


# ══════════════════════════════════════════════
# Route 13: CEO — Attendance Overview Chart
# ══════════════════════════════════════════════
@router.get("/overview")
def get_attendance_overview(
    range_: str = Query("weekly", alias="range", pattern="^(weekly|monthly)$"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Chart data — the frontend used to fake this with Math.random().
    weekly  = the last 7 days
    monthly = every day of the current month (up to today)
    """
    ceo = _get_user_or_404(db, current_user["user_id"])
    company_id = current_user["company_id"]
    today = _work_date(db, company_id)

    if range_ == "weekly":
        start = today - timedelta(days=6)
        end = today
        label_fmt = "%a"                      # Mon, Tue...
    else:
        start = today.replace(day=1)
        end = today
        label_fmt = "%d"                      # 01, 02...

    total_employees = len(_company_employees(db, ceo))

    sessions = db.query(AttendanceSession).filter(
        AttendanceSession.company_id == company_id,
        AttendanceSession.date >= start,
        AttendanceSession.date <= end
    ).all()

    leaves = db.query(LeaveRequest).filter(
        LeaveRequest.company_id == company_id,
        LeaveRequest.status == LeaveStatusEnum.approved,
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start
    ).all()

    policy = db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()

    by_day = {}
    for s in sessions:
        by_day.setdefault(s.date, []).append(s)

    # Today is still in progress — someone who has not arrived is "pending",
    # not "absent". Absent only once the check-in window has closed.
    today_window = _checkin_window(policy, today, get_pkt_now())
    today_final = bool(
        today_window.get("enforced") and today_window.get("reason") == "shift_ended"
    )

    data = []
    day = start
    while day <= end:
        day_sessions = by_day.get(day, [])
        on_leave = len([
            l for l in leaves if l.start_date <= day <= l.end_date
        ])
        present = len(day_sessions)
        working = _is_working_day(policy, day)
        missing = max(0, total_employees - present - on_leave)

        if not working:
            absent = pending = 0        # off day — nobody is absent
        elif day == today and not today_final:
            absent, pending = 0, missing
        else:
            absent, pending = missing, 0

        data.append({
            "name": day.strftime(label_fmt),
            "date": str(day),
            "present": present,
            "late": len([s for s in day_sessions if s.is_late]),
            "on_leave": on_leave,
            "absent": absent,
            "pending": pending,
            "is_working_day": working,
            "avg_net_hours": round(
                sum(s.net_hours or 0 for s in day_sessions) / len(day_sessions), 2
            ) if day_sessions else 0,
        })
        day += timedelta(days=1)

    return {
        "range": range_,
        "from": str(start),
        "to": str(end),
        "total_employees": total_employees,
        "data": data
    }


# ══════════════════════════════════════════════
# Route 14: Attendance photo (the CEO, or the employee themselves)
# ══════════════════════════════════════════════
@router.get("/photo/{session_id}/{kind}")
def get_attendance_photo(
    session_id: int,
    kind: str,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    """Photos are served from the DB. Files from older records still work."""
    if kind not in ("checkin", "checkout"):
        raise HTTPException(status_code=400, detail="kind must be 'checkin' or 'checkout'")

    session = db.query(AttendanceSession).filter(
        AttendanceSession.id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    _assert_can_view(db, current_user, session.employee_id)

    # ──── 1. The DB (the real place) ────
    photo = db.query(AttendancePhoto).filter(
        AttendancePhoto.session_id == session_id,
        AttendancePhoto.kind == kind
    ).first()

    if photo:
        return Response(
            content=photo.image_data,
            media_type=photo.mime_type or "image/jpeg",
            headers={
                # ──── An attendance photo never changes — let the browser cache it ────
                "Cache-Control": "private, max-age=86400",
                "Content-Disposition":
                    f'inline; filename="{kind}_{session.employee_id}_{session.date}.jpg"',
            },
        )

    # ──── 2. Legacy file fallback ────
    path = session.check_in_face_image if kind == "checkin" else session.check_out_face_image
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/jpeg", filename=os.path.basename(path))

    raise HTTPException(status_code=404, detail="Photo is not available")


# ══════════════════════════════════════════════
# Route 14b: Enrollment Photo
# ══════════════════════════════════════════════
@router.get("/enrollment-photo/{employee_id}")
def get_enrollment_photo(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    """The employee's enrolment reference photo — so the CEO can compare"""
    _assert_can_view(db, current_user, employee_id)

    photo = db.query(AttendancePhoto).filter(
        AttendancePhoto.employee_id == employee_id,
        AttendancePhoto.kind == PhotoKindEnum.enrollment
    ).first()

    if not photo:
        raise HTTPException(status_code=404, detail="Enrolment photo is not available")

    return Response(
        content=photo.image_data,
        media_type=photo.mime_type or "image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ══════════════════════════════════════════════
# Route 15: Employee — office info (before checking in)
# ══════════════════════════════════════════════
@router.get("/my-office")
def get_my_office(
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant)
):
    """So the employee knows where the office is and what the radius is"""
    user = _get_user_or_404(db, current_user["user_id"])
    company_id = _resolve_company_id(db, user)

    office = db.query(OfficeLocation).filter(
        OfficeLocation.company_id == company_id,
        OfficeLocation.is_active == True
    ).first() if company_id else None

    policy = _work_policy(db, company_id)

    now = get_pkt_now()
    today = work_date_for(policy, now)
    window = _checkin_window(policy, today, now)

    return {
        "office": {
            "office_name": office.office_name,
            "latitude": office.latitude,
            "longitude": office.longitude,
            "radius_meters": office.radius_meters,
        } if office else None,
        # ──── The full policy — the employee should know it too ────
        "policy": _policy_view(policy, window),
        "checkin_window": window,
        "is_working_day": _is_working_day(policy, today),
        # ──── The attendance day (differs from the calendar on a night shift) ────
        "work_date": str(today),
        "is_overnight_shift": is_overnight_shift(policy),
        "server_date": str(get_pkt_now().date()),
        "server_time": str(now),
    }
