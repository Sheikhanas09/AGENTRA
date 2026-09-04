"""
The second wall: Postgres row-level security
────────────────────────────────────────────
The tenant guard in `utils/tenant_guard.py` adds `company_id = <this
session's company>` to every ORM query. It covers everything this
application does today, because this application issues zero raw SQL.

"Today" is the load-bearing word. One `db.execute(text(...))` added in a
hurry next year, one library that talks to the connection directly, one
place that reaches past the ORM — and the wall has a door in it, with
nothing to say so.

So the rule is moved into the database, where it does not depend on how
the query was written:

    CREATE POLICY tenant_isolation ON payslips
      USING (company_id = current_setting('agentra.company_id', true)::int)

Postgres now applies that to every SELECT, UPDATE and DELETE against the
table, from any code path at all. A query that forgets the company does
not return the wrong rows — it returns none.

Run:  py migrations/migrate_rls.py            (shows what it would do)
      py migrations/migrate_rls.py --apply    (does it)

═══════════════════════════════════════════════════════════
⚠ THE APP MUST NOT CONNECT AS A SUPERUSER
═══════════════════════════════════════════════════════════
Postgres exempts superusers and table owners from RLS. Connecting as
`postgres` — which is what this app does today — means every policy
below is created, is listed by every inspection query, looks completely
correct, and does absolutely nothing.

That is the worst possible state: a wall everybody believes in. So this
script creates a plain `agentra_app` role, and `check_tenancy.py`
reports whether the connected role is actually subject to RLS rather
than whether the policies exist. `FORCE ROW LEVEL SECURITY` is set as
well, so the policies apply even to the table owner.

═══════════════════════════════════════════════════════════
WHAT `agentra.company_id = '0'` MEANS, AND ITS LIMIT
═══════════════════════════════════════════════════════════
Some work legitimately crosses companies: the superadmin approving a
company, the scheduler asking which companies exist, login looking a
user up by email before anybody knows their company. Those set the
sentinel `'0'`, and the policies let it through.

Honest limitation: a SQL-injection hole would let an attacker run
`SET agentra.company_id = '0'` and step past these policies. Two things
hold that in place — this codebase issues no raw SQL at all
(`check_tenancy.py` asserts it), and the ORM guard would still be
filtering above. A dedicated BYPASSRLS role for the few unscoped paths
would close it completely, at the cost of a second connection pool. It
is a real trade and it is written down rather than glossed over.

═══════════════════════════════════════════════════════════
WHY `companies` ITSELF HAS NO POLICY
═══════════════════════════════════════════════════════════
It holds no tenant data — an id, a display name, a slug, a status. Two
things need to see all of it: the check that a company name is not
already taken (which must look at names it is not allowed to use), and
the superadmin's console. A policy here would make the name check pass
silently and let a duplicate name through to the unique index as a raw
IntegrityError.
"""

import os
import re
import secrets
import sys

from sqlalchemy import text

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

from app.database import admin_engine, DATABASE_URL
from app.utils.tenant_guard import TENANT_COLUMN

# DDL needs the table owner, not the application role. Once DATABASE_URL
# points at `agentra_app` that role can no longer CREATE POLICY — which
# is the point: an app able to drop the policies protecting it is not
# protected. Put the privileged URL in ADMIN_DATABASE_URL.
engine = admin_engine()

APPLY = "--apply" in sys.argv
WRITE_ENV = "--write-env" in sys.argv

APP_ROLE = "agentra_app"
POLICY = "tenant_isolation"

# The GUC the policies read. Namespaced so it cannot collide with
# anything Postgres or an extension uses.
SETTING = "agentra.company_id"

# The sentinel that means "this connection may cross companies".
UNSCOPED_VALUE = "0"


def say(m=""):
    print(m)


def tenant_tables(conn):
    """
    Every table with a `company_id` column.

    Discovered, not listed. A hand-written list of protected tables is
    the thing that silently falls out of date the first time somebody
    adds a model — and this project has been bitten by exactly that
    before.
    """
    rows = conn.execute(text("""
        SELECT c.table_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_name = c.table_name AND t.table_schema = c.table_schema
         WHERE c.table_schema = 'public'
           AND c.column_name = :col
           AND t.table_type = 'BASE TABLE'
         ORDER BY c.table_name
    """), {"col": TENANT_COLUMN}).fetchall()
    return [r[0] for r in rows]


def role_exists(conn, name):
    return bool(conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name}).first())


# ══════════════════════════════════════════════
# Step 1 — a role that RLS actually applies to
# ══════════════════════════════════════════════
def step_role():
    say(f"\n[1] the application role `{APP_ROLE}`")
    password = os.getenv("APP_DB_PASSWORD", "").strip() or secrets.token_urlsafe(18)

    with engine.begin() as conn:
        if role_exists(conn, APP_ROLE):
            say(f"   `{APP_ROLE}` already exists — its password is left alone")
            return None

        say(f"   + CREATE ROLE {APP_ROLE} LOGIN")
        if not APPLY:
            return None
        conn.execute(text(
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD :pw NOSUPERUSER "
            f"NOCREATEDB NOCREATEROLE NOBYPASSRLS"
        ), {"pw": password})
    return password


def step_grants():
    say("\n[2] privileges")
    db_name = engine.url.database
    stmts = [
        f"GRANT CONNECT ON DATABASE {db_name} TO {APP_ROLE}",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
        f"IN SCHEMA public TO {APP_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        # Tables created later (a new model, a migration) are covered
        # without anybody remembering to come back here.
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, "
        f"UPDATE, DELETE ON TABLES TO {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT "
        f"ON SEQUENCES TO {APP_ROLE}",
    ]
    for s in stmts:
        say(f"   {s[:78]}")
        if APPLY:
            with engine.begin() as conn:
                conn.execute(text(s))


# ══════════════════════════════════════════════
# Step 3 — the policies
# ══════════════════════════════════════════════
def step_policies():
    say("\n[3] row-level security")
    with engine.begin() as conn:
        tables = tenant_tables(conn)
        say(f"   {len(tables)} tables carry `{TENANT_COLUMN}`")

        for t in tables:
            enabled = conn.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = :t"), {"t": t}).first()
            has_policy = conn.execute(text(
                "SELECT 1 FROM pg_policies "
                "WHERE tablename = :t AND policyname = :p"),
                {"t": t, "p": POLICY}).first()

            todo = []
            if not (enabled and enabled[0]):
                todo.append("ENABLE")
            if not (enabled and enabled[1]):
                todo.append("FORCE")
            if not has_policy:
                todo.append("POLICY")

            say(f"   {t:26} {' '.join(todo) if todo else 'already protected'}")
            if not APPLY or not todo:
                continue

            if "ENABLE" in todo:
                conn.execute(text(
                    f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY"))
            if "FORCE" in todo:
                # Without FORCE the table's OWNER is exempt. The owner is
                # the role that ran create_all — so the policies would be
                # off for exactly the connection the app uses.
                conn.execute(text(
                    f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY"))
            if "POLICY" in todo:
                # USING filters what can be READ (and which rows an
                # UPDATE/DELETE can touch); WITH CHECK filters what can
                # be WRITTEN. Both are needed: without WITH CHECK a
                # tenant could INSERT a row carrying another company's
                # id — and then not be able to see it, which looks like
                # nothing happened.
                #
                # `current_setting(..., true)` yields NULL when unset,
                # and `company_id = NULL` is NULL, which is not true. So
                # a connection that never announced a company reads
                # nothing. Default deny, from the shape of SQL itself.
                cond = (
                    f"(company_id = NULLIF(current_setting('{SETTING}', true), "
                    f"'')::int "
                    f"OR current_setting('{SETTING}', true) = '{UNSCOPED_VALUE}')"
                )
                conn.execute(text(
                    f"CREATE POLICY {POLICY} ON {t} "
                    f"FOR ALL TO PUBLIC USING {cond} WITH CHECK {cond}"
                ))
    return tables


# ══════════════════════════════════════════════
# Step 4 — tell the operator exactly what to do
# ══════════════════════════════════════════════
def step_env(password):
    say("\n[4] .env")
    host = engine.url.host or "localhost"
    port = engine.url.port or 5432
    db_name = engine.url.database
    pw = password or "<the password you already set>"
    url = f"postgresql://{APP_ROLE}:{pw}@{host}:{port}/{db_name}"

    if password:
        say("   The role's password is shown ONCE. Put this in `.env`:")
    else:
        say(f"   `{APP_ROLE}` already existed, so its password is not known "
            f"here. Use the one you saved:")
    say("")
    say(f"   DATABASE_URL={url}")
    say("")

    if WRITE_ENV and password and APPLY:
        _write_env(url)
    elif password:
        say("   (or re-run with --write-env to have it written for you)")


def _write_env(url):
    path = ".env"
    try:
        lines = open(path, encoding="utf-8").read().splitlines() \
            if os.path.exists(path) else []
    except OSError as e:
        say(f"   could not read .env: {e}")
        return

    out, replaced = [], False
    for ln in lines:
        if re.match(r"\s*DATABASE_URL\s*=", ln):
            if not replaced:
                out.append("# The previous value is kept below, commented out,")
                out.append("# because switching back is how you check whether a")
                out.append("# problem is RLS or something else.")
                out.append("# " + ln)
                out.append(f"DATABASE_URL={url}")
                replaced = True
            continue
        out.append(ln)
    if not replaced:
        out += ["", f"DATABASE_URL={url}"]

    open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
    say("   .env updated (the old DATABASE_URL is kept as a comment)")


def main():
    say("=" * 66)
    say("  ROW-LEVEL SECURITY" + ("  [APPLY]" if APPLY else "  [dry run]"))
    say("=" * 66)

    with engine.connect() as conn:
        who = conn.execute(text(
            "SELECT current_user, "
            "(SELECT usesuper FROM pg_user WHERE usename = current_user)"
        )).first()
    say(f"\n  connected as `{who[0]}`"
        + ("  (superuser — which is why this script can create the role)"
           if who[1] else ""))

    password = step_role()
    step_grants()
    tables = step_policies()
    step_env(password)

    say("\n" + "=" * 66)
    if APPLY:
        say(f"  {len(tables)} tables protected.")
        say("  NOTHING IS ENFORCED UNTIL DATABASE_URL POINTS AT "
            f"`{APP_ROLE}` —")
        say("  as `postgres` the policies exist and are ignored.")
        say("  Then: py tests/check_tenancy.py")
    else:
        say("  dry run — nothing was written. Re-run with --apply")
    say("=" * 66)


if __name__ == "__main__":
    main()
