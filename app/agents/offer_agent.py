import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def generate_offer_letter(
    candidate_name: str,
    candidate_email: str,
    job_title: str,
    company_name: str,
    salary_range: str,
    ceo_name: str,
    application_id: int,
    sender_email: str,
    sender_password: str
) -> dict:

    today = datetime.now().strftime("%B %d, %Y")

    # ──── Accept Link ────
    ngrok_url = os.getenv("NGROK_URL", "http://127.0.0.1:8000")
    accept_link = f"{ngrok_url}/recruitment/accept-offer/{application_id}?ngrok-skip-browser-warning=true"

    # ──── Offer Letter body ────
    body = f"""Dear {candidate_name},

Congratulations! We are pleased to offer you the position of {job_title} at {company_name}.

OFFER DETAILS:
━━━━━━━━━━━━━━━━━━━━━━
💼 Position:      {job_title}
🏢 Company:       {company_name}
💰 Salary:        {salary_range}
📅 Offer Date:    {today}
━━━━━━━━━━━━━━━━━━━━━━

We were thoroughly impressed with your skills, experience, and the enthusiasm you demonstrated throughout our recruitment process.

TO ACCEPT THIS OFFER:
Please click the link below to accept your offer:
👉 {accept_link}

This offer is valid for 3 business days.

Upon acceptance, you will receive:
- Joining date and onboarding details
- Required documents list
- First day schedule
- Company policies and handbook

We look forward to welcoming you to the {company_name} family!

Best regards,
{ceo_name}
CEO, {company_name}

---
This is an official offer letter from {company_name}.
"""

    # ──── Email bhejo ────
    email_sent = False
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = candidate_email
        msg['Subject'] = f"Job Offer — {job_title} at {company_name} 🎉"
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, candidate_email, msg.as_string())
        server.quit()
        email_sent = True
        print(f"Offer letter sent to {candidate_email}")

    except Exception as e:
        print(f"Email error: {e}")
        email_sent = False

    return {
        "candidate_name": candidate_name,
        "candidate_email": candidate_email,
        "job_title": job_title,
        "company_name": company_name,
        "offer_date": today,
        "accept_link": accept_link,
        "email_sent": email_sent
    }