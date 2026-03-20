import json
import os
from typing import TypedDict
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

load_dotenv()

llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="llama-3.1-8b-instant",
    temperature=0.3,
    max_tokens=1500
)

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
    match_score: float
    skill_gap: str
    summary: str
    error: str

def analyze_cv_node(state: CVScreeningState) -> CVScreeningState:
    prompt = f"""
You are an expert HR recruiter. Analyze this CV against job requirements.

JOB DETAILS:
- Title: {state['job_title']}
- Required Skills: {state['job_skills']}
- Experience Required: {state['job_experience']}
- Keywords: {state['job_keywords']}

CANDIDATE CV:
{state['cv_text'][:3000]}

Return ONLY this JSON:
{{
    "match_score": <number 0-100>,
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
        return {
            **state,
            "match_score": float(result.get("match_score", 0)),
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

def validate_score_node(state: CVScreeningState) -> CVScreeningState:
    score = state.get("match_score", 0)
    if score < 0: score = 0
    elif score > 100: score = 100
    return {**state, "match_score": score}

def build_cv_screening_graph():
    graph = StateGraph(CVScreeningState)
    graph.add_node("analyze_cv", analyze_cv_node)
    graph.add_node("validate_score", validate_score_node)
    graph.set_entry_point("analyze_cv")
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
        "match_score": 0.0, "skill_gap": "", "summary": "", "error": ""
    }
    result = cv_screening_graph.invoke(initial_state)
    return {
        "candidate_id": result["candidate_id"],
        "job_id": result["job_id"],
        "match_score": result["match_score"],
        "skill_gap": result["skill_gap"],
        "summary": result["summary"],
        "error": result.get("error", "")
    }