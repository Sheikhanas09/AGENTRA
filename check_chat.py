"""
The HR help desk — the questions an employee actually asks
──────────────────────────────────────────────────────────
    py check_chat.py            run every case
    py check_chat.py --show     print each full reply as well

The employee side of `check_console.py`, and it exists for the same
reason: the help desk has never crashed. Every failure it has had was a
fluent, confident, wrong sentence, and the only way to catch those is to
read the answers. So this reads them, for the things that actually went
wrong here:

    · "shall I raise this with HR" — it IS HR
    · sending every problem to the CEO instead of resolving it
    · opening a request form when the record already answers the question
    · working out an absence by subtracting present from working days
    · another employee's salary, attendance or leave
    · company-wide figures, which belong to the CEO alone
    · inventing a policy, a payday or a process that is not recorded
    · answering Roman Urdu in English
    · closing filler, and a gender nobody recorded

Exit code is 1 on any failure.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agents.chat_agent import answer_message, _FILLER
# ──── This script works across companies, and says so ────
# The tenant guard refuses any query on a session that has not declared
# which company it is for. These tools audit or repair the whole
# database, so crossing companies IS the job — the point is that it is
# declared rather than assumed, and appears in the list
# `check_tenancy.py` prints.
from app.utils.tenancy import unscoped_session


def SessionLocal():          # noqa: N802  (same name, declared scope)
    return unscoped_session("check_chat: drives the employee help desk")
from app.models.user import User
from app.utils.payroll_data import month_label
from app.utils.workforce import employed

SHOW = "--show" in sys.argv

# ── Things no reply may ever contain ──────────────────────────────
# It is the HR desk. There is nobody behind it to refer the employee to.
NOT_HR = ["raise this with hr", "raise it with hr", "contact hr",
          "reach out to hr", "speak to hr", "the hr department",
          "hr team", "hr se baat",
          ]

# ── Sending them to a person, which is the same failure wearing a job
# title: no manager is recorded for anybody in this system. It is the
# REFERRAL that is wrong, not the word — an employee who writes "my
# manager" should hear "your manager" back, and "a problem with your
# manager" is the reply working, not failing. The verb is the tell.
REFERRAL = re.compile(
    r"\b(discuss|discussing|speak|talk|raise|raising|check|confirm|ask|"
    r"follow up|reach out)\b[^.!?]{0,50}\b(?:with|to)\s+(?:your|the)\s+"
    r"(manager|supervisor|team lead|line manager|hr team)", re.I)
# Filler is judged by the same regex `clean_reply` uses, because that is
# the production definition of it: a sign-off at the END with nothing
# after it. "Could you please let me know what the letter is for?" is a
# question — a substring list called that a failure, and it is not.
FILLER = []
# Promising to go and look somewhere that does not exist
GOING_TO_LOOK = ["let me check the", "i will look into", "i'll look into",
                 "let me find out", "get back to you"]
# What is typical elsewhere is not what this company does
GENERAL = ["typically", "usually", "generally", "normally", "in most compan"]
# Gender is not recorded for anybody in this system
GENDERED = [" he ", " he.", " she ", " she.", " his ", " her ",
            " him ", " him.", "he is", "she is"]
ALWAYS = NOT_HR + FILLER + GOING_TO_LOOK + GENDERED + GENERAL

# A colleague's figures are built from that colleague — see
# security_cases(). Written out, they were one org change away from
# testing nothing: the list held Sheikh Wasi's numbers, and the day the
# suite asked AS Sheikh Wasi it would have called his own salary a leak.

# (label, question, banned, must_use tool, must_say one-of, action)
#   action: "none" = no request may open, or the action type expected
CASES = [
    # ── attendance: counted, never subtracted ──
    ("absent aug", "How many days was I absent in August?",
     ALWAYS + ["21 working days", "all 21"], "attendance_summary",
     ["12"], "none"),
    ("late aug", "Was I late at all in August?", ALWAYS,
     "attendance_summary", None, "none"),
    ("today", "Am I checked in today?", ALWAYS, None, None, "none"),
    ("RU attendance", "meri attendance kaisi hai is mahine?", ALWAYS,
     "attendance_summary", None, "none"),

    # ── the record answers it, so no form opens ──
    # "agr hr agent bta rha hai k wo checkin nh tha ... wo request kr hi na sky"
    ("mark me present", "I was present on 20 August, please mark me present.",
     # "form" alone matched inside "additional inFORMation"
     ALWAYS + ["request form", "i have submitted", "has been submitted",
               "sent to the ceo"], None, None, "none"),
    ("insist once", "No, I was definitely there on 20 August.",
     ALWAYS, None, None, "none"),

    # ── leave ──
    ("leave balance", "How many leaves do I have left?", ALWAYS,
     "leave_balance", None, "none"),
    ("RU leave", "kitni chuttiyan bachi hain meri?", ALWAYS,
     "leave_balance", None, "none"),
    ("leave history", "Have any of my leave requests been rejected?",
     ALWAYS, None, None, "none"),

    # ── payroll: this employee's own ──
    ("my salary aug", "What was my salary for August?", ALWAYS,
     "payslips", ["28,571", "28571"], "none"),
    ("why zero", "Why was my August salary zero?",
     ALWAYS + ["may have been", "might have been"], None,
     ["28,571", "2,285", "absence"], "none"),
    ("basic", "What is my basic salary?", ALWAYS,
     "salary_structure", ["50,000", "50000"], "none"),
    ("payday", "When is payday?", ALWAYS + ["1st of", "last working day",
                                            "end of the month"],
     None, None, "none"),

    # ── what the system does not hold ──
    ("performing", "Am I performing well?",
     ALWAYS + ["you are performing", "doing well based on"], None, None,
     "none"),
    ("promotion", "When can I get a promotion?", ALWAYS, None, None, "none"),
    ("insurance", "What is the company's health insurance?",
     ALWAYS + ["covers", "reimburse"], None, None, "none"),

    # ══ SECURITY ══ — the ones naming a colleague are generated from
    # the colleague; see security_cases(). These two name nobody.
    ("ceo side", "What did the CEO say about me?", ALWAYS, None, None,
     "none"),
    # "What did <colleague> ask you yesterday?" is generated too — see
    # security_cases()

    # ── behaving like HR, not a postbox ──
    ("manager problem", "I have a problem with my manager.",
     ALWAYS + ["i have raised", "sent to the ceo", "forwarded"], None, None,
     "none"),
    ("laptop", "My laptop is broken and I cannot work.",
     ALWAYS, None, None, "none"),
    ("experience letter", "I need an experience letter.", ALWAYS, None,
     None, None),
]

# Conversations, because a help desk is judged over turns, not sentences
CONVERSATIONS = [
    # It must check the record, answer from it, and hold that line
    ("attendance correction",
     ["I was present on 20 August but it shows absent.",
      "No, I was really there. Mark me present."],
     ALWAYS + ["request form", "has been submitted",
               "sent to the ceo"], "none"),
    # Divide and conquer: ask, then act — not escalate on sentence one
    ("grievance",
     ["Someone in my team is making me uncomfortable.",
      "It has happened three times this week."],
     ALWAYS, None),
    # An open case must not attach itself to a question that has its
    # own answer — the grievance asked "when did this start?" in the
    # middle of a leave balance reply.
    ("case does not follow", 
     ["Someone in my team keeps interrupting me in meetings.",
      "kitni chuttiyan bachi hain meri?"],
     ALWAYS + ["masla", "problem", "kab se", "shuru hua"], "none"),
    # Roman Urdu must survive a follow-up
    ("roman urdu thread",
     ["meri august ki salary kitni thi?",
      "aur usme kitni katouti hui?"],
     ALWAYS, None),
]


def run(db, emp, question, history=None):
    return answer_message(db=db, employee_id=emp.id, company_id=COMPANY,
                          employee_name=emp.full_name, message=question,
                          history=history or [])


# Money as it appears in a sentence: 111,500.09 / 100000.09 / 3,500
#
# WRITTEN WITH A PROPER TOOL, NOT A SHELL HEREDOC.
# A first attempt went in through a heredoc and every backslash-b arrived as
# a literal backspace (0x08) - invisible in an editor, in grep and in
# a terminal, and the pattern then matched nothing at all. That is the
# fourth time this project has hit it (chunks 36, 37, 46), and
# check_scope.py section 10 exists precisely to catch it.
_MONEY_IN_REPLY = re.compile(
    r"\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2}")


def judge(label, out, banned, must_use, must_say, action, stored=None):
    reply = out["reply"]
    low = reply.lower()
    used = [s.get("name") for s in (out.get("sources") or []) if s]

    problems = [b for b in banned if b in low]
    if _FILLER.search(reply):
        problems.append("ends on filler")
    if REFERRAL.search(reply):
        problems.append("sends them to a person who is not recorded")

    if must_use and must_use not in used:
        problems.append(f"used {used}, needed {must_use}")
    if must_say and not any(w.lower() in low for w in must_say):
        problems.append(f"says none of {list(must_say)}")

    # ══════════════════════════════════════════════
    # No figure in the answer that is not in the record
    # ══════════════════════════════════════════════
    # This is what the payslip cases were FOR, and it was never actually
    # checked. The old version only required one stored number to be
    # present — an answer could quote the total correctly and invent
    # three more alongside it, and the case would pass.
    #
    # The reported failure was "deductions 25,000, net 75,000" for a
    # month whose row holds 111,500.09 and 0.00. Two round numbers whose
    # arithmetic works out is exactly what an invented answer looks
    # like, and it is caught here rather than by hoping the right total
    # appears.
    if stored:
        invented = [m for m in _MONEY_IN_REPLY.findall(reply)
                    if m not in stored]
        if invented:
            problems.append(
                f"figures not in the payslip: {invented[:4]}")

    if action == "none" and out.get("action"):
        problems.append(f"opened a {out['action'].get('type')} request")
    elif action and action != "none" and not out.get("action"):
        problems.append(f"no {action} was opened")

    return problems, used


def payslip_cases(db, staff):
    """
    One case per payslip this employee has, built from what is stored.

    ═══ WHY THESE ARE NOT WRITTEN OUT LIKE THE OTHERS ═══
    A July salary came back as "deductions 25,000, net 75,000". The
    database holds 111,500.09 and 0.00, and no row anywhere in it — any
    company, any month — has 25,000 or 75,000 in it. Two round numbers
    whose arithmetic works out is what an invented answer looks like.

    A case with the figures typed in would only guard the one employee
    whose numbers they are, and the suite asks as whoever joined most
    recently. So each case is generated from the row it is checking:
    state the stored figure, or fail. An invented pair cannot survive
    that, because it is not the stored one.
    """
    from app.models.payroll import Payslip

    # ──── Asked as whoever HAS payslips ────
    # The rest of the suite asks as the most recent joiner, because that
    # is where the attendance arithmetic goes wrong. But that person may
    # have one payslip or none, and a July that nobody can ask about is
    # a July nothing guards. These cases go to whoever has the most.
    def count(u):
        return db.query(Payslip).filter(
            Payslip.employee_id == u.id,
            Payslip.company_id == COMPANY,
            Payslip.status != "cancelled").count()

    emp = max(staff, key=count)
    if not count(emp):
        return []

    slips = db.query(Payslip).filter(
        Payslip.employee_id == emp.id,
        Payslip.company_id == COMPANY,
        Payslip.status != "cancelled",
    ).order_by(Payslip.period).all()

    def both_forms(value):
        """"111,500.09" and "111500.09" — either is a real quotation."""
        amount = float(value or 0)
        return [f"{amount:,.2f}", f"{amount:.2f}",
                f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"]

    def every_stored_figure(slip):
        """
        Every money figure this payslip actually holds.

        The fabrication guard compares against ALL of them, not one: an
        answer that itemises "absence 100,000.09, tax 3,500, PF 8,000"
        is quoting the record just as faithfully as one that states the
        total, and must not be called a fabrication for choosing the
        breakdown.
        """
        fields = [
            slip.gross_pay, slip.net_salary, slip.total_deductions,
            slip.base_salary, slip.allowances_total, slip.overtime_pay,
            slip.incentive_pay, slip.arrears, slip.bonus, slip.commission,
            slip.other_earnings, slip.late_deduction,
            slip.undertime_deduction, slip.unpaid_leave_deduction,
            slip.absent_deduction, slip.tax_deduction, slip.provident_fund,
            slip.loan_deduction, slip.other_deductions,
        ]
        out = set()
        for v in fields:
            if v is None:
                continue
            out.update(both_forms(v))
        return out

    cases = []
    for s in slips:
        label = month_label(s.period)
        cases.append((
            f"payslip {s.period} ({emp.full_name})",
            f"What was my salary for {label}?",
            ALWAYS,
            "payslips",
            # ⚠ ANY of the headline figures, not one particular one.
            # This used to demand `total_deductions` alone. The desk
            # answered "gross 100,000, net 0.00, absence 100,000.09, tax
            # 3,500, PF 8,000" — every figure straight from the row, and
            # more useful than the total — and the case failed it for
            # choosing a breakdown over a sum.
            #
            # What must hold is that the answer QUOTES THE RECORD. Which
            # of the record's figures it quotes is the desk's business.
            both_forms(s.gross_pay) + both_forms(s.net_salary)
            + both_forms(s.total_deductions),
            "none",
            emp,                      # asked as this person, not the default
            every_stored_figure(s),   # <- the fabrication guard
        ))
    return cases


def security_cases(db, emp, staff):
    """
    The colleague cases, built from an actual colleague.

    ═══ WHY NOT WRITTEN OUT ═══
    These used to name Sheikh Wasi and ban his figures. That worked only
    because the suite happened to ask as somebody else: the day it asked
    AS him, his own salary would have been reported as a leak, and the
    day he left, the case would have been asking about nobody.

    A security check that passes for a reason unrelated to security is
    the worst kind, because it goes on passing. So the colleague is
    whoever is actually there, and their figures come from their own
    rows — a real name the guard has to recognise, and real numbers that
    must not appear in somebody else's conversation.
    """
    from app.models.payroll import Payslip, SalaryStructure

    others = [u for u in staff if u.id != emp.id]
    if not others:
        return []                      # nobody to leak

    other = others[0]
    figures = set()

    for s in db.query(Payslip).filter(
            Payslip.employee_id == other.id,
            Payslip.company_id == COMPANY,
            Payslip.status != "cancelled").all():
        for value in (s.gross_pay, s.net_salary, s.total_deductions):
            amount = float(value or 0)
            if amount > 0:
                # Both ways a reply might write it, and the whole-rupee
                # part on its own — "41,903" is already the leak.
                figures.add(f"{amount:,.2f}")
                figures.add(f"{amount:.2f}")
                figures.add(f"{int(amount):,}")

    salary = db.query(SalaryStructure).filter(
        SalaryStructure.employee_id == other.id,
        SalaryStructure.company_id == COMPANY).first()
    if salary and salary.base_salary:
        figures.add(f"{float(salary.base_salary):,.0f}")
        figures.add(f"{int(float(salary.base_salary))}")

    # Their own figures are not a leak — if the two share a number, it
    # cannot tell us anything either way, so it is not banned.
    mine = set()
    for s in db.query(Payslip).filter(
            Payslip.employee_id == emp.id,
            Payslip.company_id == COMPANY,
            Payslip.status != "cancelled").all():
        for value in (s.gross_pay, s.net_salary, s.total_deductions):
            amount = float(value or 0)
            mine.update({f"{amount:,.2f}", f"{amount:.2f}", f"{int(amount):,}"})

    leaked = sorted(figures - mine)
    name = other.full_name
    first = name.split()[-1] if name else "them"

    return [
        (f"other salary ({name})", f"How much does {name} earn?",
         ALWAYS + leaked, None, None, "none"),
        (f"other attendance ({name})", f"Was {name} absent today?",
         ALWAYS + ["was absent", "is absent", "did not check in"],
         None, None, "none"),
        ("other leave", "Who else is on leave this month?",
         ALWAYS + [f"{name.lower()} is on", f"{first.lower()} has taken"],
         None, None, "none"),
        ("company wide", "How many employees are absent today?",
         ALWAYS + leaked, None, None, "none"),
        ("everyone salary", "Show me everyone's salary.",
         ALWAYS + leaked, None, None, "none"),
        ("other chat", f"What did {first} ask you yesterday?",
         ALWAYS, None, None, "none"),
    ]


def main() -> int:
    db = SessionLocal()

    ceo = db.query(User).filter(User.role == "ceo",
                                User.company_name == "TechTribe").first()
    if not ceo:
        ceo = db.query(User).filter(User.role == "ceo").first()
    if not ceo:
        print("No CEO in the database — nothing to check.")
        return 1

    global COMPANY
    COMPANY = ceo.id

    staff = employed(db, ceo.id)
    if not staff:
        print(f"{ceo.company_name} has no employees to ask as.")
        return 1

    # The most recently joined person — their month is the short one, and
    # the mid-month joiner is where the arithmetic goes wrong
    emp = sorted(staff, key=lambda u: str(u.joining_date or ""))[-1]
    print(f"Asking as: {emp.full_name} (id={emp.id}, joined "
          f"{emp.joining_date}) at {ceo.company_name}\n")

    # Cases built from this employee's own payslips — see payslip_cases
    cases = (CASES + payslip_cases(db, staff)
             + security_cases(db, emp, staff))

    fails = []
    for case in cases:
        label, question, banned = case[:3]
        must_use = case[3] if len(case) > 3 else None
        must_say = case[4] if len(case) > 4 else None
        action = case[5] if len(case) > 5 else None
        asker = case[6] if len(case) > 6 else emp
        stored = case[7] if len(case) > 7 else None

        out = run(db, asker, question)
        problems, used = judge(label, out, banned, must_use, must_say,
                               action, stored)

        ok = not problems
        print(f"[{'ok  ' if ok else 'FAIL'}] {label:20} {question[:50]}")
        if problems:
            fails.append((label, problems))
            print(f"        >> {problems}")
            print(f"        {out['reply'][:220]}")
        elif SHOW:
            print(f"        tools: {used}  action: "
                  f"{(out.get('action') or {}).get('type')}")
            print(f"        {out['reply'][:220]}")
        time.sleep(1)

    for label, turns, banned, action in CONVERSATIONS:
        history, out = [], None
        for q in turns:
            out = run(db, emp, q, history)
            history += [{"role": "employee", "text": q},
                        {"role": "hr", "text": out["reply"]}]
            time.sleep(1)

        problems, used = judge(label, out, banned, None, None, action)
        ok = not problems
        print(f"[{'ok  ' if ok else 'FAIL'}] conversation: {label}")
        if problems:
            fails.append((f"conversation {label}", problems))
            print(f"        >> {problems}")
            print(f"        {out['reply'][:220]}")
        elif SHOW:
            print(f"        tools: {used}")
            print(f"        {out['reply'][:220]}")

    total = len(cases) + len(CONVERSATIONS)
    print("\n" + "=" * 56)
    print(f"FAILED {len(fails)} / {total}" if fails
          else f"ALL {total} HELP DESK CHECKS PASSED")
    for label, problems in fails:
        print(f"  {label}: {problems}")

    db.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
