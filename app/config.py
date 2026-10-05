import os

from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
# Any OpenAI-compatible model id works here — swapping providers is one .env
# line, no code change.
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-flash-lite-latest")
AGENT_MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "6"))
