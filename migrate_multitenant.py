"""
Single tenant  ->  multi tenant
───────────────────────────────
Turns "company_id means the CEO's user id, and employees find it by
matching a string" into "the tenant is a row, and everything points at
it".

Run:  py migrate_multitenant.py            (shows what it WOULD do)
      py migrate_multitenant.py --apply    (does it)

Idempotent — safe to run again. Every step checks first.

═══════════════════════════════════════════════════════════
WHY NOT ONE ROW IS RENUMBERED
═══════════════════════════════════════════════════════════
Twenty tables already carry `company_id`, holding the CEO's user id.
The obvious migration — new sequence, then UPDATE all twenty — is the
kind that half-succeeds and leaves a database where leave belongs to one
company and payroll to another.

So `companies` is SEEDED WITH THE EXISTING CEO IDS. Every company_id
already stored stays correct, the foreign keys become valid immediately,
and there is nothing to roll back.

New companies come from the sequence starting at 1000, so a legacy id
(19) and a new one (1001) are told apart at a glance while this change
settles.

═══════════════════════════════════════════════════════════
WHERE IT REFUSES TO GUESS
═══════════════════════════════════════════════════════════
Two places can be genuinely ambiguous, and in both the script REPORTS
and leaves NULL rather than picking:

  · two CEOs sharing a company name — those two tenants are already
    merged in practice, and only a human knows which employee is whose
  · a candidate with no application, when more than one company has
    recruitment data

A NULL `company_id` is not a loose end. Under the tenancy layer and the
row-level security policies, a row with no company belongs to nobody and
is visible to nobody — which is the safe way to fail.
"""

import sys

from sqlalchemy import text

from sqlalchemy.orm import sessionmaker

from app.database import admin_engine
from app.models.company import (
    Company, normalise_name,
    STATUS_ACTIVE, STATUS_PENDING, STATUS_SUSPENDED,
)
from app.models.user import User  # noqa: F401  (FK target)
from app.utils.tenant_guard import bind_unscoped

# Migrations do DDL, which the application role deliberately cannot.
engine = admin_engine()
_Session = sessionmaker(bind=engine)


def SessionLocal():          # noqa: N802  (shadows the app's on purpose)
    """
    A privileged session for the migration, marked as crossing companies.

    Without the mark the tenant guard would refuse every query in this
    file — correctly, since a migration reads and writes rows belonging
    to all of them. Saying so is the point: it appears in the list
    `check_tenancy.py` prints, rather than being a quiet exception.
    """
    s = _Session()
    bind_unscoped(s, "migration: works across all companies")
    return s

APPLY = "--apply" in sys.argv

# Where new company ids begin. Above every existing user id on purpose —
# see the module docstring.
SEQUENCE_START = 1000

# The CEO's account status decides the company's starting status. The CEO
# approval flow already existed; this carries its meaning over rather
# than inventing a second switch that could disagree with it.
CEO_STATUS_TO_COMPANY = {
    "approved": STATUS_ACTIVE,
    "active": STATUS_ACTIVE,
    "pending": STATUS_PENDING,
    "inactive": STATUS_SUSPENDED,
    "rejected": STATUS_SUSPENDED,
    "fired": STATUS_SUSPENDED,
}

# Recruitment carried no tenant column at all. `jobs` knew its CEO; the
# five tables hanging off it knew nothing, so there was no way to scope
# them even if somebody had tried.
RECRUITMENT_COLUMNS = [
    "jobs", "candidates", "applications",
    "interviews", "interview_feedback", "final_scores",
]

_notes = []


def say(msg=""):
    print(msg)


def note(msg):
    _notes.append(msg)
    print(f"   !! {msg}")


# ══════════════════════════════════════════════
# Small helpers — every step checks before acting
# ══════════════════════════════════════════════
def table_exists(conn, table) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table}).first())


def column_exists(conn, table, column) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).first())


def constraint_exists(conn, name) -> bool:
    return bool(conn.execute(text(
        "SELECT 1 FROM pg_constraint WHERE conname = :n"
    ), {"n": name}).first())


def add_column(conn, table, column, ddl):
    if not table_exists(conn, table):
        note(f"table {table} does not exist — skipped")
        return False
    if column_exists(conn, table, column):
        return False
    say(f"   + {table}.{column}")
    if APPLY:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    return True


def add_fk(conn, table, column, name):
    if not column_exists(conn, table, column):
        return
    if constraint_exists(conn, name):
        return
    say(f"   + FK {name}  ({table}.{column} -> companies.id)")
    if APPLY:
        # RESTRICT, not CASCADE. Deleting a company must never be a
        # single statement that takes a year of payroll with it; closing
        # one is a status change, and the FK is there to make the
        # difference impossible to blur.
        conn.execute(text(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES companies(id) ON DELETE RESTRICT"
        ))


# ══════════════════════════════════════════════
# Step 1 — the companies table
# ══════════════════════════════════════════════
def step_create_table():
    say("\n[1] companies table")
    with engine.begin() as conn:
        if table_exists(conn, "companies"):
            say("   already there")
            return
    say("   + CREATE TABLE companies")
    if APPLY:
        Company.__table__.create(bind=engine, checkfirst=True)


# ══════════════════════════════════════════════
# Step 2 — one row per existing CEO, keeping their id
# ══════════════════════════════════════════════
def step_seed_companies():
    say("\n[2] seed companies from existing CEOs (id is preserved)")
    db = SessionLocal()
    try:
        # ⚠ ONLY THE CEOs WHO DO NOT HAVE A COMPANY YET.
        # A first version selected every CEO and decided "already done"
        # by looking for a company whose id equalled the CEO's user id —
        # which is the very assumption this migration exists to remove.
        # So a CEO created AFTER the migration (company 1000, user 46)
        # looked unseeded, and the run died on the unique slug.
        #
        # `company_id IS NULL` is the actual question: has this CEO been
        # linked yet?
        has_col = column_exists(db.connection(), "users", "company_id")
        where = "role = 'ceo'" + (" AND company_id IS NULL" if has_col else "")
        ceos = db.execute(text(
            f"SELECT id, full_name, company_name, status FROM users "
            f"WHERE {where} ORDER BY id"
        )).fetchall()

        if not ceos:
            say("   every CEO already has a company")
            return

        # Two CEOs with the same name are ALREADY one merged tenant: the
        # employees of both resolve, through `.first()`, to whichever CEO
        # the database returned. Which employee belongs to which company
        # is not recoverable from the data, so this stops here.
        seen = {}
        for c in ceos:
            slug = normalise_name(c.company_name) or f"company-{c.id}"
            if slug in seen:
                note(
                    f"ABORT: CEOs {seen[slug]} and {c.id} both use the company "
                    f"name {c.company_name!r}. These two tenants are already "
                    f"merged and only you know which employee belongs to "
                    f"which. Rename one company, then run this again."
                )
                sys.exit(1)
            seen[slug] = c.id

        existing = {
            r[0] for r in db.execute(text("SELECT id FROM companies")).fetchall()
        } if table_exists(db.connection(), "companies") else set()

        for c in ceos:
            if c.id in existing:
                say(f"   = {c.id:5} {c.company_name!r} (already seeded)")
                continue
            slug = normalise_name(c.company_name) or f"company-{c.id}"
            status = CEO_STATUS_TO_COMPANY.get(
                (c.status or "").strip().lower(), STATUS_PENDING
            )
            say(f"   + {c.id:5} {c.company_name!r}  status={status}"
                f"  (from CEO status {c.status!r})")
            if APPLY:
                db.execute(text(
                    "INSERT INTO companies "
                    "  (id, name, slug, status, created_by, created_at, activated_at) "
                    "VALUES "
                    "  (:id, :name, :slug, :status, :by, NOW(), "
                    "   CASE WHEN :status = 'active' THEN NOW() ELSE NULL END)"
                ), {
                    "id": c.id,
                    "name": (c.company_name or f"Company {c.id}").strip(),
                    "slug": slug,
                    "status": status,
                    "by": c.id,
                })
        if APPLY:
            db.commit()
    finally:
        db.close()


# ══════════════════════════════════════════════
# Step 3 — new companies start at 1000
# ══════════════════════════════════════════════
def step_fix_sequence():
    say(f"\n[3] companies id sequence -> next value {SEQUENCE_START}")
    if not APPLY:
        say("   (would setval)")
        return
    with engine.begin() as conn:
        seq = conn.execute(text(
            "SELECT pg_get_serial_sequence('companies', 'id')"
        )).scalar()
        if not seq:
            note("no sequence on companies.id — new companies would collide")
            return
        current_max = conn.execute(text(
            "SELECT COALESCE(MAX(id), 0) FROM companies"
        )).scalar()
        start = max(SEQUENCE_START, current_max + 1)
        conn.execute(text("SELECT setval(:s, :v, false)"),
                     {"s": seq, "v": start})
        say(f"   next company id will be {start}")


# ══════════════════════════════════════════════
# Step 4 — users.company_id, and the last string match ever
# ══════════════════════════════════════════════
def step_users_company_id():
    say("\n[4] users.company_id")
    with engine.begin() as conn:
        add_column(conn, "users", "company_id", "INTEGER")

    db = SessionLocal()
    try:
        # ── CEOs: their company is the row seeded with their own id ──
        rows = db.execute(text(
            "SELECT id FROM users WHERE role = 'ceo' AND company_id IS NULL"
        )).fetchall()
        say(f"   CEOs to link: {len(rows)}")
        if APPLY and rows:
            db.execute(text(
                "UPDATE users SET company_id = id "
                "WHERE role = 'ceo' AND company_id IS NULL "
                "  AND EXISTS (SELECT 1 FROM companies c WHERE c.id = users.id)"
            ))

        # ── Employees: matched on the name, ONE LAST TIME ──
        # After this the column is the only thing anything reads. The
        # string stays on the row for display and is never matched on
        # again.
        emps = db.execute(text("""
            SELECT u.id, u.full_name, u.company_name,
                   (SELECT c.id FROM companies c
                     WHERE c.slug = lower(btrim(u.company_name))) AS match
              FROM users u
             WHERE u.role = 'employee' AND u.company_id IS NULL
             ORDER BY u.id
        """)).fetchall()

        matched = [e for e in emps if e.match]
        orphans = [e for e in emps if not e.match]
        say(f"   employees to link: {len(matched)} matched, "
            f"{len(orphans)} with no company")

        for o in orphans:
            note(f"employee {o.id} ({o.full_name!r}) has company_name "
                 f"{o.company_name!r} which matches no company — left "
                 f"unassigned, so they can no longer sign in until you "
                 f"place them")

        if APPLY and matched:
            db.execute(text("""
                UPDATE users u SET company_id = c.id
                  FROM companies c
                 WHERE c.slug = lower(btrim(u.company_name))
                   AND u.role = 'employee'
                   AND u.company_id IS NULL
            """))

        # The superadmin belongs to no tenant, and that is the point:
        # there is no company whose data they inherit by default.
        sa = db.execute(text(
            "SELECT COUNT(*) FROM users WHERE role = 'superadmin'"
        )).scalar()
        say(f"   superadmins left unassigned (correct): {sa}")

        if APPLY:
            db.commit()
    finally:
        db.close()

    with engine.begin() as conn:
        add_fk(conn, "users", "company_id", "fk_users_company")


# ══════════════════════════════════════════════
# Step 5 — recruitment gets a tenant column at last
# ══════════════════════════════════════════════
def step_recruitment_columns():
    say("\n[5] company_id on the recruitment tables")
    with engine.begin() as conn:
        for t in RECRUITMENT_COLUMNS:
            add_column(conn, t, "company_id", "INTEGER")


def step_recruitment_backfill():
    say("\n[6] backfill recruitment company_id")
    db = SessionLocal()
    try:
        # jobs: from the CEO who created it
        n = db.execute(text(
            "SELECT COUNT(*) FROM jobs WHERE company_id IS NULL"
        )).scalar()
        say(f"   jobs           {n} to fill  (from the creating CEO)")
        if APPLY and n:
            db.execute(text("""
                UPDATE jobs j SET company_id = u.company_id
                  FROM users u
                 WHERE u.id = j.ceo_id AND j.company_id IS NULL
            """))

        # Everything else reaches its company THROUGH THE JOB. Written
        # out one statement per table rather than generated from a join
        # spec — a migration runs once and is read by people, so clear
        # beats clever.
        #
        # Order matters: interview_feedback is filled from interviews,
        # so interviews must be filled first.
        backfills = [
            ("applications", """
                UPDATE applications t SET company_id = j.company_id
                  FROM jobs j WHERE j.id = t.job_id AND t.company_id IS NULL
            """),
            ("interviews", """
                UPDATE interviews t SET company_id = j.company_id
                  FROM jobs j WHERE j.id = t.job_id AND t.company_id IS NULL
            """),
            ("final_scores", """
                UPDATE final_scores t SET company_id = j.company_id
                  FROM jobs j WHERE j.id = t.job_id AND t.company_id IS NULL
            """),
            ("interview_feedback", """
                UPDATE interview_feedback t SET company_id = i.company_id
                  FROM interviews i
                 WHERE i.id = t.interview_id AND t.company_id IS NULL
            """),
        ]
        for table, sql in backfills:
            n = db.execute(text(
                f"SELECT COUNT(*) FROM {table} WHERE company_id IS NULL"
            )).scalar()
            say(f"   {table:18} {n} to fill")
            if APPLY and n:
                db.execute(text(sql))

        # candidates: through any application they have
        if APPLY:
            db.execute(text("""
                UPDATE candidates c SET company_id = a.company_id
                  FROM applications a
                 WHERE a.candidate_id = c.id AND c.company_id IS NULL
                   AND a.company_id IS NOT NULL
            """))
            db.commit()

        # candidates with no application at all — a part-finished screen
        # run whose job has since been deleted. Only assignable when the
        # answer is not a guess.
        left = db.execute(text(
            "SELECT id, full_name, email FROM candidates WHERE company_id IS NULL"
        )).fetchall()
        if left:
            owners = [r[0] for r in db.execute(text(
                "SELECT DISTINCT company_id FROM jobs WHERE company_id IS NOT NULL"
            )).fetchall()]
            if len(owners) == 1:
                say(f"   candidates     {len(left)} with no application -> "
                    f"company {owners[0]} (the only company with any jobs)")
                if APPLY:
                    db.execute(text(
                        "UPDATE candidates SET company_id = :c "
                        "WHERE company_id IS NULL"
                    ), {"c": owners[0]})
                    db.commit()
            else:
                for r in left:
                    note(f"candidate {r.id} ({r.email}) has no application and "
                         f"{len(owners)} companies have jobs — left unassigned, "
                         f"so it is visible to nobody")
    finally:
        db.close()

    with engine.begin() as conn:
        for t in RECRUITMENT_COLUMNS:
            add_fk(conn, t, "company_id", f"fk_{t}_company")


# ══════════════════════════════════════════════
# Step 6b — the child tables get one too
# ══════════════════════════════════════════════
# These five hold tenant data but reached their company only through a
# parent row: a message through its chat session, a break through its
# attendance session, a repayment through its loan.
#
# In practice the routes look the parent up first, and that lookup IS
# scoped — so there is no known way in. But "no known way in" is a
# statement about today's routes, and both walls are built to survive a
# route being written carelessly tomorrow. A table with no `company_id`
# is a table neither wall can protect: the ORM guard skips it, and
# `migrate_rls.py` cannot write a policy for it.
#
# `chat_messages` is the one that matters most. It is the transcript —
# salary figures, the reason for a sick leave, a grievance about a
# named colleague. It is the most private data in the system and it was
# the least protected.
CHILD_TABLES = [
    # (table, parent table, join column on the child, parent key)
    ("chat_messages", "chat_sessions", "session_id", "id"),
    ("attendance_intervals", "attendance_sessions", "session_id", "id"),
    ("loan_repayments", "employee_loans", "loan_id", "id"),
    ("policy_decisions_log", "leave_requests", "leave_request_id", "id"),
    ("face_enrollment", "users", "employee_id", "id"),
]


def step_child_tables():
    say("\n[6b] company_id on the child tables")
    with engine.begin() as conn:
        for table, _p, _c, _k in CHILD_TABLES:
            add_column(conn, table, "company_id", "INTEGER")

    db = SessionLocal()
    try:
        for table, parent, child_col, parent_key in CHILD_TABLES:
            n = db.execute(text(
                f"SELECT COUNT(*) FROM {table} WHERE company_id IS NULL"
            )).scalar()
            say(f"   {table:24} {n} to fill  (via {parent})")
            if APPLY and n:
                db.execute(text(f"""
                    UPDATE {table} t SET company_id = p.company_id
                      FROM {parent} p
                     WHERE p.{parent_key} = t.{child_col}
                       AND t.company_id IS NULL
                """))
        if APPLY:
            db.commit()

        for table, _p, _c, _k in CHILD_TABLES:
            left = db.execute(text(
                f"SELECT COUNT(*) FROM {table} WHERE company_id IS NULL"
            )).scalar()
            if left:
                note(f"{table}: {left} rows still without a company — their "
                     f"parent row is gone, so they are visible to nobody")
    finally:
        db.close()

    with engine.begin() as conn:
        for table, _p, _c, _k in CHILD_TABLES:
            add_fk(conn, table, "company_id", f"fk_{table}_company")
            name = f"ix_{table}_company"
            if not conn.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = :n"),
                    {"n": name}).first():
                say(f"   + INDEX {name}")
                if APPLY:
                    conn.execute(text(
                        f"CREATE INDEX {name} ON {table} (company_id)"))


# ══════════════════════════════════════════════
# Step 7 — a candidate's email is unique PER COMPANY
# ══════════════════════════════════════════════
def step_candidate_email_scope():
    say("\n[7] candidates.email: globally unique -> unique per company")
    # ═══ WHY THIS IS A LEAK AND NOT A CONSTRAINT DETAIL ═══
    # `fetch-and-screen` looks a candidate up by email and, when it finds
    # one, REUSES THAT ROW — CV text, PDF and all. With a global unique
    # index there is only ever one row per email, so the same person
    # applying to a second company would hand that company the CV,
    # filename and screening history belonging to the first.
    with engine.begin() as conn:
        # ⚠ The primary key is a unique index too. A first version of
        # this matched `indexdef ILIKE '%UNIQUE%'` and tried to drop
        # `candidates_pkey`; Postgres refused and the whole step rolled
        # back. `NOT indisprimary` is the line that matters, and the
        # email column is named explicitly so nothing else is touched.
        drops = conn.execute(text("""
            SELECT i.relname AS index_name,
                   c.conname  AS constraint_name
              FROM pg_index ix
              JOIN pg_class i ON i.oid = ix.indexrelid
              LEFT JOIN pg_constraint c
                     ON c.conindid = ix.indexrelid AND c.contype = 'u'
             WHERE ix.indrelid = 'candidates'::regclass
               AND ix.indisunique
               AND NOT ix.indisprimary
               AND pg_get_indexdef(ix.indexrelid) ILIKE '%(email)%'
        """)).fetchall()

        for row in drops:
            if row.constraint_name:
                say(f"   - DROP CONSTRAINT {row.constraint_name}")
                if APPLY:
                    conn.execute(text(
                        f"ALTER TABLE candidates "
                        f"DROP CONSTRAINT {row.constraint_name}"))
            else:
                say(f"   - DROP INDEX {row.index_name}")
                if APPLY:
                    conn.execute(text(
                        f"DROP INDEX IF EXISTS {row.index_name}"))

        if not constraint_exists(conn, "uq_candidate_company_email"):
            say("   + UNIQUE (company_id, email)")
            if APPLY:
                conn.execute(text(
                    "ALTER TABLE candidates ADD CONSTRAINT "
                    "uq_candidate_company_email UNIQUE (company_id, email)"
                ))


# ══════════════════════════════════════════════
# Step 7b — a foreign key on EVERY tenant table
# ══════════════════════════════════════════════
def step_all_foreign_keys():
    """
    The twenty tables that already had `company_id` had it as a bare
    `Integer` with nothing behind it. Any number at all could be written
    into that column and Postgres would agree — including a company that
    had never existed, or a user id that happened to be lying around.

    Adding the constraint is also what makes `ON DELETE RESTRICT` real:
    the database now refuses to remove a company while anything still
    belongs to it, so `DELETE /admin/delete-ceo` cannot orphan a year of
    payroll from any code path, not just from the one that was fixed.
    """
    say("\n[7b] foreign keys on every table carrying company_id")
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text("""
            SELECT c.table_name
              FROM information_schema.columns c
              JOIN information_schema.tables t
                ON t.table_name = c.table_name
               AND t.table_schema = c.table_schema
             WHERE c.table_schema = 'public'
               AND c.column_name = 'company_id'
               AND t.table_type = 'BASE TABLE'
             ORDER BY c.table_name
        """)).fetchall()]

        added = 0
        for t in tables:
            has = conn.execute(text("""
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'
                   AND confrelid = CAST('companies' AS regclass)
            """), {"t": t}).first()
            if has:
                continue

            # A constraint cannot be added over rows that would break it.
            bad = conn.execute(text(f"""
                SELECT COUNT(*) FROM {t} x
                 WHERE x.company_id IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM companies c
                                    WHERE c.id = x.company_id)
            """)).scalar()
            if bad:
                note(f"{t}: {bad} rows point at a company that does not "
                     f"exist — the foreign key cannot be added until those "
                     f"are resolved")
                continue

            say(f"   + FK fk_{t}_company")
            added += 1
            if APPLY:
                conn.execute(text(
                    f"ALTER TABLE {t} ADD CONSTRAINT fk_{t}_company "
                    f"FOREIGN KEY (company_id) REFERENCES companies(id) "
                    f"ON DELETE RESTRICT"
                ))
        if not added:
            say("   every tenant table already has one")


# ══════════════════════════════════════════════
# Step 8 — indexes the tenant filter will lean on
# ══════════════════════════════════════════════
def step_indexes():
    say("\n[8] indexes")
    wanted = [
        ("ix_users_company", "users", "(company_id, role, status)"),
        ("ix_jobs_company", "jobs", "(company_id, status)"),
        ("ix_candidates_company", "candidates", "(company_id)"),
        ("ix_applications_company", "applications", "(company_id, job_id)"),
        ("ix_interviews_company", "interviews", "(company_id)"),
        ("ix_feedback_company", "interview_feedback", "(company_id)"),
        ("ix_final_scores_company", "final_scores", "(company_id)"),
    ]
    with engine.begin() as conn:
        for name, table, cols in wanted:
            exists = conn.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = :n"
            ), {"n": name}).first()
            if exists:
                continue
            say(f"   + INDEX {name} ON {table} {cols}")
            if APPLY:
                conn.execute(text(
                    f"CREATE INDEX {name} ON {table} {cols}"))


# ══════════════════════════════════════════════
# Step 9 — prove it, do not assume it
# ══════════════════════════════════════════════
def step_verify():
    say("\n[9] verify")
    db = SessionLocal()
    ok = True
    try:
        q = lambda s, **k: db.execute(text(s), k).fetchall()  # noqa: E731

        companies = q("SELECT id, name, slug, status FROM companies ORDER BY id")
        say(f"   companies: {len(companies)}")
        for c in companies:
            n = q("SELECT COUNT(*) FROM users WHERE company_id = :c",
                  c=c.id)[0][0]
            say(f"     {c.id:5}  {c.name:<20} {c.status:<10} {n} users")

        stray = q("""SELECT id, full_name, role FROM users
                      WHERE company_id IS NULL AND role <> 'superadmin'""")
        if stray:
            ok = False
            for s in stray:
                note(f"user {s.id} ({s.full_name!r}, {s.role}) has no company")
        else:
            say("   every CEO and employee has a company  OK")

        # No tenant table may point at a company that is not there. This
        # is what the foreign keys now prevent; this checks the data that
        # existed before they did.
        for table in [
            "attendance_sessions", "leave_requests", "leave_balances",
            "payslips", "payroll_runs", "salary_structures", "chat_sessions",
            "hr_requests", "hr_cases", "company_leave_types", "jobs",
            "candidates", "applications", "interviews",
            "chat_messages", "attendance_intervals", "loan_repayments",
            "policy_decisions_log", "face_enrollment",
        ]:
            bad = q(f"""SELECT COUNT(*) FROM {table} t
                         WHERE t.company_id IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM companies c
                                            WHERE c.id = t.company_id)""")[0][0]
            if bad:
                ok = False
                note(f"{table}: {bad} rows point at a company that does not exist")
        if ok:
            say("   no row points at a missing company  OK")

        nulls = []
        for table in ["jobs", "candidates", "applications", "interviews",
                      "interview_feedback", "final_scores"]:
            n = q(f"SELECT COUNT(*) FROM {table} WHERE company_id IS NULL")[0][0]
            if n:
                nulls.append(f"{table}={n}")
        if nulls:
            note(f"rows still without a company (visible to nobody): "
                 f"{', '.join(nulls)}")
        else:
            say("   all recruitment rows have a company  OK")
    finally:
        db.close()
    return ok


def main():
    say("=" * 62)
    say("  MULTI-TENANT MIGRATION" + ("  [APPLY]" if APPLY else "  [dry run]"))
    say("=" * 62)

    step_create_table()
    if APPLY or table_exists(engine.connect(), "companies"):
        step_seed_companies()
        step_fix_sequence()
        step_users_company_id()
        step_recruitment_columns()
        step_recruitment_backfill()
        step_child_tables()
        step_candidate_email_scope()
        step_all_foreign_keys()
        step_indexes()
        step_verify()
    else:
        say("\n(dry run: companies table does not exist yet, so the later "
            "steps cannot be inspected. Run with --apply.)")

    say("\n" + "=" * 62)
    if _notes:
        say(f"  {len(_notes)} thing(s) need your attention:")
        for n in _notes:
            say(f"   - {n}")
    else:
        say("  clean")
    if not APPLY:
        say("\n  dry run — nothing was written. Re-run with --apply")
    say("=" * 62)


if __name__ == "__main__":
    main()
