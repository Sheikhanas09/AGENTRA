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

load_dotenv()

# ──── Sirf yeh do faisle qabool hain ────
AUTO_APPROVE = "auto_approve"
ESCALATE = "escalate_to_ceo"

# Isse kam similarity wale chunks ko "policy mil gayi" nahi maante
MIN_CHUNK_SIMILARITY = 0.25


# ──── Models — lazy init ────
# Pehle yeh module load hote hi ban jate the. Agar GROQ_API_KEY na ho ya
# koi ML package toota ho to import hi phat jata tha aur employee leave
# apply hi nahi kar pata tha. Ab pehli zarurat par bante hain.
_embedding_model = None
_chroma_client = None
_llm = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        # Same model jo CV screening mein use ki — text → vector
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
            raise RuntimeError("GROQ_API_KEY set nahi hai")
        _llm = ChatGroq(
            api_key=api_key,
            model="llama-3.1-8b-instant",
            temperature=0.1,
            # ↑ Very low — leave decisions consistent hone chahiye
            max_tokens=1000,
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


# ──── Node 1: RAG — Policy Chunks Retrieve karo ────
def rag_retrieval_node(state: LeaveAgentState) -> LeaveAgentState:
    """
    ChromaDB se relevant policy chunks nikalo
    Company ki specific policy se
    """

    # ──── Query banao ────
    query = f"""
    {state['leave_type']} leave policy rules
    maximum consecutive days auto approval
    medical certificate requirement
    conditions quota per year
    {state['total_days']} days leave request
    """
    # ↑ Leave type + duration se query banao
    # Taake relevant policy clauses mile

    try:
        collection_name = f"company_{state['company_id']}_policies"
        # ──── Cosine space — warna `1 - distance` cosine similarity nahi hoti ────
        # (settings.py mein bhi collection isi space se banti hai)
        collection = get_chroma_client().get_or_create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"}
        )

        # ──── Query embed karo ────
        query_embedding = get_embedding_model().encode(query).tolist()

        # ──── ChromaDB se search karo ────
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
        # ──── Policy nahi mili → CEO escalate ────
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

    # ──── Sirf waqai relevant chunks — halke match pe auto-approve nahi ────
    relevant = [
        c for c in state["retrieved_chunks"]
        if (c.get("similarity") or 0) >= MIN_CHUNK_SIMILARITY
    ]

    if not relevant:
        # ──── Policy mili hi nahi (ya bilkul be-rabt hai) → seedha CEO ────
        # LLM se poochne ka faida nahi, wo kabhi kabhi phir bhi approve kar deta hai
        return {
            **state,
            "decision": ESCALATE,
            "reason_text": "Is leave type ke liye policy mein koi wazeh clause "
                           "nahi mila — CEO manually review karega.",
            "policy_reference": "",
            "requires_document": False,
            "error": "",
        }

    policy_context = "\n\n".join([
        f"[Policy Clause {i+1}]: {chunk['text']}"
        for i, chunk in enumerate(relevant)
    ])

    # ──── Prompt banao ────
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

        # ──── JSON clean karo ────
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        result = json.loads(raw)

        # ──── Decision sanitize karo ────
        # LLM kuch bhi likh sakta hai ("approve", "yes", garbage) —
        # sirf saaf "auto_approve" ko auto_approve maano, warna CEO ke paas
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
        # ──── LLM fail → Conservative: CEO ko bhejo ────
        return {
            **state,
            "decision": ESCALATE,
            "reason_text": "Could not evaluate policy — escalating to CEO for manual review.",
            "policy_reference": "",
            "requires_document": False,
            "error": str(e)
        }


# ──── Graph Build karo ────
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