"""
The CV belongs to the application, not to the person
────────────────────────────────────────────────────
    py migrate_application_cv.py            show what it would do
    py migrate_application_cv.py --apply    do it

Adds `cv_text` / `cv_pdf` / `cv_filename` to `applications` and backfills
them from the candidate each application points at.

═══════════════════════════════════════════════════════════
WHY THIS MOVED
═══════════════════════════════════════════════════════════
The CV lived only on `candidates`, one row per person per company, and
`fetch-and-screen` overwrote it every time that address turned up again:

    existing_candidate.cv_pdf = app_data.get('cv_pdf')

So one person applying for two roles had ONE stored CV — the last one
read — and both applications displayed it. "View CV" showed the right
document or the wrong one depending on the order the mailbox happened to
be read in. That is exactly how it was reported: sometimes correct,
sometimes not.

It is also a scoring problem, not only a display one. `match_score` was
computed from the CV that arrived with that application; once the bytes
behind it are replaced, the score no longer refers to anything you can
open.

═══════════════════════════════════════════════════════════
WHAT THE BACKFILL CAN AND CANNOT DO
═══════════════════════════════════════════════════════════
It copies the candidate's current CV onto every application of theirs.
For anybody with one application that is exactly right. For anybody with
several, every one of them gets the same document — because that is all
that survived; the earlier CVs were overwritten and are gone.

The backfill does not invent history. It stops the CV from moving from
today on, and existing rows are as correct as the data allows. The next
fetch stores each application's own CV.
"""

import sys

from sqlalchemy import text

from app.database import admin_engine
from app.models.recruitment import Application, Candidate  # noqa: F401
from app.models.user import User                           # noqa: F401

APPLY = "--apply" in sys.argv
engine = admin_engine()

COLUMNS = [
    ("cv_text", "TEXT"),
    ("cv_pdf", "BYTEA"),
    ("cv_filename", "VARCHAR"),
]


def say(m=""):
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode())


def main():
    say("=" * 66)
    say("  The CV moves from the candidate to the application")
    say("=" * 66)
    say(f"  mode: {'APPLY' if APPLY else 'DRY RUN (use --apply)'}")
    say()

    with engine.begin() as conn:
        have = {r[0] for r in conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'applications'
        """))}

        # ── 1. the columns ──
        say("1. columns on `applications`")
        for name, ddl in COLUMNS:
            if name in have:
                say(f"   = {name:12} already there")
                continue
            if APPLY:
                conn.execute(text(
                    f"ALTER TABLE applications ADD COLUMN {name} {ddl}"))
                say(f"   + {name:12} added ({ddl})")
            else:
                say(f"   ~ {name:12} would add ({ddl})")
        say()

        if not APPLY and not all(n in have for n, _ in COLUMNS):
            say("2. backfill — cannot count until the columns exist")
            say()
            say("   Nothing was changed. Re-run with --apply.")
            return

        # ── 2. what the backfill will touch ──
        say("2. backfill from the candidate")

        # Only rows that have nothing yet, so re-running is harmless and
        # a CV already stored on an application is never overwritten by
        # the candidate's newer one.
        pending = conn.execute(text("""
            SELECT count(*) FROM applications a
            JOIN candidates c ON c.id = a.candidate_id
            WHERE a.cv_pdf IS NULL AND a.cv_text IS NULL
              AND (c.cv_pdf IS NOT NULL OR c.cv_text IS NOT NULL)
        """)).scalar()
        say(f"   {pending} application(s) have no CV and their candidate has one")

        # The ones the overwrite actually damaged, named so they can be
        # looked at rather than assumed.
        shared = conn.execute(text("""
            SELECT c.id, c.email, count(a.id) AS n
            FROM candidates c JOIN applications a ON a.candidate_id = c.id
            GROUP BY c.id, c.email HAVING count(a.id) > 1
            ORDER BY n DESC, c.id
        """)).fetchall()
        if shared:
            say()
            say(f"   {len(shared)} candidate(s) have more than one application.")
            say("   Each gets the one CV that survived — the earlier ones")
            say("   were overwritten before this change and are not")
            say("   recoverable:")
            for cid, email, n in shared[:15]:
                say(f"     candidate {cid:4}  {email:38} {n} applications")
        else:
            say("   No candidate has more than one application, so the")
            say("   backfill is exact for every row.")
        say()

        if APPLY:
            done = conn.execute(text("""
                UPDATE applications a SET
                    cv_text     = c.cv_text,
                    cv_pdf      = c.cv_pdf,
                    cv_filename = c.cv_filename
                FROM candidates c
                WHERE c.id = a.candidate_id
                  AND a.cv_pdf IS NULL AND a.cv_text IS NULL
                  AND (c.cv_pdf IS NOT NULL OR c.cv_text IS NOT NULL)
            """)).rowcount
            say(f"   {done} application(s) backfilled")
            say()

            # ── 3. what it looks like now ──
            say("3. after")
            total, withcv = conn.execute(text("""
                SELECT count(*), count(*) FILTER (WHERE cv_pdf IS NOT NULL)
                FROM applications
            """)).first()
            say(f"   {withcv}/{total} application(s) now carry their own PDF")
            orphan = conn.execute(text("""
                SELECT count(*) FROM applications a
                JOIN candidates c ON c.id = a.candidate_id
                WHERE a.cv_pdf IS NULL AND c.cv_pdf IS NOT NULL
            """)).scalar()
            if orphan:
                say(f"   ⚠ {orphan} still empty while the candidate has one")
            say()

            # ── 4. names the old extractor got wrong ──
            # Same fetch path, same trip. The name was read off the
            # flattened CV text and the first name-shaped line won,
            # which on a two-column CV is often the EMPLOYER:
            #
            #     line 10   'Wise Tech'       <- taken as the name
            #     line 27   'MUHAMMAD ANAS'   <- the actual name
            #
            # The extractor asks the PDF's typography now. Re-running
            # it over the CVs already stored fixes the rows in place —
            # no mailbox, no model, no re-screening, and the scores
            # underneath are untouched.
            say("4. names re-read from the stored PDFs")
            from app.agents.gmail_agent import extract_name_from_pdf
            rows = conn.execute(text("""
                SELECT id, full_name, cv_pdf FROM candidates
                WHERE cv_pdf IS NOT NULL
            """)).fetchall()
            fixed = 0
            for cid, name, pdf in rows:
                better = extract_name_from_pdf(bytes(pdf))
                if not better or better == name:
                    continue
                conn.execute(
                    text("UPDATE candidates SET full_name = :n WHERE id = :i"),
                    {"n": better, "i": cid})
                say(f"   candidate {cid:4}  {name!r} -> {better!r}")
                fixed += 1
            if fixed:
                say(f"   {fixed} name(s) corrected out of {len(rows)} stored CV(s)")
            else:
                say(f"   {len(rows)} stored CV(s) checked, all names already agree")
            say()

            say("   Done. `/download-cv` reads the application's copy and")
            say("   falls back to the candidate's for anything older.")
        else:
            say("   DRY RUN — nothing was written. Re-run with --apply.")


if __name__ == "__main__":
    main()
