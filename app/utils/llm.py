"""
The language model — one provider setting, one place
────────────────────────────────────────────────────
Change these three lines in `.env` and the whole system moves:

    LLM_PROVIDER=groq
    LLM_MODEL=openai/gpt-oss-120b
    LLM_API_KEY=gsk_...

Nothing else needs touching. Every agent asks this file for a model and
gets whatever is configured.

═══════════════════════════════════════════════════════════
WHY THIS FILE EXISTED BEFORE, AND WHY IT DOES MORE NOW
═══════════════════════════════════════════════════════════
In Aug 2026 Groq removed all of its llama-3.x models and every call
started failing with `The model llama-3.1-8b-instant does not exist`.
The name was hardcoded in three separate files, so all three had to be
found and fixed. That is why the MODEL moved here.

Then a day's testing ran the free tier out of tokens and the whole
system stopped — with no way to point it at another key without editing
five agents. So the PROVIDER moved here too. The lesson is the same one
twice: anything that can be taken away from you belongs behind one
function.

═══════════════════════════════════════════════════════════
WHICH MODEL, IF YOU ARE CHOOSING
═══════════════════════════════════════════════════════════
The three Groq candidates were tested on the real work — the full
handbook, a leave-only document, a payroll document:

    openai/gpt-oss-120b   25/25   5 runs out of 5   ← this one
    qwen/qwen3.6-27b      25/25   but sends reasoning alongside the
                                  JSON, so parsing breaks
    openai/gpt-oss-20b    25/25 then 15/25 — INCONSISTENT,
                                  once it skipped the whole handbook

An inconsistent model is the most dangerous thing here: the shift comes
out on one run and not the next, and nobody ever learns what was missed.

═══════════════════════════════════════════════════════════
ONE PROVIDER COVERS MOST OF THEM
═══════════════════════════════════════════════════════════
`openai_compatible` speaks the OpenAI API to any base URL, which is what
OpenRouter, Together, DeepSeek, Fireworks, LM Studio and Ollama all
serve. So a provider that is not in the table below is usually still
reachable:

    LLM_PROVIDER=openai_compatible
    LLM_BASE_URL=https://openrouter.ai/api/v1
    LLM_MODEL=meta-llama/llama-3.3-70b-instruct
    LLM_API_KEY=sk-or-...
"""

import os
from typing import Optional

from dotenv import load_dotenv

# ──── The .env is loaded HERE, not left to chance ────
# It used to arrive as a side effect of importing `security.py`, so
# anything reaching a model without touching that module found no key and
# failed with "the api_key client option must be set". The HR console did
# exactly that. This module reads the configuration, so it loads it.
load_dotenv()


# ══════════════════════════════════════════════
# What each provider needs
# ══════════════════════════════════════════════
# `key_kwarg` and `model_kwarg` exist because the LangChain wrappers do
# not agree with each other: some take `api_key`, Google takes
# `google_api_key`; most take `max_tokens`, Google takes
# `max_output_tokens`. Keeping the differences in a table means the
# agents never see them.
PROVIDERS = {
    "groq": {
        "pip": "langchain-groq",
        "module": "langchain_groq",
        "cls": "ChatGroq",
        "key_kwarg": "api_key",
        "model_kwarg": "model",
        "max_tokens_kwarg": "max_tokens",
        "default_model": "openai/gpt-oss-120b",
    },
    "openai": {
        "pip": "langchain-openai",
        "module": "langchain_openai",
        "cls": "ChatOpenAI",
        "key_kwarg": "api_key",
        "model_kwarg": "model",
        "max_tokens_kwarg": "max_tokens",
        "default_model": None,
    },
    "anthropic": {
        "pip": "langchain-anthropic",
        "module": "langchain_anthropic",
        "cls": "ChatAnthropic",
        "key_kwarg": "api_key",
        "model_kwarg": "model",
        "max_tokens_kwarg": "max_tokens",
        "default_model": None,
    },
    "google": {
        "pip": "langchain-google-genai",
        "module": "langchain_google_genai",
        "cls": "ChatGoogleGenerativeAI",
        "key_kwarg": "google_api_key",
        "model_kwarg": "model",
        "max_tokens_kwarg": "max_output_tokens",
        "default_model": None,
    },
    "ollama": {
        # Runs on your own machine, so there is no key to give it
        "pip": "langchain-ollama",
        "module": "langchain_ollama",
        "cls": "ChatOllama",
        "key_kwarg": None,
        "model_kwarg": "model",
        "max_tokens_kwarg": "num_predict",
        "default_model": None,
    },
    "openai_compatible": {
        "pip": "langchain-openai",
        "module": "langchain_openai",
        "cls": "ChatOpenAI",
        "key_kwarg": "api_key",
        "model_kwarg": "model",
        "max_tokens_kwarg": "max_tokens",
        "default_model": None,
        "needs_base_url": True,
    },
}

DEFAULT_PROVIDER = "groq"


# ══════════════════════════════════════════════
# Reading the configuration
# ══════════════════════════════════════════════
def _env(*names: str) -> str:
    """The first of these that is set to something."""
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


def provider() -> str:
    """Which provider — `LLM_PROVIDER`, otherwise Groq."""
    p = _env("LLM_PROVIDER").lower() or DEFAULT_PROVIDER
    return p if p in PROVIDERS else DEFAULT_PROVIDER


def llm_model() -> str:
    """
    Which model.

    `GROQ_MODEL` is still read so an existing `.env` keeps working
    unchanged — this file gained a provider, it did not break anybody's
    setup.
    """
    spec = PROVIDERS[provider()]
    return _env("LLM_MODEL", "GROQ_MODEL") or (spec["default_model"] or "")


def llm_key() -> str:
    """The API key. Empty is only valid for a local provider."""
    return _env("LLM_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")


def base_url() -> str:
    return _env("LLM_BASE_URL", "OPENAI_BASE_URL")


def describe() -> str:
    """One line for a log or a health check."""
    key = llm_key()
    shown = f"{key[:6]}…{key[-4:]}" if len(key) > 12 else ("set" if key else "MISSING")
    line = f"{provider()} · {llm_model() or 'NO MODEL SET'} · key {shown}"
    return line + (f" · {base_url()}" if base_url() else "")


# ══════════════════════════════════════════════
# Building the model
# ══════════════════════════════════════════════
class LLMNotConfigured(RuntimeError):
    """
    Raised with something you can act on.

    Every agent catches its own exceptions and falls back to "I could not
    look that up", so a configuration mistake would otherwise be
    invisible — the desk would simply go quiet and nobody would know why.
    """


def chat_model(temperature: float = 0.2, max_tokens: int = 1500,
               model: Optional[str] = None):
    """
    A chat model, configured from `.env`.

    Agents pass only what actually differs between them — how creative
    the reply should be, and how much room it needs. Everything else is
    the same everywhere and belongs here.
    """
    name = provider()
    spec = PROVIDERS[name]

    chosen = model or llm_model()
    if not chosen:
        raise LLMNotConfigured(
            f"No model set for provider '{name}'. Put LLM_MODEL=... in "
            f".env (for example the model id from your provider's "
            f"dashboard)."
        )

    key = llm_key()
    if spec["key_kwarg"] and not key:
        raise LLMNotConfigured(
            f"No API key found for provider '{name}'. Put LLM_API_KEY=... "
            f"in .env."
        )

    url = base_url()
    if spec.get("needs_base_url") and not url:
        raise LLMNotConfigured(
            "LLM_PROVIDER=openai_compatible also needs LLM_BASE_URL — for "
            "example https://openrouter.ai/api/v1"
        )

    try:
        module = __import__(spec["module"], fromlist=[spec["cls"]])
        cls = getattr(module, spec["cls"])
    except ImportError as e:
        raise LLMNotConfigured(
            f"Provider '{name}' needs a package that is not installed.\n"
            f"    pip install {spec['pip']}"
        ) from e

    kwargs = {
        spec["model_kwarg"]: chosen,
        "temperature": temperature,
        spec["max_tokens_kwarg"]: max_tokens,
    }
    if spec["key_kwarg"]:
        kwargs[spec["key_kwarg"]] = key
    if url:
        kwargs["base_url"] = url

    return cls(**kwargs)


# ══════════════════════════════════════════════
# The old names, kept working
# ══════════════════════════════════════════════
# Nothing should call these in new code, but they are what the earlier
# agents used and there is no reason to break a file to rename a
# function inside it.
def groq_model() -> str:
    return llm_model()


def groq_key() -> str:
    return llm_key()


DEFAULT_GROQ_MODEL = PROVIDERS["groq"]["default_model"]
