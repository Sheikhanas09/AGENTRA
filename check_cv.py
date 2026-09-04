"""
The CV, the name, and the count
───────────────────────────────
    py check_cv.py

Three things went wrong on one screen, reported together:

    "11 fetch ki hai … but show 2 ho rhi hai"
    "name mai wise tech aa rha hai, name nh candidate ka"
    "view cv py click krta tha tw kbhi sai cv show hoti thi kbhi ghlt"

They are three separate bugs on the same path, so they get one suite.

No mailbox and no model are touched: the fetch and the screener are
replaced at the seam so the checks are deterministic and free. What is
real is the database, the routes and the PDF parsing.

Rows are created and removed in `finally`.
"""
import warnings

warnings.filterwarnings("ignore")

import secrets                                                   # noqa: E402

import fitz                                                      # noqa: E402
from fastapi.testclient import TestClient                        # noqa: E402

from app.main import app                                         # noqa: E402
from app.agents.gmail_agent import (                             # noqa: E402
    extract_name_from_cv, extract_name_from_pdf, _looks_like_a_person)
from app.models.company import Company, STATUS_ACTIVE, normalise_name  # noqa: E402
from app.models.recruitment import Application, Candidate, Job    # noqa: E402
from app.models.user import User                                  # noqa: E402
from app.utils.security import create_access_token                # noqa: E402
from app.utils.tenancy import open_unscoped_session               # noqa: E402

client = TestClient(app, raise_server_exceptions=False)
fails = []


def check(label, ok, extra=""):
    if not ok:
        fails.append(f"{label}  {extra}")
    line = f"  {'[ok]  ' if ok else '[FAIL]'} {label}   {extra}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def head(t):
    print(f"\n{t}\n{'-' * len(t)}")


def make_pdf(big, small_lines):
    """
    A CV shaped like the ones that broke: the candidate's name set
    large, with an employer and a job title that come out FIRST in the
    flattened reading order. That is the two-column case the old
    extractor lost on.
    """
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in small_lines:
        page.insert_text((72, y), line, fontsize=10)
        y += 16
    page.insert_text((72, y + 30), big, fontsize=22)
    out = doc.tobytes()
    doc.close()
    return out


# ══════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════
db = open_unscoped_session("check_cv: fixtures").__enter__()
made = []

try:
    company = db.query(Company).filter(
        Company.status == STATUS_ACTIVE).order_by(Company.id).first()
    if not company:
        # A suite that skips itself is a suite that passes.
        nm = f"Cvprobe {secrets.token_hex(3).upper()}"
        company = Company(name=nm, slug=normalise_name(nm),
                          status=STATUS_ACTIVE)
        db.add(company)
        db.flush()
        made.append(company)
        print(f"  (built company {company.id} - none was active)")

    ceo = db.query(User).filter(User.company_id == company.id,
                                User.role == "ceo").first()
    if not ceo:
        print("  no CEO in that company; cannot exercise the routes")
        raise SystemExit(1)

    hdr = {"Authorization": "Bearer " + create_access_token(
        {"user_id": ceo.id, "role": ceo.role, "email": ceo.email,
         "company_id": company.id})}

    print(f"fixtures: company {company.id} ({company.name}), ceo {ceo.id}")

    # ══════════════════════════════════════════════
    # 1. Whose name is on the CV
    # ══════════════════════════════════════════════
    head("1. The name is the candidate's, not their employer's")

    EMPLOYER, PERSON = "Wise Tech", "Muhammad Anas"
    pdf = make_pdf(PERSON, ["Wise Tech", "Full Stack Developer",
                            "Karachi Pakistan"])
    flat = ""
    d = fitz.open(stream=pdf, filetype="pdf")
    for pg in d:
        flat += pg.get_text()
    d.close()

    # The premise, asserted rather than assumed: the employer really
    # does come first in the flattened text. If a PyMuPDF upgrade
    # changed that, the checks below would start passing for the wrong
    # reason and nobody would know.
    order = [ln.strip() for ln in flat.split("\n") if ln.strip()]
    check("the employer really does come before the name in the text",
          order.index(EMPLOYER) < order.index(PERSON), str(order[:3]))

    got = extract_name_from_cv(flat, "fallback", pdf_bytes=pdf)
    check("the extractor returns the person", got == PERSON, repr(got))
    check("...and not the employer", got != EMPLOYER, repr(got))

    check("the PDF route alone finds it", extract_name_from_pdf(pdf) == PERSON)
    check("a missing PDF falls back rather than raising",
          extract_name_from_cv("", "Sender Name", pdf_bytes=None)
          == "Sender Name")
    check("unreadable bytes fall back rather than raising",
          extract_name_from_cv("", "Sender Name", pdf_bytes=b"not a pdf")
          == "Sender Name")

    check("a job title under the name is not mistaken for it",
          not _looks_like_a_person("Full Stack Developer"))
    check("a real two-word name still reads as one",
          _looks_like_a_person("Muhammad Anas"))

    # Every CV already in the database - real documents, not the
    # synthetic one above.
    stored = db.query(Candidate).filter(Candidate.cv_pdf.isnot(None)).all()
    if stored:
        bad = [c for c in stored if not _looks_like_a_person(c.full_name or "")]
        check(f"all {len(stored)} stored candidates are filed under a "
              f"name-shaped name", not bad,
              ", ".join(f"{c.id}:{c.full_name!r}" for c in bad[:4]))

    # ══════════════════════════════════════════════
    # 2. Each application keeps its own CV
    # ══════════════════════════════════════════════
    head("2. Two roles, one person, two different CVs")

    job1 = Job(company_id=company.id, title="CV Probe Role One",
               company_name=company.name, status="draft")
    job2 = Job(company_id=company.id, title="CV Probe Role Two",
               company_name=company.name, status="draft")
    db.add_all([job1, job2])
    db.flush()
    made += [job1, job2]

    pdf1 = make_pdf(PERSON, ["APPLYING FOR ROLE ONE"])
    pdf2 = make_pdf(PERSON, ["APPLYING FOR ROLE TWO"])
    check("the two CVs are genuinely different documents", pdf1 != pdf2)

    cand = Candidate(company_id=company.id, full_name=PERSON,
                     email=f"cvprobe.{secrets.token_hex(4)}@example.test",
                     cv_pdf=pdf2, cv_text="role two")   # the LATEST only
    db.add(cand)
    db.flush()
    made.append(cand)

    a1 = Application(company_id=company.id, candidate_id=cand.id,
                     job_id=job1.id, status="screened",
                     cv_pdf=pdf1, cv_text="role one", cv_filename="one.pdf")
    a2 = Application(company_id=company.id, candidate_id=cand.id,
                     job_id=job2.id, status="screened",
                     cv_pdf=pdf2, cv_text="role two", cv_filename="two.pdf")
    db.add_all([a1, a2])
    db.flush()
    made += [a1, a2]
    db.commit()

    # ⚠ THE WHOLE POINT OF THE FIX.
    # Reading `candidate.cv_pdf`, which is what the route did, returns
    # pdf2 for BOTH applications. This pair is exactly the reported bug.
    r1 = client.get(f"/recruitment/download-cv/{a1.id}", headers=hdr)
    r2 = client.get(f"/recruitment/download-cv/{a2.id}", headers=hdr)
    check("both downloads succeed",
          r1.status_code == 200 and r2.status_code == 200,
          f"{r1.status_code}/{r2.status_code}")
    check("application one returns the CV sent for role one",
          r1.content == pdf1, f"{len(r1.content)} bytes")
    check("application two returns the CV sent for role two",
          r2.content == pdf2, f"{len(r2.content)} bytes")
    check("the two downloads are not the same document",
          r1.content != r2.content)

    # The list beside the button has to agree with the button.
    l1 = client.get(f"/recruitment/applications/{job1.id}", headers=hdr).json()
    l2 = client.get(f"/recruitment/applications/{job2.id}", headers=hdr).json()
    row1 = next(a for a in l1["applications"] if a["application_id"] == a1.id)
    row2 = next(a for a in l2["applications"] if a["application_id"] == a2.id)
    check("the list shows role one's filename on role one",
          row1["cv_filename"] == "one.pdf", str(row1["cv_filename"]))
    check("the list shows role two's filename on role two",
          row2["cv_filename"] == "two.pdf", str(row2["cv_filename"]))
    check("the list text matches the downloaded document too",
          row1["cv_text"] == "role one" and row2["cv_text"] == "role two")

    # Rows written before the column existed must still open.
    a1.cv_pdf = None
    a1.cv_text = None
    db.commit()
    r_old = client.get(f"/recruitment/download-cv/{a1.id}", headers=hdr)
    check("an application with no CV of its own falls back to the candidate",
          r_old.status_code == 200 and r_old.content == pdf2,
          f"{r_old.status_code}, {len(r_old.content)} bytes")

    # ══════════════════════════════════════════════
    # 3. Applicants, not messages
    # ══════════════════════════════════════════════
    head("3. Eleven emails from two people are two applicants")

    import app.agents.gmail_agent as ga
    import app.agents.cv_screening_agent as sa

    people = [f"one.{secrets.token_hex(3)}@example.test",
              f"two.{secrets.token_hex(3)}@example.test"]
    fake = []
    for i in range(11):
        fake.append({"email": people[i % 2], "name": PERSON,
                     "subject": "Application for CV Probe Role One",
                     "cv_text": f"message {i}", "cv_pdf": pdf1,
                     "cv_filename": "cv.pdf", "message_id": f"m{i}"})

    real_fetch = ga.fetch_job_application_emails
    real_screen = sa.screen_cv
    calls = []

    def fake_fetch(*a, **k):
        return list(fake)

    def fake_screen(**k):
        calls.append(k.get("candidate_email"))
        return {"match_score": 50.0, "skill_gap": "-", "summary": "probe"}

    ga.fetch_job_application_emails = fake_fetch
    sa.screen_cv = fake_screen
    try:
        r = client.post(f"/recruitment/fetch-and-screen/{job1.id}", headers=hdr)
        body = r.json() if r.status_code == 200 else {}
        check("the fetch succeeds", r.status_code == 200,
              f"{r.status_code} {str(body)[:90]}")
        check("all 11 messages are reported as read",
              body.get("emails_found") == 11, str(body.get("emails_found")))
        check("...but 'Fetched' counts the 2 applicants",
              body.get("total_fetched") == 2, str(body.get("total_fetched")))
        check("...and the 9 extras are reported, not hidden",
              body.get("duplicates_skipped") == 9,
              str(body.get("duplicates_skipped")))
        check("screening ran once per applicant, not once per message",
              body.get("screened") == 2, str(body.get("screened")))
        # The counter could be right while the work is still done twice.
        check("the screener itself was called twice, not eleven times",
              len(calls) == 2, f"{len(calls)} calls")

        lst = client.get(f"/recruitment/applications/{job1.id}",
                         headers=hdr).json()
        got_rows = [a for a in lst["applications"]
                    if (a.get("email") or "") in people]
        check("the list underneath shows exactly those 2",
              len(got_rows) == 2, f"{len(got_rows)} rows")
        check("the panel's count and the list's count agree",
              body.get("total_fetched") == len(got_rows),
              f"panel {body.get('total_fetched')}, list {len(got_rows)}")

        for row in got_rows:
            made.append(db.query(Application).filter(
                Application.id == row["application_id"]).first())
            made.append(db.query(Candidate).filter(
                Candidate.id == row["candidate_id"]).first())

        # The application rows the fetch wrote must carry their own CV,
        # not depend on the candidate's copy.
        own = [db.query(Application).filter(
            Application.id == row["application_id"]).first()
            for row in got_rows]
        check("a freshly fetched application stores its own CV",
              all(a.cv_pdf for a in own),
              f"{sum(1 for a in own if a.cv_pdf)}/{len(own)}")

        # ── the name is refreshed on a RE-fetch ──
        # The update branch never touched `full_name`, so a candidate
        # filed under a wrong name stayed under it forever.
        victim = db.query(Candidate).filter(
            Candidate.email == people[0]).first()
        victim.full_name = EMPLOYER
        db.commit()
        client.post(f"/recruitment/fetch-and-screen/{job1.id}", headers=hdr)
        db.refresh(victim)
        check("a re-fetch corrects a wrongly-stored name",
              victim.full_name == PERSON, repr(victim.full_name))

        # ...but not into something worse.
        victim.full_name = PERSON
        db.commit()
        for f in fake:
            f["name"] = "bunnyhazel9001"          # the email-prefix fallback
        client.post(f"/recruitment/fetch-and-screen/{job1.id}", headers=hdr)
        db.refresh(victim)
        check("a re-fetch does NOT replace a real name with an address stub",
              victim.full_name == PERSON, repr(victim.full_name))
    finally:
        ga.fetch_job_application_emails = real_fetch
        sa.screen_cv = real_screen

    # ══════════════════════════════════════════════
    # 4. The seam the bug actually crossed
    # ══════════════════════════════════════════════
    # Section 1 tests the extractor and section 3 tests the route, but
    # the bug lived BETWEEN them: `fetch_job_application_emails` called
    # the extractor without handing it the PDF, so the fixed extractor
    # would have gone on returning "Wise Tech" in production while
    # section 1 passed. Nothing above would have noticed.
    #
    # So this drives the real function through a stand-in Gmail service
    # - the smallest object the Google client shape needs - and reads
    # the name it comes back with.
    head("4. The real fetch, with a stand-in mailbox")

    import base64

    class _Att:
        def get(self, userId=None, messageId=None, id=None):
            return self
        def execute(self):
            return {"data": base64.urlsafe_b64encode(pdf).decode()}

    class _Msgs:
        def attachments(self):
            return _Att()
        def list(self, userId=None, q=None, maxResults=None):
            self._mode = "list"
            return self
        def get(self, userId=None, id=None, format=None):
            self._mode = "get"
            return self
        def execute(self):
            if self._mode == "list":
                return {"messages": [{"id": "msg1"}]}
            return {"payload": {
                "headers": [
                    {"name": "From", "value": "Bunny H <bunny@example.test>"},
                    {"name": "Subject", "value": "Application for X"}],
                "parts": [{"filename": "cv.pdf",
                           "body": {"attachmentId": "att1"}}]}}

    class _Users:
        def messages(self):
            return _Msgs()

    class _Service:
        def users(self):
            return _Users()

    real_service = ga.get_gmail_service
    ga.get_gmail_service = lambda *a, **k: _Service()
    try:
        out = ga.fetch_job_application_emails(db, company.id, "X")
        check("the fetch reads one application out of the mailbox",
              len(out) == 1, f"{len(out)}")
        if out:
            check("the name it files is the candidate's",
                  out[0]["name"] == PERSON, repr(out[0]["name"]))
            check("...not the employer at the top of the CV",
                  out[0]["name"] != EMPLOYER, repr(out[0]["name"]))
            check("...and not the sender's display name either",
                  out[0]["name"] != "Bunny H", repr(out[0]["name"]))
            check("the PDF bytes are carried through to be stored",
                  out[0]["cv_pdf"] == pdf, f"{len(out[0]['cv_pdf'])} bytes")
    finally:
        ga.get_gmail_service = real_service

finally:
    # ══════════════════════════════════════════════
    # Teardown
    # ══════════════════════════════════════════════
    # REVERSE ORDER IS NOT DEPENDENCY ORDER.
    # check_offer_token deletes `reversed(made)` and that works there
    # only because its list happens to be built parent-first. This one
    # is not: the fetch section appends an Application and then the
    # Candidate it belongs to, so reversing put the candidate first and
    # the foreign key refused it.
    #
    # Worse, the `except` around each delete called `db.rollback()`,
    # which throws away every delete already flushed in the same
    # transaction. One late failure quietly undid the whole teardown,
    # and each run left three candidates, two jobs and four
    # applications behind. Twenty-one strays had accumulated in a real
    # company's data before this was noticed.
    #
    # So: children first, explicitly, and a failure is reported rather
    # than swallowed.
    ORDER = [Application, Candidate, Job, Company]
    try:
        for cls in ORDER:
            for obj in made:
                if obj is None or not isinstance(obj, cls):
                    continue
                try:
                    db.delete(obj)
                    db.flush()
                except Exception as e:                          # noqa: BLE001
                    db.rollback()
                    print(f"  left behind {cls.__name__} "
                          f"{getattr(obj, 'id', '?')}: "
                          f"{str(e).splitlines()[0][:90]}")
        db.commit()
    except Exception as e:                                      # noqa: BLE001
        db.rollback()
        print(f"\n  could not clean up: {e}")
    db.close()

print()
if fails:
    print(f"{len(fails)} FAILURE(S):")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("All checks passed.")
