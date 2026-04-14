import os
import base64
import fitz  # pymupdf
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/calendar']
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), '..', 'credentials.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), '..', 'token.json')


# ──── Gmail Auth ────
def get_gmail_service():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

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

    cv_text = ""
    try:
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in pdf_doc:
            cv_text += page.get_text()
        pdf_doc.close()
    except Exception as e:
        print(f"PDF extract error: {e}")

    return cv_text, pdf_bytes


# ──── CV se naam extract karo ────
def extract_name_from_cv(cv_text: str, fallback_name: str) -> str:
    if not cv_text:
        return fallback_name

    lines = cv_text.strip().split('\n')

    skip_keywords = [
        'street', 'road', 'avenue', 'city', 'http', 'www',
        'linkedin', 'github', 'objective', 'summary', 'profile',
        'curriculum', 'vitae', 'resume', 'experience', 'education',
        'skills', 'projects', 'certifications', 'languages',
        'references', 'contact', 'address', 'phone', 'email',
        'university', 'college', 'school', 'institute', 'present',
        'semester', 'bachelor', 'master', 'bs in', 'ms in',
        'results-driven', 'motivated', 'passionate', 'seeking',
        'looking', 'developed', 'responsible', 'worked', 'camscanner'
    ]

    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            continue
        if '@' in trimmed:
            continue
        digit_count = sum(c.isdigit() for c in trimmed)
        if digit_count > 2:
            continue
        lower = trimmed.lower()
        if any(kw in lower for kw in skip_keywords):
            continue
        if not (2 < len(trimmed) < 50):
            continue
        words = trimmed.split()
        if (
            2 <= len(words) <= 4 and
            all(word.replace('-', '').replace("'", '').replace('.', '').isalpha()
                for word in words)
        ):
            if any(word[0].isupper() for word in words if word):
                return trimmed

    return fallback_name


# ──── Emails fetch karo ────
def fetch_job_application_emails(job_title: str, max_results: int = 20):
    service = get_gmail_service()

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
            id=msg['id'],
            format='full'
        ).execute()

        headers = message['payload'].get('headers', [])
        sender_email = ""
        sender_name = ""
        subject = ""

        for header in headers:
            if header['name'] == 'From':
                from_value = header['value']
                if '<' in from_value:
                    sender_name = from_value.split('<')[0].strip().strip('"')
                    sender_email = from_value.split('<')[1].replace('>', '').strip()
                else:
                    sender_email = from_value.strip()
            elif header['name'] == 'Subject':
                subject = header['value']

        cv_text = ""
        cv_filename = ""
        pdf_bytes = b""
        parts = message['payload'].get('parts', [])

        for part in parts:
            if part.get('filename', '').endswith('.pdf'):
                attachment_id = part['body'].get('attachmentId', '')
                if attachment_id:
                    cv_text, pdf_bytes = extract_pdf_from_attachment(
                        service, msg['id'], attachment_id
                    )
                    cv_filename = part['filename']
                    break

        if cv_text and sender_email:
            extracted_name = extract_name_from_cv(
                cv_text,
                fallback_name=sender_name or sender_email.split('@')[0]
            )

            applications.append({
                'email': sender_email,
                'name': extracted_name,
                'subject': subject,
                'cv_text': cv_text,
                'cv_pdf': pdf_bytes,
                'cv_filename': cv_filename,
                'message_id': msg['id']
            })

    return applications