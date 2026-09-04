"""
Connecting a company's Google account
─────────────────────────────────────
Four routes. Three are the CEO's; the fourth is Google's.

    GET    /integrations/status              what is connected, and what works
    POST   /integrations/google/connect      -> the URL to send the CEO to
    GET    /integrations/google/callback     Google comes back here
    DELETE /integrations/google              forget it, and revoke it

⚠ THE CALLBACK CANNOT BE AUTHENTICATED, AND THAT IS THE WHOLE PROBLEM.
Google calls it, not the CEO's app, so there is no token on the request.
Everything that decides WHICH COMPANY this connection belongs to has to
travel in `state` — and `state` comes back from the outside world, so it
is a signed token that is verified before it is believed. A plain
company id there would let anyone attach their own mailbox to somebody
else's company.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.integration import (
    CompanyIntegration, GOOGLE_SCOPES, STATUS_CONNECTED,
)
from app.utils import crypto
from app.utils.google_auth import (
    FRONTEND_URL, GoogleClientMissing, disconnect, exchange_code,
    get_integration, read_state, save_credentials, start_connection,
    take_pending_verifier,
)
from app.utils.mailer import can_send
from app.utils.tenancy import Tenant, get_tenant, public_scope, require_ceo

router = APIRouter(prefix="/integrations", tags=["Integrations"])


# ══════════════════════════════════════════════
# What is connected
# ══════════════════════════════════════════════
@router.get("/status")
def integration_status(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
):
    """
    Readable by any employee, deliberately: the help desk and the
    recruitment screens need to know whether email is working before
    they promise that something was sent. It returns which ACCOUNT is
    connected and nothing from inside it.
    """
    row = get_integration(db, tenant.company_id)
    ok, detail = can_send(db, tenant.company_id)

    granted = (row.granted_scopes or "") if row else ""
    return {
        "company": tenant.company_name,
        "connected": bool(row and row.is_live()),
        "account_email": row.account_email if row else None,
        "connected_at": str(row.connected_at) if row and row.connected_at else None,
        "status": row.status if row else None,
        "last_error": row.last_error if row else None,
        # What each granted scope actually buys, in the CEO's terms
        # rather than as a list of Google URLs.
        "features": {
            "read_applications": "gmail.readonly" in granted,
            "send_email": "gmail.send" in granted,
            "calendar_and_meet": "calendar" in granted,
        },
        "can_send_email": ok,
        "email_detail": detail,
        # Nothing can be stored safely without this, so the screen has to
        # be able to say so rather than failing at the last step.
        "secrets_configured": crypto.is_configured(),
    }


# ══════════════════════════════════════════════
# Start
# ══════════════════════════════════════════════
@router.post("/google/connect")
def google_connect(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_ceo),
):
    if not crypto.is_configured():
        raise HTTPException(
            status_code=500,
            detail=("INTEGRATION_SECRET_KEY is not set on the server, so a "
                    "Google token cannot be stored safely. Nothing was "
                    "connected."),
        )
    try:
        # Also stores the PKCE verifier for the callback to finish with.
        url = start_connection(db, tenant.company_id, tenant.user_id)
    except GoogleClientMissing as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "auth_url": url,
        "scopes": GOOGLE_SCOPES,
        "note": ("Sign in with the account this company should use for "
                 "recruitment email, applications and interview calendar "
                 "invites."),
    }


# ══════════════════════════════════════════════
# Google comes back
# ══════════════════════════════════════════════
@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(
    state: str = Query(None),
    code: str = Query(None),
    error: str = Query(None),
    db: Session = Depends(get_db),
    _: None = Depends(public_scope),
):
    """
    No authentication is possible here — see the module docstring. The
    signed `state` is the only thing that says which company this is,
    and it is checked before anything is written.
    """
    if error:
        return _page("Not connected",
                     f"Google reported: {error}. Nothing was changed.", False)
    if not state or not code:
        return _page("Not connected",
                     "The response from Google was incomplete. Please try "
                     "again from Settings -> Integrations.", False)

    claims = read_state(state)
    if not claims:
        # Expired, tampered with, or a token minted for something else.
        return _page(
            "Not connected",
            "That connection link is no longer valid. Start again from "
            "Settings -> Integrations.", False)

    company_id, user_id = claims

    # From here the session may touch this company's rows, and only this
    # company's — the id came from a signature, not from the URL.
    from app.utils.tenancy import bind_tenant
    bind_tenant(db, company_id)

    verifier = take_pending_verifier(db, company_id)
    if not verifier:
        return _page(
            "Not connected",
            "This connection attempt has expired, or it was started "
            "somewhere else. Go back to Settings -> Integrations and press "
            "Connect Google again.", False)

    try:
        creds = exchange_code(code, verifier)
        row = save_credentials(db, company_id, user_id, creds)
    except Exception as e:                                      # noqa: BLE001
        return _page("Not connected",
                     f"The connection could not be completed: {e}", False)

    missing = [s.rsplit("/", 1)[-1] for s in GOOGLE_SCOPES
               if s.rsplit("/", 1)[-1] not in (row.granted_scopes or "")]
    note = ""
    if missing:
        # Google lets people approve some permissions and not others, so
        # this says which parts will not work rather than letting them
        # fail one at a time later.
        note = ("<p class='warn'>Some permissions were not granted: "
                + ", ".join(missing)
                + ". The features that need them will not work until you "
                  "connect again and allow them.</p>")

    return _page(
        "Connected",
        f"<b>{row.account_email or 'This account'}</b> is now connected. "
        f"Applications, interview invitations and company email will use "
        f"it.{note}", True)


# ══════════════════════════════════════════════
# Stop
# ══════════════════════════════════════════════
@router.delete("/google")
def google_disconnect(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_ceo),
):
    existed = disconnect(db, tenant.company_id)
    return {
        "message": ("Google has been disconnected. Applications can no "
                    "longer be fetched, and no email or interview invitation "
                    "will be sent until you connect an account again."
                    if existed else "Nothing was connected."),
        "disconnected": existed,
    }


def _page(title: str, body: str, good: bool) -> HTMLResponse:
    """
    The CEO ends up looking at this in a browser tab, so it says what
    happened rather than returning JSON at a person.
    """
    colour = "#05DC7F" if good else "#f87171"
    html = f"""<!DOCTYPE html><html><head><title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0a0a;
   min-height:100vh;display:flex;align-items:center;justify-content:center;
   margin:0;padding:24px}}
 .card{{background:#111;border:1px solid {colour}55;border-radius:20px;
   padding:40px;max-width:520px;text-align:center}}
 h1{{color:{colour};font-size:22px;margin:0 0 12px}}
 p{{color:#9ca3af;font-size:15px;line-height:1.6;margin:0 0 8px}}
 .warn{{color:#fbbf24;font-size:14px;margin-top:14px}}
 a{{display:inline-block;margin-top:22px;color:#0a0a0a;background:{colour};
   padding:10px 20px;border-radius:10px;text-decoration:none;font-weight:600}}
</style></head><body><div class="card">
<div style="font-size:52px">{'&#10003;' if good else '&#9888;'}</div>
<h1>{title}</h1><p>{body}</p>
<a href="{FRONTEND_URL}/ceo/dashboard">Back to Agentra</a>
</div></body></html>"""
    return HTMLResponse(content=html,
                        headers={"ngrok-skip-browser-warning": "true"})
