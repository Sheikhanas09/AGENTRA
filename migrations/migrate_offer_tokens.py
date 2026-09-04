"""
Secure offer links
──────────────────
    py migrations/migrate_offer_tokens.py            show what it would do
    py migrations/migrate_offer_tokens.py --apply    do it

Adds the token columns to `applications` and reports which offers are
currently open on the OLD link shape, because those are the ones that
stop working.

═══════════════════════════════════════════════════════════
WHY EXISTING OFFERS ARE NOT SILENTLY MIGRATED
═══════════════════════════════════════════════════════════
It is technically easy to mint a token for every `hired` application
here. It would also be useless and misleading: the token's only purpose
is to be in the email, and those emails have already been sent carrying
the old link. Minting one would produce a URL nobody has.

So this reports them instead. The CEO re-sends the offer from the
dashboard, which issues a token and emails the new link — one action,
and the candidate gets a working link rather than a dead one.
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
from app.models.recruitment import Application  # noqa: F401  (metadata)
from app.models.user import User                # noqa: F401  (FK target)

APPLY = "--apply" in sys.argv
engine = admin_engine()

COLUMNS = [
    ("offer_token_hash", "VARCHAR(64)"),
    ("offer_token_expires_at", "TIMESTAMP"),
    ("offer_token_used_at", "TIMESTAMP"),
]


def say(m=""):
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"))


def main():
    say("=" * 66)
    say("  SECURE OFFER LINKS" + ("  [APPLY]" if APPLY else "  [dry run]"))
    say("=" * 66)

    say("\n[1] columns on `applications`")
    with engine.begin() as conn:
        for col, ddl in COLUMNS:
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'applications' AND column_name = :c"
            ), {"c": col}).first()
            if exists:
                say(f"   = applications.{col} already there")
                continue
            say(f"   + applications.{col}")
            if APPLY:
                conn.execute(text(
                    f"ALTER TABLE applications ADD COLUMN {col} {ddl}"))

        # The redemption path looks a row up by this digest on every
        # click, so it needs to be indexed — and the index is also what
        # keeps the lookup constant-ish rather than a scan whose timing
        # could hint at a match.
        idx = conn.execute(text(
            "SELECT 1 FROM pg_indexes "
            "WHERE indexname = 'ix_applications_offer_token'")).first()
        if idx:
            say("   = index ix_applications_offer_token already there")
        else:
            say("   + INDEX ix_applications_offer_token")
            if APPLY:
                conn.execute(text(
                    "CREATE INDEX ix_applications_offer_token "
                    "ON applications (offer_token_hash)"))

    say("\n[2] offers currently open on the old link shape")
    with engine.begin() as conn:
        try:
            rows = conn.execute(text("""
                SELECT a.id, a.company_id, c.full_name, c.email, j.title
                  FROM applications a
                  LEFT JOIN candidates c ON c.id = a.candidate_id
                  LEFT JOIN jobs j ON j.id = a.job_id
                 WHERE a.status = 'hired'
                 ORDER BY a.id
            """)).fetchall()
        except Exception:
            rows = []

    if not rows:
        say("   none — no offer is waiting to be accepted")
    else:
        say(f"   {len(rows)} offer(s) were sent with the OLD link, which no")
        say("   longer works. Re-send each from the dashboard to issue a")
        say("   secure link:")
        for r in rows:
            say(f"      application {r.id}  company {r.company_id}  "
                f"{r.full_name or '?'} <{r.email or '?'}>  — {r.title or '?'}")

    say("\n[3] the old route")
    say("   GET /recruitment/accept-offer/{application_id}")
    say("   kept, and inert. A candidate opening an old email is told the")
    say("   link expired instead of getting a 404 — and honouring the id")
    say("   even once would leave the hole open.")

    say("\n" + "=" * 66)
    if not APPLY:
        say("  dry run — nothing was written. Re-run with --apply")
    else:
        say("  done. Run: py tests/check_offer_token.py")
    say("=" * 66)


if __name__ == "__main__":
    main()
