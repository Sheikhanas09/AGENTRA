"""
A column that is encrypted in the database and plain in Python
──────────────────────────────────────────────────────────────
    text = Column(EncryptedText)

Everything that already reads `message.text` keeps working. Nothing at
a call site has to remember to encrypt or decrypt.

═══════════════════════════════════════════════════════════
WHY A COLUMN TYPE AND NOT `encrypt()` AT EACH CALL SITE
═══════════════════════════════════════════════════════════
The other option was to call `crypto.encrypt()` where a message is
written and `crypto.decrypt()` where one is read — six read sites and
six write sites today, and however many the next feature adds.

That is the same shape as the problem the tenant guard exists to solve:
405 queries of which 170 never mentioned `company_id`, safe only until
somebody edited a line above them. A rule that every call site must
remember is a rule that gets forgotten, and forgetting THIS one does not
raise — it writes a transcript to the database in plain text, which is
precisely the thing being prevented, and it looks like it worked.

So the rule moves into the column. SQLAlchemy encrypts on the way down
and decrypts on the way up, and a future route that writes a message
gets it right without knowing this file exists.

═══════════════════════════════════════════════════════════
WHY THE COLUMN STAYS `Text` AND NOT `LargeBinary`
═══════════════════════════════════════════════════════════
Fernet already returns url-safe base64 ASCII, so it fits a text column
as it is. That means no column-type migration, no rewriting of the
table, and — the part that matters — rows written before this existed
are still readable, because they are still just text.

Encrypted values carry a marker:

    enc:v1:gAAAAABm...

Anything without it is a row from before, and is returned as-is. That is
what lets the backfill run while the app is up, and what makes it safe
to run twice.

⚠ WHAT THIS DOES NOT DO
It defends the data AT REST: a database dump, a stolen backup, a SQL
injection that reads rows, a hosting provider or DBA with table access.
It does NOT defend against an attacker who has the server itself, because
the key is in `.env` next to it. And it is NOT end-to-end encryption —
the application decrypts every message it serves, and the help desk still
sends the plaintext to the model provider to get an answer.

⚠ AND YOU CANNOT SEARCH IT
`WHERE text LIKE '%salary%'` matches ciphertext and finds nothing. It is
used on columns that nothing filters, sorts or searches on, and
`check_encryption.py` asserts that no query does.
"""

import json

from sqlalchemy import JSON, Text
from sqlalchemy.types import TypeDecorator

from app.utils.crypto import SecretsNotConfigured, cipher_for

# Bumped only if the scheme changes. Old rows keep their own marker, so
# a future v2 can decrypt v1 rather than orphaning it.
MARKER = "enc:v1:"


class EncryptedText(TypeDecorator):
    """Encrypted in Postgres, plain `str` in Python."""

    impl = Text
    cache_ok = True

    def __init__(self, env_name: str, what: str, *args, **kwargs):
        # The key is named per column rather than global: transcripts and
        # Google tokens have different lifetimes, so they do not share a
        # secret. See the note at the top of `crypto.py`.
        self.env_name = env_name
        self.what = what
        super().__init__(*args, **kwargs)

    # ──── Python → database ────
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        if value.startswith(MARKER):
            # Already encrypted — the backfill writes ciphertext straight
            # in, and re-encrypting it would bury it a layer deeper.
            return value
        # ⚠ NO KEY MEANS NO WRITING, NOT PLAIN TEXT.
        # `crypto.py` takes the same line for the same reason: a
        # fallback that stores it unencrypted and warns produces a table
        # with a mixture in it, and the warning scrolls past once.
        return MARKER + cipher_for(self.env_name, self.what).encrypt(
            value.encode("utf-8")).decode("ascii")

    # ──── Database → Python ────
    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if not value.startswith(MARKER):
            # Written before this column type existed. Returned as it is
            # so the app keeps working during the backfill instead of
            # showing every old message as unreadable.
            return value
        token = value[len(MARKER):]
        try:
            return cipher_for(self.env_name, self.what).decrypt(
                token.encode("ascii")).decode("utf-8")
        except (SecretsNotConfigured, Exception):       # noqa: B014
            # A lost or rotated key makes this row unreadable forever.
            # That is a fact to show the reader, not a stack trace in the
            # middle of a conversation — and NOT silence either, because
            # a blank message looks like the message was blank.
            return "[this message cannot be decrypted — CHAT_SECRET_KEY " \
                   "has changed or is missing]"


class EncryptedJSON(TypeDecorator):
    """
    An encrypted dict/list. Plain Python object either side.

    ═══ WHY THE COLUMN STAYS `json` ═══
    `hr_cases.facts` holds what the employee actually said, keyed by the
    question they were asked:

        {"am i absent yesterday?": "yes", "Which date?": "September 5"}

    That is a transcript by another name, so it is encrypted for the
    same reason the messages are.

    The ciphertext is stored as a JSON *string* — `"enc:v1:gAAA..."` is
    itself valid JSON — so the column type does not change and no
    `ALTER TABLE` is needed. Postgres still sees well-formed JSON; it
    just sees a string where an object used to be.

    ⚠ NOTHING MAY QUERY INSIDE THESE COLUMNS ANY MORE.
    `facts->>'x'` would read into the ciphertext. Checked before this
    was applied: every use loads the row and works on the object in
    Python, and no SQL reaches inside. `check_encryption.py` holds that
    line.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, env_name: str, what: str, *args, **kwargs):
        self.env_name = env_name
        self.what = what
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str) and value.startswith(MARKER):
            return value
        blob = json.dumps(value, default=str)
        return MARKER + cipher_for(self.env_name, self.what).encrypt(
            blob.encode("utf-8")).decode("ascii")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, str) or not value.startswith(MARKER):
            # Written before this existed — already a dict or list.
            return value
        try:
            raw = cipher_for(self.env_name, self.what).decrypt(
                value[len(MARKER):].encode("ascii")).decode("utf-8")
            return json.loads(raw)
        except Exception:                                   # noqa: BLE001
            # Unlike a message, there is no sentence to show here — this
            # feeds `case.facts.get(...)`. An empty container is the only
            # shape the callers can survive, and the message column will
            # be saying plainly that the key is wrong.
            return None


def encrypted_chat_text():
    """The chat columns' type, so the key name is written in one place."""
    from app.utils.crypto import CHAT_ENV_NAME
    return EncryptedText(CHAT_ENV_NAME, "a chat transcript")


def encrypted_chat_json():
    """Same key, for the JSON columns on an HR case."""
    from app.utils.crypto import CHAT_ENV_NAME
    return EncryptedJSON(CHAT_ENV_NAME, "an HR case")
