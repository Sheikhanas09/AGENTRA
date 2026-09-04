"""
Move job titles out of the department column
────────────────────────────────────────────
    py tools/set_org_chart.py                                 show what is stored
    py tools/set_org_chart.py --map "Backend Developer=Engineering" \\
                        --map "Frontend Developer=Engineering"
    py tools/set_org_chart.py --map ... --apply

WHAT IS WRONG WITH THE DATA
───────────────────────────
`users.department` holds two different kinds of thing:

    Human Resources     a department
    Finance             a department
    Backend Developer   a JOB TITLE
    Frontend Developer  a JOB TITLE

and `users.designation` — the column meant for the job title — is empty
for every employee in every company.

So the console can only offer what it finds: a menu of "departments"
that is half job titles. Engineering does not appear anywhere in the
database, and nothing may invent it.

WHY THIS SCRIPT TAKES THE MAPPING FROM YOU
──────────────────────────────────────────
Only the company knows its own org chart. Backend Developer might sit
under Engineering, or Product, or Platform. A mapping written into this
file would be a guess about somebody's company, and the console has
spent long enough being wrong confidently.

So: nothing is assumed, nothing is renamed, and nothing runs without
--apply. Each --map moves ONE current department value into a real
department, keeping the old value as the person's designation:

    "Backend Developer=Engineering"

        department  "Backend Developer"  ->  "Engineering"
        designation  (empty)             ->  "Backend Developer"

Rows already carrying a designation are left exactly as they are.
"""

import os
import sys

# (purana bootstrap hataya: move ke baad yeh apne hi folder ko
#  daal raha tha, Backend/ ko nahi)

# ──── This script works across companies, and says so ────
# The tenant guard refuses any query on a session that has not declared
# which company it is for. These tools audit or repair the whole
# database, so crossing companies IS the job — the point is that it is
# declared rather than assumed, and appears in the list
# `check_tenancy.py` prints.
# ──── Backend/ ko raaste par lao ────
# Yeh script Backend/ ke andar ek folder mein hai. `py tests/x.py`
# chalane par Python sirf us folder ko sys.path par rakhta hai, cwd ko
# nahi — to `import app` nakaam ho jata. Aur kuch checks source tree ko
# `Path("app")` se scan karte hain, jo cwd par munhasir hai.
import os as _os
import sys as _sys

_BACKEND = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)
_os.chdir(_BACKEND)

from app.utils.tenancy import unscoped_session


def SessionLocal():          # noqa: N802  (same name, declared scope)
    return unscoped_session("set_org_chart: applies the CEO's department mapping")
from app.models.user import User

APPLY = "--apply" in sys.argv

MAPPING = {}
for i, arg in enumerate(sys.argv):
    if arg == "--map" and i + 1 < len(sys.argv):
        pair = sys.argv[i + 1]
    elif arg.startswith("--map="):
        pair = arg.split("=", 1)[1]
    else:
        continue
    if "=" in pair:
        title, dept = pair.split("=", 1)
        MAPPING[title.strip().lower()] = dept.strip()


def main() -> int:
    db = SessionLocal()
    people = db.query(User).filter(User.role == "employee").all()

    if not people:
        print("No employees on record.")
        return 0

    print(f"{'company':<14} {'name':<16} {'department':<22} "
          f"{'designation':<20} what would change")
    print("─" * 96)

    todo = []
    for u in people:
        current = (u.department or "").strip()
        target = MAPPING.get(current.lower())
        change = ""

        if target and not (u.designation or "").strip():
            change = f"-> dept {target}, designation {current}"
            todo.append((u, target, current))
        elif target:
            change = "already has a designation — left alone"

        print(f"{(u.company_name or '')[:13]:<14} {(u.full_name or '')[:15]:<16} "
              f"{current[:21]:<22} {str(u.designation or '—')[:19]:<20} {change}")

    if not MAPPING:
        print("\nNo --map given, so nothing would change. Each one moves a "
              "job title out of the department column, e.g.")
        print('  py tools/set_org_chart.py --map "Backend Developer=Engineering"')
        db.close()
        return 0

    print(f"\n{len(todo)} row(s) would change"
          + ("" if APPLY else " — dry run, pass --apply to write"))

    if not APPLY:
        db.close()
        return 0

    for u, target, title in todo:
        u.department = target
        u.designation = title
    db.commit()
    print(f"done — {len(todo)} row(s) updated")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
