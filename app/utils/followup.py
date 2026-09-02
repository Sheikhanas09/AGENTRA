"""
Recognising a follow-up, and carrying its subject
────────────────────────────────────────────────
Shared by the CEO console and the employee help desk, because both had
the same hole and only one of them had it fixed.

    console:  "who is on leave today?" -> "what about next week?"
              answered about ATTENDANCE
    desk:     "my attendance last month?" -> "what about July?"
              answered from the PAYSLIP, and then invented the figures
              the payslip did not have

Neither message names a subject. Both are the previous question with one
thing changed, and neither router had any way to know that.

`enforce_topic` lives here too, and belongs here: recognising a
follow-up and correcting a mis-routed subject are the same job — both
decide which tool a message ACTUALLY means, from the words in it rather
than from what the router guessed.
"""

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────
# "HOW MANY OF THEM" IS THE SAME QUESTION, NARROWED
# ─────────────────────────────────────────────────────────────────
#     CEO: Who is on leave today?
#     HR : Nobody is on leave today.
#     CEO: How many of them are from Backend?
#     HR : One — Awais Ahmed is absent today.
#
# "Them" was the empty set. The router, seeing a question with no subject
# in it, picked the nearest people-shaped tool and answered about absence
# instead. The router prompt was given this exact example and still got it
# right only some of the time, so it is decided here instead: a follow-up
# that names no new subject reuses the tools of the answer it refers to.
#
# The previous turn's tools are on the stored reply — `sources` is written
# with every HR message — so this needs no new state, only for the route
# to be given the history it already has.
_PRONOUN = re.compile(
    r"\b(them|they|those|these|their|it|that one|same)\b", re.I)

# A word that names a subject means the CEO moved on, and the router
# should route it fresh.
_SUBJECT = re.compile(
    r"\b(attendance|absent|absence|present|late|leave|holiday|payroll|"
    r"salary|salaries|pay|payslip|slip|deduction|loan|advance|hire|hiring|"
    r"candidate|interview|job|vacancy|opening|joined|joining|probation|"
    r"headcount|query|queries|request|grievance|performance|appraisal|"
    r"resign|terminat)\w*", re.I)
# "department" is deliberately NOT in that list. A department is a way of
# cutting whatever is already on the table — "which departments are they
# from" narrows the previous answer, it does not start a new subject.


def carried_tools(history) -> list:
    """The tools behind the last HR reply, if that reply recorded any."""
    for h in reversed(history or []):
        if h.get("role") == "hr":
            return [s.get("name") for s in (h.get("sources") or [])
                    if isinstance(s, dict) and s.get("name")]
    return []


# ──── The follow-up with no pronoun in it either ────
#     CEO: Who is on leave today?
#     CEO: How many of them are from Backend?      <- caught, has "them"
#     CEO: What about next week?                   <- not caught
#
# The third one carries no pronoun and no subject: it is the previous
# question with one thing swapped. Nothing marked it as a follow-up, so
# the router chose freely and answered about attendance instead of
# leave — a whole different subject, from a question that named none.
_ELLIPTICAL = re.compile(
    r"^\s*(what|how)\s+about\b|^\s*(and|aur|ya)\s+\w|^\s*what\s+if\b|"
    r"^\s*same\s+for\b|^\s*aur\b", re.I)


def is_narrowing_followup(message: str) -> bool:
    """
    The previous question, narrowed — by a pronoun or by ellipsis.

    Either way the test is the same: it refers to something, and it does
    not name a new subject of its own.
    """
    msg = message or ""
    refers_back = bool(_PRONOUN.search(msg) or _ELLIPTICAL.search(msg))
    return refers_back and not _SUBJECT.search(msg)


# ─────────────────────────────────────────────────────────────────
# A QUESTION'S SUBJECT DECIDES ITS TOOL — IN CODE
# ─────────────────────────────────────────────────────────────────
# Three reported failures turned out to be one failure:
#
#   "which employee has the most absences?"  -> leave_usage
#       "Sheikh Wasi, with 2 days taken."   (he was absent 18)
#   "how was attendance last month?"         -> a leave tool
#       "attendance was stable"             (39 of 42 days missed)
#   "who needs improvement?"                 -> attendance
#       "both have been absent..."          (there is no appraisal data)
#
# Routing them again by hand, all three came out right — the router is
# correct most of the time, which is exactly the problem. A question
# about absence must never be answered from the leave table on the run
# where the model happens to reach for it.
#
# So the subject is read from the question's own words and the tool list
# is corrected afterwards. Vocabulary, not question matching: no rule
# here mentions a name, a date, or any of the questions above.
_ATTENDANCE_WORDS = re.compile(
    r"\b(attendance|absent|absence|absences|absentee\w*|present|turn(ed|ing)?"
    r"\s*up|late|lateness|punctual\w*|check[- ]?in|haazri|hazri|"
    r"ghair[- ]?haazir)\b", re.I)
_LEAVE_WORDS = re.compile(
    r"\b(leave|leaves|holiday|holidays|vacation|time\s*off|chutti|chuttiyan|"
    r"annual|casual|sick)\b", re.I)
_PERFORMANCE_WORDS = re.compile(
    r"\b(performance|performing|performer|appraisal|appraisals|review|"
    r"rating|ratings|improvement|improve|underperform\w*|top\s+performer|"
    r"doing\s+(well|badly)|kaarkardagi)\b", re.I)

# A deduction lives on a payslip. Asked who had the highest deductions
# last month, the router picked `employee_loans` — one KIND of deduction,
# and the only one with the word in its own table — and answered "there
# were no deductions recorded for any employee". There were 88,286.52
# worth, on two payslips.
_DEDUCTION_WORDS = re.compile(
    r"\b(deduction|deductions|deducted|cut\s+from\s+(their\s+)?(pay|salary)|"
    r"katouti|kati)\b", re.I)
_LOAN_WORDS = re.compile(r"\b(loan|loans|advance|advances|qarz)\b", re.I)

# "Why is his net zero for August?" was answered out of the attendance
# table, about a single day, because nothing said the question was about
# money. A payslip question needs the payslip.
_PAY_WORDS = re.compile(
    r"\b(payslip|pay\s*slip|slip|salary|salaries|pay|paid|wage|wages|"
    r"net|gross|earnings|tankhwah|tankha)\b", re.I)

ATTENDANCE_FAMILY = ("attendance_today_company", "attendance_period",
                     "attendance_outliers", "employee_attendance")
PAYROLL_FAMILY = ("payroll_period", "payroll_comparison", "payroll_overview",
                  "employee_payslip", "salary_changes")
LEAVE_FAMILY = ("leave_today_company", "leave_taken", "leave_usage",
                "leave_overview", "employee_leave", "leave_window")
# hr_summary carries attendance inside it, so it is not "the wrong tool"
# for an attendance question — but it is not focused enough to be the
# only one, so it neither satisfies the topic nor gets removed.
NEUTRAL = ("hr_summary", "hr_issues", "employee_snapshot", "headcount")

# Words that mean a stretch of time rather than today
_A_PERIOD = re.compile(
    r"\b(month|months|mahin[ae]|year|saal|august|september|october|november|"
    r"december|january|february|march|april|may|june|july|last|previous|"
    r"pichl[ae])\b", re.I)

# "Who has the MOST absences" names no month, but it is not a question
# about today either — you cannot have the most of anything in one day.
# A superlative or a pattern word means a stretch of time.
_AGGREGATE = re.compile(
    r"\b(most|highest|worst|best|top|least|lowest|problem|problems|issue|"
    r"issues|pattern|patterns|trend|trends|overall|summary|record|history|"
    r"repeatedly|often|frequently|zyada|ziyada)\b", re.I)


def enforce_topic(message: str, tools: list, plan: dict) -> list:
    """Correct the tool list when it disagrees with what was asked."""
    msg = message or ""
    tools = list(tools or [])

    asks_attendance = bool(_ATTENDANCE_WORDS.search(msg))
    asks_leave = bool(_LEAVE_WORDS.search(msg))
    asks_performance = bool(_PERFORMANCE_WORDS.search(msg))

    # ──── Performance, with no attendance word in sight ────
    # There is no appraisal data in this system. Anything else that runs
    # becomes a substitute for it, which is how attendance turned into
    # "needs improvement". If they ask about attendance too, that is a
    # different question and it keeps its tools.
    if asks_performance and not asks_attendance and not asks_leave:
        return ["performance_data"]

    # A month was named, or "last month" was resolved into one, or the
    # question is a superlative and therefore about a stretch of days.
    over_a_period = bool(plan.get("period") or _A_PERIOD.search(msg)
                         or _AGGREGATE.search(msg))

    def _attendance_tool():
        return "attendance_period" if over_a_period else \
               "attendance_today_company"

    def _leave_tool():
        return "leave_taken" if over_a_period else "leave_today_company"

    if asks_attendance and not asks_leave:
        tools = [t for t in tools if t not in LEAVE_FAMILY]
        if not any(t in ATTENDANCE_FAMILY for t in tools):
            tools.append(_attendance_tool())

    elif asks_leave and not asks_attendance:
        tools = [t for t in tools if t not in ATTENDANCE_FAMILY]
        if not any(t in LEAVE_FAMILY for t in tools):
            tools.append(_leave_tool())

    elif asks_attendance and asks_leave:
        # Both were asked, so both get answered. Nothing is removed —
        # the mistake here would be dropping half the question.
        if not any(t in ATTENDANCE_FAMILY for t in tools):
            tools.append(_attendance_tool())
        if not any(t in LEAVE_FAMILY for t in tools):
            tools.append(_leave_tool())

    # ──── A week needs a tool with a window in it ────
    # Once "next week" resolves to a range, a leave question about it has
    # to go somewhere that reads a range. `leave_today_company` reads a
    # day and `leave_taken` reads a month, so without this the range is
    # resolved correctly and then thrown away.
    if plan.get("date_from") and any(t in LEAVE_FAMILY for t in tools):
        tools = [t for t in tools if t not in LEAVE_FAMILY]
        if "leave_window" not in tools:
            tools.append("leave_window")

    # ──── Deductions come off a payslip ────
    # Unless they asked about loans specifically, in which case the loan
    # tool is exactly right and stays.
    if _DEDUCTION_WORDS.search(msg) and not _LOAN_WORDS.search(msg):
        tools = [t for t in tools if t != "employee_loans"]

    # A question about pay needs something that holds pay. Added, never
    # substituted: "was he paid for the days he was absent" is about
    # both, and dropping either half answers half a question.
    if _DEDUCTION_WORDS.search(msg) or _PAY_WORDS.search(msg):
        # ──── Ranking people needs all of them ────
        # "Who had the highest deductions last month?" came back on
        # `employee_payslip` — one person's slip, and no person named,
        # so it returned a list of months. A superlative is a question
        # about everybody: only the company tool has the rows to sort.
        if _AGGREGATE.search(msg) and not plan.get("person"):
            tools = [t for t in tools if t != "employee_payslip"]

        if not any(t in PAYROLL_FAMILY for t in tools):
            tools.append("employee_payslip" if plan.get("person")
                         else "payroll_period")

    # Never hand back nothing — an empty list answers no question at all
    return tools or list(plan.get("tools") or [])
