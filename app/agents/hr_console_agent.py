"""
The CEO's HR console — same shape as the help desk, different door
──────────────────────────────────────────────────────────────────
    route → gather → compose

Identical architecture to `chat_agent.py`, and deliberately a separate
file with a separate tool table.

═══════════════════════════════════════════════════════════
WHY NOT ONE AGENT WITH A ROLE FLAG
═══════════════════════════════════════════════════════════
Because a flag can be wrong. `if is_ceo: tools = COMPANY_TOOLS` is one
mistaken line away from handing an employee the whole company, and that
line would sit in the middle of a function that gets edited for other
reasons for years.

Two agents, two tool tables, two routers — and `require_ceo` on the only
route that reaches this one. An employee cannot get here by phrasing a
message cleverly; there is no phrasing, because there is no path.

═══════════════════════════════════════════════════════════
WHAT THIS ONE MAY NOT DO
═══════════════════════════════════════════════════════════
It reads. It does not create requests, approve leave, change salaries or
open cases. The CEO already has screens for every one of those, and each
carries its own confirmation. A console that could act on a sentence
would be one typo away from approving the wrong month's payroll.

It also cannot read a transcript or the contents of a confidential case.
`hr_company_data.py` simply has no function that returns one.
"""

import json
import re
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.chat_agent import (
    _extract_json, _invoke_with_retry, clean_reply, decide_language,
    _today_str,
)
from app.utils.hr_company_data import run_company_tools
from datetime import timedelta

from app.utils.pkt import get_pkt_today



# ══════════════════════════════════════════════
# "august" is a period, whatever the model says
# ══════════════════════════════════════════════
# The prompt asks for "may" -> "2026-05" and the model does it most of
# the time. When it does not, `employee_payslip` gets no period, returns
# the LIST of months instead of the one asked for, and the CEO who wanted
# August's slip gets three files attached.
#
# Same approach as the weekday fix on the employee side: the model is
# good at reading intent and unreliable at turning words into numbers, so
# it keeps the intent and Python does the conversion.
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7,
    "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")\b", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def month_from_text(message: str, today) -> Optional[str]:
    """"wasi ki august slip" -> "2026-08". None if no month is named."""
    found = _MONTH_RE.search(message or "")
    if not found:
        return None
    month = _MONTHS[found.group(1).lower()]
    year_hit = _YEAR_RE.search(message or "")
    year = int(year_hit.group(1)) if year_hit else today.year
    return f"{year:04d}-{month:02d}"



# The calendar lives in one place — see utils/relative_dates.py
from app.utils.relative_dates import resolve_relative, week_window


class ConsoleState(TypedDict, total=False):
    db: object
    forced: dict           # slices the CEO chose, not the model
    company_id: int
    ceo_name: str
    message: str
    history: list

    plan: dict
    data: dict

    reply: str
    sources: list
    attachments: list
    error: str


HISTORY_TURNS = 10


# ══════════════════════════════════════════════
# Node 1: Route
# ══════════════════════════════════════════════
ROUTER_PROMPT = """Routing step of a company's HR console. The person asking
is the CEO. Decide what data answers their question.

Today is {today}. You are speaking to {name}.

=== CONVERSATION ===
{history}
=== LATEST MESSAGE ===
{message}
=== END ===

JSON only:
{{"language": "english"|"roman_urdu", "tools": [], "person": null|str,
  "period": "YYYY-MM"|null, "year": num|null, "month": num|null,
  "on_date": "YYYY-MM-DD"|null, "status": "open"|"closed"|null}}

on_date  a single day, for employee_attendance
status   for employee_queries — what is still live, or what is finished

TOOLS — name every one that could help.
  headcount            how many people, by status and department
  new_joiners          who is still on PROBATION, and how long is left.
                       NOT "who joined this month" — different question.
  joiners_in_period    who joined in a given month. "period" YYYY-MM,
                       default this month.

  THE WHOLE COMPANY ON ONE DAY — use these whenever the question is
  about everyone. They need NO name.
  attendance_today_company  every employee's status for a day: present,
                            late, on leave, absent, and by department.
                            "on_date" for a day other than today.
  attendance_period         the whole company's attendance for ONE MONTH:
                            working days, present, absent, leave, late,
                            per person and per department, with the rates.
                            "period" = "YYYY-MM". This is the tool for
                            "how was attendance last month" and for
                            "who has the most absences".
  leave_today_company       who is on leave that day, what is coming up,
                            and what is pending approval
  leave_usage               who has USED the most approved leave, by
                            person and department. "year" optional.
  leave_window              who is on approved leave between TWO DATES.
                            "date_from" and "date_to". This is the tool
                            for a WEEK — "who is on leave next week",
                            "what about next week". A week is not a day
                            and not a month.
  leave_taken               who was off during ONE MONTH and for how many
                            days of it. "period" = "YYYY-MM". This is the
                            tool for "who took leave this month" — the
                            other three answer a day, a year, or what is
                            still to come, and none of them is a month.
  attendance_outliers  who is late or absent beyond this company's own
                       threshold  (year/month optional)
  employee_snapshot    ONE named person: attendance, leave, department.
                       NOT their payslip — set "person".
  employee_leave       ONE person's leave: what is left, what they took
  employee_attendance  ONE person's attendance. With "on_date" it is that
                       single day in full — check-in time, check-out, and
                       whether it was even a working day. Without one, the
                       month.
  employee_loans       advances still running — one person, or everyone
                       if no name is given
  salary_structures    who has a salary set and WHO DOES NOT. Payroll
                       skips anyone without one, silently.
  employee_payslip     ONE named person's SALARY SLIP — set "person",
                       and "period" for a month. Without a period it
                       lists which months exist. Use this for anything
                       with "slip", "salary", "pay" and a NAME in it.
  former_employees     who used to work here, and their exit file

  RECRUITMENT
  job_posts            roles posted, and how many applied to each.
                       "name" narrows it to one title.
  candidates_for_job   who applied, with the CV screening score and the
                       skills they are missing. "name" = the JOB title.
  interview_schedule   interviews coming up and already held, with the
                       panel. "name" narrows it to one candidate.
  interview_feedback   how a candidate scored — panel notes and the
                       final ranking. "name" = the CANDIDATE.
  hiring_pipeline      the funnel in one answer, with each application
                       in a real stage, and which roles nobody applied to

  THE WHOLE MONTH
  hr_summary           people, attendance, leave, payroll, hiring and
                       employee queries for one month. "period" YYYY-MM.
                       Use it for "give me the HR summary/report".
  hr_issues            what is actually wrong right now, worst first —
                       overdue requests, unset salaries, absences past
                       the threshold, probations ending. Use it for
                       "what are the biggest HR issues".
  leave_overview       pending requests, upcoming absences, days about
                       to lapse
  payroll_period       ONE month's wage bill, by department, AND whether
                       that month has been run at all. "period" YYYY-MM,
                       default the current month. Use this, not
                       payroll_overview, for any "total payroll" question.
  payroll_comparison   one month against the one before. Says plainly
                       when a comparison is impossible.
  salary_changes       structures edited in a month, and pay that
                       actually moved since the month before
  performance_data     what this system holds about performance. Use it
                       for ANY performance, appraisal or "who is doing
                       well" question.
  payroll_overview     (older; prefer payroll_period)
  open_items           requests waiting on the CEO, and which are overdue
  case_patterns        counts of concerns raised — a summary only
  employee_queries     the queries THEMSELVES: what was asked, by whom,
                       what came of it. This is what "show me", "dikhao",
                       "list them", "which ones" means after any count.
                       "status" may be "open" or "closed".

person   the employee's name, only with "employee_snapshot"

EXAMPLES
  "kitne employees hain"              -> ["headcount"]
  "who is coming late"                -> ["attendance_outliers"]
  "how is Zeeshan doing"              -> ["employee_snapshot"], person "Zeeshan"
  "show me Imran's salary slip"       -> ["employee_payslip"], person "Imran"
  "show imran may slip"               -> ["employee_payslip"], person "Imran",
                                         period "2026-05"
  "show May month"  (after a name)    -> ["employee_payslip"], SAME person,
                                         period "2026-05"
  "what needs my decision"            -> ["open_items"]
  "salary cost this month"            -> ["payroll_period"]
  "total payroll last month"          -> ["payroll_period"], period = last
  "how much has payroll changed"      -> ["payroll_comparison"]
  "payroll by department"             -> ["payroll_period"]
  "which department costs the most"   -> ["payroll_period"]
  "unusual salary changes this month" -> ["salary_changes"]
  "how is performance this month"     -> ["performance_data"]
  "who is performing well"            -> ["performance_data"]
  "best performing department"        -> ["performance_data"]
  "kaun probation par hai"            -> ["new_joiners"]
  "how many joined this month"        -> ["joiners_in_period"]
  "today's attendance summary"        -> ["attendance_today_company"]
  "how many are present/absent today" -> ["attendance_today_company"]
  "aaj kitne absent hain"             -> ["attendance_today_company"]
  "who is on leave today"             -> ["leave_today_company"]
  "pending leave requests"            -> ["leave_today_company"]
  "who takes the most leave"          -> ["leave_usage"]
  "unusually high leave usage"        -> ["leave_usage"]
  "how was attendance last month"     -> ["attendance_period"], period = last
  "who has the most absences"         -> ["attendance_period"]
  "which department has attendance
   problems"                          -> ["attendance_period"]
  "who is on leave next week"         -> ["leave_window"]
  "what about next week" (after a
   leave question)                    -> ["leave_window"], same subject
  "who took leave this month"         -> ["leave_taken"], period = this month
  "is mahine kis ne chutti li"        -> ["leave_taken"], period = this month
  "who was on leave in August"        -> ["leave_taken"], period "2026-08"
  "august ki attendance dikhao"       -> ["attendance_outliers"],
                                         year 2026, month 8
  "any complaints lately"             -> ["case_patterns"]
  "any queries from employees?"       -> ["employee_queries"]
  "show here" / "dikhao" / "list them" after ANY summary
                                      -> the tool named in the previous
                                         answer's "detail_from"
  "what is still open"                -> ["employee_queries"], status "open"
  "imran ki chuttiyan"                -> ["employee_leave"], person "imran"
  "was Bilal in on the 19th?"         -> ["employee_attendance"],
                                         person "Bilal", on_date "2026-08-19"
  "Imran ki august attendance"        -> ["employee_attendance"],
                                         person "Imran", year 2026, month 8
  "kis ka advance chal raha hai"      -> ["employee_loans"]
  "kis ki salary set nahi hui"        -> ["salary_structures"]
  "how is hiring going"               -> ["hiring_pipeline"]
  "what roles are open"               -> ["job_posts"]
  "who applied for Full Stack"        -> ["candidates_for_job"],
                                         name "Full Stack"
  "koi interview hai is hafte"        -> ["interview_schedule"]
  "us candidate ka kya bana"          -> ["interview_feedback"], name =
                                         the candidate
  "complete HR summary for this month"-> ["hr_summary"]
  "HR report for August"              -> ["hr_summary"], period 2026-08
  "biggest HR issues right now"       -> ["hr_issues"]
  "where can candidates see openings" -> ["job_posts"] (the answer is in
                                         hiring_pipeline too)

RULES
- FOLLOW "detail_from". Every summary you receive carries a
  "detail_from" field naming the tool that opens it. When the CEO says
  "show me", "dikhao", "list them", "which ones", "details" — call THAT
  tool. Never answer a request for detail by repeating the summary.
- A QUESTION ABOUT EVERYONE NEEDS A COMPANY TOOL. "How many", "who is",
  "show me the summary", "by department", "the whole company" — never
  answer these by asking the CEO for a name. If you find yourself about
  to say "no specific names were given", you have picked the wrong tool.
- LEAVE USAGE IS NOT ABSENCE. Somebody who never applies and does not
  turn up has LOW leave usage and an attendance problem. `leave_usage`
  answers the first; `attendance_outliers` answers the second.
- Name a tool rather than none. An extra costs only time.
- CARRY THE WHOLE SUBJECT FORWARD, not just the name. The conversation
  is above you; read it before deciding what the question is about.
      "...absent today?"  then "which departments are they from?"
          -> still attendance_today_company, same day
      "...payroll for August?"  then "how much was it in July?"
          -> payroll_period, period 2026-07 ("it" = payroll)
      "...who is on leave today?"  then "how many of them are Backend?"
          -> leave_today_company, and read by_department
  A follow-up almost never needs a NEW subject — it needs the same tool
  with one thing changed.

- "THEM", "THOSE", "OF THEM" MEAN THE SET THE LAST ANSWER PRODUCED.
  Not people in general, and not a different list that happens to be
  nearby. If the last answer was "nobody is on leave today", then "how
  many of them are from Backend" is asking about an EMPTY set, and the
  answer is none — switching to who is absent instead answers a question
  nobody asked and reads as though the two are the same thing.

- CARRY THE NAME FORWARD. "show May month" straight after a question
  about a person still means that person — read the conversation and
  set "person"
  again. Dropping it turns a question about one employee into a
  company-wide total, which then gets reported as if it were theirs.
- A MONTH NAME IS A PERIOD. "may" -> "2026-05", "january" -> "2026-01",
  using the current year unless they say otherwise.
- payroll_overview is the WHOLE COMPANY. Never use it to answer about
  one person.
- "name" MEANS DIFFERENT THINGS. For candidates_for_job it is the JOB
  title; for interview_feedback and interview_schedule it is the
  CANDIDATE. Read which one the question is about.
- A CANDIDATE IS NOT AN EMPLOYEE. Somebody who applied has no
  attendance, no leave and no salary here — do not reach for an employee
  tool to answer a question about them.
- Never invent a tool name.
- The CEO cannot read anyone's chat transcript and cannot see the
  contents of a confidential case. There is no tool for it — do not
  pretend otherwise."""


# Recognising a follow-up is the same problem on both sides — see
# utils/followup.py
from app.utils.followup import (ATTENDANCE_FAMILY, LEAVE_FAMILY,
                                PAYROLL_FAMILY, carried_tools,
                                enforce_topic,
                                is_narrowing_followup)


def route_node(state: ConsoleState) -> ConsoleState:
    plan = {"language": decide_language(state["message"],
                                        state.get("history")),
            "tools": [], "person": None,
            "period": None, "year": None, "month": None,
            "on_date": None, "status": None,
            # A week is a range: "next week" fills both of these
            "date_from": None, "date_to": None,
            # The slice in force. `department` is where they sit,
            # `role` is what they do inside it — users.department and
            # users.designation, two columns that were always different
            # things.
            "department": None, "role": None}

    history = "\n".join(
        f"{'CEO' if h['role'] == 'ceo' else 'HR'}: {h['text']}"
        for h in (state.get("history") or [])[-HISTORY_TURNS:]
    ) or "(nothing yet)"

    try:
        from app.utils.llm import chat_model

        llm = chat_model(temperature=0, max_tokens=800)

        raw = _invoke_with_retry(llm, [
            SystemMessage(content="You route HR questions. Reply with JSON only."),
            HumanMessage(content=ROUTER_PROMPT.format(
                today=_today_str(),
                name=state.get("ceo_name") or "the CEO",
                history=history,
                message=state["message"],
            )),
        ], "console-router")

        parsed = _extract_json(raw)
        if parsed is None:
            raise ValueError(f"no JSON in reply: {raw[:120]!r}")

        for k in plan:
            if k in parsed and parsed[k] is not None:
                plan[k] = parsed[k]

        # The model does not get a vote on this — see decide_language.
        plan["language"] = decide_language(state["message"],
                                           state.get("history"))
        if not isinstance(plan.get("tools"), list):
            plan["tools"] = []

        # A month in the question is a period, whether or not the model
        # noticed. Without this a request for one slip comes back with
        # every month attached.
        if not plan.get("period"):
            guessed = month_from_text(state["message"], get_pkt_today())
            if guessed:
                plan["period"] = guessed

        # "this month" / "last month" / "today" are calendar facts, and
        # the model kept getting them wrong. Anything it did not already
        # fill in gets filled here.
        for key, val in resolve_relative(state["message"],
                                         get_pkt_today()).items():
            if not plan.get(key):
                plan[key] = val

        # "How many of THEM..." — same subject, narrower question.
        if is_narrowing_followup(state["message"]):
            carried = carried_tools(state.get("history"))
            if carried and set(carried) != set(plan["tools"]):
                plan["carried_from_previous_turn"] = plan["tools"]
                plan["tools"] = carried

        # ──── What the CEO chose beats what the model inferred ────
        # `forced` comes from an answer to our own question. It is not a
        # guess to be improved on.
        for key, value in (state.get("forced") or {}).items():
            plan[key] = value

        # The question's own words get the last word on the subject.
        corrected = enforce_topic(state["message"], plan["tools"], plan)
        if set(corrected) != set(plan["tools"]):
            plan["router_chose"] = plan["tools"]
            plan["tools"] = corrected

    except Exception as e:                              # noqa: BLE001
        print(f"[hr] console router failed: {e}")
        plan["error"] = str(e)

    return {**state, "plan": plan}


# ══════════════════════════════════════════════
# Node 2: Gather
# ══════════════════════════════════════════════
def gather_node(state: ConsoleState) -> ConsoleState:
    plan = state.get("plan") or {}
    data = run_company_tools(
        state["db"], state["company_id"], plan.get("tools") or [],
        person=plan.get("person"), period=plan.get("period"),
        year=plan.get("year"), month=plan.get("month"),
        on_date=plan.get("on_date"), status=plan.get("status"),
        date_from=plan.get("date_from"), date_to=plan.get("date_to"),
        # BOTH dimensions, or the slice reaches the prompt and not the
        # query — which is the difference between an answer that is
        # right and one that only reads right.
        department=plan.get("department"), role=plan.get("role"),
    )
    return {**state, "data": data}


# ══════════════════════════════════════════════
# Node 3: Compose
# ══════════════════════════════════════════════
# The one instruction that separates this from a report generator is
# "say what it means". A CEO can already read a table; what they cannot
# do is watch eight people's attendance every week. An HR that hands
# back the same numbers in a sentence has added nothing.
ANSWER_PROMPT = """You are this company's HR, briefing the CEO.

Today is {today}. You are speaking to {name}.

=== CONVERSATION ===
{history}

=== THEIR QUESTION ===
{message}

=== COMPANY DATA ===
{data}

=== WHAT IS ATTACHED TO THIS REPLY ===
{attachment_note}

HOW TO REPLY
- Reply in {language}. "roman_urdu" = Urdu in English letters.
- Lead with the answer, then the number behind it. Three or four
  sentences.
- Plain text. No markdown, no bold, no headings.
- NO footnote or citation markers. Replies were coming back ending in a
  bare "4" or "0"; there is nothing to cite here and a stray digit after
  a sentence about headcount reads as a number that means something.

- WHEN THEY ASK TO SEE SOMETHING, LIST IT. Not a paragraph about it —
  the actual rows, one per line, short — "<name> — <what it is>, <state>,
  raised <date>". Repeating the count is the one thing they did not ask
  for.

- SOME QUERIES COME BACK PRIVATE, and that is deliberate. A grievance
  shows only that one is open and since when; a policy question shows
  what was asked but not who asked it. Say so plainly if it comes up —
  "one grievance is open, raised on the 29th; the details stay between
  the employee and me" — and do not apologise for it or work around it.

- ANSWER FROM THE TOOL THAT RAN, AND NOTHING ELSE. "Nobody is on leave"
  does NOT mean everybody is at work — the leave tool knows nothing about
  attendance. If the question needs both, say what you have and stop.
  An empty answer is finished when the emptiness has been stated. Do not
  round it off with what it "means": "nobody is on leave, so all staff
  are present and accounted for" is a second claim, about attendance,
  from a tool that never looked. Same for "so everyone is working" or
  "so there are no gaps in the team". Stop at "nobody is on leave."

- NO DATA IS NOT GOOD NEWS. Silence in one record is never evidence
  about another. Say what is missing and stop.
      Bad : "Payroll has not run, therefore salaries did not change."
      Good: "No salary structure was edited in September. Payroll has
             not run, so actual pay cannot be compared."
  Where a tool hands you a `conclusion` or `do_not_infer`, that wording
  was worked out from the data — use it rather than building your own.

- COUNT WHAT WAS ASKED FOR. Records, events and people are different
  numbers and the tools keep them apart. `interview_records` is
  meetings; `candidates_interviewed` is people. "How many candidates
  have we interviewed" is the second one, and if they differ, say both.
  Same for headcount: `employees_now` is who works here; every other
  count includes people who have left.

- HIRING IS ABOUT OPEN ROLES, NOT ABOUT HIRES MADE. `actively_hiring`
  and `open_roles` answer "are we hiring". `hired: 0` means nobody has
  been taken on yet — never read it as "we are not hiring" in the same
  answer that lists five published jobs.

- A SCORE IS NOT A RECOMMENDATION. `cv_match_score` is a screening
  filter. Where a `final_score` and a `ranking` exist, those are what the
  panel decided and those are what "strongest candidate" means. Say
  which measure you used, and if the record also shows missing skills or
  that the person left, say that in the same breath.

- TWO RECORDS, ONE NAME. If `names_on_more_than_one_record` or
  `same_name_different_records` is present, those are separate records that share a
  name — not one person in two departments, and not one applicant listed
  twice. Say so plainly, in those words: "two separate records under the
  name X". The system does not record whether it is the same person
  again, and neither do you. Do not resolve it by picking one, and do
  not write a sentence that says two and one in turn — "we interviewed 2
  candidates, the candidate is X" tells the CEO nothing.

- A CANDIDATE'S STAGE IS PART OF THE ANSWER. `stage` says it in words —
  "hired, then left the company" is not a rejection and not a live
  application. If you name somebody as the strongest candidate and their
  stage says they were hired and left, that belongs in the same answer,
  not left for the CEO to discover.

- NEVER CALL AN ABSENCE "UNAUTHORISED". This system records no such
  decision — there is no field for it, and nobody has ever made it. What
  it holds is `absence_kinds`: how many absent days had no leave request
  at all, how many had one that was refused, how many are still
  undecided. Give those. "15 days with no request at all, 2 where leave
  was refused" is a real answer; "18 unauthorised absences" is a verdict
  you are not in a position to pass on somebody.

- THE COMPANY'S NET IS THE COMPANY'S. Give `how_the_net_was_reached`
  first — it is the plain sum and it works. A company that paid anyone
  anything does not have a negative net, and must never be described as
  "below zero" or "below the calculated difference".
  `employee_level_exception` is about ONE person's slip: their
  deductions came to more than their salary, so THEIR net was floored
  and some deduction went uncollected. Say it second, say whose it is,
  and keep it visibly separate from the company figures.
  The shape, with the figures taken from the data and NEVER from here:
      "<month> net was <total_net> — gross <total_gross> less the
       <deductions_actually_taken> actually taken. Separately, <name>'s
       deductions came to more than their salary, so
       <deductions_not_recovered> of them could not be collected."
      Bad : "The net is below the calculated difference because
             deductions exceeded gross."

- IF `answering_about` IS PRESENT, THE ANSWER IS ABOUT THAT SLICE ONLY.
  They chose it — usually by answering a question you asked. Every
  figure in the data is that department's, so name the department in
  the reply and do not reach for the company's totals to pad it out.
  Nothing about anyone outside the slice belongs in that answer.

- PEOPLE AND DAYS ARE DIFFERENT ANSWERS. "How many employees were
  absent" is `employees_with_an_absence`. `absent_employee_days` is the
  days those absences add up to. Lead with the one they asked for, and
  give the other only as context — never blur them into one figure.

- NAME THE MEASURE WHEN THE QUESTION DOES NOT. "The most attendance
  issues" is not a field. Say which one you ranked by — absent days, or
  the absence rate — because with departments of different sizes those
  two give different winners.

- ANSWER THE SCOPE THEY ASKED FOR. `upcoming_covers` says what the
  upcoming list actually contains. Do not shrink it to "this week"
  because the first entry happens to fall there.

- "UNUSUAL" NEEDS A BASELINE. If a tool says
  `enough_data_to_compare: false`, give the figures and say plainly that
  there are too few people for any of them to be called unusual. Two
  employees do not make an average.

- NO CLOSING FILLER. Do not end with "If you need further details or
  have any other questions, please let me know." Finish on the answer.

- NAME THE MONTH, EVERY TIME. If a tool says `processed: false`, the
  answer starts there: that month has not been run. You may then give
  the latest month that HAS been run, clearly labelled as that month —
  never as the one they asked about.
      Good: "<this month> payroll has not been processed yet. The latest
             is <latest_processed_label>, at <its net> net."
      Bad : "Total payroll is <a figure with no month attached>."

- "WHY WAS THE PAY LOW" IS ANSWERED WITH THE LINES, not with the
  possibility of lines. `deductions` names every one, with amounts, and
  `how_it_was_calculated` has the working in the same words printed on
  the slip. Read them out. "There may have been deductions" is what you
  say when you have not looked, and you have.
  Two different steps, and the answer usually needs both:
      salary -> GROSS   pro-rating, if they joined mid-month
                        (`joined_during_this_month`, `employed_days_in_month`)
      GROSS  -> NET     the deductions, each one named with its amount
  A net of zero is never "because they were absent on the 31st". It is
  the deductions adding up to the gross, and `warnings_on_the_slip` says
  so when the arithmetic went below zero and was floored.

- A COMPARISON NEEDS BOTH SIDES. `can_compare: false` means there is
  nothing to compare — say which month is missing. Do not compute a
  change from one month and a guess.

- NEVER READ PERFORMANCE OUT OF ATTENDANCE. If `performance_data` came
  back, use its `answer_to_give` — there is no appraisal data, so
  performance cannot be assessed or compared. Do not offer attendance as
  a substitute, and do not call a clean attendance record "performing
  well".
  "WHO NEEDS IMPROVEMENT" IS A PERFORMANCE QUESTION. Naming somebody
  because they were absent answers a question they did not ask, and puts
  a judgement on a colleague that nothing in this system supports. Say
  there is no appraisal data. You may add that attendance records exist
  and offer them — as attendance, named as attendance.

- ATTENDANCE IS JUDGED ON THE FIGURES, NOT ON THE MOOD. `attendance_period`
  gives working days, present, absent, leave, late and the rates. "How
  was attendance" is answered with those. Do not call a month "stable"
  or "good" unless the numbers say so — a month where most of the
  working days were missed is not stable, however few people were on
  leave.

- SOMEBODY WHO JOINED MID-MONTH OWES ATTENDANCE FROM THEIR START DATE.
  `counted_from` and `joined_during_this_period` say when their window
  opened. Their working days will be fewer than the month's, and that is
  correct, not a gap in the data.

- NEVER GUESS SOMEBODY'S GENDER FROM THEIR NAME. Not "he", not "she",
  not "sir" or "madam". Use the person's name, or "they". The system
  does not record it, so anything you pick is a guess about a real
  colleague, and half the time it is wrong to their face.
      Good: "<name> took two days of annual leave." Or "they", if the
             name has already been said in this sentence.
      Bad : "He took two days of annual leave."

- GET THE TENSE RIGHT ON DATED THINGS. Leave carries `already_taken`,
  `in_progress` and `still_to_come`. Leave that starts next week was not
  "taken" — it is booked. Asked who took leave this month on the 1st,
  say what is booked and what has already happened, separately.

- SAY WHAT IT MEANS, not just what it is. The CEO can read a table; what
  they need is the read. "Three people are past the late threshold, all
  in Marketing" beats a list of names and minutes.
- Name a next step only when the data supports one.

- Use ONLY the data above. Never estimate, never fill a gap from general
  knowledge, never invent a name that is not there.
- If the data says a person was not found or the name was ambiguous, say
  so and ask which one.
- If nothing was retrieved, say what you would need to answer it.

- NEVER ask the CEO for data about their own company. Everything you
  have is above; if a name or figure is not there, it is not recorded,
  and you say THAT. "Please provide the missing names" is HR asking its
  employer to do HR's job.

- NEVER PUT A NUMBER IN AN ANSWER THAT DID NOT COME FROM THE DATA ABOVE.
  Not from an example, not from a rule you were given, not from what a
  figure like it usually is. Every count you state must be traceable to
  a field in COMPANY DATA.
- You cannot read anyone's conversation with the help desk, and you
  cannot see inside a confidential case. If asked, say that plainly —
  it is a rule of the system, not a limitation to apologise for.
- Never mention tools, systems, models, databases or automation.
- MENTION AN ATTACHMENT ONLY IF ONE IS LISTED ABOVE. The section says
  exactly what is attached, and it is the only thing you may claim. If
  it says nothing is attached, then nothing is — whatever an earlier
  reply said.

- Never send them to a screen or a button. They asked in writing; answer
  in writing. Not "open the Requests tab and press Respond" — just tell
  them what is there.
  THE ATTACHMENT IS NOT AN ANSWER EITHER. "The deductions can be found
  in the attached payslip" is the same evasion in a different coat: the
  figures are in front of you, so give them, and let the PDF be the copy
  they keep rather than the place they have to go and look.

Reply with the message text only."""


# ─────────────────────────────────────────────────────────────────
# A TOOL THAT DID NOT RUN CANNOT BE QUOTED
# ─────────────────────────────────────────────────────────────────
# Asked "who is on leave today?" the console answered:
#
#   "Nobody is on leave today. All employees are present and accounted
#    for."
#
# The second sentence is about attendance, and the only tool that ran was
# the leave tool. It is the same reflex that once read performance out of
# attendance: an empty result feels unfinished, so the model rounds it off
# with what it "means" — and what it means is a claim nothing checked.
#
# The prompt says not to. It was rewritten twice and the sentence kept
# coming back, so it is a rule now instead: if no attendance tool ran, a
# sentence that states attendance is removed. Prompt rules hold most of
# the time; this holds every time.
# Every tool whose payload can BACK a sentence about attendance — not
# only the attendance tools. A payslip carries absent days and an
# absence deduction, so "58,096.07, including absence and tax" is a
# quotation, not a claim; and `hr_summary` carries a month of it inside
# a larger report.
#
# Leaving any of these off strips a sentence the data DOES support,
# which is the same failure pointing the other way — and that is how a
# payroll answer lost the word "absence" out of its own breakdown.
ATTENDANCE_TOOLS = (
    "attendance_today_company", "attendance_period", "attendance_outliers",
    "employee_attendance", "employee_snapshot", "hr_summary", "hr_issues",
    # These hold absence as money
    "payroll_period", "payroll_comparison", "payroll_overview",
    "employee_payslip",
)

# Only whole-company claims. "Awais was absent on the 4th" comes from a
# tool that ran; "everyone is present" comes from nowhere.
# ─────────────────────────────────────────────────────────────────
# THE VOCABULARY, NOT THE PHRASING
# ─────────────────────────────────────────────────────────────────
# This pattern used to enumerate the shapes the claim arrived in:
#
#   "all employees are present and accounted for"
#   "nobody is absent"
#   "there are no employees absent today"
#
# and each time it was tightened, the next reply found a new shape:
#
#   "everyone is accounted for"
#   "there will be no absences in the Engineering department"
#
# Three rounds of that is enough. The test is no longer HOW the sentence
# is built but WHAT it is about: when no attendance tool ran, a sentence
# that talks about attendance at all is talking about something nothing
# looked up. Cutting it costs a sentence; keeping it costs the truth.
#
# Deliberately not "leave" — a leave tool DID run, and its own sentences
# are the answer.
_UNBACKED_ATTENDANCE = re.compile(
    # "available" belongs here for the same reason as "present": nobody
    # being on leave does not mean anybody will be at their desk. It was
    # the fifth phrasing of this one claim — after "present and accounted
    # for", "everyone is accounted for", "no absences", and "nobody is
    # absent" — which is what a vocabulary list is for.
    r"\b(absent|absence|absences|absentee\w*|attendance|present|presence|"
    r"available|availability|turn(?:ed|ing)?\s+up|at\s+work|"
    r"at\s+their\s+desk|checked?[\s-]?in|haazri|hazri)\b"
    # "Accounted for" only when it is about PEOPLE. Payroll says
    # "all deductions are accounted for" and means the money adds up.
    r"|\b(everyone|every\s+\w+|all\s+\w+|nobody|no\s+one|staff|"
    r"employees?|team)\b[^.!?]{0,40}\baccounted\s+for\b",
    re.I,
)

_SENTENCE = re.compile(r"[^.!?]+[.!?]*")

# The claim usually arrives bolted onto a true sentence: "nobody is on
# leave, SO everyone is present". Cutting at the joint keeps the answer.
_CONNECTOR = re.compile(
    r"[,;]?\s*\b(so|therefore|which means|meaning|this means|hence|"
    r"and (?:so|therefore))\b",
    re.I,
)


def strip_unbacked_attendance(reply: str, data: dict) -> str:
    """Remove attendance claims when no attendance tool ran."""
    if not reply or any(k in data for k in ATTENDANCE_TOOLS):
        return reply

    kept = []
    for s in _SENTENCE.findall(reply):
        if not _UNBACKED_ATTENDANCE.search(s):
            kept.append(s)
            continue

        # Keep the half that was actually answered, drop the half that
        # was inferred — but only if the head survives on its own.
        m = _CONNECTOR.search(s)
        head = s[:m.start()].strip() if m else ""
        if head and not _UNBACKED_ATTENDANCE.search(head):
            kept.append(head.rstrip(",;") + ". ")

    out = "".join(kept).strip()

    # Never hand back an empty reply — if every sentence was a claim, the
    # answer itself was the claim, and that is a failure worth seeing.
    return out or reply


# ─────────────────────────────────────────────────────────────────
# NOBODY'S GENDER IS RECORDED, SO NOBODY'S GENDER IS GUESSED
# ─────────────────────────────────────────────────────────────────
# "He is a Frontend Developer" — about a colleague whose gender this
# system does not hold, inferred from a name. The prompt says not to and
# it mostly holds; on a Roman Urdu answer it came back anyway.
#
# This one is NOT rewritten in place. Swapping "his" for "their" is easy
# and swapping "he is" for "they is" is what naive rewriting does to the
# rest of the sentence. So the reply is asked for again, once, with the
# slip pointed out — and if it comes back a second time the answer still
# goes out, because a pronoun is a smaller problem than no answer.
_GENDERED = re.compile(r"\b(he|she|his|her|him|hers|himself|herself)\b", re.I)


def has_gendered_pronoun(text: str) -> bool:
    return bool(_GENDERED.search(text or ""))


# ─────────────────────────────────────────────────────────────────
# "IT IS IN THE ATTACHMENT" IS NOT AN ANSWER
# ─────────────────────────────────────────────────────────────────
#     CEO: Why was Anas's salary lower than the gross?
#     HR : ...you can find the detailed breakdown of these deductions in
#          the attached payslip PDF.
#
# Every one of those figures was in the payload the reply was written
# from. This is the same reflex as "open the Requests tab and press
# Respond" — the CEO asked in writing and is being sent somewhere else
# to read the answer themselves.
#
# The prompt forbids it and mostly holds. When it does not, the reply is
# asked for once more with the evasion named. Nothing is deleted: if the
# second attempt also points at the PDF, the first answer still goes.
_POINTS_AWAY = re.compile(
    r"("
    # "...can be found / are listed / see ... in the attached slip"
    r"(?:can be found|are (?:detailed|listed|shown)|refer to|"
    r"details? (?:of|for) these|breakdown[^.]{0,40})[^.]{0,60}"
    r"\b(?:in|on) the attached"
    # "see the attached payslip FOR the figures" — the other word order
    r"|(?:see|check|review|open)\s+the\s+attached[^.]{0,40}\bfor\b"
    r")",
    re.I,
)


def points_at_attachment(text: str) -> bool:
    return bool(_POINTS_AWAY.search(text or ""))


# Asking the model to rewrite it was tried first and produced
#     "Total Deductions: [Insert specific deduction names and amounts]"
# — a worse answer than the one it replaced. So the sentence is built
# here instead, from the same payload the reply was written from. There
# is nothing to get wrong: the figures are handed over, not composed.
_LABELS = {
    "absence": "absence", "late": "late arrival",
    "unpaid_leave": "unpaid leave", "short_hours": "short hours",
    "income_tax": "income tax", "provident_fund": "provident fund",
    "loan": "loan instalment", "other": "other",
}


# ─────────────────────────────────────────────────────────────────
# THE WORD THE DATA CANNOT BACK
# ─────────────────────────────────────────────────────────────────
# Asked who had the most unauthorised absences, the console now gives
# the right basis — "15 days where no leave request was made at all" —
# and still opens with "Sheikh Wasi had the most UNAUTHORISED absences".
#
# The number is defensible; the word is a finding about a colleague that
# nobody in this company has made. Nothing here records an authorisation
# decision, so the reply may describe what happened and may not classify
# it. The prompt says so and the word keeps coming back, because the CEO
# used it in the question and the model is being agreeable.
#
# The sentence is not rewritten — the basis it gives is correct and worth
# keeping. What is missing is the limit on it, and that is added.
_VERDICT_WORD = re.compile(r"\bunauthoris(ed|ing)?\b|\bunauthoriz(ed|ing)?\b",
                           re.I)
_ALREADY_QUALIFIED = re.compile(
    r"does not record|no such (field|decision)|cannot (tell|determine|say) "
    r"whether", re.I)


_KIND_LABEL = {
    "no_request_at_all": "no leave request at all",
    "request_refused": "a request that was refused",
    "request_undecided": "a request still undecided",
    "request_withdrawn": "a request that was withdrawn",
}


def note_absence_limitation(reply: str, data: dict,
                            message: str = "") -> str:
    """
    Answer an "unauthorised absence" question with what is actually known.

    ═══ WHY THE QUESTION MATTERS, NOT JUST THE REPLY ═══
    This used to fire only when the REPLY said "unauthorised". Asked who
    had the most unauthorised absences, the console then answered
    "Sheikh Wasi, 18 days absent; Sheikh Anas, 12" — no verdict word, so
    nothing was appended, and no breakdown either. The count is right
    and it is not the answer: 18 absences with 15 no-request, 2 refused
    and 1 withdrawn is a different picture from 18 of any one kind.

    The prompt asks for the breakdown and mostly gets it. When the CEO
    uses the word, the breakdown is not optional, so it is written here
    from the payload rather than hoped for.
    """
    kinds, per_person = None, []
    for value in (data or {}).values():
        if not isinstance(value, dict):
            continue
        if value.get("absence_kinds_total"):
            kinds = value["absence_kinds_total"]
            per_person = [r for r in (value.get("by_employee") or [])
                          if (r.get("absence_kinds") or {})]

    asked = _VERDICT_WORD.search(message or "")
    said = _VERDICT_WORD.search(reply or "")

    if not reply or not kinds or not (asked or said):
        return reply
    if _ALREADY_QUALIFIED.search(reply):
        return reply

    out = reply.rstrip()

    # The figures, when they asked for a verdict the data cannot give
    if asked and not any(_KIND_LABEL[k][:10] in reply.lower()
                         for k in kinds if kinds[k]):
        if per_person:
            worst = max(per_person,
                        key=lambda r: (r["absence_kinds"]
                                       .get("no_request_at_all", 0)))
            k = worst["absence_kinds"]
            parts = ", ".join(f"{n} with {_KIND_LABEL[key]}"
                              for key, n in k.items() if n)
            out += (f" Of {worst['name']}'s {worst.get('absent_days')} absent "
                    f"days: {parts}.")
        else:
            parts = ", ".join(f"{n} with {_KIND_LABEL[key]}"
                              for key, n in kinds.items() if n)
            out += f" Across everyone: {parts}."

    return out + (
        " To be exact: this system does not record whether an absence was "
        "authorised — only whether a leave request existed for the day and "
        "what was decided about it.")


def spell_out_deductions(reply: str, data: dict) -> str:
    """Replace "it is in the attachment" with the figures themselves."""
    slip = (data or {}).get("employee_payslip") or {}
    # With no month named the tool answers about the most recent slip,
    # and the figures live one level down.
    if not slip.get("deductions") and slip.get("latest_month"):
        slip = slip["latest_month"]
    cuts = {k: v for k, v in (slip.get("deductions") or {}).items() if v}
    if not points_at_attachment(reply) or not cuts:
        return reply

    kept = [s for s in _SENTENCE.findall(reply)
            if not points_at_attachment(s)]

    parts = ", ".join(f"{_LABELS.get(k, k)} {v:,.2f}"
                      for k, v in sorted(cuts.items(), key=lambda kv: -kv[1]))
    line = (f" Against a gross of {slip.get('gross_pay', 0):,.2f}, the "
            f"deductions were {parts} — {slip.get('total_deductions', 0):,.2f} "
            f"in total, leaving {slip.get('net_salary', 0):,.2f}.")

    return ("".join(kept).strip() + line).strip()


def compose_node(state: ConsoleState) -> ConsoleState:
    plan = state.get("plan") or {}
    data = state.get("data") or {}

    history = "\n".join(
        f"{'CEO' if h['role'] == 'ceo' else 'HR'}: {h['text']}"
        for h in (state.get("history") or [])[-HISTORY_TURNS:]
    ) or "(nothing yet)"

    # ──── A slip is handed over as a file, not read out ────
    # Built BEFORE the reply is written, because the model has to be told
    # whether anything is actually attached. It was saying "August's slip
    # is attached" on questions about attendance and leave — echoing the
    # phrase out of the conversation while nothing was attached at all.
    attachments = []
    slip = data.get("employee_payslip") or {}
    if slip.get("found") and slip.get("payslip_id") and slip.get("has_pdf"):
        attachments.append({
            "type": "payslip",
            "payslip_id": slip["payslip_id"],
            "period_label": slip.get("period_label"),
            "employee": slip.get("name"),
        })
    else:
        for m in (slip.get("months_on_record") or [])[:6]:
            if m.get("has_pdf") and m.get("payslip_id"):
                attachments.append({
                    "type": "payslip",
                    "payslip_id": m["payslip_id"],
                    "period_label": m.get("period_label"),
                    "employee": slip.get("name"),
                })

    if attachments:
        which = ", ".join(a["period_label"] or "" for a in attachments)
        attachment_note = (
            f"{len(attachments)} payslip PDF(s) ARE attached below "
            f"({which}). Mention it in one short clause."
        )
    else:
        attachment_note = (
            "NOTHING is attached to this reply. Do not say a slip is "
            "attached, do not mention an attachment, and do not repeat "
            "any such phrase from earlier in the conversation."
        )

    try:
        from app.utils.llm import chat_model

        llm = chat_model(temperature=0.2, max_tokens=1200)

        reply = _invoke_with_retry(llm, [
            SystemMessage(content=(
                "You are a company's HR, briefing its CEO. You never invent "
                "figures and you never mention that you are software."
            )),
            HumanMessage(content=ANSWER_PROMPT.format(
                today=_today_str(),
                name=state.get("ceo_name") or "the CEO",
                history=history,
                message=state["message"],
                data=json.dumps(data, indent=2, default=str) if data
                     else "(nothing retrieved)",
                attachment_note=attachment_note,
                language=plan.get("language", "english"),
            )),
        ], "console-compose")
        reply = strip_unbacked_attendance(clean_reply(reply), data)

        reply = note_absence_limitation(
            spell_out_deductions(reply, data), data, state["message"])

        if has_gendered_pronoun(reply):
            retry = _invoke_with_retry(llm, [
                SystemMessage(content=(
                    "You are a company's HR, briefing its CEO. You never "
                    "invent figures and you never mention that you are "
                    "software."
                )),
                HumanMessage(content=(
                    "Rewrite this so it uses no gendered pronoun — no he, "
                    "she, his, her or him. Use the person's name, or "
                    "'they'. Change nothing else: same facts, same "
                    "figures, same length, same language.\n\n" + reply
                )),
            ], "console-degender")
            retry = strip_unbacked_attendance(clean_reply(retry), data)
            if retry and not has_gendered_pronoun(retry):
                reply = retry

    except Exception as e:                              # noqa: BLE001
        print(f"[hr] console compose failed: {e}")
        reply = ("Abhi main ye nikaal nahi pa rahi. Zara der baad dobara "
                 "poochein."
                 if plan.get("language") == "roman_urdu" else
                 "I could not pull that together just now — give me a "
                 "moment and ask again.")
        return {**state, "reply": reply, "sources": [],
                "attachments": [], "error": str(e)}

    return {**state, "reply": reply, "attachments": attachments,
            "sources": [{"kind": "company", "name": k} for k in data]}


# ══════════════════════════════════════════════
# Graph
# ══════════════════════════════════════════════
def build_console_graph():
    g = StateGraph(ConsoleState)
    g.add_node("route", route_node)
    g.add_node("gather", gather_node)
    g.add_node("compose", compose_node)
    g.set_entry_point("route")
    g.add_edge("route", "gather")
    g.add_edge("gather", "compose")
    g.add_edge("compose", END)
    return g.compile()


console_graph = build_console_graph()


# ─────────────────────────────────────────────────────────────────
# WRITTEN HERE, NOT INSIDE THE FUNCTION — AND NOT THROUGH A HEREDOC
# ─────────────────────────────────────────────────────────────────
# This pattern was first written with a `\b` at each end, through a
# shell heredoc, and the `\b` arrived as \x08 — a literal backspace.
# The line looked perfect in the editor, in grep, and in the terminal;
# it simply never matched, so "what about the whole company?" quietly
# kept answering about Finance.
#
# Third time in this project. `check_scope.py` now scans every source
# file for control characters, because the eye cannot.
_WHOLE_COMPANY = re.compile(
    r"\b(whole company|entire company|all departments|everyone|"
    r"poori company|puri company)\b", re.I)


def _scope_change(db, company_id: int, message: str, scope):
    """
    What "what about Frontend?" does to the slice in force.

    A ROLE is looked for before a department, because the narrower
    reading is the one the words usually mean: with Engineering in
    force, "what about Frontend?" is a role inside it, not a department
    called Frontend. Choosing a department clears the role — `Scope`
    enforces that, so HR never inherits Engineering's job titles.
    """
    from app.utils.console_scope import (Scope, find_department, find_role,
                                         role_is_unique)

    msg = message or ""
    if _WHOLE_COMPANY.search(msg):
        return Scope(), "whole company"

    role = find_role(db, company_id, msg, scope.department)
    if role:
        owner = scope.department or role_is_unique(db, company_id, role)
        out = Scope(department=owner).narrowed_to("role", role)
        return out, f"role -> {role}"

    department = find_department(db, company_id, msg)
    if department and department != scope.department:
        return scope.narrowed_to("department", department),             f"department -> {department}"

    return scope, None


def ask_console(db, company_id: int, ceo_name: str, message: str,
                history: list) -> dict:
    """One question from the CEO. Never raises."""
    from app.utils.console_clarify import (after_department, already_asked,
                                           pending_from, phrase, read_choice,
                                           scope_from, what_to_ask)
    from app.utils.console_scope import ALL, Scope

    history = history or []
    scope = scope_from(history)
    pending = pending_from(history)
    asked_now = None

    def clarification(ask, language):
        """A question, with the scope so far kept alongside it."""
        return {
            "reply": phrase(ask, language),
            # Rides in `sources`, which every reply already stores and
            # returns — no new table, no new column.
            "sources": [{"kind": "clarification", "ask": ask},
                        scope.to_source()],
            "attachments": [],
            "language": language,
        }

    # ──── Are they answering the menu we just put up? ────
    if pending:
        choice = read_choice(message, pending)
        if choice is not None:
            level = pending["level"]
            if choice["value"] == ALL:
                # "Whole company" clears everything; "All Engineering"
                # keeps the department and drops the role.
                scope = (Scope() if level == "department"
                         else Scope(department=pending.get("department")))
            elif level == "role":
                # The role menu knows its department, and may have been
                # asked without a department menu ever going up — a
                # company with one department skips straight to it.
                scope = Scope(
                    department=pending.get("department") or scope.department
                ).narrowed_to("role", choice["value"])
            else:
                scope = scope.narrowed_to(level, choice["value"])

            message = pending.get("original_message") or message
            print(f"[hr] menu: {level} = {choice['value']} -> "
                  f"scope {scope.describe()}")

            # A department with more than one role gets a second menu.
            if (level == "department" and choice["value"] != ALL
                    and not already_asked(history, "role")):
                nxt = after_department(db, company_id, choice["value"],
                                       message)
                if nxt:
                    return clarification(
                        nxt, decide_language(message, history))
        # choice is None: they moved on. The menu is dropped, never
        # repeated, and the message is answered as it stands.

    # ──── Or changing the slice in passing? ────
    if not pending or scope.is_empty():
        scope, what = _scope_change(db, company_id, message, scope)
        if what:
            print(f"[hr] scope changed: {what} -> {scope.describe()}")

    out = console_graph.invoke({
        "db": db,
        "company_id": company_id,
        "ceo_name": ceo_name,
        "message": message,
        "history": history,
        # The slice reaches the TOOLS through here, not the prompt
        "forced": scope.as_kwargs(),
    })

    # ──── Is one question worth asking before answering? ────
    plan = out.get("plan") or {}
    language = plan.get("language", "english")
    if scope.is_empty() and not pending:
        ask = what_to_ask(db, company_id, message, plan, scope)
        if ask and not already_asked(history, ask["level"]):
            return clarification(ask, language)

    # ──── The scope is recorded even when it is empty ────
    # "What about the whole company?" clears it, and if that clearing is
    # not written down, the next turn reads back the LAST scope that was
    # — and quietly answers about Finance again. An empty scope is a
    # decision, not the absence of one.
    sources = list(out.get("sources") or [])
    sources.append(scope.to_source())

    return {
        "reply": out.get("reply") or "I could not pull that together.",
        "sources": sources,
        "attachments": out.get("attachments") or [],
        "language": language,
    }
