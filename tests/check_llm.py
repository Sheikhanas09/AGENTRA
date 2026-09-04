"""
Check the model before trusting it
──────────────────────────────────
    py tests/check_llm.py                 use what .env says
    py tests/check_llm.py --list          show every provider and its package
    py tests/check_llm.py --model X       try one model without editing .env

Run this after changing a key. It makes ONE tiny call, so a wrong key or
a withdrawn model shows up here instead of as "I could not pull that up"
in the middle of a conversation — where every agent swallows the real
error and the desk simply goes quiet.

Exit code is 1 on failure, so CI can use it.
"""

import os
import sys

# (purana bootstrap hataya: move ke baad yeh apne hi folder ko
#  daal raha tha, Backend/ ko nahi)

# ──── Backend/ ko raaste par lao ────
# Yeh script Backend/ ke andar ek folder mein hai. `py tests/x.py`
# chalane par Python sirf us folder ko sys.path par rakhta hai, cwd ko
# nahi — to `import app` nakaam ho jata. Aur kuch checks source tree ko
# `Path("app")` se scan karte hain, jo cwd par munhasir hai.
import os as _os
import sys as _sys

_BACKEND = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)
_os.chdir(_BACKEND)

from app.utils.llm import (
    LLMNotConfigured, PROVIDERS, base_url, chat_model, describe, llm_key,
    llm_model, provider,
)


def list_providers() -> None:
    print("\nProviders\n")
    for name, spec in PROVIDERS.items():
        try:
            __import__(spec["module"])
            state = "installed"
        except ImportError:
            state = f"pip install {spec['pip']}"
        extra = "  (also needs LLM_BASE_URL)" if spec.get("needs_base_url") else ""
        print(f"  {name:20} {state}{extra}")

    print("""
In .env:

    LLM_PROVIDER=openai
    LLM_MODEL=gpt-4o-mini
    LLM_API_KEY=sk-proj-...

`openai_compatible` reaches anything that speaks the OpenAI API —
OpenRouter, Together, DeepSeek, Fireworks, LM Studio, Ollama:

    LLM_PROVIDER=openai_compatible
    LLM_BASE_URL=https://openrouter.ai/api/v1
    LLM_MODEL=meta-llama/llama-3.3-70b-instruct
    LLM_API_KEY=sk-or-...
""")


def main() -> int:
    if "--list" in sys.argv:
        list_providers()
        return 0

    override = None
    if "--model" in sys.argv:
        i = sys.argv.index("--model")
        if i + 1 >= len(sys.argv):
            print("--model needs a model id after it")
            return 1
        override = sys.argv[i + 1]

    print(f"\nConfiguration : {describe()}")
    if override:
        print(f"Trying instead: {override}")

    if not llm_key() and provider() != "ollama":
        print("\nNo API key. Put LLM_API_KEY=... in .env")
        return 1

    try:
        llm = chat_model(temperature=0, max_tokens=32, model=override)
    except LLMNotConfigured as e:
        print(f"\nNot configured:\n  {e}")
        return 1

    from langchain_core.messages import HumanMessage

    print("Calling...", end=" ", flush=True)
    try:
        reply = llm.invoke([HumanMessage(content="Reply with the single "
                                                 "word: ready")])
    except Exception as e:                              # noqa: BLE001
        text = str(e)
        print("FAILED\n")
        # The three that actually happen, said in words you can act on
        if "rate_limit" in text or "429" in text:
            print("  Rate limited — the key works, the quota does not.")
            print(f"  {text[:220]}")
        elif "does not exist" in text or "404" in text:
            print(f"  The model '{override or llm_model()}' was not found.")
            print("  Check the id on your provider's dashboard.")
        elif "api_key" in text.lower() or "401" in text or "auth" in text.lower():
            print("  The key was rejected. Check LLM_API_KEY in .env.")
        else:
            print(f"  {text[:300]}")
        return 1

    said = (reply.content or "").strip()
    print("ok")
    print(f"  Model replied: {said[:60]!r}")
    if not said:
        # gpt-oss models spend the budget on reasoning tokens first, so
        # an empty string at max_tokens=32 is normal and not a fault.
        print("  (empty is fine on reasoning models at this token limit)")

    print(f"\nReady. Every agent will use {provider()} · "
          f"{override or llm_model()}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
