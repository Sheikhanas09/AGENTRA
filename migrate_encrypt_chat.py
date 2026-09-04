"""
Chat transcripts ko encrypt karna — jo pehle se para hai
────────────────────────────────────────────────────────
    py migrate_encrypt_chat.py            dekho kya karega
    py migrate_encrypt_chat.py --apply    kar do

`chat_messages.text` aur `chat_sessions.title` ab `EncryptedText` hain,
to AAJ SE likha hua har message encrypted jata hai. Yeh script us se
pehle likhi hui rows ke liye hai — kyunke jo data leak par khula hai
wohi to hai jo abhi database mein para hai.

═══════════════════════════════════════════════════════════
YEH RAW SQL SE KAAM KARTA HAI, ORM SE NAHI
═══════════════════════════════════════════════════════════
Column ab khud encrypt/decrypt karta hai. Us ka matlab yeh hai ke ORM
se `m.text` parhne par plain text milega (purani row) aur likhne par wo
encrypt ho jayega — jo theek lagta hai, magar us mein ek phanda hai: agar
script dobara chali to wo pehle se encrypted qeemat ko DOBARA encrypt
kar sakti thi.

Isliye yahan seedha SQL hai. Har row ka asli mehfooz byte parha jata
hai, marker dekha jata hai, aur sirf wo rows chhui jati hain jin par
marker nahi hai. Dobara chalana be-zarar hai.

═══════════════════════════════════════════════════════════
KEY KHO GAYI TO KYA
═══════════════════════════════════════════════════════════
`CHAT_SECRET_KEY` ke baghair yeh rows kabhi wapas nahi parhi ja saktin.
Backup ki tajweez neeche di gayi hai aur --apply se pehle poochi jati
hai. Yeh dhamki nahi, hisab hai: encryption ka poora nuqta hi yeh hai ke
key ke baghair data bekar ho.
"""

import json
import sys

from sqlalchemy import text

from app.database import admin_engine
from app.utils.crypto import CHAT_ENV_NAME, SecretsNotConfigured, cipher_for
from app.utils.encrypted_column import MARKER

APPLY = "--apply" in sys.argv
engine = admin_engine()

# (table, id column, column, is_json)
#
# Sirf free text. Labels — `hr_requests.status/kind/source`,
# `hr_cases.concern/posture/stage` — jaan-boojh kar bahar hain: un par
# queries filter karti hain (misal `chat_cases.py:116` concern par), aur
# encrypt karne se wo chup-chaap khali natija dene lagtin.
TARGETS = [
    ("chat_messages", "id", "text", False),
    ("chat_sessions", "id", "title", False),
    ("hr_requests", "id", "subject", False),
    ("hr_requests", "id", "body", False),
    ("hr_requests", "id", "ceo_note", False),
    ("hr_cases", "id", "subject", False),
    # JSON columns. Ciphertext ek JSON *string* ke tor par likha jata hai
    # (`"enc:v1:..."` khud valid JSON hai), to column ka type nahi
    # badalta aur koi ALTER TABLE nahi chahiye.
    ("hr_cases", "id", "facts", True),
    ("hr_cases", "id", "still_needed", True),
]


def say(m=""):
    try:
        print(m)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode())


def main():
    say("=" * 66)
    say("  Chat transcripts — encryption at rest")
    say("=" * 66)
    say(f"  mode: {'APPLY' if APPLY else 'DRY RUN (use --apply)'}")
    say()

    # ── 1. key ──
    say("1. key")
    try:
        cipher = cipher_for(CHAT_ENV_NAME, "a chat transcript")
        say(f"   {CHAT_ENV_NAME} set hai aur valid Fernet key hai")
    except SecretsNotConfigured as e:
        say(f"   {e}")
        raise SystemExit(1)
    say()

    with engine.begin() as conn:
        # ── 2. kitna kaam hai ──
        say("2. abhi kya halat hai")
        work = {}
        for table, idcol, col, is_json in TARGETS:
            rows = conn.execute(text(
                f"SELECT {idcol}, {col} FROM {table} WHERE {col} IS NOT NULL"
            )).fetchall()
            plain = [(i, v) for i, v in rows
                     if not (isinstance(v, str) and v.startswith(MARKER))]
            done = len(rows) - len(plain)
            work[f"{table}.{col}"] = (table, col, is_json, plain)
            say(f"   {table + '.' + col:24} {len(rows):>4} rows   "
                f"{done:>4} encrypted   {len(plain):>4} plain")
        say()

        total = sum(len(w[3]) for w in work.values())
        if total == 0:
            say("   Sab kuch pehle se encrypted hai. Kuch karne ko nahi.")
            return

        if not APPLY:
            say("3. jo badla jayega (namoona)")
            for key, (table, col, is_json, plain) in work.items():
                for i, v in plain[:2]:
                    txt = " ".join(str(v).split())[:50]
                    say(f"   {key} id={i:<5} {txt!r}")
            say()
            say(f"   DRY RUN — {total} row(s) badli jatin. Kuch nahi likha.")
            say()
            say("   ⚠ --apply se pehle database ka backup le lijiye.")
            say(f"   ⚠ Aur {CHAT_ENV_NAME} ko mehfooz rakhiye — us ke baghair")
            say("     yeh rows dobara kabhi nahi parhi ja sakengi.")
            return

        # ── 3. karo ──
        say("3. encrypt")
        for key, (table, col, is_json, plain) in work.items():
            n = 0
            for i, v in plain:
                # JSON column ka raw value pehle se Python object hai
                # (psycopg deserialize kar deta hai), to usay wapas
                # string banao — aur likhte waqt JSON string ke tor par.
                blob = json.dumps(v, default=str) if is_json else str(v)
                token = MARKER + cipher.encrypt(
                    blob.encode("utf-8")).decode("ascii")
                stored = json.dumps(token) if is_json else token
                conn.execute(
                    text(f"UPDATE {table} SET {col} = :t WHERE id = :i"),
                    {"t": stored, "i": i})
                n += 1
            say(f"   {key:24} {n} row(s) encrypted")
        say()

        # ── 4. tasdeeq ──
        # "UPDATE chal gaya" aur "data waqai mehfooz hai" do alag da'we
        # hain. Yeh doosra wala parkhta hai: koi plain row bachi to nahi,
        # aur jo likha gaya wo wapas khulta hai ya nahi.
        say("4. tasdeeq")
        bad = 0
        for table, idcol, col, is_json in TARGETS:
            key = f"{table}.{col}"
            rows = conn.execute(text(
                f"SELECT {idcol}, {col} FROM {table} WHERE {col} IS NOT NULL"
            )).fetchall()
            left = [i for i, v in rows
                    if not (isinstance(v, str) and v.startswith(MARKER))]
            unreadable = []
            for i, v in rows:
                if not (isinstance(v, str) and v.startswith(MARKER)):
                    continue
                try:
                    cipher.decrypt(v[len(MARKER):].encode("ascii"))
                except Exception:                               # noqa: BLE001
                    unreadable.append(i)

            if left:
                say(f"   [!] {key}: {len(left)} row abhi bhi plain — {left[:5]}")
                bad += len(left)
            if unreadable:
                say(f"   [!] {key}: {len(unreadable)} row wapas nahi khuli "
                    f"— {unreadable[:5]}")
                bad += len(unreadable)
            if not left and not unreadable:
                say(f"   {key:24} {len(rows):>4} rows: marker + wapas khulti hain")
        say()

        if bad:
            say(f"   {bad} masla mila — transaction rollback ho raha hai.")
            raise SystemExit(1)

        say("   Ho gaya. Ab DB dump mein in columns ka matn ciphertext hai.")
        say()
        say(f"   ⚠ {CHAT_ENV_NAME} ka backup rakhiye. Us ke baghair yeh")
        say("     transcripts hamesha ke liye na-qabil-e-parhne hain.")


if __name__ == "__main__":
    main()
