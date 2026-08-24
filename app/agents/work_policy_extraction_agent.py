"""
Work Policy Extraction Agent
────────────────────────────
Reads the CEO's policy document and extracts the **working hours** fields —
shift timings, working days, late tolerance, overtime, break policy.

A sibling of the leave-types agent (`policy_extraction_agent.py`): the same
three-node shape, the same lazy LLM/Chroma, the same "source_quote +
confidence" rule.
The only difference is that one extracts a LIST, this one extracts FIELDS.

    Policy PDF
        │
    extract_node   → the document's text (or the ChromaDB chunks)
        │
    rag_node       → picks the parts that relate to working hours
        │
    llm_node       → {fields: {shift_start: {...}, ...}}
        │
    settings.py    → only the fields that were FOUND are set;
                     the CEO fills in the rest

═══ THE MOST IMPORTANT RULE ═══
A field that is NOT in the document never arrives here at all. Its current
value is untouched — whatever the CEO set manually stays exactly as it is.

═══ AM/PM ═══
The most dangerous mistake is this: "5 PM" becoming "05:00". The whole
shift inverts and the attendance figures fall apart. So we ask the LLM for
the document's own wording (`raw`) ALONGSIDE the 24-hour value and compare
them ourselves — on a conflict, raw wins.
"""

import json
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from typing import TypedDict

load_dotenv()


# ──── The retrieval query ────
# This is roughly how a working-hours policy is worded
RETRIEVAL_QUERY = """
working hours office timings shift start end time
working days week Monday Friday Saturday weekend holiday
late arrival grace period tolerance minutes punctuality
overtime hours per day break lunch prayer time paid unpaid
lunch break timing from to duration minutes counted as working time
minimum daily working hours attendance check in check out
"""

MIN_SIMILARITY = 0.20
TOP_CHUNKS = 8

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]

# Plain names for the warning messages — the CEO should not have to read
# `late_tolerance_mins`
FIELD_LABELS = {
    "shift_start": "Shift start",
    "shift_end": "Shift end",
    "working_days": "Working days",
    "late_tolerance_mins": "Late tolerance",
    "early_checkin_grace_mins": "Early check-in grace",
    "enforce_shift_window": "Shift window enforce",
    "leave_auto_approve_hours": "Leave auto-approve",
    "min_daily_hours": "Minimum daily hours",
    "overtime_threshold": "Overtime threshold",
    "max_overtime_per_day": "Max overtime per day",
    "break_policy": "Break policy",
    "break_minutes": "Break duration",
    "break_start": "Break start",
    "break_end": "Break end",

    # ──── Payroll rules (the payroll_policy table) ────
    "overtime_multiplier": "Overtime multiplier",
    "late_deduction_policy": "How late arrival is deducted",
    "late_deduction_amount": "Late arrival deduction",
    "undertime_deduction": "Short hours deduction",
    "unpaid_leave_deduction": "Unpaid leave deduction",
    "absent_deduction": "Absence deduction",
    "tax_percentage": "Tax %",
    "tax_threshold": "Tax threshold",
    "provident_fund_percent": "Provident fund %",
}

# ──── Limits per field — outside these the CEO is warned ────
INT_LIMITS = {
    "late_tolerance_mins": (0, 240),
    "early_checkin_grace_mins": (0, 720),
    "leave_auto_approve_hours": (0, 168),
    "break_minutes": (0, 480),
}
FLOAT_LIMITS = {
    "min_daily_hours": (0.5, 24.0),
    "overtime_threshold": (0.5, 24.0),
    "max_overtime_per_day": (0.0, 12.0),

    # ──── Payroll ────
    "overtime_multiplier": (0.0, 10.0),
    "tax_percentage": (0.0, 100.0),
    "provident_fund_percent": (0.0, 100.0),
    "late_deduction_amount": (0.0, 1_000_000.0),
    "tax_threshold": (0.0, 100_000_000.0),
}


class WorkPolicyState(TypedDict):
    company_id: int
    policy_text: str
    retrieved_chunks: list
    fields: dict
    warnings: list
    error: str


# ══════════════════════════════════════════════
# Node 1: The document's text
# ══════════════════════════════════════════════
def extract_node(state: WorkPolicyState) -> WorkPolicyState:
    """Use the caller's full text if given, otherwise make do with chunks"""
    return {**state, "policy_text": (state.get("policy_text") or "").strip()}


# ══════════════════════════════════════════════
# Node 2: RAG — the working-hours sections
# ══════════════════════════════════════════════
def rag_node(state: WorkPolicyState) -> WorkPolicyState:
    """
    Pull the timing-related chunks out of ChromaDB.

    Handing over the whole document is both expensive and worse — leave
    details and dress codes get in the way and distract it.
    """
    try:
        # Lazy import — the module should load even without a GROQ key
        from app.agents.leave_agent import get_chroma_client, get_embedding_model

        collection = get_chroma_client().get_or_create_collection(
            f"company_{state['company_id']}_policies",
            metadata={"hnsw:space": "cosine"},
        )

        query_embedding = get_embedding_model().encode(RETRIEVAL_QUERY).tolist()
        results = collection.query(
            query_embeddings=[query_embedding], n_results=TOP_CHUNKS
        )

        chunks = []
        docs = (results.get("documents") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]

        for i, doc in enumerate(docs):
            similarity = 1 - dists[i] if i < len(dists) else 0
            if similarity >= MIN_SIMILARITY:
                chunks.append({"text": doc, "similarity": round(similarity, 3)})

        return {**state, "retrieved_chunks": chunks}

    except Exception as e:
        print(f"Work policy RAG error: {e}")
        return {**state, "retrieved_chunks": [], "error": str(e)}


# ══════════════════════════════════════════════
# Node 3: LLM — extract the fields
# ══════════════════════════════════════════════
PROMPT = """You are an HR policy analyst. Read the company policy below and
extract its WORKING HOURS configuration.

=== COMPANY POLICY ===
{policy}
=== END POLICY ===

Extract ONLY these fields, and ONLY if the policy actually states them:

- shift_start / shift_end: office timings.
  value  = 24-hour "HH:MM" (e.g. "09:00", "17:00", "22:00")
  raw    = the EXACT time phrase as written in the policy (e.g. "9:00 AM",
           "5 PM", "10 p.m."). Copy it character for character.
- working_days: list of full day names the office operates
  (e.g. ["Monday","Tuesday","Wednesday","Thursday","Friday"]).
  Expand ranges: "Monday to Friday" means all five days.
- late_tolerance_mins: grace minutes after shift_start before an arrival
  counts as late.
- early_checkin_grace_mins: how many minutes BEFORE shift_start an employee
  may check in.
- min_daily_hours: minimum hours an employee must work per day.
- overtime_threshold: hours after which work counts as overtime.
- max_overtime_per_day: maximum overtime hours allowed in one day.
- break_policy: "excluded" if break/lunch time is NOT counted as working
  hours (unpaid, deducted), "included" if it IS counted (paid).
- break_start / break_end: the fixed lunch/break window, if the policy gives
  one. Same format as shift_start: value = 24-hour "HH:MM", raw = the exact
  phrase as written.
- break_minutes: how many minutes of break are allowed per day.
- enforce_shift_window: true if the policy says check-in is only allowed
  during shift hours / not allowed after the shift ends.
- leave_auto_approve_hours: hours after which an unanswered leave request
  is automatically approved.

For EVERY field you report, include:
  source_quote: the EXACT sentence from the policy it came from. Do not
                paraphrase. This is how a human verifies you.
  confidence:   "high" if the policy states it plainly, "low" if inferred.

CRITICAL RULES:
- OMIT any field the policy does not mention. Do NOT guess.
- Do NOT fill in values from your general knowledge of HR norms.
- An empty result is a correct answer if the policy has no working hours.
- Never invent a source_quote. If you cannot quote it, omit the field.

Respond ONLY with JSON in this shape (omit fields not found):
{{
  "shift_start": {{"value": "09:00", "raw": "9:00 AM",
                   "source_quote": "Office hours are from 9:00 AM to 5:00 PM.",
                   "confidence": "high"}},
  "shift_end":   {{"value": "17:00", "raw": "5:00 PM",
                   "source_quote": "Office hours are from 9:00 AM to 5:00 PM.",
                   "confidence": "high"}},
  "working_days": {{"value": ["Monday","Tuesday","Wednesday","Thursday","Friday"],
                    "source_quote": "The office operates Monday through Friday.",
                    "confidence": "high"}},
  "late_tolerance_mins": {{"value": 15,
                    "source_quote": "A grace period of 15 minutes is allowed.",
                    "confidence": "high"}}
}}"""


# ══════════════════════════════════════════════
# Payroll rules — a SEPARATE call
# ══════════════════════════════════════════════
# These 8 fields used to live in the prompt above. The result: on
# llama-3.1-8b the field list grew so long that the model started dropping
# older ones — `leave_auto_approve_hours` vanished entirely, even though it
# was stated plainly in the document ("within 48 hours ... deemed approved").
#
# A small model cannot hold that many things at once. So: two separate,
# smaller calls — each with a single job. The document is still just ONE;
# both calls run over the same text.
PAYROLL_PROMPT = """You are an HR policy analyst. Read the company policy below
and extract its PAYROLL rules.

=== COMPANY POLICY ===
{policy}
=== END POLICY ===

Extract ONLY these fields, and ONLY if the policy actually states them:

- overtime_multiplier: the rate multiplier for overtime pay (e.g. 1.5 for
  "one and a half times", 2 for "double the rate").
- late_deduction_policy: how lateness is deducted.
  "pro_rata"       - the deduction is proportional to the time missed,
                     i.e. calculated from the employee's own salary for
                     the minutes/hours they were late. No fixed amount.
  "per_occurrence" - a FIXED amount (e.g. "PKR 500 fine per late arrival")
                     regardless of how late.
  "per_minute"     - a FIXED amount for EACH late minute (e.g. "PKR 10
                     per minute late").
  "none"           - lateness carries no salary deduction.
- late_deduction_amount: the fixed amount, ONLY for "per_occurrence" or
  "per_minute". Omit it for "pro_rata" — there is no fixed amount there.
- undertime_deduction: "pro_rata" if salary is deducted for working fewer
  hours than required, "none" otherwise.
- absent_deduction: how an UNAUTHORISED absence is treated — a day the
  employee neither attended nor had approved leave for.
  "per_day" - one full day's salary is deducted for each absent day.
  "none"    - no salary deduction for absence.
  This is NOT the same as unpaid leave, where the employee did apply and
  was approved.
- unpaid_leave_deduction: "pro_rata" if unpaid leave days are deducted from
  salary, "none" otherwise.
- tax_percentage: income tax rate applied to salary.
- tax_threshold: the amount BELOW which no tax applies (tax is charged only
  on the portion above it).
- provident_fund_percent: provident fund contribution rate.

For EVERY field you report, include:
  source_quote: the EXACT sentence from the policy it came from. Do not
                paraphrase. This is how a human verifies you.
  confidence:   "high" if the policy states it plainly, "low" if inferred.

CRITICAL RULES:
- OMIT any field the policy does not mention. Do NOT guess.
- Do NOT fill in values from your general knowledge of payroll norms.
- An empty result is a correct answer if the policy has no payroll rules.
- Never invent a source_quote. If you cannot quote it, omit the field.

Respond ONLY with JSON in this shape (omit fields not found):
{{
  "overtime_multiplier": {{"value": 1.5,
                    "source_quote": "Overtime is paid at 1.5 times the rate.",
                    "confidence": "high"}},
  "tax_percentage": {{"value": 5,
                    "source_quote": "Income tax of 5% is deducted at source.",
                    "confidence": "high"}}
}}"""

# Which field comes from which call
PAYROLL_PROMPT_FIELDS = {
    "overtime_multiplier",
    "late_deduction_policy", "late_deduction_amount",
    "undertime_deduction", "unpaid_leave_deduction", "absent_deduction",
    "tax_percentage", "tax_threshold", "provident_fund_percent",
}


def _ask_llm(llm, prompt: str, policy: str) -> dict:
    """One LLM call → a parsed JSON dict (empty dict if nothing is found)"""
    response = llm.invoke([
        SystemMessage(
            content="You extract structured HR data. Respond with valid JSON only. "
                    "Never invent information that is not in the provided text. "
                    "Omitting a field is always better than guessing it."
        ),
        HumanMessage(content=prompt.format(policy=policy)),
    ])

    raw = response.content.strip()
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def llm_node(state: WorkPolicyState) -> WorkPolicyState:
    """Give the chunks to the LLM, take the JSON back, then make it trustworthy"""
    policy = state.get("policy_text") or ""

    if not policy and state.get("retrieved_chunks"):
        policy = "\n\n".join(
            f"[Clause {i + 1}] {c['text']}"
            for i, c in enumerate(state["retrieved_chunks"])
        )

    if not policy.strip():
        return {
            **state,
            "fields": {},
            "warnings": ["Nothing about working hours was found in the policy document"],
        }

    policy = policy[:12000]

    try:
        from app.agents.leave_agent import get_llm

        llm = get_llm()
        parsed = _ask_llm(llm, PROMPT, policy)

        # ──── Second call: the payroll rules ────
        # Letting this one fail is acceptable — the working hours matter
        # more, and if the payroll rules are not found the CEO's existing
        # settings simply stay as they are.
        try:
            pay = _ask_llm(llm, PAYROLL_PROMPT, policy)
            for name, item in pay.items():
                if name in PAYROLL_PROMPT_FIELDS:
                    parsed[name] = item
        except Exception as e:
            print(f"[work-policy] the payroll-rules call failed: {e}")

        fields, warnings = _sanitize(parsed)

        # ──── Does each field's quote actually exist in the document? ────
        kept, quote_warnings = _verify_quotes(fields, policy)

        # ──── The shift pair — do not lose it to a lazy quote ────
        kept, quote_warnings = _rescue_shift_pair(
            fields, kept, policy, quote_warnings
        )

        # ──── Table rows outrank everything — so they run last ────
        kept, quote_warnings = _apply_labeled_times(kept, policy, quote_warnings)
        kept, quote_warnings = _apply_labeled_break(kept, policy, quote_warnings)

        warnings.extend(quote_warnings)
        fields = kept

        # ──── Do the surviving fields agree with one another? ────
        warnings.extend(_cross_check(fields))

        return {**state, "fields": fields, "warnings": warnings, "error": ""}

    except Exception as e:
        print(f"Work policy LLM error: {e}")
        return {
            **state,
            "fields": {},
            "warnings": ["The agent could not read the policy — set the working hours manually"],
            "error": str(e),
        }


# ══════════════════════════════════════════════
# Time parsing — the most delicate part
# ══════════════════════════════════════════════
def parse_time_value(value, raw="") -> tuple:
    """
    Turn the LLM's time value into "HH:MM" (24-hour).

    ═══ AM/PM KI GHALTI ═══
    This is the mistake that inverts a whole shift: "5 PM" becoming
    "05:00". Attendance then believes the day starts at 5 in the MORNING.

    So we also ask the LLM for the document's own wording (`raw`). If raw
    says "pm" while the value gives an hour below 12, RAW wins — the
    document is the truth, not the LLM's conversion.

    Return: (hhmm_string | None, warning | None)
    """
    text = str(value or "").strip()
    raw_text = str(raw or "").strip().lower()

    # ──── First take the hour/minute out of the value ────
    m = re.search(r"(\d{1,2})\s*[:.\s]\s*(\d{2})", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})", text)
        if not m:
            return None, None
        hour, minute = int(m.group(1)), 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, f"'{value}' could not be read as a time"

    warning = None

    # ──── The value itself may carry AM/PM ────
    combined = f"{text} {raw_text}".lower()
    has_pm = bool(re.search(r"\bp\.?\s?m\.?", combined))
    has_am = bool(re.search(r"\ba\.?\s?m\.?", combined))

    if has_pm and not has_am:
        if hour < 12:
            hour += 12                       # 5 PM → 17
    elif has_am and not has_pm:
        if hour == 12:
            hour = 0                         # 12 AM → 00
        elif hour > 12:
            warning = (
                f"The document says '{raw or value}' but the agent read it "
                f"as {hour}:{minute:02d} — please check"
            )

    return f"{hour:02d}:{minute:02d}", warning


def _normalize_days(value) -> tuple:
    """Normalise day names — "mon", "MONDAY", "Mondays" should all work"""
    if isinstance(value, str):
        value = re.split(r"[,;/]|\band\b", value)
    if not isinstance(value, list):
        return None, "The list of working days could not be understood"

    lookup = {d.lower(): d for d in DAY_NAMES}
    lookup.update({d[:3].lower(): d for d in DAY_NAMES})

    days, seen = [], set()
    for item in value:
        key = re.sub(r"[^a-z]", "", str(item).lower())
        match = lookup.get(key) or lookup.get(key[:3])
        if match and match not in seen:
            seen.add(match)
            days.append(match)

    if not days:
        return None, "No recognisable day was found in the working days"

    # Keep them in week order — that is the order the UI displays
    return [d for d in DAY_NAMES if d in seen], None


def _sanitize(parsed) -> tuple:
    """
    Make the LLM's answer trustworthy.

    Each field is a different kind of thing, so each gets its own handling.
    A field that cannot be understood is DROPPED SILENTLY (better that the
    CEO fills it in than that a wrong value is applied).
    """
    fields, warnings = {}, []

    if not isinstance(parsed, dict):
        return {}, ["The agent's answer could not be understood"]

    def entry(name):
        """Pull out a field's {value, source_quote, confidence}"""
        item = parsed.get(name)
        if item is None:
            return None
        if not isinstance(item, dict):
            item = {"value": item}
        if item.get("value") is None:
            return None
        return item

    def record(name, value, item, note=None):
        quote = str(item.get("source_quote") or "").strip()
        confidence = str(item.get("confidence") or "low").lower()
        if confidence not in ("high", "low"):
            confidence = "low"

        # ──── A suggestion without a quote is trusted less ────
        if not quote:
            confidence = "low"
            warnings.append(f"{name}: no line was quoted from the document")

        fields[name] = {
            "value": value,
            "source_quote": quote[:500],
            "confidence": confidence,
        }
        if note:
            warnings.append(note)

    # ──── Times ────
    for name in ("shift_start", "shift_end", "break_start", "break_end"):
        item = entry(name)
        if not item:
            continue
        hhmm, warn = parse_time_value(item.get("value"), item.get("raw"))
        if hhmm:
            record(name, hhmm, item, warn)
        elif warn:
            warnings.append(warn)

    # ──── Working days ────
    item = entry("working_days")
    if item:
        days, warn = _normalize_days(item.get("value"))
        if days:
            record("working_days", days, item)
        elif warn:
            warnings.append(warn)

    # ──── Whole numbers ────
    for name, (lo, hi) in INT_LIMITS.items():
        item = entry(name)
        if not item:
            continue
        try:
            num = int(float(item["value"]))
        except (TypeError, ValueError):
            warnings.append(f"{name}: '{item['value']}' is not a number — skipped")
            continue
        note = None
        if not lo <= num <= hi:
            note = f"{name}: {num} is outside the range ({lo}–{hi}) — please check"
            num = max(lo, min(num, hi))
        record(name, num, item, note)

    # ──── Hours (decimal) ────
    for name, (lo, hi) in FLOAT_LIMITS.items():
        item = entry(name)
        if not item:
            continue
        try:
            num = round(float(item["value"]), 2)
        except (TypeError, ValueError):
            warnings.append(f"{name}: '{item['value']}' is not a number — skipped")
            continue
        note = None
        if not lo <= num <= hi:
            note = f"{name}: {num} is outside the range ({lo}–{hi}) — please check"
            num = max(lo, min(num, hi))
        record(name, num, item, note)

    # ──── Break policy ────
    item = entry("break_policy")
    if item:
        text = str(item["value"]).strip().lower()
        if text in ("included", "include", "paid", "counted"):
            record("break_policy", "included", item)
        elif text in ("excluded", "exclude", "unpaid", "deducted", "not counted"):
            record("break_policy", "excluded", item)
        else:
            warnings.append(f"break_policy: '{item['value']}' could not be understood")

    # ──── The word-based payroll decisions ────
    item = entry("late_deduction_policy")
    if item:
        text = str(item["value"]).strip().lower().replace(" ", "_")
        # pro_rata first — "prorata" used to fall through to `per_minute`,
        # which is a different thing: with per_minute the CEO configures an
        # amount, with pro_rata the deduction comes from the employee's own
        # salary
        if text in ("pro_rata", "prorata", "proportional", "salary_based",
                    "hourly", "per_hour", "pro_rata_salary"):
            record("late_deduction_policy", "pro_rata", item)
        elif text in ("per_occurrence", "per_instance", "per_time", "fixed",
                      "flat", "fine"):
            record("late_deduction_policy", "per_occurrence", item)
        elif text in ("per_minute", "per_min"):
            record("late_deduction_policy", "per_minute", item)
        elif text in ("none", "no", "nil"):
            record("late_deduction_policy", "none", item)
        else:
            warnings.append(f"late_deduction_policy: '{item['value']}' could not be understood")

    for name in ("undertime_deduction", "unpaid_leave_deduction"):
        item = entry(name)
        if not item:
            continue
        text = str(item["value"]).strip().lower().replace(" ", "_")
        if text in ("pro_rata", "prorata", "proportional", "yes", "deducted"):
            record(name, "pro_rata", item)
        elif text in ("none", "no", "nil", "not_deducted"):
            record(name, "none", item)
        else:
            warnings.append(f"{name}: '{item['value']}' could not be understood")

    # ──── Absence ────
    # Its value is "per_day", not "pro_rata" — but the LLM often treats
    # them as the same thing, and the meaning is in fact the same (one day
    # absent = one day's pay). So both are accepted.
    item = entry("absent_deduction")
    if item:
        text = str(item["value"]).strip().lower().replace(" ", "_")
        if text in ("per_day", "perday", "pro_rata", "prorata", "daily",
                    "full_day", "yes", "deducted", "proportional"):
            record("absent_deduction", "per_day", item)
        elif text in ("none", "no", "nil", "not_deducted"):
            record("absent_deduction", "none", item)
        else:
            warnings.append(f"absent_deduction: '{item['value']}' could not be understood")

    # ──── Boolean ────
    item = entry("enforce_shift_window")
    if item:
        value = item["value"]
        if isinstance(value, str):
            value = value.strip().lower() in ("true", "yes", "1")
        record("enforce_shift_window", bool(value), item)

    # The cross-check is NOT here — the quotes are verified first, or a
    # warning would be raised on a field that was about to be dropped anyway
    return fields, warnings


NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty",
    24: "twenty four", 30: "thirty", 45: "forty five", 48: "forty eight",
    60: "sixty", 90: "ninety",
}

# Fields whose value is not a number — their evidence lives in words
KEYWORD_EVIDENCE = {
    "break_policy": r"break|lunch|meal|prayer|rest\b",
    "enforce_shift_window": r"check.?in|attendance|clock.?in|shift (?:end|hour|time)",
    "working_days": r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                    r"weekday|weekend|working day|week\b",

    # ──── The payroll enum rules ────
    # These are words, not numbers — "per_minute" has no digit to look for
    # inside a quote. Without these, `_has_evidence` simply returned True
    # for them, meaning the LLM could back all three with any line at all.
    # The very mistake that happened with the working hours.
    # All three are DEDUCTION rules, so the sentence must carry TWO things:
    # the subject (late / undertime / unpaid leave) AND the fact that money
    # is deducted for it. One alone is not enough:
    #
    #   · subject only  → "arrival after 9:50 AM is recorded as late"
    #     that is only about recording; deduction is not mentioned at all —
    #     yet the LLM filled late_deduction_policy = per_occurrence from it.
    #   · deduction only → "All deductions are made at the end of the month"
    #     one generic sentence would fill all three rules.
    #
    # And "unpaid" alone will not do either: "the lunch break is unpaid" is
    # not about unpaid LEAVE. Otherwise the CEO's "pro_rata" would quietly
    # become "none" — switching off the unpaid-leave deduction entirely.
    "late_deduction_policy":
        r"(?s)(?=.*(?:late|tardy|delayed arrival))"
        r"(?=.*(?:deduct|fine|penalt|forfeit|cut from|salary|wage|pay\b))",
    "undertime_deduction":
        r"(?s)(?=.*(?:under.?time|short(?:er)? (?:hour|time)|fewer hour|"
        r"less than the required|incomplete hour|kam ghant))"
        r"(?=.*(?:deduct|pro.?rata|forfeit|cut from|salary|wage|pay\b))",
    "unpaid_leave_deduction":
        r"(?s)(?=.*(?:unpaid leave|leave without pay|\blwp\b|"
        r"unpaid absence|unpaid day))"
        r"(?=.*(?:deduct|pro.?rata|forfeit|cut from|salary|wage|pay\b))",

    # Absence — the same two-part rule. Here the subject is "absence" but
    # NOT "unpaid leave": that is a separate rule (the person gave notice).
    # So "leave" words are deliberately excluded, or a sentence about
    # unpaid leave would fill this field too.
    "absent_deduction":
        r"(?s)(?=.*(?:absent|absence|absenteeism|no.?show|"
        r"unauthorised absence|unauthorized absence|without notification))"
        r"(?=.*(?:deduct|forfeit|cut from|salary|wage|unpaid|pay\b))",
}


def _times_in(text: str) -> list:
    """
    Pull time-like tokens out of the text → ["09:30", "18:30"]

    "15 days" is not a time — so there must be either a ":" or an AM/PM.
    Treating a bare number as a time would be the biggest misreading of all.
    """
    found = []
    pattern = r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?"

    for m in re.finditer(pattern, text, re.I):
        hour, minute, meridiem = m.group(1), m.group(2), (m.group(3) or "").lower()
        if minute is None and not meridiem:
            continue

        h, mm = int(hour), int(minute or 0)
        if h > 23 or mm > 59:
            continue
        if "p" in meridiem and h < 12:
            h += 12
        elif "a" in meridiem and h == 12:
            h = 0

        found.append(f"{h:02d}:{mm:02d}")

    return found


def _has_evidence(name: str, value, text: str) -> bool:
    """
    Does this text contain EVIDENCE for this field's value?

    That is: a line said to prove shift_start = "09:30" must contain 9:30.
    A line reading "Annual leave: 15 days" cannot be a citation for 9:30,
    no matter how genuinely it appears in the document.
    """
    low = text.lower()

    if name in ("shift_start", "shift_end"):
        return value in _times_in(low)

    if name in KEYWORD_EVIDENCE:
        return bool(re.search(KEYWORD_EVIDENCE[name], low))

    # ──── Everything else is a number ────
    try:
        num = float(value)
    except (TypeError, ValueError):
        return True                       # nothing to check, so let it pass

    whole = int(num)
    if abs(num - whole) < 0.001:
        if re.search(rf"\b{whole}\b", low):
            return True
        word = NUMBER_WORDS.get(whole)
        return bool(word and word in low)

    # 8.5 → "8.5" ya "8½" ya "8 1/2"
    return bool(re.search(rf"\b{whole}\s*[.,]\s*5\b", low))


def _verify_quotes(fields, policy_text: str) -> tuple:
    """
    Is each field's quote REALLY in the document? If not, the field is dropped.

    ═══ THIS IS THE MOST IMPORTANT GUARD ═══
    The LLM is told plainly "do not report anything not in the document",
    and fills it in from its own HR knowledge anyway. In testing it was
    given a LEAVE-only policy — with no mention of timings at all — and the
    agent produced a complete "09:00 – 17:00, Monday–Friday, 15 min
    tolerance". That would have gone straight onto the CEO's real shift.

    A prompt cannot stop this. But a fabricated value has a fabricated
    citation — and that CAN be checked. If the quote is found in the
    document, the value came from the document.

    Two questions — both must be answered yes:

      1. Is the quote REALLY in the document?
      2. Does that quote contain EVIDENCE for the value? (and the document too)

    The first question alone is not enough. In testing the agent quoted a
    genuine leave line — "Annual leave: 15 days per calendar year" — and on
    the strength of it sent shift_start = 09:00. The line was true, the
    value invented.

    Three degrees of a quote matching:
      • hu-ba-hu mila                 → rehne do
      • most of the words matched     → keep it, but with low confidence
      • not found                     → DROP it (the CEO will fill it in)
    """
    if not policy_text:
        return fields, []

    def norm(text):
        return re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())

    doc = re.sub(r"\s+", " ", norm(policy_text))
    kept, warnings = {}, []

    for name, item in fields.items():
        raw_quote = item.get("source_quote", "")
        quote = re.sub(r"\s+", " ", norm(raw_quote)).strip()
        label = FIELD_LABELS.get(name, name)

        if not quote:
            warnings.append(f"{label}: no line was quoted from the document — not applied")
            continue

        # ──── Question 2 first: is there evidence? ────
        # The document is checked too — the agent sometimes appends its own
        # invented line to the end of a quote, one the document never had
        if not (_has_evidence(name, item["value"], raw_quote)
                and _has_evidence(name, item["value"], policy_text)):
            warnings.append(
                f"{label}: the quoted line does not mention this value at "
                f"all — not applied"
            )
            continue

        if quote in doc:
            kept[name] = item
            continue

        # ──── A light paraphrase — look at the longest common run ────
        words = [w for w in quote.split() if w]
        longest = 0
        for start in range(len(words)):
            for end in range(len(words), start + longest, -1):
                if " ".join(words[start:end]) in doc:
                    longest = end - start
                    break

        if len(words) and longest >= max(5, int(0.6 * len(words))):
            item["confidence"] = "low"
            kept[name] = item
            warnings.append(
                f"{FIELD_LABELS.get(name, name)}: the citation does not fully "
                f"match the document — please check"
            )
        else:
            warnings.append(
                f"{FIELD_LABELS.get(name, name)}: the quoted line is not in the "
                f"document at all — this value was not applied"
            )

    return kept, warnings


# ══════════════════════════════════════════════
# Labeled rows — the policy's clearest statement
# ══════════════════════════════════════════════
# Real policies often contain a summary table:
#
#     Start of Work            09:00 AM
#     Grace Period             09:00 AM – 09:15 AM
#     Lunch Break              01:00 PM – 02:00 PM
#     End of Work              07:00 PM
#     Total Shift Duration     9 Hours
#
# "End of Work 07:00 PM" is the clearest statement in the document — no
# sentence the LLM picks can beat it. But the LLM usually grabs a nearby
# prose line instead (it does not value tables).
#
# So this check sits OUTSIDE the LLM — a regex straight over the document.
# Whatever it finds outranks the LLM's answer.
START_LABEL = re.compile(
    r"start(?:ing)?\s*(?:of\s*)?(?:work|shift|time|duty|day)"
    r"|shift\s*start|work(?:ing)?\s*(?:day\s*)?(?:begins|starts)"
    r"|reporting\s*time|office\s*(?:opens|opening)|opening\s*time",
    re.I,
)
END_LABEL = re.compile(
    r"end(?:ing)?\s*(?:of\s*)?(?:work|shift|time|duty|day)"
    r"|shift\s*end|work(?:ing)?\s*(?:day\s*)?ends"
    r"|office\s*(?:closes|closing)|closing\s*time",
    re.I,
)


def _labeled_times(policy_text: str) -> dict:
    """
    Find "<label> <time>" rows in the document.

    The condition: the line must carry the label AND EXACTLY ONE time. A
    line with two times (like "Grace Period 09:00 AM – 09:15 AM") is a
    range, not a field assignment — it is skipped.

    If one label gives two different times, nothing is returned — the
    document contradicts itself and we cannot decide for it.

    Return: {"shift_start": (hhmm, line), ...}  — only when unambiguous
    """
    found = {"shift_start": {}, "shift_end": {}}

    for line in re.split(r"[\r\n]+", policy_text):
        times = _times_in(line)
        if len(times) != 1:
            continue

        for name, pattern in (("shift_start", START_LABEL), ("shift_end", END_LABEL)):
            if pattern.search(line):
                found[name].setdefault(times[0], line.strip())

    out = {}
    for name, hits in found.items():
        if len(hits) == 1:                      # a single answer — unambiguous
            value, line = next(iter(hits.items()))
            out[name] = (value, line)
    return out


BREAK_LABEL = re.compile(
    r"lunch\s*break|break\s*(?:time|period|hours?|timing)"
    r"|lunch\s*(?:time|hour|period)|meal\s*break|prayer\s*break|tea\s*break",
    re.I,
)


def _labeled_break(policy_text: str) -> tuple:
    """
    Find the break row — but here there are two times, not one.

        Lunch Break              01:00 PM – 02:00 PM

    So `_labeled_times()` (which demands exactly ONE time) cannot catch it.
    A break is a RANGE, not a single field.

    If one label gives two different ranges, nothing is returned.

    Return: (start, end, line) ya None
    """
    hits = {}

    for line in re.split(r"[\r\n]+", policy_text):
        if not BREAK_LABEL.search(line):
            continue
        times = _times_in(line)
        if len(times) == 2 and times[0] != times[1]:
            hits.setdefault((times[0], times[1]), line.strip())

    if len(hits) != 1:
        return None

    (start, end), line = next(iter(hits.items()))
    return start, end, line


def _apply_labeled_break(fields, policy_text: str, warnings: list) -> tuple:
    """
    If the break row is found it wins — and the duration is derived from it.

    `break_minutes` may also arrive separately from the LLM, but a duration
    stored in two places eventually disagrees with the times ("1 hour"
    written while the range is 45 minutes). When the times exist, the
    duration always comes from them.
    """
    if not policy_text:
        return fields, warnings

    found = _labeled_break(policy_text)
    if not found:
        return fields, warnings

    start, end, line = found
    span = _times_in(line)
    minutes = _minutes_between(start, end)

    for name, value in (("break_start", start), ("break_end", end),
                        ("break_minutes", minutes)):
        label = FIELD_LABELS[name]
        warnings = [w for w in warnings if not w.startswith(f"{label}:")]
        fields[name] = {
            "value": value,
            "source_quote": line[:500],
            "confidence": "high",
        }

    return fields, warnings


def _minutes_between(start: str, end: str) -> int:
    """'13:00' to '14:00' = 60. Correct even across midnight."""
    s = int(start[:2]) * 60 + int(start[3:5])
    e = int(end[:2]) * 60 + int(end[3:5])
    return (e - s) if e >= s else (24 * 60 - s + e)


def _apply_labeled_times(fields, policy_text: str, warnings: list) -> tuple:
    """
    If a table row is found it wins — the LLM's answer does not override it.

    This does not weaken the guard: the value still comes from the
    document, not the LLM. In fact this evidence is STRONGER than an LLM
    citation, because the line itself says which field the value belongs to.
    """
    if not policy_text:
        return fields, warnings

    for name, (value, line) in _labeled_times(policy_text).items():
        label = FIELD_LABELS[name]
        current = fields.get(name)

        # Whatever was said about this field before is now superseded —
        # the table row decides
        warnings = [w for w in warnings if not w.startswith(f"{label}:")]

        if current is None:
            # The LLM missed it — the document states it plainly
            fields[name] = {
                "value": value,
                "source_quote": line[:500],
                "confidence": "high",
            }

        elif current["value"] != value:
            # The document disagrees with itself — the table says one thing,
            # the prose another
            warnings.append(
                f"{label}: the document says two different things — "
                f'"{line.strip()}" as well as {current["value"]}. '
                f"The table value was used; please check"
            )
            fields[name] = {
                "value": value,
                "source_quote": line[:500],
                "confidence": "low",
            }

        else:
            # The same value, but the table citation is clearer
            fields[name] = {
                "value": value,
                "source_quote": line[:500],
                "confidence": "high",
            }

    return fields, warnings


def _rescue_shift_pair(proposed, kept, policy_text: str, warnings: list) -> tuple:
    """
    Shift start and end are a PAIR — the LLM often quotes ONE line for both.

    ═══ THE REAL CASE ═══
    The document said plainly:
        "Monday to Friday: 09:00 AM – 06:00 PM"
    The LLM gave both values perfectly correctly (09:00 and 18:00), but
    cited this line for both:
        "Employees are expected to be available and ready to work at 09:00 AM."
    That line does not contain 18:00 at all, so `shift_end` was left
    without evidence and dropped — even though the value was in the document.

    Loosening the guard would be wrong (that same guard stops invented
    values). So: another route — find a line in the document that states
    EXACTLY that pair. If one exists, keep both, cited to that line and at
    `low` confidence.

    If the pair is not in the document nothing happens — an invented value
    still cannot get in (such as 09:00–17:00 in a leave-only document).
    """
    if "shift_start" in kept and "shift_end" in kept:
        return kept, warnings          # both were already verified

    start = proposed.get("shift_start", {}).get("value")
    end = proposed.get("shift_end", {}).get("value")
    if not start or not end or start == end or not policy_text:
        return kept, warnings

    for line in re.split(r"[\r\n]+", policy_text):
        times = _times_in(line)
        if len(times) < 2 or times[0] != start or times[1] != end:
            continue

        rescued = []
        for name, value in (("shift_start", start), ("shift_end", end)):
            if name in kept:
                continue
            kept[name] = {
                "value": value,
                "source_quote": line.strip()[:500],
                "confidence": "low",
            }
            rescued.append(name)

        # The rescued field's "not applied" warning is now wrong — remove
        # it, or the CEO gets the opposite message
        dropped_labels = tuple(f"{FIELD_LABELS[n]}:" for n in rescued)
        warnings = [w for w in warnings if not w.startswith(dropped_labels)]

        warnings.extend(
            f"{FIELD_LABELS[n]}: the agent quoted the wrong line — the "
            f"correct line was found in the document; please check"
            for n in rescued
        )
        break

    return kept, warnings


def _cross_check(fields) -> list:
    """
    Fields can each be valid and still be nonsense together.
    Here we only WARN — the CEO decides; we never change things silently.
    """
    warnings = []

    def val(name):
        return fields.get(name, {}).get("value")

    start, end = val("shift_start"), val("shift_end")

    if start and end:
        if start == end:
            warnings.append("Shift start and end are the same time — please check")
        else:
            sh, sm = (int(x) for x in start.split(":"))
            eh, em = (int(x) for x in end.split(":"))
            s, e = sh * 60 + sm, eh * 60 + em
            length = (e - s) if e > s else (24 * 60 - s + e)

            if length < 60:
                warnings.append(f"The shift works out to only {length} minutes — please check")
            elif length > 16 * 60:
                warnings.append(
                    f"The shift works out to {length // 60} hours — "
                    f"the AM/PM may have been read the wrong way round"
                )

            # Does the shift length agree with the minimum hours?
            min_hours = val("min_daily_hours")
            if min_hours and min_hours > length / 60 + 0.01:
                warnings.append(
                    f"A minimum of {min_hours} hours is required but the shift "
                    f"is only {round(length / 60, 1)} hours"
                )

    ot, min_hours = val("overtime_threshold"), val("min_daily_hours")
    if ot and min_hours and ot < min_hours:
        warnings.append(
            f"Overtime starts at {ot} hours, which is below the minimum "
            f"of {min_hours} hours"
        )

    return warnings


# ══════════════════════════════════════════════
# Graph
# ══════════════════════════════════════════════
def build_work_policy_graph():
    graph = StateGraph(WorkPolicyState)
    graph.add_node("extract", extract_node)
    graph.add_node("rag", rag_node)
    graph.add_node("llm", llm_node)

    graph.set_entry_point("extract")
    graph.add_edge("extract", "rag")
    graph.add_edge("rag", "llm")
    graph.add_edge("llm", END)
    return graph.compile()


work_policy_graph = build_work_policy_graph()


def extract_work_policy(company_id: int, policy_text: str = "") -> dict:
    """
    Extract the working hours from the policy — ONLY the fields in the document.

    Return: {fields, warnings, chunks_used, error}
    """
    result = work_policy_graph.invoke({
        "company_id": company_id,
        "policy_text": policy_text or "",
        "retrieved_chunks": [],
        "fields": {},
        "warnings": [],
        "error": "",
    })

    return {
        "fields": result.get("fields", {}),
        "warnings": result.get("warnings", []),
        "chunks_used": len(result.get("retrieved_chunks", [])),
        "error": result.get("error", ""),
    }
