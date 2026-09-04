"""
Reading job applications out of a company's own mailbox.

═══════════════════════════════════════════════════════════
THIS FILE USED TO OPEN ONE SHARED INBOX
═══════════════════════════════════════════════════════════
    TOKEN_FILE = app/token.json          # one file, one Google account
    def get_gmail_service():             # no company anywhere in sight
        ...
        creds = flow.run_local_server(port=0)

Every company's "Fetch & Screen" read the SAME mailbox, and the only
thing narrowing it was the subject line:

    query = f'subject:"Application for {job_title}" has:attachment'

So two companies both hiring a "Backend Developer" shared their
applicants: whichever pressed Fetch first pulled the other's candidates
out of that inbox and filed them — names, emails, CVs — as its own.

The tenant guard could not prevent it. It protects what is in the
database; this was data arriving from outside and being written as the
caller's own. The fix had to be upstream, so the mailbox is now the
company's own and there is no shared inbox to mix up.

`run_local_server(port=0)` is also gone. It opened a browser ON THE
SERVER and waited for somebody to click Allow, which is not something a
CEO on their own machine can do.
"""

import base64

import fitz  # pymupdf

from app.utils.google_auth import service_for


def get_gmail_service(db, company_id: int):
    """
    This company's mailbox, and no other.

    Raises `GoogleNotConnected` when the company has not connected an
    account — which the route turns into a clear message. There is no
    fallback to a shared account, because a fallback is what this
    replaces.
    """
    return service_for(db, company_id, "gmail", "v1")


# ──── Extract the PDF attachment ────
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


# ══════════════════════════════════════════════
# Whose CV is this?
# ══════════════════════════════════════════════
# The text scan below reads the extracted lines and returns the first
# one SHAPED like a name: two to four alphabetic words, capitalised, no
# digits, not a section heading.
#
# ⚠ A CV IS FULL OF THINGS SHAPED LIKE NAMES. Employers, universities,
# tools, city names. Whether the candidate's own name comes first is
# down to the order PyMuPDF happens to emit the blocks in — and on a
# two-column CV it often does not:
#
#     line 10   'Wise Tech'        <- his EMPLOYER, taken as his name
#     line 27   'MUHAMMAD ANAS'    <- his actual name
#
# Two candidates were filed as "Wise Tech" that way.
#
# So the PDF is asked first. On essentially every CV the name is the
# largest text on page one — that is what a CV IS, typographically — and
# that is a fact about the document rather than a guess about word
# order. The text scan stays as the fallback for a CV with no usable
# font information.
def _looks_like_a_person(text: str) -> bool:
    """Two to four capitalised alphabetic words, and not a heading."""
    t = (text or "").strip()
    if not (2 < len(t) < 50) or "@" in t:
        return False
    if any(c.isdigit() for c in t):
        return False
    if any(kw in t.lower() for kw in _NOT_A_NAME):
        return False
    words = t.split()
    if not (2 <= len(words) <= 4):
        return False
    if not all(w.replace("-", "").replace("'", "").replace(".", "").isalpha()
               for w in words):
        return False
    return any(w[0].isupper() for w in words if w)


# Headings and stock CV phrases. Shared by both routes so they cannot
# drift apart.
_NOT_A_NAME = [
    'street', 'road', 'avenue', 'city', 'http', 'www',
    'linkedin', 'github', 'objective', 'summary', 'profile',
    'curriculum', 'vitae', 'resume', 'experience', 'education',
    'skills', 'projects', 'certifications', 'languages',
    'references', 'contact', 'address', 'phone', 'email',
    'university', 'college', 'school', 'institute', 'present',
    'semester', 'bachelor', 'master', 'bs in', 'ms in',
    'results-driven', 'motivated', 'passionate', 'seeking',
    'looking', 'developed', 'responsible', 'worked', 'camscanner',
    # Job titles sit right under the name in the same large-ish type
    'developer', 'engineer', 'designer', 'manager', 'analyst',
    'intern', 'consultant', 'specialist', 'architect', 'scientist',
    'full stack', 'frontend', 'backend', 'technologies',
]


def extract_name_from_pdf(pdf_bytes) -> str:
    """
    The biggest name-shaped line on page one, or None.

    Reads the actual font sizes rather than the flattened text, so the
    heading wins over anything the reading order puts before it.
    """
    if not pdf_bytes:
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[0]
            # Same span, same size, same line — joined, because a name
            # is often two spans ("MUHAMMAD" / "ANAS").
            lines = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", [])
                             if s.get("text", "").strip()]
                    if not spans:
                        continue
                    text = " ".join(s["text"].strip() for s in spans)
                    size = max(s.get("size", 0) for s in spans)
                    lines.append((size, " ".join(text.split())))
        finally:
            doc.close()
    except Exception as e:                                      # noqa: BLE001
        print(f"[gmail] could not read the PDF for a name: {e}")
        return None

    for size, text in sorted(lines, key=lambda x: -x[0]):
        if _looks_like_a_person(text):
            return text
    return None


# ──── Extract the name from the CV ────
def extract_name_from_cv(cv_text: str, fallback_name: str,
                         pdf_bytes=None) -> str:
    # The document's own typography first — see the note above.
    from_pdf = extract_name_from_pdf(pdf_bytes)
    if from_pdf:
        return from_pdf

    if not cv_text:
        return fallback_name

    lines = cv_text.strip().split('\n')

    # One list, shared with the PDF route — two copies of a keyword
    # list is two lists that eventually disagree.
    skip_keywords = _NOT_A_NAME

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


# ──── Fetch the emails ────
def fetch_job_application_emails(db, company_id: int, job_title: str,
                                 max_results: int = 20):
    """
    Applications for one job, from ONE COMPANY'S mailbox.

    `db` and `company_id` are not decoration: they are what makes
    `userId="me"` below mean this company's account instead of a shared
    one. The subject filter narrows within that mailbox; it is not, and
    never was, a tenant boundary.
    """
    service = get_gmail_service(db, company_id)

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
                fallback_name=sender_name or sender_email.split('@')[0],
                # The PDF itself, so the name can be read from the
                # typography rather than from the order the text
                # happened to come out in.
                pdf_bytes=pdf_bytes,
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