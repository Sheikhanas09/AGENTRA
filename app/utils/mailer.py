"""
Sending mail as the company, not as us
──────────────────────────────────────
Every outbound email in the system used one Gmail account:

    sender_email = "nirmal.naik1994@gmail.com"   # four places in
                                                 # routes/recruitment.py
    sender_password = os.getenv("GMAIL_APP_PASSWORD")

    # utils/notify.py
    SENDER_EMAIL = os.getenv("NOTIFY_SENDER_EMAIL") or "nirmal.naik1994@..."

So a candidate applying to one company received their offer letter from
a stranger's personal address, and every company's leave decisions went
out from the same one.

This sends through the Gmail API using the company's OWN connection.
Two things follow from that, and the second matters more than it looks:

  1. The From address is the company's own account, because it is
     literally their mailbox sending it.

  2. THERE IS NO PASSWORD ANYWHERE. `GMAIL_APP_PASSWORD` was a
     credential in `.env` that could send mail as that account forever.
     An OAuth token is scoped to `gmail.send`, is revocable from the
     account's own security page, and is stored encrypted.

═══════════════════════════════════════════════════════════
A COMPANY THAT HAS NOT CONNECTED CANNOT SEND
═══════════════════════════════════════════════════════════
And that is the correct behaviour. The alternative — falling back to a
shared account — is what this replaces, and a fallback would quietly
undo the whole change the first time somebody forgot to connect.

The caller gets `False` and a reason. Notifications already treat a
failed send as non-fatal, so a leave still gets approved; the CEO is
told on the Integrations screen that mail is not going out.
"""

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from app.utils.google_auth import GoogleNotConnected, service_for


class MailNotSent(RuntimeError):
    pass


def send_as_company(
    db,
    company_id: int,
    to: str,
    subject: str,
    html_body: str = None,
    text_body: str = None,
    attachments=None,
    cc: str = None,
):
    """
    Send one email as this company.

    Returns (ok: bool, detail: str). It does not raise for an ordinary
    failure — mail is never allowed to break the operation that
    triggered it, which is the rule `utils/notify.py` has always had and
    the reason a leave approval does not depend on SMTP being up.

    `attachments` is a list of (filename, bytes, mime_subtype), so a
    payslip or an offer letter PDF goes out the same way.
    """
    if not to:
        return False, "no recipient"

    try:
        service = service_for(db, company_id, "gmail", "v1")
    except GoogleNotConnected as e:
        return False, str(e)
    except Exception as e:                                      # noqa: BLE001
        return False, f"could not reach Google: {e}"

    try:
        message = _build(to, subject, html_body, text_body, attachments, cc)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(
            userId="me", body={"raw": raw}).execute()
        return True, sent.get("id", "sent")
    except Exception as e:                                      # noqa: BLE001
        return False, f"Gmail refused the message: {e}"


def _build(to, subject, html_body, text_body, attachments, cc):
    msg = MIMEMultipart("mixed")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    # No `From`: the Gmail API stamps the authenticated account, and
    # setting it by hand is how mail ends up rejected for a mismatch.

    alt = MIMEMultipart("alternative")
    if text_body:
        alt.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        alt.attach(MIMEText(html_body, "html", "utf-8"))
    if not text_body and not html_body:
        alt.attach(MIMEText("", "plain", "utf-8"))
    msg.attach(alt)

    for item in (attachments or []):
        filename, data, subtype = item
        part = MIMEApplication(data, _subtype=subtype or "octet-stream")
        part.add_header("Content-Disposition", "attachment",
                        filename=filename)
        msg.attach(part)

    return msg


def can_send(db, company_id: int):
    """
    Whether mail would go out, without sending one.

    Used by the Integrations screen and by `check_tenancy`-style checks,
    so "is this company able to email?" has a real answer rather than
    being discovered when an offer letter silently does not arrive.
    """
    from app.models.integration import CompanyIntegration

    row = db.query(CompanyIntegration).filter(
        CompanyIntegration.company_id == company_id).first()
    if not row or not row.is_live():
        return False, "Google is not connected for this company"
    if not row.has_scope("gmail.send"):
        return False, ("connected, but permission to send mail was not "
                       "granted — reconnect and allow sending")
    return True, row.account_email or "connected"
