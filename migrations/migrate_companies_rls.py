"""
Row-level security on `companies` itself
────────────────────────────────────────
    py migrations/migrate_companies_rls.py            show what it would do
    py migrations/migrate_companies_rls.py --apply    do it

`companies` was the one table left without a policy. Two things stood in
the way, and both had to be solved before enabling it — turning it on
alone would have broken every request in the system.

═══════════════════════════════════════════════════════════
OBSTACLE 1 — THE LOOKUP THAT DECIDES THE SCOPE
═══════════════════════════════════════════════════════════
`get_tenant` read the company row BEFORE stamping the session:

    user    = _bootstrap_user(db, ...)
    company = _load_company(db, user.company_id)   <- session unscoped
    ...
    bind_tenant(db, company.id)

With a policy in place that read returns nothing, `get_tenant` raises,
and every authenticated request becomes a 403. The two lines are simply
swapped — bind first, then load — which is also strictly better: the
window in which the session has no scope gets shorter, not longer.

═══════════════════════════════════════════════════════════
OBSTACLE 2 — "IS THIS COMPANY NAME TAKEN?"
═══════════════════════════════════════════════════════════
That question has to see names the asker is not allowed to read. Under a
policy it silently answers "no" every time, a duplicate slug reaches the
unique index, and the CEO gets a raw IntegrityError instead of "that
name is already registered".

So it becomes a function that returns a BOOLEAN and never rows:

    agentra_company_name_taken(slug, exclude_id) -> boolean

`SECURITY DEFINER`, so it runs as the owner and sees the whole table;
`SET search_path`, without which a SECURITY DEFINER function is a
privilege-escalation hole; and `REVOKE ... FROM PUBLIC` so only the
application role may call it.

⚠ It answers one bit. Somebody can learn that a name is registered —
which is inherent to any "this name is taken" check anywhere, and is
exactly what the CEO asked. It cannot be used to read a name, a status,
or anything else.
"""

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

from app.database import admin_engine

APPLY = "--apply" in sys.argv
engine = admin_engine()

APP_ROLE = "agentra_app"
SETTING = "agentra.company_id"

FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION agentra_company_name_taken(
    p_slug text,
    p_exclude integer DEFAULT NULL
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM public.companies
         WHERE slug = p_slug
           AND (p_exclude IS NULL OR id <> p_exclude)
    );
$fn$;
"""

POLICY_COND = (
    f"(id = NULLIF(current_setting('{SETTING}', true), '')::int "
    f"OR current_setting('{SETTING}', true) = '0')"
)


def say(m=""):
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"))


def main():
    say("=" * 66)
    say("  COMPANIES RLS" + ("  [APPLY]" if APPLY else "  [dry run]"))
    say("=" * 66)

    # ── 1. the name check, before anything is locked down ──
    say("\n[1] agentra_company_name_taken(slug, exclude) -> boolean")
    say("   SECURITY DEFINER, fixed search_path, PUBLIC revoked")
    if APPLY:
        with engine.begin() as conn:
            conn.execute(text(FUNCTION_SQL))
            conn.execute(text(
                "REVOKE ALL ON FUNCTION agentra_company_name_taken(text, integer) "
                "FROM PUBLIC"))
            conn.execute(text(
                f"GRANT EXECUTE ON FUNCTION "
                f"agentra_company_name_taken(text, integer) TO {APP_ROLE}"))
        say("   created")

    # ── 2. prove it works before relying on it ──
    say("\n[2] the function answers correctly")
    if APPLY:
        from app.database import engine as app_engine
        with app_engine.connect() as conn:
            taken = conn.execute(text(
                "SELECT agentra_company_name_taken('techtribe', NULL)")).scalar()
            free = conn.execute(text(
                "SELECT agentra_company_name_taken("
                "'definitely-not-registered-xyz', NULL)")).scalar()
            skip = conn.execute(text(
                "SELECT agentra_company_name_taken('techtribe', "
                "(SELECT id FROM companies WHERE slug='techtribe'))")).scalar()
        say(f"   'techtribe' taken          -> {taken}   (expected True)")
        say(f"   an unused name             -> {free}   (expected False)")
        say(f"   'techtribe' excluding self -> {skip}   (expected False)")
        if not (taken is True and free is False and skip is False):
            say("   ⚠ the function is not answering correctly — NOT enabling "
                "the policy")
            return

    # ── 3. the policy ──
    say("\n[3] row-level security on companies")
    with engine.begin() as conn:
        r = conn.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'companies'")).first()
        pol = conn.execute(text(
            "SELECT 1 FROM pg_policies WHERE tablename = 'companies' "
            "AND policyname = 'tenant_isolation'")).first()

        todo = []
        if not (r and r[0]):
            todo.append("ENABLE")
        if not (r and r[1]):
            todo.append("FORCE")
        if not pol:
            todo.append("POLICY")

        if not todo:
            say("   already protected")
        else:
            say(f"   {' '.join(todo)}")
            say(f"   USING {POLICY_COND}")
            if APPLY:
                if "ENABLE" in todo:
                    conn.execute(text(
                        "ALTER TABLE companies ENABLE ROW LEVEL SECURITY"))
                if "FORCE" in todo:
                    conn.execute(text(
                        "ALTER TABLE companies FORCE ROW LEVEL SECURITY"))
                if "POLICY" in todo:
                    conn.execute(text(
                        f"CREATE POLICY tenant_isolation ON companies "
                        f"FOR ALL TO PUBLIC "
                        f"USING {POLICY_COND} WITH CHECK {POLICY_COND}"))

    say("\n" + "=" * 66)
    if APPLY:
        say("  done. Now run, in this order:")
        say("     py tests/check_tenancy.py        (login and tenant lookup)")
        say("     py tests/check_companies_rls.py  (the new boundary)")
        say("     py tests/_e2e_newcompany.py      (signup, which creates a company)")
    else:
        say("  dry run — nothing was written. Re-run with --apply")
    say("=" * 66)


if __name__ == "__main__":
    main()
