"""
The `companies` table's own boundary
────────────────────────────────────
    py check_companies_rls.py

`companies` was the last table without a row-level-security policy, and
the reasons it was left out were real: the lookup that DECIDES a
request's scope reads from it, and "is this name taken?" has to see
names the asker may not read.

Both are solved rather than waived — the scope is bound before the row
is read, and the name check is a boolean function. These checks prove
that the policy is on AND that neither of those two things broke, which
is the only combination worth having.
"""
import warnings

warnings.filterwarnings("ignore")

import secrets                                                  # noqa: E402

from sqlalchemy import func, text                               # noqa: E402

from app.database import engine                                 # noqa: E402
from app.models.company import (                                # noqa: E402
    Company, name_is_taken, normalise_name,
)
from app.models.user import User                                # noqa: E402
from app.utils.tenancy import (                                 # noqa: E402
    open_tenant_session, open_unscoped_session,
)

fails = []


def check(label, ok, extra=""):
    if not ok:
        fails.append(f"{label}  {extra}")
    line = f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


# ══════════════════════════════════════════════
# 1. The policy is actually on
# ══════════════════════════════════════════════
print("The policy:")
with engine.connect() as c:
    r = c.execute(text(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = 'companies'")).first()
    pol = c.execute(text(
        "SELECT COUNT(*) FROM pg_policies WHERE tablename = 'companies'"
    )).scalar()
    check("row-level security is enabled and FORCEd",
          bool(r and r[0] and r[1]), f"enabled={r[0]} forced={r[1]}")
    check("a policy exists", pol == 1, f"{pol} policies")

    su = c.execute(text(
        "SELECT (SELECT usesuper FROM pg_user WHERE usename=current_user), "
        "(SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user)"
    )).first()
    check("the application role is subject to it",
          not su[0] and not su[1],
          "not a superuser, no BYPASSRLS")

# ══════════════════════════════════════════════
# 2. Raw SQL, with the ORM out of the picture
# ══════════════════════════════════════════════
print("\nRaw SQL against `companies`:")
with open_unscoped_session("check_companies_rls: listing") as db:
    ids = [c.id for c in db.query(Company).order_by(Company.id).all()]
    total = len(ids)
print(f"  ({total} companies exist: {ids})")

with engine.connect() as c:
    n = c.execute(text("SELECT COUNT(*) FROM companies")).scalar()
    check("no company announced -> no rows", n == 0, f"{n} rows")

    if ids:
        c.execute(text(f"SET \"agentra.company_id\" = '{ids[0]}'"))
        n = c.execute(text("SELECT COUNT(*) FROM companies")).scalar()
        check("scoped to one company -> exactly its own row", n == 1,
              f"{n} rows")
        if len(ids) > 1:
            n = c.execute(text(
                f"SELECT COUNT(*) FROM companies WHERE id = {ids[-1]}"
            )).scalar()
            check("...and asking for another company's row -> nothing",
                  n == 0, f"{n} rows")

    c.execute(text("SET \"agentra.company_id\" = '0'"))
    n = c.execute(text("SELECT COUNT(*) FROM companies")).scalar()
    check("the '0' sentinel sees them all (superadmin, login, scheduler)",
          n == total, f"{n} of {total}")

# ══════════════════════════════════════════════
# 3. Through the ORM, from a tenant's session
# ══════════════════════════════════════════════
if len(ids) > 1:
    print("\nFrom a tenant's own session:")
    mine, theirs = ids[0], ids[-1]
    with open_tenant_session(mine) as db:
        own = db.query(Company).filter(Company.id == mine).first()
        check("a company can read itself", own is not None and own.id == mine,
              f"company {mine}")
        other = db.query(Company).filter(Company.id == theirs).first()
        check("a company cannot read another", other is None,
              f"asked for {theirs}, got {other}")
        everything = db.query(Company).all()
        check("an unfiltered list returns only its own row",
              len(everything) == 1 and everything[0].id == mine,
              f"{len(everything)} row(s)")

# ══════════════════════════════════════════════
# 4. The thing the policy would have broken
# ══════════════════════════════════════════════
print("\n'Is this name taken?' — the question that must still work:")
with open_unscoped_session("check_companies_rls: reading names") as db:
    known = db.query(Company).order_by(Company.id).first()
    known_slug, known_id = known.slug, known.id

# From a session scoped to a DIFFERENT company — which is precisely the
# case a plain query gets wrong under the policy: it sees nothing and
# answers "free".
scope = ids[-1] if len(ids) > 1 else ids[0]
with open_tenant_session(scope) as db:
    check("a taken name is reported as taken, from another company's session",
          name_is_taken(db, known_slug) is True,
          f"slug {known_slug!r}, asked while scoped to {scope}")
    check("an unused name is reported as free",
          name_is_taken(db, f"nobody-{secrets.token_hex(6)}") is False)
    check("a company's own name does not clash with itself",
          name_is_taken(db, known_slug, exclude_company_id=known_id) is False,
          "so renaming to the same slug is allowed")

    # And the plain query it replaced — shown failing, on purpose, so
    # the reason the function exists is visible rather than asserted.
    naive = db.query(Company).filter(Company.slug == known_slug).first()
    check("...whereas the plain query it replaced sees nothing",
          naive is None,
          "which is why the boolean function exists")

# ══════════════════════════════════════════════
# 5. The function itself
# ══════════════════════════════════════════════
print("\nThe function's own boundary:")
with engine.connect() as c:
    kind = c.execute(text(
        "SELECT prosecdef FROM pg_proc WHERE proname = "
        "'agentra_company_name_taken'")).scalar()
    check("it is SECURITY DEFINER", kind is True)

    cfg = c.execute(text(
        "SELECT proconfig FROM pg_proc WHERE proname = "
        "'agentra_company_name_taken'")).scalar()
    check("...with a fixed search_path",
          bool(cfg) and any("search_path" in x for x in cfg),
          str(cfg))

    # ⚠ ASK POSTGRES, DO NOT PARSE THE ACL STRING.
    # A first version stripped the app role's entry out of `proacl` and
    # then looked for "=X/" — which `postgres=X/postgres` also contains,
    # so it reported a PUBLIC grant that was not there. The ACL text is a
    # serialisation, not an answer; `has_function_privilege` is the
    # answer.
    sig = "agentra_company_name_taken(text,integer)"
    check("the application role may execute it",
          c.execute(text("SELECT has_function_privilege('agentra_app', "
                         ":s, 'EXECUTE')"), {"s": sig}).scalar() is True)
    check("PUBLIC may not execute it",
          c.execute(text("SELECT has_function_privilege('public', "
                         ":s, 'EXECUTE')"), {"s": sig}).scalar() is False,
          "so it cannot be reached by any other role")

    # It must answer one bit and nothing else.
    c.execute(text("SET \"agentra.company_id\" = '-1'"))
    out = c.execute(
        text("SELECT agentra_company_name_taken(:s, NULL)"),
        {"s": known_slug}).scalar()
    check("it answers even from a scope that can read no companies",
          out is True, f"-> {out}")
    check("and it returns a boolean, never a row", isinstance(out, bool),
          type(out).__name__)

print("\n" + "=" * 66)
print(f"  {len(fails)} FAILURE(S)" if fails
      else "  companies boundary: every check passed")
for f in fails:
    print(f"   - {f}")
print("=" * 66)
raise SystemExit(1 if fails else 0)
