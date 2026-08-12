"""
Policy Extraction Agent
───────────────────────
CEO ki policy document parh kar leave types TAJWEEZ karta hai.

AHEM USOOL — yeh agent kuch SAVE nahi karta:
Wo sirf mashwara deta hai, CEO review karta hai aur khud confirm karta hai.
Isi liye LLM ki koi ghalti seedha production balance mein nahi jaati.

    Policy PDF
        │
    extract_node   → document ka text (ya ChromaDB ke chunks)
        │
    rag_node       → leave se mutalliq hisse chunte hain
        │
    llm_node       → {types: [...], confidence, source_quote}
        │
    CEO review + confirm  →  company_leave_types

Har tajweez ke saath `source_quote` hota hai — document ki wo asal line
jis se yeh value nikli. CEO khud dekh sakta hai ke agent ne sahi parha ya nahi.
"""

import json
import os
import re

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Optional

load_dotenv()


# ──── Retrieval ke liye query ────
# Leave policy alfaaz mein aisi hi likhi hoti hai
RETRIEVAL_QUERY = """
leave policy entitlement days per year
annual casual sick emergency unpaid maternity paternity study leave
number of days allowed medical certificate required
advance notice prior approval how many days before applying
"""

# Isse kam similarity wale chunks policy nahi mane jate
MIN_SIMILARITY = 0.20

# Itne chunks LLM ko dete hain
TOP_CHUNKS = 8

# LLM se aayi values ki hadd — bahar ho to CEO ko warn karte hain
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
# Node 1: Document ka text
# ══════════════════════════════════════════════
def extract_node(state: ExtractionState) -> ExtractionState:
    """
    Agar caller ne poora text de diya hai to wahi use karo.
    Warna ChromaDB ke chunks pe guzara karenge (agla node).
    """
    text = (state.get("policy_text") or "").strip()
    return {**state, "policy_text": text}


# ══════════════════════════════════════════════
# Node 2: RAG — leave se mutalliq hisse
# ══════════════════════════════════════════════
def rag_node(state: ExtractionState) -> ExtractionState:
    """
    ChromaDB se leave se mutalliq chunks nikalo.

    Poora document LLM ko dena mehnga bhi hai aur natija bhi kharab —
    parking rules aur dress code beech mein aa kar dhyan bata dete hain.
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
        print(f"Policy extraction RAG error: {e}")
        return {**state, "retrieved_chunks": [], "error": str(e)}


# ══════════════════════════════════════════════
# Node 3: LLM — types nikalo
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
      "source_quote": "Employees are entitled to 15 days of annual leave...",
      "confidence": "high"
    }}
  ]
}}"""


def llm_node(state: ExtractionState) -> ExtractionState:
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
            "types": [],
            "warnings": ["Policy document mein leave se mutalliq kuch nahi mila"],
        }

    # Bohot lamba text LLM ko confuse karta hai aur mehnga bhi hai
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
            "warnings": ["Agent policy parh nahi paya — types manually banayein"],
            "error": str(e),
        }


def _sanitize(raw_types) -> tuple:
    """
    LLM ke jawab ko qabil-e-etemad banao.

    LLM kuch bhi bhej sakta hai — "fifteen days", manfi adad, code mein
    space. Yahan sab saaf hota hai, aur jo ajeeb lage us par CEO ko
    warning milti hai (chup chaap theek nahi karte).
    """
    types, warnings = [], []
    seen = set()

    if not isinstance(raw_types, list):
        return [], ["Agent ka jawab samajh nahi aaya"]

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
            warnings.append(f"{label}: din ka adad samajh nahi aaya, 0 rakha hai")
            days = 0

        if days < 0:
            days = 0
        if days > MAX_REASONABLE_DAYS:
            warnings.append(f"{label}: {days} din ajeeb lagta hai — zaroor check karein")

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

        # ──── Bina hawale ke tajweez par bharosa kam ────
        if not quote:
            confidence = "low"
            warnings.append(f"{label}: document se koi line quote nahi hui")

        types.append({
            "code": code,
            "label": label,
            "days_per_year": days,
            "is_unlimited": bool(item.get("is_unlimited")),
            "requires_certificate": bool(item.get("requires_certificate")),
            "advance_notice_days": notice,
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
    Policy se leave types nikalo — SIRF tajweez, kuch save nahi hota.

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
