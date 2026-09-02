"""
Asking the CEO one question before answering
────────────────────────────────────────────
A console that guesses is confidently wrong the day the company has six
departments instead of two. "Tell me about the employees" has no single
right answer then, and picking one silently is worse than asking.

═══════════════════════════════════════════════════════════
BUT ASKING IS ALSO A FAILURE — IT WAS THE FIRST ONE
═══════════════════════════════════════════════════════════
The very first complaint about this console was that it asked:

    "I cannot provide today's attendance as no specific names were
     given."

HR asking its employer for its employer's own data. So the line is:

    ask about SCOPE      which department, which role, which month
    never about DATA     never a name, a number, a date it can look up

And only when all of these hold:

    1. the question does not say which slice it means
    2. there is MORE THAN ONE real slice — read from the database
    3. the answer would differ depending on the choice
    4. they have not already narrowed it some other way

"How many employees do we have?" has one correct answer and is answered.
"How many Backend Developers are there?" names a role that belongs to
one department, so the scope is already decided — also answered.

═══════════════════════════════════════════════════════════
THE MENU IS NUMBERED, AND THE NUMBER IS RESOLVED HERE
═══════════════════════════════════════════════════════════
"1" means the first option OF THE MENU THAT IS OPEN. There is no global
numbering: the department menu's 1 and the role menu's 1 are different
answers, and which one applies is decided by which menu was asked — not
by what a model remembers. The options travel with the question, so
resolving a number is a lookup, and a lookup cannot drift.

═══════════════════════════════════════════════════════════
NOBODY IS HELD AT A MENU
═══════════════════════════════════════════════════════════
If they ignore the question and ask something else, it is dropped — not
repeated, not insisted on. The same question is never asked twice in one
conversation.
"""

import re
from typing import Optional

from sqlalchemy.orm import Session

from app.utils.console_scope import (ALL, Scope, departments_of, find_role,
                                     role_is_unique, roles_in)

# ── When a question is open-ended rather than specific ──
# "tell me about the employees" wants a conversation; "how many
# employees do we have" wants a number and already has one.
_OPEN_ENDED = re.compile(
    r"\b(tell me about|about the|details? (of|about|on)|show me the|"
    r"give me the|walk me through|overview of|batao|bta(?:o|ein)|"
    r"ke bare|ki tafseel|dikhao)\b", re.I)

# A count or a total is a complete question — never interrupt one
_A_TOTAL = re.compile(
    r"\b(how many|how much|total|kitne|kitni|count|number of|sum)\b", re.I)

_PEOPLE = re.compile(
    r"\b(employee|employees|staff|team|people|workforce|mulazim)\w*", re.I)
_ATTENDANCE = re.compile(r"\b(attendance|absent|absence|present|late)\w*", re.I)
_LEAVE = re.compile(r"\b(leave|leaves|chutti|holiday)\w*", re.I)
_PAYROLL = re.compile(r"\b(payroll|salary|salaries|pay|wage)\w*", re.I)

_ABOUT_PEOPLE = (_PEOPLE, _ATTENDANCE, _LEAVE, _PAYROLL)


# ══════════════════════════════════════════════
# Building a menu
# ══════════════════════════════════════════════
def _menu(options, all_label: str) -> list:
    """
    Numbered options, with the escape hatch last.

    The number is assigned here and stored with the question, so "2"
    always means what the CEO saw as 2 — not what a later re-ordering of
    the same list would make of it.
    """
    rows = [{"n": i, "label": f"{o['value']} ({o['employees']})",
             "value": o["value"]}
            for i, o in enumerate(options, start=1)]
    rows.append({"n": len(rows) + 1, "label": all_label, "value": ALL})
    return rows


def department_menu(db: Session, company_id: int, original: str) -> dict:
    return {
        "level": "department",
        "department": None,
        "menu": _menu(departments_of(db, company_id), "Whole company"),
        "original_message": original,
    }


def role_menu(db: Session, company_id: int, department: str,
              original: str) -> dict:
    return {
        "level": "role",
        "department": department,
        "menu": _menu(roles_in(db, company_id, department),
                      f"All {department}"),
        "original_message": original,
    }


# ══════════════════════════════════════════════
# Deciding whether to ask at all
# ══════════════════════════════════════════════
def what_to_ask(db: Session, company_id: int, message: str, plan: dict,
                scope: Scope) -> Optional[dict]:
    """The menu worth putting up before answering — or None."""
    msg = message or ""

    # They named a person: narrower than any menu here.
    if plan.get("person"):
        return None

    # They already narrowed it — by department, by role, or by naming a
    # month. Asking again is the obstruction this file exists to avoid.
    if not scope.is_empty():
        return None
    if any(plan.get(k) for k in ("period", "on_date", "year", "month",
                                 "date_from", "date_to")):
        return None

    if not any(p.search(msg) for p in _ABOUT_PEOPLE):
        return None
    if not _OPEN_ENDED.search(msg) or _A_TOTAL.search(msg):
        return None

    # ──── Do they already name a slice, without saying so? ────
    # "how many Backend Developers" names a role. If exactly one
    # department has that role, the scope is settled and there is
    # nothing to ask.
    named_role = find_role(db, company_id, msg)
    if named_role and role_is_unique(db, company_id, named_role):
        return None

    departments = departments_of(db, company_id)

    # ──── One department is not a choice, but its roles may be ────
    # A company where everybody is in Engineering has nothing to ask
    # about departments — and may still have Backend, Frontend and QA
    # inside it. Skipping straight to the role menu asks the question
    # that actually has two answers, instead of none at all.
    if len(departments) == 1:
        return after_department(db, company_id, departments[0]["value"], msg)

    if not departments:
        return None

    return department_menu(db, company_id, msg)


def after_department(db: Session, company_id: int, department: str,
                     original: str) -> Optional[dict]:
    """
    The role menu, if that department has more than one role to offer.

    Nothing here knows which departments have roles. A company that has
    not recorded designations gets no second question at all, because
    `roles_in` comes back empty — the step disappears rather than asking
    a question with no answers in it.
    """
    if len(roles_in(db, company_id, department)) < 2:
        return None
    return role_menu(db, company_id, department, original)


# ══════════════════════════════════════════════
# Putting the question
# ══════════════════════════════════════════════
def phrase(ask: dict, language: str = "english") -> str:
    """
    The question with its numbered options.

    Written here rather than by the model because the options are data.
    A model asked to list the departments would eventually list one that
    does not exist.
    """
    listed = "\n".join(f"  {o['n']}. {o['label']}" for o in ask["menu"])

    if language == "roman_urdu":
        head = ("Aap kis department ke bare mein poochh rahe hain?"
                if ask["level"] == "department" else
                f"{ask['department']} mein ek se zyada role hain. Kaunsa?")
        return f"{head}\n\n{listed}\n\nNumber likh dein, ya naam."

    head = ("Which department do you mean?" if ask["level"] == "department"
            else f"{ask['department']} has more than one role. Which one?")
    return f"{head}\n\n{listed}\n\nReply with the number, or the name."


# ══════════════════════════════════════════════
# Reading their answer
# ══════════════════════════════════════════════
_JUST_A_NUMBER = re.compile(r"^\s*#?\s*(\d{1,2})\s*[.)]?\s*$")
_NUMBER_IN_A_SENTENCE = re.compile(
    r"\b(?:option|number|no\.?|#)\s*(\d{1,2})\b", re.I)
_EVERYTHING = re.compile(
    r"\b(all|every|whole|entire|both|company|sab|saray|sabhi|poori|dono)\b",
    re.I)


def read_choice(message: str, ask: dict) -> Optional[dict]:
    """
    Which option they picked — or None if they answered something else.

    The number is tried first and settled here, never by the model: "1"
    is a lookup in the menu that was actually asked.
    """
    msg = (message or "").strip()
    menu = ask.get("menu") or []

    hit = _JUST_A_NUMBER.match(msg) or _NUMBER_IN_A_SENTENCE.search(msg)
    if hit:
        n = int(hit.group(1))
        return next((o for o in menu if o["n"] == n), None)

    # By name: the whole value first, then any word of it long enough to
    # be meaningful — "Frontend" finds "Frontend Developer".
    low = f" {msg.lower()} "
    for option in menu:
        if option["value"] == ALL:
            continue
        value = option["value"].lower()
        if f" {value} " in low or value in low:
            return option
    for option in menu:
        if option["value"] == ALL:
            continue
        for word in option["value"].split():
            if len(word) >= 4 and f" {word.lower()} " in low:
                return option

    if _EVERYTHING.search(msg):
        return next((o for o in menu if o["value"] == ALL), None)

    return None


# ══════════════════════════════════════════════
# Reading it back out of the conversation
# ══════════════════════════════════════════════
def pending_from(history) -> Optional[dict]:
    """The menu the last reply put up, if it put one up."""
    for h in reversed(history or []):
        if h.get("role") != "hr":
            continue
        for s in (h.get("sources") or []):
            if isinstance(s, dict) and s.get("kind") == "clarification":
                return s.get("ask")
        return None                    # only the most recent reply counts
    return None


def scope_from(history) -> Scope:
    """The slice in force, from the most recent reply that carried one."""
    for h in reversed(history or []):
        if h.get("role") != "hr":
            continue
        for s in (h.get("sources") or []):
            if isinstance(s, dict) and s.get("kind") == "scope":
                return Scope.from_source(s)
    return Scope()


def already_asked(history, level: str) -> bool:
    """Whether this conversation has already put that question."""
    for h in history or []:
        for s in (h.get("sources") or []):
            if (isinstance(s, dict) and s.get("kind") == "clarification"
                    and (s.get("ask") or {}).get("level") == level):
                return True
    return False
