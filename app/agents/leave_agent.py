import os
import json
import chromadb
from datetime import datetime
from sentence_transformers import SentenceTransformer
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from typing import TypedDict
from dotenv import load_dotenv

from app.utils.llm import groq_model

load_dotenv()

# ──── Only these two decisions are accepted ────
AUTO_APPROVE = "auto_approve"
ESCALATE = "escalate_to_ceo"

# Chunks below this similarity are not treated as "policy found"
MIN_CHUNK_SIMILARITY = 0.25


# ──── Models — lazy init ────
# These used to be built as soon as the module loaded. Without a
# GROQ_API_KEY, or with a broken ML package, the import itself blew up and
# an employee could not apply for leave at all. They are now built on
# first use.
_embedding_model = None
_chroma_client = None
_llm = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        # The same model CV screening uses — text → vector
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(
            path=os.path.join(os.path.dirname(__file__), "..", "chroma_db")
        )
    return _chroma_client


def get_llm():
    global _llm
    if _llm is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        _llm = ChatGroq(
            api_key=api_key,
            model=groq_model(),
            temperature=0.1,
            # ↑ Very low — leave decisions must be consistent
            #
            # This was 1000, but gpt-oss is a REASONING model: its thinking
            # tokens come out of the same budget. On the full handbook the
            # answer was CUT off mid-way at 1000 (truncated JSON, parse
            # failure). At 4000 the real cost is ~1100 — plenty of room.
            max_tokens=4000,
        )
    return _llm


# ──── State ────
class LeaveAgentState(TypedDict):
    # ──── Inputs ────
    employee_id: int
    company_id: int
    leave_type: str        # "sick", "annual", "casual", etc.
    start_date: str        # "2026-05-10"
    end_date: str          # "2026-05-13"
    total_days: int        # 4
    reason: str
    has_medical_cert: bool
    leave_balance: int     # Remaining balance

    # ──── RAG Outputs ────
    retrieved_chunks: list
    retrieval_query: str

    # ──── LLM Outputs ────
    decision: str          # "auto_approve" ya "escalate_to_ceo"
    reason_text: str
    policy_reference: str
    requires_document: bool

    # ──── Error ────
    error: str


# ──── Node 1: RAG — retrieve the policy chunks ────
def rag_retrieval_node(state: LeaveAgentState) -> LeaveAgentState:
    """
    Pull the relevant policy chunks out of ChromaDB,
    from this company's own policy
    """

    # ──── Build the query ────
    query = f"""
    {state['leave_type']} leave policy rules
    maximum consecutive days auto approval
    medical certificate requirement
    conditions quota per year
    {state['total_days']} days leave request
    """
    # ↑ Build the query from the leave type and duration
    # Taake relevant policy clauses mile

    try:
        collection_name = f"company_{state['company_id']}_policies"
        # ──── Cosine space — otherwise `1 - distance` is not cosine similarity ────
        # (settings.py creates the collection with the same space)
        collection = get_chroma_client().get_or_create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        # ──── Embed the query ────
        query_embedding = get_embedding_model().encode(query).tolist()

        # ──── Search ChromaDB ────
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=5
            # ↑ Top 5 relevant chunks
        )

        chunks = []
        if results and results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                chunks.append({
                    "text": doc,
                    "similarity": 1 - results["distances"][0][i]
                    if results["distances"] else 0
                })

        return {
            **state,
            "retrieved_chunks": chunks,
            "retrieval_query": query.strip()
        }

    except Exception as e:
        print(f"RAG error: {e}")
        # ──── No policy found → escalate to the CEO ────
        return {
            **state,
            "retrieved_chunks": [],
            "retrieval_query": query.strip(),
            "error": str(e)
        }


# ──── Node 2: LLM Decision ────
def llm_decision_node(state: LeaveAgentState) -> LeaveAgentState:
    """
    Retrieved policy chunks + leave request → LLM → Decision
    """

    # ──── Only genuinely relevant chunks — no auto-approve on a weak match ────
    relevant = [
        c for c in state["retrieved_chunks"]
        if (c.get("similarity") or 0) >= MIN_CHUNK_SIMILARITY
    ]

    if not relevant:
        # ──── No policy at all (or completely unrelated) → straight to the CEO ────
        # Asking the LLM gains nothing; it sometimes approves anyway
        return {
            **state,
            "decision": ESCALATE,
            "reason_text": "No clear clause for this leave type was found in "
                           "the policy — HR will review it manually.",
            "policy_reference": "",
            "requires_document": False,
            "error": "",
        }

    policy_context = "\n\n".join([
        f"[Policy Clause {i+1}]: {chunk['text']}"
        for i, chunk in enumerate(relevant)
    ])

    # ──── Build the prompt ────
    prompt = f"""
You are the HRX Leave Agent. Evaluate this leave request strictly based on the company policy.

=== COMPANY POLICY CLAUSES ===
{policy_context}
=== END POLICY ===

=== LEAVE REQUEST ===
Leave Type: {state['leave_type']}
Duration: {state['total_days']} days ({state['start_date']} to {state['end_date']})
Reason: {state['reason']}
Medical Certificate Provided: {state['has_medical_cert']}
Remaining Balance: {state['leave_balance']} days
=== END REQUEST ===

Rules:
- If policy not found or unclear → escalate_to_ceo
- If balance is 0 → escalate_to_ceo
- If duration exceeds auto-approval limit → escalate_to_ceo
- If medical cert required but not provided → escalate_to_ceo
- If all conditions met → auto_approve

Respond ONLY in this JSON format:
{{
    "decision": "auto_approve or escalate_to_ceo",
    "reason": "explanation in one sentence",
    "policy_reference": "exact policy clause used",
    "requires_document": true or false
}}
"""

    try:
        messages = [
            SystemMessage(content="You are an HR leave evaluation agent. Always respond with valid JSON only."),
            HumanMessage(content=prompt)
        ]

        response = get_llm().invoke(messages)
        raw = response.content.strip()

        # ──── Clean up the JSON ────
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        result = json.loads(raw)

        # ──── Sanitise the decision ────
        # An LLM can write anything ("approve", "yes", garbage) — only a
        # plain "auto_approve" counts as auto_approve, otherwise it goes
        # to the CEO
        raw_decision = str(result.get("decision", "")).strip().lower()
        decision = AUTO_APPROVE if raw_decision == AUTO_APPROVE else ESCALATE

        return {
            **state,
            "decision": decision,
            "reason_text": result.get("reason", ""),
            "policy_reference": result.get("policy_reference", ""),
            "requires_document": result.get("requires_document", False),
            "error": ""
        }

    except Exception as e:
        print(f"LLM decision error: {e}")
        # ──── LLM failed → be conservative: send it to the CEO ────
        return {
            **state,
            "decision": ESCALATE,
            "reason_text": "Could not evaluate policy — escalating to CEO for manual review.",
            "policy_reference": "",
            "requires_document": False,
            "error": str(e)
        }


# ──── Build the graph ────
def build_leave_graph():
    graph = StateGraph(LeaveAgentState)

    graph.add_node("rag_retrieval", rag_retrieval_node)
    # ↑ Node 1: Policy chunks dhundo

    graph.add_node("llm_decision", llm_decision_node)
    # ↑ Node 2: Decision lo

    graph.set_entry_point("rag_retrieval")
    graph.add_edge("rag_retrieval", "llm_decision")
    graph.add_edge("llm_decision", END)

    return graph.compile()


leave_graph = build_leave_graph()


# ──── Main Function ────
def evaluate_leave_request(
    employee_id: int,
    company_id: int,
    leave_type: str,
    start_date: str,
    end_date: str,
    total_days: int,
    reason: str,
    has_medical_cert: bool,
    leave_balance: int
) -> dict:

    initial_state: LeaveAgentState = {
        "employee_id": employee_id,
        "company_id": company_id,
        "leave_type": leave_type,
        "start_date": start_date,
        "end_date": end_date,
        "total_days": total_days,
        "reason": reason,
        "has_medical_cert": has_medical_cert,
        "leave_balance": leave_balance,
        "retrieved_chunks": [],
        "retrieval_query": "",
        "decision": "",
        "reason_text": "",
        "policy_reference": "",
        "requires_document": False,
        "error": ""
    }

    result = leave_graph.invoke(initial_state)

    return {
        "decision": result["decision"],
        "reason": result["reason_text"],
        "policy_reference": result["policy_reference"],
        "requires_document": result["requires_document"],
        "retrieved_chunks": result["retrieved_chunks"],
        "retrieval_query": result["retrieval_query"],
        "error": result.get("error", "")
    }