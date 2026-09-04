"""
Notification Service
────────────────────
An employee should learn what happened to their leave without having to
open the app.

TWO RULES:
1. A notification must never block the real work. SMTP can fail, the
   network can drop, the address can be wrong — the leave must still be
   approved. So everything sits inside try/except and no exception ever
   escapes.

2. Sending an email takes 2-5 seconds. Doing that inside the request
   would leave the "Approve" button hanging for 5 seconds. So it goes to
   a background thread and the user gets an immediate response.

═══════════════════════════════════════════════════════════
HOW THESE EMAILS READ
═══════════════════════════════════════════════════════════
Anything addressed to an EMPLOYEE is written the way an HR department
writes: a decision was made, here it is. Nothing about deadlines,
schedulers or requests approving themselves — that is internal plumbing,
and an employee reading "your leave approved itself" would rightly stop
trusting the decision.

Mail addressed to the CEO is the opposite: they configure the automation,
so they are told plainly when something was approved on their behalf.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════
# THE SENDER IS THE COMPANY
# ══════════════════════════════════════════════
# This module used to hold:
#
#     SENDER_EMAIL = os.getenv("NOTIFY_SENDER_EMAIL") or "someone@gmail.com"
#     SENDER_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
#
# — one address and one app password for every company in the system. An
# employee of one company received their leave decision, and their
# payslip, from a different company's Gmail account.
#
# Every function below now takes `company_id` and sends through that
# company's own connected Google account (`utils/mailer.py`). There is no
# shared fallback, deliberately: a fallback is the thing being removed,
# and it would quietly reinstate itself the first time somebody forgot
# to connect.

# To switch these off while testing, set NOTIFICATIONS_ENABLED=false in .env
ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "true").strip().lower() not in (
    "false", "0", "no"
)

# Two emails at a time — a burst should not pile up threads
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")

_warned = False


def _warn_once(message: str):
    global _warned
    if not _warned:
        print(f"[notify] {message}")
        _warned = True


def _deliver(company_id: int, to: List[str], subject: str, body: str):
    """
    The actual send — runs on a background thread.

    ⚠ IT OPENS ITS OWN SESSION. The request's session is long gone by the
    time this runs, and the credential load may refresh the token and
    write it back. `open_tenant_session` also means every query in here
    is confined to this company, on a thread nobody is watching.
    """
    from app.utils.mailer import send_as_company
    from app.utils.tenancy import open_tenant_session

    try:
        with open_tenant_session(company_id) as db:
            ok, detail = send_as_company(
                db, company_id, to=", ".join(to), subject=subject,
                text_body=body)
        if ok:
            print(f"[notify] sent -> {', '.join(to)} | {subject}")
        else:
            print(f"[notify] NOT SENT -> {', '.join(to)} | {subject} | {detail}")
    except Exception as e:                                      # noqa: BLE001
        # ──── Not silently — log it, but never stop the work ────
        print(f"[notify] FAILED -> {', '.join(to)} | {subject} | {e}")


def send_email(company_id: int, to, subject: str, body: str) -> bool:
    """
    Send an email in the background, as `company_id`. Returns immediately.

    Return: True = queued (it will be sent), False = cannot be sent at all
    """
    if not ENABLED:
        return False

    if not company_id:
        _warn_once("send_email was called without a company — not sending")
        return False

    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = [e.strip() for e in recipients if e and "@" in str(e)]
    if not recipients:
        return False

    try:
        _pool.submit(_deliver, company_id, recipients, subject, body)
        return True
    except Exception as e:
        print(f"[notify] queue failed: {e}")
        return False


# ══════════════════════════════════════════════
# Templates
# ══════════════════════════════════════════════
LINE = "━" * 40


def _footer(company: str) -> str:
    return f"\n\n{LINE}\n{company or 'Agentra'}\nHR Department".rstrip()


def _dates(start, end) -> str:
    return str(start) if str(start) == str(end) else f"{start} to {end}"


def leave_submitted_to_ceo(
    company_id: int,
    ceo_email: str, ceo_name: str, employee_name: str, leave_type: str,
    start, end, days: int, reason: str, auto_approve_at: Optional[str],
    agent_note: str = "", company: str = "",
) -> bool:
    """A new request has arrived — tell the CEO (with the deadline)"""
    deadline = (
        f"\nIf you do not respond by {auto_approve_at}, this request will be "
        f"approved automatically (provided the balance allows it)."
        if auto_approve_at else ""
    )
    agent = f"\n\nLeave Agent recommendation:\n{agent_note}" if agent_note else ""

    body = f"""Dear {ceo_name},

{employee_name} has submitted a leave request.

{LINE}
Type      : {leave_type}
Dates     : {_dates(start, end)}
Days      : {days} working days
Reason    : {reason}
{LINE}
{deadline}{agent}

Open the Agentra dashboard to decide."""

    return send_email(
        company_id,
        ceo_email,
        f"Leave request — {employee_name} ({_dates(start, end)})",
        body + _footer(company),
    )


def leave_decision_to_employee(
    company_id: int,
    employee_email: str, employee_name: str, decision: str, leave_type: str,
    start, end, days: int, note: str = "", remaining: Optional[int] = None,
    auto: bool = False, company: str = "",
) -> bool:
    """
    A decision has been made — tell the employee.

    `auto` is still accepted so callers stay unchanged, but the wording
    deliberately does NOT depend on it. Whether a person pressed Approve
    or the deadline passed, the employee is told the same thing: HR has
    approved the leave. How the decision was reached is internal — and
    "your leave approved itself" reads as though nobody looked at it.
    """
    if decision == "approved":
        headline = "Your leave request has been APPROVED."
        subject = f"Leave approved — {_dates(start, end)}"
        balance = (
            f"\nRemaining : {remaining} days" if remaining is not None else ""
        )
    else:
        headline = "We are sorry — your leave request has been REJECTED."
        subject = f"Leave rejected — {_dates(start, end)}"
        balance = ""

    reason_block = f"\n{LINE}\nReason:\n{note}" if note else ""

    body = f"""Dear {employee_name},

{headline}

{LINE}
Type      : {leave_type}
Dates     : {_dates(start, end)}
Days      : {days} working days{balance}
{LINE}{reason_block}

You can see the full details on your Agentra dashboard."""

    return send_email(company_id, employee_email, subject,
                      body + _footer(company))


def leave_auto_approved_to_ceo(
    company_id: int,
    ceo_email: str, ceo_name: str, employee_name: str, leave_type: str,
    start, end, days: int, hours: int, company: str = "",
) -> bool:
    """The deadline passed and the request approved itself — inform the CEO"""
    body = f"""Dear {ceo_name},

{employee_name}'s leave request has been approved automatically because
there was no response for {hours} hours.

{LINE}
Type   : {leave_type}
Dates  : {_dates(start, end)}
Days   : {days} working days
{LINE}

If this is not right, you can still use "Cancel Leave" on the dashboard."""

    return send_email(
        company_id,
        ceo_email,
        f"Leave auto-approved — {employee_name} ({_dates(start, end)})",
        body + _footer(company),
    )


def leave_cancelled_to_ceo(
    company_id: int,
    ceo_email: str, ceo_name: str, employee_name: str, leave_type: str,
    start, end, by_ceo: bool = False, company: str = "",
) -> bool:
    """An employee cancelled their own approved leave — tell the CEO"""
    if by_ceo:
        return False   # The CEO did it themselves; telling them adds nothing

    body = f"""Dear {ceo_name},

{employee_name} has cancelled their leave.

{LINE}
Type   : {leave_type}
Dates  : {_dates(start, end)}
{LINE}

The balance has been returned to their account."""

    return send_email(
        company_id,
        ceo_email,
        f"Leave cancelled — {employee_name} ({_dates(start, end)})",
        body + _footer(company),
    )


def leave_reminder_to_ceo(
    company_id: int,
    ceo_email: str, ceo_name: str, pending: List[dict], hours_left: int,
    company: str = "",
) -> bool:
    """The deadline is close — remind the CEO"""
    if not pending:
        return False

    lines = "\n".join(
        f"  · {p['employee_name']} — {p['leave_type']}, "
        f"{_dates(p['start_date'], p['end_date'])} ({p['deductible_days']} days)"
        for p in pending
    )

    body = f"""Dear {ceo_name},

{len(pending)} leave request(s) are waiting for your response.
If there is no answer within the next {hours_left} hours, they will be
approved automatically.

{LINE}
{lines}
{LINE}

Open the Agentra dashboard."""

    return send_email(
        company_id,
        ceo_email,
        f"{len(pending)} leave request(s) awaiting your response",
        body + _footer(company),
    )
