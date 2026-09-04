"""
The outside world: Google Meet, Google Calendar, and email.

═══════════════════════════════════════════════════════════
THESE TOOLS USED ONE GOOGLE ACCOUNT FOR EVERY COMPANY
═══════════════════════════════════════════════════════════
Each of them took `sender_email` and `sender_password`, and the caller
passed the same hard-coded address and the one `GMAIL_APP_PASSWORD`
every time. The calendar tool did not even take arguments — it read
`app/token.json` off disk. So every company's interview landed on one
person's calendar and every offer letter arrived from their address.

Now each tool takes `google_token`: the company's own OAuth credentials,
handed down from the route that already loaded and decrypted them.

⚠ AN APP PASSWORD IS FOREVER; A TOKEN IS NOT. `GMAIL_APP_PASSWORD` sat
in `.env` and could send mail as that account until somebody remembered
to revoke it. These tokens are scoped to `gmail.send` and `calendar`,
are revocable from the account's own security page, and are stored
encrypted.

WHY THE TOKEN TRAVELS AS AN ARGUMENT: this server is a separate process
speaking MCP over stdio, with no database of its own. Credentials came
in as arguments before (`sender_password` did), the transport is a local
pipe between two processes of the same application, and giving this
server its own database connection would mean a second place that
decides which company a request belongs to.
"""

import base64
import json
import uuid
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ⚠ A SEPARATE PROCESS, SO IT NEEDS THIS TOO.
# This server is launched over stdio and makes its own Calendar and
# Gmail calls. Installing the IPv4 preference only in `main.py` would fix
# the API's Google calls and leave the Meet link and the interview email
# still hanging — half the symptoms gone, which is a confusing place to
# debug from. Imported defensively: this file is also started directly.
try:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", ".."))
    from app.utils.net import prefer_ipv4 as _prefer_ipv4
    _prefer_ipv4()
except Exception:                                               # noqa: BLE001
    pass

server = Server("agentra-meeting-email")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def _credentials(google_token: str):
    """Rebuild the company's credentials from the JSON handed to us."""
    from google.oauth2.credentials import Credentials
    if not google_token:
        raise ValueError(
            "No Google account is connected for this company. Open "
            "Settings -> Integrations and press Connect Google."
        )
    return Credentials.from_authorized_user_info(
        json.loads(google_token), SCOPES)


def _send_mail(google_token: str, to, subject: str, body: str,
               attachment=None):
    """
    One email, sent through the company's own Gmail.

    `to` may be a string or a list. No `From` header is set: the Gmail
    API stamps the authenticated account, and setting it by hand is how
    a message gets rejected for a mismatch.
    """
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=_credentials(google_token),
                    cache_discovery=False)

    msg = MIMEMultipart()
    msg["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if attachment:
        filename, data, subtype = attachment
        part = MIMEApplication(data, _subtype=subtype)
        part.add_header("Content-Disposition", "attachment",
                        filename=filename)
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="generate_meeting_link",
            description="Generate a real Google Meet link via Google Calendar API",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "time": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "google_token": {"type": "string"}
                },
                "required": ["title", "date", "time", "attendees",
                             "google_token"]
            }
        ),
        types.Tool(
            name="send_interview_email",
            description="Send interview scheduled email",
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_name": {"type": "string"},
                    "candidate_email": {"type": "string"},
                    "job_title": {"type": "string"},
                    "company_name": {"type": "string"},
                    "scheduled_date": {"type": "string"},
                    "scheduled_time": {"type": "string"},
                    "meeting_link": {"type": "string"},
                    "interviewer_1_email": {"type": "string"},
                    "interviewer_2_email": {"type": "string"},
                    "hr_name": {"type": "string"},
                    "google_token": {"type": "string"}
                },
                "required": [
                    "candidate_name", "candidate_email", "job_title",
                    "company_name", "scheduled_date", "scheduled_time",
                    "meeting_link", "interviewer_1_email", "hr_name",
                    "google_token"
                ]
            }
        ),
        types.Tool(
            name="send_offer_letter",
            description="Send offer letter email to candidate with accept link",
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_name": {"type": "string"},
                    "candidate_email": {"type": "string"},
                    "job_title": {"type": "string"},
                    "company_name": {"type": "string"},
                    "salary_range": {"type": "string"},
                    "ceo_name": {"type": "string"},
                    "accept_link": {"type": "string"},
                    "offer_date": {"type": "string"},
                    "google_token": {"type": "string"}
                },
                "required": [
                    "candidate_name", "candidate_email", "job_title",
                    "company_name", "salary_range", "ceo_name",
                    "accept_link", "offer_date", "google_token"
                ]
            }
        ),
        types.Tool(
            name="send_onboarding_email",
            description="Send onboarding details email to hired candidate",
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_name": {"type": "string"},
                    "candidate_email": {"type": "string"},
                    "job_title": {"type": "string"},
                    "company_name": {"type": "string"},
                    "joining_date": {"type": "string"},
                    "google_token": {"type": "string"}
                },
                "required": [
                    "candidate_name", "candidate_email", "job_title",
                    "company_name", "joining_date", "google_token"
                ]
            }
        ),
        types.Tool(
            name="send_rejection_email",
            description="Send professional rejection email to candidate",
            inputSchema={
                "type": "object",
                "properties": {
                    "candidate_name": {"type": "string"},
                    "candidate_email": {"type": "string"},
                    "job_title": {"type": "string"},
                    "company_name": {"type": "string"},
                    "ceo_name": {"type": "string"},
                    "google_token": {"type": "string"}
                },
                "required": [
                    "candidate_name", "candidate_email", "job_title",
                    "company_name", "ceo_name", "google_token"
                ]
            }
        ),
        types.Tool(
            name="send_payroll_email",
            description=(
                "Email a salary slip PDF to an employee. The PDF arrives "
                "base64-encoded because MCP carries JSON, not raw bytes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "employee_name": {"type": "string"},
                    "employee_email": {"type": "string"},
                    "period_label": {"type": "string"},
                    "net_salary": {"type": "string"},
                    "currency": {"type": "string"},
                    "company_name": {"type": "string"},
                    "pdf_base64": {"type": "string"},
                    "google_token": {"type": "string"}
                },
                "required": [
                    "employee_name", "employee_email", "period_label",
                    "net_salary", "company_name", "pdf_base64",
                    "google_token"
                ]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):

    # ──── Tool 1: Google Meet Link ────
    if name == "generate_meeting_link":
        try:
            from googleapiclient.discovery import build
            from datetime import datetime, timedelta

            # The company's own calendar. This used to read
            # `app/token.json` from disk — one file, one account — so
            # every company's interviews were created on the same
            # person's calendar. The refresh is handled by the route
            # before the token gets here, so there is nothing to write
            # back from inside this process.
            creds = _credentials(arguments["google_token"])
            service = build('calendar', 'v3', credentials=creds,
                            cache_discovery=False)

            title = arguments.get("title", "Interview")
            date_str = arguments.get("date", "")
            time_str = arguments.get("time", "")
            attendees = [a for a in arguments.get("attendees", []) if a]

            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%B %d, %Y %I:%M %p")
            except:
                dt = datetime.now() + timedelta(days=1)

            start_time = dt.isoformat()
            end_time = (dt + timedelta(hours=1)).isoformat()

            event = {
                'summary': title,
                'description': 'Interview scheduled via Agentra HR System',
                'start': {'dateTime': start_time, 'timeZone': 'Asia/Karachi'},
                'end': {'dateTime': end_time, 'timeZone': 'Asia/Karachi'},
                'attendees': [{'email': email} for email in attendees],
                'conferenceData': {
                    'createRequest': {
                        'requestId': str(uuid.uuid4()),
                        'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                    }
                }
            }

            event = service.events().insert(
                calendarId='primary',
                body=event,
                conferenceDataVersion=1,
                sendUpdates='none'
            ).execute()

            meet_link = event.get('hangoutLink', '')
            if not meet_link:
                unique_id = str(uuid.uuid4())[:8].upper()
                meet_link = f"https://meet.jit.si/Agentra-{unique_id}"

            return [types.TextContent(type="text", text=meet_link)]

        except Exception as e:
            print(f"Calendar error: {e}")
            unique_id = str(uuid.uuid4())[:8].upper()
            meet_link = f"https://meet.jit.si/Agentra-{unique_id}"
            return [types.TextContent(type="text", text=meet_link)]

    # ──── Tool 2: Interview Email ────
    elif name == "send_interview_email":
        candidate_name = arguments["candidate_name"]
        candidate_email = arguments["candidate_email"]
        job_title = arguments["job_title"]
        company_name = arguments["company_name"]
        scheduled_date = arguments["scheduled_date"]
        scheduled_time = arguments["scheduled_time"]
        meeting_link = arguments["meeting_link"]
        interviewer_1_email = arguments["interviewer_1_email"]
        interviewer_2_email = arguments.get("interviewer_2_email", "")
        hr_name = arguments["hr_name"]
        google_token = arguments["google_token"]

        candidate_subject = f"Interview Scheduled — {job_title} at {company_name}"
        candidate_body = f"""Dear {candidate_name},

We are pleased to inform you that your interview has been scheduled.

INTERVIEW DETAILS:
━━━━━━━━━━━━━━━━━━━━━━
📅 Date:          {scheduled_date}
⏰ Time:          {scheduled_time}
💼 Position:      {job_title}
🏢 Company:       {company_name}
🔗 Meeting Link:  {meeting_link}
━━━━━━━━━━━━━━━━━━━━━━

Please click the meeting link above to join at the scheduled time.
Make sure to join 5 minutes early.

Best of luck!

Best regards,
HR Team
{company_name}"""

        interviewer_subject = f"Interview Assignment — {candidate_name} for {job_title}"
        interviewers_list = interviewer_1_email
        if interviewer_2_email:
            interviewers_list += f", {interviewer_2_email}"

        interviewer_body = f"""Dear Interviewer,

You have been assigned to conduct an interview for the position of {job_title}.

INTERVIEW DETAILS:
━━━━━━━━━━━━━━━━━━━━━━
👤 Candidate:     {candidate_name}
📅 Date:          {scheduled_date}
⏰ Time:          {scheduled_time}
💼 Position:      {job_title}
🏢 Company:       {company_name}
👥 Interviewers:  {interviewers_list}
🔗 Meeting Link:  {meeting_link}
━━━━━━━━━━━━━━━━━━━━━━

Please review the candidate's profile before the interview.
Join the meeting on time and submit feedback after the interview.

Best regards,
HR: {hr_name}
{company_name}"""

        try:
            # Two separate emails on purpose — the candidate must not see
            # who the interviewers are, and the interviewers get notes
            # the candidate should not read.
            _send_mail(google_token, candidate_email, candidate_subject,
                       candidate_body)

            interviewers = [interviewer_1_email]
            if interviewer_2_email:
                interviewers.append(interviewer_2_email)
            _send_mail(google_token, interviewers, interviewer_subject,
                       interviewer_body)

            return [types.TextContent(
                type="text",
                text=f"Email successfully sent to: {candidate_email}, {', '.join(interviewers)}"
            )]

        except Exception as e:
            return [types.TextContent(type="text", text=f"Email error: {str(e)}")]

    # ──── Tool 3: Offer Letter Email ────
    elif name == "send_offer_letter":
        candidate_name = arguments["candidate_name"]
        candidate_email = arguments["candidate_email"]
        job_title = arguments["job_title"]
        company_name = arguments["company_name"]
        salary_range = arguments["salary_range"]
        ceo_name = arguments["ceo_name"]
        accept_link = arguments["accept_link"]
        offer_date = arguments["offer_date"]
        google_token = arguments["google_token"]

        subject = f"Job Offer — {job_title} at {company_name} 🎉"
        body = f"""Dear {candidate_name},

Congratulations! We are pleased to offer you the position of {job_title} at {company_name}.

OFFER DETAILS:
━━━━━━━━━━━━━━━━━━━━━━
💼 Position:      {job_title}
🏢 Company:       {company_name}
💰 Salary:        {salary_range}
📅 Offer Date:    {offer_date}
━━━━━━━━━━━━━━━━━━━━━━

TO ACCEPT THIS OFFER:
Please click the link below to accept your offer:
👉 {accept_link}

This offer is valid for 3 business days.

Upon acceptance, you will receive:
- Joining date and onboarding details
- Required documents list
- First day schedule
- Company policies and handbook

Best regards,
{ceo_name}
CEO, {company_name}"""

        try:
            _send_mail(google_token, candidate_email, subject, body)

            return [types.TextContent(type="text", text=f"Offer letter sent to {candidate_email}")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Email error: {str(e)}")]

    # ──── Tool 4: Onboarding Email ────
    elif name == "send_onboarding_email":
        candidate_name = arguments["candidate_name"]
        candidate_email = arguments["candidate_email"]
        job_title = arguments["job_title"]
        company_name = arguments["company_name"]
        joining_date = arguments["joining_date"]
        google_token = arguments["google_token"]

        subject = f"Welcome to {company_name}! — Onboarding Details 🎊"
        body = f"""Dear {candidate_name},

We are thrilled that you have accepted our offer for {job_title} at {company_name}!

ONBOARDING DETAILS:
━━━━━━━━━━━━━━━━━━━━━━
📅 Joining Date:    {joining_date}
⏰ Reporting Time:  09:00 AM
🏢 Company:         {company_name}
💼 Position:        {job_title}
━━━━━━━━━━━━━━━━━━━━━━

REQUIRED DOCUMENTS:
- CNIC / National ID Card (Original + Copy)
- Educational Certificates (Original + Copy)
- Experience Letters from previous employers
- 2 Passport Size Photos
- Bank Account Details for salary processing

FIRST DAY SCHEDULE:
09:00 AM — Arrival & Registration
09:30 AM — HR Orientation
10:30 AM — Team Introduction
11:00 AM — Workspace Setup

Company handbook and policies will be shared on your first day.

Best regards,
HR Team
{company_name}"""

        try:
            _send_mail(google_token, candidate_email, subject, body)

            return [types.TextContent(type="text", text=f"Onboarding email sent to {candidate_email}")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Email error: {str(e)}")]

    # ──── Tool 5: Rejection Email ────
    elif name == "send_rejection_email":
        candidate_name = arguments["candidate_name"]
        candidate_email = arguments["candidate_email"]
        job_title = arguments["job_title"]
        company_name = arguments["company_name"]
        ceo_name = arguments["ceo_name"]
        google_token = arguments["google_token"]

        subject = f"Application Update — {job_title} at {company_name}"
        body = f"""Dear {candidate_name},

Thank you for your interest in the {job_title} position at {company_name} and for taking the time to go through our recruitment process.

After careful consideration, we regret to inform you that we will not be moving forward with your application at this time.

This was a difficult decision as we had many qualified candidates. We were impressed with your background and encourage you to apply for future openings that match your skills and experience.

We wish you all the best in your job search and future endeavors.

Best regards,
{ceo_name}
CEO, {company_name}"""

        try:
            _send_mail(google_token, candidate_email, subject, body)

            return [types.TextContent(type="text", text=f"Rejection email sent to {candidate_email}")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Email error: {str(e)}")]

    # ──── Tool 6: Salary Slip Email (PDF attachment) ────
    elif name == "send_payroll_email":
        employee_name = arguments["employee_name"]
        employee_email = arguments["employee_email"]
        period_label = arguments["period_label"]
        net_salary = arguments["net_salary"]
        currency = arguments.get("currency", "PKR")
        company_name = arguments["company_name"]
        pdf_base64 = arguments["pdf_base64"]
        google_token = arguments["google_token"]

        subject = f"Salary Slip - {period_label}"
        body = f"""Dear {employee_name},

Please find attached your salary slip for {period_label}.

Net Salary: {currency} {net_salary}

The slip includes a full breakdown of your earnings, deductions and the
attendance record they were calculated from. If any figure does not look
right, please contact HR.

This mailbox is not monitored - please write to HR with any questions.

{company_name}
HR Department"""

        try:
            # Base64 back to bytes — MCP carries JSON, not raw bytes
            pdf_bytes = base64.b64decode(pdf_base64)

            # The slip goes as an attachment — the salary breakdown is
            # not repeated in the email body, it lives in the PDF
            safe = "".join(ch for ch in employee_name
                           if ch.isalnum() or ch in " -_").strip()
            filename = (f"salary-slip-{safe.replace(' ', '_')}"
                        f"-{period_label.replace(' ', '-')}.pdf")

            _send_mail(google_token, employee_email, subject, body,
                       attachment=(filename, pdf_bytes, "pdf"))

            return [types.TextContent(
                type="text",
                text=f"Salary slip emailed to {employee_email}"
            )]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Email error: {str(e)}")]

    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())