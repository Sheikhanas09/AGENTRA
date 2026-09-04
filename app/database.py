import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

load_dotenv()

# ══════════════════════════════════════════════
# Where the database lives
# ══════════════════════════════════════════════
# This used to be one hard-coded line with the password in it. It is now
# read from `.env` (which is gitignored) and only falls back to the old
# value so nobody's setup breaks on the day they pull this.
#
# ⚠ THE ROLE MATTERS AS MUCH AS THE URL. Postgres lets superusers ignore
# row-level security entirely, so connecting as `postgres` silently turns
# off the second wall — no error, no warning, just no protection. Run
# `py migrations/migrate_rls.py --apply`, then point this at the `agentra_app` role
# it creates. `check_tenancy.py` reports which role is actually in use.
_FALLBACK_DB = "postgresql://postgres:12345@localhost:5432/agentra"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or _FALLBACK_DB

engine = create_engine(DATABASE_URL)


def admin_engine():
    """
    A connection for the migration scripts, which need DDL.

    Once `DATABASE_URL` points at `agentra_app` — which is the whole
    point, since that role is not exempt from row-level security — that
    role cannot CREATE TABLE, ALTER TABLE or CREATE POLICY. That is
    correct: an application that can rewrite its own schema can also
    remove the policies protecting it.

    So migrations run as the owner. Put the privileged URL in
    `ADMIN_DATABASE_URL`; without it this falls back to `DATABASE_URL`,
    which is what happens before the RLS step and is fine there.
    """
    url = os.getenv("ADMIN_DATABASE_URL", "").strip() or DATABASE_URL
    return create_engine(url)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


# ══════════════════════════════════════════════
# Switch the tenant guard on
# ══════════════════════════════════════════════
# Imported HERE, at the bottom of the module every other module already
# depends on, so the hooks are registered before any Session can be
# created — including in the one-off scripts, which is where a guard
# that has to be imported by hand would first be forgotten.
#
# `tenant_guard` imports nothing from this app, so there is no cycle.
from app.utils import tenant_guard  # noqa: E402,F401


def get_db():
    """
    The request's session.

    It arrives with NO company stamped on it, and that is the safe state:
    the guard refuses tenant queries until something decides the scope.
    `Depends(get_tenant)` is what decides it, and FastAPI hands the route
    and that dependency the same session, so the stamp lands on the one
    the route actually uses.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()