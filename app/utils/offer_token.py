"""
The link in a candidate's offer email
─────────────────────────────────────
That link is opened by somebody with no account, from their inbox, so it
carries its own authority. Until now that authority was the row's
primary key:

    /recruitment/accept-offer/34

Which is to say: a counter. Anyone could walk 1, 2, 3… and every row
sitting at `status == "hired"` would flip to `accepted` — for ANY
company — and fire an onboarding email at the candidate. No login, no
company, nothing to guess.

The tenant guard cannot help. The route is public by necessity, so it
runs unscoped on purpose; there is no session to scope it to. The
authority has to be in the link itself.

═══════════════════════════════════════════════════════════
WHAT THE LINK CARRIES NOW
═══════════════════════════════════════════════════════════
    secrets.token_urlsafe(32)     256 bits, from the OS CSPRNG

Not a counter, not a UUID (v4 is fine but v1 leaks time and MAC), and
not derived from the application id — derivable is guessable.

═══════════════════════════════════════════════════════════
THE DATABASE STORES A HASH, NOT THE TOKEN
═══════════════════════════════════════════════════════════
The plaintext exists exactly once, in the email that is being sent, and
is never written down. A dump of `applications` yields SHA-256 digests,
which are not links.

Plain SHA-256 rather than bcrypt/argon2 on purpose: this is a 256-bit
random value, not a human password. There is no dictionary to try and no
work factor that would help — and the digest has to be looked up by
index on every click, which a deliberately slow hash would make
expensive for no gain.

═══════════════════════════════════════════════════════════
SINGLE USE, AND EXPIRING
═══════════════════════════════════════════════════════════
Redeeming stamps `offer_token_used_at`, and a used token is refused
afterwards — so a link forwarded, quoted in a reply, or sitting in a
mail archive cannot be replayed. Offers also stop being open forever:
`OFFER_LINK_DAYS` (default 14) bounds the window.

Re-sending an offer issues a NEW token and overwrites the stored hash,
so exactly one link is live at a time and an earlier one stops working.

═══════════════════════════════════════════════════════════
EVERY FAILURE LOOKS THE SAME FROM OUTSIDE
═══════════════════════════════════════════════════════════
Unknown, expired, already used, wrong status — all produce one response.
Distinguishing them would confirm which tokens exist, which is the
enumeration this replaces.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

# 32 bytes -> 43 url-safe characters. Long enough that guessing is not a
# threat model; short enough to survive being wrapped by a mail client.
TOKEN_BYTES = 32

DEFAULT_DAYS = 14


def link_days() -> int:
    try:
        n = int(os.getenv("OFFER_LINK_DAYS", "").strip() or DEFAULT_DAYS)
    except ValueError:
        return DEFAULT_DAYS
    return n if n > 0 else DEFAULT_DAYS


def hash_token(token: str) -> str:
    """The stored form. Hex so it fits an indexed VARCHAR."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(db, application, days: int = None) -> str:
    """
    Mint a link for this application and return the PLAINTEXT token.

    The caller puts it in the email and then forgets it — this is the
    only moment it exists outside the candidate's inbox.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    application.offer_token_hash = hash_token(token)
    application.offer_token_expires_at = (
        datetime.utcnow() + timedelta(days=days or link_days()))
    # A re-issue clears any previous redemption, because this is a new
    # offer link and it has not been used.
    application.offer_token_used_at = None
    db.commit()
    return token


# Why a redemption was refused. The candidate is never shown which —
# see the module docstring — but the server logs it, and the tests
# assert on it.
INVALID = "invalid"        # no such token
EXPIRED = "expired"
USED = "used"
NOT_OPEN = "not_open"      # withdrawn, already accepted, rejected


def redeem(db, token: str):
    """
    Exchange a token for its application, exactly once.

    Returns `(application, None)` on success, or `(None, reason)`.
    The application is NOT modified here — the route decides what
    acceptance means. This only answers "is this link good, and whose is
    it", and marks it spent.
    """
    from app.models.recruitment import Application

    if not token or len(token) < 20:
        # Short-circuits obvious junk without touching the database.
        return None, INVALID

    row = db.query(Application).filter(
        Application.offer_token_hash == hash_token(token)
    ).first()

    if not row:
        return None, INVALID
    if row.offer_token_used_at is not None:
        return None, USED
    if (row.offer_token_expires_at
            and row.offer_token_expires_at < datetime.utcnow()):
        return None, EXPIRED
    if row.status != "hired":
        # The offer was withdrawn, or somebody already went through this.
        return None, NOT_OPEN

    # Marked spent BEFORE the caller acts on it. If the work afterwards
    # fails, the link is still burnt — which is the right way round: a
    # replayable link is worse than a candidate who has to ask for a new
    # one.
    row.offer_token_used_at = datetime.utcnow()
    db.commit()
    return row, None


def build_link(base_url: str, token: str) -> str:
    """
    The URL that goes in the email.

    A separate path from the old `/accept-offer/{id}` so the two can
    never be confused: this one takes a token, that one now only
    explains that it no longer works.
    """
    base = (base_url or "").rstrip("/")
    return f"{base}/recruitment/offer/{token}?ngrok-skip-browser-warning=true"
