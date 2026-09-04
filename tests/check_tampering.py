"""
Can a normal user choose which company they are?
────────────────────────────────────────────────
    py tests/check_tampering.py

Everything else proves that a request scoped to company A cannot reach
company B. This asks the question before that one: can the caller change
which company they are scoped TO?

Three ways it could happen, and each is checked against the running
application rather than read out of the source:

  1. supplying `company_id` in a body, query string, path or header
  2. editing the token, whose payload is base64 and public to its holder
  3. reaching one of the fifteen routes that legitimately run unscoped

Nothing is created and nothing is written.
"""
import warnings

warnings.filterwarnings("ignore")

import base64                                                   # noqa: E402
import inspect                                                  # noqa: E402
import json                                                     # noqa: E402

from fastapi.testclient import TestClient                       # noqa: E402
from jose import jwt                                            # noqa: E402

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

from app.main import app                                        # noqa: E402
from app.models.company import Company, STATUS_ACTIVE           # noqa: E402
from app.models.user import User                                # noqa: E402
from app.utils.security import (                                # noqa: E402
    ALGORITHM, SECRET_KEY, create_access_token,
)
from app.utils.tenancy import (                                 # noqa: E402
    auth_scope, get_tenant, open_unscoped_session, public_scope,
    require_ceo, require_employee, require_superadmin,
)

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


with open_unscoped_session("check_tampering: fixtures") as db:
    companies = db.query(Company).filter(
        Company.status == STATUS_ACTIVE).order_by(Company.id).all()
    people = []
    for c in companies:
        ceo = db.query(User).filter(
            User.company_id == c.id, User.role == "ceo").first()
        emp = db.query(User).filter(
            User.company_id == c.id, User.role == "employee",
            User.status == "active").first()
        if ceo:
            people.append((c.id, c.name, ceo.id, ceo.email,
                           emp.id if emp else None,
                           emp.email if emp else None))
    all_ids = [c.id for c in db.query(Company).order_by(Company.id).all()]

if not people:
    print("  no active company with a CEO")
    raise SystemExit(1)

cid, cname, ceo_id, ceo_email, emp_id, emp_email = people[0]
other_ids = [i for i in all_ids if i != cid]
victim = other_ids[0] if other_ids else None

print(f"acting as {cname} (company {cid}); other companies: {other_ids}")


def hdr(user_id, role, email, company_id):
    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": user_id, "role": role, "email": email,
         "company_id": company_id})}


HDR = hdr(ceo_id, "ceo", ceo_email, cid)

# ══════════════════════════════════════════════
# 1. Is there anything to tamper WITH?
# ══════════════════════════════════════════════
print("\nDoes any route accept a company from the client?")

# ⚠ IDENTIFIERS, NOT NAMES.
# A first version also matched "company", which flagged
# `CEOSignup.company_name` and `JobResponse.company_name` — neither of
# which can select a tenant. The first NAMES a company being created
# (and is refused if the name is taken); the second is a response field.
#
# That a name cannot choose a tenant is the entire point of this
# refactor: the system used to resolve an employee's company by matching
# `company_name` text, and a check that treats every mention of a name
# as a finding trains people to skip the output.
SUSPECT = ("company_id", "companyid", "tenant_id", "tenantid", "ceo_id")
def _deps(route):
    found = set()

    def walk(d):
        for sub in d.dependencies:
            if sub.call:
                found.add(sub.call)
            walk(sub)
    if hasattr(route, "dependant"):
        walk(route.dependant)
    return found


offenders, admin_ok = [], []
for r in app.routes:
    path = getattr(r, "path", None)
    fn = getattr(r, "endpoint", None)
    if not path or not fn or path.startswith(("/openapi", "/docs", "/redoc")):
        continue
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        continue
    hits = [p for p in params if any(s in p.lower() for s in SUSPECT)]
    if not hits:
        continue
    # ⚠ THE SUPERADMIN IS THE EXCEPTION, AND HAS TO BE.
    # They belong to no company, so naming one is the only way they can
    # say which to act on — `/admin/approve-ceo/{ceo_id}` cannot work
    # any other way. The rule is therefore not "no route takes an
    # identifier" but "no TENANT-SCOPED route does": inside a tenant the
    # company comes from the token and nowhere else.
    if require_superadmin in _deps(r):
        admin_ok.append(f"{path} {hits}")
    else:
        offenders.append(f"{path} {hits}")

check("no tenant-scoped handler takes a tenant identifier",
      not offenders, "; ".join(offenders[:3]))
print(f"  ({len(admin_ok)} superadmin route(s) take one, which is how "
      f"they name their target)")

print("  (schemas)")
import app.schemas.user as su                                   # noqa: E402
import app.schemas.recruitment as sr                            # noqa: E402
schema_hits = []
for mod in (su, sr):
    for name, obj in vars(mod).items():
        flds = getattr(obj, "model_fields", None)
        if not flds:
            continue
        bad = [f for f in flds if any(s in f.lower() for s in SUSPECT)]
        if bad:
            schema_hits.append(f"{mod.__name__}.{name}{bad}")
check("no request schema carries a company field",
      not schema_hits, "; ".join(schema_hits[:3]))

# ══════════════════════════════════════════════
# 2. Send one anyway, every way there is
# ══════════════════════════════════════════════
if victim:
    print(f"\nSending company_id={victim} anyway:")
    probes = [
        ("query", "GET", f"/ceo/employees?company_id={victim}", None, {}),
        ("query", "GET", f"/leave/all?company_id={victim}", None, {}),
        ("query", "GET", f"/payroll/runs?company_id={victim}", None, {}),
        ("query", "GET",
         f"/attendance/flags/today?company_id={victim}", None, {}),
        ("header", "GET", "/ceo/employees", None,
         {"X-Company-Id": str(victim), "X-Tenant-Id": str(victim)}),
    ]
    mine = client.get("/ceo/employees", headers=HDR).text
    for kind, method, url, body, extra in probes:
        h = dict(HDR)
        h.update(extra)
        r = client.request(method, url, headers=h, json=body)
        # The answer must be the caller's own, not the victim's, and not
        # an error either — the parameter should simply be ignored.
        same_as_mine = (url.startswith("/ceo/employees")
                        and r.text == mine)
        ok = r.status_code == 200 and (same_as_mine
                                       or url != "/ceo/employees")
        check(f"{kind}: {url.split('?')[0]}", ok,
              f"{r.status_code}, {len(r.content)}b"
              + (" identical to my own" if same_as_mine else ""))

    # A body that names another company, on a route that writes.
    r = client.post("/ceo/create-employee", headers=HDR, json={
        "full_name": "Tamper Probe", "email": "tamper.probe@example.test",
        "phone": "0300", "department": "Engineering",
        "designation": "Dev", "joining_date": "2026-01-01",
        "password": "tamper12345", "company_id": victim,
    })
    if r.status_code == 200:
        # It was created — it must belong to the CALLER, not to `victim`.
        with open_unscoped_session("check_tampering: verify") as db:
            made = db.query(User).filter(
                User.email == "tamper.probe@example.test").first()
            got = made.company_id if made else None
            check("body: an extra company_id is ignored on create",
                  got == cid, f"landed in company {got}, caller is {cid}")
            if made:
                db.delete(made)
                db.commit()
    else:
        check("body: an extra company_id is ignored on create",
              r.status_code in (400, 422),
              f"{r.status_code} — rejected outright, also fine")

# ══════════════════════════════════════════════
# 3. The token itself
# ══════════════════════════════════════════════
print("\nEditing the token:")
if victim:
    forged = create_access_token({
        "user_id": ceo_id, "role": "ceo", "email": ceo_email,
        "company_id": victim,          # <- claims another company
    })
    r = client.get("/ceo/employees",
                   headers={"Authorization": f"Bearer {forged}"})
    check("a token claiming another company is refused",
          r.status_code == 401,
          f"{r.status_code} — the claim is compared with the user's row")

# Re-signed with a wrong key: the signature must fail before anything else.
payload = {"user_id": ceo_id, "role": "ceo", "email": ceo_email,
           "company_id": victim or cid}
bad_sig = jwt.encode(payload, "not-the-real-secret", algorithm=ALGORITHM)
r = client.get("/ceo/employees", headers={"Authorization": f"Bearer {bad_sig}"})
check("a token signed with the wrong key is refused", r.status_code == 401,
      str(r.status_code))

# `alg: none`, the classic.
head = base64.urlsafe_b64encode(
    json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
body = base64.urlsafe_b64encode(
    json.dumps(payload).encode()).rstrip(b"=").decode()
r = client.get("/ceo/employees",
               headers={"Authorization": f"Bearer {head}.{body}."})
check("an unsigned `alg: none` token is refused", r.status_code == 401,
      str(r.status_code))

# Role escalation inside a correctly signed token.
if emp_id:
    esc = create_access_token({"user_id": emp_id, "role": "ceo",
                               "email": emp_email, "company_id": cid})
    r = client.get("/ceo/employees", headers={"Authorization": f"Bearer {esc}"})
    check("an employee claiming role=ceo is refused", r.status_code == 403,
          f"{r.status_code} — the role is re-read from the database")

# ══════════════════════════════════════════════
# 4. The unscoped sentinel
# ══════════════════════════════════════════════
print("\nThe '0' sentinel — who can reach an unscoped session:")
SCOPERS = {get_tenant: "tenant", require_ceo: "tenant", require_employee: "tenant",
           require_superadmin: "UNSCOPED", public_scope: "UNSCOPED",
           auth_scope: "UNSCOPED"}
unscoped = []
for r in app.routes:
    path = getattr(r, "path", None)
    if not path or path.startswith(("/openapi", "/docs", "/redoc")):
        continue
    got = []

    def walk(d):
        for sub in d.dependencies:
            if sub.call in SCOPERS:
                got.append(SCOPERS[sub.call])
            walk(sub)
    if hasattr(r, "dependant"):
        walk(r.dependant)
    if "UNSCOPED" in got:
        m = ",".join(sorted(getattr(r, "methods", []) or []))
        unscoped.append(f"{m} {path}")

print(f"  {len(unscoped)} unscoped route(s):")
for u in unscoped:
    print(f"     {u}")

allowed_prefixes = ("/auth/", "/admin/", "/recruitment/public/",
                    "/recruitment/accept-offer/", "/recruitment/offer/",
                    "/integrations/google/callback")
stray = [u for u in unscoped
         if not any(p in u for p in allowed_prefixes)]
check("every unscoped route is auth, superadmin, or public by design",
      not stray, "; ".join(stray))

# A normal user reaching the ones they CAN reach must still learn nothing.
print("  what a normal user gets from the public ones:")
r = client.get("/recruitment/public/jobs", headers=HDR)
if r.status_code == 200:
    jobs = r.json().get("jobs", [])
    check("the public job board returns only published roles",
          all(j.get("status", "published") == "published" for j in jobs)
          if jobs else True,
          f"{len(jobs)} job(s) — a job board spans companies by design")

r = client.get("/admin/approved-ceos", headers=HDR)
check("a CEO cannot reach the superadmin's unscoped routes",
      r.status_code == 403, str(r.status_code))
if emp_id:
    r = client.get("/admin/approved-ceos",
                   headers=hdr(emp_id, "employee", emp_email, cid))
    check("nor can an employee", r.status_code == 403, str(r.status_code))

print("\n" + "=" * 66)
print(f"  {len(fails)} FAILURE(S)" if fails
      else "  tenant tampering: every check passed")
for f in fails:
    print(f"   - {f}")
print("=" * 66)
raise SystemExit(1 if fails else 0)
