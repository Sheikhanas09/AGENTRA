"""
Per-company Google — the boundary checks
────────────────────────────────────────
    py tests/check_integrations.py

Everything except the final click-through at Google itself, which needs
a human and a browser. What is checked here is the part that decides
WHOSE mailbox gets attached to WHICH company — and that is exactly the
part a person clicking Allow cannot verify for you.

The one that matters most: the callback cannot be authenticated, because
Google is the caller. So a signed `state` is the only thing tying it to
a company, and the checks below include offering a perfectly valid LOGIN
token as that `state` — correctly signed, wrong purpose.
"""
import warnings

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient                      # noqa: E402
# ──── Backend/ ko raaste par lao ────
# Yeh script Backend/ ke andar ek folder mein hai. `py tests/x.py`
# chalane par Python sirf us folder ko sys.path par rakhta hai, cwd ko
# nahi — to `import app` nakaam ho jata. Aur kuch checks source tree ko
# `Path("app")` se scan karte hain, jo cwd par munhasir hai.
import os as _os
import sys as _sys

_BACKEND = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)
_os.chdir(_BACKEND)

from app.main import app                                       # noqa: E402
from app.models.company import Company                         # noqa: E402
from app.models.integration import CompanyIntegration          # noqa: E402
from app.models.user import User                               # noqa: E402
from app.utils.security import create_access_token             # noqa: E402
from app.utils.tenancy import open_unscoped_session            # noqa: E402

client = TestClient(app, raise_server_exceptions=False)
fails = []


def check(label, ok, extra=""):
    if not ok:
        fails.append(f"{label}  {extra}")
    try:
        print(f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}")
    except UnicodeEncodeError:
        line = f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}"
        print(line.encode("ascii", "replace").decode("ascii"))


with open_unscoped_session("integration test") as db:
    people = {}
    for c in db.query(Company).order_by(Company.id).all():
        ceo = db.query(User).filter(User.company_id == c.id,
                                    User.role == "ceo").first()
        # An ACTIVE one: `get_tenant` refuses a fired account, so
        # picking the first employee row tested the wrong thing.
        emp = db.query(User).filter(
            User.company_id == c.id, User.role == "employee",
            User.status == "active").first()
        people[c.id] = (c.name, c.status, ceo, emp)
    rows = {r.company_id: r for r in db.query(CompanyIntegration).all()}

print("companies:")
for cid, (name, status, ceo, emp) in people.items():
    conn = "connected" if cid in rows else "not connected"
    print(f"   {cid:5}  {name:<12} {status:<10} {conn}")


def tok(u):
    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": u.id, "role": u.role, "email": u.email,
         "company_id": u.company_id})}


live = [(cid, v) for cid, v in people.items()
        if v[1] == "active" and v[2]]
cid, (name, _s, ceo, emp) = live[0]
hdr = tok(ceo)

print(f"\nAs {name} (company {cid}):")

r = client.get("/integrations/status", headers=hdr)
check("status reads", r.status_code == 200, str(r.status_code))
if r.status_code == 200:
    j = r.json()
    print(f"        connected={j['connected']}  account={j['account_email']}")
    print(f"        features={j['features']}")
    print(f"        can_send_email={j['can_send_email']}  ({j['email_detail']})")
    check("the encryption key is configured", j["secrets_configured"])

    # ⚠ NOT "is it connected?" — that is whatever the operator last did,
    # and a check that asserts a particular state fails the moment
    # somebody legitimately disconnects. What must always hold is that
    # the two answers AGREE: mail can be sent exactly when a live
    # connection granted `gmail.send`, and never on some other basis.
    expected_send = bool(j["connected"] and j["features"]["send_email"])
    check("'can send email' matches the granted scopes",
          j["can_send_email"] == expected_send,
          f"connected={j['connected']} "
          f"send_scope={j['features']['send_email']} "
          f"can_send={j['can_send_email']}")
    if not j["connected"]:
        check("a disconnected company reports no account and no features",
              j["account_email"] is None
              and not any(j["features"].values()),
              "nothing left behind by the disconnect")

r = client.post("/integrations/google/connect", headers=hdr)
check("connect returns a Google URL",
      r.status_code == 200 and "accounts.google.com" in r.json().get("auth_url", ""),
      str(r.status_code))
if r.status_code == 200:
    url = r.json()["auth_url"]
    for needed in ["gmail.readonly", "gmail.send", "calendar",
                   "access_type=offline", "state="]:
        check(f"the URL asks for {needed}", needed in url)

# ══════════════════════════════════════════════
# Can this machine actually reach Google?
# ══════════════════════════════════════════════
# Everything below can pass while nothing works. On a network that
# advertises IPv6 without routing it, `www.googleapis.com` resolves to
# eight IPv6 addresses BEFORE any IPv4 one; the connection is attempted
# in that order and the HTTP client's timeout fires long before the
# working addresses are reached.
#
# The account connects (that host returns a single IPv6 address, so the
# fallback has time), and then every call using it hangs: fetching
# applications returns 500, the interview email never sends, and the
# Meet link falls back to a placeholder. It reads as a broken feature
# and is a broken route.
print("\nReaching Google from this machine:")

from app.utils import net                                       # noqa: E402

check("the IPv4 preference is installed", net.enabled(),
      "set PREFER_IPV4=false in .env to turn it off")

for host in ["oauth2.googleapis.com", "gmail.googleapis.com",
             "www.googleapis.com"]:
    up, how = net.reachable(host, timeout=3)
    check(f"{host} is reachable", up, how)
    if "IPv6 unreachable" in how:
        # The finding, not a failure: IPv4 works, so the preference above
        # is carrying the system. Worth saying out loud, because without
        # it every Google call on this machine hangs.
        print(f"         {net.describe(host)}")


# ══════════════════════════════════════════════
# PKCE — the half of the handshake that never leaves this server
# ══════════════════════════════════════════════
# The first real connection failed with `(invalid_grant) Missing code
# verifier`: the callback built a fresh `Flow`, which had no memory of
# the verifier the authorisation URL had generated.
#
# Nothing in this suite noticed, and that is the lesson — every state
# check below passes perfectly well with no verifier existing at all.
# So the handshake is now checked in its two halves, including the
# arithmetic Google itself will do.
print("\nPKCE, the half Google never sees:")

import base64                                                   # noqa: E402
import hashlib                                                  # noqa: E402
from urllib.parse import parse_qs, urlparse                     # noqa: E402

from app.models.integration import CompanyIntegration           # noqa: E402
from app.utils.crypto import decrypt                            # noqa: E402
from app.utils.google_auth import take_pending_verifier         # noqa: E402
from app.utils.tenancy import open_tenant_session               # noqa: E402

r = client.post("/integrations/google/connect", headers=hdr)
challenge_sent = None
if r.status_code == 200:
    q = parse_qs(urlparse(r.json()["auth_url"]).query)
    challenge_sent = (q.get("code_challenge") or [None])[0]
    check("the authorisation URL carries a PKCE challenge",
          bool(challenge_sent), (challenge_sent or "")[:22] + "...")
    check("...hashed with S256",
          (q.get("code_challenge_method") or [None])[0] == "S256")

with open_tenant_session(cid) as db:
    row = db.query(CompanyIntegration).filter(
        CompanyIntegration.company_id == cid).first()
    stored = bytes(row.pending_verifier) if row and row.pending_verifier else None
    check("the verifier was kept for the callback to finish with",
          bool(stored), f"{len(stored) if stored else 0} bytes")
    check("and kept as ciphertext, not the raw string",
          bool(stored) and stored.startswith(b"gAAAA"))

    verifier = decrypt(stored) if stored else None
    if verifier and challenge_sent:
        # Exactly what Google recomputes at the token endpoint. If these
        # do not match, the exchange fails the way it did the first time.
        digest = hashlib.sha256(verifier.encode()).digest()
        recomputed = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        check("the stored verifier hashes to the challenge that was sent",
              recomputed == challenge_sent, "SHA-256(verifier) == challenge")

    first = take_pending_verifier(db, cid)
    second = take_pending_verifier(db, cid)
    check("the verifier is consumed on use",
          bool(first) and second is None,
          "replaying the same callback link gets nothing")

r = client.get("/integrations/google/callback?code=x&state="
               + create_access_token({
                   "purpose": "google_oauth", "company_id": cid,
                   "user_id": ceo.id}))
check("a callback with no verifier waiting is refused",
      "expired" in r.text or "started somewhere else" in r.text,
      "rather than reaching Google and failing there")


# ══════════════════════════════════════════════
# The part that actually matters
# ══════════════════════════════════════════════
print("\nThe callback is the only unauthenticated way in — so:")

r = client.get("/integrations/google/callback?code=x&state=garbage")
check("a forged state is refused",
      r.status_code == 200 and "no longer valid" in r.text,
      "and nothing was written")

# A perfectly valid LOGIN token, offered as `state`. The signature checks
# out; only the `purpose` claim stops it.
login_token = create_access_token(
    {"user_id": ceo.id, "role": "ceo", "email": ceo.email,
     "company_id": cid})
r = client.get(f"/integrations/google/callback?code=x&state={login_token}")
check("a valid LOGIN token is refused as `state`",
      "no longer valid" in r.text,
      "signed correctly, but not for this purpose")

r = client.get("/integrations/google/callback?error=access_denied")
check("a refusal at Google is handled",
      r.status_code == 200 and "Nothing was changed" in r.text)

r = client.get("/integrations/google/callback")
check("a callback with nothing in it is handled",
      r.status_code == 200 and "incomplete" in r.text)

# ══════════════════════════════════════════════
# One company cannot see or touch another's
# ══════════════════════════════════════════════
print("\nAcross companies:")
other = [(c, v) for c, v in people.items() if c != cid and v[2]]
if other:
    ocid, (oname, ostatus, oceo, _e) = other[0]
    ohdr = tok(oceo)
    r = client.get("/integrations/status", headers=ohdr)
    if ostatus != "active":
        check(f"{oname} is suspended, so it is refused entirely",
              r.status_code == 403, str(r.status_code))
    else:
        # ⚠ NOT "the other company is not connected".
        # That asserted a STATE, and the state changed the moment the
        # operator legitimately connected Google for the second company
        # too — the check failed while nothing was wrong. Third time
        # this suite family has made that mistake.
        #
        # What must always hold is that each company's answer comes from
        # ITS OWN row. So both are compared against the database rather
        # than against an assumption about which one is set up.
        from app.utils.tenancy import open_unscoped_session as _un
        with _un("check_integrations: comparing rows") as _db:
            rows_now = {x.company_id: x for x in
                        _db.query(CompanyIntegration).all()}
            mine_row = rows_now.get(cid)
            theirs_row = rows_now.get(ocid)
            mine_conn = bool(mine_row and mine_row.is_live())
            theirs_conn = bool(theirs_row and theirs_row.is_live())
            theirs_email = theirs_row.account_email if theirs_row else None

        body = r.json() if r.status_code == 200 else {}
        check(f"{oname} reads its OWN integration row",
              r.status_code == 200
              and body.get("connected") == theirs_conn
              and body.get("account_email") == theirs_email,
              f"reports connected={body.get('connected')} "
              f"(row says {theirs_conn})")
        check(f"...and {name}'s row is a separate one",
              mine_row is not theirs_row,
              f"{name} connected={mine_conn}, {oname} connected={theirs_conn}"
              + ("  — both connected, which is fine: the check is that each"
                 " reads its own" if mine_conn and theirs_conn else ""))

# An employee may read the status (screens need it) but not change it.
if emp:
    ehdr = tok(emp)
    r = client.get("/integrations/status", headers=ehdr)
    check("an employee may READ the status", r.status_code == 200,
          str(r.status_code))
    r = client.post("/integrations/google/connect", headers=ehdr)
    check("an employee may NOT connect", r.status_code == 403,
          str(r.status_code))
    r = client.delete("/integrations/google", headers=ehdr)
    check("an employee may NOT disconnect", r.status_code == 403,
          str(r.status_code))

# ══════════════════════════════════════════════
# The token never leaves the server
# ══════════════════════════════════════════════
print("\nThe stored token:")
r = client.get("/integrations/status", headers=hdr)
body = r.text
leaked = [w for w in ("refresh_token", "client_secret", "token_encrypted",
                      "1//", "GOCSPX") if w in body]
check("no part of the token appears in any response", not leaked, str(leaked))

with open_unscoped_session("integration test") as db:
    row = db.query(CompanyIntegration).filter(
        CompanyIntegration.company_id == cid).first()
    if row and row.token_encrypted:
        raw = bytes(row.token_encrypted)
        check("what is stored is ciphertext, not JSON",
              b"refresh_token" not in raw and not raw.lstrip().startswith(b"{"),
              f"{len(raw)} bytes, starts {raw[:12]!r}")
        from app.utils.crypto import decrypt
        check("and it decrypts back to a real token",
              "refresh_token" in (decrypt(raw) or ""))

# ══════════════════════════════════════════════
# A company that has not connected gets a clear refusal
# ══════════════════════════════════════════════
print("\nA company with no Google account:")
from app.utils.google_auth import GoogleNotConnected, credentials_for  # noqa: E402
from app.utils.mailer import can_send, send_as_company                 # noqa: E402
from app.utils.tenancy import open_tenant_session                      # noqa: E402

unconnected = [c for c in people if c not in rows]
if unconnected:
    ucid = unconnected[0]
    with open_tenant_session(ucid) as db:
        try:
            credentials_for(db, ucid)
            check("credentials_for refuses", False, "it returned something")
        except GoogleNotConnected as e:
            check("credentials_for refuses with a readable message",
                  "Connect Google" in str(e), str(e)[:70])
        ok, detail = can_send(db, ucid)
        check("can_send says no", not ok, detail)
        sent, detail = send_as_company(db, ucid, to="x@example.com",
                                       subject="probe", text_body="probe")
        check("sending does not fall back to a shared account",
              not sent, detail[:70])
else:
    print("   (every company is connected — nothing to check here)")

print("\n" + "=" * 66)
print(f"  {len(fails)} FAILURE(S)" if fails
      else "  per-company Google: every check passed")
for f in fails:
    print(f"   - {f}")
print("=" * 66)

# ⚠ THIS WAS MISSING, AND IT IS THE WHOLE POINT OF A CHECK.
# Every other suite ends with it; this one printed "1 FAILURE(S)" and
# then exited 0. Anything reading the exit code — CI, a shell loop, the
# `for s in ...` line used to run them all — was told it passed.
#
# A checker that cannot report failure is worse than no checker: it is a
# green light nobody earned.
raise SystemExit(1 if fails else 0)
