"""
Secrets at rest
───────────────
A company's Google refresh token is not a password — it is worse. A
password gets you in until somebody changes it; this gets you into that
company's mailbox and calendar, silently, until it is revoked. Several
of them sitting in a database column in plain text is one `SELECT` away
from every tenant's email.

So they are encrypted with a key that is NOT in the database. Somebody
who gets a copy of the dump gets ciphertext.

═══════════════════════════════════════════════════════════
THE KEY IS SEPARATE FROM `SECRET_KEY` ON PURPOSE
═══════════════════════════════════════════════════════════
Reusing the JWT secret would look tidy and would mean that rotating it —
which the security notes already tell you to do, and which merely logs
everybody out — would ALSO make every stored Google token permanently
unreadable. Two secrets with two different lifetimes should not share
one value.

═══════════════════════════════════════════════════════════
NO KEY MEANS NO STORING, NOT PLAIN TEXT
═══════════════════════════════════════════════════════════
The tempting fallback is "if the key is missing, store it as-is and warn".
That produces a database with a mix of encrypted and plain rows, and the
warning scrolls past once. `encrypt()` raises instead, so connecting an
account without a key fails loudly and nothing sensitive is written.
"""

import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()

ENV_NAME = "INTEGRATION_SECRET_KEY"

# ══════════════════════════════════════════════
# MORE THAN ONE SECRET, FOR THE SAME REASON AS BEFORE
# ══════════════════════════════════════════════
# The note above explains why the Google key is not `SECRET_KEY`. The
# same argument applies again the moment a second kind of secret shows
# up: chat transcripts are encrypted with `CHAT_SECRET_KEY`, not with
# this one.
#
# Sharing one value would mean losing it costs BOTH — every company has
# to reconnect Google AND every transcript ever written becomes
# unreadable. Two things with different lifetimes and different blast
# radii do not share a key.
CHAT_ENV_NAME = "CHAT_SECRET_KEY"

_key = os.getenv(ENV_NAME, "").strip()


class SecretsNotConfigured(RuntimeError):
    """No encryption key, so nothing sensitive may be written."""


def is_configured() -> bool:
    return bool(_key)


def new_key() -> str:
    """A fresh key, for printing into `.env`. Never stored by this code."""
    return Fernet.generate_key().decode()


def cipher_for(env_name: str, what: str) -> Fernet:
    """
    The cipher for one named key.

    Each secret is read from the environment at call time rather than
    cached at import, so a key added to `.env` after the module loaded
    still works — which is what happens when somebody follows the error
    message below and restarts nothing.
    """
    key = os.getenv(env_name, "").strip()
    if not key:
        raise SecretsNotConfigured(
            f"{env_name} is not set in .env, so {what} cannot be stored "
            f"safely. Generate one with:\n"
            f"    py -c \"from app.utils.crypto import new_key; "
            f"print(new_key())\"\n"
            f"and put it in .env as {env_name}=<value>.\n"
            # Plain ASCII on purpose. This message is printed on a
            # Windows console more often than not, and cp1252 raises
            # UnicodeEncodeError on the arrow and warning glyphs — an
            # error about a missing key that itself crashes while being
            # displayed is not a helpful error.
            f"Keep it - losing it means the data encrypted with it "
            f"cannot be read again."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise SecretsNotConfigured(
            f"{env_name} is not a valid Fernet key ({e}). It must be the "
            f"44-character value produced by `new_key()`."
        ) from e


def is_chat_configured() -> bool:
    return bool(os.getenv(CHAT_ENV_NAME, "").strip())


def _cipher() -> Fernet:
    if not _key:
        raise SecretsNotConfigured(
            f"{ENV_NAME} is not set in .env, so a Google token cannot be "
            f"stored safely. Generate one with:\n"
            f"    py -c \"from app.utils.crypto import new_key; "
            f"print(new_key())\"\n"
            f"and put it in .env as {ENV_NAME}=<value>.\n"
            f"⚠ Keep it — losing it means every connected company has to "
            f"reconnect Google."
        )
    try:
        return Fernet(_key.encode())
    except (ValueError, TypeError) as e:
        raise SecretsNotConfigured(
            f"{ENV_NAME} is not a valid Fernet key ({e}). It must be the "
            f"44-character value produced by `new_key()`."
        ) from e


def encrypt(plaintext: str) -> bytes:
    if plaintext is None:
        return None
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt(blob) -> str:
    """
    Returns None when the value cannot be read.

    A key that has been rotated or lost makes every stored token
    undecryptable. That is not a crash — it is "this company is no
    longer connected", which is exactly what the caller should show, so
    the CEO can reconnect instead of seeing a stack trace.
    """
    if not blob:
        return None
    try:
        return _cipher().decrypt(bytes(blob)).decode("utf-8")
    except (InvalidToken, SecretsNotConfigured, ValueError, TypeError):
        return None
