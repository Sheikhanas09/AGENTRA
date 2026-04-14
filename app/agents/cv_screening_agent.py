import json
import os
from typing import TypedDict
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
import chromadb
from sentence_transformers import SentenceTransformer

load_dotenv()

llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="llama-3.1-8b-instant",
    temperature=0.3,
    max_tokens=1500
)

# ──── ChromaDB + Sentence Transformer initialize ────
chroma_client = chromadb.PersistentClient(
    path=os.path.join(os.path.dirname(__file__), "..", "chroma_db")
)
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")


# ──── RAG: Job Description store karo ────
def store_job_in_vectordb(job_id: int, job_title: str, job_description: str, job_skills: str, job_keywords: str):
    collection = chroma_client.get_or_create_collection(name="job_descriptions")
    doc_id = f"job_{job_id}"

    full_job_text = f"""
    Job Title: {job_title}
    Required Skills: {job_skills}
    Keywords: {job_keywords}
    Description: {job_description}
    """.strip()

    embedding = embedding_model.encode(full_job_text).tolist()

    # ──── Already exist karta hai to update karo ────
    existing = collection.get(ids=[doc_id])
    if existing["ids"]:
        collection.update(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[full_job_text],
            metadatas=[{"job_id": job_id, "job_title": job_title}]
        )
    else:
        collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[full_job_text],
            metadatas=[{"job_id": job_id, "job_title": job_title}]
        )


# ──── RAG: CV ko job se match karo ────
def get_rag_similarity(job_id: int, cv_text: str) -> float:
    try:
        collection = chroma_client.get_or_create_collection(name="job_descriptions")
        cv_embedding = embedding_model.encode(cv_text[:2000]).tolist()

        results = collection.query(
            query_embeddings=[cv_embedding],
            n_results=1,
            where={"job_id": job_id}
        )

        if results and results["distances"] and results["distances"][0]:
            # Distance 0 = perfect match, 2 = worst
            # Convert to similarity score 0-100
            distance = results["distances"][0][0]
            similarity = max(0, (1 - distance / 2) * 100)
            return round(similarity, 2)
        return 0.0

    except Exception as e:
        print(f"RAG similarity error: {e}")
        return 0.0


class CVScreeningState(TypedDict):
    candidate_id: int
    job_id: int
    candidate_name: str
    candidate_email: str
    cv_text: str
    job_title: str
    job_description: str
    job_keywords: str
    job_experience: str
    job_skills: str
    rag_score: float       # ← RAG similarity score
    match_score: float
    skill_gap: str
    summary: str
    error: str


# ──── Node 1: RAG Similarity Score ────
def rag_similarity_node(state: CVScreeningState) -> CVScreeningState:
    # ──── Job description Vector DB mein store karo ────
    store_job_in_vectordb(
        job_id=state["job_id"],
        job_title=state["job_title"],
        job_description=state["job_description"],
        job_skills=state["job_skills"],
        job_keywords=state["job_keywords"]
    )

    # ──── CV ko job se semantically match karo ────
    rag_score = get_rag_similarity(
        job_id=state["job_id"],
        cv_text=state["cv_text"]
    )

    return {**state, "rag_score": rag_score}


# ──── Node 2: LLM Analysis ────
def analyze_cv_node(state: CVScreeningState) -> CVScreeningState:
    rag_score = state.get("rag_score", 0)

    prompt = f"""
You are an expert HR recruiter. Analyze this CV against job requirements.

SEMANTIC SIMILARITY SCORE (from Vector DB): {rag_score:.1f}/100
Use this as a reference — it shows how closely the CV matches the job description semantically.

JOB DETAILS:
- Title: {state['job_title']}
- Required Skills: {state['job_skills']}
- Experience Required: {state['job_experience']}
- Keywords: {state['job_keywords']}

CANDIDATE CV:
{state['cv_text'][:3000]}

Return ONLY this JSON:
{{
    "match_score": <number 0-100, consider the semantic similarity score above>,
    "skill_gap": "<comma separated missing skills>",
    "summary": "<2-3 sentences about candidate suitability>"
}}

Scoring: 80-100 Excellent, 60-79 Good, 40-59 Average, 0-39 Poor
Return ONLY JSON.
"""
    messages = [
        SystemMessage(content="You are an expert HR recruiter. Always respond with valid JSON only."),
        HumanMessage(content=prompt)
    ]
    try:
        response = llm.invoke(messages)
        raw = response.content.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        result = json.loads(raw)

        # ──── Final Score: RAG 40% + LLM 60% ────
        llm_score = float(result.get("match_score", 0))
        final_score = (rag_score * 0.40) + (llm_score * 0.60)

        return {
            **state,
            "match_score": round(final_score, 2),
            "skill_gap": str(result.get("skill_gap", "")),
            "summary": str(result.get("summary", "")),
            "error": ""
        }
    except Exception as e:
        return {
            **state,
            "match_score": 0.0,
            "skill_gap": "Could not analyze",
            "summary": "CV screening failed",
            "error": str(e)
        }


# ──── Node 3: Validate Score ────
def validate_score_node(state: CVScreeningState) -> CVScreeningState:
    score = state.get("match_score", 0)
    if score < 0: score = 0
    elif score > 100: score = 100
    return {**state, "match_score": score}


def build_cv_screening_graph():
    graph = StateGraph(CVScreeningState)
    graph.add_node("rag_similarity", rag_similarity_node)   # ← Node 1: RAG
    graph.add_node("analyze_cv", analyze_cv_node)           # ← Node 2: LLM
    graph.add_node("validate_score", validate_score_node)   # ← Node 3: Validate
    graph.set_entry_point("rag_similarity")
    graph.add_edge("rag_similarity", "analyze_cv")
    graph.add_edge("analyze_cv", "validate_score")
    graph.add_edge("validate_score", END)
    return graph.compile()

cv_screening_graph = build_cv_screening_graph()


def screen_cv(
    candidate_id: int, job_id: int, candidate_name: str,
    candidate_email: str, cv_text: str, job_title: str,
    job_description: str, job_keywords: str,
    job_experience: str, job_skills: str,
) -> dict:
    initial_state: CVScreeningState = {
        "candidate_id": candidate_id, "job_id": job_id,
        "candidate_name": candidate_name, "candidate_email": candidate_email,
        "cv_text": cv_text, "job_title": job_title,
        "job_description": job_description, "job_keywords": job_keywords,
        "job_experience": job_experience, "job_skills": job_skills,
        "rag_score": 0.0,
        "match_score": 0.0, "skill_gap": "", "summary": "", "error": ""
    }
    result = cv_screening_graph.invoke(initial_state)
    return {
        "candidate_id": result["candidate_id"],
        "job_id": result["job_id"],
        "match_score": result["match_score"],
        "rag_score": result.get("rag_score", 0),
        "skill_gap": result["skill_gap"],
        "summary": result["summary"],
        "error": result.get("error", "")
    }