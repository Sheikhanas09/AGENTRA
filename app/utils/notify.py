"""
Notification Service
────────────────────
Employee ko pata chalna chahiye ke uski leave ka kya bana — bina app khole.

DO USOOL:
1. Notification kabhi asal kaam nahi rok sakti. SMTP fail ho, network band ho,
   email ghalat ho — leave phir bhi approve honi chahiye. Isliye har cheez
   try/except mein hai aur koi exception bahar nahi jata.

2. Email bhejne mein 2-5 second lagte hain. Agar request usi waqt bheje to
   CEO ka "Approve" button 5 second latka rahega. Isliye background thread
   mein jata hai — user ko foran jawab milta hai.
"""

import os
import smtplib
import threading
from concurrent.futures import ThreadPoolExecutor
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 20

SENDER_EMAIL = os.getenv("NOTIFY_SENDER_EMAIL") or "nirmal.naik1994@gmail.com"
SENDER_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Testing ke doran band karna ho to .env mein NOTIFICATIONS_ENABLED=false
ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "true").strip().lower() not in (
    "false", "0", "no"
)

# Ek waqt mein 2 email — burst mein bhi threads ka ambaar na lage
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")

_warned = False


def _warn_once(message: str):
    global _warned
    if not _warned:
        print(f"[notify] {message}")
        _warned = True


def _deliver(to: List[str], subject: str, body: str):
    """Asal SMTP kaam — background thread mein chalta hai"""
    try:
        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to, msg.as_string())
        server.quit()

        print(f"[notify] sent -> {', '.join(to)} | {subject}")

    except Exception as e:
        # ──── Chup chaap nahi — log karo, magar kaam mat roko ────
        print(f"[notify] FAILED -> {', '.join(to)} | {subject} | {e}")


def send_email(to, subject: str, body: str) -> bool:
    """
    Email background mein bhejo. Foran wapas aata hai.

    Return: True = queue ho gayi (bhej di jayegi), False = bheji hi nahi ja sakti
    """
    if not ENABLED:
        return False

    if not SENDER_PASSWORD:
        _warn_once("GMAIL_APP_PASSWORD set nahi — emails band hain")
        return False

    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = [e.strip() for e in recipients if e and "@" in str(e)]
    if not recipients:
        return False

    try:
        _pool.submit(_deliver, recipients, subject, body)
        return True
    except Exception as e:
        print(f"[notify] queue failed: {e}")
        return False


# ══════════════════════════════════════════════
# Templates
# ══════════════════════════════════════════════
LINE = "━" * 40


def _footer(company: str) -> str:
    return f"\n\n{LINE}\nAgentra HR System\n{company or ''}".rstrip()


def _dates(start, end) -> str:
    return str(start) if str(start) == str(end) else f"{start} se {end}"


def leave_submitted_to_ceo(
    ceo_email: str, ceo_name: str, employee_name: str, leave_type: str,
    start, end, days: int, reason: str, auto_approve_at: Optional[str],
    agent_note: str = "", company: str = "",
) -> bool:
    """Nayi request aayi — CEO ko batao (deadline ke saath)"""
    deadline = (
        f"\nAap ne {auto_approve_at} tak jawab na diya to yeh request khud "
        f"approve ho jayegi (balance maujood hone par)."
        if auto_approve_at else ""
    )
    agent = f"\n\nLeave Agent ka mashwara:\n{agent_note}" if agent_note else ""

    body = f"""Assalam-o-Alaikum {ceo_name},

{employee_name} ne leave request bheji hai.

{LINE}
Type      : {leave_type}
Dates     : {_dates(start, end)}
Days      : {days} working days
Reason    : {reason}
{LINE}
{deadline}{agent}

Faisla karne ke liye Agentra dashboard kholein."""

    return send_email(
        ceo_email,
        f"Leave request — {employee_name} ({_dates(start, end)})",
        body + _footer(company),
    )


def leave_decision_to_employee(
    employee_email: str, employee_name: str, decision: str, leave_type: str,
    start, end, days: int, note: str = "", remaining: Optional[int] = None,
    auto: bool = False, company: str = "",
) -> bool:
    """Faisla ho gaya — employee ko batao"""
    if decision == "approved":
        headline = (
            "Aap ki leave request APPROVE ho gayi hai."
            if not auto else
            "Aap ki leave request khud-ba-khud APPROVE ho gayi hai\n"
            "(CEO ne muqarrara waqt mein jawab nahi diya)."
        )
        subject = f"Leave approved — {_dates(start, end)}"
        balance = (
            f"\nBaqi balance : {remaining} din" if remaining is not None else ""
        )
    else:
        headline = "Afsos, aap ki leave request REJECT kar di gayi hai."
        subject = f"Leave rejected — {_dates(start, end)}"
        balance = ""

    reason_block = f"\n{LINE}\nWajah:\n{note}" if note else ""

    body = f"""Assalam-o-Alaikum {employee_name},

{headline}

{LINE}
Type      : {leave_type}
Dates     : {_dates(start, end)}
Days      : {days} working days{balance}
{LINE}{reason_block}

Tafseel Agentra dashboard par dekh sakte hain."""

    return send_email(employee_email, subject, body + _footer(company))


def leave_auto_approved_to_ceo(
    ceo_email: str, ceo_name: str, employee_name: str, leave_type: str,
    start, end, days: int, hours: int, company: str = "",
) -> bool:
    """Deadline guzar gayi aur request khud approve ho gayi — CEO ko ittila"""
    body = f"""Assalam-o-Alaikum {ceo_name},

{employee_name} ki leave request khud approve ho gayi hai kyunki
{hours} ghante tak koi jawab nahi diya gaya.

{LINE}
Type   : {leave_type}
Dates  : {_dates(start, end)}
Days   : {days} working days
{LINE}

Agar yeh theek nahi to dashboard se "Cancel Leave" kar sakte hain."""

    return send_email(
        ceo_email,
        f"Leave auto-approved — {employee_name} ({_dates(start, end)})",
        body + _footer(company),
    )


def leave_cancelled_to_ceo(
    ceo_email: str, ceo_name: str, employee_name: str, leave_type: str,
    start, end, by_ceo: bool = False, company: str = "",
) -> bool:
    """Employee ne apni approved leave cancel kar di — CEO ko batao"""
    if by_ceo:
        return False   # CEO ne khud ki hai, usay batane ka faida nahi

    body = f"""Assalam-o-Alaikum {ceo_name},

{employee_name} ne apni leave cancel kar di hai.

{LINE}
Type   : {leave_type}
Dates  : {_dates(start, end)}
{LINE}

Balance wapas us ke account mein daal diya gaya hai."""

    return send_email(
        ceo_email,
        f"Leave cancelled — {employee_name} ({_dates(start, end)})",
        body + _footer(company),
    )


def leave_reminder_to_ceo(
    ceo_email: str, ceo_name: str, pending: List[dict], hours_left: int,
    company: str = "",
) -> bool:
    """Deadline qareeb hai — CEO ko yaad dihani"""
    if not pending:
        return False

    lines = "\n".join(
        f"  · {p['employee_name']} — {p['leave_type']}, "
        f"{_dates(p['start_date'], p['end_date'])} ({p['deductible_days']} din)"
        for p in pending
    )

    body = f"""Assalam-o-Alaikum {ceo_name},

{len(pending)} leave request(s) aap ke jawab ka intezar kar rahi hain.
Agle {hours_left} ghante mein jawab na mila to yeh khud approve ho jayengi.

{LINE}
{lines}
{LINE}

Agentra dashboard kholein."""

    return send_email(
        ceo_email,
        f"{len(pending)} leave request(s) jawab ki muntazir",
        body + _footer(company),
    )
