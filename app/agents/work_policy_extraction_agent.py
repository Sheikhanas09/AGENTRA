"""
Work Policy Extraction Agent
────────────────────────────
CEO ki policy document parh kar **working hours** ke fields nikalta hai —
shift timings, working days, late tolerance, overtime, break policy.

Leave types wale agent (`policy_extraction_agent.py`) ka bhai hai: wahi
3-node shape, wahi lazy LLM/Chroma, wahi "source_quote + confidence" usool.
Farq sirf yeh hai ke wo LIST nikalta hai, yeh alag alag FIELDS.

    Policy PDF
        │
    extract_node   → document ka text (ya ChromaDB ke chunks)
        │
    rag_node       → working hours se mutalliq hisse chunte hain
        │
    llm_node       → {fields: {shift_start: {...}, ...}}
        │
    settings.py    → sirf wahi fields set hote hain jo MILE
                     baqi CEO khud bharta hai

═══ SAB SE AHEM USOOL ═══
Jo field document mein NAHI hai, wo yahan aata hi nahi. Us ki maujooda
value ko haath nahi lagta — CEO ne jo manually set kiya tha wo waise ka
waisa rehta hai.

═══ AM/PM ═══
Sab se khatarnak ghalti yehi hai: "5 PM" ka "05:00" ban jana. Poori shift
ulti ho jati hai aur attendance ka hisaab kharab. Is liye LLM se 24-hour
value ke SAATH document ka asal lafz (`raw`) bhi mangte hain aur khud
milaate hain — takraar ho to raw jeetta hai.
"""

import json
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from typing import TypedDict

load_dotenv()


# ──── Retrieval ke liye query ────
# Working hours policy inhi alfaaz mein likhi hoti hai
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

# Warning messages mein aam fehm naam — CEO ko `late_tolerance_mins` nahi
# padhna chahiye
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
    "break_minutes": "Break ki muddat",
    "break_start": "Break start",
    "break_end": "Break end",
}

# ──── Har field ki hadd — is se bahar ho to CEO ko warning ────
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
}


class WorkPolicyState(TypedDict):
    company_id: int
    policy_text: str
    retrieved_chunks: list
    fields: dict
    warnings: list
    error: str


# ══════════════════════════════════════════════
# Node 1: Document ka text
# ══════════════════════════════════════════════
def extract_node(state: WorkPolicyState) -> WorkPolicyState:
    """Caller ne poora text diya ho to wahi, warna chunks pe guzara"""
    return {**state, "policy_text": (state.get("policy_text") or "").strip()}


# ══════════════════════════════════════════════
# Node 2: RAG — working hours wale hisse
# ══════════════════════════════════════════════
def rag_node(state: WorkPolicyState) -> WorkPolicyState:
    """
    ChromaDB se timings se mutalliq chunks nikalo.

    Poora document dena mehnga bhi hai aur natija bhi kharab — leave
    ki tafseel aur dress code beech mein aa kar dhyan bata dete hain.
    """
    try:
        # Lazy import — GROQ key na ho to bhi module load ho jaye
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
# Node 3: LLM — fields nikalo
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


def llm_node(state: WorkPolicyState) -> WorkPolicyState:
    """Chunks LLM ko do, JSON wapas lo, phir usay bharose ke qabil banao"""
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
            "warnings": ["Policy document mein working hours se mutalliq kuch nahi mila"],
        }

    policy = policy[:12000]

    try:
        from app.agents.leave_agent import get_llm

        response = get_llm().invoke([
            SystemMessage(
                content="You extract structured HR data. Respond with valid JSON only. "
                        "Never invent information that is not in the provided text. "
                        "Omitting a field is always better than guessing it."
            ),
            HumanMessage(content=PROMPT.format(policy=policy)),
        ])

        raw = response.content.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        parsed = json.loads(raw)
        fields, warnings = _sanitize(parsed)

        # ──── Har field ka hawala document mein maujood hai? ────
        kept, quote_warnings = _verify_quotes(fields, policy)

        # ──── Shift ki jodi — sust hawale ki wajah se na giray ────
        kept, quote_warnings = _rescue_shift_pair(
            fields, kept, policy, quote_warnings
        )

        # ──── Table rows sab par bhaari — yeh sab se aakhir mein ────
        kept, quote_warnings = _apply_labeled_times(kept, policy, quote_warnings)
        kept, quote_warnings = _apply_labeled_break(kept, policy, quote_warnings)

        warnings.extend(quote_warnings)
        fields = kept

        # ──── Ab jo fields bachi hain, wo aapas mein mel khati hain? ────
        warnings.extend(_cross_check(fields))

        return {**state, "fields": fields, "warnings": warnings, "error": ""}

    except Exception as e:
        print(f"Work policy LLM error: {e}")
        return {
            **state,
            "fields": {},
            "warnings": ["Agent policy parh nahi paya — working hours manually set karein"],
            "error": str(e),
        }


# ══════════════════════════════════════════════
# Time parsing — sab se nazuk hissa
# ══════════════════════════════════════════════
def parse_time_value(value, raw="") -> tuple:
    """
    LLM ki time value ko "HH:MM" (24-hour) banao.

    ═══ AM/PM KI GHALTI ═══
    Yehi wo ghalti hai jo poori shift ulti kar deti hai: "5 PM" ka
    "05:00" ban jana. Attendance phir 5 baje SUBAH shuru maanti hai.

    Is liye LLM se document ka asal lafz (`raw`) bhi mangte hain. Agar
    raw mein "pm" likha ho aur value 12 se kam ghanta de rahi ho, to
    RAW jeetta hai — document sach hai, LLM ka conversion nahi.

    Return: (hhmm_string | None, warning | None)
    """
    text = str(value or "").strip()
    raw_text = str(raw or "").strip().lower()

    # ──── Pehle value se ghanta/minute nikalo ────
    m = re.search(r"(\d{1,2})\s*[:.\s]\s*(\d{2})", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d{1,2})", text)
        if not m:
            return None, None
        hour, minute = int(m.group(1)), 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, f"'{value}' waqt samajh nahi aaya"

    warning = None

    # ──── Value mein khud AM/PM likha ho ────
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
                f"Document mein '{raw or value}' likha hai magar agent ne "
                f"{hour}:{minute:02d} nikala — khud dekh lein"
            )

    return f"{hour:02d}:{minute:02d}", warning


def _normalize_days(value) -> tuple:
    """Din ke naam theek karo — "mon", "MONDAY", "Mondays" sab chalein"""
    if isinstance(value, str):
        value = re.split(r"[,;/]|\band\b", value)
    if not isinstance(value, list):
        return None, "Working days ki list samajh nahi aayi"

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
        return None, "Working days mein koi pehchana hua din nahi mila"

    # Hafte ki tarteeb mein rakho — UI isi tarteeb mein dikhata hai
    return [d for d in DAY_NAMES if d in seen], None


def _sanitize(parsed) -> tuple:
    """
    LLM ke jawab ko qabil-e-etemad banao.

    Har field alag qisam ka hai, is liye har ek ka apna hisaab. Jo
    field samajh na aaye wo CHUP CHAAP GIR jati hai (galat value
    lagane se behtar hai ke CEO khud bhar de).
    """
    fields, warnings = {}, []

    if not isinstance(parsed, dict):
        return {}, ["Agent ka jawab samajh nahi aaya"]

    def entry(name):
        """Field ka {value, source_quote, confidence} nikalo"""
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

        # ──── Bina hawale ke tajweez par bharosa kam ────
        if not quote:
            confidence = "low"
            warnings.append(f"{name}: document se koi line quote nahi hui")

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
            warnings.append(f"{name}: '{item['value']}' adad nahi hai — chhor diya")
            continue
        note = None
        if not lo <= num <= hi:
            note = f"{name}: {num} hadd se bahar hai ({lo}–{hi}) — check karein"
            num = max(lo, min(num, hi))
        record(name, num, item, note)

    # ──── Ghante (decimal) ────
    for name, (lo, hi) in FLOAT_LIMITS.items():
        item = entry(name)
        if not item:
            continue
        try:
            num = round(float(item["value"]), 2)
        except (TypeError, ValueError):
            warnings.append(f"{name}: '{item['value']}' adad nahi hai — chhor diya")
            continue
        note = None
        if not lo <= num <= hi:
            note = f"{name}: {num} hadd se bahar hai ({lo}–{hi}) — check karein"
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
            warnings.append(f"break_policy: '{item['value']}' samajh nahi aaya")

    # ──── Boolean ────
    item = entry("enforce_shift_window")
    if item:
        value = item["value"]
        if isinstance(value, str):
            value = value.strip().lower() in ("true", "yes", "1")
        record("enforce_shift_window", bool(value), item)

    # Cross-check yahan NAHI — pehle hawale ki tasdeeq hoti hai, warna
    # us field par warning aati jo aage chal kar gir hi jani thi
    return fields, warnings


NUMBER_WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 15: "fifteen", 20: "twenty",
    24: "twenty four", 30: "thirty", 45: "forty five", 48: "forty eight",
    60: "sixty", 90: "ninety",
}

# Jin fields ki value adad nahi — inka saboot lafzon mein hota hai
KEYWORD_EVIDENCE = {
    "break_policy": r"break|lunch|meal|prayer|rest\b",
    "enforce_shift_window": r"check.?in|attendance|clock.?in|shift (?:end|hour|time)",
    "working_days": r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                    r"weekday|weekend|working day|week\b",
}


def _times_in(text: str) -> list:
    """
    Text mein se waqt jaise tokens nikalo → ["09:30", "18:30"]

    "15 days" waqt nahi hai — is liye ya to ":" hona chahiye ya AM/PM.
    Sirf adad ko waqt maan lena sab se badi ghalat-fehmi hoti.
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
    Is text mein is field ki value ka SABOOT hai?

    Yani: shift_start = "09:30" kehne wali line mein 9:30 likha hona
    chahiye. "Annual leave: 15 days" wali line 9:30 ka hawala nahi ban sakti,
    chahe wo document mein maujood hi kyun na ho.
    """
    low = text.lower()

    if name in ("shift_start", "shift_end"):
        return value in _times_in(low)

    if name in KEYWORD_EVIDENCE:
        return bool(re.search(KEYWORD_EVIDENCE[name], low))

    # ──── Baqi sab adad hain ────
    try:
        num = float(value)
    except (TypeError, ValueError):
        return True                       # jis ka hisaab nahi, usay chhor do

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
    Har field ka hawala WAQAI document mein hai? Na ho to field gir jati hai.

    ═══ YEH SAB SE ZAROORI GUARD HAI ═══
    LLM se saaf kaha jata hai ke "jo document mein na ho wo mat batao",
    magar wo phir bhi apni HR maloomat se bhar deta hai. Test mein sirf
    LEAVE ki policy di gayi thi — jis mein timings ka zikr tak nahi tha —
    aur agent ne pura "09:00 – 17:00, Monday–Friday, 15 min tolerance"
    bana kar de diya. Wo seedha CEO ki asli shift par chal jata.

    Prompt se yeh nahi ruk sakta. Magar jhoothi value ka hawala bhi
    jhootha hota hai — aur wo CHECK ho sakta hai. Quote document mein
    milta hai to value document se aayi hai.

    Do sawaal — dono ka jawab haan hona chahiye:

      1. Quote WAQAI document mein hai?
      2. Us quote mein value ka SABOOT hai? (aur document mein bhi)

    Sirf pehla sawaal kaafi nahi. Test mein agent ne leave wali asli line
    quote ki — "Annual leave: 15 days per calendar year" — aur us ke sahare
    shift_start = 09:00 bhej diya. Line sachi thi, value man-gharat.

    Quote milne ke teen darje:
      • hu-ba-hu mila                 → rehne do
      • zyada tar alfaaz mil gaye     → rehne do magar confidence low
      • nahi mila                     → HATA do (CEO khud bharega)
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
            warnings.append(f"{label}: document se koi line quote nahi hui — nahi lagayi")
            continue

        # ──── Sawaal 2 pehle: saboot hai ya nahi ────
        # Document mein bhi dekhte hain — agent quote ke aakhir mein apni
        # banayi hui line jor deta hai, jo document mein hoti hi nahi
        if not (_has_evidence(name, item["value"], raw_quote)
                and _has_evidence(name, item["value"], policy_text)):
            warnings.append(
                f"{label}: jo line quote hui us mein is value ka zikr hi nahi — "
                f"nahi lagayi"
            )
            continue

        if quote in doc:
            kept[name] = item
            continue

        # ──── Halka sa paraphrase — sab se lamba mushtarak silsila dekho ────
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
                f"{FIELD_LABELS.get(name, name)}: hawala document se poora "
                f"nahi milta — khud dekh lein"
            )
        else:
            warnings.append(
                f"{FIELD_LABELS.get(name, name)}: jo line quote hui wo document "
                f"mein hai hi nahi — yeh value nahi lagayi gayi"
            )

    return kept, warnings


# ══════════════════════════════════════════════
# Labeled rows — policy ka sab se saaf bayan
# ══════════════════════════════════════════════
# Asli policies mein aksar ek summary table hota hai:
#
#     Start of Work            09:00 AM
#     Grace Period             09:00 AM – 09:15 AM
#     Lunch Break              01:00 PM – 02:00 PM
#     End of Work              07:00 PM
#     Total Shift Duration     9 Hours
#
# "End of Work 07:00 PM" is document ka sab se saaf bayan hai — LLM ki
# chuni hui koi bhi jumla us se behtar nahi ho sakta. Magar LLM aksar
# aas paas ki prose line uthata hai (aur table ki qadar nahi karta).
#
# Is liye yeh check LLM se BAHAR hai — seedha document par regex. Jo
# yahan mile wo LLM ke jawab par bhaari hai.
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
    Document mein "<label> <waqt>" wali rows dhoondo.

    Shart: line mein label ho AUR THEEK EK waqt ho. Do waqt wali line
    (jaise "Grace Period 09:00 AM – 09:15 AM") field assignment nahi,
    range hai — usay chhor dete hain.

    Ek hi label do alag waqt de raha ho to kuch wapas nahi karte —
    document khud confused hai, hum us par faisla nahi kar sakte.

    Return: {"shift_start": (hhmm, line), ...}  — sirf saaf soorat mein
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
        if len(hits) == 1:                      # ek hi jawab — saaf hai
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
    Break ki row dhoondo — magar yahan do waqt hote hain, ek nahi.

        Lunch Break              01:00 PM – 02:00 PM

    Is liye `_labeled_times()` (jo theek EK waqt maangta hai) ise pakad
    nahi sakta. Break aik RANGE hai, single field nahi.

    Ek hi label do alag range de raha ho to kuch wapas nahi karte.

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
    Break ki row mile to wohi chalti hai — aur muddat usi se nikalti hai.

    `break_minutes` LLM se alag bhi aa sakti hai, magar do jagah rakhi hui
    muddat kabhi na kabhi waqt se alag ho jati hai ("1 hour" likha ho magar
    range 45 minute ki nikle). Waqt maujood ho to muddat hamesha usi se.
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
    """'13:00' se '14:00' = 60. Aadhi raat paar ho to bhi theek."""
    s = int(start[:2]) * 60 + int(start[3:5])
    e = int(end[:2]) * 60 + int(end[3:5])
    return (e - s) if e >= s else (24 * 60 - s + e)


def _apply_labeled_times(fields, policy_text: str, warnings: list) -> tuple:
    """
    Table row mil jaye to wohi chalti hai — LLM ka jawab us par nahi.

    Yeh guard ko kamzor nahi karta: value document se hi aati hai, LLM
    se nahi. Balki yeh saboot LLM ke hawale se ZYADA mazboot hai, kyunki
    line khud kehti hai ke yeh kis field ki value hai.
    """
    if not policy_text:
        return fields, warnings

    for name, (value, line) in _labeled_times(policy_text).items():
        label = FIELD_LABELS[name]
        current = fields.get(name)

        # Is field par jo bhi pehle kaha gaya tha wo ab purana ho chuka —
        # faisla ab table row par hai
        warnings = [w for w in warnings if not w.startswith(f"{label}:")]

        if current is None:
            # LLM se rah gaya tha — document mein saaf likha hai
            fields[name] = {
                "value": value,
                "source_quote": line[:500],
                "confidence": "high",
            }

        elif current["value"] != value:
            # Document apne aap mein mukhtalif hai — table kuch, prose kuch
            warnings.append(
                f"{label}: document mein do alag baatein likhi hain — "
                f'"{line.strip()}" bhi aur {current["value"]} bhi. '
                f"Table wali li gayi hai, zaroor check karein"
            )
            fields[name] = {
                "value": value,
                "source_quote": line[:500],
                "confidence": "low",
            }

        else:
            # Wahi value, magar table ka hawala zyada saaf hai
            fields[name] = {
                "value": value,
                "source_quote": line[:500],
                "confidence": "high",
            }

    return fields, warnings


def _rescue_shift_pair(proposed, kept, policy_text: str, warnings: list) -> tuple:
    """
    Shift start aur end ek JODI hain — LLM aksar dono ke liye EK hi line
    quote kar deta hai.

    ═══ ASAL SOORAT ═══
    Document mein saaf likha tha:
        "Monday to Friday: 09:00 AM – 06:00 PM"
    LLM ne dono values bilkul sahi deen (09:00 aur 18:00), magar dono ka
    hawala yeh line di:
        "Employees are expected to be available and ready to work at 09:00 AM."
    Us line mein 18:00 hai hi nahi, is liye `shift_end` saboot ke bagair
    reh gaya aur gir gaya — halanke value document mein maujood thi.

    Guard ko dheela karna ghalat hota (wohi guard man-gharat values rokta
    hai). Is liye doosra rasta: document mein aisi line dhoondo jo THEEK
    wohi jodi batati ho. Mil jaye to dono rakh lo, usi line ke hawale ke
    saath aur `low` confidence par.

    Jodi document mein na ho to kuch nahi hota — man-gharat value ab bhi
    andar nahi aa sakti (jaise leave-only document mein 09:00–17:00).
    """
    if "shift_start" in kept and "shift_end" in kept:
        return kept, warnings          # dono pehle hi tasdeeq ho chuke

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

        # Jo field bach gayi, uska "nahi lagayi" wala warning ab ghalat
        # hai — usay hata do warna CEO ko ulta paigham milega
        dropped_labels = tuple(f"{FIELD_LABELS[n]}:" for n in rescued)
        warnings = [w for w in warnings if not w.startswith(dropped_labels)]

        warnings.extend(
            f"{FIELD_LABELS[n]}: agent ne ghalat line quote ki thi — "
            f"document se sahi line dhoond li gayi, khud dekh lein"
            for n in rescued
        )
        break

    return kept, warnings


def _cross_check(fields) -> list:
    """
    Field alag alag theek hon, phir bhi mil kar bemaani ho sakte hain.
    Yahan sirf WARN karte hain — CEO faisla kare, hum chup chaap na badlein.
    """
    warnings = []

    def val(name):
        return fields.get(name, {}).get("value")

    start, end = val("shift_start"), val("shift_end")

    if start and end:
        if start == end:
            warnings.append("Shift start aur end ek hi waqt hain — zaroor check karein")
        else:
            sh, sm = (int(x) for x in start.split(":"))
            eh, em = (int(x) for x in end.split(":"))
            s, e = sh * 60 + sm, eh * 60 + em
            length = (e - s) if e > s else (24 * 60 - s + e)

            if length < 60:
                warnings.append(f"Shift sirf {length} minute ki ban rahi hai — check karein")
            elif length > 16 * 60:
                warnings.append(
                    f"Shift {length // 60} ghante ki ban rahi hai — "
                    f"shayad AM/PM ulta parha gaya"
                )

            # Shift ki lambai aur minimum hours ka mel
            min_hours = val("min_daily_hours")
            if min_hours and min_hours > length / 60 + 0.01:
                warnings.append(
                    f"Minimum {min_hours} ghante mange ja rahe hain magar shift "
                    f"sirf {round(length / 60, 1)} ghante ki hai"
                )

    ot, min_hours = val("overtime_threshold"), val("min_daily_hours")
    if ot and min_hours and ot < min_hours:
        warnings.append(
            f"Overtime {ot} ghante par shuru ho raha hai jo minimum "
            f"{min_hours} ghante se kam hai"
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
    Policy se working hours nikalo — SIRF wo fields jo document mein hain.

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
