"""
No query can forget the company
───────────────────────────────
There are 405 ORM queries in this codebase and 170 of them never mention
`company_id`. Most are safe by accident — they filter on an
`employee_id` that some earlier line checked. Safe by accident is a
property that lasts until somebody edits the earlier line.

So the filter stops being something each query has to remember. This
module hooks SQLAlchemy so that EVERY select, update and delete against
a table with a `company_id` column has `company_id = <this session's
company>` added to it before it runs, whether the query asked for it or
not.

    db.query(Payslip).filter(Payslip.id == 7).first()
        ->  SELECT ... FROM payslips
             WHERE payslips.id = 7 AND payslips.company_id = 19

That one line is why a CEO of another company now gets `None` from every
one of those 170 queries instead of somebody else's data.

═══════════════════════════════════════════════════════════
WHY THIS COVERS EVERYTHING HERE, AND WHERE IT WOULD NOT
═══════════════════════════════════════════════════════════
An ORM hook is only as complete as the ORM's share of the data access.
This application runs `db.execute(text(...))` exactly ZERO times — every
one of the 405 reads and writes goes through the ORM. (`check_tenancy.py`
asserts that, so it stays true.) The Postgres row-level security
policies added by `migrate_rls.py` are the second wall, for anything
that ever bypasses this one.

═══════════════════════════════════════════════════════════
THE DEFAULT IS TO REFUSE
═══════════════════════════════════════════════════════════
A session nobody stamped is not treated as "all companies". It raises.
A new route that forgets `Depends(get_tenant)` therefore fails loudly on
its first request instead of quietly serving every tenant's rows — which
is the failure this whole module exists to prevent.

Reading across companies has to be asked for, in writing:

    bind_unscoped(db, "why")                  a whole session
    .execution_options(tenant_bypass="why")   one statement

`check_tenancy.py` prints every occurrence of both so the list stays
short and reviewed.

═══════════════════════════════════════════════════════════
WHICH MODELS ARE PROTECTED IS NOT A LIST
═══════════════════════════════════════════════════════════
Any mapped class with a `company_id` column is tenant data. There is no
hand-written registry to fall out of date, because this project has been
bitten by exactly that before: a guard whose list of tool names had to
be kept in step by hand, and nothing said a word when it was not. A new
model with a `company_id` is protected the moment it is defined.
"""

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

# Stamped on `session.info`. See `utils/tenancy.py` for why the Session
# carries this and not a ContextVar.
TENANT_KEY = "agentra_tenant"

# The value stamped instead of a company id when crossing companies is
# deliberate. Always a (UNSCOPED, reason) pair — the reason travels with
# the permission rather than living in a comment somewhere else.
UNSCOPED = "__unscoped__"

# The column that makes a table tenant data.
TENANT_COLUMN = "company_id"


class TenantScopeError(RuntimeError):
    """
    A query touched tenant data on a session that was never told which
    company it belongs to.

    Always a bug in our code, never bad input: a route without
    `Depends(get_tenant)`, or a background job that opened a bare
    `SessionLocal()` instead of `open_tenant_session()`.
    """


# ══════════════════════════════════════════════
# Stamping (the primitives; policy lives in tenancy.py)
# ══════════════════════════════════════════════
def bind_tenant(db: Session, company_id: int) -> None:
    db.info[TENANT_KEY] = int(company_id)
    _push_to_database(db)


def bind_unscoped(db: Session, reason: str) -> None:
    if not reason or not str(reason).strip():
        raise ValueError("bind_unscoped needs a reason")
    db.info[TENANT_KEY] = (UNSCOPED, str(reason).strip())
    _push_to_database(db)


# ══════════════════════════════════════════════
# Telling Postgres the same thing
# ══════════════════════════════════════════════
# The row-level security policies added by `migrate_rls.py` read
# `current_setting('agentra.company_id')`. This is what puts it there, so
# the two walls can never disagree about which company a session is: the
# ORM filter and the database policy are fed from the same stamp.
#
# See `migrate_rls.py` for why '0' means "may cross companies".
DB_SETTING = "agentra.company_id"
DB_UNSCOPED = "0"


def _setting_value(scope):
    if is_unscoped(scope):
        return DB_UNSCOPED
    if isinstance(scope, int):
        return str(scope)
    return None


_warned_once = set()


def _push_to_database(db: Session, connection=None) -> None:
    """
    ⚠ `SET LOCAL` LASTS ONLY UNTIL THE TRANSACTION ENDS.
    A route that commits half way through and then queries again would
    otherwise find the setting gone and — correctly, and disastrously
    for that request — be shown nothing at all. That is why this is not
    only called from `bind_*`: the `after_begin` hook below re-applies it
    at the start of every new transaction on an already-stamped session.
    """
    value = _setting_value(scope_of(db))
    if value is None:
        return

    # ⚠ USE THE CONNECTION THE EVENT HANDED US.
    # A first version always called `db.connection()`, including from
    # inside `after_begin` — where the session is mid-way through
    # starting a transaction and that call does not do what it looks
    # like. The re-application quietly did nothing, so after the first
    # `db.commit()` in a request the setting was gone and every
    # subsequent query returned zero rows. Signup failed with
    # "Could not refresh instance", which points nowhere near here.
    target = connection if connection is not None else db.connection()

    try:
        # No parameter binding: SET LOCAL will not take one. The value is
        # `str(int)` or the literal '0', so there is nothing to inject —
        # `int()` in `bind_tenant` is what makes that true.
        target.exec_driver_sql(f"SET LOCAL \"{DB_SETTING}\" = '{value}'")
    except Exception as e:                                  # noqa: BLE001
        # ⚠ NOT SWALLOWED SILENTLY.
        # This failing means the database-level wall is off while
        # everything still appears to work — the single most dangerous
        # state this system can be in, and the earlier version of this
        # line hid exactly that. The ORM guard still holds, so the app
        # is not wrong; it is one wall short, and somebody has to know.
        key = str(e)[:60]
        if key not in _warned_once:
            _warned_once.add(key)
            print(f"[tenancy] WARNING: could not set {DB_SETTING} — "
                  f"row-level security is NOT in effect for this session. "
                  f"{type(e).__name__}: {key}")


@event.listens_for(Session, "after_begin")
def _reapply_on_new_transaction(session, transaction, connection):
    if scope_of(session) is not None:
        _push_to_database(session, connection)


def allow_bootstrap_statement(db: Session) -> None:
    """
    Open the database side for the ONE query that decides the tenant.

    ═══ WHY THIS EXISTS ═══
    `get_tenant` has to read a user row to find out which company they
    are in. At that moment the session has no scope — which is exactly
    right, since nothing has decided one yet.

    `execution_options(tenant_bypass=...)` lifts the ORM filter for that
    statement. It does NOT lift the row-level security policy, because
    that lives in Postgres and knows nothing about SQLAlchemy options.
    So with RLS switched on, the bootstrap read returned no row, every
    request became 401, and the system looked like it had lost its
    tokens. Two walls have to be told the same thing.

    The opening is one statement wide: `bind_tenant` runs immediately
    afterwards and replaces the setting with the real company.
    """
    from sqlalchemy import text  # noqa: F401  (used by _push_to_database)

    try:
        db.connection().exec_driver_sql(
            f"SET LOCAL \"{DB_SETTING}\" = '{DB_UNSCOPED}'")
    except Exception:
        # No RLS applied yet — nothing to open, and the ORM guard is
        # unaffected either way.
        pass


def scope_of(db: Session):
    """The company id, an (UNSCOPED, reason) pair, or None for undecided."""
    return db.info.get(TENANT_KEY)


def is_unscoped(scope) -> bool:
    return isinstance(scope, tuple) and scope and scope[0] == UNSCOPED


def _company_id(scope):
    """The int to filter by, or None when this session is not narrowed."""
    return scope if isinstance(scope, int) else None


# ══════════════════════════════════════════════
# Which mappers are tenant data
# ══════════════════════════════════════════════
def is_tenant_mapper(mapper) -> bool:
    try:
        return TENANT_COLUMN in mapper.columns
    except Exception:
        return False


def tenant_classes():
    """Every mapped class carrying a company_id — for the checker."""
    from sqlalchemy.orm import class_mapper  # noqa: F401
    from app.database import Base

    out = []
    for m in Base.registry.mappers:
        if is_tenant_mapper(m):
            out.append(m.class_)
    return sorted(out, key=lambda c: c.__name__)


# ══════════════════════════════════════════════
# Reads, and ORM-issued updates and deletes
# ══════════════════════════════════════════════
@event.listens_for(Session, "do_orm_execute")
def _scope_orm_execute(state):
    """
    Adds the company filter to every ORM statement that touches tenant
    data.

    `with_loader_criteria` is used rather than editing the WHERE clause
    by hand because it follows the entity wherever it appears — joins,
    subqueries, relationship loads and aliases included. Rewriting the
    top-level WHERE would miss a join, and a filter that covers most
    shapes of a query is the kind of guard that reads as working.
    """
    # `session.execute()` on a plain textual or Core statement has no
    # mappers, so there is nothing for this to attach to. Those are
    # covered by the database's own policies, not here.
    mappers = [m for m in state.all_mappers if is_tenant_mapper(m)]
    if not mappers:
        return

    # One statement, opted out in writing. Greppable on purpose.
    if state.execution_options.get("tenant_bypass"):
        return

    scope = scope_of(state.session)

    if is_unscoped(scope):
        return

    if scope is None:
        names = ", ".join(sorted({m.class_.__name__ for m in mappers}))
        raise TenantScopeError(
            f"A query on tenant data ({names}) ran on a session with no "
            f"company. Add `Depends(get_tenant)` to the route, or open the "
            f"session with `open_tenant_session(company_id)`. To read across "
            f"companies on purpose, say so: `bind_unscoped(db, \"why\")`."
        )

    company_id = _company_id(scope)
    for mapper in mappers:
        cls = mapper.class_
        # ═══ THE PLAIN EXPRESSION, NEVER THE LAMBDA FORM ═══
        # SQLAlchemy also accepts `lambda cls: cls.company_id == value`,
        # and that form is faster because the compiled statement is
        # cached. It caches the CLOSURE with it — so the company id from
        # whichever request compiled it first can be reused for the next
        # one. A cached tenant filter is precisely the leak this module
        # exists to stop, and it would show up as one company seeing
        # another's rows only under load. The expression below binds the
        # value as a parameter every time.
        state.statement = state.statement.options(
            with_loader_criteria(
                cls,
                getattr(cls, TENANT_COLUMN) == company_id,
                include_aliases=True,
            )
        )


# ══════════════════════════════════════════════
# Writes
# ══════════════════════════════════════════════
# A filter only protects reading. Without this, a CEO of company A could
# still INSERT a row carrying company B's id, or edit one of their own
# rows to move it across. The read guard would then dutifully hide the
# result from them, which looks like it worked.
@event.listens_for(Session, "before_flush")
def _scope_flush(session, flush_context, instances):
    scope = scope_of(session)

    if is_unscoped(scope):
        return

    touched = {
        o for o in list(session.new) + list(session.dirty) + list(session.deleted)
        if hasattr(type(o), TENANT_COLUMN)
        and is_tenant_mapper(_safe_mapper(o))
    }
    if not touched:
        return

    if scope is None:
        names = ", ".join(sorted({type(o).__name__ for o in touched}))
        raise TenantScopeError(
            f"Writing tenant data ({names}) on a session with no company. "
            f"Open it with `open_tenant_session(company_id)`, or say in "
            f"writing why it may cross companies."
        )

    company_id = _company_id(scope)

    for obj in session.new:
        if obj not in touched:
            continue
        current = getattr(obj, TENANT_COLUMN, None)
        if current is None:
            # Filled in rather than demanded. Every route used to repeat
            # `company_id=ceo.id` by hand, and the one that forgot wrote
            # a row belonging to nobody.
            setattr(obj, TENANT_COLUMN, company_id)
        elif int(current) != int(company_id):
            raise TenantScopeError(
                f"Refused to create a {type(obj).__name__} for company "
                f"{current} from a session scoped to company {company_id}."
            )

    for obj in list(session.dirty) + list(session.deleted):
        if obj not in touched:
            continue
        current = getattr(obj, TENANT_COLUMN, None)
        if current is not None and int(current) != int(company_id):
            raise TenantScopeError(
                f"Refused to modify a {type(obj).__name__} belonging to "
                f"company {current} from a session scoped to company "
                f"{company_id}."
            )
        # A row may not change hands. Moving one would take its history
        # with it — a payslip that is suddenly another company's is a
        # forged record, not a corrected one.
        hist = _history(obj, TENANT_COLUMN)
        if hist is not None and hist.deleted and hist.added:
            was, now = hist.deleted[0], hist.added[0]
            if was is not None and now is not None and int(was) != int(now):
                raise TenantScopeError(
                    f"Refused to move a {type(obj).__name__} from company "
                    f"{was} to company {now}. Rows do not change companies."
                )


def _safe_mapper(obj):
    from sqlalchemy import inspect as sa_inspect
    try:
        return sa_inspect(type(obj))
    except Exception:
        return None


def _history(obj, attr):
    from sqlalchemy import inspect as sa_inspect
    try:
        return sa_inspect(obj).attrs[attr].history
    except Exception:
        return None
