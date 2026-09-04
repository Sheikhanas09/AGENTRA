"""
Does it still hold with more than two companies?

The design does not count companies — every session carries one
`company_id` and every query is filtered by it — so N should behave
exactly like 2. That is a claim, and this file is the test of it rather
than an assertion about it.

Four fresh companies are registered through the real signup route, each
given its own employee, job, salary and leave type, and then EVERY
ORDERED PAIR is probed in both directions: 4 companies = 12 attacker /
target pairs, not just the two adjacent ones.

    py _e2e_many.py             build them, probe, leave them in place
    py _e2e_many.py --cleanup   remove them afterwards

Everything it creates is named "Probe ..." with @probetest.example
accounts, so `_cleanup_probe.py` also removes them.
"""
import random
import string
import sys
import warnings

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient                      # noqa: E402
from app.main import app                                       # noqa: E402
from app.models.company import Company                         # noqa: E402
from app.models.payroll import Payslip, SalaryStructure        # noqa: E402
from app.models.recruitment import Job                         # noqa: E402
from app.models.user import User                               # noqa: E402
from app.utils.security import create_access_token             # noqa: E402
from app.utils.tenancy import open_unscoped_session            # noqa: E402

HOW_MANY = 4
CLEANUP = "--cleanup" in sys.argv

client = TestClient(app, raise_server_exceptions=False)
fails = []


def check(label, ok, extra=""):
    if not ok:
        fails.append(f"{label}   {extra}")
    print(f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}")


def superadmin_header():
    with open_unscoped_session("e2e-many: superadmin") as db:
        sa = db.query(User).filter(User.role == "superadmin").first()
    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": sa.id, "role": "superadmin", "email": sa.email,
         "company_id": None})}


SA = superadmin_header()


def build(n):
    """Register one company all the way to having data in it."""
    tag = "".join(random.choices(string.ascii_lowercase, k=6))
    name = f"Probe {tag.upper()}"
    ceo_email = f"ceo.{tag}@probetest.example"
    emp_email = f"emp.{tag}@probetest.example"
    pw = "probe12345"

    r = client.post("/auth/ceo-signup", json={
        "full_name": f"Owner {n}", "email": ceo_email, "company_name": name,
        "password": pw, "confirm_password": pw})
    if r.status_code != 200:
        raise SystemExit(f"signup {n} failed: {r.status_code} {r.text[:200]}")
    cid, ceo_uid = r.json()["company_id"], r.json()["user_id"]

    client.put(f"/admin/approve-ceo/{ceo_uid}", headers=SA)

    r = client.post("/auth/login", json={"email": ceo_email, "password": pw})
    hdr = {"Authorization": "Bearer " + r.json()["access_token"]}

    r = client.post("/ceo/create-employee", headers=hdr, json={
        "full_name": f"Worker {tag.upper()}", "email": emp_email,
        "phone": "0300", "department": "Engineering",
        "designation": "Backend Developer", "joining_date": "2026-01-10",
        "password": "worker12345"})
    emp_id = r.json().get("employee_id")

    client.post("/settings/work-policy", headers=hdr, json={
        "working_days": ["Monday", "Tuesday", "Wednesday", "Thursday",
                         "Friday"],
        "shift_start": "09:00", "shift_end": "18:00",
        "late_tolerance_mins": 15, "min_daily_hours": 8.0,
        "overtime_threshold": 9.0})

    # A salary that is unique to this company, so a leak is unmistakable
    # in the response body rather than something to be inferred.
    salary = 100000 + n * 1111
    client.post("/payroll/salary-structure", headers=hdr, json={
        "employee_id": emp_id, "base_salary": salary, "house_allowance": 0,
        "transport_allowance": 0, "medical_allowance": 0,
        "other_allowances": 0})

    r = client.post("/recruitment/jobs/create", headers=hdr, json={
        "title": f"Engineer {tag.upper()}", "department": "Engineering",
        "employment_type": "Full-time", "experience": "2 years",
        "skills": "Python", "salary_range": str(salary),
        "additional_info": ""})
    job_id = r.json().get("job_id")

    r = client.post("/auth/login", json={"email": emp_email,
                                         "password": "worker12345"})
    ehdr = {"Authorization": "Bearer " + r.json()["access_token"]}

    return {
        "n": n, "name": name, "tag": tag.upper(), "cid": cid,
        "ceo_uid": ceo_uid, "emp_id": emp_id, "job_id": job_id,
        "salary": salary, "hdr": hdr, "ehdr": ehdr,
    }


print("=" * 72)
print(f"  {HOW_MANY} NEW COMPANIES — every pair, both directions")
print("=" * 72)

print("\nBuilding:")
cos = []
for i in range(1, HOW_MANY + 1):
    c = build(i)
    cos.append(c)
    print(f"  {c['name']:<14} company={c['cid']:<5} ceo_user={c['ceo_uid']:<4}"
          f" employee={c['emp_id']:<4} job={c['job_id']:<4}"
          f" salary={c['salary']}")

ids = [c["cid"] for c in cos]
check("every company got its own id", len(set(ids)) == len(ids), str(ids))
check("no company id equals its CEO's user id",
      all(c["cid"] != c["ceo_uid"] for c in cos),
      "this is what makes the test able to fail")

# ══════════════════════════════════════════════
# Every ordered pair
# ══════════════════════════════════════════════
print(f"\nCross-tenant probes ({HOW_MANY * (HOW_MANY - 1)} ordered pairs, "
      f"7 routes each):")

leaks = 0
for a in cos:
    for b in cos:
        if a is b:
            continue
        probes = [
            ("GET", f"/recruitment/jobs/{b['job_id']}"),
            ("DELETE", f"/recruitment/jobs/{b['job_id']}"),
            ("GET", f"/payroll/salary-structure/{b['emp_id']}"),
            ("GET", f"/leave/balance/{b['emp_id']}"),
            ("GET", f"/attendance/today/{b['emp_id']}"),
            ("GET", f"/leave/history/{b['emp_id']}"),
            ("GET", f"/attendance/summary/{b['emp_id']}/2026/9"),
        ]
        got = []
        for method, url in probes:
            r = client.request(method, url, headers=a["hdr"])
            if r.status_code == 200 and len(r.content) > 2:
                got.append(f"{method} {url} -> 200")
        if got:
            leaks += len(got)
            for g in got:
                check(f"{a['name']} -> {b['name']}", False, g)

if not leaks:
    print(f"  [ok]   {HOW_MANY * (HOW_MANY - 1) * 7} attempts across "
          f"{HOW_MANY * (HOW_MANY - 1)} pairs — none returned data")

# ══════════════════════════════════════════════
# Nobody's own numbers turn up in anybody else's list
# ══════════════════════════════════════════════
print("\nList screens — each company's own figures only:")
LISTS = ["/ceo/employees", "/recruitment/jobs", "/payroll/salary-structures",
         "/leave/all", "/attendance/flags/today", "/hr/overview",
         "/recruitment/all-employees", "/settings/work-policy"]

for url in LISTS:
    trouble = []
    for a in cos:
        r = client.get(url, headers=a["hdr"])
        if r.status_code != 200:
            continue
        # Every other company's unique salary and tag must be absent.
        for b in cos:
            if b is a:
                continue
            if str(b["salary"]) in r.text or b["tag"] in r.text:
                trouble.append(f"{a['name']} saw {b['name']}'s data")
    check(url, not trouble, "; ".join(trouble[:2]))

# ══════════════════════════════════════════════
# And each employee sees only their own employer
# ══════════════════════════════════════════════
print("\nEmployees:")
emp_trouble = []
for a in cos:
    for b in cos:
        if b is a:
            continue
        for url in [f"/payroll/salary-structure/{b['emp_id']}",
                    f"/leave/balance/{b['emp_id']}",
                    f"/attendance/today/{b['emp_id']}"]:
            r = client.get(url, headers=a["ehdr"])
            if r.status_code == 200 and len(r.content) > 2:
                emp_trouble.append(f"{a['name']}'s employee read {url}")
check("no employee reaches another company", not emp_trouble,
      "; ".join(emp_trouble[:2]))

# Their own payslip route still answers for themselves
own_ok = all(client.get("/payroll/slips", headers=c["ehdr"]).status_code == 200
             for c in cos)
check("every employee can still read their own", own_ok)

# ══════════════════════════════════════════════
# The database's own view, per company
# ══════════════════════════════════════════════
print("\nAt the database, with the ORM out of the picture:")
from sqlalchemy import text                                    # noqa: E402
from app.database import engine                                # noqa: E402

with engine.connect() as conn:
    counts = {}
    for c in cos:
        conn.execute(text(f"SET \"agentra.company_id\" = '{c['cid']}'"))
        counts[c["cid"]] = {
            "users": conn.execute(text("SELECT count(*) FROM users")).scalar(),
            "jobs": conn.execute(text("SELECT count(*) FROM jobs")).scalar(),
            "salaries": conn.execute(
                text("SELECT count(*) FROM salary_structures")).scalar(),
        }
    for cid, v in counts.items():
        print(f"     company {cid}: {v}")
    check("each company sees exactly its own 2 users and 1 job",
          all(v["users"] == 2 and v["jobs"] == 1 and v["salaries"] == 1
              for v in counts.values()))

    conn.execute(text("SET \"agentra.company_id\" = '-1'"))
    stray = conn.execute(text("SELECT count(*) FROM users")).scalar()
    check("an unknown company sees nothing at all", stray == 0, f"{stray} rows")

print("\n" + "=" * 72)
if fails:
    print(f"  {len(fails)} FAILURE(S)")
    for f in fails:
        print(f"   - {f}")
else:
    print(f"  {HOW_MANY} companies, fully separated — and the design does not "
          f"count them")
print("=" * 72)

if CLEANUP:
    print("\nCleaning up…")
    import subprocess
    subprocess.run([sys.executable, "_cleanup_probe.py", "--apply"])
else:
    print(f"\n  {HOW_MANY} probe companies left in place. Remove them with:")
    print("     py _cleanup_probe.py --apply")

sys.exit(1 if fails else 0)
