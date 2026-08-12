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
    """Company ki work policy — company_id na ho to None"""
    if not company_id:
        return None
    return db.query(CompanyWorkPolicy).filter(
        CompanyWorkPolicy.company_id == company_id
    ).first()


def _work_date(db: Session, company_id: Optional[int], now: Optional[datetime] = None):
    """
    Is company ka abhi ka ATTENDANCE DIN.

    Raat ki shift mein aadhi raat ke baad bhi din wahi rehta hai jis din
    shift shuru hui thi — warna employee 12 baje ke baad dobara check-in
    kar leta tha.
    """
    now = now or get_pkt_now()
    return work_date_for(_work_policy(db, company_id), now)


def _employee_work_date(db: Session, employee_id: int):
    """Employee ki company ke hisaab se abhi ka attendance din"""
    employee = db.query(User).filter(User.id == employee_id).first()
    company_id = _resolve_company_id(db, employee) if employee else None
    return _work_date(db, company_id or employee_id)


def _assert_self(current_user: dict, employee_id: int):
    """Check-in/out/pause/resume sirf apne liye"""
    _assert_self_base(current_user, employee_id, "attendance mark")


# ══════════════════════════════════════════════
# GPS helpers
# ══════════════════════════════════════════════

# Browser GPS accuracy ka max allowance jo hum radius mein add karte hain
MAX_ACCURACY_ALLOWANCE_M = 250

# Isse zyada accuracy value = reading bharosay ke qabil nahi
UNRELIABLE_ACCURACY_M = 2000


def _calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine formula — 2 GPS points ka distance meters mein"""
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

    Masla tha: same jagah pe check-in 15m aur check-out 20098m dikhata tha.
    Wajah — browser kabhi GPS chip use karta hai (accuracy ~10m) aur kabhi
    WiFi/IP se andaza lagata hai (accuracy hazaron meters).

    Fix: reading ki apni accuracy ko radius mein add karo, aur agar reading
    itni kharab ho ke kuch keh hi nahi sakte to verify skip kar do —
    employee ko galat "outside office" flag na lage.
    """
    result = {
        "verified": False,
        "distance": None,
        "accuracy": accuracy,
        "note": None,
        "message": "",
    }

    # ──── Office set hi nahi → verification skip ────
    if not office:
        result["verified"] = True
        result["note"] = "office_not_set"
        result["message"] = "Office location set nahi hai — verification skip"
        return result

    # ──── GPS mila hi nahi (permission deny / timeout) ────
    if lat is None or lng is None:
        result["verified"] = False
        result["note"] = "gps_unavailable"
        result["message"] = "GPS location nahi mili"
        return result

    distance = _calculate_distance(lat, lng, office.latitude, office.longitude)
    result["distance"] = round(distance, 1)

    # ──── Reading itni kharab ke judge nahi kar sakte ────
    if accuracy and accuracy > UNRELIABLE_ACCURACY_M:
        result["verified"] = True
        result["note"] = "gps_unreliable"
        result["message"] = (
            f"GPS accuracy bohot kam ({int(accuracy)}m) — verification skip"
        )
        return result

    # ──── Accuracy ko radius mein allowance ke tor pe add karo ────
    allowance = min(accuracy or 0, MAX_ACCURACY_ALLOWANCE_M)
    tolerance = office.radius_meters + allowance

    result["verified"] = distance <= tolerance
    result["note"] = "in_range" if result["verified"] else "out_of_range"
    result["message"] = (
        f"Office se {distance:.0f}m (allowed {tolerance:.0f}m)"
        if result["verified"] else
        f"Office se bahar — {distance:.0f}m (allowed {tolerance:.0f}m)"
    )
    return result


# ══════════════════════════════════════════════
# Check-in window helpers
# ══════════════════════════════════════════════
def _in_time_window(now_m: int, start_m: int, end_m: int) -> bool:
    """
    now_m window ke andar hai? Aadhi raat ko wrap karne wali window bhi
    handle hoti hai (misal 22:00 se 06:00 wali night shift).
    """
    if start_m <= end_m:
        return start_m <= now_m <= end_m
    return now_m >= start_m or now_m <= end_m


# Grace itni bari na ho ke window ka matlab hi khatam ho jaye
MAX_EARLY_GRACE_MINS = 12 * 60


def _checkin_window(policy, day: date, now: datetime) -> dict:
    """
    Check-in ki ijazat hai ya nahi.

    Rule: check-in sirf shift ke darmiyan.
      window = [shift_start - grace  ...  shift_end]
    Shift end ke baad check-in NAHI hota → banda us din absent rehta hai.
    (Check-out pe koi pabandi nahi — jitna der tak marzi kaam kare)

    Non-working day pe koi shift hi nahi hoti, isliye window apply nahi hota
    (weekend overtime possible rahe).
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
        result["message"] = "Aaj working day nahi — check-in window apply nahi hota"
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
        # ──── Seedhi window, shift khatam ho chuki ────
        result["reason"] = "shift_ended"
        result["message"] = (
            f"Shift {policy.shift_start} – {policy.shift_end} thi. "
            f"Ab {_fmt_hhmm(now_m)} ho chuke hain — check-in window band ho chuka hai. "
            f"Aaj absent mark hoga."
        )

    else:
        # ──── Window abhi khula nahi (ya night shift ka gap chal raha hai) ────
        result["reason"] = "too_early"
        result["message"] = (
            f"Check-in {result['opens_at']} se khulega "
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
    Photo DB mein save karo (compress kar ke).

    Photo save fail ho to attendance NAHI ruknI chahiye — attendance
    ka asal record time + GPS hai, photo sirf evidence hai.
    """
    if not base64_image:
        return False

    prepared = prepare_photo_for_db(base64_image)
    if not prepared:
        print(f"Photo prepare failed (kind={kind}, employee={employee_id})")
        return False

    try:
        # ──── Pehle se hai to replace karo (unique session_id+kind) ────
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
    Kis session ki kaunsi photos hain — ek hi query mein (N+1 se bachne ke liye).
    image_data column deliberately load NAHI karte, sirf kind.
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
    Employee ka abhi khula hua session.

    Sirf aaj ki date nahi dekhte — raat ko kaam karne wale ka session KAL
    ki date pe hota hai. Misal: 4 baje shaam check-in, raat 1 baje check-out
    (tab tak PKT date badal chuki hoti hai). Pehle aisa banda check-out hi
    nahi kar pata tha aur session hamesha ke liye khula reh jata tha.
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
    """Is din employee ki approved leave hai?"""
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
    Ek jaisa shape har jagah — frontend ko guess na karna pade.

    photo_kinds = is session ki available photos ({"checkin","checkout"}).
    None ho to purane file-path columns pe fall back karte hain (legacy rows).
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
    Employee ko dikhane wali policy — poori aur tayyar.

    ═══ YEH ALAG FUNCTION KYUN ═══
    Employee ko sirf shift ka waqt nahi, poora qanoon maloom hona chahiye:
    break kab aur kitni der, kitna pehle check-in ho sakta hai, kitni der
    baad late lagega, overtime kab shuru hoga. Pehle yeh sab sirf CEO ke
    Settings mein tha — employee ko kabhi bataya hi nahi gaya.

    Hisaab yahan hota hai, UI mein nahi. `late_after` (shift start +
    tolerance) jaisi cheez UI ko khud nahi jorni chahiye — warna wohi
    purani ghalti dobara hogi jahan UI apna hisaab lagata tha aur server
    se alag ho jata tha.
    """
    if not policy:
        return None

    start_m = _parse_hhmm(policy.shift_start)
    tolerance = policy.late_tolerance_mins or 0

    # ──── Break muqarrar waqt par hai ya jab chahein? ────
    b_start = _parse_hhmm(policy.break_start) if policy.break_start else None
    b_end = _parse_hhmm(policy.break_end) if policy.break_end else None
    fixed_break = b_start is not None and b_end is not None

    if fixed_break:
        # Waqt diya hai to muddat usi se nikalti hai — do jagah rakhi
        # hui muddat kabhi na kabhi ek dusre se alag ho jati hai
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

        # ──── Check-in ki hadd ────
        "enforce_shift_window": policy.enforce_shift_window,
        "early_checkin_grace_mins": policy.early_checkin_grace_mins,
        "checkin_opens_at": window.get("opens_at"),
        "checkin_closes_at": window.get("closes_at"),

        # ──── Late ────
        "late_tolerance_mins": tolerance,
        # Is waqt ke BAAD aana late hai — employee ko khud jorna na pare
        "late_after": _fmt_hhmm(start_m + tolerance) if start_m is not None else None,

        # ──── Break ────
        "break_policy": getattr(policy.break_policy, "value", policy.break_policy),
        "break_minutes": span,
        "break_start": policy.break_start if fixed_break else None,
        "break_end": policy.break_end if fixed_break else None,
        "break_is_fixed": fixed_break,

        # ──── Ghante ────
        "min_daily_hours": policy.min_daily_hours,
        "overtime_threshold": policy.overtime_threshold,
        "max_overtime_per_day": policy.max_overtime_per_day,
    }


def _no_session_status(policy, day: date, now: datetime, window: dict,
                       current_work_date: date) -> tuple:
    """
    Jis employee ka session nahi bana — wo ABSENT hai, ya abhi aana baqi hai?

    ═══ MASLA KYA THA ═══
    Pehle session na hone par seedha "Absent" likh dete the. Yani subah
    9 baje shift shuru hone se pehle hi poori team absent dikhti thi, aur
    raat ki shift mein to shift shuru hone se ghante pehle hi.

    ═══ SAHI USOOL ═══
    Absent wahi hai jo check-in ka MAUQA guzar jane ke baad bhi nahi aaya —
    yani jab check-in window band ho chuka ho (wahi lamha jab employee ka
    check-in button disable hota hai). Us se pehle wo sirf "abhi nahi aaya".

    Day shift ho ya night, dono par yehi hisaab lagta hai kyunki window
    khud shift se banta hai.

    Return: (status, note)
    """
    # ──── Guzra hua din — mauqa yaqeenan khatam ────
    if day < current_work_date:
        return "Absent", None

    # ──── Aane wala din ────
    if day > current_work_date:
        return "Upcoming", "Yeh din abhi aaya nahi"

    # ═══ Aaj ka din ═══
    if window.get("enforced"):
        reason = window.get("reason")
        if reason == "too_early":
            return "Upcoming", window.get("message")
        if window.get("open"):
            return "Not Checked In", "Check-in window abhi khula hai"
        # shift_ended — mauqa guzar gaya
        return "Absent", "Check-in window band ho chuka"

    # ──── Window enforce nahi — shift end hi hadd hai ────
    end_m = _parse_hhmm(policy.shift_end) if policy else None
    if end_m is None:
        # Policy hi nahi — kisi ko absent kehna jaldbazi hogi
        return "Not Checked In", None

    now_m = now.hour * 60 + now.minute
    start_m = _parse_hhmm(policy.shift_start)

    if start_m is not None and end_m < start_m:
        # ──── Raat ki shift ────
        # Is work date ki shift kabhi "guzar" nahi sakti: jaise hi wo
        # khatam hoti hai, work date khud agle din par chala jata hai.
        # To ya wo abhi aani hai (do shifton ke darmiyan ka gap), ya chal rahi hai.
        if end_m < now_m < start_m:
            return "Upcoming", "Shift raat ko shuru hogi"
        return "Not Checked In", None

    if now_m > end_m:
        return "Absent", "Shift khatam ho chuki"
    return "Not Checked In", None


def _absent_row(employee: User, day: date, working_day: bool) -> dict:
    """Jis employee ne check-in hi nahi kiya — same shape, saari values khali"""
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
        raise HTTPException(status_code=400, detail="Kam se kam 1 image chahiye")

    embedding = enroll_face_from_images(data.face_images)
    if not embedding:
        raise HTTPException(status_code=400, detail="Kisi bhi image mein face detect nahi hua")

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
    current_user: dict = Depends(get_current_user)
):
    _assert_self(current_user, data.employee_id)

    now = get_pkt_now()

    employee = _get_user_or_404(db, data.employee_id)
    company_id = _resolve_company_id(db, employee) or data.employee_id
    policy = _work_policy(db, company_id)

    # ──── Attendance ka DIN — calendar date nahi, shift ka din ────
    # Raat ki shift (22:00-05:00) mein 12 baje ke baad bhi din wahi rehta hai
    today = work_date_for(policy, now)

    # ──── Is din ka session pehle se hai? (checked-out bhi count hota hai) ────
    existing = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == data.employee_id,
        AttendanceSession.date == today
    ).first()

    if existing:
        if existing.status == AttendanceStatusEnum.checked_out:
            raise HTTPException(
                status_code=400,
                detail="Aaj ka din complete ho chuka hai — dobara check-in nahi ho sakta"
            )
        raise HTTPException(status_code=400, detail="Aap already checked in hain!")

    # ──── Kal ka session abhi khula to pehle wo band karo ────
    # Warna dangling session hamesha ke liye khula reh jayega
    dangling = _open_session(db, data.employee_id, today)
    if dangling:
        raise HTTPException(
            status_code=400,
            detail=f"{dangling.date} ka session abhi khula hai — "
                   f"pehle us ka check-out karein"
        )

    working_day = _is_working_day(policy, today)

    # ──── Check-in window — shift ke bahar check-in nahi ────
    window = _checkin_window(policy, today, now)
    if not window["open"]:
        raise HTTPException(status_code=400, detail=window["message"])

    # ──── Late check (sirf working day pe) ────
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
    db.flush()          # session.id chahiye photo ke liye

    # ──── Photo DB mein (sirf record ke liye — verification nahi) ────
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
    current_user: dict = Depends(get_current_user)
):
    _assert_self(current_user, data.employee_id)

    session = _open_session(
        db, data.employee_id, _employee_work_date(db, data.employee_id),
        AttendanceStatusEnum.checked_in
    )

    if not session:
        raise HTTPException(status_code=404, detail="Active session nahi mila")

    if data.session_id and data.session_id != session.id:
        raise HTTPException(status_code=400, detail="Session id match nahi kar raha")

    now = get_pkt_now()

    # ──── Pehle se koi open break to nahi ────
    open_interval = db.query(AttendanceInterval).filter(
        AttendanceInterval.session_id == session.id,
        AttendanceInterval.end_time == None
    ).first()

    if open_interval:
        raise HTTPException(status_code=400, detail="Aap already break pe hain")

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
    current_user: dict = Depends(get_current_user)
):
    _assert_self(current_user, data.employee_id)

    session = _open_session(
        db, data.employee_id, _employee_work_date(db, data.employee_id),
        AttendanceStatusEnum.paused
    )

    if not session:
        raise HTTPException(status_code=404, detail="Paused session nahi mila")

    if data.session_id and data.session_id != session.id:
        raise HTTPException(status_code=400, detail="Session id match nahi kar raha")

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
    current_user: dict = Depends(get_current_user)
):
    _assert_self(current_user, data.employee_id)

    today = _employee_work_date(db, data.employee_id)

    # ──── Check-out pe koi time pabandi nahi — raat gaye bhi ho sakta hai ────
    session = _open_session(db, data.employee_id, today)

    if not session:
        raise HTTPException(status_code=404, detail="Active session nahi mila")

    if data.session_id and data.session_id != session.id:
        raise HTTPException(status_code=400, detail="Session id match nahi kar raha")

    if not session.check_in_time:
        raise HTTPException(status_code=400, detail="Check-in time missing — check-out nahi ho sakta")

    now = get_pkt_now()

    # ──── Khule hue breaks band karo ────
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

    # ──── Under / Overtime (sirf working day pe) ────
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
        # ──── Off-day pe kaam = poora overtime ────
        overtime_minutes = int(min(net_hours * 60, (policy.max_overtime_per_day or 0) * 60))

    # ──── Shift end se pehle nikal gaya? ────
    is_early_checkout = False
    early_checkout_minutes = 0

    if policy and session.is_working_day and now.date() == session.date:
        shift_start_m = _parse_hhmm(policy.shift_start)
        shift_end_m = _parse_hhmm(policy.shift_end)
        # ──── Overnight shift (end < start) pe yeh calculation valid nahi ────
        if (shift_end_m is not None and shift_start_m is not None
                and shift_end_m > shift_start_m):
            checkout_m = now.hour * 60 + now.minute
            if checkout_m < shift_end_m:
                is_early_checkout = True
                early_checkout_minutes = shift_end_m - checkout_m

    # ──── Flag aur minutes hamesha match karein ────
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
    current_user: dict = Depends(get_current_user)
):
    employee = _assert_can_view(db, current_user, employee_id)

    try:
        target_date = date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format YYYY-MM-DD hona chahiye")

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
            "message": "Is din ka record nahi mila"
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
    current_user: dict = Depends(get_current_user)
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
        raise HTTPException(status_code=400, detail="Date format YYYY-MM-DD hona chahiye")

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
    current_user: dict = Depends(get_current_user)
):
    employee = _assert_can_view(db, current_user, employee_id)
    now = get_pkt_now()

    company_id = _resolve_company_id(db, employee)
    policy = _work_policy(db, company_id)

    # ──── Shift ka din, calendar ka nahi (raat ki shift ke liye) ────
    today = work_date_for(policy, now)
    window = _checkin_window(policy, today, now)

    session = db.query(AttendanceSession).filter(
        AttendanceSession.employee_id == employee_id,
        AttendanceSession.date == today
    ).first()

    # ──── Kal ka khula session (raat bhar kaam kiya) ────
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
                "Aaj approved leave pe hain" if leave
                else (window["message"] if not window["open"] else "Aaj check-in nahi ki")
            )
        }

    # ──── Ab tak ke breaks ────
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

    # ──── Session kal ka hai? (raat bhar ka kaam) ────
    out["is_previous_day_session"] = session.date != today
    if out["is_previous_day_session"]:
        out["message"] = f"{session.date} ka session abhi khula hai — check-out karein"

    # ──── Live elapsed seconds — timer refresh pe reset na ho ────
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
    current_user: dict = Depends(get_current_user)
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
    current_user: dict = Depends(get_current_user)
):
    # ──── Apna hi face enroll ho sakta hai ────
    employee_id = current_user["user_id"]

    if not data.face_images:
        raise HTTPException(status_code=400, detail="Kam se kam 1 image chahiye")

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

    # ──── Reference photo bhi DB mein rakho (pehle kahin save nahi hoti thi) ────
    photo_saved = _store_photo(
        db, data.face_images[0], PhotoKindEnum.enrollment,
        employee_id=employee_id, company_id=company_id, captured_at=now,
    )

    db.commit()

    return {"message": message, "enrolled": True, "photo_saved": photo_saved}


# ══════════════════════════════════════════════
# Route 11: CEO — Team Attendance (kisi bhi din ka)
# ══════════════════════════════════════════════
@router.get("/flags/today")
def get_team_attendance(
    report_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo)
):
    """
    Poori team ka attendance — un employees ke saath jinhone check-in
    nahi kiya (Absent / On Leave). Pehle sirf checked-in wale aate the
    isliye 'Absent' count hamesha 0 rehta tha.
    """
    ceo = _get_user_or_404(db, current_user["user_id"])
    company_id = ceo.id
    now_pkt = get_pkt_now()

    # Aaj ka SHIFT wala din — is se pata chalta hai ke maanga gaya din
    # guzar chuka hai, chal raha hai, ya abhi aaya hi nahi
    current_work_date = _work_date(db, company_id, now_pkt)

    try:
        day = (date.fromisoformat(report_date) if report_date
               else current_work_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date format YYYY-MM-DD hona chahiye")

    employees = _company_employees(db, ceo)
    employee_ids = [e.id for e in employees]

    # ──── Ek query mein saare sessions (N+1 nahi) ────
    sessions = db.query(AttendanceSession).filter(
        AttendanceSession.company_id == company_id,
        AttendanceSession.date == day
    ).all()
    session_map = {s.employee_id: s for s in sessions}

    # ──── Ek query mein approved leaves ────
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
                # Absent kehna jaldbazi hogi agar check-in ka mauqa
                # abhi baqi hai — window band hone par hi Absent
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
    pending = _count("Not Checked In")     # abhi aa sakte hain
    upcoming = _count("Upcoming")          # shift shuru hi nahi hui

    return {
        "date": str(day),
        "server_time": str(now_pkt),
        "is_working_day": working_day,
        "total": len(rows),

        # ──── Shift ka status — UI isi se batata hai ke Absent
        #      final hai ya abhi count chal raha hai ────
        "shift_state": {
            "is_today": day == current_work_date,
            "is_past": day < current_work_date,
            "checkin_open": bool(window.get("open")) if window.get("enforced") else None,
            "window_reason": window.get("reason"),
            "window_message": window.get("message"),
            "opens_at": window.get("opens_at"),
            "closes_at": window.get("closes_at"),
            "attendance_final": day < current_work_date or (
                window.get("enforced") and window.get("reason") == "shift_ended"
            ),
        },

        # ──── Active policy — dashboard pe dikhane ke liye ────
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
    current_user: dict = Depends(get_current_user)
):
    _assert_can_view(db, current_user, employee_id)

    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="Month 1-12 ke beech hona chahiye")

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
    Chart data — pehle frontend Math.random() se dummy bana raha tha.
    weekly  = pichhle 7 din
    monthly = current month ka har din (aaj tak)
    """
    ceo = _get_user_or_404(db, current_user["user_id"])
    company_id = ceo.id
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

    # Aaj ka din abhi chal raha hai — jo abhi tak nahi aaya wo "absent"
    # nahi, "pending" hai. Absent tabhi jab check-in window band ho jaye.
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
            absent = pending = 0        # off day — koi absent nahi
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
# Route 14: Attendance Photo (CEO ya khud employee)
# ══════════════════════════════════════════════
@router.get("/photo/{session_id}/{kind}")
def get_attendance_photo(
    session_id: int,
    kind: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Photo DB se serve hoti hai. Purane records ki file abhi bhi chal jaayegi."""
    if kind not in ("checkin", "checkout"):
        raise HTTPException(status_code=400, detail="kind 'checkin' ya 'checkout' hona chahiye")

    session = db.query(AttendanceSession).filter(
        AttendanceSession.id == session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session nahi mila")

    _assert_can_view(db, current_user, session.employee_id)

    # ──── 1. DB (asal jagah) ────
    photo = db.query(AttendancePhoto).filter(
        AttendancePhoto.session_id == session_id,
        AttendancePhoto.kind == kind
    ).first()

    if photo:
        return Response(
            content=photo.image_data,
            media_type=photo.mime_type or "image/jpeg",
            headers={
                # ──── Attendance photo badalti nahi — browser cache kar le ────
                "Cache-Control": "private, max-age=86400",
                "Content-Disposition":
                    f'inline; filename="{kind}_{session.employee_id}_{session.date}.jpg"',
            },
        )

    # ──── 2. Legacy file fallback ────
    path = session.check_in_face_image if kind == "checkin" else session.check_out_face_image
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/jpeg", filename=os.path.basename(path))

    raise HTTPException(status_code=404, detail="Photo available nahi hai")


# ══════════════════════════════════════════════
# Route 14b: Enrollment Photo
# ══════════════════════════════════════════════
@router.get("/enrollment-photo/{employee_id}")
def get_enrollment_photo(
    employee_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Employee ki enrollment reference photo — CEO compare kar sake"""
    _assert_can_view(db, current_user, employee_id)

    photo = db.query(AttendancePhoto).filter(
        AttendancePhoto.employee_id == employee_id,
        AttendancePhoto.kind == PhotoKindEnum.enrollment
    ).first()

    if not photo:
        raise HTTPException(status_code=404, detail="Enrollment photo available nahi hai")

    return Response(
        content=photo.image_data,
        media_type=photo.mime_type or "image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ══════════════════════════════════════════════
# Route 15: Employee — Office info (check-in se pehle)
# ══════════════════════════════════════════════
@router.get("/my-office")
def get_my_office(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Employee ko pata ho office kahan hai aur radius kya hai"""
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
        # ──── Poori policy — employee ko bhi maloom honi chahiye ────
        "policy": _policy_view(policy, window),
        "checkin_window": window,
        "is_working_day": _is_working_day(policy, today),
        # ──── Attendance ka din (raat ki shift mein calendar se alag hota hai) ────
        "work_date": str(today),
        "is_overnight_shift": is_overnight_shift(policy),
        "server_date": str(get_pkt_now().date()),
        "server_time": str(now),
    }
