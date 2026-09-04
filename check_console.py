"""
CEO console — the questions a CEO actually asks
───────────────────────────────────────────────
    py check_console.py            run every case
    py check_console.py --show     print each full reply as well

This makes real model calls, so it costs tokens and takes a few minutes.
It exists because the console's failures were never crashes — every one
of them was a fluent, confident, wrong sentence. Only reading the answers
catches that, so this reads them for the things that go wrong:

    · giving up and asking the CEO for a name
    · a payslip "attached" when nothing is
    · a month's figures presented as another month's
    · performance read out of attendance
    · closing filler
    · a number that appears in no tool result

Exit code is 1 on any failure.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.agents.chat_agent import _FILLER
from app.agents.hr_console_agent import ask_console
# ──── This script works across companies, and says so ────
# The tenant guard refuses any query on a session that has not declared
# which company it is for. These tools audit or repair the whole
# database, so crossing companies IS the job — the point is that it is
# declared rather than assumed, and appears in the list
# `check_tenancy.py` prints.
from app.utils.tenancy import unscoped_session


def SessionLocal():          # noqa: N802  (same name, declared scope)
    return unscoped_session("check_console: drives the CEO console for one company")
from app.models.payroll import Payslip
from app.models.user import User

SHOW = "--show" in sys.argv

# (label, question, phrases that must NOT appear)
GIVES_UP = ["no specific name", "please provide", "names were given",
            "provide the missing"]
# Judged by the regex `clean_reply` uses — filler is a sign-off at the
# END, not the same words inside a real question.
FILLER = []
FAKE_ATTACH = ["slip is attached", "slips are attached"]
# The leave tool never looked at attendance, so it cannot conclude this
# Every phrasing this one claim has arrived in so far. Nobody
# being on leave says nothing about who turns up.
LEAVE_IS_NOT_ATTENDANCE = ["accounted for", "are present", "at work",
                           "no absences", "are available",
                           "nobody is absent"]
# Gender is not recorded anywhere, so any pronoun for an employee is a
# guess about a real colleague. Checked on every case, not a chosen few.
GENDERED = [" he ", " he.", " she ", " she.", " his ", " her ",
            " him ", " him.", "he is", "she is"]
# An absence question answered out of the leave table. "days taken" and
# "took leave" are how that answer reads when it happens.
LEAVE_NOT_ABSENCE = ["days taken", "took leave", "has not taken any leave"]
# Attendance standing in for an appraisal that does not exist
ATTENDANCE_AS_PERFORMANCE = ["needs improvement because", "been absent",
                             "absent without leave", "clean month",
                             "perfect attendance"]

CASES = [
    # ── people ──
    ("joined this month", "How many employees joined this month?", GIVES_UP),
    ("on probation", "How many employees are currently on probation?", GIVES_UP),
    ("departments", "Which departments have the most employees?", GIVES_UP),

    # ── attendance ──
    ("today summary", "Show me today's attendance summary.", GIVES_UP),
    ("present today", "How many employees are present today?", GIVES_UP),
    ("absent today", "How many employees are absent today?", GIVES_UP),
    ("august summary", "Show me August attendance summary for the entire company.",
     GIVES_UP + FAKE_ATTACH),
    ("most absences", "Which employee had the most absences in August?", GIVES_UP),

    # ── leave ──
    # An empty leave list must not be turned into a claim about attendance
    ("on leave today", "How many employees are on leave today?",
     GIVES_UP + LEAVE_IS_NOT_ATTENDANCE),
    ("who on leave", "Who is on leave today?",
     GIVES_UP + LEAVE_IS_NOT_ATTENDANCE),
    ("pending leave", "Show me all pending leave requests.", GIVES_UP),
    ("highest leave", "Who has the highest leave usage this year?", GIVES_UP),
    # A month is not a day and not a year — see get_leave_taken
    ("leave this month", "Who took leave this month?", GIVES_UP, "leave_taken"),
    ("leave in august", "Who was on leave in August?", GIVES_UP, "leave_taken"),
    ("high usage", "Are there any employees with unusually high leave usage?",
     GIVES_UP),

    # ── payroll: the month must be named ──
    ("payroll this month", "What is the total payroll for this month?",
     GIVES_UP + FAKE_ATTACH),
    ("payroll last month", "What was the total payroll last month?", GIVES_UP),
    ("payroll change", "How much has payroll changed compared to last month?",
     GIVES_UP),
    ("payroll by dept", "Show me payroll by department.", GIVES_UP),
    ("costliest dept", "Which department has the highest payroll cost?", GIVES_UP),
    ("salary changes", "Are there any unusual salary changes this month?", GIVES_UP),

    # ── performance: must refuse, without borrowing attendance ──
    ("perf overall", "How is overall employee performance this month?",
     GIVES_UP + ["clean month", "performing well based on attendance"]),
    ("perf top", "Which employees are performing exceptionally well?",
     GIVES_UP + ["clean month"]),
    ("perf dept", "Which departments have the best performance?",
     GIVES_UP + ["clean month"]),

    # ── queries ──
    ("queries", "Is there any queries from employees?", GIVES_UP),

    # ── recruitment ──
    ("hiring", "How is the recruitment process going?", GIVES_UP),
    ("openings", "Show me the job openings.", GIVES_UP),

    # ── the month as a whole ──
    ("hr summary", "Give me a complete HR summary for this month.", GIVES_UP),
    ("hr report aug", "Give me the HR report for August.", GIVES_UP),
    ("hr issues", "What are the biggest HR issues right now?", GIVES_UP),

    # ── the answers that were wrong on the CEO's second pass ──
    # Each names the tool that must run: right words off the wrong
    # source read exactly like a good answer.
    ("who works here", "Who works here?", GIVES_UP, "headcount"),
    ("people in company", "How many people work in the company?",
     GIVES_UP, "headcount"),
    ("active employees", "How many active employees do we have?",
     GIVES_UP, "headcount"),
    ("most absences", "Which employee has the most absences?",
     GIVES_UP + LEAVE_NOT_ABSENCE, "attendance_period"),
    ("attendance last month", "How was attendance last month?",
     GIVES_UP + LEAVE_NOT_ABSENCE + ["stable"], "attendance_period"),
    ("dept attendance", "Which department has attendance problems?",
     GIVES_UP, "attendance_period"),
    ("late today", "Is anyone late today?", GIVES_UP,
     "attendance_today_company"),
    ("dept most leave", "Which department takes the most leave?",
     GIVES_UP, None),
    ("leave this week", "Who will be on leave this week?", GIVES_UP, None),
    ("salary expense", "What is our total salary expense?", GIVES_UP, None),
    ("salaries changed", "Did salaries change this month?",
     GIVES_UP + ["therefore, there have been no changes",
                 "therefore there have been no changes"], "salary_changes"),
    # The tool alone was not enough: the reply named the employee whose
    # deductions EXCEEDED gross instead of the one with the most, and
    # the case still passed. The ranking figure is what was asked for.
    ("highest deductions", "Who had the highest deductions last month?",
     GIVES_UP + ["no deductions recorded"], "payroll_period",
     ["58,096", "58096"]),
    ("needs improvement", "Who needs improvement?",
     GIVES_UP + ATTENDANCE_AS_PERFORMANCE, "performance_data"),
    ("doing well", "Who is doing well?",
     GIVES_UP + ATTENDANCE_AS_PERFORMANCE, "performance_data"),
    ("perf dropped", "Has anyone's performance dropped recently?",
     GIVES_UP + ATTENDANCE_AS_PERFORMANCE, "performance_data"),
    # Both tools now carry records-vs-people, so either may answer —
    # what must not happen is meetings reported as people.
    # ⚠ WORDING WAS THE WRONG THING TO CHECK.
    # This demanded one of "record"/"records"/"meeting"/"separate". The
    # console answered "We have interviewed 2 candidates. The interviews
    # held include Muhammad Anas for two different roles..." — the right
    # count AND the distinction, in words that were not on the list.
    #
    # That is the trap this project named in Chunk 51 after hitting it
    # four times: counting the SHAPES an answer can take, and the model
    # finding a new one. The substance is what matters — the number of
    # PEOPLE, never the number of meetings reported as people — so both
    # sides of the case are built from the rows.
    ("interviewed count", "How many candidates have we interviewed?",
     lambda db, cid: GIVES_UP + _meetings_as_people(db, cid),
     ("hiring_pipeline", "interview_schedule"),
     lambda db, cid: _people_interviewed(db, cid)),
    ("hiring now", "Are we hiring anyone right now?",
     GIVES_UP + ["we are not hiring", "not hiring anyone"],
     "hiring_pipeline"),
    ("strongest candidate", "Who is our strongest candidate?",
     GIVES_UP, ("hiring_pipeline", "candidates_for_job")),
    ("applied count", "How many people have applied?", GIVES_UP, None),
    ("upcoming interviews", "Do we have any interviews coming up?",
     GIVES_UP, None),
    ("paid last month", "How much did we pay last month?", GIVES_UP, None),
    # The slip carries every deduction line and the working behind it.
    # "There may have been deductions" is the answer of a bot that did
    # not read what it was holding.
    ("why lower", "Why was Anas's salary lower than the gross?",
     GIVES_UP + ["may have been", "might have been", "suggests that",
                 "can be found in the attached", "found in the attached"],
     "employee_payslip",
     # the actual figures, not a pointer to where they live
     ["28,571", "28571", "2,285", "2285"]),

    # ── the CEO's third pass: each of these had a wrong answer ──
    # "Unauthorised" is a decision nobody records. The breakdown that
    # DOES exist has to appear instead of the verdict.
    ("unauthorised", "Who had the most unauthorized absences last month?",
     GIVES_UP, "attendance_period",
     ["no request", "no leave request", "refused"]),
    # gross - deductions != net, because one net was floored at zero
    ("payroll reconciles", "What was the total payroll for August?",
     GIVES_UP + ["net is below", "net was below zero",
                 "payroll is negative", "below the calculated"],
     "payroll_period",
     # any wording that explains WHY the three totals do not subtract
     ["2,285", "2285", "could not be taken", "set to zero",
      "below zero", "exceed", "net of zero", "floor"]),
    # People, not employee-days
    ("how many absent", "How many employees were absent in August?",
     GIVES_UP, "attendance_period", ["2 employee", "two employee"]),
    # "Attendance issues" is not a field — the measure must be named
    ("dept issues", "Which department had the most attendance issues?",
     GIVES_UP, "attendance_period", ["absent days", "absence rate"]),
    # A booking is not a day taken — and what the answer must say
    # depends on whether anybody currently HAS a booking.
    ("most leave year", "Which employee took the most leave this year?",
     GIVES_UP, "leave_usage", lambda db, cid: _leave_expectation(db, cid)),
    # The list is every upcoming leave, not this week's
    ("upcoming leaves", "Are there any upcoming leaves?",
     GIVES_UP + ["this week"], None, None),

    # A week is Monday to Sunday, and a leave question stays a leave
    # question when the follow-up names no subject of its own.
    ("leave next week", "Who will be on leave next week?", GIVES_UP,
     "leave_window", None),
    ("leave this week", "Who will be on leave this week?", GIVES_UP,
     "leave_window", None),

    # ── divide and conquer: ask about scope, never about data ──
    # A question with one correct answer is answered, not interrogated.
    ("no needless ask", "How many employees do we have right now?",
     GIVES_UP + ["which department do you mean"], "headcount", None),
    ("no ask on a total", "How many employees are absent today?",
     GIVES_UP + ["which department do you mean"], None, None),

    # ── Roman Urdu and typos ──
    ("RU absent", "aaj kitny employees absent hain", GIVES_UP),
    ("RU attendance", "attendance ka summary do", GIVES_UP),
    ("RU august", "august ki attendance dikhao", GIVES_UP + FAKE_ATTACH),
    ("RU salary", "salary ka kya scene hai", GIVES_UP),
    ("RU leave", "is mahine kis ne chutti li", GIVES_UP, "leave_taken"),
]


# Follow-ups: (label, [turns], phrases the LAST reply must NOT contain)
# "them" has to mean the set the previous answer produced.
CONVERSATIONS = [
    ("absent -> departments",
     ["How many employees are absent today?",
      "Which departments are they from?"], GIVES_UP),
    ("payroll -> it",
     ["What is the total payroll for August?",
      "How much was it in July?"], GIVES_UP + ["august"]),
    # The tool is the real signal here. Banning the word "absent" caught
    # a correct answer that happened to say "the leave records show
    # nobody absent" — right tool, right facts, wrong word list.
    # "What about next week?" carries no pronoun and no subject — it is
    # the same question with one thing swapped, and it used to land on
    # the attendance tool.
    ("leave -> next week",
     ["Who is on leave today?",
      "How many of them are from Backend?",
      "What about next week?"],
     GIVES_UP + LEAVE_IS_NOT_ATTENDANCE + ["absent", "attendance record"],
     "leave_window"),
    # An elliptical follow-up that swaps the MONTH, not the subject
    # Ambiguous -> ask with the real departments -> answer that slice
    ("ask then scope",
     ["Tell me about the employees", "Backend"],
     GIVES_UP + ["frontend"], "headcount"),
    # They ignore the question and ask something else: it is dropped
    ("ask then changed subject",
     ["Tell me about attendance", "What was the total payroll for August?"],
     GIVES_UP + ["which department"], "payroll_period"),
    ("payroll -> what about june",
     ["What was the total payroll for July?", "What about June?"],
     GIVES_UP + ["july"], "payroll_period"),
    ("payroll -> why lower",
     ["What was the total payroll for August?",
      "Why was the net lower than the gross?"],
     GIVES_UP + ["net is below", "below the calculated"], "payroll_period"),
    ("leave -> subset",
     ["Who is on leave today?",
      "How many of them are from Backend?"],
     GIVES_UP + LEAVE_IS_NOT_ATTENDANCE, "leave_today_company"),
]


def pick_company(db):
    """
    The company with payroll on record, not simply the first CEO row.

    ═══ WHY THIS IS NOT `.first()` ═══
    It was, and `.first()` returned a company with zero payslips. Every
    payroll case passed by agreeing that nothing had been processed —
    the "not processed" branch was checked twenty times and the branch
    that reads real figures was never checked at all. A suite that only
    ever sees empty data cannot fail on a wrong number.
    """
    ceos = db.query(User).filter(User.role == "ceo").all()
    if not ceos:
        return None

    def slips(c):
        return db.query(Payslip).filter(
            Payslip.company_id == c.id,
            Payslip.status != "cancelled",
        ).count()

    return max(ceos, key=slips)


def _hiring(db, company_id):
    from app.utils.hr_company_data import run_company_tools
    try:
        return (run_company_tools(db, company_id, ["hiring_pipeline"])
                .get("hiring_pipeline") or {})
    except Exception:                                           # noqa: BLE001
        return {}


def _people_interviewed(db, company_id):
    """The candidate count the answer must state — not the meeting count."""
    d = _hiring(db, company_id)
    n = d.get("candidates_interviewed")
    if n is None:
        return ["interview"]          # nothing to compare; keep it loose
    if n == 0:
        return ["no candidate", "nobody", "none", "0 candidate"]
    return [f"{n} candidate", f"interviewed {n}", f"{n} people",
            f"{n} person" if n == 1 else f"{n} unique"]


def _meetings_as_people(db, company_id):
    """
    Phrases that would mean the meeting count was reported as people.

    One candidate interviewed for two roles is two records and one
    person. Saying "we interviewed 5 candidates" when 5 is the number of
    interview records is the specific failure — and it is only wrong
    because of what the rows say, so it is built from them.
    """
    d = _hiring(db, company_id)
    records = d.get("interview_records")
    people = d.get("candidates_interviewed")
    if records is None or people is None or records == people:
        return []                     # no distinction to get wrong today
    return [f"{records} candidates", f"interviewed {records}",
            f"{records} people"]


def _leave_expectation(db, company_id):
    """
    What the answer to "who took the most leave?" must contain, today.

    The distinction being protected is real: `days_debited_from_balance`
    counts an approved future booking, `days_already_taken` does not, and
    the console once reported the first as the second. But which of them
    the answer has to SPELL OUT depends on the data:

        somebody has days booked ahead  ->  the answer must separate them
        nobody does                     ->  it must state the days taken

    Returning None would skip the check; it never returns None, so the
    case cannot quietly stop testing anything.
    """
    from app.utils.hr_company_data import run_company_tools

    try:
        data = run_company_tools(db, company_id, ["leave_usage"])
        rows = (data.get("leave_usage") or {}).get("by_employee") or []
    except Exception:                                           # noqa: BLE001
        rows = []

    if not rows:
        return ["no leave", "nobody", "no approved leave", "0 days"]

    if any((r.get("days_booked_ahead") or 0) > 0 for r in rows):
        # A booking exists, so conflating it with a day taken is exactly
        # the failure. The answer has to name the difference.
        return ["booked", "still to come", "upcoming", "not yet taken"]

    # Nothing is booked ahead, so the answer just has to be the right
    # number of days actually taken.
    top = max(rows, key=lambda r: r.get("days_already_taken") or 0)
    n = top.get("days_already_taken") or 0
    return [f"{n} day", f"{n} days"] if n else ["no leave", "0 day", "none"]


def main() -> int:
    db = SessionLocal()
    ceo = pick_company(db)
    if not ceo:
        print("No CEO in the database — nothing to check.")
        return 1
    print(f"Company under test: {ceo.company_name} (id={ceo.id})\n")

    fails = []
    for case in CASES:
        # A case may name the tool that must have run. Right words off the
        # wrong source still reads as a good answer, and it isn't one.
        label, question, banned = case[:3]
        must_use = case[3] if len(case) > 3 else None
        # Words the answer MUST contain. Banned phrases catch the wrong
        # answer; this catches the answer that quietly drops half the
        # question — a count given without the thing being counted.
        must_say = case[4] if len(case) > 4 else None

        out = ask_console(db=db, company_id=ceo.id,
                          ceo_name=ceo.full_name or "", message=question,
                          history=[])
        reply = out["reply"]
        low = reply.lower()
        # A clarification is a source without a tool name
        used = [s.get("name") for s in out["sources"] if s.get("name")]

        # ⚠ `problems` IS STARTED HERE, NOT LATER.
        # It used to be created by the `banned` comprehension BELOW the
        # source-shape loop — so that loop's `problems.append` had no
        # list to append to on the first case, and on later cases the
        # comprehension REASSIGNED the name and threw away whatever it
        # had found.
        #
        # The source-shape check was added to stop the blank-screen crash
        # coming back, and it has been dead since the day it was written:
        # never reporting, and set up to raise NameError if it ever did.
        # A guard that reads as working and does nothing is the thing
        # this suite exists to catch.
        problems = []

        # ──── The shape the UI has to render ────
        # Every source is a dict, and one without a `name` must still say
        # what it IS. `s.name.replace()` on a nameless source unmounted
        # the CEO's console and left a blank screen; the contract is
        # checked here so the producer cannot change it in silence.
        for s in out["sources"]:
            if not isinstance(s, dict) or not (s.get("name") or s.get("kind")):
                problems.append(f"source with neither name nor kind: {s!r}")

        # `banned` may be built from the data too, for the same reason
        # `must_say` may: the wrong answer is often a REAL figure used
        # for the wrong thing, and which figure that is depends on the
        # rows.
        blocked = banned(db, ceo.company_id) if callable(banned) else banned
        problems += [b for b in blocked if b in low]
        problems += [g for g in GENDERED if g in low]
        if _FILLER.search(reply):
            problems.append("ends on filler")
        # must_use may name one tool or several acceptable ones — some
        # questions have more than one right source.
        if must_use:
            allowed = (must_use,) if isinstance(must_use, str) else must_use
            if not any(t in used for t in allowed):
                problems.append(f"used {used}, needed one of {list(allowed)}")
        # ⚠ `must_say` MAY BE A FUNCTION OF THE DATA.
        # A fixed list asserts a particular state of the database, and
        # this one aged out: the leave case demanded the words "booked"
        # or "still to come", which were right when written because
        # Sheikh Wasi had a day approved for 4-5 September. On the 4th
        # that day was taken, `days_booked_ahead` became 0, the console
        # answered correctly — and the check failed.
        #
        # Same lesson as `check_integrations`: assert that the answer
        # AGREES WITH THE DATA, not that the data is in a given state.
        wanted = must_say(db, ceo.company_id) if callable(must_say) else must_say
        if wanted and not any(w.lower() in low for w in wanted):
            problems.append(f"says none of {list(wanted)}")

        # A payslip claimed but not sent
        if any(p in low for p in FAKE_ATTACH) and not out.get("attachments"):
            problems.append("claims an attachment that is not there")

        ok = not problems
        print(f"[{'ok  ' if ok else 'FAIL'}] {label:20} {question[:52]}")
        if problems:
            fails.append((label, problems))
            print(f"        >> {problems}")
            print(f"        {reply[:200]}")
        elif SHOW:
            print(f"        tools: {[s['name'] for s in out['sources']]}")
            print(f"        {reply[:200]}")
        time.sleep(1)

    # ── follow-ups: the second turn is the one being judged ──
    for conv in CONVERSATIONS:
        label, turns, banned = conv[:3]
        must_use = conv[3] if len(conv) > 3 else None
        hist, reply, tools = [], "", []
        for q in turns:
            out = ask_console(db=db, company_id=ceo.id,
                              ceo_name=ceo.full_name or "", message=q,
                              history=hist)
            reply = out["reply"]
            tools = [s.get("name") for s in out["sources"]
                     if s.get("name")]
            # Same history shape the route builds — sources included, so
            # a narrowing follow-up can carry the tools forward.
            hist += [{"role": "ceo", "text": q},
                     {"role": "hr", "text": reply,
                      "sources": out["sources"]}]
            time.sleep(1)

        low = reply.lower()
        problems = [b for b in banned if b in low]
        problems += [g for g in GENDERED if g in low]
        if _FILLER.search(reply):
            problems.append("ends on filler")
        if must_use and must_use not in tools:
            problems.append(f"last turn used {tools}, needed {must_use}")

        ok = not problems
        print(f"[{'ok  ' if ok else 'FAIL'}] follow-up: {label}")
        if problems:
            fails.append((f"follow-up {label}", problems))
            print(f"        >> {problems}")
            print(f"        {reply[:200]}")
        elif SHOW:
            print(f"        tools: {tools}")
            print(f"        {reply[:200]}")

    total = len(CASES) + len(CONVERSATIONS)
    print("\n" + "=" * 56)
    print(f"FAILED {len(fails)} / {total}" if fails
          else f"ALL {total} CONSOLE CHECKS PASSED")
    for label, problems in fails:
        print(f"  {label}: {problems}")

    db.close()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
