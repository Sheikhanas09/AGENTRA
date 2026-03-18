import os
import base64
import json
import fitz  # pymupdf
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '..', 'credentials.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), '..', 'token.json')


# ──── Gmail Auth ────
def get_gmail_service():
    creds = None

    # Token pehle se hai?
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Token nahi hai ya expire ho gaya
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Token save karo
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


# ──── PDF attachment extract karo ────
def extract_pdf_from_attachment(service, message_id, attachment_id):
    attachment = service.users().messages().attachments().get(
        userId='me',
        messageId=message_id,
        id=attachment_id
    ).execute()

    data = attachment.get('data', '')
    pdf_bytes = base64.urlsafe_b64decode(data + '==')

    # PDF text extract
    cv_text = ""
    try:
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in pdf_doc:
            cv_text += page.get_text()
        pdf_doc.close()
    except Exception as e:
        print(f"PDF extract error: {e}")

    return cv_text, pdf_bytes


# ──── Emails fetch karo ────
def fetch_job_application_emails(job_title: str, max_results: int = 20):
    service = get_gmail_service()

    # Subject mein job title wali emails dhundo
    query = f'subject:"Application for {job_title}" has:attachment'

    results = service.users().messages().list(
        userId='me',
        q=query,
        maxResults=max_results
    ).execute()

    messages = results.get('messages', [])
    applications = []

    for msg in messages:
        message = service.users().messages().get(
            userId='me',
            messageId=msg['id'],
            format='full'
        ).execute()

        # Email headers se info lo
        headers = message['payload'].get('headers', [])
        sender_email = ""
        sender_name = ""
        subject = ""

        for header in headers:
            if header['name'] == 'From':
                from_value = header['value']
                # "Name <email>" format parse karo
                if '<' in from_value:
                    sender_name = from_value.split('<')[0].strip().strip('"')
                    sender_email = from_value.split('<')[1].replace('>', '').strip()
                else:
                    sender_email = from_value.strip()
            elif header['name'] == 'Subject':
                subject = header['value']

        # PDF attachment dhundo
        cv_text = ""
        cv_filename = ""
        parts = message['payload'].get('parts', [])

        for part in parts:
            if part.get('filename', '').endswith('.pdf'):
                attachment_id = part['body'].get('attachmentId', '')
                if attachment_id:
                    cv_text, _ = extract_pdf_from_attachment(
                        service, msg['id'], attachment_id
                    )
                    cv_filename = part['filename']
                    break

        if cv_text and sender_email:
            applications.append({
                'email': sender_email,
                'name': sender_name or sender_email.split('@')[0],
                'subject': subject,
                'cv_text': cv_text,
                'cv_filename': cv_filename,
                'message_id': msg['id']
            })

    return applications