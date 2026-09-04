"""
The CEO's HR console
────────────────────
    POST /hr/ask          ask the company anything
    GET  /hr/overview     the numbers, without asking
    GET  /hr/settings     this company's thresholds
    PUT  /hr/settings     change them

═══════════════════════════════════════════════════════════
EVERY ROUTE HERE IS require_ceo. THAT IS THE WHOLE BOUNDARY.
═══════════════════════════════════════════════════════════
`/chat/*` answers "what about me" and is open to employees. `/hr/*`
answers "what about us" and is not. The two never meet: the employee's
tool table has no company-wide function in it, and this router is not
reachable without a CEO token.

That separation is why an employee cannot phrase their way into another
person's salary. There is no phrasing — the code that could return it is
not in the dictionary their question is routed against.

═══════════════════════════════════════════════════════════
THE CONSOLE READS. IT DOES NOT ACT.
═══════════════════════════════════════════════════════════
No route here approves leave, resolves a request, changes a salary or
closes a case. The CEO has a screen with a confirmation for each of
those. A console that could act on a sentence would be one typo away
from approving the wrong month's payroll.

`PUT /hr/settings` is the exception, and it is a form with named fields,
not a sentence.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.chat import HrSettings
from app.models.user import User
from app.utils.chat_cases import get_settings, stale_cases
from app.utils.company import require_ceo, get_user_or_404
from app.utils.hr_company_data import run_company_tools

router = APIRouter(prefix="/hr", tags=["HR Console"])

MAX_QUESTION_CHARS = 1000
CONTEXT_TURNS = 6

# Which settings a CEO may change, and the range each may hold. A
# threshold of 0 switches a check off; the ceilings stop a typo turning
# "6 late days" into "600" and silently disabling the whole thing.
SETTING_LIMITS = {
    "probation_days":                (0, 730),
    "probation_notice_days":         (0, 90),
    "leave_expiry_notice_days":      (0, 365),
    "leave_low_balance_days":        (0, 60),
    "late_pattern_count":            (0, 100),
    "late_pattern_window_days":      (1, 365),
    "absence_pattern_count":         (0, 100),
    "absence_pattern_window_days":   (1, 365),
    "case_stale_days":               (0, 90),
    "request_sla_days":              (0, 90),
    "grievance_cluster_count":       (0, 100),
    "grievance_cluster_window_days": (1, 730),
}

BOOL_SETTINGS = ("proactive_to_employee", "proactive_to_ceo")


class AskIn(BaseModel):
    text: str = Field(min_length=1)
    session_id: Optional[int] = None


class SettingsIn(BaseModel):
    """Only the fields being changed need to be sent."""
    probation_days: Optional[int] = None
    probation_notice_days: Optional[int] = None
    leave_expiry_notice_days: Optional[int] = None
    leave_low_balance_days: Optional[int] = None
    late_pattern_count: Optional[int] = None
    late_pattern_window_days: Optional[int] = None
    absence_pattern_count: Optional[int] = None
    absence_pattern_window_days: Optional[int] = None
    case_stale_days: Optional[int] = None
    request_sla_days: Optional[int] = None
    grievance_cluster_count: Optional[int] = None
    grievance_cluster_window_days: Optional[int] = None
    proactive_to_employee: Optional[bool] = None
    proactive_to_ceo: Optional[bool] = None


# ══════════════════════════════════════════════
# Ask
# ══════════════════════════════════════════════
@router.post("/ask")
def ask(
    data: AskIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    A question about the company, answered from the company's own data.

    ═══ WHY THE TURNS ARE STORED NOW ═══
    They used to come from the browser, on the reasoning that a console
    is a tool rather than a record. In practice that meant switching to
    another tab threw the conversation away — and, worse, "show May
    month" straight after "show me Wasi's slip" had nothing to carry the
    name forward with, so it was answered with company-wide totals.

    The thread is the CEO's own, in their own company, listed only by
    `/hr/sessions`. It never appears in an employee's sidebar.
    """
    from app.models.chat import ChatMessage

    ceo = get_user_or_404(db, current_user["user_id"])

    text = data.text.strip()
    if not text:
        raise HTTPException(400, "Please type a question")
    if len(text) > MAX_QUESTION_CHARS:
        raise HTTPException(
            400, f"That question is too long (limit {MAX_QUESTION_CHARS})")

    session = _console_session(db, ceo.company_id, data.session_id)

    # `sources` travels with the reply, not just the text: a follow-up
    # like "how many of them are from Backend" is answered by reusing the
    # tools that produced the answer it refers to.
    history = [
        {"role": m.role, "text": m.text, "sources": m.sources or []}
        for m in db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.id.desc())
        .limit(CONTEXT_TURNS)
        .all()
    ][::-1]

    db.add(ChatMessage(session_id=session.id, role="ceo", text=text))
    db.flush()

    try:
        from app.agents.hr_console_agent import ask_console

        out = ask_console(db=db, company_id=current_user["company_id"],
                          ceo_name=ceo.full_name or "", message=text,
                          history=history)
    except Exception as e:                              # noqa: BLE001
        print(f"[hr] console unavailable: {e}")
        out = {"reply": "I could not pull that together just now — give me "
                        "a moment and ask again.",
               "sources": [], "attachments": [], "language": "english"}

    db.add(ChatMessage(session_id=session.id, role="hr", text=out["reply"],
                       sources=out.get("sources") or []))
    session.last_active_at = datetime.utcnow()
    if not session.title:
        title = " ".join(text.split())
        session.title = title[:57] + "…" if len(title) > 60 else title
    db.commit()

    return {**out, "session_id": session.id}


# ══════════════════════════════════════════════
# Overview — the numbers without asking for them
# ══════════════════════════════════════════════
@router.get("/overview")
def overview(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    What the console would say if nobody asked.

    Everything on this page is a count or a total. Nothing here names a
    person in connection with a confidential case.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    data = run_company_tools(db, ceo.company_id, [
        "headcount", "new_joiners", "attendance_outliers",
        "leave_overview", "payroll_overview", "open_items", "case_patterns",
    ])

    stale = stale_cases(db, ceo.company_id)
    db.commit()          # get_settings may have created the settings row

    now = datetime.utcnow()
    return {
        **data,
        # Concern and age only. Not the subject, and never the facts — a
        # stale grievance is still a grievance, and the CEO learning that
        # one exists is different from learning what is in it.
        "stale_cases": [{
            "case_id": c.id,
            "concern": c.concern,
            "stage": c.stage,
            "days_quiet": (now - c.last_touched_at).days
            if c.last_touched_at else 0,
            "last_touched": str(c.last_touched_at)[:19]
            if c.last_touched_at else None,
        } for c in stale],
    }


# ══════════════════════════════════════════════
# Settings — every number this company decides by
# ══════════════════════════════════════════════
@router.get("/settings")
def read_settings(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    ceo = get_user_or_404(db, current_user["user_id"])
    s = get_settings(db, ceo.company_id)
    db.commit()

    return {
        "settings": {
            **{k: getattr(s, k) for k in SETTING_LIMITS},
            **{k: getattr(s, k) for k in BOOL_SETTINGS},
        },
        "limits": {k: {"min": lo, "max": hi}
                   for k, (lo, hi) in SETTING_LIMITS.items()},
        "updated_at": str(s.updated_at)[:19] if s.updated_at else None,
    }


@router.put("/settings")
def update_settings(
    data: SettingsIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    Change this company's thresholds.

    Nothing in the HR desk decides anything by a number written in code.
    This is where those numbers live, and this is the only way to change
    them.
    """
    ceo = get_user_or_404(db, current_user["user_id"])
    s = get_settings(db, ceo.company_id)

    changed = {}
    for field, (lo, hi) in SETTING_LIMITS.items():
        value = getattr(data, field, None)
        if value is None:
            continue
        if not (lo <= value <= hi):
            raise HTTPException(
                400, f"{field} must be between {lo} and {hi}")
        if getattr(s, field) != value:
            changed[field] = value
            setattr(s, field, value)

    for field in BOOL_SETTINGS:
        value = getattr(data, field, None)
        if value is None:
            continue
        if getattr(s, field) != value:
            changed[field] = value
            setattr(s, field, value)

    if changed:
        s.set_by = ceo.id
        db.add(s)
        db.commit()
    else:
        db.commit()

    return {"message": "Saved" if changed else "Nothing changed",
            "changed": changed}


# ══════════════════════════════════════════════
# Employment records — the leavers' file
# ══════════════════════════════════════════════
# Free text is fine for the note; the REASON is a fixed list, because it
# is the field somebody will eventually filter on. "How many people did
# we let go last year" cannot be answered against prose.
END_REASONS = ("terminated", "resigned", "retired", "contract_ended")

# The status each reason leaves on the user row. Kept beside the reasons
# rather than inferred, so adding a reason forces a decision about it.
REASON_STATUS = {
    "terminated": "fired",
    "resigned": "resigned",
    "retired": "retired",
    "contract_ended": "contract_ended",
}


class EndEmploymentIn(BaseModel):
    employee_id: int
    reason: str = "terminated"
    ended_on: Optional[str] = None          # YYYY-MM-DD, defaults to today
    note: Optional[str] = None
    final_settlement_done: bool = False


@router.get("/former")
def list_former(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """Everyone who used to work here, and what their file says."""
    ceo = get_user_or_404(db, current_user["user_id"])
    data = run_company_tools(db, ceo.company_id, ["former_employees"])
    db.commit()
    return data.get("former_employees") or {"count": 0, "former_employees": []}


@router.post("/end-employment")
def end_employment(
    data: EndEmploymentIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    Record that somebody has left, and stop treating them as staff.

    ═══ WHY THIS IS ONE ACTION AND NOT TWO ═══
    Changing the status and writing the record have to happen together.
    Doing only the first leaves a person marked as gone with no account
    of when or why — which is precisely the row a labour dispute asks
    for. Doing only the second leaves them on the payroll.

    The user row itself is NOT deleted. Their payslips, attendance and
    leave all point at it, and that history is the part a company is
    obliged to keep.
    """
    from datetime import date as _date

    from app.models.chat import EmploymentRecord
    from app.utils.workforce import FORMER

    ceo = get_user_or_404(db, current_user["user_id"])

    reason = (data.reason or "terminated").strip().lower()
    if reason not in END_REASONS:
        raise HTTPException(
            400, f"Reason must be one of: {', '.join(END_REASONS)}")

    employee = db.query(User).filter(
        User.id == data.employee_id,
        User.company_name == ceo.company_name,
        User.role == "employee",
    ).first()
    if not employee:
        raise HTTPException(404, "Employee not found in your company")

    if employee.status in FORMER:
        raise HTTPException(
            400, f"{employee.full_name} is already recorded as {employee.status}")

    ended = _date.today()
    if data.ended_on:
        try:
            ended = _date.fromisoformat(data.ended_on)
        except ValueError:
            raise HTTPException(400, "ended_on must be YYYY-MM-DD")

    record = EmploymentRecord(
        employee_id=employee.id,
        company_id=current_user["company_id"],
        # Copied now, not looked up later — a leaver's record should read
        # the way it did on the day they left, even if a department is
        # renamed afterwards.
        name_at_exit=employee.full_name,
        department_at_exit=employee.department,
        joined_on=employee.joining_date,
        ended_on=ended,
        end_reason=reason,
        end_note=(data.note or "").strip() or None,
        final_settlement_done=bool(data.final_settlement_done),
        recorded_by=ceo.id,
    )
    db.add(record)

    employee.status = REASON_STATUS[reason]
    db.add(employee)
    db.commit()
    db.refresh(record)

    return {
        "message": f"{employee.full_name} recorded as {employee.status}",
        "record_id": record.id,
        "employee_id": employee.id,
        "status": employee.status,
        "ended_on": str(ended),
        "note": "They will no longer be paid, emailed or counted as staff. "
                "Their payroll and attendance history is kept.",
    }


@router.post("/former/{record_id}/settle")
def mark_settled(
    record_id: int,
    note: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """Final settlement paid — the company owes them nothing further."""
    from app.models.chat import EmploymentRecord

    ceo = get_user_or_404(db, current_user["user_id"])
    rec = db.query(EmploymentRecord).filter(
        EmploymentRecord.id == record_id,
        EmploymentRecord.company_id == ceo.company_id,
    ).first()
    if not rec:
        raise HTTPException(404, "Record not found")

    rec.final_settlement_done = True
    rec.final_settlement_note = (note or "").strip() or None
    db.add(rec)
    db.commit()
    return {"message": "Final settlement recorded", "record_id": rec.id}


# ══════════════════════════════════════════════
# Console conversations
# ══════════════════════════════════════════════
# The console used to keep its turns in the browser only, which meant
# switching tabs threw the conversation away — and "show May month" after
# "show me Wasi's slip" had nothing to carry the name forward with.
#
# They live in `chat_sessions` with `kind = "console"`, so an employee's
# help desk and a CEO's console can never appear in each other's list.
CONSOLE_KIND = "console"
CONSOLE_TURNS = 8


def _console_session(db: Session, ceo_id: int, session_id: Optional[int]):
    """The CEO's own console thread, or a new one."""
    from app.models.chat import ChatSession

    if session_id:
        s = db.query(ChatSession).filter(
            ChatSession.id == session_id,
            ChatSession.employee_id == ceo_id,
            ChatSession.kind == CONSOLE_KIND,
        ).first()
        if s:
            return s
        # Someone else's id gets a fresh thread, not an error and not
        # their conversation.
    s = ChatSession(employee_id=ceo_id, company_id=ceo_id,
                    kind=CONSOLE_KIND, title=None)
    db.add(s)
    db.flush()
    return s


@router.get("/sessions")
def console_sessions(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """The CEO's past console conversations. Only ever their own."""
    from app.models.chat import ChatSession

    ceo = get_user_or_404(db, current_user["user_id"])
    rows = db.query(ChatSession).filter(
        ChatSession.employee_id == ceo.id,
        ChatSession.kind == CONSOLE_KIND,
    ).order_by(ChatSession.last_active_at.desc()).limit(30).all()

    return {"sessions": [{
        "session_id": s.id,
        "title": s.title or "New conversation",
        "last_active_at": str(s.last_active_at)[:19] if s.last_active_at else None,
    } for s in rows]}


@router.get("/session/{session_id}")
def console_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    from app.models.chat import ChatMessage, ChatSession

    ceo = get_user_or_404(db, current_user["user_id"])
    s = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.employee_id == ceo.id,
        ChatSession.kind == CONSOLE_KIND,
    ).first()
    if not s:
        raise HTTPException(404, "Conversation not found")

    msgs = db.query(ChatMessage).filter(
        ChatMessage.session_id == s.id).order_by(ChatMessage.id).all()

    return {
        "session_id": s.id,
        "title": s.title,
        "messages": [{
            "id": m.id,
            "role": m.role,
            "text": m.text,
            "sources": m.sources or [],
            "at": str(m.created_at)[:19] if m.created_at else None,
        } for m in msgs],
    }


@router.delete("/session/{session_id}")
def delete_console_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    from app.models.chat import ChatMessage, ChatSession

    ceo = get_user_or_404(db, current_user["user_id"])
    s = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.employee_id == ceo.id,
        ChatSession.kind == CONSOLE_KIND,
    ).first()
    if not s:
        raise HTTPException(404, "Conversation not found")

    db.query(ChatMessage).filter(
        ChatMessage.session_id == s.id).delete(synchronize_session=False)
    db.delete(s)
    db.commit()
    return {"message": "Conversation deleted"}
