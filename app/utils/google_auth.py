"""
One company's Google credentials
────────────────────────────────
Everything that talks to Google goes through `credentials_for(db,
company_id)`. There is no other way to build a Google client, which is
the point: a caller cannot accidentally fall back to a shared account,
because there is no shared account left to fall back to.

═══════════════════════════════════════════════════════════
THE FLOW, AND WHY IT IS THE WEB ONE
═══════════════════════════════════════════════════════════
The old code used `InstalledAppFlow.run_local_server(port=0)` — which
opens a browser ON THE MACHINE RUNNING THE SERVER and waits. That is
fine for one company installing the app on their own laptop. With
several companies it is not a flow at all: the CEO is on their own
computer and the server is somewhere else, and nobody is sitting at the
server to click Allow.

So it is the authorisation-code flow:

    CEO clicks Connect  ->  GET /integrations/google/start
                            returns Google's URL, carrying a signed
                            `state` that says which company this is
    CEO approves at Google
    Google redirects    ->  GET /integrations/google/callback?code&state
                            state verified, code exchanged, token
                            encrypted and stored against that company

⚠ `state` IS NOT A LABEL, IT IS THE ONLY THING TYING THE CALLBACK TO A
COMPANY. The callback has no Authorization header — Google is the one
calling it — so if `state` were just a company id, anybody could hit the
callback with their own code and somebody else's id and attach THEIR
mailbox to that company. It is a short-lived signed token, verified
before it is believed.

═══════════════════════════════════════════════════════════
DESKTOP CLIENT, LOOPBACK REDIRECT
═══════════════════════════════════════════════════════════
`credentials.json` here is an "installed" (desktop) client, so Google
accepts a redirect to `http://localhost:<port>/<path>` and nothing else.
That works for this setup, where the browser and the backend are on the
same machine.

For a real deployment, create a "Web application" client in the Google
Cloud console, add the public callback URL to it, and point
`GOOGLE_REDIRECT_URI` at that. Nothing in this file changes — the
redirect URI is read from the environment for exactly that reason.
"""

import json
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from jose import JWTError, jwt

from app.models.integration import (
    CompanyIntegration, GOOGLE_SCOPES,
    STATUS_CONNECTED, STATUS_REVOKED, STATUS_ERROR,
)
from app.utils.crypto import decrypt, encrypt
from app.utils.security import ALGORITHM, SECRET_KEY

load_dotenv()

CLIENT_SECRETS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "credentials.json")

# Where Google sends the CEO back to. Overridable so the same code runs
# against localhost, ngrok, or a real domain.
REDIRECT_URI = (
    os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    or "http://localhost:8000/integrations/google/callback"
)

# Where the CEO's browser is sent afterwards, so they land back on the
# screen they started from rather than on a bare JSON response.
FRONTEND_URL = os.getenv("FRONTEND_URL", "").strip() or "http://localhost:5173"

# The `state` token's lifetime. Long enough to read Google's consent
# screen, short enough that a stale link is not a way in.
STATE_MINUTES = 15


class GoogleNotConnected(RuntimeError):
    """
    This company has not connected a Google account.

    Not an error to log and swallow — it is the answer, and the caller
    is expected to tell the CEO to connect one. The old behaviour was to
    quietly use somebody else's account, which is what this replaces.
    """


class GoogleClientMissing(RuntimeError):
    """`credentials.json` is not on disk, so nobody can connect at all."""


# ══════════════════════════════════════════════
# The signed `state`
# ══════════════════════════════════════════════
def make_state(company_id: int, user_id: int) -> str:
    return jwt.encode(
        {
            "purpose": "google_oauth",
            "company_id": int(company_id),
            "user_id": int(user_id),
            "exp": datetime.utcnow() + timedelta(minutes=STATE_MINUTES),
        },
        SECRET_KEY, algorithm=ALGORITHM,
    )


def read_state(state: str):
    """The company this callback belongs to, or None if it cannot be trusted."""
    try:
        claims = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if claims.get("purpose") != "google_oauth":
        # A login token is also a valid signature. Without this check one
        # could be pasted in as `state` and would be believed.
        return None
    return claims.get("company_id"), claims.get("user_id")


# ══════════════════════════════════════════════
# Starting and finishing the connection
# ══════════════════════════════════════════════
def _flow(state=None):
    from google_auth_oauthlib.flow import Flow

    if not os.path.exists(CLIENT_SECRETS_FILE):
        raise GoogleClientMissing(
            "app/credentials.json is missing — download the OAuth client "
            "from the Google Cloud console and put it there."
        )
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=GOOGLE_SCOPES, state=state)
    flow.redirect_uri = REDIRECT_URI
    return flow


def start_connection(db, company_id: int, user_id: int) -> str:
    """
    Begin the handshake: the URL to send the CEO to, and the PKCE
    verifier stored for the callback to finish with.

    ⚠ THE VERIFIER HAS TO OUTLIVE THIS REQUEST.
    `authorization_url()` generates one and sends only its SHA-256 hash
    to Google. The callback is a SEPARATE request with a fresh `Flow`
    object, which has no memory of it, and `fetch_token()` then sends
    `code_verifier=None`. Google answers:

        (invalid_grant) Missing code verifier

    Disabling PKCE (`autogenerate_code_verifier=False`) makes that error
    go away and removes the protection with it — PKCE is what stops an
    intercepted authorisation code from being redeemed by somebody else.
    So the verifier is kept here instead, encrypted, and cleared as soon
    as it is used.
    """
    flow = _flow(state=make_state(company_id, user_id))
    url, _ = flow.authorization_url(
        # Without this Google returns only an access token, which expires
        # in an hour — and then the nightly payroll email has no way to
        # send anything and nobody knows why.
        access_type="offline",
        # Forces the consent screen even if this account has approved
        # before, which is what makes Google hand over a refresh token on
        # a re-connect rather than silently returning none.
        prompt="consent",
        include_granted_scopes="true",
    )

    row = get_integration(db, company_id)
    if not row:
        row = CompanyIntegration(company_id=company_id, provider="google")
        db.add(row)
    row.pending_verifier = encrypt(flow.code_verifier)
    row.pending_started_at = datetime.utcnow()
    db.commit()

    return url


def take_pending_verifier(db, company_id: int):
    """
    The verifier for this connection attempt, consumed.

    Cleared whether or not the exchange then succeeds: it is good for one
    attempt, and a stale one lying around is a secret with no purpose.
    Anything older than the `state` token's own lifetime is ignored —
    the two are halves of the same attempt.
    """
    row = get_integration(db, company_id)
    if not row or not row.pending_verifier:
        return None

    started = row.pending_started_at
    verifier = decrypt(row.pending_verifier)

    row.pending_verifier = None
    row.pending_started_at = None
    db.commit()

    if started and datetime.utcnow() - started > timedelta(minutes=STATE_MINUTES):
        return None
    return verifier


def exchange_code(code: str, code_verifier: str = None):
    """The authorisation code for real credentials."""
    flow = _flow()
    # Set before `fetch_token`, which passes it through as
    # `code_verifier` — the other half of what was hashed into the
    # authorisation URL.
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials


def account_email_of(creds) -> str:
    """Which mailbox this actually is — the CEO's real question."""
    try:
        from googleapiclient.discovery import build
        profile = build("gmail", "v1", credentials=creds,
                        cache_discovery=False
                        ).users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception:                                           # noqa: BLE001
        return None


# ══════════════════════════════════════════════
# Storing and loading
# ══════════════════════════════════════════════
def save_credentials(db, company_id: int, user_id: int, creds) -> CompanyIntegration:
    row = db.query(CompanyIntegration).filter(
        CompanyIntegration.company_id == company_id).first()
    if not row:
        row = CompanyIntegration(company_id=company_id, provider="google")
        db.add(row)

    row.token_encrypted = encrypt(creds.to_json())
    row.account_email = account_email_of(creds) or row.account_email
    row.granted_scopes = " ".join(creds.scopes or [])
    row.status = STATUS_CONNECTED
    row.last_error = None
    row.connected_at = datetime.utcnow()
    row.connected_by = user_id
    db.commit()
    db.refresh(row)
    return row


def get_integration(db, company_id: int):
    return db.query(CompanyIntegration).filter(
        CompanyIntegration.company_id == company_id).first()


def credentials_for(db, company_id: int):
    """
    This company's Google credentials, refreshed if needed.

    ⚠ A REFRESHED TOKEN HAS TO BE WRITTEN BACK.
    Google hands out a new access token and google-auth updates the
    object in memory. Not saving it means every single call does the
    refresh round-trip again — and worse, when Google eventually rotates
    the refresh token itself, the stored copy is the old one and the
    connection dies at some unrelated moment weeks later.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    row = get_integration(db, company_id)
    if not row or not row.token_encrypted:
        raise GoogleNotConnected(
            "This company has not connected a Google account yet. "
            "Open Settings -> Integrations and press Connect Google."
        )

    raw = decrypt(row.token_encrypted)
    if not raw:
        # The key is gone or was rotated. Say that, rather than letting a
        # confusing Google error surface three layers up.
        row.status = STATUS_ERROR
        row.last_error = (
            "The stored token could not be decrypted — INTEGRATION_SECRET_KEY "
            "has changed or is missing. Reconnect Google."
        )
        db.commit()
        raise GoogleNotConnected(row.last_error)

    creds = Credentials.from_authorized_user_info(json.loads(raw), GOOGLE_SCOPES)

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            row.token_encrypted = encrypt(creds.to_json())
            row.status = STATUS_CONNECTED
            row.last_error = None
        except Exception as e:                                  # noqa: BLE001
            row.status = STATUS_REVOKED
            row.last_error = f"Google refused the stored token: {e}"
            db.commit()
            raise GoogleNotConnected(
                "The Google connection is no longer valid — it may have been "
                "revoked. Open Settings -> Integrations and connect again."
            ) from e

    row.last_used_at = datetime.utcnow()
    db.commit()
    return creds


def service_for(db, company_id: int, api: str, version: str):
    """A Google API client for one company. The only way to build one."""
    from googleapiclient.discovery import build

    return build(api, version, credentials=credentials_for(db, company_id),
                 cache_discovery=False)


def disconnect(db, company_id: int) -> bool:
    """
    Forget the token. Google is asked to revoke it as well, but the row
    is cleared either way — if the network call fails, "disconnected"
    still has to mean disconnected here.
    """
    row = get_integration(db, company_id)
    if not row:
        return False

    raw = decrypt(row.token_encrypted)
    if raw:
        try:
            import requests
            token = json.loads(raw).get("refresh_token")
            if token:
                requests.post("https://oauth2.googleapis.com/revoke",
                              params={"token": token}, timeout=8)
        except Exception:                                       # noqa: BLE001
            pass

    row.token_encrypted = None
    row.status = None
    row.granted_scopes = None
    row.last_error = None
    # An abandoned handshake leaves a verifier behind; it is useless on
    # its own, but a secret kept for no reason is still a secret kept.
    row.pending_verifier = None
    row.pending_started_at = None
    db.commit()
    return True
