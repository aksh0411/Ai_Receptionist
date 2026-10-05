"""Shared LLM client factory: one place for the resilience policy.

The openai client retries 429/5xx with exponential backoff on its own — we only
set the budget here (30s timeout per call, 2 retries) so a hung or rate-limited
provider can neither hold a request thread forever nor fail the user on the
first transient error (free-tier 429/503s were observed in live testing).
"""

from openai import OpenAI

from app.config import LLM_API_KEY, LLM_BASE_URL


def make_client() -> OpenAI | None:
    if not LLM_API_KEY:
        return None
    return OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=30.0, max_retries=2)
