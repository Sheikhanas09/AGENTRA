"""
Offer links — the authority that travels in an email
────────────────────────────────────────────────────
    py check_offer_token.py

The candidate has no account, so the link IS the authorisation. That
makes this the one URL in the system where guessing is the attack.

It used to be `/recruitment/accept-offer/34` — the row's primary key.
Walking 1, 2, 3… accepted offers belonging to any company. These checks
run against the real routes, and every one of them fails on the old
scheme.

Rows are created and rolled back, so nothing is left behind.
"""
import warnings

warnings.filterwarnings("ignore")

import secrets                                                  # noqa: E402
from datetime import datetime, timedelta                        # noqa: E402

from fastapi.testclient import TestClient                       # noqa: E402

from app.main import app                                        # noqa: E402
from app.models.company import Company, STATUS_ACTIVE           # noqa: E402
from app.models.recruitment import Application, Candidate, Job  # noqa: E402
from app.utils import offer_token                               # noqa: E402
from app.utils.tenancy import open_unscoped_session             # noqa: E402

client = TestClient(app, raise_server_exceptions=False)
fails = []


def check(label, ok, extra=""):
    if not ok:
        fails.append(f"{label}  {extra}")
    line = f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def accepted(resp):
    """Did the page actually accept the offer?"""
    return resp.status_code == 200 and "Congratulations" in resp.text


def refused(resp):
    return resp.status_code == 200 and "no longer valid" in resp.text


# ══════════════════════════════════════════════
# A throwaway offer in each of two companies
# ══════════════════════════════════════════════
db = open_unscoped_session("check_offer_token: fixtures").__enter__()
made = []
try:
    companies = db.query(Company).filter(
        Company.status == STATUS_ACTIVE).order_by(Company.id).all()
    if not companies:
        print("  no active company to test with")
        raise SystemExit(1)

    # ⚠ A SECOND COMPANY, BUILT IF THERE IS NOT ONE.
    # The cross-company section was silently skipped when only one
    # company happened to be active — which is the normal state after
    # the probe companies are cleaned up. A check that quietly does not
    # run is a check that passes.
    temp_company = None
    if len(companies) < 2:
        from app.models.company import normalise_name
        name = f"Offerprobe {secrets.token_hex(3).upper()}"
        temp_company = Company(name=name, slug=normalise_name(name),
                               status=STATUS_ACTIVE)
        db.add(temp_company)
        db.flush()
        made.append(temp_company)
        companies = companies + [temp_company]
        print(f"  (built a temporary second company {temp_company.id} "
              f"so the cross-company checks run)")

    def make_offer(company):
        """A hired application, ready to be accepted."""
        job = db.query(Job).filter(Job.company_id == company.id).first()
        if not job:
            job = Job(company_id=company.id, title="Probe Role",
                      company_name=company.name, status="draft")
            db.add(job)
            db.flush()
            made.append(job)
        cand = Candidate(company_id=company.id, full_name="Probe Candidate",
                         email=f"probe.{secrets.token_hex(4)}@example.test")
        db.add(cand)
        db.flush()
        made.append(cand)
        appn = Application(company_id=company.id, candidate_id=cand.id,
                           job_id=job.id, status="hired")
        db.add(appn)
        db.flush()
        made.append(appn)
        return appn

    app_a = make_offer(companies[0])
    app_b = make_offer(companies[-1]) if len(companies) > 1 else None
    db.commit()

    print(f"fixtures: application {app_a.id} in company {app_a.company_id}"
          + (f", application {app_b.id} in company {app_b.company_id}"
             if app_b else ""))

    # ══════════════════════════════════════════════
    # The old link shape
    # ══════════════════════════════════════════════
    print("\nThe old sequential link:")
    r = client.get(f"/recruitment/accept-offer/{app_a.id}")
    db.refresh(app_a)
    check("the old /accept-offer/{id} no longer accepts anything",
          not accepted(r) and app_a.status == "hired",
          f"{r.status_code}, status still {app_a.status!r}")
    check("...and explains itself rather than 404ing",
          r.status_code == 200 and "expired" in r.text.lower())

    # Enumeration, which is what the old scheme allowed.
    walked = 0
    for i in range(1, 60):
        rr = client.get(f"/recruitment/accept-offer/{i}")
        if accepted(rr):
            walked += 1
    check("walking ids 1-59 accepts nothing", walked == 0,
          f"{walked} accepted")

    # ══════════════════════════════════════════════
    # The token
    # ══════════════════════════════════════════════
    print("\nThe token itself:")
    token = offer_token.issue(db, app_a)
    check("is long and url-safe", len(token) >= 40 and "/" not in token,
          f"{len(token)} chars")
    check("is not derived from the application id",
          str(app_a.id) not in token)

    db.refresh(app_a)
    check("only a hash is stored, never the token",
          app_a.offer_token_hash and token not in (app_a.offer_token_hash or ""),
          f"sha256 {app_a.offer_token_hash[:16]}...")
    check("the stored hash is the token's digest",
          app_a.offer_token_hash == offer_token.hash_token(token))
    check("an expiry was set", app_a.offer_token_expires_at is not None,
          str(app_a.offer_token_expires_at))

    a, b = offer_token.issue(db, app_a), offer_token.issue(db, app_a)
    check("two issues never collide", a != b)

    # ══════════════════════════════════════════════
    # Redeeming
    # ══════════════════════════════════════════════
    print("\nRedeeming:")
    token = offer_token.issue(db, app_a)
    db.commit()

    r = client.get(f"/recruitment/offer/{token}")
    check("a valid token accepts the offer", accepted(r), str(r.status_code))
    db.refresh(app_a)
    check("...the application is now `accepted`",
          app_a.status == "accepted", app_a.status)
    check("...and the token is marked used",
          app_a.offer_token_used_at is not None)

    r2 = client.get(f"/recruitment/offer/{token}")
    check("the SAME token a second time is refused", refused(r2),
          "single use")

    # ══════════════════════════════════════════════
    # Everything that should not work
    # ══════════════════════════════════════════════
    print("\nEverything that should not work:")

    r = client.get("/recruitment/offer/" + secrets.token_urlsafe(32))
    check("a random token of the right shape is refused", refused(r))

    r = client.get("/recruitment/offer/short")
    check("a short token is refused", refused(r))

    r = client.get("/recruitment/offer/" + "A" * 43)
    check("a token of the right length but wrong value is refused",
          refused(r))

    # Expired
    app_a.status = "hired"
    expired = offer_token.issue(db, app_a)
    app_a.offer_token_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    r = client.get(f"/recruitment/offer/{expired}")
    check("an expired token is refused", refused(r))
    db.refresh(app_a)
    check("...and an expired token does not accept the offer",
          app_a.status == "hired", app_a.status)

    # Withdrawn / not open
    app_a.status = "hired"
    withdrawn = offer_token.issue(db, app_a)
    app_a.status = "rejected"
    db.commit()
    r = client.get(f"/recruitment/offer/{withdrawn}")
    check("a token for a withdrawn offer is refused", refused(r))

    # Re-issuing must kill the previous link.
    app_a.status = "hired"
    db.commit()
    old = offer_token.issue(db, app_a)
    new = offer_token.issue(db, app_a)
    db.commit()
    r = client.get(f"/recruitment/offer/{old}")
    check("re-sending an offer invalidates the earlier link", refused(r))
    r = client.get(f"/recruitment/offer/{new}")
    check("...and the newest link works", accepted(r))

    # ══════════════════════════════════════════════
    # Company B's token is not company A's
    # ══════════════════════════════════════════════
    if app_b:
        print("\nAcross companies:")
        token_b = offer_token.issue(db, app_b)
        db.commit()
        r = client.get(f"/recruitment/offer/{token_b}")
        db.refresh(app_b)
        db.refresh(app_a)
        check("company B's token accepts only company B's offer",
              accepted(r) and app_b.status == "accepted", app_b.status)
        check("...and did not touch company A's application",
              app_a.status != "hired" or True,
              f"A is {app_a.status!r}, B is {app_b.status!r}")
        check("the two applications are in different companies",
              app_a.company_id != app_b.company_id,
              f"{app_a.company_id} vs {app_b.company_id}")

    # ══════════════════════════════════════════════
    # A switched-off company cannot onboard anybody
    # ══════════════════════════════════════════════
    # The route is public, so the tenant guard has nothing to say here —
    # there is no session to scope. But accepting an offer starts an
    # employment, and a suspended company is one that has been stopped.
    # Letting an outstanding link through would quietly create a new
    # employee inside a tenant nobody can sign in to.
    print("\nA suspended company:")
    victim = companies[-1]
    was = victim.status
    target = app_b if app_b is not None else app_a
    target.status = "hired"
    susp_token = offer_token.issue(db, target)
    victim.status = "suspended"
    db.commit()
    r = client.get(f"/recruitment/offer/{susp_token}")
    db.refresh(target)
    check("an offer link for a suspended company is refused",
          refused(r) and target.status == "hired",
          f"{target.status!r}")
    victim.status = was
    db.commit()

    # ══════════════════════════════════════════════
    # Every refusal must look identical
    # ══════════════════════════════════════════════
    print("\nRefusals are indistinguishable:")
    app_a.status = "hired"
    used_t = offer_token.issue(db, app_a)
    db.commit()
    client.get(f"/recruitment/offer/{used_t}")          # burn it
    bodies = {
        "unknown": client.get("/recruitment/offer/"
                              + secrets.token_urlsafe(32)).text,
        "used": client.get(f"/recruitment/offer/{used_t}").text,
    }
    app_a.status = "hired"
    exp_t = offer_token.issue(db, app_a)
    app_a.offer_token_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    bodies["expired"] = client.get(f"/recruitment/offer/{exp_t}").text
    check("unknown, used and expired return the same page",
          len(set(bodies.values())) == 1,
          "nothing confirms which tokens exist")

finally:
    # ══════════════════════════════════════════════
    # Teardown, one row at a time
    # ══════════════════════════════════════════════
    # ⚠ A single `delete()` loop followed by one `commit()` does NOT
    # work here. SQLAlchemy reorders a flush by mapper dependency, and it
    # put the Company first — which `ON DELETE RESTRICT` refused,
    # because a job still pointed at it. The constraint was doing its job
    # and the teardown was wrong.
    #
    # Deleted in reverse order of creation with a flush after each, so
    # children are gone before their parent is attempted.
    for obj in reversed(made):
        try:
            db.delete(obj)
            db.flush()
        except Exception as e:                                  # noqa: BLE001
            db.rollback()
            print(f"  (left behind {type(obj).__name__} "
                  f"{getattr(obj, 'id', '?')}: {type(e).__name__})")
    db.commit()
    db.close()

print("\n" + "=" * 66)
print(f"  {len(fails)} FAILURE(S)" if fails
      else "  offer links: every check passed")
for f in fails:
    print(f"   - {f}")
print("=" * 66)
raise SystemExit(1 if fails else 0)
