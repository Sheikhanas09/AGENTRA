"""
Scope check — run me before shipping anything
─────────────────────────────────────────────
    py check_scope.py

The rule this enforces, in one line:

    an employee's data may reach the CEO;
    the CEO's data may NEVER reach an employee.

Every check reads the actual tool tables, routes and source. No model is
involved, so it runs when the API quota is gone, and it cannot be talked
around by a cleverly worded message.

This is not a unit-test suite. It is the one thing that has to keep
passing for the separation between `/chat/*` and `/hr/*` to still mean
anything — checked mechanically instead of remembered.

Exit code is 1 on any failure, so CI can use it.
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.chat_data import TOOLS
from app.utils.hr_company_data import COMPANY_TOOLS
import app.routes.chat as chat_routes
import app.routes.hr as hr_routes

fails = []


def check(label, ok, detail=""):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


# ══════════════════════════════════════════════
print("\n1. The two tool tables never meet")
# If a company-wide function ever appears in the employee's table, the
# whole "there is no other query" guarantee is gone.
overlap = set(TOOLS) & set(COMPANY_TOOLS)
check("no tool is in both tables", not overlap, str(overlap or ""))

check("chat_data does not import hr_company_data",
      "hr_company_data" not in inspect.getsource(
          sys.modules["app.utils.chat_data"]))


# ══════════════════════════════════════════════
print("\n2. Every employee tool is scoped to one person")
for name, fn in sorted(TOOLS.items()):
    params = list(inspect.signature(fn).parameters)
    check(f"{name} takes employee_id", "employee_id" in params)


# ══════════════════════════════════════════════
print("\n3. Every /hr route requires the CEO")
hr_src = inspect.getsource(hr_routes)
for line in hr_src.split("\n"):
    if line.strip().startswith("@router."):
        idx = hr_src.index(line)
        check(f"{line.strip()} -> require_ceo",
              "require_ceo" in hr_src[idx:idx + 900])


# ══════════════════════════════════════════════
print("\n4. Nobody reads someone else's conversation")
chat_src = inspect.getsource(chat_routes)
lines = chat_src.split("\n")

# The filter is usually on the FOLLOWING line, so read a small window.
leaks = []
for i, line in enumerate(lines):
    if "db.query(ChatMessage)" not in line:
        continue
    if "ChatMessage.session_id" not in " ".join(lines[i:i + 4]):
        leaks.append(line.strip())
check("every ChatMessage query is scoped to a session", not leaks,
      str(leaks[:2]))

# A session may be fetched for somebody else ONLY to write into — the
# CEO's decision going back into the employee's own thread. Reading one
# is always scoped to the caller.
for i, line in enumerate(lines):
    if "db.query(ChatSession)" not in line:
        continue
    block = "\n".join(lines[i:i + 24])
    own = ("ChatSession.employee_id == employee_id" in block
           or 'ChatSession.employee_id == current_user["user_id"]' in block)
    write_only = "db.add(ChatMessage(" in block and "_msg_out" not in block
    check(f"session query at line {i + 1}", own or write_only,
          "own-scoped" if own else
          ("write-only" if write_only else "READS SOMEONE ELSE'S"))

check("no company-wide tool returns a transcript",
      not any("ChatMessage" in inspect.getsource(fn)
              for fn in COMPANY_TOOLS.values()))


# ══════════════════════════════════════════════
print("\n5. Only the employed are paid or written to")
from app.database import SessionLocal
from app.models.user import User
from app.utils.company import company_employees
from app.utils.workforce import employed, former, may_receive_mail, unclassified

db = SessionLocal()
try:
    ceos = db.query(User).filter(User.role.in_(("ceo", "superadmin"))).all()
    checked_any = False

    for ceo in ceos:
        if not ceo.company_name:
            continue
        checked_any = True
        paid = company_employees(db, ceo)
        leavers = former(db, ceo.id)
        strays = unclassified(db, ceo.id)

        check(f"[{ceo.company_name}] payroll list excludes every leaver",
              not (set(u.id for u in paid) & set(u.id for u in leavers)),
              f"employed={len(paid)} former={len(leavers)}")
        check(f"[{ceo.company_name}] no leaver may be emailed",
              all(not may_receive_mail(u) for u in leavers))
        check(f"[{ceo.company_name}] every employed person may be emailed",
              all(may_receive_mail(u) for u in employed(db, ceo.id)))
        # A status nobody classified is a decision waiting to be made,
        # not something to guess at.
        check(f"[{ceo.company_name}] no unclassified status",
              not strays,
              str([f"{u.full_name}({u.status})" for u in strays]))

    if not checked_any:
        check("at least one company to check", False, "no CEO has a company")
finally:
    db.close()


# ══════════════════════════════════════════════
print("\n6. The claim-stripper's tool list is real")
# `strip_unbacked_attendance` removes attendance sentences when no
# attendance tool ran. A name misspelled in that list silently deletes a
# true sentence instead — the same bug pointing the other way — and
# nothing at runtime would say so.
from app.agents.hr_console_agent import ATTENDANCE_TOOLS

unknown = [n for n in ATTENDANCE_TOOLS if n not in COMPANY_TOOLS]
check("every name in ATTENDANCE_TOOLS is a real tool", not unknown,
      str(unknown))

# The reverse: a tool that returns attendance but is missing from the
# list. Checked by looking for the shared attendance model in its source.
missing = [n for n, fn in COMPANY_TOOLS.items()
           if n not in ATTENDANCE_TOOLS
           and "attendance_for(" in inspect.getsource(fn)]
check("no attendance-bearing tool is left off the list", not missing,
      str(missing))


# ══════════════════════════════════════════════
print("\n7. No payslip charges a day before the person was hired")
# Payroll counted every month from its 1st. Somebody hired on 14 August
# was measured from 1 August, and the fortnight before they had a job
# came back as absence — deducted at a full day's pay each.
#
# This reads the slips themselves rather than the code: a slip is a
# record of what was actually decided, and that is the thing that must
# not contain a day nobody worked.
from app.models.payroll import Payslip

db = SessionLocal()
try:
    slips = db.query(Payslip).filter(Payslip.status != "cancelled").all()
    people = {u.id: u for u in db.query(User).all()}
    bad = []

    for s in slips:
        u = people.get(s.employee_id)
        if not u or not u.joining_date:
            continue
        snap = s.attendance_snapshot or {}
        early = [d for d in (snap.get("dates") or [])
                 if str(d) < str(u.joining_date)]
        if early:
            bad.append(f"{u.full_name} {s.period}: {early[:3]}")

    check("no absence is dated before a joining date", not bad, str(bad[:3]))

    # The other half: those days must not be paid either
    paid_early = []
    for s in slips:
        u = people.get(s.employee_id)
        snap = s.attendance_snapshot or {}
        if not u or not u.joining_date or not snap.get("employed_days_in_month"):
            continue
        if (snap["employed_days_in_month"] < (snap.get("working_days_in_month") or 0)
                and str(s.base_salary) == str(
                    (s.salary_snapshot or {}).get("base_salary"))):
            paid_early.append(f"{u.full_name} {s.period}")

    check("a mid-month joiner's salary is pro-rated", not paid_early,
          str(paid_early[:3]))
finally:
    db.close()


# ══════════════════════════════════════════════
print("\n8. Both sides count an absence the same way")
# The help desk said "you were absent for all 21 working days" in the
# same conversation where the payslip said 12. Two answers, one system,
# and the employee had no way to know which was real.
#
# They now share `attendance_view.attendance_for`. This checks the
# RESULTS rather than the imports, because sharing a function is not the
# point — agreeing is.
from app.utils.attendance_view import attendance_for
from app.utils.chat_data import TOOLS as EMP_TOOLS

db = SessionLocal()
try:
    from datetime import date as _date, timedelta

    disagree = []
    for ceo in db.query(User).filter(User.role == "ceo").all():
        for u in employed(db, ceo.id):
            for month in (7, 8, 9):
                start = _date(2026, month, 1)
                end = _date(2026, month + 1, 1) - timedelta(days=1)
                mine = attendance_for(db, u.id, ceo.id, start, end)
                theirs = EMP_TOOLS["attendance_summary"](
                    db, u.id, ceo.id, 2026, month)
                if mine["absent_days"] != theirs["absent_days"]:
                    disagree.append(
                        f"{u.full_name} 2026-{month:02d}: console "
                        f"{mine['absent_days']} vs desk "
                        f"{theirs['absent_days']}")

    check("the console and the help desk agree on absences", not disagree,
          str(disagree[:3]))

    # And the identity that makes subtraction unnecessary
    broken = []
    for ceo in db.query(User).filter(User.role == "ceo").all():
        for u in employed(db, ceo.id):
            a = EMP_TOOLS["attendance_summary"](db, u.id, ceo.id, 2026, 8)
            if (a["present_days"] + a["leave_days"] + a["absent_days"]
                    != a["your_working_days"]):
                broken.append(f"{u.full_name}: {a['present_days']}+"
                              f"{a['leave_days']}+{a['absent_days']} != "
                              f"{a['your_working_days']}")
    check("present + leave + absent = their working days", not broken,
          str(broken[:3]))
finally:
    db.close()


# ══════════════════════════════════════════════
print("\n9. No prompt example contains real data")
# The oldest recurring bug in this project, and it has now happened
# three times:
#
#   "three people have a clean month"   was written in a prompt example
#   "the latest is August 2026, at 61,713.48 net"   was a real total,
#                                       and went stale besides
#   "August net was 41,903.93 — gross 128,571.43…"  was reproduced word
#                                       for word in a live answer
#
# A model reads an example as a demonstration of the ANSWER, not of the
# shape. Any figure or name in one can come back out as fact — right for
# the company it was copied from, invented for every other.
import re as _re

from app.agents import chat_agent as _chat
from app.agents import hr_console_agent as _console

PROMPTS = {
    "console router": _console.ROUTER_PROMPT,
    "console answer": _console.ANSWER_PROMPT,
    "chat router": getattr(_chat, "ROUTER_PROMPT", ""),
    "chat answer": getattr(_chat, "ANSWER_PROMPT", ""),
}

# 41,903.93 / 61713.48 / 128,571.43 — a figure with money's shape
_MONEY = _re.compile(r"\b\d{1,3}(,\d{3})+(\.\d+)?\b|\b\d+\.\d{2}\b")

db = SessionLocal()
try:
    names = set()
    for u in db.query(User).all():
        for part in (u.full_name or "").split():
            if len(part) >= 4:
                names.add(part.lower())

    for label, text in PROMPTS.items():
        money = sorted(set(m.group(0) for m in _MONEY.finditer(text or "")))
        check(f"[{label}] no money figure in an example", not money,
              str(money[:4]))

        found = sorted(n for n in names if n in (text or "").lower())
        check(f"[{label}] no real employee name", not found, str(found[:4]))
finally:
    db.close()


# ══════════════════════════════════════════════
print("\n10. No source file contains a control character")
# Three times now, a regex written through a shell heredoc has arrived
# with `\b` turned into \x08 — a literal backspace. Each time the line
# looked correct in the editor, in grep and in the terminal, and each
# time it silently never matched:
#
#   Chunk 36   a chat pattern that stopped catching anything
#   Chunk 37   the same, in the filler stripper
#   here       "what about the whole company?" kept the old scope
#
# The eye cannot see this and review cannot catch it, so it is checked
# by ordinal. Tab, newline and carriage return are the only control
# characters a source file has any business containing.
ALLOWED = {9, 10, 13}

dirty = []
for folder, subdirs, files in os.walk(os.path.dirname(os.path.abspath(__file__))):
    subdirs[:] = [d for d in subdirs
                  if d not in {"__pycache__", ".git", "venv", ".venv",
                               "node_modules"}]
    for name in files:
        if not name.endswith(".py"):
            continue
        path = os.path.join(folder, name)
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                for ch in line:
                    if ord(ch) < 32 and ord(ch) not in ALLOWED:
                        dirty.append(f"{os.path.relpath(path)}:{lineno} "
                                     f"{hex(ord(ch))}")
                        break

check("no stray control characters in any .py", not dirty, str(dirty[:3]))


# ══════════════════════════════════════════════
print("\n" + "=" * 52)
if fails:
    print(f"FAILED {len(fails)}")
    for f in fails:
        print("  -", f)
else:
    print("ALL SCOPE CHECKS PASSED")
sys.exit(1 if fails else 0)
