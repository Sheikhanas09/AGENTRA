import json
import os
from typing import TypedDict
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

load_dotenv()

# ──── LLM Setup ────
llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="llama-3.1-8b-instant",
    temperature=0.9,
    max_tokens=2000
)


# ──── State Schema ────
class JDState(TypedDict):
    title: str
    department: str
    employment_type: str
    experience: str
    skills: str
    salary_range: str
    company_name: str
    additional_info: str
    ceo_email: str
    full_description: str
    keywords: str
    summary: str
    error: str


# ──── Node 1: Generate JD ────
def generate_jd_node(state: JDState) -> JDState:

    additional_benefits = ""
    if state.get("additional_info") and state["additional_info"].strip():
        additional_benefits = "\n".join([f"• {b.strip()}" for b in state["additional_info"].split(",")])

    hr_email = state.get("ceo_email") or f"hr@{state['company_name'].lower().replace(' ', '')}.com"
    skills_list = [s.strip() for s in state["skills"].split(",")]

    prompt = f"""
You are an expert HR professional and technical recruiter at {state['company_name']}.

Write a compelling, detailed, and professional job posting for this position:

Position: {state['title']}
Company: {state['company_name']}
Department: {state['department']}
Employment Type: {state['employment_type']}
Experience Required: {state['experience']}
Required Skills: {state['skills']}
Salary: {state['salary_range']}
Additional Benefits: {state['additional_info'] if state['additional_info'].strip() else 'Not specified'}
HR Email: {hr_email}

Write naturally like a real HR professional — do NOT fill templates, write original content.

For each skill in "{state['skills']}", describe what the candidate will ACTUALLY DO:
- Python → writing automation scripts, data processing pipelines, OOP-based modules
- FastAPI → building REST API endpoints, request/response validation, JWT auth middleware
- React → building reusable components, managing state with hooks, integrating backend APIs
- JavaScript → writing async functions, handling DOM events, using ES6+ features
- SQL/PostgreSQL → writing complex JOIN queries, designing schemas, optimizing slow queries
- Django → creating models, writing views, configuring URLs, using ORM
- Node.js → setting up Express server, writing middleware, building REST APIs
- MongoDB → designing collections, writing aggregation pipelines, indexing
- Docker → writing Dockerfiles, setting up docker-compose, containerizing apps
- Git → branching, pull requests, resolving merge conflicts
- Tailwind CSS → building responsive layouts, custom utility classes
- For any other skill → describe its real practical day-to-day usage

Experience level guidance:
- Fresher/0-6 months: simple tasks, learning attitude, basic features only
- 6 months-1 year: small features, bug fixes, some project experience
- 1-2 years: independent work, moderate complexity
- 3+ years: system design, mentoring, complex features

Rules:
- Only use skills from: {state['skills']}
- Requirements must match {state['experience']} level exactly — do not over-require
- If additional benefits not specified — only mention salary and employment type
- Write responsibilities that directly reflect the skills provided

Format exactly like this:
🏢 {state['company_name']} — {state['title']}
{state['department']}  |  {state['employment_type']}


📋 Job Summary

[Write 2-3 natural sentences about this role and what the person will work on]


✅ Key Responsibilities

[Write 5-6 bullet points — each one specific to a skill from {state['skills']}]


🎯 Requirements

[Write 4-5 bullet points — specific technical requirements for each skill, matched to {state['experience']} level]


💰 Compensation & Benefits

- Salary: {state['salary_range']}
- Type: {state['employment_type']}
{additional_benefits if additional_benefits else ''}


📩 How to Apply

You can apply through any of the following ways:

1️⃣  Online Portal (Recommended):
   Visit our careers page and click "Apply Now" for instant AI-powered screening

2️⃣  Email:
   Send your CV to: {hr_email}
   Subject: Application for {state['title']}

Now return the complete job posting inside this JSON:
{{
    "full_description": "the complete formatted job posting with \\n for line breaks",
    "keywords": "comma separated technical keywords from {state['skills']} only",
    "summary": "one line summary of this role"
}}

Return ONLY the JSON. No text outside the JSON.
"""

    messages = [
        SystemMessage(content="You are an expert HR professional and technical recruiter. Write compelling, natural job postings. Always respond with valid JSON only. Never add benefits or skills beyond what is provided. Match requirements to the given experience level."),
        HumanMessage(content=prompt)
    ]

    response = llm.invoke(messages)
    raw = response.content.strip()

    # ──── Clean response ────
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(raw)

        full_desc = result.get("full_description", "")
        if isinstance(full_desc, dict):
            parts = []
            for k, v in full_desc.items():
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, list):
                    parts.extend([f"• {i}" for i in v])
            full_desc = "\n\n".join(parts)
        elif isinstance(full_desc, list):
            full_desc = "\n".join([str(i) for i in full_desc])

        keywords = result.get("keywords", state["skills"])
        if isinstance(keywords, list):
            keywords = ", ".join(keywords)

        return {
            **state,
            "full_description": str(full_desc),
            "keywords": str(keywords),
            "summary": str(result.get("summary", "")),
            "error": ""
        }

    except json.JSONDecodeError:
        # ──── Fallback ────
        skills_responsibilities = "\n".join([
            f"• Work with {skill.strip()} on real features and tasks"
            for skill in skills_list
        ])

        skills_requirements = "\n".join([
            f"• Hands-on experience with {skill.strip()}"
            for skill in skills_list
        ])

        fallback = f"""🏢 {state['company_name']} — {state['title']}
{state['department']}  |  {state['employment_type']}


📋 Job Summary

We are looking for a {state['title']} with {state['experience']} of experience to join our {state['department']} team at {state['company_name']}.


✅ Key Responsibilities

{skills_responsibilities}
- Collaborate with the team and participate in code reviews
- Deliver tasks on time while following best practices


🎯 Requirements

- {state['experience']} of hands-on experience
{skills_requirements}
- Good communication and problem-solving skills


💰 Compensation & Benefits

- Salary: {state['salary_range']}
- Type: {state['employment_type']}
{additional_benefits}


📩 How to Apply

You can apply through any of the following ways:

1️⃣  Online Portal (Recommended):
   Visit our careers page and click "Apply Now" for instant AI-powered screening

2️⃣  Email:
   Send your CV to: {hr_email}
   Subject: Application for {state['title']}"""

        return {
            **state,
            "full_description": fallback,
            "keywords": state["skills"],
            "summary": f"Seeking a {state['title']} at {state['company_name']}",
            "error": ""
        }


# ──── Node 2: Validate Output ────
def validate_output_node(state: JDState) -> JDState:
    if not state.get("full_description"):
        return {
            **state,
            "error": "JD generation failed"
        }
    return state


# ──── Build LangGraph ────
def build_jd_graph():
    graph = StateGraph(JDState)

    graph.add_node("generate_jd", generate_jd_node)
    graph.add_node("validate_output", validate_output_node)

    graph.set_entry_point("generate_jd")
    graph.add_edge("generate_jd", "validate_output")
    graph.add_edge("validate_output", END)

    return graph.compile()


# ──── Main Function ────
jd_graph = build_jd_graph()


def generate_job_description(
    title: str,
    department: str,
    employment_type: str,
    experience: str,
    skills: str,
    salary_range: str,
    company_name: str,
    additional_info: str = "",
    ceo_email: str = ""
) -> dict:

    initial_state: JDState = {
        "title": title,
        "department": department,
        "employment_type": employment_type,
        "experience": experience,
        "skills": skills,
        "salary_range": salary_range,
        "company_name": company_name,
        "additional_info": additional_info,
        "ceo_email": ceo_email,
        "full_description": "",
        "keywords": "",
        "summary": "",
        "error": ""
    }

    result = jd_graph.invoke(initial_state)

    return {
        "full_description": result["full_description"],
        "keywords": result["keywords"],
        "summary": result["summary"],
        "error": result.get("error", "")
    }