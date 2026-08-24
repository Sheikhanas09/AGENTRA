"""
Policy Extraction Agent
───────────────────────
Reads the CEO's policy document and SUGGESTS leave types.

IMPORTANT RULE — this agent SAVES nothing:
It only advises; the CEO reviews and confirms. That is why an LLM mistake
never lands directly in a production balance.

    Policy PDF
        │
    extract_node   → the document's text (or the ChromaDB chunks)
        │
    rag_node       → picks the parts that relate to leave
        │
    llm_node       → {types: [...], confidence, source_quote}
        │
    CEO review + confirm  →  company_leave_types

Every suggestion carries a `source_quote` — the exact line in the document
the value came from, so the CEO can check whether the agent read it right.
"""

import json
import os
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Optional

load_dotenv()


# ──── The retrieval query ────
# This is roughly how a leave policy is worded
RETRIEVAL_QUERY = """
leave policy entitlement days per year
annual casual sick emergency unpaid maternity paternity study leave
number of days allowed medical certificate required
advance notice prior approval how many days before applying
"""

# Chunks below this similarity are not treated as policy
MIN_SIMILARITY = 0.20

# This many chunks are given to the LLM
TOP_CHUNKS = 8

# Limits on the values from the LLM — beyond these the CEO is warned
MAX_REASONABLE_DAYS = 400
MAX_REASONABLE_NOTICE = 90


class ExtractionState(TypedDict):
    company_id: int
    policy_text: str
    retrieved_chunks: list
    types: list
    warnings: list
    error: str


# ══════════════════════════════════════════════
# Node 1: The document's text
# ══════════════════════════════════════════════
def extract_node(state: ExtractionState) -> ExtractionState:
    """
    If the caller supplied the full text, use it.
    Otherwise we make do with the ChromaDB chunks (the next node).
    """
    text = (state.get("policy_text") or "").strip()
    return {**state, "policy_text": text}


# ══════════════════════════════════════════════
# Node 2: RAG — the parts that relate to leave
# ══════════════════════════════════════════════
def rag_node(state: ExtractionState) -> ExtractionState:
    """
    Pull the leave-related chunks out of ChromaDB.

    Handing the whole document to the LLM is both expensive and worse —
    parking rules and dress codes get in the way and distract it.
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
        print(f"Policy extraction RAG error: {e}")
        return {**state, "retrieved_chunks": [], "error": str(e)}


# ══════════════════════════════════════════════
# Node 3: LLM — extract the types
# ══════════════════════════════════════════════
PROMPT = """You are an HR policy analyst. Read the company policy below and
extract every LEAVE TYPE it defines.

=== COMPANY POLICY ===
{policy}
=== END POLICY ===

For each leave type you find, report:
- code: lowercase single word (annual, casual, sick, emergency, unpaid,
  maternity, paternity, study, hajj, bereavement...)
- label: human readable name as written in the policy
- days_per_year: the entitlement number. Use 0 if the policy grants the leave
  but states no fixed quota. Use null if you cannot tell.
- is_unlimited: true only if the policy says it is unlimited or unpaid with no cap
- requires_certificate: true only if the policy explicitly requires a medical
  certificate or documentary proof
- advance_notice_days: how many days in advance it must be applied for.
  0 if it can be taken the same day (typically sick/emergency).
- is_paid: true if the employee keeps their salary during this leave, false if
  the policy calls it unpaid / without pay / leave without pay / LWP.
  OMIT this field entirely if the policy does not say either way — do NOT
  guess. Payroll deducts salary from this field, so a guess costs real money.
- source_quote: the EXACT sentence from the policy this came from. Do not
  paraphrase. This is how a human verifies you.
- confidence: "high" if the policy states it plainly, "low" if you inferred it

CRITICAL RULES:
- Report ONLY leave types actually mentioned in the policy above.
- Do NOT add common types from your own knowledge if the policy is silent.
- If the policy mentions no leave types at all, return an empty list.
- Never invent a source_quote. If you cannot quote it, set confidence to "low"
  and source_quote to "".

Respond ONLY with JSON:
{{
  "types": [
    {{
      "code": "annual",
      "label": "Annual Leave",
      "days_per_year": 15,
      "is_unlimited": false,
      "requires_certificate": false,
      "advance_notice_days": 7,
      "is_paid": true,
      "source_quote": "Employees are entitled to 15 days of annual leave...",
      "confidence": "high"
    }}
  ]
}}"""


def llm_node(state: ExtractionState) -> ExtractionState:
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
            "types": [],
            "warnings": ["Nothing about leave was found in the policy document"],
        }

    # Very long text confuses the LLM and costs more
    policy = policy[:12000]

    try:
        from app.agents.leave_agent import get_llm

        response = get_llm().invoke([
            SystemMessage(
                content="You extract structured HR data. Respond with valid JSON only. "
                        "Never invent information that is not in the provided text."
            ),
            HumanMessage(content=PROMPT.format(policy=policy)),
        ])

        raw = response.content.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        parsed = json.loads(raw)
        types, warnings = _sanitize(parsed.get("types", []))

        return {**state, "types": types, "warnings": warnings, "error": ""}

    except Exception as e:
        print(f"Policy extraction LLM error: {e}")
        return {
            **state,
            "types": [],
            "warnings": ["The agent could not read the policy — create the types manually"],
            "error": str(e),
        }


def _paid_value(raw, code: str, label: str):
    """
    Is this type paid? → True / False / **None**

    ═══ WHY None MATTERS ═══
    "Unknown" and "paid" are two completely different things. Treating
    silence as `True` would let every policy upload quietly turn the CEO's
    "unpaid" into "paid" — and the deduction for that type would be
    switched off forever.

    So there are three states, and `apply_policy_types()` decides:
      True/False → the agent read it plainly from the document
      None       → the document is silent — leave an existing type alone,
                   decide a new type from its NAME

    An LLM often sends words like "yes"/"unpaid" instead of a boolean, so
    strings are accepted too.
    """
    if isinstance(raw, bool):
        return raw

    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("true", "yes", "paid", "y"):
            return True
        if s in ("false", "no", "unpaid", "n"):
            return False

    # ──── The agent is silent — does the name itself say so? ────
    # It comes from one place, so the migration, the routes and the agent
    # can never decide differently
    from app.routes.leave import looks_unpaid

    return False if looks_unpaid(code, label) else None


def _sanitize(raw_types) -> tuple:
    """
    Make the LLM's answer trustworthy.

    An LLM can send anything — "fifteen days", a negative number, a space
    inside a code. Everything is cleaned here, and anything odd raises a
    warning for the CEO (we never fix things silently).
    """
    types, warnings = [], []
    seen = set()

    if not isinstance(raw_types, list):
        return [], ["The agent's answer could not be understood"]

    for item in raw_types:
        if not isinstance(item, dict):
            continue

        code = str(item.get("code") or "").strip().lower()
        code = re.sub(r"[^a-z0-9_]+", "_", code).strip("_")
        if not code or code in seen:
            continue
        seen.add(code)

        label = str(item.get("label") or code.replace("_", " ").title()).strip()

        # ──── Days ────
        days = item.get("days_per_year")
        try:
            days = int(days) if days is not None else 0
        except (TypeError, ValueError):
            warnings.append(f"{label}: the number of days was unreadable, set to 0")
            days = 0

        if days < 0:
            days = 0
        if days > MAX_REASONABLE_DAYS:
            warnings.append(f"{label}: {days} days looks odd — please check it")

        # ──── Notice ────
        notice = item.get("advance_notice_days")
        try:
            notice = int(notice) if notice is not None else 0
        except (TypeError, ValueError):
            notice = 0
        notice = max(0, min(notice, MAX_REASONABLE_NOTICE))

        quote = str(item.get("source_quote") or "").strip()
        confidence = str(item.get("confidence") or "low").lower()
        if confidence not in ("high", "low"):
            confidence = "low"

        # ──── A suggestion without a quote is trusted less ────
        if not quote:
            confidence = "low"
            warnings.append(f"{label}: no line was quoted from the document")

        # ──── Paid or unpaid — this affects MONEY directly ────
        paid = _paid_value(item.get("is_paid"), code, label)
        if paid is False:
            warnings.append(
                f"{label}: this is unpaid — payroll will deduct for each "
                f"day. Please confirm this"
            )

        types.append({
            "code": code,
            "label": label,
            "days_per_year": days,
            "is_unlimited": bool(item.get("is_unlimited")),
            "requires_certificate": bool(item.get("requires_certificate")),
            "advance_notice_days": notice,
            # True / False / None — None means "the document is silent"
            "is_paid": paid,
            "source_quote": quote[:500],
            "confidence": confidence,
        })

    return types, warnings


# ══════════════════════════════════════════════
# Graph
# ══════════════════════════════════════════════
def build_extraction_graph():
    graph = StateGraph(ExtractionState)
    graph.add_node("extract", extract_node)
    graph.add_node("rag", rag_node)
    graph.add_node("llm", llm_node)

    graph.set_entry_point("extract")
    graph.add_edge("extract", "rag")
    graph.add_edge("rag", "llm")
    graph.add_edge("llm", END)
    return graph.compile()


extraction_graph = build_extraction_graph()


def extract_leave_types(company_id: int, policy_text: str = "") -> dict:
    """
    Extract leave types from the policy — SUGGESTIONS only, nothing is saved.

    Return: {types, warnings, chunks_used, error}
    """
    result = extraction_graph.invoke({
        "company_id": company_id,
        "policy_text": policy_text or "",
        "retrieved_chunks": [],
        "types": [],
        "warnings": [],
        "error": "",
    })

    return {
        "types": result.get("types", []),
        "warnings": result.get("warnings", []),
        "chunks_used": len(result.get("retrieved_chunks", [])),
        "error": result.get("error", ""),
    }
