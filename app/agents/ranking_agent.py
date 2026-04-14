import os
from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv()


class RankingState(TypedDict):
    job_id: int
    candidates: List[dict]
    ranked_list: List[dict]
    best_candidate: dict


# ──── Node 1: Sort by final score ────
def sort_candidates_node(state: RankingState) -> RankingState:
    candidates = state["candidates"]

    # ──── Final score se sort karo ────
    sorted_candidates = sorted(
        candidates,
        key=lambda x: x.get("final_score", 0),
        reverse=True
    )

    # ──── Rank assign karo ────
    ranked = []
    for i, candidate in enumerate(sorted_candidates):
        ranked.append({
            **candidate,
            "rank": i + 1
        })

    best = ranked[0] if ranked else {}

    return {
        **state,
        "ranked_list": ranked,
        "best_candidate": best
    }


# ──── Node 2: Category assign karo ────
def assign_category_node(state: RankingState) -> RankingState:
    ranked = state["ranked_list"]
    updated = []

    for candidate in ranked:
        score = candidate.get("final_score", 0)
        if score >= 80:
            category = "Strong Hire"
        elif score >= 65:
            category = "Hire"
        elif score >= 50:
            category = "Consider"
        else:
            category = "Reject"

        updated.append({**candidate, "ranking_category": category})

    return {**state, "ranked_list": updated}


# ──── LangGraph build ────
def build_ranking_graph():
    graph = StateGraph(RankingState)
    graph.add_node("sort_candidates", sort_candidates_node)
    graph.add_node("assign_category", assign_category_node)
    graph.set_entry_point("sort_candidates")
    graph.add_edge("sort_candidates", "assign_category")
    graph.add_edge("assign_category", END)
    return graph.compile()

ranking_graph = build_ranking_graph()


# ──── Main Function ────
def rank_candidates(job_id: int, candidates: list) -> dict:
    if not candidates:
        return {"job_id": job_id, "ranked_list": [], "best_candidate": {}}

    initial_state: RankingState = {
        "job_id": job_id,
        "candidates": candidates,
        "ranked_list": [],
        "best_candidate": {}
    }

    result = ranking_graph.invoke(initial_state)

    return {
        "job_id": job_id,
        "ranked_list": result["ranked_list"],
        "best_candidate": result["best_candidate"]
    }