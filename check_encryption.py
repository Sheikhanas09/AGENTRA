"""
Encryption at rest — jo attacker ko milega, wo kya hai
──────────────────────────────────────────────────────
    py check_encryption.py

CEO ka sawal yeh tha: _"in case hmari db leak ho jaye ya hack ho jaye tw
attacker sb kuch dekh skta hai"_.

To yeh suite theek wohi karti hai: **raw SQL se rows parhti hai — jaisa
ek dump ya SQL injection dekhta hai — aur poochti hai ke matn wahan
maujood hai ya nahi.** ORM se parhna is sawal ka jawab nahi de sakta,
kyunke ORM to khud hi decrypt kar deta hai; wo "encrypted hai" ka jawab
hamesha haan dega chahe kuch bhi na ho.

Har check us surat mein FAIL hota hai jab column plain text par wapas
chala jaye.

Rows banayi aur `finally` mein hata di jati hain.
"""
import warnings

warnings.filterwarnings("ignore")

import os                                                     # noqa: E402
import secrets                                                # noqa: E402

from sqlalchemy import text as sql                            # noqa: E402

from app.models.chat import (                                 # noqa: E402
    ChatMessage, ChatSession, HrCase, HrRequest)
from app.models.user import User                              # noqa: E402
from app.utils.crypto import CHAT_ENV_NAME, cipher_for        # noqa: E402
from app.utils.encrypted_column import MARKER                 # noqa: E402
from app.utils.tenancy import unscoped_session                # noqa: E402

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


# The words a real transcript contains. If any of these turn up in the
# raw column, the encryption is not on.
SECRET = f"salary 28571 grievance about Zeeshan {secrets.token_hex(4)}"
TITLE = f"Why was my August salary zero {secrets.token_hex(3)}"

db = unscoped_session("check_encryption")
made = []

try:
    user = db.query(User).filter(User.company_id.isnot(None)).first()
    if not user:
        print("  no user to attach a session to")
        raise SystemExit(1)

    # ══════════════════════════════════════════════
    # 1. The key
    # ══════════════════════════════════════════════
    head("1. The key")
    check(f"{CHAT_ENV_NAME} is set", bool(os.getenv(CHAT_ENV_NAME, "").strip()))
    check("...and it is a usable Fernet key",
          cipher_for(CHAT_ENV_NAME, "probe") is not None)
    # Two secrets, two lifetimes. Sharing one value would mean rotating
    # the Google key also destroys every transcript.
    check("...and it is NOT the same value as INTEGRATION_SECRET_KEY",
          os.getenv(CHAT_ENV_NAME, "x") != os.getenv("INTEGRATION_SECRET_KEY", "y"))
    check("...and it is NOT the same value as SECRET_KEY",
          os.getenv(CHAT_ENV_NAME, "x") != os.getenv("SECRET_KEY", "y"))

    # ══════════════════════════════════════════════
    # 2. What a database dump actually contains
    # ══════════════════════════════════════════════
    head("2. What a dump contains")

    s = ChatSession(employee_id=user.id, company_id=user.company_id,
                    kind="employee", title=TITLE)
    db.add(s)
    db.flush()
    made.append(s)
    m = ChatMessage(session_id=s.id, company_id=user.company_id,
                    role="employee", text=SECRET)
    db.add(m)
    db.flush()
    made.append(m)
    db.commit()

    # ⚠ RAW SQL, NOT THE ORM.
    # The ORM decrypts on the way out, so asking it whether the data is
    # encrypted is asking the wrong witness. This is the bytes.
    raw_text = db.execute(
        sql("SELECT text FROM chat_messages WHERE id = :i"), {"i": m.id}
    ).scalar()
    raw_title = db.execute(
        sql("SELECT title FROM chat_sessions WHERE id = :i"), {"i": s.id}
    ).scalar()

    check("the message is not stored as the text that was written",
          raw_text != SECRET)
    check("...and no word of it survives in the column",
          not any(w in raw_text.lower()
                  for w in ("salary", "grievance", "zeeshan", "28571")),
          raw_text[:40] + "...")
    check("the title is not stored as written", raw_title != TITLE)
    check("...and no word of it survives either",
          not any(w in raw_title.lower() for w in ("salary", "august", "zero")))
    check("both carry the version marker",
          raw_text.startswith(MARKER) and raw_title.startswith(MARKER),
          MARKER)

    # ══════════════════════════════════════════════
    # 2b. An HR case, including its JSON
    # ══════════════════════════════════════════════
    # `facts` is a dict, not a string, so it takes a different column
    # type and gets its own round trip. Encrypting the case's subject
    # while leaving the answers inside `facts` readable would have been
    # the same mistake as encrypting messages and leaving the titles.
    head("2b. An HR case, and the JSON inside it")

    FACTS = {"what happened with Zeeshan?": "he shouted at me",
             "Which date?": "September 5"}
    NEEDED = ["Was anyone else present?"]

    req = HrRequest(company_id=user.company_id, employee_id=user.id,
                    kind="complaint", source="chat", status="open",
                    subject="Issue with Zeeshan",
                    body="he does not talk to me professionally",
                    ceo_note="not approved")
    db.add(req)
    db.flush()
    made.append(req)
    case = HrCase(company_id=user.company_id, employee_id=user.id,
                  concern="grievance", posture="confidential",
                  stage="gathering", subject="Issue with Zeeshan",
                  facts=FACTS, still_needed=NEEDED)
    db.add(case)
    db.flush()
    made.append(case)
    db.commit()

    raw_req = db.execute(
        sql("SELECT subject, body, ceo_note FROM hr_requests WHERE id = :i"),
        {"i": req.id}).first()
    raw_case = db.execute(
        sql("SELECT subject, facts::text, still_needed::text "
            "FROM hr_cases WHERE id = :i"), {"i": case.id}).first()

    check("the request's subject, body and note are all encrypted",
          all(str(v).startswith(MARKER) for v in raw_req),
          str(raw_req[0])[:34] + "...")
    check("...and 'Zeeshan' appears in none of them",
          not any("zeeshan" in str(v).lower() for v in raw_req))
    check("the case's facts are encrypted too",
          MARKER in str(raw_case[1]), str(raw_case[1])[:40] + "...")
    check("...and the employee's own words are not in the column",
          not any(w in str(raw_case[1]).lower()
                  for w in ("shouted", "zeeshan", "september")))
    check("still_needed is encrypted", MARKER in str(raw_case[2]))

    db.expire_all()
    c2 = db.query(HrCase).filter(HrCase.id == case.id).first()
    r2 = db.query(HrRequest).filter(HrRequest.id == req.id).first()
    check("the facts come back as the same dict", c2.facts == FACTS,
          str(c2.facts)[:50])
    check("still_needed comes back as the same list",
          c2.still_needed == NEEDED)
    check("the request's body comes back intact",
          r2.body == "he does not talk to me professionally")

    # The label columns must NOT have been swept up: queries filter on
    # them, and encrypting one would make those queries quietly empty.
    check("the case's `concern` is still queryable plain text",
          db.query(HrCase).filter(HrCase.concern == "grievance",
                                  HrCase.id == case.id).first() is not None)
    check("the request's `status` is still queryable plain text",
          db.query(HrRequest).filter(HrRequest.status == "open",
                                     HrRequest.id == req.id).first() is not None)

    # ══════════════════════════════════════════════
    # 3. And it still reads back
    # ══════════════════════════════════════════════
    # Encryption that loses the data is not a feature.
    head("3. The application still reads it")
    db.expire_all()
    m2 = db.query(ChatMessage).filter(ChatMessage.id == m.id).first()
    s2 = db.query(ChatSession).filter(ChatSession.id == s.id).first()
    check("the message comes back exactly as written", m2.text == SECRET)
    check("the title comes back exactly as written", s2.title == TITLE)

    # ══════════════════════════════════════════════
    # 4. Nothing left in plain text
    # ══════════════════════════════════════════════
    # The backfill is the whole point: the rows that would leak are the
    # ones already in the table, not the ones written from now on.
    head("4. Every row in the database, not just the new one")
    for table, col in (("chat_messages", "text"), ("chat_sessions", "title"),
                       ("hr_requests", "subject"), ("hr_requests", "body"),
                       ("hr_requests", "ceo_note"), ("hr_cases", "subject"),
                       ("hr_cases", "facts"), ("hr_cases", "still_needed")):
        rows = db.execute(
            sql(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
        ).fetchall()
        plain = [i for i, v in rows if not str(v).startswith(MARKER)]
        check(f"all {len(rows)} {table}.{col} rows are encrypted",
              not plain, f"plain: {plain[:5]}" if plain else "")

    # ══════════════════════════════════════════════
    # 5. Nothing may search these columns
    # ══════════════════════════════════════════════
    # This is the standing cost of the choice, so it is asserted rather
    # than remembered. `WHERE text LIKE '%salary%'` matches ciphertext
    # and quietly returns nothing — a bug that looks like "no results".
    head("5. No query filters or sorts on the encrypted columns")
    import pathlib
    import re

    ENCRYPTED_ATTRS = (
        "ChatMessage.text", "ChatSession.title",
        "HrRequest.subject", "HrRequest.body", "HrRequest.ceo_note",
        "HrCase.subject", "HrCase.facts", "HrCase.still_needed",
    )
    # ⚠ LABEL COLUMNS YAHAN JAAN-BOOJH KAR NAHI HAIN.
    # `HrCase.concern`, `.posture`, `.stage` aur `HrRequest.status`,
    # `.kind`, `.source` par queries filter karti hain — misal
    # `chat_cases.py:116` `concern` par. Unhein encrypt karna is check ko
    # pass to kara deta, aur wo queries chup-chaap khali natija deti
    # rehtin. Isi liye audit pehle kiya gaya tha: data ne dikhaya ke
    # `concern` mein 11 rows par sirf 4 alag values hain — wo matn nahi,
    # category hai.
    offenders = []
    pattern = re.compile(
        r"(" + "|".join(a.replace(".", r"\.") for a in ENCRYPTED_ATTRS) + r")\s*"
        r"(\.(like|ilike|contains|startswith|endswith|in_)\b|[=!<>]=)")
    for path in pathlib.Path("app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for n, line in enumerate(src.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{n}")
    check("no ORM filter compares against them", not offenders,
          ", ".join(offenders[:3]))

    # ⚠ COMMENTS AUR DOCSTRINGS HATA KAR DEKHO.
    # Pehli koshish ne poori file par grep kiya aur khud
    # `encrypted_column.py` ko pakad liya — us ke docstring mein
    # `WHERE text LIKE '%salary%'` likha hai, misal ke tor par, theek
    # is qanoon ko samjhane ke liye. Jo check apni hi documentation ko
    # khilaf-warzi samjhe wo har run par shor machayega aur log usay
    # nazarandaz karna seekh jayenge.
    import tokenize

    raw_sql = []
    for path in pathlib.Path("app").rglob("*.py"):
        code = []
        try:
            with tokenize.open(path) as fh:
                prev = None
                for tok in tokenize.generate_tokens(fh.readline):
                    if tok.type == tokenize.COMMENT:
                        continue
                    # Docstring = apne bayan mein akela string
                    if (tok.type == tokenize.STRING
                            and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                         tokenize.NL, None)):
                        continue
                    if tok.type == tokenize.STRING:
                        code.append(tok.string)
                    elif tok.type == tokenize.NAME:
                        code.append(tok.string)
                    if tok.type not in (tokenize.NL, tokenize.COMMENT):
                        prev = tok.type
        except Exception:                                   # noqa: BLE001
            code = [path.read_text(encoding="utf-8")]
        blob = " ".join(code).lower()
        for frag in ("where text like", "where title like",
                     "where subject like", "where body like",
                     "order by text", "order by title", "order by subject",
                     # JSON ke andar jhankna bhi ab ciphertext parhega:
                     # `facts->>'x'` chup-chaap kuch nahi dega.
                     "facts->", "still_needed->"):
            if frag in blob:
                raw_sql.append(f"{path}: {frag}")
    check("no raw SQL searches them", not raw_sql, ", ".join(raw_sql[:3]))

    # ══════════════════════════════════════════════
    # 6. A wrong key does not look like an empty message
    # ══════════════════════════════════════════════
    # A lost key makes rows unreadable forever — that is inherent. What
    # must NOT happen is it coming back as "" , because a blank message
    # reads as "they sent nothing" rather than "this cannot be read".
    head("6. A lost key says so")
    from cryptography.fernet import Fernet

    real = os.environ.get(CHAT_ENV_NAME)
    os.environ[CHAT_ENV_NAME] = Fernet.generate_key().decode()
    try:
        db.expire_all()
        m3 = db.query(ChatMessage).filter(ChatMessage.id == m.id).first()
        got = m3.text
    finally:
        if real is not None:
            os.environ[CHAT_ENV_NAME] = real
    check("a wrong key does not return the plaintext", got != SECRET)
    check("...and does not return an empty string", bool(got and got.strip()))
    check("...it says the message cannot be decrypted",
          "cannot be decrypted" in got.lower(), got[:60])

    db.expire_all()
    back = db.query(ChatMessage).filter(ChatMessage.id == m.id).first()
    check("the right key still reads it afterwards", back.text == SECRET)

finally:
    try:
        # Children before parents.
        for cls in (ChatMessage, ChatSession, HrCase, HrRequest):
            for obj in made:
                if not isinstance(obj, cls):
                    continue
                try:
                    db.delete(obj)
                    db.flush()
                except Exception as e:                      # noqa: BLE001
                    db.rollback()
                    print(f"  left behind {cls.__name__}: "
                          f"{str(e).splitlines()[0][:80]}")
        db.commit()
    except Exception as e:                                  # noqa: BLE001
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
