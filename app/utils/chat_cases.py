"""
The file HR keeps while it is working on something
──────────────────────────────────────────────────
Phase 1 taught the model to read what it had already been told. This
makes it something the SYSTEM knows rather than something the model is
asked to notice.

The difference matters. A prompt rule is followed most of the time; a row
in a table is true every time. And once the facts live in a row, they
survive a browser refresh, a new session, and a week away — none of which
a prompt does.

═══════════════════════════════════════════════════════════
NOTHING IS DECIDED BY A NUMBER IN THIS FILE
═══════════════════════════════════════════════════════════
Every threshold comes from `hr_settings` for that company. If you find a
literal at the point of a decision here, it is a bug — a call centre and
an architecture studio do not agree on what "too many late arrivals"
means, and neither should have to edit Python to say so.

═══════════════════════════════════════════════════════════
THE MODEL PROPOSES, THIS FILE DISPOSES
═══════════════════════════════════════════════════════════
The model returns a concern name and some facts. It does not get to
invent a concern, set its own posture, or decide who may read the case:

    concern  must be a key in the playbook, or it is dropped
    posture  comes from the playbook, never from the model
    scope    comes from the posture, never from the conversation

Same shape as the tool table — the model picks from a fixed list and the
code decides what that choice is allowed to mean.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.chat import HrCase, HrSettings
from app.utils.chat_playbook import PLAYBOOK, NEVER_ESCALATE_ALONE

# Stages a case moves through. `gathering` is where HR asks; `ready`
# means it has enough to act; `raised` means it reached the CEO.
STAGES = ("gathering", "ready", "raised", "resolved", "closed")

# A posture that keeps the case to the employee unless they release it.
PRIVATE_POSTURES = ("confidential", "advisory")


# ══════════════════════════════════════════════
# Settings
# ══════════════════════════════════════════════
def get_settings(db: Session, company_id: int) -> HrSettings:
    """
    This company's numbers, creating the row on first use.

    Creating it here rather than making every caller handle `None` means
    a company that has never opened the settings screen still behaves
    sensibly, and the CEO finds real values waiting when they do.
    """
    s = db.query(HrSettings).filter(
        HrSettings.company_id == company_id).first()
    if s:
        return s

    s = HrSettings(company_id=company_id)
    db.add(s)
    db.flush()
    return s


# ══════════════════════════════════════════════
# Reading
# ══════════════════════════════════════════════
def open_case_for(db: Session, employee_id: int, company_id: int,
                  concern: Optional[str] = None,
                  session_id: Optional[int] = None) -> Optional[HrCase]:
    """
    The case this message probably belongs to — or none.

    ═══ WHY THIS IS NOT JUST "THE LATEST OPEN CASE" ═══
    It was, and it produced the worst bug in the help desk so far. An
    employee opened a fresh conversation with "I have an issue with an
    employee" and HR answered:

        "I've recorded that Zeeshan has been behaving unprofessionally
         toward you for the past week..."

    Zeeshan was from a different conversation days earlier. HR had put
    words in someone's mouth, named a colleague they had not mentioned,
    and treated a new problem as an old one. If that had been a real
    grievance about a different person, the record would now be wrong
    about who did what.

    So a case is resumed only when it is plausibly still the same
    conversation:

        same chat session          -> resume, obviously
        different session, but
        touched within
        `case_stale_days`          -> resume; coming back tomorrow to
                                      finish something is normal
        older than that            -> do NOT resume. Start clean.

    The window is the company's own setting, not a number here. A case
    that ages out is not lost — it stays open, and the employee can
    return to it by saying what it was about.
    """
    q = db.query(HrCase).filter(
        HrCase.employee_id == employee_id,
        HrCase.company_id == company_id,
        HrCase.stage.in_(("gathering", "ready")),
    )
    if concern:
        q = q.filter(HrCase.concern == concern)

    # Anything in this very conversation is the same conversation.
    if session_id:
        same = q.filter(HrCase.session_id == session_id) \
                .order_by(HrCase.last_touched_at.desc()).first()
        if same:
            return same

    days = get_settings(db, company_id).case_stale_days or 0
    if days <= 0:
        # The company has switched staleness off — only ever continue
        # within the same conversation.
        return None

    cutoff = datetime.utcnow() - timedelta(days=days)
    return q.filter(HrCase.last_touched_at >= cutoff) \
            .order_by(HrCase.last_touched_at.desc()).first()


def case_brief(case: Optional[HrCase]) -> str:
    """
    The case as the model should read it.

    Deliberately plain text, not JSON: this goes into a prompt, and a
    model follows a short briefing more reliably than it parses a nested
    object. Anything the employee has said is worded as settled fact so
    there is no temptation to ask again.
    """
    if not case:
        return "(no case open — this may be the start of one)"

    lines = [f"CASE #{case.id} · {case.concern} · stage: {case.stage}"]

    facts = case.facts or {}
    if facts:
        lines.append("Already established — do NOT ask for any of this again:")
        for k, v in facts.items():
            lines.append(f"  · {k}: {v}")
    else:
        lines.append("Nothing established yet.")

    missing = case.still_needed or []
    if missing:
        lines.append("Still missing — ask for ONE of these, the most important:")
        for m in missing:
            lines.append(f"  · {m}")
    else:
        lines.append("Nothing missing. Do not ask further questions.")

    if case.confidential:
        lines.append("CONFIDENTIAL — this stays between you and them.")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# Writing
# ══════════════════════════════════════════════
def start_case(db: Session, employee_id: int, company_id: int,
               concern: str, session_id: Optional[int] = None,
               subject: Optional[str] = None) -> Optional[HrCase]:
    """
    Open a file for a concern the playbook recognises.

    An unknown concern returns None rather than creating a case with a
    posture nobody defined — that is how a model's typo would otherwise
    become a row with no rules attached to it.
    """
    entry = PLAYBOOK.get(concern)
    if not entry:
        return None

    posture = entry.get("posture", "procedural")

    case = HrCase(
        employee_id=employee_id,
        company_id=company_id,
        concern=concern,
        posture=posture,
        stage="gathering",
        subject=(subject or entry.get("about") or concern)[:200],
        facts={},
        # The playbook's questions ARE the checklist. Copying them onto
        # the case means "what is still missing" shrinks as answers come
        # in, instead of being re-derived from scratch every turn.
        still_needed=list(entry.get("ask") or []),
        confidential=posture in PRIVATE_POSTURES,
        session_id=session_id,
    )
    db.add(case)
    db.flush()
    return case


def record_facts(db: Session, case: HrCase, facts: dict) -> HrCase:
    """
    Write down what was just learned, and cross it off the list.

    A question is considered answered when the model returns a fact keyed
    to it. Matching is on the question text the playbook supplied, so the
    model cannot quietly retire a question by inventing a new key.
    """
    if not facts:
        return case

    merged = dict(case.facts or {})
    for k, v in facts.items():
        if v in (None, "", []):
            continue
        merged[str(k)[:120]] = str(v)[:600]

    remaining = [q for q in (case.still_needed or []) if q not in merged]

    case.facts = merged
    case.still_needed = remaining
    case.last_touched_at = datetime.utcnow()

    # Nothing left to ask — HR has what it needs to act.
    if not remaining and case.stage == "gathering":
        case.stage = "ready"

    db.add(case)
    return case


def may_escalate(case: HrCase) -> bool:
    """
    Whether this case may ever reach the CEO.

    Two concerns never do on their own, and this is the guard rather than
    a prompt instruction: asking what the notice period is, is not
    resigning, and filing it told an employer their employee was leaving.
    """
    return case.concern not in NEVER_ESCALATE_ALONE


def mark_raised(db: Session, case: HrCase, hr_request_id: int) -> HrCase:
    case.stage = "raised"
    case.hr_request_id = hr_request_id
    case.last_touched_at = datetime.utcnow()
    db.add(case)
    return case


def close_case(db: Session, case: HrCase, stage: str = "closed") -> HrCase:
    case.stage = stage if stage in STAGES else "closed"
    case.closed_at = datetime.utcnow()
    case.last_touched_at = datetime.utcnow()
    db.add(case)
    return case


# ══════════════════════════════════════════════
# For the CEO — and only what they are owed
# ══════════════════════════════════════════════
def stale_cases(db: Session, company_id: int) -> list:
    """
    Cases nobody has touched for longer than this company allows.

    The window is `hr_settings.case_stale_days`, never a number here.
    """
    days = get_settings(db, company_id).case_stale_days or 0
    if days <= 0:
        return []

    cutoff = datetime.utcnow() - timedelta(days=days)
    return db.query(HrCase).filter(
        HrCase.company_id == company_id,
        HrCase.stage.in_(("gathering", "ready")),
        HrCase.last_touched_at < cutoff,
    ).order_by(HrCase.last_touched_at).all()
