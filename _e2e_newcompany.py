"""
A brand new company, end to end.

This is the test that matters most for this change. Every company that
existed before the migration has `company_id == its CEO's user id`,
because the migration kept those ids on purpose. So the old data cannot
tell you whether `ceo.id` is still being used somewhere a COMPANY was
meant — the two numbers agree, and the bug hides.

A company registered afterwards has company id 1000+ and a CEO with an
ordinary user id, and every one of those places breaks loudly.
"""
import random
import string
import warnings

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                           # noqa: E402
from app.models.company import Company             # noqa: E402
from app.models.user import User                   # noqa: E402
from app.utils.security import create_access_token  # noqa: E402
from app.utils.tenancy import open_unscoped_session  # noqa: E402

c = TestClient(app, raise_server_exceptions=False)
tag = "".join(random.choices(string.ascii_lowercase, k=5))
NAME = f"Probe {tag.upper()}"
EMAIL = f"ceo.{tag}@probetest.example"
EMP_EMAIL = f"emp.{tag}@probetest.example"
PW = "probe12345"

steps = []


def step(label, ok, extra=""):
    steps.append((label, bool(ok), extra))
    print(f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}")


def body(r, n=110):
    return f"{r.status_code} {r.text[:n]}"


# ── 1. signing up registers a company ──
r = c.post("/auth/ceo-signup", json={
    "full_name": "Probe Owner", "email": EMAIL, "company_name": NAME,
    "password": PW, "confirm_password": PW})
step("signup registers a company", r.status_code == 200 and r.json().get("company_id"),
     body(r))
if r.status_code != 200:
    raise SystemExit(1)
cid = r.json()["company_id"]
ceo_uid = r.json()["user_id"]
step("the company id is NOT the CEO's user id", cid != ceo_uid,
     f"company={cid}  ceo_user={ceo_uid}")

# ── 2. the name is claimed ──
r = c.post("/auth/ceo-signup", json={
    "full_name": "Impostor", "email": f"x.{tag}@probetest.example",
    "company_name": NAME.lower(), "password": PW, "confirm_password": PW})
step("the same name cannot be claimed twice (any casing)",
     r.status_code == 400, body(r, 80))

# ── 3. no sign-in before approval ──
r = c.post("/auth/login", json={"email": EMAIL, "password": PW})
step("cannot sign in before the admin approves", r.status_code == 403,
     str(r.status_code))

# ── 4. the superadmin approves ──
with open_unscoped_session("e2e: superadmin token") as db:
    sa = db.query(User).filter(User.role == "superadmin").first()
sa_hdr = {"Authorization": "Bearer " + create_access_token(
    {"user_id": sa.id, "role": "superadmin", "email": sa.email,
     "company_id": None})}
r = c.put(f"/admin/approve-ceo/{ceo_uid}", headers=sa_hdr)
step("approving the CEO activates the company", r.status_code == 200, body(r))

# ── 5. login now works and carries the company ──
r = c.post("/auth/login", json={"email": EMAIL, "password": PW})
step("login works and returns the company id",
     r.status_code == 200 and r.json().get("company_id") == cid, body(r, 90))
hdr = {"Authorization": "Bearer " + r.json()["access_token"]}

# ── 6. the work — every one of these used `ceo.id` as a company ──
r = c.post("/ceo/create-employee", headers=hdr, json={
    "full_name": "Probe Worker", "email": EMP_EMAIL, "phone": "0300",
    "department": "Engineering", "designation": "Backend Developer",
    "joining_date": "2026-01-10", "password": "worker12345"})
step("create an employee", r.status_code == 200, body(r))
emp_id = r.json().get("employee_id")

r = c.post("/settings/work-policy", headers=hdr, json={
    "working_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    "shift_start": "09:00", "shift_end": "18:00", "late_tolerance_mins": 15,
    "min_daily_hours": 8.0, "overtime_threshold": 9.0})
step("save the work policy", r.status_code == 200, body(r))

r = c.get("/settings/work-policy", headers=hdr)
step("read the work policy back", r.status_code == 200 and "09:00" in r.text,
     body(r, 70))

r = c.post("/settings/office-location", headers=hdr, json={
    "office_name": "HQ", "latitude": 24.86, "longitude": 67.00,
    "radius_meters": 200})
step("save the office location", r.status_code == 200, body(r, 70))

r = c.get("/leave/types", headers=hdr)
n_types = len(r.json().get("types", [])) if r.status_code == 200 else 0
step("the new company has its own leave types", n_types > 0,
     f"{r.status_code}  {n_types} types")

r = c.post("/payroll/salary-structure", headers=hdr, json={
    "employee_id": emp_id, "base_salary": 100000, "house_allowance": 0,
    "transport_allowance": 0, "medical_allowance": 0, "other_allowances": 0})
step("set a salary structure", r.status_code == 200, body(r))

r = c.get("/payroll/salary-structures", headers=hdr)
step("read the salary structure back",
     r.status_code == 200 and str(emp_id) in r.text, body(r, 70))

r = c.post("/payroll/policy", headers=hdr, json={
    "overtime_multiplier": 1.5, "late_deduction_policy": "pro_rata",
    "late_deduction_amount": 0, "undertime_deduction": "pro_rata",
    "unpaid_leave_deduction": "pro_rata", "absent_deduction": "per_day",
    "tax_percentage": 5, "tax_threshold": 100000,
    "provident_fund_percent": 5})
step("set the payroll rules", r.status_code == 200, body(r))

r = c.get("/attendance/flags/today", headers=hdr)
step("the CEO attendance dashboard loads", r.status_code == 200, body(r, 70))

r = c.get("/hr/overview", headers=hdr)
step("the HR console overview loads", r.status_code == 200, str(r.status_code))

r = c.get("/chat/requests", headers=hdr)
step("the CEO requests inbox loads", r.status_code == 200, str(r.status_code))

# ── 7. the employee's own side ──
r = c.post("/auth/login", json={"email": EMP_EMAIL, "password": "worker12345"})
step("the new employee can sign in", r.status_code == 200, body(r, 70))
ehdr = {"Authorization": "Bearer " + r.json()["access_token"]}
for url in [f"/attendance/today/{emp_id}", "/attendance/my-office",
            f"/leave/balance/{emp_id}", f"/leave/history/{emp_id}",
            "/payroll/slips", "/leave/types", "/payroll/policy"]:
    r = c.get(url, headers=ehdr)
    step(f"employee: {url}", r.status_code == 200, body(r, 60))

# ── 8. and none of it reaches another company ──
with open_unscoped_session("e2e: another company's CEO") as db:
    other_ceo = db.query(User).filter(
        User.company_id == 19, User.role == "ceo").first()
ohdr = {"Authorization": "Bearer " + create_access_token(
    {"user_id": other_ceo.id, "role": "ceo", "email": other_ceo.email,
     "company_id": 19})}

r = c.get("/recruitment/jobs", headers=ohdr)
step("another company's job list does not contain the new company's",
     NAME not in r.text, str(r.status_code))
r = c.get(f"/payroll/salary-structure/{emp_id}", headers=ohdr)
step("another company cannot read the new salary",
     r.status_code in (403, 404), str(r.status_code))
r = c.get(f"/attendance/today/{emp_id}", headers=ohdr)
step("another company cannot read the new employee's attendance",
     r.status_code in (403, 404), str(r.status_code))

bad = [s for s in steps if not s[1]]
print("\n" + "=" * 64)
print(f"  {len(steps) - len(bad)}/{len(steps)} passed"
      + (f"   ** {len(bad)} FAILED **" if bad
         else "   — a brand new company works end to end"))
for label, _, extra in bad:
    print(f"   - {label}   {extra}")
print(f"\n  left behind: company {cid} {NAME!r} "
      f"(remove it with: py _cleanup_probe.py)")
