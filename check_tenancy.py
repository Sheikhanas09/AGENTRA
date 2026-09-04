"""
Is one company's data reachable from another?
─────────────────────────────────────────────
    py check_tenancy.py           run everything
    py check_tenancy.py --show    print the responses too

Eleven sections. The last three do not read the code — they log in as one
company and try to take another's data over HTTP, because a review of
the source can only ever show what somebody meant.

That distinction was earned during this work. Every recruitment table
had `company_id` added, the migration filled every row correctly, and
the SQLAlchemy models were not updated to declare the column. The
database was right, the data was right, the code read as though it were
working — and the ORM could not see the column, so the guard silently
skipped all six tables. The live probe is what noticed.

Exit code 1 if anything fails, so it can be a CI step.
"""

import re
import sys
import pathlib
import warnings

warnings.filterwarnings("ignore")

from sqlalchemy import text                                    # noqa: E402

# ⚠ THE MODELS MUST BE IMPORTED BEFORE ANYTHING ASKS WHICH ARE PROTECTED.
# `tenant_classes()` reads SQLAlchemy's mapper registry, and a class that
# has not been imported is not in it. Without this the first run reported
# every table as "in the database but on no model" — which is exactly the
# failure section 1 exists to catch, arriving as a false alarm.
#
# ⚠ AND IT IS A SCAN, NOT A LIST. This was six hand-written imports, and
# the day `models/integration.py` was added the checker reported the new
# table as unprotected — because the CHECKER had not been updated, not
# because anything was wrong. A checker that cries wolf about its own
# maintenance is one people learn to ignore, so it finds them itself.
import importlib               # noqa: E402
import pkgutil                 # noqa: E402

import app.models              # noqa: E402

for _m in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f"app.models.{_m.name}")

SHOW = "--show" in sys.argv

_fails = []
_warns = []


def ok(msg):
    print(f"   [ok]   {msg}")


def fail(msg):
    _fails.append(msg)
    print(f"   [FAIL] {msg}")


def warn(msg):
    _warns.append(msg)
    print(f"   [warn] {msg}")


def head(n, title):
    print(f"\n{'=' * 70}\n {n}. {title}\n{'=' * 70}")


# ══════════════════════════════════════════════
# 1. The tenant column exists everywhere it should
# ══════════════════════════════════════════════
def section_schema():
    head(1, "Schema — every tenant table keyed to a real company")
    from app.database import engine
    from app.utils.tenant_guard import tenant_classes, TENANT_COLUMN

    classes = tenant_classes()
    print(f"   {len(classes)} models declare `{TENANT_COLUMN}`")

    with engine.connect() as c:
        db_tables = {r[0] for r in c.execute(text("""
            SELECT table_name FROM information_schema.columns
             WHERE table_schema = 'public' AND column_name = :col
        """), {"col": TENANT_COLUMN})}

        model_tables = {cls.__tablename__ for cls in classes}

        # ═══ THE FAILURE THIS SECTION EXISTS FOR ═══
        # A column present in Postgres but absent from the model is
        # invisible to the ORM guard. Everything looks correct from
        # either side on its own.
        only_db = db_tables - model_tables
        only_model = model_tables - db_tables
        if only_db:
            fail(f"in the database but not on any model (the guard cannot "
                 f"protect these): {sorted(only_db)}")
        if only_model:
            fail(f"on a model but not in the database: {sorted(only_model)}")
        if not only_db and not only_model:
            ok(f"all {len(db_tables)} tables agree between models and database")

        # Foreign keys — a company_id that points nowhere is not a scope
        missing_fk = []
        for t in sorted(db_tables):
            # `:t::regclass` reads as a bind parameter followed by a cast
            # to SQLAlchemy and breaks; CAST(...) is unambiguous.
            has = c.execute(text("""
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'
                   AND confrelid = CAST('companies' AS regclass)
            """), {"t": t}).first()
            if not has:
                missing_fk.append(t)
        if missing_fk:
            fail(f"no foreign key to companies: {missing_fk}")
        else:
            ok("every tenant table has a foreign key to `companies`")

        idx = c.execute(text("""
            SELECT COUNT(*) FROM pg_indexes
             WHERE schemaname='public' AND indexdef ILIKE '%company_id%'
        """)).scalar()
        ok(f"{idx} indexes cover company_id "
           f"(the filter is on every query now, so it has to be indexed)")


# ══════════════════════════════════════════════
# 2. The data itself
# ══════════════════════════════════════════════
def section_data():
    head(2, "Data — nobody and nothing is stranded")
    from app.utils.tenancy import open_unscoped_session
    from app.utils.tenant_guard import tenant_classes

    with open_unscoped_session("check_tenancy: auditing every company") as db:
        q = lambda s, **k: db.execute(text(s), k).fetchall()   # noqa: E731

        companies = q("SELECT id, name, slug, status FROM companies ORDER BY id")
        print(f"   {len(companies)} companies")
        for c in companies:
            n = q("SELECT COUNT(*) FROM users WHERE company_id=:c", c=c.id)[0][0]
            print(f"      {c.id:5}  {c.name:<20} {c.status:<10} {n} users")

        dupes = q("SELECT slug, COUNT(*) FROM companies GROUP BY slug "
                  "HAVING COUNT(*) > 1")
        if dupes:
            fail(f"two companies share a name: {dupes}")
        else:
            ok("no two companies share a name (the merge that used to be "
               "possible cannot happen)")

        stray = q("""SELECT id, full_name, role FROM users
                      WHERE company_id IS NULL AND role <> 'superadmin'""")
        if stray:
            fail(f"users with no company (they cannot sign in): {stray}")
        else:
            ok("every CEO and employee belongs to a company")

        sa = q("SELECT COUNT(*) FROM users WHERE role='superadmin' "
               "AND company_id IS NOT NULL")[0][0]
        if sa:
            fail(f"{sa} superadmin(s) are inside a company — they must not be")
        else:
            ok("the superadmin belongs to no company, by design")

        orphans = []
        for cls in tenant_classes():
            t = cls.__tablename__
            n = q(f"""SELECT COUNT(*) FROM {t} x
                       WHERE x.company_id IS NOT NULL
                         AND NOT EXISTS (SELECT 1 FROM companies c
                                          WHERE c.id = x.company_id)""")[0][0]
            if n:
                orphans.append(f"{t}={n}")
        if orphans:
            fail(f"rows pointing at a company that does not exist: {orphans}")
        else:
            ok("no row points at a missing company")

        nulls = []
        for cls in tenant_classes():
            t = cls.__tablename__
            if t == "users":
                continue
            n = q(f"SELECT COUNT(*) FROM {t} WHERE company_id IS NULL")[0][0]
            if n:
                nulls.append(f"{t}={n}")
        if nulls:
            warn(f"rows with no company — visible to nobody, which is safe, "
                 f"but they are also unreachable: {nulls}")
        else:
            ok("no tenant row is without a company")


# ══════════════════════════════════════════════
# 3. The guard is on, and it refuses by default
# ══════════════════════════════════════════════
def section_guard():
    head(3, "The ORM guard — a query cannot forget the company")
    from app.database import SessionLocal
    from app.models.payroll import Payslip
    from app.utils.tenancy import open_tenant_session
    from app.utils.tenant_guard import TenantScopeError, tenant_classes

    ok(f"{len(tenant_classes())} models are protected, discovered from the "
       f"column — there is no hand-written list to fall out of date")

    # An unscoped session must REFUSE, not quietly return everything.
    db = SessionLocal()
    try:
        db.query(Payslip).first()
        fail("an unscoped session returned tenant data instead of refusing")
    except TenantScopeError:
        ok("an unscoped session raises TenantScopeError "
           "(a route that forgets `Depends(get_tenant)` fails loudly)")
    except Exception as e:
        # With RLS on and no setting, Postgres returns nothing rather than
        # raising — also safe, but the guard should have caught it first.
        fail(f"unscoped session raised something unexpected: {type(e).__name__}")
    finally:
        db.close()

    # A scoped session must see only its own rows.
    with open_unscoped_probe() as db:
        ids = [r[0] for r in db.execute(text(
            "SELECT id FROM companies ORDER BY id")).fetchall()]

    counts = {}
    for cid in ids:
        with open_tenant_session(cid) as db:
            counts[cid] = db.query(Payslip).count()
    total_seen = sum(counts.values())
    with open_unscoped_probe() as db:
        total = db.execute(text("SELECT COUNT(*) FROM payslips")).scalar()
    print(f"   payslips per company: {counts}   (all companies: {total})")
    if total_seen == total:
        ok("the per-company counts add up to the whole table — no row is "
           "visible twice, and none is lost")
    else:
        fail(f"per-company payslips sum to {total_seen}, table holds {total}")

    # Writing into another company must be refused.
    from app.models.chat import HrNudge
    from datetime import datetime
    if len(ids) >= 2:
        a, b = ids[0], ids[-1]
        with open_tenant_session(a) as db:
            db.add(HrNudge(company_id=b, kind="probe", ref="x",
                           sent_at=datetime.utcnow()))
            try:
                db.flush()
                fail(f"a session scoped to company {a} created a row for {b}")
            except TenantScopeError:
                ok("creating a row for another company is refused before it "
                   "reaches the database")
            except Exception as e:
                ok(f"creating a row for another company is refused "
                   f"({type(e).__name__} — the database policy caught it)")
            finally:
                db.rollback()


def open_unscoped_probe():
    from app.utils.tenancy import open_unscoped_session
    return open_unscoped_session("check_tenancy: counting across companies")


# ══════════════════════════════════════════════
# 4. Every way of crossing companies, listed
# ══════════════════════════════════════════════
def section_exceptions():
    head(4, "The written-down exceptions")
    root = pathlib.Path("app")
    pat_scope = re.compile(r'bind_unscoped\(\s*\w+\s*,\s*["\']([^"\']+)')
    pat_stmt = re.compile(r'tenant_bypass\s*=\s*(?:["\']([^"\']+)|(\w+))')
    pat_open = re.compile(r'open_unscoped_session\(\s*["\']([^"\']+)')

    found = []
    for p in sorted(root.rglob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in pat_scope.finditer(src):
            found.append((str(p), "session", m.group(1)))
        for m in pat_open.finditer(src):
            found.append((str(p), "session", m.group(1)))
        for m in pat_stmt.finditer(src):
            found.append((str(p), "statement", m.group(1) or m.group(2)))

    # Definitions of the helpers themselves are not uses of them.
    found = [f for f in found if "tenant_guard.py" not in f[0]]

    print(f"   {len(found)} places may read across companies:")
    for path, kind, reason in found:
        print(f"      {kind:9} {path:34} {reason}")

    if len(found) > 15:
        warn(f"{len(found)} exceptions is a lot — each one is a place the "
             f"tenant filter does not apply")
    else:
        ok("the set is small enough to review by eye")


# ══════════════════════════════════════════════
# 4b. A user id is not a company id
# ══════════════════════════════════════════════
def section_id_confusion():
    head("4b", "A user id and a company id are not the same number")
    # ═══ WHY THIS SECTION EXISTS ═══
    # For every company that existed before this change, `company_id`
    # WAS the CEO's user id. Roughly ninety places said `ceo.id` and
    # meant the company; eleven said it and meant the person. Converting
    # them is a judgement call per line, and a sweep got eleven wrong —
    # writing `set_by = 1003` (a company) into a column that is a
    # foreign key to `users.id`.
    #
    # Postgres refused, because this migration added that foreign key.
    # It would otherwise have been stored, pointed at nothing, and shown
    # up months later as "who approved this payroll? nobody".
    #
    # This checks both directions so the confusion cannot come back.
    from app.database import Base

    user_cols = set()
    for m in Base.registry.mappers:
        for col in m.columns:
            for fk in col.foreign_keys:
                if fk.column.table.name == "users":
                    user_cols.add(col.name)
    user_cols.discard("company_id")

    into_user = re.compile(
        r"\b(" + "|".join(sorted(user_cols)) + r")\s*=\s*"
        r"(ceo\.company_id|tenant\.company_id|"
        r"current_user\[[\"']company_id[\"']\])")
    into_company = re.compile(
        r"(?<![.\w])company_id\s*=\s*"
        r"(ceo\.id|user\.id|employee\.id|u\.id|"
        r"current_user\[[\"']user_id[\"']\]|tenant\.user_id)\b")

    bad = []
    for p in sorted(pathlib.Path("app").rglob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        for i, ln in _code_lines(src):
            if into_user.search(ln):
                bad.append(f"a COMPANY id into a USER column — {p}:{i}  "
                           f"{ln.strip()[:60]}")
            if into_company.search(ln) and "==" not in ln:
                bad.append(f"a USER id into company_id — {p}:{i}  "
                           f"{ln.strip()[:60]}")
    if bad:
        for b in bad:
            fail(b)
    else:
        ok(f"no confusion between the two in any assignment "
           f"({len(user_cols)} user-id columns checked, comments and "
           f"docstrings excluded)")


def _code_lines(src):
    """
    (line number, text) for the lines that are actually CODE.

    ⚠ COMMENTS AND DOCSTRINGS HAVE TO BE EXCLUDED, AND `startswith("#")`
    IS NOT ENOUGH. This project documents a fix by quoting the broken
    code above it, so the docstrings are full of lines like
    `company_id = ceo.id` that are being shown as the thing that WAS
    wrong. A scan that reads them reports the explanation as the bug —
    which is worse than not scanning, because it trains everyone to
    ignore the check.

    `tokenize` knows the difference; a regex over raw text cannot.
    """
    import io
    import tokenize

    skip = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                for n in range(tok.start[0], tok.end[0] + 1):
                    skip.add(n)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # An unparseable file is not this check's problem to report.
        return []

    return [(i, ln) for i, ln in enumerate(src.splitlines(), 1)
            if i not in skip]


# ══════════════════════════════════════════════
# 4c. Every route says what it is allowed to see
# ══════════════════════════════════════════════
def section_route_coverage():
    head("4c", "Every route declares a scope")
    # A route with only `Depends(get_current_user)` has proved WHO is
    # asking and nothing about WHICH COMPANY, so its session is never
    # stamped and the guard refuses its first query. That is safe — it
    # is a 500, not a leak — but it is also a broken screen, and it
    # should be found here rather than by a user.
    #
    # Two were found this way after the sweep looked finished:
    # `/recruitment/my-interviews` and the interview feedback POST.
    from app.main import app
    from app.utils.tenancy import (
        get_tenant, require_ceo, require_employee, require_superadmin,
        public_scope, auth_scope,
    )

    scopers = {get_tenant, require_ceo, require_employee,
               require_superadmin, public_scope, auth_scope}
    # Routes that touch no tenant data at all.
    exempt = {"/", "/scheduler/status"}

    def dependencies_of(route):
        found = set()

        def walk(dep):
            for sub in dep.dependencies:
                if sub.call:
                    found.add(sub.call)
                walk(sub)

        if hasattr(route, "dependant"):
            walk(route.dependant)
        return found

    missing = []
    total = 0
    for r in app.routes:
        path = getattr(r, "path", None)
        if not path or path.startswith(("/openapi", "/docs", "/redoc")):
            continue
        if path in exempt:
            continue
        total += 1
        if not (dependencies_of(r) & scopers):
            methods = ",".join(sorted(getattr(r, "methods", []) or []))
            missing.append(f"{methods} {path}")

    if missing:
        for m in sorted(missing):
            fail(f"no tenant scope on {m} — its first query will raise")
    else:
        ok(f"all {total} routes declare a scope "
           f"(a tenant, the superadmin, public, or auth)")


# ══════════════════════════════════════════════
# 5. Nothing goes around the ORM
# ══════════════════════════════════════════════
def section_raw_sql():
    head(5, "No raw SQL in the application")
    # The ORM guard's whole claim to completeness rests on this. Raw SQL
    # is not wrong — but it is not covered by the guard, so it must be a
    # deliberate, visible decision rather than something that drifts in.
    root = pathlib.Path("app")
    hits = []
    pat = re.compile(r"\.execute\(\s*text\(|\.exec_driver_sql\(|\.executemany\(")
    for p in sorted(root.rglob("*.py")):
        if p.name == "tenant_guard.py":
            continue        # it issues the SET LOCAL the policies read
        for i, ln in enumerate(p.read_text(encoding="utf-8",
                                           errors="replace").splitlines(), 1):
            if pat.search(ln):
                hits.append(f"{p}:{i}  {ln.strip()[:70]}")
    if hits:
        warn(f"{len(hits)} raw SQL statement(s) — the ORM guard does not "
             f"cover these; row-level security still does:")
        for h in hits:
            print(f"      {h}")
    else:
        ok("no raw SQL — every query goes through the ORM, which is what "
           "makes the guard's coverage complete rather than partial")


# ══════════════════════════════════════════════
# 6. The database's own wall
# ══════════════════════════════════════════════
def section_rls():
    head(6, "Row-level security — the wall that does not care how the "
            "query was written")
    from app.database import engine
    from app.utils.tenant_guard import TENANT_COLUMN

    with engine.connect() as c:
        who = c.execute(text(
            "SELECT current_user, "
            "(SELECT usesuper FROM pg_user WHERE usename=current_user), "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user)"
        )).first()
        role, is_super, bypass = who

        tables = [r[0] for r in c.execute(text("""
            SELECT c.table_name FROM information_schema.columns c
              JOIN information_schema.tables t
                ON t.table_name=c.table_name AND t.table_schema=c.table_schema
             WHERE c.table_schema='public' AND c.column_name=:col
               AND t.table_type='BASE TABLE'
        """), {"col": TENANT_COLUMN})]

        unprotected = []
        for t in tables:
            r = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname=:t"), {"t": t}).first()
            pol = c.execute(text(
                "SELECT COUNT(*) FROM pg_policies WHERE tablename=:t"),
                {"t": t}).scalar()
            if not (r and r[0] and r[1] and pol):
                unprotected.append(t)

        if unprotected:
            fail(f"no row-level security on: {unprotected}  "
                 f"(run: py migrate_rls.py --apply)")
        else:
            ok(f"all {len(tables)} tables have RLS enabled, FORCEd and a policy")

        # ═══ THE CHECK THAT MATTERS ═══
        # Postgres exempts superusers from RLS entirely. Connecting as
        # `postgres` leaves every policy in place, listed by every
        # inspection query, doing nothing at all — a wall everybody
        # believes in. So this asks whether the CONNECTED ROLE is
        # actually subject to them.
        if is_super or bypass:
            fail(f"connected as `{role}`, which "
                 f"{'is a superuser' if is_super else 'has BYPASSRLS'} — "
                 f"the policies above are NOT in effect. Point DATABASE_URL "
                 f"at the `agentra_app` role.")
        else:
            ok(f"connected as `{role}` — not a superuser, no BYPASSRLS, so "
               f"the policies actually apply")

    # Prove it rather than assert it: go around the ORM entirely.
    if not (is_super or bypass):
        with engine.connect() as c:
            n_unset = c.execute(text("SELECT COUNT(*) FROM payslips")).scalar()
            c.execute(text("SET \"agentra.company_id\" = '-1'"))
            n_none = c.execute(text("SELECT COUNT(*) FROM payslips")).scalar()
        if n_unset == 0 and n_none == 0:
            ok("raw SQL with no company announced returns 0 rows — the "
               "database refuses on its own, with the ORM out of the picture")
        else:
            fail(f"raw SQL returned {n_unset}/{n_none} rows without a company")


# ══════════════════════════════════════════════
# 7-9. Over HTTP, as one company, against another
# ══════════════════════════════════════════════
_temp_companies = []


def _ensure_two_companies():
    """
    The live sections need two live companies to have any meaning.

    ═══ A CHECK THAT SKIPS ITSELF IS A CHECK THAT PASSES ═══
    A first version printed "fewer than two active companies — nothing
    to probe" and carried on to ALL CHECKS PASSED. Every one of the
    strongest sections had been skipped and the summary line did not say
    so in a way anybody would notice. On a fresh install, or after the
    test companies are cleaned up, that is the normal state — so the
    check would reliably be at its weakest exactly when somebody was
    most likely to be relying on it.

    It builds what it needs instead, through the real signup and
    approval routes (so the setup is itself a test of them), and removes
    them in `_drop_temp_companies` whatever happens afterwards.
    """
    import random
    import string
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.user import User
    from app.utils.tenancy import open_unscoped_session

    client = TestClient(app, raise_server_exceptions=False)
    with open_unscoped_session("check_tenancy: superadmin") as db:
        sa = db.query(User).filter(User.role == "superadmin").first()
    if not sa:
        return
    sa_hdr = {"Authorization": "Bearer " + _mint(sa.id, "superadmin",
                                                 sa.email, None)}

    for _ in range(2):
        tag = "".join(random.choices(string.ascii_lowercase, k=7))
        name = f"Checkfixture {tag.upper()}"
        email = f"ceo.{tag}@checkfixture.example"
        pw = "checkfixture12345"
        r = client.post("/auth/ceo-signup", json={
            "full_name": "Fixture Owner", "email": email,
            "company_name": name, "password": pw, "confirm_password": pw})
        if r.status_code != 200:
            warn(f"could not create a probe company: {r.status_code} "
                 f"{r.text[:90]}")
            return
        cid, uid = r.json()["company_id"], r.json()["user_id"]
        client.put(f"/admin/approve-ceo/{uid}", headers=sa_hdr)

        login = client.post("/auth/login", json={"email": email,
                                                 "password": pw})
        hdr = {"Authorization": "Bearer " + login.json()["access_token"]}
        client.post("/ceo/create-employee", headers=hdr, json={
            "full_name": f"Fixture Worker {tag.upper()}",
            "email": f"emp.{tag}@checkfixture.example", "phone": "0300",
            "department": "Engineering", "designation": "Backend Developer",
            "joining_date": "2026-01-10", "password": "worker12345"})
        _temp_companies.append(cid)

    print(f"   (built {len(_temp_companies)} temporary companies for the "
          f"live sections: {_temp_companies})")


def _drop_temp_companies():
    """Remove whatever `_ensure_two_companies` built, always."""
    if not _temp_companies:
        return
    from sqlalchemy import text as _t
    from app.database import admin_engine

    order = [
        "loan_repayments", "payroll_adjustments", "payslips", "payroll_runs",
        "employee_loans", "salary_structures", "payroll_policy",
        "company_branding", "policy_decisions_log", "leave_documents",
        "leave_requests", "leave_balances", "company_leave_types",
        "attendance_intervals", "attendance_photos", "attendance_sessions",
        "face_enrollment", "office_locations", "company_policy_overrides",
        "company_policies", "company_work_policy", "chat_messages",
        "chat_sessions", "hr_nudges", "hr_cases", "hr_requests",
        "hr_settings", "employment_records", "interview_feedback",
        "final_scores", "interviews", "applications", "candidates", "jobs",
        "users",
    ]
    try:
        with admin_engine().begin() as conn:
            for cid in _temp_companies:
                for t in order:
                    conn.execute(_t(f"DELETE FROM {t} WHERE company_id = :c"),
                                 {"c": cid})
                conn.execute(_t("DELETE FROM companies WHERE id = :c"),
                             {"c": cid})
        print(f"\n   (removed the temporary companies: {_temp_companies})")
    except Exception as e:                                      # noqa: BLE001
        warn(f"could not remove the temporary companies {_temp_companies}: "
             f"{e} — remove them with py _cleanup_probe.py")


def _mint(user_id, role, email, company_id):
    from app.utils.security import create_access_token
    return create_access_token({"user_id": user_id, "role": role,
                                "email": email, "company_id": company_id})


def _fixtures():
    from app.models.company import Company, STATUS_ACTIVE
    from app.models.user import User
    from app.utils.tenancy import open_unscoped_session

    with open_unscoped_session("check_tenancy: finding two companies") as db:
        rows = db.query(Company).filter(
            Company.status == STATUS_ACTIVE).order_by(Company.id).all()
        out = []
        for c in rows:
            ceo = db.query(User).filter(
                User.company_id == c.id, User.role == "ceo").first()
            emp = db.query(User).filter(
                User.company_id == c.id, User.role == "employee").first()
            if ceo:
                out.append((c.id, c.name, ceo.id, ceo.role, ceo.email,
                            emp.id if emp else None,
                            emp.role if emp else None,
                            emp.email if emp else None))
        return out


def _token(user_id, role, email, company_id):
    from app.utils.security import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": user_id, "role": role, "email": email,
         "company_id": company_id})}


def section_cross_tenant():
    head(7, "Live — one company's CEO reaching for another's rows")
    from fastapi.testclient import TestClient
    from app.main import app
    from app.utils.tenancy import open_unscoped_session

    fx = _fixtures()
    if len(fx) < 2:
        _ensure_two_companies()
        fx = _fixtures()
    if len(fx) < 2:
        fail("could not obtain two live companies, so the cross-tenant "
             "probe did not run — this check has NOT passed")
        return

    # The prober should be the company with LESS data, the target the one
    # with more, so a leak is unmistakable.
    from app.models.recruitment import Job
    from app.models.payroll import Payslip

    def weight(cid):
        with open_unscoped_session("check_tenancy: sizing companies") as db:
            return (db.query(Job).filter(Job.company_id == cid).count()
                    + db.query(Payslip).filter(Payslip.company_id == cid).count())

    fx.sort(key=lambda r: weight(r[0]))
    A, B = fx[0], fx[-1]
    print(f"   attacker: {A[1]} (company {A[0]})")
    print(f"   target:   {B[1]} (company {B[0]})\n")

    from app.models.attendance import LeaveRequest, CompanyPolicy
    from app.models.recruitment import Application
    from app.models.chat import ChatSession, HrRequest
    from app.models.payroll import PayrollRun

    with open_unscoped_session("check_tenancy: target row ids") as db:
        def first_id(model):
            r = db.query(model).filter(model.company_id == B[0]).first()
            return r.id if r else None
        ids = {
            "job": first_id(Job), "app": first_id(Application),
            "leave": first_id(LeaveRequest), "slip": first_id(Payslip),
            "chat": first_id(ChatSession), "req": first_id(HrRequest),
            "policy": first_id(CompanyPolicy), "run": first_id(PayrollRun),
        }
    emp_b = B[5]

    probes = [
        ("GET", f"/recruitment/jobs/{ids['job']}"),
        ("DELETE", f"/recruitment/jobs/{ids['job']}"),
        ("GET", f"/recruitment/applications/{ids['job']}"),
        ("GET", f"/recruitment/ranked-candidates/{ids['job']}"),
        ("GET", f"/recruitment/download-cv/{ids['app']}"),
        ("PUT", f"/recruitment/shortlist/{ids['app']}"),
        ("POST", f"/recruitment/reject/{ids['app']}"),
        ("GET", f"/leave/balance/{emp_b}"),
        ("GET", f"/leave/history/{emp_b}"),
        ("GET", f"/leave/certificate/{ids['leave']}"),
        ("POST", f"/leave/approve/{ids['leave']}"),
        ("POST", f"/leave/cancel/{ids['leave']}"),
        ("GET", f"/payroll/slip/{ids['slip']}"),
        ("GET", f"/payroll/slip/{ids['slip']}/download"),
        ("GET", f"/payroll/salary-structure/{emp_b}"),
        ("GET", f"/payroll/run/{ids['run']}"),
        ("POST", f"/payroll/run/{ids['run']}/approve"),
        ("GET", f"/attendance/today/{emp_b}"),
        ("GET", f"/attendance/history/{emp_b}"),
        ("GET", f"/attendance/summary/{emp_b}/2026/8"),
        ("GET", f"/attendance/enrollment-photo/{emp_b}"),
        ("GET", f"/attendance/report/{emp_b}/2026-08-19"),
        ("GET", f"/chat/session/{ids['chat']}"),
        ("DELETE", f"/chat/session/{ids['chat']}"),
        ("GET", f"/chat/letter/{ids['req']}"),
        ("GET", f"/hr/session/{ids['chat']}"),
        ("GET", f"/settings/policy/status/{ids['policy']}"),
        ("POST", f"/settings/policy/{ids['policy']}/activate"),
        ("DELETE", f"/settings/policy/{ids['policy']}"),
    ]

    client = TestClient(app, raise_server_exceptions=False)
    hdr = _token(A[2], A[3], A[4], A[0])

    leaks = skipped = 0
    for method, url in probes:
        if "None" in url:
            skipped += 1
            continue
        r = client.request(method, url, headers=hdr)
        leaked = r.status_code == 200 and len(r.content) > 2
        if leaked:
            leaks += 1
            fail(f"{method} {url} -> 200 ({len(r.content)} bytes)")
            if SHOW:
                print(f"          {r.text[:200]}")
        else:
            print(f"   [ok]   {method:6} {url:46} {r.status_code}")
    if skipped:
        print(f"   ({skipped} probes skipped — the target has no such row)")
    if not leaks:
        ok(f"{len(probes) - skipped} cross-tenant attempts, none returned data")


def section_lists():
    head(8, "Live — the list screens show only the caller's company")
    from fastapi.testclient import TestClient
    from app.main import app

    fx = _fixtures()
    if len(fx) < 2:
        fail("fewer than two live companies — the list-isolation check "
             "did not run")
        return
    A, B = fx[0], fx[-1]
    client = TestClient(app, raise_server_exceptions=False)
    ha, hb = _token(*A[2:5], A[0]), _token(*B[2:5], B[0])

    urls = [
        "/ceo/employees", "/recruitment/jobs", "/recruitment/employees",
        "/recruitment/all-employees", "/recruitment/interviews",
        "/leave/all", "/leave/pending", "/leave/calendar", "/leave/types",
        "/payroll/runs", "/payroll/salary-structures",
        "/attendance/flags/today", "/chat/requests", "/hr/overview",
        "/hr/former", "/hr/sessions", "/settings/policy/list",
        "/settings/work-policy",
    ]
    bad = 0
    for u in urls:
        ra, rb = client.get(u, headers=ha), client.get(u, headers=hb)
        # Identical non-trivial payloads for two different companies means
        # somebody is being shown data that is not theirs.
        if ra.status_code == 200 and ra.text == rb.text and len(ra.text) > 60:
            bad += 1
            fail(f"{u}: both companies received an identical {len(ra.text)}-byte "
                 f"response")
        else:
            print(f"   [ok]   {u:34} A:{ra.status_code} {len(ra.content):6}b   "
                  f"B:{rb.status_code} {len(rb.content):6}b")
    if not bad:
        ok("every list differs between the two companies")


def section_lifecycle():
    head(9, "Live — a suspended company locks out everybody in it")
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.company import Company, STATUS_ACTIVE, STATUS_SUSPENDED
    from app.models.user import User
    from app.utils.tenancy import open_unscoped_session

    fx = _fixtures()
    if not fx:
        warn("no active company to test with")
        return
    target = fx[-1]
    cid, name, ceo_id, ceo_role, ceo_email, emp_id, emp_role, emp_email = target
    if not emp_id:
        warn(f"{name} has no employee — the employee half is not tested")

    client = TestClient(app, raise_server_exceptions=False)
    hdr_ceo = _token(ceo_id, ceo_role, ceo_email, cid)
    hdr_emp = _token(emp_id, emp_role, emp_email, cid) if emp_id else None

    before = client.get("/ceo/employees", headers=hdr_ceo).status_code

    with open_unscoped_session("check_tenancy: suspending a company") as db:
        c = db.query(Company).filter(Company.id == cid).first()
        was = c.status
        c.status = STATUS_SUSPENDED
        c.suspended_reason = "check_tenancy probe"
        db.commit()
    try:
        r_ceo = client.get("/ceo/employees", headers=hdr_ceo)
        r_emp = (client.get(f"/attendance/today/{emp_id}", headers=hdr_emp)
                 if hdr_emp else None)

        # An already-issued token must stop working: suspension that only
        # blocks the next login means "suspended tomorrow".
        if before == 200 and r_ceo.status_code == 403:
            ok("the CEO's existing token stops working the moment the "
               "company is suspended")
        else:
            fail(f"CEO was {before} before suspension and "
                 f"{r_ceo.status_code} after — expected 200 then 403")

        if r_emp is not None:
            if r_emp.status_code == 403:
                ok("an EMPLOYEE of the suspended company is locked out too "
                   "(this used to be the gap: only the CEO was blocked)")
            else:
                fail(f"employee still reached the system: {r_emp.status_code}")
    finally:
        with open_unscoped_session("check_tenancy: restoring") as db:
            c = db.query(Company).filter(Company.id == cid).first()
            c.status = was
            c.suspended_reason = None
            db.commit()
        after = client.get("/ceo/employees", headers=hdr_ceo).status_code
        if after == before:
            ok(f"{name} restored to `{was}` — the probe left nothing behind")
        else:
            fail(f"{name} not restored cleanly ({after} != {before})")


def main():
    print("=" * 70)
    print("  TENANT ISOLATION")
    print("=" * 70)

    try:
        for fn in (section_schema, section_data, section_guard,
                   section_exceptions, section_id_confusion,
                   section_route_coverage, section_raw_sql, section_rls,
                   section_cross_tenant, section_lists, section_lifecycle):
            try:
                fn()
            except Exception as e:                              # noqa: BLE001
                import traceback
                fail(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
                if SHOW:
                    traceback.print_exc()
    finally:
        # Even on a crash. A checker that leaves half-built companies
        # behind makes the next run's numbers wrong.
        _drop_temp_companies()

    print("\n" + "=" * 70)
    if _fails:
        print(f"  {len(_fails)} FAILURE(S)")
        for f in _fails:
            print(f"   - {f}")
    if _warns:
        print(f"  {len(_warns)} warning(s)")
        for w in _warns:
            print(f"   - {w}")
    if not _fails:
        print("  ALL TENANT ISOLATION CHECKS PASSED")
    print("=" * 70)
    sys.exit(1 if _fails else 0)


if __name__ == "__main__":
    main()
