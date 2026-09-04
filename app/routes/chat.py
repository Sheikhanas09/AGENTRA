"""
HR Help Desk routes
───────────────────
    POST   /chat/message            employee asks something
    POST   /chat/confirm            employee confirms a drafted request
    GET    /chat/sessions           their own threads
    GET    /chat/session/{id}       one thread
    DELETE /chat/session/{id}       delete a thread

    GET    /chat/requests           employee: own · CEO: whole company
    POST   /chat/requests/{id}/resolve      CEO decides

═══════════════════════════════════════════════════════════
SECURITY — THIS IS THE MOST EXPOSED SURFACE IN THE SYSTEM
═══════════════════════════════════════════════════════════
Everywhere else a user submits a form with fixed fields. Here they type
free text that reaches a language model. So four rules, and none of them
depend on the model behaving:

  1. `employee_id` comes from the JWT. It is never read from the body,
     never from a query string, never from the message text. There is no
     parameter on any route here that lets a caller name someone else.

  2. Every tool filters on that id (`utils/chat_data.py`). Even a model
     that is talked into asking for another person's salary gets a query
     scoped to the caller.

  3. Session ownership is checked on every read and delete. Guessing an
     id gets a 404, not someone else's transcript.

  4. A transcript is private to its employee. No route here lets the CEO
     read one — what reaches the CEO is the `hr_requests` row, which is
     the part addressed to them.

═══════════════════════════════════════════════════════════
THE HELP DESK NEVER CREATES A LEAVE REQUEST
═══════════════════════════════════════════════════════════
`/chat/message` can return a leave DRAFT. Confirming it does not create
anything here — the frontend posts the confirmed values to
`POST /leave/request`, the same route the Leave tab uses.

That keeps balance, overlap, advance notice, certificate rules and the
CEO's approval flow in ONE place. A second path would drift from the
first, and the drift would be discovered in someone's salary.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.utils.tenancy import Tenant, get_tenant, require_ceo
from app.models.chat import ChatSession, ChatMessage, HrRequest
from app.models.user import User
from app.utils.company import (
    require_ceo, get_user_or_404, resolve_company_id, company_employees,
)
from app.utils.security import get_current_user

router = APIRouter(prefix="/chat", tags=["HR Help Desk"])

# A single message. Long enough for a real question, short enough that
# nobody pastes a novel into the prompt.
MAX_MESSAGE_CHARS = 2000

# Turns kept per thread for context. Older ones stay in the DB and are
# still readable — they are simply not sent to the model.
CONTEXT_TURNS = 8

REQUEST_KINDS = ("document", "advance", "correction", "complaint",
                 "question", "other")


# ══════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════
class MessageIn(BaseModel):
    text: str = Field(min_length=1)
    session_id: Optional[int] = None


class ConfirmIn(BaseModel):
    """
    Confirming a drafted HR request.

    Leave is NOT confirmed here — the frontend sends that to
    `/leave/request` so there is only ever one way a leave request is born.
    """
    kind: str = "other"
    subject: str = Field(min_length=3, max_length=200)
    body: Optional[str] = None
    session_id: Optional[int] = None


class ResolveIn(BaseModel):
    status: str = "resolved"          # resolved | rejected
    note: Optional[str] = None


# ══════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════
def _own_session(db: Session, session_id: int, employee_id: int) -> ChatSession:
    """
    A thread, but only if it belongs to the caller.

    404 rather than 403 on someone else's id — a 403 would confirm that
    the thread exists.
    """
    s = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.employee_id == employee_id,
        ChatSession.kind == "employee",
    ).first()
    if not s:
        raise HTTPException(404, "Conversation not found")
    return s


def _title_from(text: str) -> str:
    t = " ".join(text.split())
    return t[:57] + "…" if len(t) > 60 else t


def _msg_out(m: ChatMessage) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "text": m.text,
        "sources": m.sources or [],
        "at": str(m.created_at)[:19] if m.created_at else None,
    }


# ══════════════════════════════════════════════
# Route 1: Ask something
# ══════════════════════════════════════════════
@router.post("/message")
def send_message(
    data: MessageIn,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    """
    The employee asks; the help desk answers.

    The whole turn is stored — question, answer, and what the answer was
    built from. When someone later says "the help desk told me I had five
    days", the thread shows exactly what was said and which records it
    came from.
    """
    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user) or user.id

    text = data.text.strip()
    if not text:
        raise HTTPException(400, "Please type a message")
    if len(text) > MAX_MESSAGE_CHARS:
        raise HTTPException(
            400,
            f"That message is too long (limit {MAX_MESSAGE_CHARS} characters)",
        )

    # ──── Thread ────
    if data.session_id:
        session = _own_session(db, data.session_id, user.id)
    else:
        session = ChatSession(
            employee_id=user.id,
            company_id=company_id,
            title=_title_from(text),
        )
        db.add(session)
        db.flush()

    # `sources` travels with the reply, not just its text: "what about
    # July?" is answered by reusing the tools that answered the question
    # before it. Without this the desk changed subject mid-thread and
    # then invented figures for the new one.
    history = [
        {"role": m.role, "text": m.text, "sources": m.sources or []}
        for m in db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.id.desc())
        .limit(CONTEXT_TURNS)
        .all()
    ][::-1]

    db.add(ChatMessage(session_id=session.id, role="employee", text=text))
    db.flush()

    # ──── Answer ────
    # The import sits inside the handler for the same reason the leave
    # agent's does: a missing GROQ_API_KEY or a broken ML package must
    # not stop the module from loading.
    try:
        from app.agents.chat_agent import answer_message

        out = answer_message(
            db=db,
            employee_id=user.id,
            company_id=company_id,
            employee_name=user.full_name or "",
            message=text,
            history=history,
            session_id=session.id,
        )
    except Exception as e:                              # noqa: BLE001
        print(f"[chat] agent unavailable: {e}")
        out = {
            "reply": "I could not pull that up just now — give me a "
                     "moment and ask me again.",
            "intent": "error",
            "sources": [],
            "attachments": [],
            "action": None,
            "language": "english",
        }

    reply = ChatMessage(
        session_id=session.id,
        role="hr",
        text=out["reply"],
        intent=out.get("intent"),
        sources=out.get("sources") or [],
    )
    db.add(reply)

    session.last_active_at = datetime.utcnow()
    if not session.title:
        session.title = _title_from(text)

    db.commit()
    db.refresh(reply)

    return {
        "session_id": session.id,
        "message": _msg_out(reply),
        "attachments": out.get("attachments") or [],
        # A draft only. Nothing has been created yet.
        "action": out.get("action"),
        "language": out.get("language", "english"),
    }


# ══════════════════════════════════════════════
# Route 2: Confirm a drafted request
# ══════════════════════════════════════════════
@router.post("/confirm")
def confirm_request(
    data: ConfirmIn,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    """
    Turn a draft into an `hr_requests` row the CEO will see.

    Only reached after the employee pressed Confirm on a card that showed
    them exactly what would be sent.
    """
    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user) or user.id

    if user.role in ("ceo", "superadmin"):
        raise HTTPException(400, "The help desk is for employees")

    kind = (data.kind or "other").strip().lower()
    if kind not in REQUEST_KINDS:
        kind = "other"

    req = HrRequest(
        company_id=company_id,
        employee_id=user.id,
        kind=kind,
        subject=data.subject.strip(),
        body=(data.body or "").strip() or None,
        source="chat",
        status="open",
    )
    db.add(req)
    db.flush()

    # ──── Leave a trace in the thread ────
    # Otherwise the employee scrolls up next week and cannot tell whether
    # they actually sent it.
    if data.session_id:
        session = _own_session(db, data.session_id, user.id)
        db.add(ChatMessage(
            session_id=session.id,
            role="hr",
            text=f"Noted — “{req.subject}”. I am looking into it and will "
                 f"come back to you here.",
            intent="request_created",
            sources=[{"kind": "hr_request", "id": req.id}],
        ))
        session.last_active_at = datetime.utcnow()

    # ──── Tell the CEO ────
    # In the background, and never able to stop the request being saved.
    try:
        from app.utils import notify

        # ═══ A COMPANY ID IS NOT A USER ID ═══
        # This read `User.id == company_id`, which was true only while a
        # company WAS its CEO's user row. For any company registered since,
        # it returns None — and the email simply never goes, with nothing
        # said anywhere.
        ceo = db.query(User).filter(
            User.company_id == company_id, User.role == "ceo").first()
        if ceo and ceo.email:
            notify.send_email(
                company_id=company_id,
                to=ceo.email,
                subject=f"HR request — {user.full_name}",
                body=(
                    f"Dear {ceo.full_name},\n\n"
                    f"{user.full_name} has raised a request through the HR "
                    f"help desk.\n\n"
                    f"{'━' * 40}\n"
                    f"Type    : {kind}\n"
                    f"Subject : {req.subject}\n"
                    f"{'━' * 40}\n\n"
                    f"Open Requests on the Agentra dashboard to respond."
                ),
            )
    except Exception as e:                              # noqa: BLE001
        print(f"[chat] could not notify the CEO: {e}")

    db.commit()

    return {
        "message": "Noted",
        "request_id": req.id,
        "subject": req.subject,
        "status": req.status,
    }


# ══════════════════════════════════════════════
# Route 3-5: The employee's own threads
# ══════════════════════════════════════════════
@router.get("/sessions")
def list_sessions(
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    """Only ever the caller's own — there is no parameter for anyone else."""
    rows = db.query(ChatSession).filter(
        ChatSession.employee_id == current_user["user_id"],
        ChatSession.kind == "employee",
    ).order_by(ChatSession.last_active_at.desc()).limit(30).all()

    return {
        "sessions": [{
            "session_id": s.id,
            "title": s.title or "New conversation",
            "last_active_at": str(s.last_active_at)[:19] if s.last_active_at else None,
        } for s in rows]
    }


@router.get("/session/{session_id}")
def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    session = _own_session(db, session_id, current_user["user_id"])
    msgs = db.query(ChatMessage).filter(
        ChatMessage.session_id == session.id
    ).order_by(ChatMessage.id).all()

    return {
        "session_id": session.id,
        "title": session.title,
        "messages": [_msg_out(m) for m in msgs],
    }


@router.delete("/session/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    """
    The employee can delete their own thread.

    Any `hr_requests` raised from it stay — those belong to the CEO's
    queue, not to the conversation.
    """
    session = _own_session(db, session_id, current_user["user_id"])
    db.query(ChatMessage).filter(ChatMessage.session_id == session.id).delete(
        synchronize_session=False
    )
    db.delete(session)
    db.commit()
    return {"message": "Conversation deleted"}


# ══════════════════════════════════════════════
# Route 6: Requests — employee sees own, CEO sees the company
# ══════════════════════════════════════════════
@router.get("/requests")
def list_requests(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user) or user.id
    is_ceo = current_user["role"] in ("ceo", "superadmin")

    q = db.query(HrRequest).filter(HrRequest.company_id == company_id)
    if not is_ceo:
        # An employee sees their own and nobody else's
        q = q.filter(HrRequest.employee_id == user.id)
    if status:
        q = q.filter(HrRequest.status == status)

    rows = q.order_by(HrRequest.id.desc()).limit(200).all()

    names = {}
    if is_ceo:
        names = {u.id: u.full_name for u in company_employees(db, user)}

    return {
        "total": len(rows),
        "open": len([r for r in rows if r.status == "open"]),
        "requests": [{
            "request_id": r.id,
            "employee_id": r.employee_id,
            "employee_name": names.get(r.employee_id) if is_ceo else user.full_name,
            "kind": r.kind,
            "subject": r.subject,
            "body": r.body,
            "status": r.status,
            "ceo_note": r.ceo_note,
            "created_at": str(r.created_at)[:19] if r.created_at else None,
            "resolved_at": str(r.resolved_at)[:19] if r.resolved_at else None,
        } for r in rows],
    }


# ══════════════════════════════════════════════
# Route 7: CEO answers a request
# ══════════════════════════════════════════════
@router.post("/requests/{request_id}/resolve")
def resolve_request(
    request_id: int,
    data: ResolveIn,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_ceo),
):
    """
    The CEO closes a request.

    The `company_id` filter is not optional: without it a CEO could pass
    another company's request id and answer it.
    """
    ceo = get_user_or_404(db, current_user["user_id"])

    req = db.query(HrRequest).filter(
        HrRequest.id == request_id,
        HrRequest.company_id == ceo.company_id,
    ).first()
    if not req:
        raise HTTPException(404, "Request not found")

    status = (data.status or "resolved").strip().lower()
    if status not in ("resolved", "rejected"):
        raise HTTPException(400, "Status must be 'resolved' or 'rejected'")

    # ──── A rejection has to say why ────
    # The employee sees this note, and "rejected" on its own tells them
    # nothing.
    note = (data.note or "").strip()
    if status == "rejected" and len(note) < 5:
        raise HTTPException(
            400, "Please give a reason — the employee will see it"
        )

    req.status = status
    req.ceo_note = note or None
    req.resolved_at = datetime.utcnow()
    req.resolved_by = ceo.id

    # ──── The answer lands back in the employee's own thread ────
    # They asked in the chat; they should be told in the chat, not have to
    # go hunting for a status somewhere else.
    # ⚠ `kind` FILTER YAHAN LAZMI HAI.
    # `/chat/message` par `Depends(get_tenant)` hai, yani CEO bhi ye help
    # desk use kar sakta hai — aur uska console thread hamesha zyada
    # recent hota hai (ek CEO ke console mein 70 messages the jab yeh
    # pakda gaya). Bina filter ke ye query us console thread ko chun leti
    # thi, to "Your request has been approved" console transcript mein
    # gir jata aur jis thread mein sawal poocha gaya tha wahan jawab
    # kabhi na aata.
    #
    # Leak nahi tha — wohi shakhs, wohi company. Magar comment upar
    # "employee's own thread" kehta hai, aur code kuch aur karta tha.
    session = db.query(ChatSession).filter(
        ChatSession.employee_id == req.employee_id,
        ChatSession.kind == "employee",
    ).order_by(ChatSession.last_active_at.desc()).first()

    if session:
        head = ("Your request has been approved"
                if status == "resolved" else
                "Your request could not be approved")
        db.add(ChatMessage(
            session_id=session.id,
            role="hr",
            text=f"{head} — “{req.subject}”." + (f"\n\n{note}" if note else ""),
            intent="request_decided",
            sources=[{"kind": "hr_request", "id": req.id}],
        ))
        session.last_active_at = datetime.utcnow()

    # ──── And by email, after the commit ────
    employee = db.query(User).filter(User.id == req.employee_id).first()
    db.commit()

    try:
        from app.utils import notify

        if employee and employee.email:
            notify.send_email(
                # `ceo.company_id`, not a bare `company_id` — that name
                # does not exist in this function. It raised NameError on
                # EVERY call, the `except` below printed it, and the
                # employee was simply never emailed that their request
                # had been answered. Nothing failed loudly enough for
                # anyone to look.
                company_id=ceo.company_id,
                to=employee.email,
                subject=f"HR request {status} — {req.subject}",
                body=(
                    f"Dear {employee.full_name},\n\n"
                    f"Your request “{req.subject}” has been {status}.\n"
                    + (f"\nReason:\n{note}\n" if note else "")
                    + "\nYou can see the details on your Agentra dashboard."
                ),
            )
    except Exception as e:                              # noqa: BLE001
        print(f"[chat] could not notify the employee: {e}")

    return {
        "message": f"Request {status}",
        "request_id": req.id,
        "status": req.status,
    }


# ══════════════════════════════════════════════
# Route 8: A letter, as a PDF
# ══════════════════════════════════════════════
LETTER_MIN_STATUS = "resolved"


@router.get("/letter/{request_id}")
def download_letter(
    request_id: int,
    kind: str = "employment",
    include_salary: bool = False,
    db: Session = Depends(get_db),
    current_user: Tenant = Depends(get_tenant),
):
    """
    The letter the CEO approved, as a PDF.

    ═══ WHY IT HANGS OFF AN APPROVED REQUEST ═══
    Producing a PDF is not the same as issuing a certificate. Anyone
    could ask the software for one; only the company can certify. So the
    letter exists once — and only once — the CEO has resolved the
    `hr_requests` row behind it, which puts a person between "an employee
    asked" and "the company has stated".

    An employee may download their own; the CEO may download any in their
    own company. Both checks are on the request row, not on a parameter.
    """
    from fastapi.responses import Response

    from app.utils.hr_letters import (
        LETTER_KINDS, build_letter_pdf, letter_context,
    )

    user = get_user_or_404(db, current_user["user_id"])
    company_id = resolve_company_id(db, user) or user.id
    is_ceo = current_user["role"] in ("ceo", "superadmin")

    req = db.query(HrRequest).filter(
        HrRequest.id == request_id,
        HrRequest.company_id == company_id,
    ).first()
    if not req:
        raise HTTPException(404, "Request not found")

    # Someone else's request is a 404, not a 403 — a 403 confirms it exists
    if not is_ceo and req.employee_id != user.id:
        raise HTTPException(404, "Request not found")

    if req.status != LETTER_MIN_STATUS:
        raise HTTPException(
            400,
            "This letter has not been approved yet. You will be told here "
            "once it has."
        )

    if kind not in LETTER_KINDS:
        raise HTTPException(400, f"Unknown letter type '{kind}'")

    ctx = letter_context(db, req.employee_id, company_id)
    if not ctx:
        raise HTTPException(404, "Employee record not found")

    if not ctx["employee"].get("joined"):
        raise HTTPException(
            400,
            "A joining date is needed before this letter can be issued — "
            "I will get that added and let you know."
        )

    pdf = build_letter_pdf(
        kind=kind,
        employee=ctx["employee"],
        company=ctx["company"],
        include_salary=bool(include_salary),
        purpose=(req.body or "")[:180] or None,
    )

    safe = "".join(ch for ch in (ctx["employee"]["name"] or "letter")
                   if ch.isalnum() or ch in " -_").strip().replace(" ", "_")

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="{kind}-letter-{safe or "employee"}.pdf"',
            # Somebody's salary may be on this page — no proxy may keep it
            "Cache-Control": "private, no-store",
        },
    )
