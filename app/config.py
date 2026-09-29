import os

from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
# Any OpenAI-compatible model id works here (Groq-hosted Qwen today, a served Qwen
# adapter after fine-tuning — same code, one .env line to swap).
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")
AGENT_MAX_TOOL_ROUNDS = int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "6"))
