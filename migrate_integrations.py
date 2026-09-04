"""
Per-company Google accounts
───────────────────────────
Creates `company_integrations`, and moves the ONE existing
`app/token.json` into it as the company that has actually been using it.

    py migrate_integrations.py            show what it would do
    py migrate_integrations.py --apply    do it

Idempotent.

═══════════════════════════════════════════════════════════
WHICH COMPANY GETS THE EXISTING TOKEN
═══════════════════════════════════════════════════════════
There is one token on disk and it belongs to whoever set the system up.
Handing it to the wrong company would give them a stranger's mailbox, so
this does not guess: it takes the company that has actually been doing
recruitment (the one with jobs), and if more than one has, it stops and
asks.

Every other company connects its own account from
Settings → Integrations. That is the point of the change.

⚠ THE OLD TOKEN DOES NOT COVER SENDING.
`token.json` was granted `gmail.readonly` and `calendar`. Sending now
goes through the Gmail API, which needs `gmail.send`, and a scope cannot
be added to a token that was never granted it. So even the company that
inherits this token has to press Connect Google once to approve sending —
and the Integrations screen says exactly that rather than letting the
first offer letter fail.
"""

import json
import os
import sys

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database import admin_engine
from app.models.company import Company
from app.models.integration import CompanyIntegration, STATUS_CONNECTED
from app.models.user import User  # noqa: F401

# ⚠ `User` is imported for its side effect, not for its name.
# `company_integrations.connected_by` is a foreign key to `users.id`, and
# SQLAlchemy resolves that against its metadata at CREATE TABLE time. If
# the `users` table has not been defined in this process the create fails
# with NoReferencedTableError — which reads like a database problem and
# is really a missing import.
from app.utils import crypto
from app.utils.tenant_guard import bind_unscoped

APPLY = "--apply" in sys.argv

engine = admin_engine()
_Session = sessionmaker(bind=engine)

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "app", "token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "app",
                                "credentials.json")


def session():
    s = _Session()
    bind_unscoped(s, "migration: per-company Google integrations")
    return s


def say(m=""):
    # The Windows console is cp1252 and cannot encode ⚠ — printing one
    # raises UnicodeEncodeError and kills the migration half way through,
    # which is a spectacular way for a decorative character to break a
    # database change.
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"))


notes = []


def note(m):
    notes.append(m)
    print(f"   !! {m}")


# ══════════════════════════════════════════════
# 1. The table
# ══════════════════════════════════════════════
def step_table():
    say("\n[1] company_integrations table")
    with engine.begin() as conn:
        exists = conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'company_integrations'")).first()
    if exists:
        say("   already there")
        return
    say("   + CREATE TABLE company_integrations")
    if APPLY:
        CompanyIntegration.__table__.create(bind=engine, checkfirst=True)


# ══════════════════════════════════════════════
# 1b. The PKCE columns
# ══════════════════════════════════════════════
def step_pkce_columns():
    """
    `authorization_url()` and `fetch_token()` are two separate requests
    with a trip through Google in between, and PKCE requires the second
    to present the verifier the first generated. A fresh `Flow` in the
    callback has no memory of it, so the very first real connection
    failed with:

        (invalid_grant) Missing code verifier

    It cannot be carried in `state` — that goes through the browser's
    address bar, and being unavailable to whoever intercepts the
    redirect is precisely the property PKCE provides. So it is stored
    here, encrypted, and cleared on use.
    """
    say("\n[1b] PKCE columns on company_integrations")
    with engine.begin() as conn:
        if not conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'company_integrations'")).first():
            say("   (table not created yet)")
            return
        for col, ddl in [("pending_verifier", "BYTEA"),
                         ("pending_started_at", "TIMESTAMP")]:
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'company_integrations' "
                "  AND column_name = :c"), {"c": col}).first()
            if exists:
                continue
            say(f"   + company_integrations.{col}")
            if APPLY:
                conn.execute(text(
                    f"ALTER TABLE company_integrations "
                    f"ADD COLUMN {col} {ddl}"))
        if not APPLY:
            return
        say("   ok")


# ══════════════════════════════════════════════
# 2. The encryption key has to exist first
# ══════════════════════════════════════════════
def step_key():
    say("\n[2] INTEGRATION_SECRET_KEY")
    if crypto.is_configured():
        say("   set — tokens will be encrypted at rest")
        return True

    say("   NOT SET. A Google token is a standing key to a company's "
        "mailbox;")
    say("   it is not written anywhere until this exists. Add to .env:")
    say("")
    say(f"   INTEGRATION_SECRET_KEY={crypto.new_key()}")
    say("")
    say("   ⚠ Keep it. Losing it means every connected company must "
        "reconnect.")
    note("INTEGRATION_SECRET_KEY is missing — the existing token was not "
         "imported. Add the line above to .env and run this again.")
    return False


# ══════════════════════════════════════════════
# 3. Give the existing token to the company using it
# ══════════════════════════════════════════════
def step_import_existing():
    say("\n[3] the existing app/token.json")

    if not os.path.exists(TOKEN_FILE):
        say("   no token.json on disk — every company connects its own")
        return

    # On a dry run the table has not been created, so there is nothing to
    # look in yet. Say so instead of failing on a missing relation.
    with engine.begin() as conn:
        if not conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'company_integrations'"
        )).first():
            say("   (the table does not exist yet — run with --apply to "
                "create it and import)")
            return

    db = session()
    try:
        already = db.query(CompanyIntegration).filter(
            CompanyIntegration.token_encrypted.isnot(None)).count()
        if already:
            say(f"   {already} company(ies) already connected — leaving them")
            return

        # The company that has actually been recruiting is the one whose
        # mailbox this is. Not a guess: no jobs means no applications
        # means this token was never used for them.
        rows = db.execute(text("""
            SELECT c.id, c.name, COUNT(j.id) AS jobs
              FROM companies c LEFT JOIN jobs j ON j.company_id = c.id
             GROUP BY c.id, c.name HAVING COUNT(j.id) > 0
             ORDER BY COUNT(j.id) DESC
        """)).fetchall()

        if not rows:
            note("no company has any jobs, so there is no way to tell whose "
                 "mailbox token.json is. It was NOT imported — connect from "
                 "Settings -> Integrations.")
            return
        if len(rows) > 1:
            note(f"{len(rows)} companies have jobs "
                 f"({', '.join(r.name for r in rows)}) — only you know whose "
                 f"mailbox token.json is. It was NOT imported; connect each "
                 f"company from Settings -> Integrations.")
            return

        target = rows[0]
        raw = open(TOKEN_FILE, encoding="utf-8").read()
        scopes = json.loads(raw).get("scopes", [])

        say(f"   -> company {target.id} ({target.name}) — the only one with "
            f"jobs ({target.jobs})")
        say(f"      granted scopes: {[s.rsplit('/', 1)[-1] for s in scopes]}")

        if "https://www.googleapis.com/auth/gmail.send" not in scopes:
            say("      ⚠ no `gmail.send` — this token can read and use the")
            say("        calendar but CANNOT send. Press Connect Google once")
            say("        to add it; a scope cannot be granted retroactively.")

        if not APPLY:
            return

        db.add(CompanyIntegration(
            company_id=target.id,
            provider="google",
            token_encrypted=crypto.encrypt(raw),
            granted_scopes=" ".join(scopes),
            status=STATUS_CONNECTED,
            last_error=(
                None
                if "https://www.googleapis.com/auth/gmail.send" in scopes
                else "Imported from the old shared token.json. Sending mail "
                     "was never authorised for it — press Connect Google to "
                     "add that permission."
            ),
        ))
        db.commit()
        say("      imported and encrypted")
    finally:
        db.close()


# ══════════════════════════════════════════════
# 4. The same two walls as every other table
# ══════════════════════════════════════════════
def step_rls():
    say("\n[4] row-level security on the new table")
    with engine.begin() as conn:
        r = conn.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'company_integrations'")).first()
        pol = conn.execute(text(
            "SELECT 1 FROM pg_policies WHERE tablename = "
            "'company_integrations' AND policyname = 'tenant_isolation'"
        )).first()

        if r and r[0] and r[1] and pol:
            say("   already protected")
            return

        say("   + ENABLE + FORCE + POLICY")
        if not APPLY:
            return

        cond = ("(company_id = NULLIF(current_setting('agentra.company_id', "
                "true), '')::int OR current_setting('agentra.company_id', "
                "true) = '0')")
        conn.execute(text("ALTER TABLE company_integrations "
                          "ENABLE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE company_integrations "
                          "FORCE ROW LEVEL SECURITY"))
        conn.execute(text(
            f"CREATE POLICY tenant_isolation ON company_integrations "
            f"FOR ALL TO PUBLIC USING {cond} WITH CHECK {cond}"))
        conn.execute(text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON company_integrations "
            "TO agentra_app"))


# ══════════════════════════════════════════════
# 5. The old shared files
# ══════════════════════════════════════════════
def step_old_files():
    say("\n[5] the old shared files")
    say(f"   app/credentials.json  KEEP — it is the OAuth CLIENT, shared by")
    say(f"                         every company by design; it identifies the")
    say(f"                         application to Google, not an account")
    if os.path.exists(TOKEN_FILE):
        say(f"   app/token.json        one account's standing access. Once the")
        say(f"                         company above has reconnected, delete it")
        say(f"                         — nothing reads it any more.")
        note("app/token.json still on disk. Nothing reads it now; remove it "
             "once the import is confirmed, and make sure it is gitignored.")


def step_verify():
    say("\n[6] verify")
    db = session()
    try:
        rows = db.query(CompanyIntegration).all()
        companies = {c.id: c.name for c in db.query(Company).all()}
        say(f"   {len(rows)} integration row(s)")
        for r in rows:
            enc = "encrypted" if r.token_encrypted else "no token"
            readable = "readable" if (
                r.token_encrypted and crypto.decrypt(r.token_encrypted)
            ) else ("UNREADABLE" if r.token_encrypted else "-")
            say(f"      company {r.company_id} "
                f"({companies.get(r.company_id, '?')}): {r.status} · {enc} "
                f"· {readable}")
            if readable == "UNREADABLE":
                note(f"company {r.company_id}'s token cannot be decrypted — "
                     f"INTEGRATION_SECRET_KEY has changed. They must "
                     f"reconnect.")
    finally:
        db.close()


def main():
    say("=" * 66)
    say("  PER-COMPANY GOOGLE" + ("  [APPLY]" if APPLY else "  [dry run]"))
    say("=" * 66)

    step_table()
    step_pkce_columns()
    have_key = step_key()
    if have_key:
        step_import_existing()
    step_rls()
    step_old_files()
    if have_key:
        step_verify()

    say("\n" + "=" * 66)
    if notes:
        say(f"  {len(notes)} thing(s) need your attention:")
        for n in notes:
            say(f"   - {n}")
    else:
        say("  clean")
    if not APPLY:
        say("\n  dry run — nothing was written. Re-run with --apply")
    say("=" * 66)


if __name__ == "__main__":
    main()
