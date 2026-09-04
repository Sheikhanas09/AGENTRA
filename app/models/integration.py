"""
Each company's own Google account
─────────────────────────────────
Recruitment reaches outside the system in three places — it reads CVs
from a mailbox, it creates a Meet link on a calendar, and it emails
candidates. All three used ONE Google account, hard-coded:

    app/credentials.json + app/token.json      one OAuth token
    sender_email="nirmal.naik1994@gmail.com"   in four places
    GMAIL_APP_PASSWORD                          one password in .env

That was correct while there was one company. With more than one it
produces three separate problems, and the third is not a cosmetic one:

  1. Every company's offer letters and rejections arrive from a stranger's
     personal Gmail address.

  2. Every company's interviews land on that one person's calendar.

  3. ⚠ CV FETCH SHARED ONE MAILBOX, MATCHED ONLY ON THE JOB TITLE:

         query = f'subject:"Application for {job_title}" has:attachment'
         service.users().messages().list(userId='me', q=query)

     Two companies both hiring a "Backend Developer" — and whichever
     one presses Fetch pulls the OTHER company's applicants out of the
     shared inbox and files them, CVs and all, as its own candidates.

The tenant guard cannot help with that one. It protects what is IN the
database; this is data arriving from outside and being written as the
caller's own. The fix has to be upstream: each company connects its own
Google account, and then there is no shared inbox to mix up.
"""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, DateTime, Text, LargeBinary, ForeignKey, Index,
)

from app.database import Base

# What the system asks each company for, and why each one is needed.
#   gmail.readonly  read applications and their CV attachments
#   gmail.send      send interview invitations, offers, rejections, and
#                   the leave/payroll notifications — from the company's
#                   own address, which is also why no app password is
#                   stored anywhere any more
#   calendar        create the interview event and its Meet link
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]

STATUS_CONNECTED = "connected"
STATUS_REVOKED = "revoked"      # Google refused the refresh token
STATUS_ERROR = "error"          # something else went wrong; reason stored


class CompanyIntegration(Base):
    """
    One row per company. Absent means "not connected", and every caller
    treats that as a normal state rather than a failure — a company that
    has not connected Google simply cannot use the parts that need it,
    and is told so.
    """

    __tablename__ = "company_integrations"
    __table_args__ = (
        Index("ix_integration_company", "company_id"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # The tenant column, so the guard and the row-level security policies
    # cover this table like every other. `unique` because one company has
    # one Google account.
    company_id = Column(
        Integer,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False, unique=True, index=True,
    )

    provider = Column(String(20), nullable=False, default="google")

    # ══════════════════════════════════════════════
    # The token, encrypted
    # ══════════════════════════════════════════════
    # The full authorised-user JSON that google-auth writes, put through
    # `utils/crypto.py`. It holds a refresh token, which does not expire
    # on its own — anybody reading this column in the clear could read
    # that company's mail until somebody noticed and revoked it.
    #
    # BYTEA, not text: it is ciphertext, and storing ciphertext in a
    # string column invites somebody to "fix the encoding" one day.
    token_encrypted = Column(LargeBinary, nullable=True)

    # ──── Shown to the CEO, safe to store in the clear ────
    # Which account is connected. Without it the Integrations screen can
    # only say "connected", and the CEO cannot tell whether it is the
    # right mailbox — which is the one question they will have.
    account_email = Column(String(255), nullable=True)

    # The scopes actually granted. Google lets a user tick some and not
    # others, so what was ASKED for is not what was necessarily given;
    # the screen reads this to say which features are live.
    granted_scopes = Column(Text, nullable=True)

    status = Column(String(20), nullable=True)
    last_error = Column(Text, nullable=True)

    # ══════════════════════════════════════════════
    # PKCE: the half of the handshake that stays here
    # ══════════════════════════════════════════════
    # `authorization_url()` generates a 128-character `code_verifier`,
    # sends only its SHA-256 hash to Google, and `fetch_token()` must
    # then present the original. Those are two separate HTTP requests
    # with a trip through Google in between, so the verifier has to
    # survive somewhere — and a fresh `Flow` object in the callback has
    # no memory of it. That produced, on the very first real connection:
    #
    #     (invalid_grant) Missing code verifier
    #
    # ⚠ IT CANNOT TRAVEL IN `state`. `state` goes to Google and comes
    # back through the browser's address bar, and the whole point of
    # PKCE is that the verifier is the one thing an attacker who
    # intercepts the redirect does NOT have. Putting it there would
    # leave the parameter in place and remove the protection.
    #
    # Encrypted like the token, and cleared the moment it is used.
    pending_verifier = Column(LargeBinary, nullable=True)
    pending_started_at = Column(DateTime, nullable=True)

    connected_at = Column(DateTime, nullable=True)
    connected_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    def is_live(self) -> bool:
        return bool(self.token_encrypted) and self.status == STATUS_CONNECTED

    def has_scope(self, scope: str) -> bool:
        return scope in (self.granted_scopes or "")

    def __repr__(self):  # pragma: no cover
        return (f"<CompanyIntegration company={self.company_id} "
                f"{self.account_email!r} {self.status}>")
