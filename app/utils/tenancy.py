"""
Who is asking, and which company they are asking about
──────────────────────────────────────────────────────
One place decides the tenant for a request. Nothing else is allowed to
work it out for itself, because when two pieces of code answer "which
company is this?" separately, one of them is eventually wrong and it is
never the one anybody is looking at.

═══════════════════════════════════════════════════════════
WHY THE TENANT IS CARRIED ON THE SESSION, NOT A CONTEXTVAR
═══════════════════════════════════════════════════════════
The obvious way is a ContextVar set by a dependency. It does not work
here, and it fails silently, which is worse than not working.

FastAPI runs `def` (non-async) endpoints and dependencies in a THREAD
POOL. `anyio` copies the current context into the worker thread, so a
value written inside that thread lands in the copy and is thrown away
when it returns. The endpoint then runs in a different worker with a
different copy. The tenant would be set, and then simply not be there —
and an "unset tenant" that nobody notices is exactly a leak.

The Session is the honest carrier. It is created once per request, it is
the thing that actually runs the queries, and FastAPI caches
`Depends(get_db)` so the route and this module share one instance. So
the tenant is stamped on `session.info`, and the guard in
`utils/tenant_guard.py` reads it from the very session it is about to
run a query on. No propagation, nothing to lose.

═══════════════════════════════════════════════════════════
THE DEFAULT IS REFUSAL
═══════════════════════════════════════════════════════════
A session with no tenant stamped on it does not quietly return every
company's rows. The guard raises. That turns "somebody forgot the
dependency on a new route" from a silent cross-tenant leak into a loud
500 on the first request.

Reading across companies is therefore never something that just happens;
it has to be written down:

    Depends(require_superadmin)   the superadmin, who has no company
    Depends(public_scope)         the public job portal
    open_unscoped_session(reason) background jobs and scripts

Each of those records a reason, and `check_tenancy.py` lists every one
of them so the set stays small and reviewed.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models.company import Company, LIVE_STATUSES
from app.models.user import User
from app.utils.security import get_current_user

# ──── The mechanism lives next door ────
# `tenant_guard` is the machinery (the SQLAlchemy hooks) and imports
# nothing from the app, so `database.py` can switch it on without an
# import cycle. This file is the policy: who gets which scope, and why.
from app.utils.tenant_guard import (  # noqa: F401  (re-exported on purpose)
    TENANT_KEY, UNSCOPED, TenantScopeError,
    bind_tenant, bind_unscoped, scope_of, allow_bootstrap_statement,
)

# ──── The one query that cannot be scoped ────
# Finding out which company somebody is in has to read their user row
# BEFORE the answer is known. Spelled out so `check_tenancy.py` can list
# it among the exceptions rather than it being an invisible special case.
_BOOTSTRAP = "tenancy: reading the user row that decides the tenant"


@dataclass(frozen=True)
class Tenant:
    """
    The answer, frozen. A route cannot widen its own scope after the
    fact, because there is nothing on here to reassign.
    """

    company_id: int
    company_name: str
    user_id: int
    role: str

    @property
    def is_ceo(self) -> bool:
        return self.role == "ceo"

    @property
    def is_employee(self) -> bool:
        return self.role == "employee"

    # ══════════════════════════════════════════════
    # Reads like the dict it replaces
    # ══════════════════════════════════════════════
    # Sixty routes were written against `current_user["user_id"]` from
    # the old `require_ceo`. Rewriting sixty signatures AND sixty bodies
    # in the same change would make it impossible to tell a tenancy bug
    # from a typo, so the old shape still answers.
    #
    # This is a transition shim and is meant to shrink: new code should
    # take `tenant: Tenant` and use `tenant.company_id`. What it must
    # never do is hand back something that could be mistaken for a
    # company — hence `["company_id"]` is the company, and the CEO's user
    # id is only ever `["user_id"]`. Those were the same number before
    # this change and are not any more.
    def __getitem__(self, key: str):
        if key in ("user_id", "role", "company_id", "company_name"):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default


# ══════════════════════════════════════════════
# The request path
# ══════════════════════════════════════════════
def _bootstrap_user(db: Session, user_id: int) -> Optional[User]:
    """
    Read one user row while the session still has no company.

    `User` carries `company_id`, so the guard would normally refuse this
    query — correctly, since nothing has decided a scope yet. This is the
    single genuine chicken-and-egg in the system, and it is opted out of
    for ONE statement rather than by loosening the session, so nothing
    else on this request inherits the exemption.

    ⚠ BOTH WALLS HAVE TO BE OPENED, FOR THE SAME ONE STATEMENT.
    `tenant_bypass` lifts the ORM filter. It does nothing to the Postgres
    policy, which has never heard of SQLAlchemy options — so with
    row-level security switched on this read returned no row, `get_tenant`
    raised, and EVERY request became 401 including the legitimate ones.
    It looked exactly like the tokens had broken.
    """
    allow_bootstrap_statement(db)
    return (
        db.query(User)
        .execution_options(tenant_bypass=_BOOTSTRAP)
        .filter(User.id == user_id)
        .first()
    )


def _load_company(db: Session, company_id: int) -> Company:
    # ⚠ THE SESSION IS ALREADY BOUND WHEN THIS RUNS.
    # It used to run BEFORE `bind_tenant`, which was fine while
    # `companies` had no row-level security: the ORM guard ignores it
    # (no `company_id` column) and Postgres had no policy. The moment a
    # policy was added, this read returned nothing on an unscoped
    # session, `get_tenant` raised, and EVERY authenticated request
    # became a 403.
    #
    # Binding first is also simply better — it shortens the window in
    # which the session has no scope rather than lengthening it. The id
    # comes from the user's own row, so there is nothing to decide here
    # that the binding could get wrong.
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(
            status_code=403,
            detail="Your account is not linked to a company. Please contact "
                   "your administrator.",
        )
    return company


def get_tenant(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> Tenant:
    """
    The dependency almost every route wants.

    Four things are checked, and the order is the point:

      1. the user still exists
      2. the user has a company
      3. THE TOKEN'S COMPANY STILL MATCHES THE USER'S COMPANY
      4. that company is live

    (3) is what makes an old token safe. The token carries `company_id`
    so the common path needs no extra thought, but it is never TRUSTED —
    it is compared against the database row. A token minted before
    somebody was moved, or one edited by hand, disagrees with the row and
    is refused rather than being used to reach the old company.

    (4) is what makes suspension real. A company being switched off has
    to end the sessions already open, not just block the next login —
    otherwise "suspended" means "suspended tomorrow".
    """
    user = _bootstrap_user(db, current_user["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="Token invalid or expired")

    if user.status == "fired":
        raise HTTPException(
            status_code=403, detail="Your account has been deactivated.")

    if not user.company_id:
        # The superadmin belongs to no company on purpose and must not
        # arrive here — they use `require_superadmin`.
        raise HTTPException(
            status_code=403,
            detail="This account is not linked to a company.",
        )

    token_company = current_user.get("company_id")
    if token_company is not None and int(token_company) != int(user.company_id):
        raise HTTPException(
            status_code=401,
            detail="Your session is out of date. Please sign in again.",
        )

    # Bound BEFORE the company row is read — see `_load_company`. The id
    # comes from the user's own row, which was just verified against the
    # token, so binding to it commits to nothing that has not already
    # been checked.
    bind_tenant(db, user.company_id)

    company = _load_company(db, user.company_id)
    if company.status not in LIVE_STATUSES:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{company.name} is not active"
                + (f" — {company.suspended_reason}"
                   if company.suspended_reason else "")
                + ". Please contact your administrator."
            ),
        )

    return Tenant(
        company_id=company.id,
        company_name=company.name,
        user_id=user.id,
        role=user.role,
    )


def require_ceo(tenant: Tenant = Depends(get_tenant)) -> Tenant:
    """
    The CEO OF THIS COMPANY.

    ═══ THE OLD `require_ceo` ONLY CHECKED THE JOB TITLE ═══
    It asked "is this user a CEO?" and nothing more. Being a CEO of some
    company is not permission over another one, but every route that
    trusted it behaved as though it were: `DELETE /jobs/{id}`,
    `GET /applications/{id}`, `hire`, `reject`, `shortlist` all took an
    id straight from the URL and acted on it.

    Now the answer carries the company with it, and the guard in
    `tenant_guard.py` makes every query underneath obey it. A CEO asking
    for another company's job gets a 404 — not because the route checked,
    but because that row is not in their world at all.
    """
    if not tenant.is_ceo:
        raise HTTPException(
            status_code=403, detail="Only the CEO can do this")
    return tenant


def require_employee(tenant: Tenant = Depends(get_tenant)) -> Tenant:
    if not tenant.is_employee:
        raise HTTPException(
            status_code=403, detail="This is for employees only")
    return tenant


# ══════════════════════════════════════════════
# The written-down exceptions
# ══════════════════════════════════════════════
def require_superadmin(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """
    The platform operator. Belongs to no company and can see them all —
    that is the job: approving new companies, suspending one.

    The role is re-read from the database rather than taken from the
    token, because this is the one identity for which a forged claim
    would open everything.
    """
    # Widened FIRST, then read. The other order would need the bootstrap
    # exemption for a query that is about to be allowed everything
    # anyway, and two ways of saying the same thing is how one of them
    # ends up wrong.
    bind_unscoped(db, "superadmin: platform administration")
    user = db.query(User).filter(User.id == current_user["user_id"]).first()
    if not user or user.role != "superadmin":
        raise HTTPException(
            status_code=403, detail="Only the Super Admin can do this")
    return {"user_id": user.id, "role": user.role}


def public_scope(db: Session = Depends(get_db)) -> None:
    """
    For the routes that have no signed-in user and are meant to be
    public: the job portal, and the link a candidate clicks to accept an
    offer.

    These genuinely span companies — a job board that showed only one
    company's roles would not be a job board. What they may read is
    still narrow, and each route says so itself; this only stops the
    guard from refusing a session that has no tenant.
    """
    bind_unscoped(db, "public: unauthenticated job portal / offer link")


def auth_scope(db: Session = Depends(get_db)) -> None:
    """
    Sign-up and sign-in, which have to find a user BEFORE anybody knows
    which company they are in. The lookup is by email and by nothing
    else.
    """
    bind_unscoped(db, "auth: login and signup, before a tenant is known")


# ══════════════════════════════════════════════
# Outside a request: schedulers, scripts, background tasks
# ══════════════════════════════════════════════
class _SessionCM:
    """A session that closes itself, with its scope already stamped."""

    def __init__(self, binder):
        self._binder = binder
        self._db: Optional[Session] = None

    def __enter__(self) -> Session:
        self._db = SessionLocal()
        self._binder(self._db)
        return self._db

    def __exit__(self, *exc):
        if self._db is not None:
            self._db.close()
        return False


def open_tenant_session(company_id: int) -> _SessionCM:
    """
    A session for background work on ONE company.

        with open_tenant_session(company_id) as db:
            ...

    The monthly payroll job, the leave sweeps and the proactive HR checks
    all loop over companies. Each pass gets its own scoped session, so a
    query written without a `company_id` filter inside that loop is
    still confined to the company the loop is on.
    """
    return _SessionCM(lambda db: bind_tenant(db, company_id))


def open_unscoped_session(reason: str) -> _SessionCM:
    """
    A session that may read across companies, for the one thing that
    legitimately has to: finding out WHICH companies exist before
    looping over them.

    The work inside the loop belongs in `open_tenant_session`. This is
    for the list, not for the work.
    """
    return _SessionCM(lambda db: bind_unscoped(db, reason))


def unscoped_session(reason: str) -> Session:
    """
    A plain session that may cross companies. The caller closes it.

    For the one-off scripts — `check_scope.py`, `check_console.py`,
    `regenerate_payslips.py` and the rest. They audit or repair the whole
    database, so crossing companies is their entire job; what they must
    not do is cross by accident, which is why they say so here and turn
    up in the list `check_tenancy.py` prints.

    Application code should use `open_tenant_session` instead — if a
    piece of code knows which company it is working on, it should say so
    and get the protection.
    """
    db = SessionLocal()
    bind_unscoped(db, reason)
    return db


def tenant_session(company_id: int) -> Session:
    """A plain session limited to one company. The caller closes it."""
    db = SessionLocal()
    bind_tenant(db, company_id)
    return db


def live_company_ids(db: Session) -> list:
    """
    Every company a background job should act on.

    ═══ THE SCHEDULER USED TO DISCOVER COMPANIES FROM DATA ═══
    It took its list from `DISTINCT company_id` on `leave_requests` and
    on `salary_structures`. A company that had just signed up had
    neither, so its payroll never ran and its leave was never swept —
    and nothing reported that, because from the job's point of view the
    company did not exist.

    The list of companies now comes from the companies table, which is
    the only place that knows.
    """
    rows = db.query(Company.id).filter(
        Company.status.in_(LIVE_STATUSES)).order_by(Company.id).all()
    return [r[0] for r in rows]
