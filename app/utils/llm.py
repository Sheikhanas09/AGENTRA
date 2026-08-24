"""
The Groq model — in ONE place

═══ WHY THIS FILE EXISTS ═══
In Aug 2026 Groq removed all of its llama-3.x models. Every LLM call
started returning 404:

    The model `llama-3.1-8b-instant` does not exist

Policy upload kept working (indexing is deterministic) but extracting
leave types and working hours both stopped. The damage was not only that
the model went away — the name was hardcoded in THREE separate files
(cv_screening, jd_generator, leave_agent), so all three had to be found
and changed.

Now there is one place. If Groq removes something again next year it is
one line here, or `GROQ_MODEL=...` in `.env` — no code needs touching.

═══ WHY THIS MODEL ═══
The three remaining candidates were tested on the real work (the full
handbook, a leave-only document, a payroll document):

    openai/gpt-oss-120b   25/25   5 out of 5 runs  ← this one
    qwen/qwen3.6-27b      25/25   but sends reasoning alongside the
                                  JSON, so parsing breaks
    openai/gpt-oss-20b    25/25 then 15/25 — INCONSISTENT,
                                  once it skipped the whole handbook

An inconsistent model is the most dangerous thing here: the shift comes
out on one run and not the next — and the CEO never learns what was missed.
"""
import os

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def groq_model() -> str:
    """Which Groq model — `GROQ_MODEL` from `.env`, otherwise the default"""
    return (os.getenv("GROQ_MODEL") or "").strip() or DEFAULT_GROQ_MODEL
