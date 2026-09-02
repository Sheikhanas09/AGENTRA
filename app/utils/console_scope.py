"""
Which slice of the company the CEO is talking about
───────────────────────────────────────────────────
    department   Engineering, Finance, Human Resources …   users.department
    role         Backend Developer, QA Engineer …          users.designation

Two columns that already existed. `designation` was added the day the
console reported "the Backend Developer department has 2 people", which
is not a department and not a useful answer — the schema has said these
are different things ever since. Nothing had filled it in yet, and
nothing had filtered on it.

═══════════════════════════════════════════════════════════
EVERY OPTION COMES OUT OF THE DATABASE
═══════════════════════════════════════════════════════════
No department, role, or hierarchy is written down in this file. A
company with one department is never asked which one; a department with
one role is never asked which role; and a role nobody holds is never
offered. That is not politeness — a menu built from anywhere but the
data eventually offers a department nobody works in.

It also means this file cannot invent "Engineering". If a company's
records put a job title in the department column, the menu shows job
titles, because that is what the company has said about itself.

═══════════════════════════════════════════════════════════
A SCOPE IS A SET OF DIMENSIONS, NOT A PAIR
═══════════════════════════════════════════════════════════
`Scope` holds department and role today. A team, a location or a project
is another key beside them and another `people_in` clause — not another
rewrite of the menu logic, the numbering, or the carry-forward rules.
"""

import re
from typing import List, Optional

from sqlalchemy.orm import Session

# What the CEO picks when they do not want a filter at all
ALL = "__ALL__"

# The dimensions a scope can have, narrowest last. Adding "team" here and
# to `people_in` is the whole change needed to support one.
DIMENSIONS = ("department", "role")


class Scope:
    """The slice in force. Empty means the whole company."""

    __slots__ = DIMENSIONS

    def __init__(self, department=None, role=None):
        self.department = department or None
        self.role = role or None

    # ──── A scope travels in `sources`, like everything else here ────
    def to_source(self) -> dict:
        return {"kind": "scope",
                **{d: getattr(self, d) for d in DIMENSIONS}}

    @classmethod
    def from_source(cls, s: dict) -> "Scope":
        return cls(**{d: (s or {}).get(d) for d in DIMENSIONS})

    def as_kwargs(self) -> dict:
        return {d: getattr(self, d) for d in DIMENSIONS if getattr(self, d)}

    def is_empty(self) -> bool:
        return not any(getattr(self, d) for d in DIMENSIONS)

    def describe(self) -> str:
        parts = [getattr(self, d) for d in DIMENSIONS if getattr(self, d)]
        return " · ".join(parts) if parts else "the whole company"

    def narrowed_to(self, dimension: str, value) -> "Scope":
        """
        A new scope with one dimension set.

        Setting a WIDER dimension clears the narrower ones: choosing a
        new department cannot keep the old department's role, or "what
        about HR?" would answer about HR's Backend Developers.
        """
        out = Scope(**{d: getattr(self, d) for d in DIMENSIONS})
        setattr(out, dimension, value or None)

        if value:
            for narrower in DIMENSIONS[DIMENSIONS.index(dimension) + 1:]:
                setattr(out, narrower, None)
        return out

    def __repr__(self):
        return f"Scope({self.describe()})"


# ══════════════════════════════════════════════
# What exists, according to the database
# ══════════════════════════════════════════════
def departments_of(db: Session, company_id: int) -> List[dict]:
    """Departments with somebody employed in them, biggest first."""
    from app.utils.workforce import employed

    counts = {}
    for u in employed(db, company_id):
        d = (u.department or "Unassigned").strip()
        counts[d] = counts.get(d, 0) + 1
    return [{"value": d, "employees": n}
            for d, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def roles_in(db: Session, company_id: int,
             department: Optional[str] = None) -> List[dict]:
    """
    The roles held inside a department — from `designation`.

    Empty when the company has not recorded designations, and then no
    role menu is ever shown. The feature disappears rather than asking a
    question with no answers in it.
    """
    from app.utils.workforce import employed

    counts = {}
    for u in employed(db, company_id):
        if department and department.strip().lower() not in (
                u.department or "").strip().lower():
            continue
        if not (u.designation or "").strip():
            continue
        r = u.designation.strip()
        counts[r] = counts.get(r, 0) + 1
    return [{"value": r, "employees": n}
            for r, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def find_department(db: Session, company_id: int, text: str) -> Optional[str]:
    """The department this text names, if it names one."""
    return _match(text, [d["value"] for d in departments_of(db, company_id)])


def find_role(db: Session, company_id: int, text: str,
              department: Optional[str] = None) -> Optional[str]:
    """
    The role this text names, inside a department or anywhere.

    Used for "what about Frontend?" — which is a ROLE under a
    department, and must not be read as a department of its own.
    """
    return _match(text, [r["value"] for r in roles_in(db, company_id,
                                                      department)])


def role_is_unique(db: Session, company_id: int, role: str) -> Optional[str]:
    """
    The department a role belongs to, when it belongs to exactly one.

    "How many Backend Developers are there?" needs no clarification if
    only one department has any — the question already says which slice
    it means.
    """
    from app.utils.workforce import employed

    owners = {(u.department or "").strip()
              for u in employed(db, company_id)
              if (u.designation or "").strip().lower() == role.strip().lower()}
    owners = {o for o in owners if o}
    return owners.pop() if len(owners) == 1 else None


def _match(text: str, values) -> Optional[str]:
    """
    Whether a message names one of these values.

    Whole value first, then any word of it long enough to be meaningful —
    so "Frontend" finds "Frontend Developer" while "the" finds nothing.
    """
    # Punctuation is not part of a name. "What about Backend?" ends the
    # word with a question mark, and " backend " was not in it — so the
    # role went unrecognised and the slice never changed.
    low = " " + re.sub(r"[^\w\s]", " ", (text or "").lower()) + " "
    for value in values:
        if f" {value.lower()} " in low or value.lower() in low:
            return value
    for value in values:
        for word in value.split():
            if len(word) >= 4 and f" {word.lower()} " in low:
                return value
    return None
