import os
from typing import TypedDict
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv()


class EvaluationState(TypedDict):
    candidate_id: int
    job_id: int
    resume_score: float
    technical_score: float
    communication_score: float
    final_score: float
    ranking_category: str


# ──── Node 1: Final Score Calculate ────
def calculate_score_node(state: EvaluationState) -> EvaluationState:
    resume_score = state["resume_score"]          # 0-100
    technical_score = state["technical_score"]    # 0-10
    communication_score = state["communication_score"]  # 0-10

    # ──── 0-10 ko 0-100 mein convert karo ────
    technical_100 = technical_score * 10
    communication_100 = communication_score * 10

    # ──── Formula ────
    final_score = (
        (resume_score * 0.40) +
        (technical_100 * 0.40) +
        (communication_100 * 0.20)
    )
    final_score = round(final_score, 2)

    return {**state, "final_score": final_score}


# ──── Node 2: Category Assign ────
def assign_category_node(state: EvaluationState) -> EvaluationState:
    score = state["final_score"]

    if score >= 80:
        category = "Strong Hire"
    elif score >= 65:
        category = "Hire"
    elif score >= 50:
        category = "Consider"
    else:
        category = "Reject"

    return {**state, "ranking_category": category}


# ──── LangGraph build ────
def build_evaluation_graph():
    graph = StateGraph(EvaluationState)
    graph.add_node("calculate_score", calculate_score_node)
    graph.add_node("assign_category", assign_category_node)
    graph.set_entry_point("calculate_score")
    graph.add_edge("calculate_score", "assign_category")
    graph.add_edge("assign_category", END)
    return graph.compile()

evaluation_graph = build_evaluation_graph()


# ──── Main Function ────
def evaluate_candidate(
    candidate_id: int,
    job_id: int,
    resume_score: float,
    technical_score: float,
    communication_score: float
) -> dict:

    initial_state: EvaluationState = {
        "candidate_id": candidate_id,
        "job_id": job_id,
        "resume_score": resume_score,
        "technical_score": technical_score,
        "communication_score": communication_score,
        "final_score": 0.0,
        "ranking_category": ""
    }

    result = evaluation_graph.invoke(initial_state)

    return {
        "candidate_id": result["candidate_id"],
        "job_id": result["job_id"],
        "resume_score": result["resume_score"],
        "technical_score": result["technical_score"],
        "communication_score": result["communication_score"],
        "final_score": result["final_score"],
        "ranking_category": result["ranking_category"]
    }